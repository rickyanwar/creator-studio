from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

import login


class TestNameValidation:
    def test_valid_names(self):
        assert login.validate_account_name("acct1") is True
        assert login.validate_account_name("a_b-c") is True

    def test_invalid_names(self):
        assert login.validate_account_name("../x") is False
        assert login.validate_account_name("A") is False
        assert login.validate_account_name("") is False
        assert login.validate_account_name("a" * 33) is False


class TestDecryptHelper:
    def test_decrypt_roundtrip(self):
        pytest.importorskip("cryptography")
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        def _encrypt(plain: str, prefix: bytes) -> bytes:
            data = plain.encode("utf-8")
            padder = padding.PKCS7(128).padder()
            padded = padder.update(data) + padder.finalize()
            key = hashlib.pbkdf2_hmac("sha1", b"peanuts", b"saltysalt", 1, 16)
            cipher = Cipher(algorithms.AES(key), modes.CBC(b" " * 16))
            enc = cipher.encryptor()
            return prefix + enc.update(padded) + enc.finalize()

        sample = "test_cookie_value_secure_12345"
        v10_enc = _encrypt(sample, b"v10")
        assert login.decrypt_cookie_value(v10_enc) == sample

        v11_enc = _encrypt(sample, b"v11")
        assert login.decrypt_cookie_value(v11_enc) == sample

    def test_plain_values_pass_through(self):
        assert login.decrypt_cookie_value(b"plain_byte_value") == "plain_byte_value"
        assert login.decrypt_cookie_value("plain_str_value") == "plain_str_value"
        assert login.decrypt_cookie_value("") == ""
        assert login.decrypt_cookie_value(None) == ""


class TestCookieExtraction:
    def test_google_cookies_extracted(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            db_path = Path(f.name)

        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, encrypted_value BLOB)")
        cur.execute(
            "INSERT INTO cookies VALUES (?, ?, ?)",
            (".google.com", "__Secure-1PSID", b"valid_psid_value_longer_than_10"),
        )
        cur.execute(
            "INSERT INTO cookies VALUES (?, ?, ?)",
            ("accounts.google.com", "__Secure-1PSIDTS", b"valid_psidts_val"),
        )
        cur.execute(
            "INSERT INTO cookies VALUES (?, ?, ?)",
            (".example.com", "other_cookie", b"other_cookie_value"),
        )
        conn.commit()
        conn.close()

        try:
            cookies = login.extract_cookies(db_path)
            assert cookies is not None
            assert "__Secure-1PSID" in cookies
            assert "__Secure-1PSIDTS" in cookies
            assert "other_cookie" not in cookies
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_missing_secure_1psid_returns_none(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            db_path = Path(f.name)

        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, encrypted_value BLOB)")
        cur.execute(
            "INSERT INTO cookies VALUES (?, ?, ?)",
            (".google.com", "NID", b"some_nid_value_here"),
        )
        conn.commit()
        conn.close()

        try:
            assert login.extract_cookies(db_path) is None
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_short_secure_1psid_returns_none(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            db_path = Path(f.name)

        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, encrypted_value BLOB)")
        cur.execute(
            "INSERT INTO cookies VALUES (?, ?, ?)",
            (".google.com", "__Secure-1PSID", b"short"),
        )
        conn.commit()
        conn.close()

        try:
            assert login.extract_cookies(db_path) is None
        finally:
            if db_path.exists():
                db_path.unlink()


class TestAccountFileWriter:
    def test_save_account_mode_and_proxy(self):
        with tempfile.TemporaryDirectory() as td:
            accounts_dir = Path(td)
            target = login.save_account(
                accounts_dir,
                "acct1",
                {"__Secure-1PSID": "psid_value_12345"},
                "socks5://100.64.96.50:1080",
            )
            assert target.is_file()
            assert target.stat().st_mode & 0o777 == 0o600

            tmp_files = list(accounts_dir.glob("*.tmp"))
            assert len(tmp_files) == 0

            data = json.loads(target.read_text())
            assert data["proxy"] == "socks5h://100.64.96.50:1080"
            assert data["cookies"]["__Secure-1PSID"] == "psid_value_12345"
            assert "updated_at" in data

    def test_save_account_keeps_socks5h(self):
        with tempfile.TemporaryDirectory() as td:
            accounts_dir = Path(td)
            target = login.save_account(
                accounts_dir,
                "acct2",
                {"__Secure-1PSID": "psid_value_67890"},
                "socks5h://1.2.3.4:1080",
            )
            data = json.loads(target.read_text())
            assert data["proxy"] == "socks5h://1.2.3.4:1080"


