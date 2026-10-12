"""Render colour variants for new News and Quote templates (Sections 22 & 23)."""

import base64
import json
import os
import subprocess
import time
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

ROOT_DIR = Path(__file__).resolve().parents[3]
NOTES_ASSETS = ROOT_DIR / ".notes" / "assets"
SEEDS_FILE = ROOT_DIR / "backend" / "app" / "seeds" / "default_templates.json"

NEWS_IMG_PATH = NOTES_ASSETS / "v5_C.jpg"
QUOTE_IMG_PATH = NOTES_ASSETS / "v5_A2.jpg"

OUT_DIR = Path(os.path.expanduser("~/Downloads/motogp-speaker-tests/templates-baru/warna"))
OUT_DIR.mkdir(parents=True, exist_ok=True)

ACCENTS = [
    ("merah", "#E8362B"),
    ("kuning", "#FFC400"),
    ("hijau", "#91F64C"),
    ("oranye", "#FF7A1A"),
    ("biru", "#2F80ED"),
    ("cyan", "#22D3EE"),
    ("ungu", "#9B5CFF"),
    ("pink", "#FF3E8A"),
]


def to_data_uri(path: Path) -> str:
    b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
    ext = path.suffix.lower().lstrip(".")
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"
    return f"data:{mime};base64,{b64}"


def build_contact_sheet(rendered_pairs: list, out_path: Path):
    """WARNA.jpg grid: one row per colour, 2 columns (news, quote), tile ~360px wide,

    header text above each row in white on black.
    """
    tile_w = 360
    tile_h = int(round(360 * (1350 / 1080)))  # 450
    header_h = 36
    row_h = header_h + tile_h
    sheet_w = tile_w * 2  # 720
    sheet_h = row_h * len(rendered_pairs)

    sheet = Image.new("RGB", (sheet_w, sheet_h), (0, 0, 0))
    draw = ImageDraw.Draw(sheet)

    try:
        font = ImageFont.truetype(
            str(ROOT_DIR / "renderer" / "fonts" / "Montserrat-Bold.ttf"), 18
        )
    except Exception:
        font = ImageFont.load_default()

    for idx, (name, hex_code, news_im, quote_im) in enumerate(rendered_pairs):
        y_top = idx * row_h
        draw.rectangle([0, y_top, sheet_w, y_top + header_h], fill=(15, 15, 15))
        label_text = f"{name.upper()}  {hex_code}"
        draw.text((16, y_top + 8), label_text, fill=(255, 255, 255), font=font)

        news_thumb = news_im.resize((tile_w, tile_h), Image.Resampling.LANCZOS).convert(
            "RGB"
        )
        quote_thumb = quote_im.resize(
            (tile_w, tile_h), Image.Resampling.LANCZOS
        ).convert("RGB")

        sheet.paste(news_thumb, (0, y_top + header_h))
        sheet.paste(quote_thumb, (tile_w, y_top + header_h))

    sheet.save(out_path, quality=92)
    print(f"Contact sheet saved: {out_path}")


def main():
    port = 3045
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

        with open(SEEDS_FILE, "r", encoding="utf-8") as f:
            templates = json.load(f)
        base_news = next(t for t in templates if t["name"] == "GP News — Breaking")
        base_quote = next(t for t in templates if t["name"] == "GP Quote")

        news_img_uri = to_data_uri(NEWS_IMG_PATH)
        quote_img_uri = to_data_uri(QUOTE_IMG_PATH)

        rendered_rows = []

        for name, hex_code in ACCENTS:
            # Render News via renderer accent derivation path
            news_payload = {
                "template_json": base_news["template_json"],
                "width": 1080,
                "height": 1350,
                "title": "Bagnaia crashes out in Indonesia: **No feeling, no grip**",
                "label": "BREAKING",
                "image_src": news_img_uri,
                "scale": 1,
                "accent": hex_code,
            }
            news_out = OUT_DIR / f"news_{name}.png"
            resp = httpx.post(
                f"{renderer_url}/render", json=news_payload, timeout=60.0
            )
            resp.raise_for_status()
            news_out.write_bytes(resp.content)
            print(f"Rendered: {news_out}")

            # Render Quote via renderer accent derivation path
            quote_payload = {
                "template_json": base_quote["template_json"],
                "width": 1080,
                "height": 1350,
                "title": "I'm sorry if Raúl is angry, but **he was the one** who touched me. I couldn't disappear.",
                "caption": "Marco **Bezzecchi** on the Raúl clash",
                "image_src": quote_img_uri,
                "scale": 1,
                "accent": hex_code,
            }
            quote_out = OUT_DIR / f"quote_{name}.png"
            resp = httpx.post(
                f"{renderer_url}/render", json=quote_payload, timeout=60.0
            )
            resp.raise_for_status()
            quote_out.write_bytes(resp.content)
            print(f"Rendered: {quote_out}")

            rendered_rows.append(
                (
                    name,
                    hex_code,
                    Image.open(news_out),
                    Image.open(quote_out),
                )
            )

        # Build WARNA.jpg grid
        contact_path = OUT_DIR / "WARNA.jpg"
        build_contact_sheet(rendered_rows, contact_path)

    finally:
        server_proc.terminate()
        server_proc.wait()


if __name__ == "__main__":
    main()
