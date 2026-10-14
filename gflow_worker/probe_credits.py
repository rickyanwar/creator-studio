"""Read one Google Flow credit balance through the Flow web UI."""

import asyncio
import os
from pathlib import Path
import re
import sys

from playwright.async_api import async_playwright

CREDIT_PATTERNS = [
    re.compile(r"(\d[\d.,]*)\s*(?:kredit|credits?)\s*(?:google\s*flow)?", re.I),
    re.compile(r"(\d[\d.,]*)\s*google\s*flow\s*(?:kredit|credits?)", re.I),
]


def extract_credits_from_text(text: str) -> int | None:
    """Extract integer credit count from page inner text.

    Supports both English ('1,050 credits', '1,035 Google Flow credits')
    and Indonesian ('1.050 kredit', '1.035 kredit Google Flow').
    Ignores non-balance mentions like 'refund credits' or 'additional credits'.
    """
    if not text:
        return None
    for line in text.splitlines():
        line_clean = line.strip()
        if not line_clean:
            continue
        lower = line_clean.lower()
        if "refund" in lower or "additional" in lower:
            continue
        for pat in CREDIT_PATTERNS:
            match = pat.search(line_clean)
            if match:
                raw_val = match.group(1)
                val = re.sub(r"[.,]", "", raw_val)
                if val.isdigit():
                    return int(val)
    return None


async def run(project: str, out_dir: str):
    proxy = (os.getenv("GFLOW_PROXY") or os.getenv("GFLOW_SOCKS5_PROXY") or "").strip()
    if proxy:
        try:
            from gflow_worker.proxy_guard import ensure_proxy_ready

            ensure_proxy_ready(proxy)
        except Exception as exc:
            print(f"CREDITS: unreadable (proxy error: {exc})", flush=True)
            sys.exit(2)

    profile_name = os.getenv("GFLOW_CLI_PROFILE") or Path(out_dir).name.split("-credits-")[-1]
    cli_home = os.getenv("GFLOW_CLI_HOME", "/home/gflow/.local/share/gflow-cli")
    user_data_dir = f"{cli_home}/profile_{profile_name}"

    args = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--password-store=basic",
        "--disable-blink-features=AutomationControlled",
    ]
    if proxy:
        args.append(f"--proxy-server={proxy}")

    launch_kwargs = {
        "user_data_dir": user_data_dir,
        "headless": True,
        "args": args,
    }
    if Path("/usr/bin/google-chrome").exists():
        launch_kwargs["channel"] = "chrome"

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(**launch_kwargs)
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            url = (
                f"https://flow.google.com/project/{project}"
                if (project and project != "(none)" and len(project) > 20)
                else "https://flow.google.com"
            )
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception:
                pass

            # Polling loop: Google Flow SPA can take several seconds to load over residential proxy.
            # Poll dynamically up to max_poll_seconds instead of failing on hardcoded short sleep.
            max_poll_seconds = float(os.getenv("GFLOW_PROBE_POLL_TIMEOUT", "30"))
            poll_interval = 2.0
            start_time = asyncio.get_event_loop().time()
            credits_val = None
            menu_clicked = False

            while (asyncio.get_event_loop().time() - start_time) < max_poll_seconds:
                # 1. Check if redirected to Google Accounts login/checkpoint
                current_url = page.url or ""
                if "accounts.google.com" in current_url or "ServiceLogin" in current_url:
                    break

                # 2. Dismiss overlay / chat close button if present
                for close_sel in [
                    "button:has(.google-symbols:text-is('close'))",
                    "button:has-text('close')",
                    "[aria-label*='Close']",
                    "[aria-label*='Tutup']",
                ]:
                    try:
                        btn = page.locator(close_sel)
                        if await btn.count():
                            await btn.first.click(timeout=1000)
                            await asyncio.sleep(0.5)
                    except Exception:
                        pass

                # 3. Dismiss onboarding / dialogs ("Got it", "Mengerti", "OK", "Start Creating")
                for ok_sel in ["Got it", "Mengerti", "OK", "Start Creating", "Mulai Berkreasi"]:
                    try:
                        btn = page.locator("button, [role=button]").filter(
                            has_text=re.compile(rf"^\s*{ok_sel}\s*$", re.I)
                        )
                        if await btn.count():
                            await btn.first.click(timeout=1000)
                            await asyncio.sleep(0.5)
                    except Exception:
                        pass

                # 4. Check if credit text is already visible on the page
                try:
                    text = await page.evaluate("() => document.body ? document.body.innerText : ''")
                    credits_val = extract_credits_from_text(text)
                    if credits_val is not None:
                        break
                except Exception:
                    pass

                # 5. If account menu not opened yet, try to click PRO/ULTRA badge or avatar
                if not menu_clicked:
                    try:
                        badge = page.locator("button, a, [role=button]").filter(
                            has_text=re.compile(r"^\s*(PRO|ULTRA)\s*$", re.I)
                        )
                        if await badge.count():
                            await badge.first.click(timeout=1500)
                            menu_clicked = True
                            await asyncio.sleep(1.0)
                        else:
                            avatar = page.locator(
                                "header button, nav button, [aria-haspopup='menu'], [aria-label*='Google Account'], [aria-label*='Akun Google']"
                            ).last
                            if await avatar.count():
                                await avatar.click(timeout=1500)
                                menu_clicked = True
                                await asyncio.sleep(1.0)
                    except Exception:
                        pass

                await asyncio.sleep(poll_interval)

            # Always capture screenshot for diagnostics
            try:
                Path(out_dir).mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=f"{out_dir}/credits.png")
            except Exception:
                pass

            if credits_val is not None:
                print(f"CREDITS: {credits_val}", flush=True)
            else:
                # Final check after screenshot
                try:
                    text = await page.evaluate("() => document.body ? document.body.innerText : ''")
                    credits_val = extract_credits_from_text(text)
                except Exception:
                    credits_val = None

                if credits_val is not None:
                    print(f"CREDITS: {credits_val}", flush=True)
                else:
                    current_url = page.url or ""
                    if "accounts.google.com" in current_url or "ServiceLogin" in current_url:
                        print("CREDITS: unreadable (logged out)", flush=True)
                    else:
                        print("CREDITS: unknown", flush=True)
        finally:
            await ctx.close()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: probe_credits.py <project> <out_dir>", file=sys.stderr)
        sys.exit(1)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    asyncio.run(run(sys.argv[1], sys.argv[2]))
