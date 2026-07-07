# PROMPT v2: IG-FB Reposter + News-to-Image Content Generator

> **Konteks**: Ini pengembangan dari app "ig-fb-reposter" yang sudah ada. Menambahkan **Fitur 2: Auto Content Generation dari Berita → Desain Gambar → Publish**. Fitur 1 (IG repost) tetap ada. Kedua fitur hidup di menu Fanpage yang sama dan sama-sama bermuara ke Repliz publisher.

---

## 🧭 BIG PICTURE — Dua Mode Konten per Fanpage

Tiap Fanpage sekarang bisa punya DUA sumber konten (bisa aktif salah satu atau keduanya):

```
FANPAGE
├── MODE 1: IG Repost (sudah ada)
│     IG sources → crawl image → AI caption → Repliz publish
│
└── MODE 2: News-to-Image (BARU)
      News sites → scrape (title+content) → AI copywriter
        → pilih gambar dari Gallery → apply Template desain
        → [review / auto] → export PNG → Repliz publish
```

Keduanya menghasilkan `publish_jobs` yang diproses oleh Repliz Publisher yang sama.

---

## ⚙️ TECH STACK TAMBAHAN (untuk Fitur 2)

| Fungsi | Tech | Catatan |
|---|---|---|
| News scraper | **BeautifulSoup4 + httpx** (atau **Playwright** untuk situs JS-heavy) | Per-site CSS selector config |
| Image gallery downloader | **google-images-download** (primary, sudah dites jalan) + **icrawler** (fallback) | google-images-download dipakai karena sudah terbukti jalan di environment kamu. icrawler disiapkan sebagai fallback kalau repo resmi (archived) break di server baru / setelah Google update. Keduanya harus support min-size 500x500 & dedup |
| Image editor (review) | **canva-clone** (Davronov-Alimardon) | Editor interaktif berbasis canvas (Fabric.js). Untuk mode REVIEW |
| Template auto-render (auto) | **Node canvas / Puppeteer headless + Fabric.js** | Render template JSON jadi PNG tanpa buka browser. Untuk mode AUTO |
| Image processing | **Pillow** | Resize, validasi resolusi, watermark opsional |

---

## 🚨 KEPUTUSAN LIBRARY (WAJIB DICATAT)

1. **Image downloader: `google-images-download` primary + `icrawler` fallback.**
   - google-images-download sudah dites jalan di environment kamu → jadikan primary.
   - CATATAN: repo resmi google-images-download di-archive (Des 2025). Kalau nanti break di server baru atau setelah Google update, sistem harus auto-fallback ke icrawler. Karena itu bungkus downloader dalam 1 abstraction layer (`image_downloader.py`) dengan 2 backend yang bisa diswitch.
   - Kedua backend WAJIB: filter resolusi minimum 500x500 + dedup by source URL.

2. **canva-clone punya 2 jalur pemakaian**:
   - **Review mode** → pakai UI editor canva-clone langsung (human buka, edit, export).
   - **Auto mode** → TIDAK bisa pakai editor interaktif headless. Template harus disimpan sebagai **JSON (Fabric.js canvas serialize)**, lalu di-render server-side (Puppeteer load halaman Fabric.js minimal → inject title + gambar → `canvas.toDataURL()` → simpan PNG).
   - ➜ Konsekuensi: template disimpan sebagai **JSON dengan placeholder** (`{{title}}`, `{{image_slot_1}}`), bukan sebagai PNG statis.

---

## ⚠️ CATATAN RISIKO (untuk keputusan sadar, bukan blocker)

- **Gambar hasil download by-keyword itu berhak cipta.** Google/Bing Images mengembalikan foto milik orang/agensi. Konten MotoGP/F1 dijaga ketat (Dorna, FOM). Mitigasi: pakai filter `license='commercial,modify'` di icrawler, ATAU beralih ke sumber legal (Unsplash/Pexels API, atau foto berlisensi). Tetap ada risiko sisa.
- **Scraping + republish berita**: fakta tidak berhak cipta, tapi ekspresi tulisan iya. AI rewrite yang substansial + atribusi sumber mengurangi risiko, tidak menghilangkan. Hormati `robots.txt` tiap situs.
- Terapkan rate limit sopan saat scraping (delay antar request, User-Agent wajar).

---

## 🗄️ DATABASE SCHEMA TAMBAHAN (Fitur 2)

### Tabel `news_sources` (config scraper per situs)
- `id`, `name`, `category_url`, `is_active`, `scrape_interval_minutes` (default 60)
- `render_mode` enum: `static` (BeautifulSoup) / `js` (Playwright)
- CSS selector config: `article_list_selector`, `article_link_attribute`, `title_selector`, `content_selector`, `image_selector` (opsional), `date_selector` (opsional)
- `last_scraped_at`

### Tabel `scraped_articles` (dengan flag dedup)
- `id`, `news_source_id` (FK)
- `article_url` (**UNIQUE** — flag dedup)
- `scraped_title`, `scraped_content`, `scraped_image_url` (opsional)
- `is_processed` (bool, default false)
- `status` enum: `scraped` / `copywritten` / `designed` / `published` / `skipped`
- `scraped_at`

### Tabel `gallery_images` (dengan flag dedup)
- `id`, `keyword`, `source_image_url` (**UNIQUE** — dedup), `local_path`, `public_url`
- `width`, `height` (harus ≥ 500×500, validasi via Pillow saat download)
- `source_engine` (`bing`/`google`), `license_info`
- `is_used` (bool), `downloaded_at`

### Tabel `gallery_keywords` (settings download)
- `id`, `keyword`, `is_active`, `max_images` (default 50), `min_width`/`min_height` (default 500)
- `source_engine` (default `bing`), `license_filter` (default `commercial,modify`)
- `last_downloaded_at`

