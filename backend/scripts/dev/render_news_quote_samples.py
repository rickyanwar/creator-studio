"""Render proof samples for new News and Quote templates."""

import base64
import json
import os
import subprocess
import time
from pathlib import Path

import httpx
import numpy as np
from PIL import Image

OUT_DIR = Path(os.path.expanduser("~/Downloads/motogp-speaker-tests/templates-baru"))
OUT_DIR.mkdir(parents=True, exist_ok=True)

ROOT_DIR = Path(__file__).resolve().parents[3]
NOTES_ASSETS = ROOT_DIR / ".notes" / "assets"
SEEDS_FILE = ROOT_DIR / "backend" / "app" / "seeds" / "default_templates.json"

NEWS_IMG_PATH = NOTES_ASSETS / "v5_C.jpg"
QUOTE_IMG_PATH = NOTES_ASSETS / "v5_A2.jpg"

MOCK_NEWS_PATH = NOTES_ASSETS / "mock_news.png"
MOCK_QUOTE_PATH = NOTES_ASSETS / "mock_quote.png"
MOCK_QUOTE_LONG_PATH = NOTES_ASSETS / "mock_quote_long.png"

def to_data_uri(path: Path) -> str:
    b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
    ext = path.suffix.lower().lstrip(".")
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"
    return f"data:{mime};base64,{b64}"

def get_templates():
    with open(SEEDS_FILE, "r", encoding="utf-8") as f:
        templates = json.load(f)
    news_tpl = next(t for t in templates if t["name"] == "GP News — Breaking")
    quote_tpl = next(t for t in templates if t["name"] == "GP Quote")
    return news_tpl, quote_tpl

def check_quote_badge_pixels(img_path: Path):
    im = Image.open(img_path).convert("RGB")
    arr = np.array(im)
    # Search in badge rows for the red tab and streak
    # Depending on title height/anchor, the quote badge top ranges from ~860 to ~960
    y_min, y_max = 850, 980
    sub = arr[y_min:y_max, :, :]
    r, g, b = sub[:, :, 0], sub[:, :, 1], sub[:, :, 2]
    tab_mask = (r > 200) & (g < 110) & (b < 100)

    # In quote badge rows, the tab is 119px wide centered at 540.
    # Exclude title rows by looking only at rows where mid tab is present
    mid_tab = tab_mask[:, 480:600]
    row_counts = np.sum(mid_tab, axis=1)
    badge_row_indices = np.where(row_counts > 50)[0]
    assert len(badge_row_indices) > 0, f"No red tab rows found in {img_path}"
    # Use the 58px band of the tab
    sub_tab_mask = tab_mask[badge_row_indices[0]:badge_row_indices[0] + 60, :]

    # Densest columns for the 119px tab
    tab_col_counts = np.sum(sub_tab_mask, axis=0)
    tab_cols = np.where(tab_col_counts > 25)[0]
    assert len(tab_cols) > 0, f"No red tab found in {img_path}"
    tab_min_x, tab_max_x = tab_cols[0], tab_cols[-1]
    tab_center_x = (tab_min_x + tab_max_x) / 2.0

    # Streak pixels on both sides: x < 300 and x > 780
    streak_mask = (r > 120) & (g < 90) & (b < 90)
    left_streak_count = np.sum(streak_mask[:, :300])
    right_streak_count = np.sum(streak_mask[:, 780:])

    print(f"[{img_path.name}] tab_min_x={tab_min_x}, tab_max_x={tab_max_x}, tab_center_x={tab_center_x:.1f}, "
          f"left_streak_pixels={left_streak_count}, right_streak_pixels={right_streak_count}")

    assert abs(tab_center_x - 540) <= 4, f"Tab center {tab_center_x} not 540 ± 4 in {img_path}"
    assert left_streak_count > 50, f"Left streak missing in {img_path}"
    assert right_streak_count > 50, f"Right streak missing in {img_path}"

