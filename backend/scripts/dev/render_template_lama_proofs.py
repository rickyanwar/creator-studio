import base64
import json
import os
import subprocess
import time
from pathlib import Path

import httpx
import numpy as np
from PIL import Image

ROOT_DIR = Path(__file__).resolve().parents[3]
NOTES_ASSETS = ROOT_DIR / ".notes" / "assets"
OUT_DIR = Path(os.path.expanduser("~/Downloads/motogp-speaker-tests/template-lama"))
OUT_DIR.mkdir(parents=True, exist_ok=True)

ORIG_FILE = Path("/tmp/default_templates_orig.json")
UPGRADED_FILE = ROOT_DIR / "backend" / "app" / "seeds" / "default_templates.json"

NEWS_IMG_PATH = NOTES_ASSETS / "v5_C.jpg"
QUOTE_IMG_PATH = NOTES_ASSETS / "v5_A2.jpg"

SHORT_TITLE_NEWS = "Bagnaia crashes out in Indonesia: **No feeling, no grip**"
LONG_TITLE_NEWS = "Bagnaia crashes out in Indonesia: **No feeling, no grip** — the two-time champion falls to P9 in the standings with just five races left"

SHORT_TITLE_QUOTE = "I'm sorry if Raúl is angry, but **he was the one** who touched me. I couldn't disappear."
LONG_TITLE_QUOTE = "I have never thought about **winning the championship**, and I believe Marc Marquez doesn't think about it either."

LONG_SUBTITLE_NEWS = "The two-time champion falls to **P9 in the standings** with just five races left in the season"
LONG_SUBTITLE_QUOTE = 'Francesco "Pecco" Bagnaia'

def to_data_uri(path: Path) -> str:
    b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
    ext = path.suffix.lower().lstrip(".")
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"
    return f"data:{mime};base64,{b64}"

def main():
    port = 3055
    env = os.environ.copy()
    env["PORT"] = str(port)
    server_proc = subprocess.Popen(
        ["node", "server.js"],
        cwd=str(ROOT_DIR / "renderer"),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    renderer_url = f"http://127.0.0.1:{port}"

    try:
        for _ in range(30):
            try:
                r = httpx.get(f"{renderer_url}/health", timeout=1.0)
                if r.status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(0.5)

        with open(ORIG_FILE, "r", encoding="utf-8") as f:
            orig_templates = json.load(f)

        with open(UPGRADED_FILE, "r", encoding="utf-8") as f:
            upgraded_templates = json.load(f)[:19]

        news_img_uri = to_data_uri(NEWS_IMG_PATH)
        quote_img_uri = to_data_uri(QUOTE_IMG_PATH)

        results = []
        # For sheet SEMUA.jpg: 19 rows x 4 columns (before_short, after_short, before_long, after_long)
        # card is 1080x1350
        sheet_rows = 19
        sheet_cols = 4
        thumb_w, thumb_h = 270, 337  # 1/4 size
        sheet = Image.new("RGB", (thumb_w * sheet_cols, thumb_h * sheet_rows), (15, 15, 15))

        short_diffs = []
        overlap_results = []
        font_size_reports = []

        for idx in range(19):
            t_orig = orig_templates[idx]
            t_up = upgraded_templates[idx]
            name = t_orig["name"]
            cat = t_orig.get("category", "news")
            safe_name = name.replace(" ", "_").replace("·", "").replace("—", "-").replace("(", "").replace(")", "").replace("/", "_")

            is_quote = cat == "quote" or "Quote" in name
            img_uri = quote_img_uri if is_quote else news_img_uri
            short_t = SHORT_TITLE_QUOTE if is_quote else SHORT_TITLE_NEWS
            long_t = LONG_TITLE_QUOTE if is_quote else LONG_TITLE_NEWS
            long_sub = LONG_SUBTITLE_QUOTE if is_quote else LONG_SUBTITLE_NEWS

            # Report subtitle font sizes
            orig_objs = t_orig["template_json"].get("objects", [])
            up_objs = t_up["template_json"].get("objects", [])
            orig_sub_obj = next((o for o in orig_objs if o.get("placeholderRole") == "subtitle"), None)
            up_sub_obj = next((o for o in up_objs if o.get("placeholderRole") == "subtitle"), None)
            if orig_sub_obj and up_sub_obj:
                font_size_reports.append((name, orig_sub_obj.get("fontSize"), up_sub_obj.get("fontSize"), up_sub_obj.get("subtitleMinFontSize")))

            # 4 renders per template:
            # 1. before-short
            # 2. after-short
            # 3. before-long
            # 4. after-long
            render_configs = [
                ("before_short", t_orig["template_json"], short_t, None, 0),
                ("after_short", t_up["template_json"], short_t, None, 1),
                ("before_long", t_orig["template_json"], long_t, long_sub, 2),
                ("after_long", t_up["template_json"], long_t, long_sub, 3),
            ]

            rendered_imgs = {}
            for col_name, tjson, title_text, sub_text, col_idx in render_configs:
                payload = {
                    "template_json": tjson,
                    "width": 1080,
                    "height": 1350,
                    "title": title_text,
                    "image_src": img_uri,
                    "scale": 1,
                }
                if sub_text is not None:
                    payload["subtitle"] = sub_text
                if is_quote:
                    payload["caption"] = "Marco **Bezzecchi** on the Raúl clash"

                out_file = OUT_DIR / f"{idx+1:02d}_{safe_name}_{col_name}.png"
                resp = httpx.post(f"{renderer_url}/render", json=payload, timeout=60.0)
                resp.raise_for_status()
                out_file.write_bytes(resp.content)
                img = Image.open(out_file).convert("RGB")
                rendered_imgs[col_name] = img

                thumb = img.resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
                sheet.paste(thumb, (col_idx * thumb_w, idx * thumb_h))

            # Pixel difference between before_short and after_short
            arr_before = np.array(rendered_imgs["before_short"]).astype(float)
            arr_after = np.array(rendered_imgs["after_short"]).astype(float)
            mean_abs_diff = float(np.mean(np.abs(arr_before - arr_after)))
            short_diffs.append((name, mean_abs_diff))

            print(f"[{idx+1:02d}/19] {name}: short mean abs diff = {mean_abs_diff:.4f}")

        sheet_out = OUT_DIR / "SEMUA.jpg"
        sheet.save(sheet_out, quality=90)
        print(f"\nSaved contact sheet: {sheet_out}")

        print("\n--- Summary of Short Before/After Mean Pixel Diffs (< 2) ---")
        for name, diff in short_diffs:
            status = "PASS" if diff < 2.0 else "WARN"
            print(f"  {status}: {name} -> diff={diff:.4f}")

        print("\n--- Subtitle Font Size Report (Before vs After) ---")
        for name, bfs, afs, min_fs in font_size_reports:
            print(f"  {name}: base fontSize={bfs} -> base fontSize={afs}, subtitleMinFontSize={min_fs}")

    finally:
        server_proc.terminate()

if __name__ == "__main__":
    main()
