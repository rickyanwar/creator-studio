"""Read one Google Flow credit balance through the Flow web UI."""

import asyncio
import os
from pathlib import Path
import re
import sys

from playwright.async_api import async_playwright

project, out_dir = sys.argv[1], sys.argv[2]
profile_name = os.getenv("GFLOW_CLI_PROFILE") or Path(out_dir).name.split("-credits-")[-1]
cli_home = os.getenv("GFLOW_CLI_HOME", "/home/gflow/.local/share/gflow-cli")
user_data_dir = f"{cli_home}/profile_{profile_name}"


async def run():
    proxy = (os.getenv("GFLOW_PROXY") or os.getenv("GFLOW_SOCKS5_PROXY") or "").strip()
    if proxy:
        try:
            from gflow_worker.proxy_guard import ensure_proxy_ready
            ensure_proxy_ready(proxy)
        except Exception as exc:
            print(f"CREDITS: unreadable (proxy error: {exc})", flush=True)
            sys.exit(2)

    async with async_playwright() as p:
        args = ["--no-sandbox", "--disable-dev-shm-usage"]
        if proxy:
            args.append(f"--proxy-server={proxy}")
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=True,
            args=args,
        )
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            url = (
                f"https://flow.google.com/project/{project}"
                if (project and project != "(none)" and len(project) > 20)
                else "https://flow.google.com"
            )
            await page.goto(url)
            await asyncio.sleep(5)

            # 1. Tutup panel chat / overlay jika ada
            close_btn = page.locator(
                "button:has(.google-symbols:text-is('close')), button:has-text('close'), [aria-label*='Close']"
            )
            if await close_btn.count():
                try:
                    await close_btn.first.click()
                except Exception:
                    pass
                await asyncio.sleep(1)

            # 2. Tutup modal onboarding / start creating jika ada
            got_it = page.locator(
                "button:has-text('Got it'), button:has-text('Mengerti'), button:has-text('OK'), button:has-text('Start Creating')"
            )
            if await got_it.count():
                try:
                    await got_it.first.click()
                except Exception:
                    pass
                await asyncio.sleep(1)

            # 3. Klik badge PRO / ULTRA / avatar untuk membuka panel akun
            badge = page.locator("button, a, [role=button]").filter(
                has_text=re.compile(r"^\s*(PRO|ULTRA)\s*$")
            )
            if await badge.count():
                try:
                    await badge.first.click()
                except Exception:
                    pass
                await asyncio.sleep(2)
            else:
                # Fallback jika badge tidak ada di landing page: klik avatar profil (di kanan atas)
                avatar = page.locator(
                    "header button, nav button, [aria-haspopup='menu']"
                ).last
                if await avatar.count():
                    try:
                        await avatar.click()
                    except Exception:
                        pass
                    await asyncio.sleep(2)

            await page.screenshot(path=f"{out_dir}/credits.png")

            # Ekstrak seluruh teks yang muncul di layar
            text = await page.evaluate("() => document.body.innerText")

            # Pola kredit bahasa Indonesia ("1.050 kredit") maupun bahasa Inggris ("1,050 credits")
            credit_patterns = [
                re.compile(r"(\d[\d.,]*)\s*(kredit|credits?)\s*(google\s*flow)?", re.I),
                re.compile(r"(\d[\d.,]*)\s*google\s*flow\s*(kredit|credits?)", re.I),
            ]

            found = False
            for line in text.splitlines():
                for pat in credit_patterns:
                    match = pat.search(line)
                    if match and "refund" not in line.lower() and "additional" not in line.lower():
                        raw_val = match.group(1)
                        val = re.sub(r"[.,]", "", raw_val)
                        print(f"CREDITS: {val}", flush=True)
                        found = True
                        break
                if found:
                    break

            if not found:
                print("CREDITS: unknown", flush=True)
        finally:
            await ctx.close()


asyncio.run(run())

