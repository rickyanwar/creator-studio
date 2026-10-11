"""Read one Google Flow credit balance through the Flow web UI."""

import asyncio
import re
import sys

from gflow_cli.api.transports import migrated_composer as mc
from gflow_cli.cli import main


project, out_dir = sys.argv[1], sys.argv[2]
original_select = mc.MigratedComposer._select
CREDIT_LINE = re.compile(r"(\d[\d.,]*)\s*(kredit|credits?)\b", re.I)


# Adapted from the owner's Hermes google-flow skill.
async def probe(self, page, pane, *, axis, lig=None, text=None):
    if axis != "duration":
        return await original_select(self, page, pane, axis=axis, lig=lig, text=text)
    for _ in range(2):
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.5)
    badge = page.locator("button, a, [role=button]").filter(has_text=re.compile(r"^\s*(PRO|ULTRA)\s*$"))
    if await badge.count():
        await badge.first.click(timeout=5000)
        await asyncio.sleep(2)
    body = await page.locator("body").inner_text()
    await page.screenshot(path=f"{out_dir}/credits.png")
    for line in body.splitlines():
        match = CREDIT_LINE.search(line)
        if match and "flow" in line.lower() and "refund" not in line.lower():
            print(f"CREDITS: {re.sub(r'[.,]', '', match.group(1))}", flush=True)
            break
    else:
        print("CREDITS: unknown", flush=True)
    raise SystemExit("PROBE: aborted before submit (no credits spent)")


mc.MigratedComposer._select = probe
sys.argv = ["gflow", "video", "t2v", "credit probe", "--model", "omni-flash", "--duration", "10",
            "--aspect", "9:16", "--project", project, "-o", f"{out_dir}/never.mp4"]
main()
