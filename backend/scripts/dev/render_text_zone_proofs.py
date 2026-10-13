"""Render proof renders with REAL production data for template-zone-fix.

Inputs:
- .notes/samples/samples.json (18 real posts with photo, published PNG, etc.)
- .notes/prod_old_templates.json and .notes/prod_gp_templates.json
Outputs:
- ~/Downloads/template-fix-20261013/<job>_published.png
- ~/Downloads/template-fix-20261013/<job>_new.png
- ~/Downloads/template-fix-20261013/<job>_gp.png (for news/quote)
- ~/Downloads/template-fix-20261013/SEMUA.jpg (contact sheet)
- Pixel check diff (PASS if top_y >= 841)
"""

import base64
import copy
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# Add backend to sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parents[1]
ROOT_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.template_text_zone import apply_text_zone
from app.services.design_images import (
    _safe_face_cy_ceiling,
    fix_unsafe_single_photo_face,
    focus_points_for,
    prepare_design_images,
)
from app.tasks.design_renderer import clean_title_for_render, clear_template_text_objects

NOTES_DIR = ROOT_DIR / ".notes"
SAMPLES_FILE = NOTES_DIR / "samples" / "samples.json"
PROD_OLD_FILE = NOTES_DIR / "prod_old_templates.json"
PROD_GP_FILE = NOTES_DIR / "prod_gp_templates.json"
OUT_DIR = Path(os.path.expanduser("~/Downloads/template-fix-20261013"))