def check_news_pill_pixels(img_path: Path):
    im = Image.open(img_path).convert("RGB")
    arr = np.array(im)
    # The pill sits above the title, in y around [940, 1040]
    y_min, y_max = 940, 1040
    sub = arr[y_min:y_max, :, :]
    r, g, b = sub[:, :, 0], sub[:, :, 1], sub[:, :, 2]
    pill_mask = (r > 190) & (g < 100) & (b < 90)

    col_counts = np.sum(pill_mask, axis=0)
    pill_cols = np.where(col_counts > 30)[0]
    assert len(pill_cols) > 0, f"No red pill found in {img_path}"
    pill_min_x, pill_max_x = pill_cols[0], pill_cols[-1]
    pill_center_x = (pill_min_x + pill_max_x) / 2.0

    # White text pixels inside the pill area (r > 230, g > 230, b > 230)
    white_mask = (r > 230) & (g > 230) & (b > 230)
    white_cols = np.where(np.sum(white_mask, axis=0) > 5)[0]
    white_cols = [x for x in white_cols if pill_min_x <= x <= pill_max_x]
    assert len(white_cols) > 0, f"No white text found inside pill in {img_path}"
    text_min_x, text_max_x = white_cols[0], white_cols[-1]
    text_center_x = (text_min_x + text_max_x) / 2.0

    margin_left = text_min_x - pill_min_x
    margin_right = pill_max_x - text_min_x

    # Section 18: Corner radius ≈ height / 2.
    # Check that pill's leftmost red column at its vertical middle row is about h/2 further left
    # than at its top row, within 25 percent.
    row_red_counts = np.sum(pill_mask, axis=1)
    pill_rows = np.where(row_red_counts > 20)[0]
    assert len(pill_rows) > 0, f"No pill rows in {img_path}"
    top_r = pill_rows[0]
    bot_r = pill_rows[-1]
    mid_r = (top_r + bot_r) // 2
    h = bot_r - top_r + 1

    top_cols = np.where(pill_mask[top_r, :])[0]
    mid_cols = np.where(pill_mask[mid_r, :])[0]
    assert len(top_cols) > 0 and len(mid_cols) > 0, f"Missing rows in pill mask for {img_path}"
    left_top = top_cols[0]
    left_mid = mid_cols[0]
    dx = left_top - left_mid
    expected_dx = h / 2.0
    dx_ratio = dx / expected_dx

    print(f"[{img_path.name}] pill_x=[{pill_min_x}, {pill_max_x}], pill_center={pill_center_x:.1f}, "
          f"text_x=[{text_min_x}, {text_max_x}], text_center={text_center_x:.1f}, "
          f"margins=({margin_left}, {margin_right}), pill_h={h}, dx={dx}, expected_dx={expected_dx:.1f}, dx_ratio={dx_ratio:.3f}")

    assert abs(pill_center_x - 540) <= 4, f"Pill center {pill_center_x} not 540 ± 4 in {img_path}"
    assert abs(text_center_x - 540) <= 4, f"Text center {text_center_x} not 540 ± 4 in {img_path}"
    assert margin_left >= 20, f"Left margin {margin_left} < 20 in {img_path}"
    assert margin_right >= 20, f"Right margin {margin_right} < 20 in {img_path}"
    assert 0.75 <= dx_ratio <= 1.25, f"Pill corner radius dx ratio {dx_ratio:.3f} not within 25% of h/2 in {img_path}"

