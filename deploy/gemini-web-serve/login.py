from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

RUN_DIR = os.environ.get("GWS_RUN_DIR", "/run/gemini-web-login")
PROFILES_DIR = os.environ.get("GWS_PROFILES_DIR", "/var/lib/gemini-web-serve/profiles")
ACCOUNTS_DIR = os.environ.get("GWS_ACCOUNTS_DIR", "/etc/gemini-web/accounts")
ENV_FILE = os.environ.get("GWS_ENV_FILE", "/etc/gemini-web-serve.env")
DEFAULT_PROXY = "socks5://100.64.96.50:1080"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
PSID_RE = re.compile(r"^g\.a[0-9A-Za-z_\-]+$")

CHROME_FLAGS = [
    "--no-first-run",
    "--no-default-browser-check",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--renderer-process-limit=2",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-sync",
    "--disable-default-apps",
    "--disable-component-update",
    "--disable-breakpad",
    "--disable-gpu",
    "--window-size=1280,800",
    "--password-store=basic",
]


def validate_account_name(name: str) -> bool:
    return bool(NAME_RE.match(name))


def format_proxy_for_chrome(proxy: str) -> str:
    if proxy.startswith("socks5h://"):
        return "socks5://" + proxy[len("socks5h://"):]
    return proxy


def format_proxy_for_service(proxy: str) -> str:
    if proxy.startswith("socks5://"):
        return "socks5h://" + proxy[len("socks5://"):]
    return proxy


def parse_env_file(path: Path | str) -> dict[str, str]:
    p = Path(path)
    if not p.is_file():
        return {}
    res: dict[str, str] = {}
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            k, v = stripped.split("=", 1)
            k = k.strip()
            v = v.strip()
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                v = v[1:-1]
            res[k] = v
    return res


def decrypt_cookie_value(raw: bytes | str | None, version: int = 0) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not raw:
        return ""
    if raw.startswith(b"v10") or raw.startswith(b"v11"):
        ciphertext = raw[3:]
        if not ciphertext:
            return ""
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.primitives import padding

            key = hashlib.pbkdf2_hmac("sha1", b"peanuts", b"saltysalt", 1, 16)
            cipher = Cipher(algorithms.AES(key), modes.CBC(b" " * 16))
            decryptor = cipher.decryptor()
            padded = decryptor.update(ciphertext) + decryptor.finalize()
            unpadder = padding.PKCS7(128).unpadder()
            decrypted = unpadder.update(padded) + unpadder.finalize()
            if version >= 24:
                decrypted = decrypted[32:]
            val = decrypted.decode("utf-8")
            if not val or not all(32 <= ord(c) <= 126 for c in val):
                return ""
            return val
        except Exception:
            return ""
    try:
        val = raw.decode("utf-8")
        if not val or not all(32 <= ord(c) <= 126 for c in val):
            return ""
        return val
    except Exception:
        return ""


def extract_cookies(cookie_db: Path | str) -> dict[str, str] | None:
    p = Path(cookie_db)
    if not p.is_file():
        return None

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        shutil.copy2(p, tmp_path)
        conn = sqlite3.connect(str(tmp_path))
        try:
            cur = conn.cursor()
            version = 0
            try:
                cur.execute("SELECT value FROM meta WHERE key='version'")
                row = cur.fetchone()
                if row and row[0] is not None:
                    version = int(row[0])
            except Exception:
                version = 0

            cur.execute("PRAGMA table_info(cookies)")
            cols = [r[1] for r in cur.fetchall()]
            has_value = "value" in cols
            if has_value:
                cur.execute("SELECT host_key, name, encrypted_value, value FROM cookies WHERE host_key LIKE '%google.com'")
            else:
                cur.execute("SELECT host_key, name, encrypted_value FROM cookies WHERE host_key LIKE '%google.com'")
            rows = cur.fetchall()
        finally:
            conn.close()

        cookies: dict[str, str] = {}
        cookie_hosts: dict[str, str] = {}
        bad_count = 0
        for row in rows:
            host, name = row[0], row[1]
            enc_val = row[2]
            if has_value and not enc_val:
                enc_val = row[3]
            val = decrypt_cookie_value(enc_val, version=version)
            if not val:
                bad_count += 1
                continue
            if name not in cookies or host == ".google.com":
                cookies[name] = val
                cookie_hosts[name] = host

        print(f"Cookies found: {len(cookies)}, bad values skipped: {bad_count}")

        psid = cookies.get("__Secure-1PSID", "")
        if len(psid) <= 10:
            return None
        return cookies
    except Exception:
        return None
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def save_account(
    accounts_dir: Path | str,
    name: str,
    cookies: dict[str, str],
    proxy: str,
) -> Path:
    p_dir = Path(accounts_dir)
    p_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p_dir, 0o700)
    except OSError:
        pass

    target = p_dir / f"{name}.json"
    tmp = p_dir / f"{name}.json.tmp"
    data = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "cookies": cookies,
        "proxy": format_proxy_for_service(proxy),
    }
    content = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, content)
    finally:
        os.close(fd)
    os.replace(str(tmp), str(target))
    return target


