"""Compare GP News (21) and GP Quote (22) templates against approved mockups.

Measures:
- Mean absolute difference overall and in text band (y 820-1350)
- Measured title top/bottom, line count, cap height
- Pill box position/size or quote badge position
- Caption baseline
- Scrim start row
Saves side-by-side (mock | render | diff heat map) to ~/Downloads/template-fix-20261013/
"""

import base64
import copy
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import numpy as np
from PIL import Image

ROOT_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT_DIR / "backend"))

from app.services.template_text_zone import apply_text_zone, apply_gp_design  # noqa: E402

NOTES_DIR = ROOT_DIR / ".notes"
ASSETS_DIR = NOTES_DIR / "assets"
PROD_TEMPLATES_FILE = NOTES_DIR / "prod_gp_templates.json"

OUT_DIR = Path(os.path.expanduser("~/Downloads/template-fix-20261013"))
OUT_DIR.mkdir(parents=True, exist_ok=True)

V5_C_PATH = ASSETS_DIR / "v5_C.jpg"
V5_A2_PATH = ASSETS_DIR / "v5_A2.jpg"

MOCK_PATHS = {
    "news": ASSETS_DIR / "mock_news.png",
    "quote": ASSETS_DIR / "mock_quote.png",
    "quote_long": ASSETS_DIR / "mock_quote_long.png",
}


def to_data_uri(path: Path) -> str:
    b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
    ext = path.suffix.lower().lstrip(".")
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"
    return f"data:{mime};base64,{b64}"


def diff_to_heatmap(diff_2d: np.ndarray) -> np.ndarray:
    """Convert a 2D float difference array [0..255] into an RGB heatmap."""
    val_vis = np.clip(diff_2d / 100.0, 0.0, 1.0)
    r = np.clip(1.5 - np.abs(val_vis * 4.0 - 3.0), 0.0, 1.0)
    g = np.clip(1.5 - np.abs(val_vis * 4.0 - 2.0), 0.0, 1.0)
    b = np.clip(1.5 - np.abs(val_vis * 4.0 - 1.0), 0.0, 1.0)
    rgb = np.stack([r, g, b], axis=-1) * 255.0
    return rgb.astype(np.uint8)


def measure_image(arr: np.ndarray, is_news: bool) -> dict:
    res = {}
    ref_path = V5_C_PATH if is_news else V5_A2_PATH
    ref = np.array(Image.open(ref_path).convert("RGB"), dtype=float)
    # Scrim affects rows below ~700. Scan 600..850 for onset where darkening diff exceeds threshold
    diff_rows = np.mean(np.abs(ref[600:900, 200:800, :] - arr[600:900, 200:800, :]), axis=(1, 2))
    scrim_sub = np.where(diff_rows > 1.5)[0]
    res["scrim_start_row"] = int(scrim_sub[0] + 600) if len(scrim_sub) else None

    if is_news:
        sub = arr[840:1060, :, :]
        r, g, b = sub[:, :, 0], sub[:, :, 1], sub[:, :, 2]
        pill_mask = (r > 180) & (g < 110) & (b < 100)
        p_rows = np.where(np.sum(pill_mask[:, 350:730], axis=1) > 20)[0] + 840
        if len(p_rows):
            p_top, p_bot = p_rows[0], p_rows[-1]
            p_cols = np.where(np.sum(pill_mask[p_top - 840 : p_bot - 840 + 1, :], axis=0) > 10)[0]
            p_left, p_right = p_cols[0], p_cols[-1]
            res["pill_box"] = {
                "left": int(p_left),
                "top": int(p_top),
                "width": int(p_right - p_left + 1),
                "height": int(p_bot - p_top + 1),
            }
        else:
            res["pill_box"] = None
        res["quote_badge"] = None
        res["caption_baseline"] = None
        title_y_min = (res["pill_box"]["top"] + res["pill_box"]["height"] + 5) if res["pill_box"] else 1000
        title_y_max = 1320
    else:
        res["pill_box"] = None
        sub = arr[750:1000, 480:600, :]
        r, g, b = sub[:, :, 0], sub[:, :, 1], sub[:, :, 2]
        tab_mask = (r > 180) & (g < 110) & (b < 100)
        q_rows = np.where(np.sum(tab_mask, axis=1) > 20)[0] + 750
        if len(q_rows):
            q_top = int(q_rows[0])
            row_cnts = np.sum(tab_mask, axis=1)
            q_bot = q_top
            for y_idx in range(q_top - 750 + 40, len(row_cnts)):
                if row_cnts[y_idx] == 0:
                    q_bot = 750 + y_idx - 1
                    break
            sub_tab = arr[q_top : q_bot + 1, 450:630, :]
            str_, stg, stb = sub_tab[:, :, 0], sub_tab[:, :, 1], sub_tab[:, :, 2]
            c_mask = (str_ > 180) & (stg < 110) & (stb < 100)
            c_cols = np.where(np.sum(c_mask, axis=0) > 5)[0] + 450
            res["quote_badge"] = {
                "left": int(c_cols[0]),
                "top": int(q_top),
                "width": int(c_cols[-1] - c_cols[0] + 1),
                "height": int(q_bot - q_top + 1),
            }
        else:
            res["quote_badge"] = None

        cap_sub = arr[1280:1335, 70:1010, :]
        cr, cg, cb = cap_sub[:, :, 0], cap_sub[:, :, 1], cap_sub[:, :, 2]
        cap_mask = ((cr > 180) & (cg > 180) & (cb > 180)) | ((cr > 180) & (cg < 110) & (cb < 100))
        cap_rows = np.where(np.sum(cap_mask, axis=1) > 10)[0] + 1280
        if len(cap_rows):
            res["caption_baseline"] = int(cap_rows[-1])
            res["caption_top"] = int(cap_rows[0])
        else:
            res["caption_baseline"] = None
            res["caption_top"] = None

        title_y_min = (res["quote_badge"]["top"] + res["quote_badge"]["height"] + 15) if res["quote_badge"] else 910
        title_y_max = (res["caption_top"] - 10) if res["caption_top"] else 1280

    t_sub = arr[title_y_min:title_y_max, 70:1010, :]
    tr, tg, tb = t_sub[:, :, 0], t_sub[:, :, 1], t_sub[:, :, 2]
    t_mask = ((tr > 180) & (tg > 180) & (tb > 180)) | ((tr > 180) & (tg < 110) & (tb < 100))
    row_counts = np.sum(t_mask, axis=1)
    lines = []
    in_l = False
    l_start = 0
    for y, cnt in enumerate(row_counts):
        actual_y = y + title_y_min
        if cnt > 15:
            if not in_l:
                in_l = True
                l_start = actual_y
        else:
            if in_l:
                in_l = False
                if actual_y - l_start > 15:
                    lines.append((l_start, actual_y - 1))
    if in_l and (title_y_max - l_start > 15):
        lines.append((l_start, title_y_max - 1))

    res["line_count"] = len(lines)
    if len(lines):
        res["title_top"] = lines[0][0]
        res["title_bottom"] = lines[-1][1]
        res["cap_height"] = lines[0][1] - lines[0][0] + 1
        res["lines"] = lines
    else:
        res["title_top"] = None
        res["title_bottom"] = None
        res["cap_height"] = None
        res["lines"] = []

    return res