def check_title_line_spacing_and_glow(img_path: Path):
    from PIL import ImageFilter
    im = Image.open(img_path).convert("RGB")
    arr = np.array(im)
    r, g, b = arr[:, :, 0].astype(float), arr[:, :, 1].astype(float), arr[:, :, 2].astype(float)
    luma = 0.299 * r + 0.587 * g + 0.114 * b

    # Section 16 line spacing assertion on news_short_breaking:
    # Title sits in y in [1060, 1310], x in [70, 1010]
    title_sub = arr[1060:1310, 70:1010]
    tr = title_sub[:, :, 0].astype(float)
    tg = title_sub[:, :, 1].astype(float)
    tb = title_sub[:, :, 2].astype(float)
    sub_luma = luma[1060:1310, 70:1010]

    # White title letters in line 1 and line 2
    # In news_short_breaking, line 1 is "BAGNAIA CRASHES", line 2 is "OUT IN INDONESIA: NO", line 3 is "FEELING, NO GRIP" (red)
    # Detect line rows by summing text pixels across width
    text_mask = ((tr > 200) & (tg > 200) & (tb > 200)) | ((tr > 190) & (tg < 110) & (tb < 100))
    row_counts = np.sum(text_mask, axis=1)

    # Find line vertical bands
    rows_with_text = np.where(row_counts > 50)[0]
    assert len(rows_with_text) > 0, "No title lines found"

    # Split into continuous row clusters
    lines = []
    cur_line = [rows_with_text[0]]
    for y in rows_with_text[1:]:
        if y == cur_line[-1] + 1:
            cur_line.append(y)
        else:
            if len(cur_line) > 20:  # Valid line height
                lines.append((cur_line[0], cur_line[-1]))
            cur_line = [y]
    if len(cur_line) > 20:
        lines.append((cur_line[0], cur_line[-1]))

    assert len(lines) >= 2, f"Expected at least 2 title lines, found {len(lines)}"
    l1_top, l1_bot = lines[0]
    l2_top, l2_bot = lines[1]
    cap_height = l1_bot - l1_top
    line_gap = l2_top - l1_bot
    spacing_ratio = line_gap / cap_height

    print(f"[{img_path.name}] line1=[{l1_top}, {l1_bot}], line2=[{l2_top}, {l2_bot}], "
          f"cap_height={cap_height}, line_gap={line_gap}, ratio={spacing_ratio:.3f}")
    assert 0.40 <= spacing_ratio <= 0.55, f"Line gap ÷ cap height {spacing_ratio:.3f} not in 0.40-0.55"

    # Glow assertions (Section 16, 17, 18)
    # White title letters on line 1:
    white_mask_l1 = (tr > 200) & (tg > 200) & (tb > 200)
    white_mask_l1[l1_bot + 10:, :] = False

    im_white = Image.fromarray((white_mask_l1 * 255).astype(np.uint8))
    d2 = np.array(im_white.filter(ImageFilter.MaxFilter(5))) > 0
    d5 = np.array(im_white.filter(ImageFilter.MaxFilter(11))) > 0
    d9 = np.array(im_white.filter(ImageFilter.MaxFilter(19))) > 0
    d16 = np.array(im_white.filter(ImageFilter.MaxFilter(33))) > 0

    ring_2_5 = d5 & (~d2) & (~white_mask_l1)
    ring_10_16 = d16 & (~d9) & (~white_mask_l1)
    ring_2_5[l1_bot + 10:, :] = False
    ring_10_16[l1_bot + 10:, :] = False

    mean_luma_2_5 = float(np.mean(sub_luma[ring_2_5]))
    mean_luma_10_16 = float(np.mean(sub_luma[ring_10_16]))
    print(f"[{img_path.name}] white ring mean_luma_2_5={mean_luma_2_5:.2f}, mean_luma_10_16={mean_luma_10_16:.2f}")

    assert 12 <= mean_luma_2_5 <= 30, f"White ring luma {mean_luma_2_5:.2f} not in range 12-30"
    assert mean_luma_2_5 > mean_luma_10_16, f"White ring luma {mean_luma_2_5:.2f} not > {mean_luma_10_16:.2f}"

    # Red title glow ring and white title glow ring colour assertions (Section 17 & 18)
    # Line 3 is pure red highlight text
    l3_top = lines[2][0] if len(lines) > 2 else l2_top
    red_hl_mask = (tr > 190) & (tg < 100) & (tb < 90)
    red_hl_mask[:l3_top - 5, :] = False

    im_red = Image.fromarray((red_hl_mask * 255).astype(np.uint8))
    dr1 = np.array(im_red.filter(ImageFilter.MaxFilter(3))) > 0
    dr5 = np.array(im_red.filter(ImageFilter.MaxFilter(11))) > 0
    dr6 = np.array(im_red.filter(ImageFilter.MaxFilter(13))) > 0
    ring_red_2_5 = dr5 & (~dr1) & (~red_hl_mask)
    ring_red_2_6 = dr6 & (~dr1) & (~red_hl_mask)
    ring_red_2_5[:l3_top - 5, :] = False
    ring_red_2_6[:l3_top - 5, :] = False

    red_ring_r = float(np.mean(tr[ring_red_2_5]))
    red_ring_g = float(np.mean(tg[ring_red_2_5]))
    red_ring_b = float(np.mean(tb[ring_red_2_5]))
    red_ring_2_6_r = float(np.mean(tr[ring_red_2_6]))

    # Pure white ring (line 1, away from red letters)
    white_ring_r = float(np.mean(tr[ring_2_5]))
    white_ring_g = float(np.mean(tg[ring_2_5]))
    white_ring_b = float(np.mean(tb[ring_2_5]))

    print(f"[{img_path.name}] red ring 2-5 mean RGB: ({red_ring_r:.2f}, {red_ring_g:.2f}, {red_ring_b:.2f}) "
          f"[r-g={red_ring_r - red_ring_g:.2f}, r-b={red_ring_r - red_ring_b:.2f}]")
    print(f"[{img_path.name}] red ring 2-6 mean R: {red_ring_2_6_r:.2f}")
    print(f"[{img_path.name}] white ring mean RGB: ({white_ring_r:.2f}, {white_ring_g:.2f}, {white_ring_b:.2f}) "
          f"[|r-g|={abs(white_ring_r - white_ring_g):.2f}]")

    assert red_ring_r > red_ring_g + 15 and red_ring_r > red_ring_b + 15, "Red ring is not reddish"
    assert abs(white_ring_r - white_ring_g) < 15, "White ring is not neutral"
    assert 15 <= red_ring_2_6_r <= 45, f"Red ring 2-6px R {red_ring_2_6_r:.2f} not in range"