def find_free_port() -> int:
    s = socket.socket()
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def start_renderer(port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env["PORT"] = str(port)
    proc = subprocess.Popen(
        ["node", "server.js"],
        cwd=str(ROOT_DIR / "renderer"),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            r = httpx.get(f"{url}/health", timeout=1.0)
            if r.status_code == 200:
                return proc
        except Exception:
            pass
        time.sleep(0.5)
    proc.terminate()
    raise RuntimeError(f"Renderer failed to start on port {port}")


def measure_text_top_y(render_img: Image.Image, blank_img: Image.Image) -> float:
    """Find topmost differing row below y 120 (canvas coordinates).
    
    Count only rows where full render has bright text pixels (max channel > 200)
    that differ from empty render by > 60.
    """
    scale = render_img.height / 1350.0
    arr1 = np.array(render_img.convert("RGB"))
    arr2 = np.array(blank_img.convert("RGB"))
    bright = np.max(arr1, axis=-1) > 200
    abs_diff = np.max(np.abs(arr1.astype(np.int16) - arr2.astype(np.int16)), axis=-1) > 60
    diff = bright & abs_diff
    
    y_threshold = int(120 * scale)
    diff[:y_threshold, :] = False
    
    y_indices = np.where(np.any(diff, axis=1))[0]
    if len(y_indices) == 0:
        return 1350.0
    min_y_px = int(np.min(y_indices))
    return min_y_px / scale


def create_contact_sheet(samples: list, rows_data: list, out_path: Path):
    """Create SEMUA.jpg contact sheet: one row per sample, columns published | new | gp.
    
    Each card has a small label (job id, fanpage, template name).
    """
    card_w, card_h = 360, 450  # 1/3 scale of 1080x1350
    header_h = 36
    cell_h = card_h + header_h
    cell_w = card_w
    n_rows = len(samples)
    n_cols = 3  # published | new | gp
    
    sheet_w = cell_w * n_cols + 40
    sheet_h = cell_h * n_rows + 40
    sheet = Image.new("RGB", (sheet_w, sheet_h), (20, 20, 24))
    draw = ImageDraw.Draw(sheet)
    
    font = None
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 13)
    except Exception:
        font = ImageFont.load_default()
    
    for row_idx, data in enumerate(rows_data):
        y_offset = 20 + row_idx * cell_h
        
        # 3 columns: published, new, gp
        cols = [
            ("published", data.get("published_img"), data["pub_label"]),
            ("new", data.get("new_img"), data["new_label"]),
            ("gp", data.get("gp_img"), data.get("gp_label", "N/A (Discussion)")),
        ]
        
        for col_idx, (col_type, img, label_text) in enumerate(cols):
            x_offset = 20 + col_idx * cell_w
            
            # Draw label box
            draw.rectangle([x_offset, y_offset, x_offset + cell_w - 4, y_offset + header_h - 2], fill=(32, 34, 40))
            draw.text((x_offset + 6, y_offset + 8), label_text, fill=(240, 240, 240), font=font)
            
            # Draw card
            if img:
                resized = img.resize((cell_w - 4, card_h), Image.Resampling.LANCZOS)
                sheet.paste(resized, (x_offset, y_offset + header_h))
            else:
                draw.rectangle(
                    [x_offset, y_offset + header_h, x_offset + cell_w - 4, y_offset + header_h + card_h],
                    fill=(40, 40, 46)
                )
                draw.text((x_offset + 30, y_offset + header_h + 180), "No GP template for discussion", fill=(140, 140, 140), font=font)

    sheet.save(out_path, quality=90)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    
    with open(SAMPLES_FILE, "r", encoding="utf-8") as f:
        samples = json.load(f)
    with open(PROD_OLD_FILE, "r", encoding="utf-8") as f:
        old_templates = {t["id"]: t for t in json.load(f)}
    with open(PROD_GP_FILE, "r", encoding="utf-8") as f:
        gp_templates = {t["id"]: t for t in json.load(f)}

    port = find_free_port()
    print(f"Starting local renderer on port {port}...")
    proc = start_renderer(port)
    renderer_url = f"http://127.0.0.1:{port}"

    rows_data = []
    pixel_check_table = []

    try:
        total = len(samples)
        for idx, s in enumerate(samples, 1):
            job_id = s["job_id"]
            cat = s["category"]
            tid = s["template_id"]
            fanpage = s["fanpage"]
            old_tpl_meta = old_templates[tid]
            old_tpl_name = old_tpl_meta.get("name", f"Template {tid}")
            
            print(f"[{idx:02d}/{total:02d}] Processing Job {job_id} ({cat}, tpl {tid}: {old_tpl_name})...")
            
            # 1. Copy published PNG
            pub_src = NOTES_DIR / "samples" / s["published"]
            pub_dst = OUT_DIR / f"{job_id}_published.png"
            shutil.copyfile(pub_src, pub_dst)
            pub_img = Image.open(pub_dst).convert("RGB")
            
            # Prepare photo and subject title
            photo_path = NOTES_DIR / "samples" / s["photo"]
            photo_bytes = photo_path.read_bytes()
            main_datauri = f"data:image/jpeg;base64,{base64.b64encode(photo_bytes).decode('ascii')}"
            subject_title = " — ".join(p for p in (s.get("subtitle"), s.get("title")) if p) or s["title"]
            
            # 2. Render new template
            tpl_new_base = apply_text_zone(old_tpl_meta["template_json"], category=cat)
            tpl_new, image_srcs_new = prepare_design_images(
                None, tpl_new_base, 1080, subject_title, fanpage, main_datauri,
                main_path=str(photo_path), smart=False, expand=True
            )
            image_zooms_new = tpl_new.pop("_splitImageZooms", None)
            safe_cy_new = _safe_face_cy_ceiling(tpl_new, 1350)
            if image_srcs_new and image_srcs_new[0]:
                try:
                    main_bytes = base64.b64decode(image_srcs_new[0].split(",", 1)[1])
                    fixed = fix_unsafe_single_photo_face(main_bytes, 1080, 1350, safe_cy_new)
                    if fixed:
                        image_srcs_new[0] = fixed
                except Exception:
                    pass
            fps_new = focus_points_for(image_srcs_new, for_split=False, safe_cy_ceiling=safe_cy_new)
            
            title_clean = clean_title_for_render(s["title"])
            tpl_payload_new = clear_template_text_objects(tpl_new)
            
            if cat == "discussion":
                sub_new, cap_new, lbl_new = "", "", s["subtitle"] or "DISCUSSION"
            elif cat == "quote":
                sub_new, cap_new, lbl_new = s["subtitle"] or "", "", ""
            else:
                sub_new, cap_new, lbl_new = s["subtitle"] or "", "", s["label"] or ""
            
            payload_new = {
                "template_json": tpl_payload_new,
                "width": 1080,
                "height": 1350,
                "title": title_clean,
                "subtitle": sub_new,
                "caption": cap_new,
                "label": lbl_new,
                "watermark": s["watermark"] or "",
                "watermark_image": s["watermark_image"],
                "image_srcs": image_srcs_new,
                "focus_points": fps_new,
                "image_zooms": image_zooms_new,
                "scale": 2,
                "accent": s["accent"],
            }
            
            r_new = httpx.post(f"{renderer_url}/render", json=payload_new, timeout=120.0)
            r_new.raise_for_status()
            new_dst = OUT_DIR / f"{job_id}_new.png"
            new_dst.write_bytes(r_new.content)
            new_img = Image.open(new_dst).convert("RGB")
            
            # Pixel check for new render: render without text & scrim
            tpl_eval_new = copy.deepcopy(tpl_payload_new)
            tpl_eval_new["objects"] = [o for o in tpl_eval_new["objects"] if o.get("placeholderRole") != "scrim"]
            tpl_eval_blank = copy.deepcopy(tpl_eval_new)
            for o in tpl_eval_blank["objects"]:
                if o.get("placeholderRole") == "quoteIcon":
                    o["visible"] = False
                    if "text" in o:
                        o["text"] = ""
            payload_eval1 = dict(payload_new, template_json=tpl_eval_new)
            payload_eval2 = dict(payload_eval1, template_json=tpl_eval_blank, title="", subtitle="", caption="", label="")
            
            r_ev1 = httpx.post(f"{renderer_url}/render", json=payload_eval1, timeout=120.0)
            r_ev2 = httpx.post(f"{renderer_url}/render", json=payload_eval2, timeout=120.0)
            r_ev1.raise_for_status()
            r_ev2.raise_for_status()
            
            img_ev1 = Image.open(io.BytesIO(r_ev1.content)).convert("RGB")
            img_ev2 = Image.open(io.BytesIO(r_ev2.content)).convert("RGB")
            top_y_new = measure_text_top_y(img_ev1, img_ev2)
            pass_new = "PASS" if top_y_new >= 841 else "FAIL"
            pixel_check_table.append({
                "job": job_id,
                "template": f"{old_tpl_name} ({tid})",
                "type": "new",
                "top_y": top_y_new,
                "status": pass_new,
            })
            
            # GP render if news or quote
            gp_img = None
            gp_label_str = "N/A"
            if cat in ("news", "quote"):
                gp_tid = 21 if cat == "news" else 22
                gp_tpl_meta = gp_templates[gp_tid]
                gp_tpl_name = gp_tpl_meta.get("name", f"Template {gp_tid}")
                gp_label_str = f"#{job_id} · {fanpage} · {gp_tpl_name}"
                
                tpl_gp_base = apply_text_zone(gp_tpl_meta["template_json"], category=cat)
                tpl_gp, image_srcs_gp = prepare_design_images(
                    None, tpl_gp_base, 1080, subject_title, fanpage, main_datauri,
                    main_path=str(photo_path), smart=False, expand=True
                )
                image_zooms_gp = tpl_gp.pop("_splitImageZooms", None)
                safe_cy_gp = _safe_face_cy_ceiling(tpl_gp, 1350)
                if image_srcs_gp and image_srcs_gp[0]:
                    try:
                        main_bytes = base64.b64decode(image_srcs_gp[0].split(",", 1)[1])
                        fixed = fix_unsafe_single_photo_face(main_bytes, 1080, 1350, safe_cy_gp)
                        if fixed:
                            image_srcs_gp[0] = fixed
                    except Exception:
                        pass
                fps_gp = focus_points_for(image_srcs_gp, for_split=False, safe_cy_ceiling=safe_cy_gp)
                
                tpl_payload_gp = clear_template_text_objects(tpl_gp)
                if cat == "news":
                    sub_gp, cap_gp, lbl_gp = s["subtitle"] or "", "", s["label"] or ""
                else:
                    sub_gp, cap_gp, lbl_gp = "", s["subtitle"] or "", ""
                
                payload_gp = {
                    "template_json": tpl_payload_gp,
                    "width": 1080,
                    "height": 1350,
                    "title": title_clean,
                    "subtitle": sub_gp,
                    "caption": cap_gp,
                    "label": lbl_gp,
                    "watermark": s["watermark"] or "",
                    "watermark_image": s["watermark_image"],
                    "image_srcs": image_srcs_gp,
                    "focus_points": fps_gp,
                    "image_zooms": image_zooms_gp,
                    "scale": 2,
                    "accent": s["accent"],
                }
                
                r_gp = httpx.post(f"{renderer_url}/render", json=payload_gp, timeout=120.0)
                r_gp.raise_for_status()
                gp_dst = OUT_DIR / f"{job_id}_gp.png"
                gp_dst.write_bytes(r_gp.content)
                gp_img = Image.open(gp_dst).convert("RGB")
                
                # Pixel check for GP render
                tpl_eval_gp = copy.deepcopy(tpl_payload_gp)
                tpl_eval_gp["objects"] = [o for o in tpl_eval_gp["objects"] if o.get("placeholderRole") != "scrim"]
                tpl_eval_gp_blank = copy.deepcopy(tpl_eval_gp)
                for o in tpl_eval_gp_blank["objects"]:
                    if o.get("placeholderRole") == "quoteIcon":
                        o["visible"] = False
                        if "text" in o:
                            o["text"] = ""
                payload_eval_gp1 = dict(payload_gp, template_json=tpl_eval_gp)
                payload_eval_gp2 = dict(payload_eval_gp1, template_json=tpl_eval_gp_blank, title="", subtitle="", caption="", label="")
                
                r_gp_ev1 = httpx.post(f"{renderer_url}/render", json=payload_eval_gp1, timeout=120.0)
                r_gp_ev2 = httpx.post(f"{renderer_url}/render", json=payload_eval_gp2, timeout=120.0)
                r_gp_ev1.raise_for_status()
                r_gp_ev2.raise_for_status()
                
                img_gp_ev1 = Image.open(io.BytesIO(r_gp_ev1.content)).convert("RGB")
                img_gp_ev2 = Image.open(io.BytesIO(r_gp_ev2.content)).convert("RGB")
                top_y_gp = measure_text_top_y(img_gp_ev1, img_gp_ev2)
                pass_gp = "PASS" if top_y_gp >= 841 else "FAIL"
                pixel_check_table.append({
                    "job": job_id,
                    "template": f"{gp_tpl_name} ({gp_tid})",
                    "type": "gp",
                    "top_y": top_y_gp,
                    "status": pass_gp,
                })

            rows_data.append({
                "job_id": job_id,
                "published_img": pub_img,
                "new_img": new_img,
                "gp_img": gp_img,
                "pub_label": f"#{job_id} · {fanpage} · Published",
                "new_label": f"#{job_id} · {fanpage} · {old_tpl_name}",
                "gp_label": gp_label_str,
            })

        # Save SEMUA.jpg
        semua_path = OUT_DIR / "SEMUA.jpg"
        print(f"Generating contact sheet: {semua_path}...")
        create_contact_sheet(samples, rows_data, semua_path)
        print("Contact sheet created successfully!")

        # Print pixel check table
        print("\n" + "=" * 65)
        print("PIXEL CHECK RESULTS (diff topmost row below y 120)")
        print("=" * 65)
        print(f"{'Job':<8} | {'Type':<5} | {'Template':<35} | {'Top Y':<7} | {'Status':<6}")
        print("-" * 65)
        for item in pixel_check_table:
            print(f"{item['job']:<8} | {item['type']:<5} | {item['template'][:35]:<35} | {item['top_y']:<7.1f} | {item['status']:<6}")
        print("=" * 65)

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    main()