def apply_gp_tweaks(t21_json: dict, t22_json: dict) -> tuple[dict, dict]:
    """Return tweaked template JSONs matching Main.dc.html and Quote.dc.html exactly."""
    t21 = apply_gp_design(t21_json, "GP News — Breaking")
    t22 = apply_gp_design(t22_json, "GP Quote")
    return t21, t22


def save_comparison_sheet(out_path: Path, mock_img: Image.Image, render_img: Image.Image, diff_img: Image.Image):
    w, h = mock_img.size
    sheet = Image.new("RGB", (w * 3, h), (10, 10, 10))
    sheet.paste(mock_img, (0, 0))
    sheet.paste(render_img, (w, 0))
    sheet.paste(diff_img, (w * 2, 0))
    sheet.save(out_path, quality=92)
    print(f"Saved side-by-side: {out_path}")


def main():
    port = 3060
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

        with open(PROD_TEMPLATES_FILE, "r", encoding="utf-8") as f:
            templates = json.load(f)

        t21_prod = next(t for t in templates if t["id"] == 21)
        t22_prod = next(t for t in templates if t["id"] == 22)

        v5_c_uri = to_data_uri(V5_C_PATH)
        v5_a2_uri = to_data_uri(V5_A2_PATH)

        # Baseline: prod templates with apply_text_zone helper
        t21_base = apply_text_zone(t21_prod["template_json"], category="gp")
        t22_base = apply_text_zone(t22_prod["template_json"], category="gp")

        # Tweaked: proposed exact JSON tweaks applied
        t21_tweaked, t22_tweaked = apply_gp_tweaks(t21_prod["template_json"], t22_prod["template_json"])

        cases = [
            {
                "name": "news",
                "is_news": True,
                "mock_path": MOCK_PATHS["news"],
                "base_tpl": t21_base,
                "tweaked_tpl": t21_tweaked,
                "payload": {
                    "width": 1080,
                    "height": 1350,
                    "title": "Bagnaia crashes out in Indonesia: **No feeling, no grip**",
                    "label": "BREAKING",
                    "image_src": v5_c_uri,
                    "scale": 1,
                },
            },
            {
                "name": "quote",
                "is_news": False,
                "mock_path": MOCK_PATHS["quote"],
                "base_tpl": t22_base,
                "tweaked_tpl": t22_tweaked,
                "payload": {
                    "width": 1080,
                    "height": 1350,
                    "title": "I'm sorry if Raúl is angry, but **he was the one** who touched me. I couldn't disappear.",
                    "caption": "Marco **Bezzecchi** on the Raúl clash",
                    "image_src": v5_a2_uri,
                    "scale": 1,
                },
            },
            {
                "name": "quote_long",
                "is_news": False,
                "mock_path": MOCK_PATHS["quote_long"],
                "base_tpl": t22_base,
                "tweaked_tpl": t22_tweaked,
                "payload": {
                    "width": 1080,
                    "height": 1350,
                    "title": "I have never thought about **winning the championship**, and I believe Marc Marquez doesn't think about it either.",
                    "caption": "Marco **Bezzecchi** on the title fight",
                    "image_src": v5_a2_uri,
                    "scale": 1,
                },
            },
        ]

        print("\n" + "=" * 70)
        print("GP TEMPLATES VS MOCK COMPARISON")
        print("=" * 70 + "\n")

        for c in cases:
            name = c["name"]
            is_news = c["is_news"]
            mock_im = Image.open(c["mock_path"]).convert("RGB")
            mock_arr = np.array(mock_im, dtype=float)

            # 1. Render baseline
            p_base = {**c["payload"], "template_json": c["base_tpl"]}
            r_base = httpx.post(f"{renderer_url}/render", json=p_base, timeout=60.0)
            r_base.raise_for_status()
            base_temp_path = OUT_DIR / f"_temp_base_{name}.png"
            base_temp_path.write_bytes(r_base.content)
            base_im = Image.open(base_temp_path).convert("RGB")
            base_arr = np.array(base_im, dtype=float)

            # 2. Render tweaked
            p_tweak = {**c["payload"], "template_json": c["tweaked_tpl"]}
            r_tweak = httpx.post(f"{renderer_url}/render", json=p_tweak, timeout=60.0)
            r_tweak.raise_for_status()
            tweak_temp_path = OUT_DIR / f"_temp_tweak_{name}.png"
            tweak_temp_path.write_bytes(r_tweak.content)
            tweak_im = Image.open(tweak_temp_path).convert("RGB")
            tweak_arr = np.array(tweak_im, dtype=float)

            # Measure baseline vs mock
            diff_base = np.abs(mock_arr - base_arr)
            mean_base_all = np.mean(diff_base)
            mean_base_band = np.mean(diff_base[820:1350, :, :])

            # Measure tweaked vs mock
            diff_tweak = np.abs(mock_arr - tweak_arr)
            mean_tweak_all = np.mean(diff_tweak)
            mean_tweak_band = np.mean(diff_tweak[820:1350, :, :])

            # Structural measurements
            m_mock = measure_image(mock_arr.astype(np.uint8), is_news=is_news)
            m_base = measure_image(base_arr.astype(np.uint8), is_news=is_news)
            m_tweak = measure_image(tweak_arr.astype(np.uint8), is_news=is_news)

            # Generate side-by-side sheet: mock | tweaked render | heatmap
            heat_map_arr = diff_to_heatmap(np.mean(diff_tweak, axis=2))
            heat_map_im = Image.fromarray(heat_map_arr)
            out_final_path = OUT_DIR / f"gp_vs_mock_{name}_final.jpg"
            save_comparison_sheet(out_final_path, mock_im, tweak_im, heat_map_im)
            out_sheet_path = OUT_DIR / f"gp_vs_mock_{name}.jpg"
            save_comparison_sheet(out_sheet_path, mock_im, tweak_im, heat_map_im)

            # Also save baseline side-by-side for comparison:
            heat_map_base_arr = diff_to_heatmap(np.mean(diff_base, axis=2))
            heat_map_base_im = Image.fromarray(heat_map_base_arr)
            out_base_sheet_path = OUT_DIR / f"gp_vs_mock_{name}_baseline.jpg"
            save_comparison_sheet(out_base_sheet_path, mock_im, base_im, heat_map_base_im)

            print(f"[{name.upper()}]")
            print(f"  Mean Abs Diff Overall:    Baseline={mean_base_all:.2f} -> Tweaked={mean_tweak_all:.2f}")
            print(f"  Mean Abs Diff Text Band:  Baseline={mean_base_band:.2f} -> Tweaked={mean_tweak_band:.2f}")
            print("  Title Top / Bottom:")
            print(f"    Mock:    top={m_mock['title_top']}, bottom={m_mock['title_bottom']}")
            print(f"    Base:    top={m_base['title_top']}, bottom={m_base['title_bottom']}")
            print(f"    Tweaked: top={m_tweak['title_top']}, bottom={m_tweak['title_bottom']}")
            print("  Line Count & Cap Height:")
            print(f"    Mock:    lines={m_mock['line_count']}, cap_height={m_mock['cap_height']}")
            print(f"    Base:    lines={m_base['line_count']}, cap_height={m_base['cap_height']}")
            print(f"    Tweaked: lines={m_tweak['line_count']}, cap_height={m_tweak['cap_height']}")
            if is_news:
                print("  Pill Box Position / Size:")
                print(f"    Mock:    {m_mock['pill_box']}")
                print(f"    Base:    {m_base['pill_box']}")
                print(f"    Tweaked: {m_tweak['pill_box']}")
            else:
                print("  Quote Badge Position:")
                print(f"    Mock:    {m_mock['quote_badge']}")
                print(f"    Base:    {m_base['quote_badge']}")
                print(f"    Tweaked: {m_tweak['quote_badge']}")
                print("  Caption Baseline:")
                print(f"    Mock:    {m_mock['caption_baseline']}")
                print(f"    Base:    {m_base['caption_baseline']}")
                print(f"    Tweaked: {m_tweak['caption_baseline']}")
            print("  Scrim Start Row:")
            print(f"    Mock:    {m_mock['scrim_start_row']}")
            print(f"    Base:    {m_base['scrim_start_row']}")
            print(f"    Tweaked: {m_tweak['scrim_start_row']}")
            print("-" * 70)

        # ── Render Toto Wolff quote comparison (gp_vs_ref_toto.jpg) ──
        toto_photo_path = NOTES_DIR / "samples" / "photos" / "11944.jpg"
        if not toto_photo_path.exists():
            toto_photo_path = next((NOTES_DIR / "samples" / "photos").glob("*.jpg"))
        toto_payload = {
            "width": 1080,
            "height": 1350,
            "title": "MAYBE WE NEED TO HAVE A LITTLE **CUSHION UNDER YOUR ASS** TO HAVE YOU SIT HIGHER WITH THE **TRACK LIMITS** BUT TODAY WAS REALLY GOOD.",
            "caption": "**TOTO WOLFF** ON KIMI ANTONELLI",
            "image_src": to_data_uri(toto_photo_path),
            "accent": "#E8362B",
            "scale": 1,
            "template_json": t22_tweaked,
        }
        r_toto = httpx.post(f"{renderer_url}/render", json=toto_payload, timeout=60.0)
        r_toto.raise_for_status()
        toto_ren_path = OUT_DIR / "_temp_toto_ren.png"
        toto_ren_path.write_bytes(r_toto.content)
        our_toto_im = Image.open(toto_ren_path).convert("RGB")

        ref_toto_path = ASSETS_DIR / "ref_quote_toto.png"
        ref_toto_raw = Image.open(ref_toto_path).convert("RGB")
        # Letterbox/scale reference to 1080x1350 for side-by-side comparison
        rw, rh = ref_toto_raw.size
        scale_f = min(1080 / rw, 1350 / rh)
        rw_new, rh_new = int(rw * scale_f), int(rh * scale_f)
        ref_toto_scaled = ref_toto_raw.resize((rw_new, rh_new), Image.Resampling.LANCZOS)
        ref_toto_1080 = Image.new("RGB", (1080, 1350), (10, 10, 10))
        ref_toto_1080.paste(ref_toto_scaled, ((1080 - rw_new) // 2, (1350 - rh_new) // 2))

        # Crop text zoom bands:
        # Reference text crop (around y=800..1216 in raw coordinates)
        ref_crop = ref_toto_raw.crop((30, int(840 * rh / 1350), rw - 30, int(1200 * rh / 1350)))
        ref_crop_resized = ref_crop.resize((1080, 450), Image.Resampling.LANCZOS)

        # Our render text crop (around y=840..1200)
        our_crop = our_toto_im.crop((30, 840, 1050, 1200))
        our_crop_resized = our_crop.resize((1080, 450), Image.Resampling.LANCZOS)

        # Assemble gp_vs_ref_toto.jpg:
        # Top row: Reference (1080x1350) | Ours (1080x1350)
        # Bottom row: Zoomed Reference Text (1080x450) | Zoomed Ours Text (1080x450)
        toto_sheet = Image.new("RGB", (2160, 1800), (10, 10, 10))
        toto_sheet.paste(ref_toto_1080, (0, 0))
        toto_sheet.paste(our_toto_im, (1080, 0))
        toto_sheet.paste(ref_crop_resized, (0, 1350))
        toto_sheet.paste(our_crop_resized, (1080, 1350))

        toto_out_path = OUT_DIR / "gp_vs_ref_toto.jpg"
        toto_sheet.save(toto_out_path, quality=92)
        print(f"Saved Toto comparison: {toto_out_path}")

        # Clean up temporary pngs
        for f in OUT_DIR.glob("_temp_*.png"):
            f.unlink()

    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except Exception:
            server_proc.kill()


if __name__ == "__main__":
    main()