def check_rgb_split_chroma_fringe(img_path: Path):
    """Section 19 & 20: Pixel assertion on white title letters.
    The pixel 1 px left of a left edge has r > g + 15.
    The pixel 1 px right of a right edge has g > r + 5 and b > r + 5.
    Check at least 20 edges and report the pass ratio, which must be >= 80%.
    Also assert that the mean saturation of the fringe at 1 px is lower than section 19 (< 0.45).
    """
    im = Image.open(img_path).convert("RGB")
    arr = np.array(im)
    r = arr[..., 0].astype(float)
    g = arr[..., 1].astype(float)
    b = arr[..., 2].astype(float)

    # Focus on title region y in 1060..1310
    y_min, y_max = 1060, 1310
    sub_r = r[y_min:y_max, :]
    sub_g = g[y_min:y_max, :]
    sub_b = b[y_min:y_max, :]

    # White text mask on non-highlight title letters (line 1)
    white_mask = (sub_r > 200) & (sub_g > 200) & (sub_b > 200)
    white_mask[75:, :] = False

    # Find isolated white letter edges on rows with white text
    left_edges = []
    right_edges = []

    h, w = white_mask.shape
    for y in range(h):
        # Scan across row for white pixel edges
        for x in range(3, w - 3):
            # Left edge: (x-1 is not white, x is white)
            if not white_mask[y, x - 1] and white_mask[y, x]:
                if not white_mask[y, x - 2]:
                    left_edges.append((y, x))
            # Right edge: (x is white, x+1 is not white)
            if white_mask[y, x] and not white_mask[y, x + 1]:
                if not white_mask[y, x + 2]:
                    right_edges.append((y, x))

    # Evaluate edges at 1px outside
    passed_left = 0
    total_left = 0
    sats_left = []
    for y, x in left_edges:
        px_r = sub_r[y, x - 1]
        px_g = sub_g[y, x - 1]
        px_b = sub_b[y, x - 1]
        if px_r > px_g + 15:
            passed_left += 1
        total_left += 1
        mx = max(px_r, px_g, px_b)
        mn = min(px_r, px_g, px_b)
        sats_left.append((mx - mn) / mx if mx > 0 else 0.0)

    passed_right = 0
    total_right = 0
    sats_right = []
    for y, x in right_edges:
        px_r = sub_r[y, x + 1]
        px_g = sub_g[y, x + 1]
        px_b = sub_b[y, x + 1]
        if px_g > px_r + 5 and px_b > px_r + 5:
            passed_right += 1
        total_right += 1
        mx = max(px_r, px_g, px_b)
        mn = min(px_r, px_g, px_b)
        sats_right.append((mx - mn) / mx if mx > 0 else 0.0)

    total_edges = total_left + total_right
    total_passed = passed_left + passed_right
    pass_ratio = total_passed / total_edges if total_edges > 0 else 0.0
    mean_sat = float((np.mean(sats_left) + np.mean(sats_right)) / 2.0)

    print(f"[{img_path.name}] Chroma fringe check (1px): total_edges={total_edges} (left={total_left}, right={total_right}), "
          f"passed={total_passed} (left={passed_left}, right={passed_right}), pass_ratio={pass_ratio * 100:.2f}%, mean_sat={mean_sat:.4f}")

    assert total_edges >= 20, f"Expected at least 20 edges to check, got {total_edges}"
    assert pass_ratio >= 0.80, f"Chroma fringe pass ratio {pass_ratio * 100:.2f}% is below 80%"
    assert mean_sat < 0.45, f"Mean saturation at 1px {mean_sat:.4f} is not lower than section 19 (< 0.45)"