def clean_profile_locks(profile_dir: Path | str) -> None:
    p = Path(profile_dir)
    if not p.is_dir():
        return
    for item in p.glob("Singleton*"):
        try:
            item.unlink()
        except OSError:
            pass


def stop_process(pid_file: Path) -> None:
    if not pid_file.is_file():
        return
    try:
        pid = int(pid_file.read_text().strip())
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError:
            pass
    except (ValueError, OSError):
        pass
    finally:
        try:
            pid_file.unlink()
        except OSError:
            pass


def stop_session(run_dir: Path | str = RUN_DIR) -> None:
    r_dir = Path(run_dir)
    for proc in ("chrome", "x11vnc", "xvfb"):
        stop_process(r_dir / f"{proc}.pid")
    vnc_pass = r_dir / "vnc.pass"
    if vnc_pass.exists():
        try:
            vnc_pass.unlink()
        except OSError:
            pass


def print_instructions(vnc_password: str) -> None:
    print("=" * 60)
    print("Gemini Web Login Steps:")
    print("1. Open SSH tunnel (keep open):")
    print("   ssh -N -L 5901:localhost:5913 -i ~/.ssh/deploy_key root@109.123.232.184")
    print("2. Open VNC client:")
    print("   open vnc://localhost:5901")
    print(f"   VNC password: {vnc_password}")
    print("3. Log in to the Google account in Chrome and open gemini.google.com until the chat page shows.")
    print("Waiting for cookies (checking every 3s, max 60m)...")
    print("=" * 60)


def wait_for_cookies(
    profile_dir: Path | str,
    timeout: float = 3600.0,
    poll_interval: float = 3.0,
) -> dict[str, str] | None:
    cookie_db = Path(profile_dir) / "Default" / "Cookies"
    start = time.time()
    while time.time() - start < timeout:
        if cookie_db.exists():
            cookies = extract_cookies(cookie_db)
            if cookies:
                return cookies
        time.sleep(poll_interval)
    return None