### Tabel `design_templates` (template per fanpage)
- `id`, `fanpage_id` (FK), `name`
- `template_json` (Fabric.js canvas serialize dengan placeholder)
- `placeholder_config` (JSON): `{"title_layer_id": "text_headline", "image_slot_id": "img_main", "max_title_chars": 80}`
- `canvas_width`, `canvas_height` (e.g. 1080x1080), `is_default`

### Update `target_fanpages`
- `mode1_ig_repost_enabled`, `mode2_news_content_enabled`
- `mode2_publish_mode` enum: `auto` / `manual_review` (default `manual_review`)
- `mode2_gallery_keywords[]`, `mode2_default_template_id` (FK)

### Update `publish_jobs`
- `content_type` enum: `ig_repost` / `news_content`
- `source_article_id` (FK, nullable), `design_image_path`, `design_image_url`, `design_template_id` (FK)

---

## 🔄 WORKFLOW FITUR 2

**A. News Scraper** (beat, per interval): fetch category → extract links → dedup → extract title/content/image → insert `scraped_articles` → push ke Copywriter queue. Delay 5-15s antar artikel, hormati robots.txt.

**B. AI Copywriter** (per fanpage mode2 aktif): rewrite scraped title+content → judul baru (untuk desain) + caption baru (untuk FB post), pakai caption criteria fanpage → create `publish_jobs` (content_type=`news_content`, status `pending_design`) → artikel `copywritten`.

**C. Image Selection**: match `gallery_images` by keyword (dari `mode2_gallery_keywords` atau entity extraction dari judul) → prioritas `is_used=false` ≥500×500 → fallback hero image artikel → else flag butuh gambar manual.

**D. Design Rendering**:
- REVIEW mode: antrian review → admin buka canva-clone editor (template preloaded, title + gambar terisi) → edit bebas → Export PNG → `pending_publish`.
- AUTO mode: headless render (Puppeteer + Fabric.js) → inject title + gambar → `canvas.toDataURL()` → PNG ke `/var/www/media/designs/{job_uuid}.png` → `pending_publish`.

**E. Repliz Publisher**: sama seperti Fitur 1 — `type: "image"`, `medias[].url` = `design_image_url`.

**F. Gallery Auto-Downloader** (beat, harian / on-demand): per keyword aktif → download → dedup by URL → validasi ≥500×500 Pillow → simpan `/var/www/media/gallery/{keyword_slug}/{uuid}.jpg` → insert `gallery_images`.

---

## 🎨 DASHBOARD TAMBAHAN

- **Edit Fanpage**: tab Mode 1 (existing) + tab Mode 2 (toggle, news sources, gallery keywords, default template, publish mode, caption criteria terpisah)
- **News Sources** (✅ selesai): CRUD + selector config + Selector Tester
- **Gallery** (baru): grid images + filter keyword, settings keywords, Download Now
- **Template Designer** (baru): canva-clone per fanpage, tandai placeholder layers
- **Preview & Antrian** (extend): filter content_type, "Open in Designer" untuk review

---

## 📦 PHASES

- **Phase 2A — News Scraper** ✅ (selesai & terverifikasi 2026-07-06: motosan.es live scrape OK)
- **Phase 2B — Gallery Downloader**: tabel + downloader abstraction + settings UI. Acceptance: keyword "marc marquez" → ≥10 gambar unik ≥500×500, no duplikat
- **Phase 2C — AI Copywriter News**: extend AI worker (title + caption baru), reuse Gemini+Groq failover
- **Phase 2D — Template & Design Render**: `design_templates`, canva-clone (review), Puppeteer+Fabric.js (auto). Acceptance: 1 artikel → title auto-fill → export PNG (review & auto)
- **Phase 2E — Wiring ke Publisher**: extend `publish_jobs` content_type, news → Repliz. Acceptance: 1 berita → desain → publish ke 1 fanpage real

---

## 🚫 CONSTRAINTS

- google-images-download = primary (sudah dites), WAJIB fallback icrawler (repo archived)
- ❌ JANGAN scrape tanpa robots.txt check & delay sopan
- ❌ JANGAN download gambar < 500×500 (validasi Pillow)
- ❌ JANGAN download ulang / scrape ulang URL yang sudah ada (dedup wajib)
- ❌ JANGAN pakai editor interaktif canva-clone untuk auto mode (harus headless render)
- ⚠️ Template WAJIB tersimpan sebagai JSON (Fabric.js), bukan gambar statis

---

## ✅ DECISIONS LOG (final)

| # | Topik | Keputusan |
|---|---|---|
| 1 | Sumber gambar gallery | Kombinasi: downloader (foto entitas evergreen) + fallback hero image artikel. Filter lisensi `commercial,modify`. |
| 2 | Auto-render engine | Puppeteer + Fabric.js — hasil auto-mode identik dengan canva-clone editor. |
| 3 | Caption criteria Mode 2 | Set terpisah dari Mode 1 |
| 4 | Pemilihan gambar auto | Entity extraction dari judul → match gallery by keyword → fallback hero image → flag manual |
| 5 | News source awal | motosan.es + gpone.com, arsitektur support N via UI |

### ⚠️ Memory Puppeteer di VPS 4 GB

- Render worker terpisah concurrency = 1; spin up Chrome per job lalu tutup
- Flags: `--no-sandbox --disable-gpu --disable-dev-shm-usage --single-process`
- Sering OOM → upgrade VPS 8 GB. Alternatif: Pillow renderer untuk template sederhana

## 📚 REFERENSI

- icrawler: https://pypi.org/project/icrawler/
- canva-clone: https://github.com/Davronov-Alimardon/canva-clone
- News sources: https://www.motosan.es/motogp , https://www.gpone.com/en/category/motogp