def main():
    port = 3042
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

        news_tpl, quote_tpl = get_templates()
        news_img_uri = to_data_uri(NEWS_IMG_PATH)
        quote_img_uri = to_data_uri(QUOTE_IMG_PATH)

        samples = [
            {
                "filename": "news_short_breaking.png",
                "tpl": news_tpl,
                "payload": {
                    "template_json": news_tpl["template_json"],
                    "width": 1080,
                    "height": 1350,
                    "title": "Bagnaia crashes out in Indonesia: **No feeling, no grip**",
                    "label": "BREAKING",
                    "image_src": news_img_uri,
                    "scale": 1,
                },
            },
            {
                "filename": "news_short_normal.png",
                "tpl": news_tpl,
                "payload": {
                    "template_json": news_tpl["template_json"],
                    "width": 1080,
                    "height": 1350,
                    "title": "Bagnaia crashes out in Indonesia: **No feeling, no grip**",
                    "label": "MOTOGP NEWS",
                    "image_src": news_img_uri,
                    "scale": 1,
                },
            },
            {
                "filename": "news_long.png",
                "tpl": news_tpl,
                "payload": {
                    "template_json": news_tpl["template_json"],
                    "width": 1080,
                    "height": 1350,
                    "title": "Bagnaia crashes out in Indonesia: **No feeling, no grip** — the two-time champion falls to P9 in the standings with just five races left",
                    "label": "BREAKING",
                    "image_src": news_img_uri,
                    "scale": 1,
                },
            },
            {
                "filename": "quote_short.png",
                "tpl": quote_tpl,
                "payload": {
                    "template_json": quote_tpl["template_json"],
                    "width": 1080,
                    "height": 1350,
                    "title": "I'm sorry if Raúl is angry, but **he was the one** who touched me. I couldn't disappear.",
                    "caption": "Marco **Bezzecchi** on the Raúl clash",
                    "image_src": quote_img_uri,
                    "scale": 1,
                },
            },
            {
                "filename": "quote_long.png",
                "tpl": quote_tpl,
                "payload": {
                    "template_json": quote_tpl["template_json"],
                    "width": 1080,
                    "height": 1350,
                    "title": "I have never thought about **winning the championship**, and I believe Marc Marquez doesn't think about it either.",
                    "caption": "Marco **Bezzecchi** on the title fight",
                    "image_src": quote_img_uri,
                    "scale": 1,
                },
            },
            {
                "filename": "quote_very_long.png",
                "tpl": quote_tpl,
                "payload": {
                    "template_json": quote_tpl["template_json"],
                    "width": 1080,
                    "height": 1350,
                    "title": "I'm happy, **I like the new format!** Having two qualifyings is good because a bad Friday won't ruin both races.",
                    "caption": "Marco **Bezzecchi** on pressure",
                    "image_src": quote_img_uri,
                    "scale": 1,
                },
            },
        ]

        rendered_images = {}
        for s in samples:
            out_path = OUT_DIR / s["filename"]
            resp = httpx.post(f"{renderer_url}/render", json=s["payload"], timeout=60.0)
            resp.raise_for_status()
            out_path.write_bytes(resp.content)
            print(f"Rendered: {out_path}")
            rendered_images[s["filename"]] = Image.open(out_path)

        print("\n--- Running Section 15 Mandatory Pixel Assertions ---")
        for q_name in ("quote_short.png", "quote_long.png", "quote_very_long.png"):
            check_quote_badge_pixels(OUT_DIR / q_name)

        for n_name in ("news_short_breaking.png", "news_short_normal.png"):
            check_news_pill_pixels(OUT_DIR / n_name)
        print("All Section 15 pixel assertions passed!\n")

        print("--- Running Section 16 and 17 Pixel Assertions ---")
        check_title_line_spacing_and_glow(OUT_DIR / "news_short_breaking.png")
        print("All Section 16 & 17 pixel assertions passed!\n")

        print("--- Running Section 19 RGB-split Chroma Fringe Pixel Assertions ---")
        check_rgb_split_chroma_fringe(OUT_DIR / "news_short_breaking.png")
        print("All Section 19 pixel assertions passed!\n")

        # Contact sheet SEMUA.jpg (3 cols x 2 rows)
        cols = 3
        rows = 2
        card_w, card_h = 1080, 1350
        thumb_w, thumb_h = card_w // 2, card_h // 2
        sheet_w = thumb_w * cols
        sheet_h = thumb_h * rows

        sheet = Image.new("RGB", (sheet_w, sheet_h), (20, 20, 20))
        img_list = [
            rendered_images["news_short_breaking.png"],
            rendered_images["news_short_normal.png"],
            rendered_images["news_long.png"],
            rendered_images["quote_short.png"],
            rendered_images["quote_long.png"],
            rendered_images["quote_very_long.png"],
        ]
        for idx, img in enumerate(img_list):
            r = idx // cols
            c = idx % cols
            thumb = img.resize((thumb_w, thumb_h), Image.Resampling.LANCZOS).convert("RGB")
            sheet.paste(thumb, (c * thumb_w, r * thumb_h))

        contact_path = OUT_DIR / "SEMUA.jpg"
        sheet.save(contact_path, quality=90)
        print(f"Contact sheet saved: {contact_path}")

        # COMPARE.jpg: Approved mock-up vs render side-by-side
        compare_pairs = [
            (Image.open(MOCK_NEWS_PATH), rendered_images["news_short_breaking.png"]),
            (Image.open(MOCK_QUOTE_PATH), rendered_images["quote_short.png"]),
            (Image.open(MOCK_QUOTE_LONG_PATH), rendered_images["quote_long.png"]),
        ]

        comp_cols = 2
        comp_rows = 3
        comp_thumb_w = card_w // 2
        comp_thumb_h = card_h // 2
        comp_sheet = Image.new("RGB", (comp_thumb_w * comp_cols, comp_thumb_h * comp_rows), (15, 15, 15))

        for row_idx, (mock_im, render_im) in enumerate(compare_pairs):
            m_thumb = mock_im.resize((comp_thumb_w, comp_thumb_h), Image.Resampling.LANCZOS).convert("RGB")
            r_thumb = render_im.resize((comp_thumb_w, comp_thumb_h), Image.Resampling.LANCZOS).convert("RGB")
            comp_sheet.paste(m_thumb, (0, row_idx * comp_thumb_h))
            comp_sheet.paste(r_thumb, (comp_thumb_w, row_idx * comp_thumb_h))

        compare_path = OUT_DIR / "COMPARE.jpg"
        comp_sheet.save(compare_path, quality=90)
        print(f"Compare sheet saved: {compare_path}")

    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except Exception:
            server_proc.kill()

if __name__ == "__main__":
    main()