def call_reload(bind: str, port: str | int, token: str, account_name: str) -> None:
    url = f"http://{bind}:{port}/v1/reload"
    req = urllib.request.Request(
        url,
        data=b"",
        headers={"Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for a in data.get("accounts", []):
                if a.get("name") == account_name:
                    print(
                        f"Account '{account_name}': status={a.get('status')} "
                        f"available={a.get('available')} reason={a.get('reason')}"
                    )
                    return
            print(f"Account '{account_name}' reloaded successfully.")
    except Exception:
        print("Service is not reachable; it will pick the file up on its next request/restart.")


def start_login_processes(
    name: str,
    proxy: str | None = None,
    run_dir: Path | str = RUN_DIR,
    profiles_dir: Path | str = PROFILES_DIR,
    env_file: Path | str = ENV_FILE,
) -> tuple[subprocess.Popen, subprocess.Popen, subprocess.Popen, str, Path]:
    r_dir = Path(run_dir)
    r_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(r_dir, 0o700)
    except OSError:
        pass

    profile_dir = Path(profiles_dir) / name
    profile_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(profile_dir, 0o700)
    except OSError:
        pass
    clean_profile_locks(profile_dir)

    vnc_pass = secrets.token_hex(4)
    vnc_pass_file = r_dir / "vnc.pass"
    subprocess.run(["x11vnc", "-storepasswd", vnc_pass, str(vnc_pass_file)], check=True)
    try:
        os.chmod(vnc_pass_file, 0o600)
    except OSError:
        pass

    xvfb = subprocess.Popen(["Xvfb", ":97", "-screen", "0", "1280x800x24"])
    (r_dir / "xvfb.pid").write_text(str(xvfb.pid))

    vnc_cmd = [
        "x11vnc", "-display", ":97", "-localhost",
        "-rfbport", "5913", "-rfbauth", str(vnc_pass_file),
        "-forever", "-shared",
    ]
    x11vnc = subprocess.Popen(vnc_cmd)
    (r_dir / "x11vnc.pid").write_text(str(x11vnc.pid))

    env_vars = parse_env_file(env_file)
    eff_proxy = proxy or env_vars.get("GWS_DEFAULT_PROXY") or DEFAULT_PROXY
    chrome_proxy = format_proxy_for_chrome(eff_proxy)

    chrome_cmd = (
        ["google-chrome", f"--user-data-dir={profile_dir}"]
        + CHROME_FLAGS
        + [f"--proxy-server={chrome_proxy}", "https://gemini.google.com"]
    )
    c_env = os.environ.copy()
    c_env["DISPLAY"] = ":97"
    chrome = subprocess.Popen(chrome_cmd, env=c_env)
    (r_dir / "chrome.pid").write_text(str(chrome.pid))

    return xvfb, x11vnc, chrome, vnc_pass, profile_dir


def run_login(
    name: str,
    proxy: str | None = None,
    run_dir: Path | str = RUN_DIR,
    profiles_dir: Path | str = PROFILES_DIR,
    accounts_dir: Path | str = ACCOUNTS_DIR,
    env_file: Path | str = ENV_FILE,
    timeout: float = 3600.0,
    poll_interval: float = 3.0,
) -> bool:
    if not validate_account_name(name):
        print(f"Error: Invalid account name '{name}'. Must match ^[a-z0-9][a-z0-9_-]{{0,31}}$")
        return False

    stop_session(run_dir)
    profile_dir = Path(profiles_dir) / name
    clean_profile_locks(profile_dir)

    try:
        _, _, _, vnc_pass, _ = start_login_processes(
            name, proxy=proxy, run_dir=run_dir, profiles_dir=profiles_dir, env_file=env_file
        )
        print_instructions(vnc_pass)
        cookies = wait_for_cookies(profile_dir, timeout=timeout, poll_interval=poll_interval)
        if not cookies:
            print("Timed out waiting for login cookies.")
            return False

        psid = cookies.get("__Secure-1PSID", "")
        if not PSID_RE.match(psid):
            print("Login cookies look invalid (decryption problem); account file not written.")
            sys.exit(1)

        print(f"Login successful! Acquired {len(cookies)} cookies.")
        env_vars = parse_env_file(env_file)
        eff_proxy = proxy or env_vars.get("GWS_DEFAULT_PROXY") or DEFAULT_PROXY
        save_account(accounts_dir, name, cookies, eff_proxy)
    finally:
        stop_session(run_dir)

    env_vars = parse_env_file(env_file)
    bind = env_vars.get("GWS_BIND") or "172.17.0.1"
    port = env_vars.get("GWS_PORT") or "8098"
    token = env_vars.get("GWS_TOKEN") or ""
    call_reload(bind, port, token, name)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Gemini Web VNC login helper")
    parser.add_argument("name", help="Account name to log in, or 'stop' to terminate existing session")
    parser.add_argument("--proxy", default=None, help="Proxy URL (socks5://...)")
    args = parser.parse_args()

    if args.name == "stop":
        stop_session()
        print("Stopped login session processes.")
        sys.exit(0)

    ok = run_login(args.name, proxy=args.proxy)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