class TestEnvFileParser:
    def test_parse_env_file(self):
        with tempfile.NamedTemporaryFile("w+", suffix=".env") as f:
            f.write("# leading comment\n")
            f.write("\n")
            f.write("GWS_BIND=127.0.0.1\n")
            f.write("GWS_PORT=\"8098\"\n")
            f.write("GWS_TOKEN='test_token_secret'\n")
            f.write("# inline comment line\n")
            f.write("GWS_DEFAULT_PROXY=socks5h://100.64.96.50:1080\n")
            f.write("INVALID_LINE_NO_EQUALS\n")
            f.flush()

            parsed = login.parse_env_file(f.name)
            assert parsed["GWS_BIND"] == "127.0.0.1"
            assert parsed["GWS_PORT"] == "8098"
            assert parsed["GWS_TOKEN"] == "test_token_secret"
            assert parsed["GWS_DEFAULT_PROXY"] == "socks5h://100.64.96.50:1080"
            assert "INVALID_LINE_NO_EQUALS" not in parsed

    def test_parse_missing_file_returns_empty(self):
        assert login.parse_env_file("/path/that/does/not/exist.env") == {}


class TestDoneMessageNoSecrets:
    def test_done_message_cookie_count_only(self, capsys):
        cookies = {
            "__Secure-1PSID": "g.a_super_secret_psid_value_12345",
            "__Secure-1PSIDTS": "super_secret_psidts_value_67890",
            "NID": "super_secret_nid_value_abcdef",
        }
        with tempfile.TemporaryDirectory() as td:
            p_dir = Path(td) / "profiles"
            a_dir = Path(td) / "accounts"
            r_dir = Path(td) / "run"
            env_file = Path(td) / "env"
            env_file.write_text("GWS_TOKEN=super_secret_token_123456789\n")

            with patch("login.start_login_processes") as mock_start, \
                 patch("login.wait_for_cookies", return_value=cookies), \
                 patch("login.call_reload") as mock_reload:
                mock_start.return_value = (None, None, None, "vncpass1", p_dir / "acct1")
                ok = login.run_login(
                    "acct1",
                    run_dir=r_dir,
                    profiles_dir=p_dir,
                    accounts_dir=a_dir,
                    env_file=env_file,
                )
                assert ok is True

            captured = capsys.readouterr()
            out = captured.out
            assert "3" in out
            for val in cookies.values():
                assert val not in out
                assert val not in captured.err
            assert "super_secret_token_123456789" not in out
            assert "super_secret_token_123456789" not in captured.err


class TestCleanupHelpers:
    def test_clean_profile_locks(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "SingletonLock").write_text("lock")
            (p / "SingletonCookie").write_text("cookie")
            (p / "other_file.txt").write_text("other")

            login.clean_profile_locks(p)
            assert not (p / "SingletonLock").exists()
            assert not (p / "SingletonCookie").exists()
            assert (p / "other_file.txt").exists()

    def test_stop_session_cleans_files(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            (r / "xvfb.pid").write_text("999999")
            (r / "x11vnc.pid").write_text("999998")
            (r / "chrome.pid").write_text("999997")
            (r / "vnc.pass").write_text("pass")

            login.stop_session(r)
            assert not (r / "xvfb.pid").exists()
            assert not (r / "x11vnc.pid").exists()
            assert not (r / "chrome.pid").exists()
            assert not (r / "vnc.pass").exists()


def _encrypt_chrome_cookie(plain: str, host_key: str, version: int) -> bytes:
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    data = plain.encode("utf-8")
    if version >= 24:
        data = hashlib.sha256(host_key.encode("utf-8")).digest() + data

    padder = padding.PKCS7(128).padder()
    padded = padder.update(data) + padder.finalize()
    key = hashlib.pbkdf2_hmac("sha1", b"peanuts", b"saltysalt", 1, 16)
    cipher = Cipher(algorithms.AES(key), modes.CBC(b" " * 16))
    enc = cipher.encryptor()
    return b"v10" + enc.update(padded) + enc.finalize()


def _create_test_cookie_db(db_path: Path, version: int, cookies_data: list[tuple[str, str, bytes]]) -> None:
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("CREATE TABLE meta (key TEXT, value TEXT)")
    cur.execute("INSERT INTO meta VALUES ('version', ?)", (str(version),))
    cur.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB)")
    for host_key, name, enc_val in cookies_data:
        cur.execute("INSERT INTO cookies VALUES (?, ?, '', ?)", (host_key, name, enc_val))
    conn.commit()
    conn.close()


