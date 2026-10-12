import json
import os
import subprocess
import time
from pathlib import Path
import httpx
import numpy as np
from PIL import Image

ROOT_DIR = Path(__file__).resolve().parents[3]
SEEDS_FILE = ROOT_DIR / "backend" / "app" / "seeds" / "default_templates.json"
NOTES_ASSETS = ROOT_DIR / ".notes" / "assets"
NEWS_IMG_PATH = NOTES_ASSETS / "v5_C.jpg"
QUOTE_IMG_PATH = NOTES_ASSETS / "v5_A2.jpg"

PREVIEW_DIR = Path(os.path.expanduser("~/Downloads/motogp-speaker-tests/templates-baru/warna"))
APP_DIR = Path(os.path.expanduser("~/Downloads/motogp-speaker-tests/templates-baru/warna-app"))
APP_DIR.mkdir(parents=True, exist_ok=True)


def to_data_uri(path: Path) -> str:
    import base64
    b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
    ext = path.suffix.lower().lstrip(".")
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"
    return f"data:{mime};base64,{b64}"


def mean_abs_pixel_diff(path_a: Path, path_b: Path) -> float:
    im_a = Image.open(path_a).convert("RGBA")
    im_b = Image.open(path_b).convert("RGBA")
    arr_a = np.array(im_a, dtype=np.float32)
    arr_b = np.array(im_b, dtype=np.float32)
    diff = np.abs(arr_a - arr_b)
    return float(np.mean(diff))


def main():
    port = 3046
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
        news_tpl = next(t for t in templates if t["name"] == "GP News — Breaking")
        quote_tpl = next(t for t in templates if t["name"] == "GP Quote")

        news_img_uri = to_data_uri(NEWS_IMG_PATH)
        quote_img_uri = to_data_uri(QUOTE_IMG_PATH)

        targets = [
            ("kuning", "#FFC400"),
            ("biru", "#2F80ED"),
        ]

        for color_name, hex_code in targets:
            # 1. News
            news_payload = {
                "template_json": news_tpl["template_json"],
                "width": 1080,
                "height": 1350,
                "title": "Bagnaia crashes out in Indonesia: **No feeling, no grip**",
                "label": "BREAKING",
                "image_src": news_img_uri,
                "scale": 1,
                "accent": hex_code,
            }
            news_out = APP_DIR / f"news_{color_name}.png"
            resp = httpx.post(f"{renderer_url}/render", json=news_payload, timeout=60.0)
            resp.raise_for_status()
            news_out.write_bytes(resp.content)

            # Compare against approved preview
            approved_news = PREVIEW_DIR / f"news_{color_name}.png"
            news_diff = mean_abs_pixel_diff(news_out, approved_news)
            print(f"news_{color_name}: mean absolute pixel diff = {news_diff:.4f} (pass < 4: {news_diff < 4})")
            assert news_diff < 4, f"news_{color_name} diff too high: {news_diff}"

            # 2. Quote
            quote_payload = {
                "template_json": quote_tpl["template_json"],
                "width": 1080,
                "height": 1350,
                "title": "I'm sorry if Raúl is angry, but **he was the one** who touched me. I couldn't disappear.",
                "caption": "Marco **Bezzecchi** on the Raúl clash",
                "image_src": quote_img_uri,
                "scale": 1,
                "accent": hex_code,
            }
            quote_out = APP_DIR / f"quote_{color_name}.png"
            resp = httpx.post(f"{renderer_url}/render", json=quote_payload, timeout=60.0)
            resp.raise_for_status()
            quote_out.write_bytes(resp.content)

            # Compare against approved preview
            approved_quote = PREVIEW_DIR / f"quote_{color_name}.png"
            quote_diff = mean_abs_pixel_diff(quote_out, approved_quote)
            print(f"quote_{color_name}: mean absolute pixel diff = {quote_diff:.4f} (pass < 4: {quote_diff < 4})")
            assert quote_diff < 4, f"quote_{color_name} diff too high: {quote_diff}"

        print("ALL PROOF RENDERS MATCH APPROVED PREVIEWS (mean diff < 4)")

    finally:
        server_proc.terminate()
        server_proc.wait()


if __name__ == "__main__":
    main()