class TestCookieDbDecryption:
    def test_version_24_exact_decrypt(self):
        pytest.importorskip("cryptography")
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            db_path = Path(f.name)

        psid = "g.a000test_value-123"
        psidts = "g.a000test_psidts_456"
        enc_psid = _encrypt_chrome_cookie(psid, ".google.com", 24)
        enc_psidts = _encrypt_chrome_cookie(psidts, ".google.com", 24)

        _create_test_cookie_db(
            db_path,
            version=24,
            cookies_data=[
                (".google.com", "__Secure-1PSID", enc_psid),
                (".google.com", "__Secure-1PSIDTS", enc_psidts),
            ],
        )
        try:
            cookies = login.extract_cookies(db_path)
            assert cookies is not None
            assert cookies["__Secure-1PSID"] == psid
            assert cookies["__Secure-1PSIDTS"] == psidts
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_version_23_exact_decrypt(self):
        pytest.importorskip("cryptography")
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            db_path = Path(f.name)

        psid = "g.a000test_value-123"
        psidts = "g.a000test_psidts_456"
        enc_psid = _encrypt_chrome_cookie(psid, ".google.com", 23)
        enc_psidts = _encrypt_chrome_cookie(psidts, ".google.com", 23)

        _create_test_cookie_db(
            db_path,
            version=23,
            cookies_data=[
                (".google.com", "__Secure-1PSID", enc_psid),
                (".google.com", "__Secure-1PSIDTS", enc_psidts),
            ],
        )
        try:
            cookies = login.extract_cookies(db_path)
            assert cookies is not None
            assert cookies["__Secure-1PSID"] == psid
            assert cookies["__Secure-1PSIDTS"] == psidts
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_corrupted_value_skipped_and_counted_bad(self, capsys):
        pytest.importorskip("cryptography")
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            db_path = Path(f.name)

        psid = "g.a000test_value-123"
        enc_psid = _encrypt_chrome_cookie(psid, ".google.com", 24)
        corrupted_enc = b"v10bad_corrupted_bytes_that_fail_decryption"

        _create_test_cookie_db(
            db_path,
            version=24,
            cookies_data=[
                (".google.com", "__Secure-1PSID", enc_psid),
                (".google.com", "corrupted_cookie", corrupted_enc),
            ],
        )
        try:
            cookies = login.extract_cookies(db_path)
            assert cookies is not None
            assert cookies["__Secure-1PSID"] == psid
            assert "corrupted_cookie" not in cookies

            captured = capsys.readouterr()
            assert "bad values skipped: 1" in captured.out
            assert "Cookies found: 1" in captured.out
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_prefer_dot_google_com_host(self):
        pytest.importorskip("cryptography")
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            db_path = Path(f.name)

        preferred = "g.a000preferred_google_com"
        secondary = "g.a000secondary_accounts_google"
        enc_preferred = _encrypt_chrome_cookie(preferred, ".google.com", 24)
        enc_secondary = _encrypt_chrome_cookie(secondary, "accounts.google.com", 24)

        _create_test_cookie_db(
            db_path,
            version=24,
            cookies_data=[
                ("accounts.google.com", "__Secure-1PSID", enc_secondary),
                (".google.com", "__Secure-1PSID", enc_preferred),
            ],
        )
        try:
            cookies = login.extract_cookies(db_path)
            assert cookies is not None
            assert cookies["__Secure-1PSID"] == preferred
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_invalid_1psid_exits_nonzero_and_file_not_written(self, capsys):
        bad_psid = "invalid_not_matching_psid_format"
        cookies = {
            "__Secure-1PSID": bad_psid,
            "__Secure-1PSIDTS": "ts_val",
        }
        with tempfile.TemporaryDirectory() as td:
            p_dir = Path(td) / "profiles"
            a_dir = Path(td) / "accounts"
            r_dir = Path(td) / "run"
            env_file = Path(td) / "env"
            env_file.write_text("GWS_TOKEN=test_token\n")

            with patch("login.start_login_processes") as mock_start, \
                 patch("login.wait_for_cookies", return_value=cookies):
                mock_start.return_value = (None, None, None, "vncpass1", p_dir / "acct1")
                with pytest.raises(SystemExit) as excinfo:
                    login.run_login(
                        "acct1",
                        run_dir=r_dir,
                        profiles_dir=p_dir,
                        accounts_dir=a_dir,
                        env_file=env_file,
                    )
                assert excinfo.value.code != 0

            target = a_dir / "acct1.json"
            assert not target.exists()

            captured = capsys.readouterr()
            assert "Login cookies look invalid (decryption problem); account file not written." in captured.out
            assert bad_psid not in captured.out
            assert bad_psid not in captured.err
