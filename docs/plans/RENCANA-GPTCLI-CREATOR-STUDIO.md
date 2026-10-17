# RENCANA KERJA — ChatGPT (gpt-cli) sebagai engine utama AI image editing di creator-studio

Dokumen tunggal untuk agent yang mengerjakan. Baca **seluruhnya** sebelum mulai.
Disusun dari diskusi dengan pemilik (15–17 Okt 2026). Semua keputusan di bagian 2 sudah final,
kecuali yang ditandai **[MENUNGGU PEMILIK]**.

---

## 0. Cara memakai dokumen ini

Pekerjaan dibagi 4 fase. Kerjakan berurutan dan patuhi titik berhenti.

| Fase | Isi | Memakai kuota ChatGPT? | Titik berhenti |
|---|---|---|---|
| **A** | Pemeriksaan & konfigurasi server (gpt serve, 9router, uji 12–14) | Hanya A4 | Kirim laporan A ke pemilik sebelum lanjut ke C |
| **B** | Implementasi kode di repo `creator-studio` | Tidak | Semua tes hijau, commit + push ke branch kerja |
| **C** | Deploy & aktivasi untuk fanpage uji **GP Racing** | Ya (produksi) | Hanya oleh/izin pemilik |
| **D** | Evaluasi beberapa hari, lalu perluasan | Ya | — |

Fase A dan B boleh berjalan bersamaan. Fase C **tidak boleh** dimulai sebelum A lolos dan B ter-deploy.

**Aturan umum untuk agent:**
- Repo: `rickyanwar/creator-studio`, basis `main` terbaru (minimal `f2a761d`).
  Branch kerja: `claude/beautiful-gauss-tgql3b` (ikuti instruksi pemilik kalau berbeda).
- Jangan menyalakan saklar gpt-cli (`gptcli_enabled`) di produksi. Itu keputusan pemilik di fase C.
- Jangan membuat gambar ChatGPT/Codex di luar uji A4.
- Jangan menaruh token/API key di log, commit, atau chat. Token gpt serve ada di `/etc/gpt-serve.env`.
- Ikuti gaya kode sekitar (komentar bertanggal + alasan, seperti yang sudah ada di repo).
- Aturan pemilik 2026-10-17 berlaku: **akurasi di atas ketersediaan (fail-closed)**, dan model vision
  9router **hanya `FlashFast`** (lewat `_vision_chat` yang sudah ada, jangan menambah model lain).

---

## 1. Konteks

- **creator-studio** (`/opt/studio`, Docker): app konten sosial media (IG/FB/News) untuk fanpage
  olahraga. Sekarang AI image editing memakai 9router `cx/gpt-image-2.5` (= akun **Codex** pemilik,
  kuota reset tiap 5 jam).
- **gpt-cli / gpt serve** (`/root/chatgpt-cli`, systemd `gpt-serve.service`, `http://172.17.0.1:8099`):
  mengendalikan ChatGPT web (akun **ChatGPT Plus** pribadi pemilik, profil `utama`) lewat Chrome + proxy
  HP, dan menyediakan API seperti OpenAI Images. Sudah jadi dan teruji.
- Tujuan: **ChatGPT jadi engine utama** AI image editing; Codex jadi pembagi beban + cadangan; template
  biasa jadi jalan terakhir. Akun pribadi harus awet: ada jam tidur, jeda, dan rem.

---

## 2. Keputusan pemilik

| Topik | Keputusan |
|---|---|
| Kapan dipakai | **Hanya** kalau toggle AI editing untuk jenis konten itu menyala di fanpage. Toggle mati → post biasa (template + foto), tidak ada panggilan AI sama sekali |
| Jenis konten tahap 1 | **Instagram** (IG recreate, toggle `visual_engine`), **News** (speaker design, `news_visual_engine`), **Facebook** (FB recreate, `fb_visual_engine`) |
| Tahap 2 | **Pinterest** dan **Discussion**: belum punya AI editing sama sekali (perlu toggle + desain/prompt baru). **[MENUNGGU PEMILIK]** konfirmasi dikerjakan setelah tahap 1 lancar. Di luar lingkup dokumen ini |
| Engine | ChatGPT (gpt serve) utama → Codex (9router) → template |
| Siang 05:30–24:00 WIB | ChatGPT **≥ 65%** job; Codex **≤ 35%** (termasuk limpahan saat ramai). Saat ChatGPT tidak bisa dipakai → Codex sebagai cadangan |
| Malam 00:00–05:30 WIB | ChatGPT tidur. **Hanya Codex**, pelan-pelan (jeda 4 ± 1 menit). **Semua** jenis post tetap jalan (ada fanpage berjadwal/target negara zona waktu lain) |
| Rem ChatGPT | Di gpt serve: 140/hari, 30 per 3 jam, jeda 25 ± 12 detik antar job (env) |
| Rem Codex | **Berbasis persentase kuota** jendela 5 jam (dibaca dari 9router kalau tersedia). Porsi siang hanya selama pemakaian < 60%; cadangan sampai 85%; malam berhenti di 70%; ≥ 95% atau 429 → istirahat. Pengaman keras: 170/hari |
| Perkiraan volume | Sampai **300+ gambar/hari** total (ChatGPT ±140, Codex ±165) |
| Ukuran ke gpt serve | `1080x1350` (4:5), thread `studio` |
| QA hasil ChatGPT | **Tanpa QA penuh**. Hanya: (1) **cek wajah ganda** (SFace lokal + Gemini `FlashFast` selalu) di Instagram, News, Facebook; (2) **cek ejaan teks radio** untuk layout team radio. Gagal / error → Codex (fail-closed) |
| QA hasil Codex | Tetap seperti sekarang (`check_generated`, `identity_score`) |
| Revisi ChatGPT | Tidak ada loop revisi (gpt serve sendiri mengulang sekali kalau dijawab teks) |
| Alert Telegram (lewat Hermes) | Penolakan ChatGPT (dengan teks lengkap + link chat), `cloudflare_challenge`, gagal total ≥ 3 beruntun. **Tanpa ringkasan harian**. Server/proxy/logout gpt serve sudah dipantau skrip Hermes milik gpt-cli (`gpt-health`/`gpt-heal`) |
| Fanpage uji | **GP Racing** (MotoGP). Team radio tidak muncul di sini (khusus F1) |
| Worker | Job AI untuk fanpage gpt-cli dikirim ke `worker-visual` (antrian `visual`, concurrency 1) |

---

## 3. Kontrak gpt serve (ringkas — sumber: `DOKUMENTASI_LENGKAP_1.md` bagian 3–4)

**Request** `POST http://172.17.0.1:8099/v1/images/generations`
Header: `Authorization: Bearer <GPT_SERVE_TOKEN>`, `Content-Type: application/json`, `X-Request-Id: <id stabil>`.
```json
{"prompt": "...", "images": ["data:image/jpeg;base64,...", "..."], "size": "1080x1350", "thread": "studio"}
```
- `images` = data URL, urutan = Image 1, Image 2, … (prompt app menyebut gambar dengan nomor ini).
- gpt serve menambah header di awal prompt (target rasio, pemetaan "Image N = attachment ke-N",
  "abaikan gambar pesan sebelumnya"). Prompt app dikirim apa adanya.
- Timeout job di server: 0 gambar 120 s, 1–2 gambar 300 s, 3+ gambar 600 s. Antrian FIFO, menunggu
  (bukan ditolak) sampai 20 job.
- `X-Request-Id` sama → hasil diputar ulang (cache 6 jam), tidak memakai turn kedua.

**Sukses 200:** `data[0].b64_json` (PNG sesuai `size`, metadata dibuang) + blok `gptcli`
(`request_id`, `profile`, `egress`, `duration_s`, `chat_url`, `attempts`, `queue_wait_s`, `width`,
`height`, `orig_width`, `orig_height`).

**Error:** `{"error": {"type", "message", "request_id", ...}}`. App membaca **`error.type`**:

| HTTP | `error.type` | Arti | `Retry-After` |
|---|---|---|---|
| 400 | `bad_request` | request salah | — |
| 401 | `unauthorized` | token salah | — |
| 403 | `cloudflare_challenge` | dicegat Cloudflare | — |
| 404 | `not_found` | path/method salah | — |
| 429 | `quota` | limit akun Plus, cooldown 3 jam | ya |
| 429 | `soft_cap` | rem harian/3 jam | ya |
| 502 | `refused` | ChatGPT menolak; `message` = teks lengkap, + `chat_url`, `prompt_head` | — |
| 502 | `no_image` | dijawab teks 2× | — |
| 503 | `sleeping` | jam tidur | ya |
| 503 | `unavailable` | proxy HP mati | — |
| 503 | `logged_out` | sesi habis | — |
| 503 | `busy` | antrian > 20 | — |
| 504 | `timeout` | lewat timeout job | — |

**Health** `GET /health` (tanpa auth): `ok`, `proxy_alive`, `sleeping`, `sleep_window`, `hb_age_s`,
`queue_len`, `profiles.utama.{daemon, running, logged_in, quota_until, today_count, soft_cap_daily, chat_url, turns_used, busy_s}`.
App **tidak** perlu memanggil `/health` per job.

---

## 4. FASE A — Pemeriksaan & konfigurasi server

Dikerjakan oleh agent yang berjalan di server. Hasil seluruh fase A dikirim ke pemilik (bagian A5).

### A1. Verifikasi gpt serve (tanpa memakai kuota)

```bash
curl -s http://172.17.0.1:8099/health; echo
TOKEN=$(grep -oP '(?<=^GPT_SERVE_TOKEN=).*' /etc/gpt-serve.env)
curl -s -X POST http://172.17.0.1:8099/v1/images/generations \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"prompt":"tes","images":["bukan-data-url"]}'; echo
cd /root/chatgpt-cli && git log --oneline -3
```
Lolos kalau: `/health` sesuai bagian 3; panggilan kedua → `400 bad_request` dengan pesan
"must be a data URL". Dari dalam container app juga harus terjangkau:
```bash
docker exec "$(docker ps --format '{{.Names}}' | grep -m1 worker-visual)" \
  python -c "import httpx;print(httpx.get('http://172.17.0.1:8099/health',timeout=5).status_code)"
```

### A2. Konfigurasi gpt serve

Di `/etc/gpt-serve.env` (mode 600):
```
GPT_SERVE_SLEEP=00:00-05:30
GPT_SERVE_DAILY_CAP=140
```
(Biarkan yang lain: `WINDOW_CAP=30`, `WINDOW_H=3.0`, `MIN_GAP=25`, `JITTER=12`, `MAX_TURNS=15`,
`NEW_AFTER_SLEEP=1`, `IDLE_STOP=600`.) Lalu `systemctl restart gpt-serve` dan cek `/health`:
`sleep_window` = `00:00-05:30`, `profiles.utama.soft_cap_daily` = 140.

### A3. Angka pemakaian kuota Codex di 9router

App akan membatasi Codex berdasarkan **persentase** kuota jendela 5 jam (dan mingguan kalau ada).
Cari tanpa membuat gambar:
1. Apakah dashboard 9router menampilkan pemakaian/sisa kuota Codex (mis. "5h: 42% used",
   "weekly: 18%", waktu reset)?
2. Endpoint API apa yang dipakai dashboard itu (source/dokumentasi 9router, atau tab Network browser)?
3. `curl` endpoint itu dengan API key 9router yang dipakai app (`Settings.nine_router_api_key` /
   env `NINE_ROUTER_API_KEY`) → kirim contoh JSON asli (samarkan key).
4. Laporkan: per akun Codex atau gabungan; jumlah akun Codex di 9router; frekuensi pembaruan; ada
   batas mingguan atau tidak; format waktu reset.
5. Kalau tidak ada endpoint: apakah respons `/v1/images/generations` 9router membawa header pemakaian
   (`x-codex-*`, `x-ratelimit-*`)? Ambil dari log/panggilan yang **sudah** dijadwalkan app, jangan
   membuat panggilan baru.

### A4. Uji live 12–14 (memakai turn ChatGPT — jalankan 06:00–23:00 WIB)

Uji 11 (team radio) dilewati; pengiriman prompt panjang sudah terbukti offline, dan team radio tidak
muncul di GP Racing.

**Siapkan bahan dari container app:**
```bash
mkdir -p /root/uji-gpt && cd /root/uji-gpt
API=$(docker ps --format '{{.Names}}' | grep -m1 -- '-api-')
docker exec -i "$API" python - <<'EOF'
from app.database import SessionLocal
from app.models.gallery import GalleryImage
db = SessionLocal()
for kw in ["Marquez", "Acosta", "Bagnaia", "Martin"]:
    for r in (db.query(GalleryImage).filter(GalleryImage.keyword.ilike(f"%{kw}%")).limit(5)):
        print(kw, "|", r.keyword, "|", r.local_path)
EOF
# salin foto wajah close-up yang jelas:
docker cp "$API":<local_path> ./marquez.jpg
docker cp "$API":<local_path> ./acosta.jpg
docker cp "$API":<local_path> ./bagnaia.jpg
```
**Prompt asli dari kode app** (jangan menulis prompt sendiri):
```bash
for who in "Marc Marquez, Ducati Lenovo MotoGP rider|#C8102E" "Pedro Acosta, Red Bull KTM MotoGP rider|#FF6600" "Francesco Bagnaia, Ducati Lenovo MotoGP rider|#C8102E"; do
  name="${who%%|*}"; accent="${who##*|}"; f="prompt_$(echo "$name" | cut -d' ' -f2 | tr A-Z a-z).txt"
  docker exec -i "$API" python - "$name" "$accent" <<'EOF' > "/root/uji-gpt/$f"
import sys
from app.services.visual_engine import build_solo_prompt
print(build_solo_prompt(hero_desc=sys.argv[1], purpose="news", accent=sys.argv[2], sport="MotoGP"))
EOF
done
```
Kirim lewat gpt serve dengan `size: "1080x1350"`, `thread: "studio"`, `X-Request-Id` unik per job,
gambar sebagai data URL (`base64 -w0`).

- **Uji 12 — foto asli:** 3 job (Márquez, Acosta, Bagnaia) → semua 200.
- **Uji 13 — crop:** laporkan `orig_width`/`orig_height` tiap job. Cek visual: kepala utuh, yang
  terpotong bagian bawah.
- **Uji 14 — tidak tercampur:** di thread `studio`, bergantian Márquez (Ducati, merah) ↔ Acosta
  (KTM, oranye) **≥ 10 turn**. Lolos kalau tidak ada hasil dengan wajah atau warna tim yang tertukar.
  Kalau tercampur: catat turn ke berapa, lalu ulangi dengan `GPT_SERVE_MAX_TURNS` lebih kecil (mis. 5)
  dan laporkan keduanya.

Simpan semua PNG di `/root/uji-gpt/hasil/` beserta `chat_url`, `attempts`, `duration_s`.

### A5. Laporan fase A ke pemilik

1. Output A1 (health + 400 + versi gpt-cli + akses dari container).
2. Konfirmasi A2 (`/health` setelah restart).
3. Temuan A3: endpoint + JSON contoh, atau header, atau "tidak ada".
4. Hasil A4: tabel per job (pembalap, turn, status, attempts, durasi, orig size, tercampur ya/tidak) +
   lokasi PNG. Kesimpulan uji 14 dan nilai `MAX_TURNS` yang disarankan.

---

## 5. FASE B — Implementasi kode

Semua path relatif ke `backend/` kecuali disebut lain. Pertahankan perilaku lama saat saklar mati:
**dengan `gptcli_enabled = false`, tidak boleh ada perubahan perilaku sama sekali** (kecuali B1 yang
menampilkan setting baru).

### B0. Fakta kode yang perlu diketahui

- Titik masuk tunggal: `app/services/visual_engine.py::generate(db, req, *, timeout=600, fanpage_id=None) -> bytes`.
  Saat ini: cek batas harian (`Settings.visual_engine_daily_max`, menghitung semua `AICopyEvent`
  `context='image_gen'` sejak 00:00 WIB), kirim ke 9router `/images/generations`, 2 percobaan, catat
  satu `AICopyEvent`.
- Pemanggil:
  - `app/services/redesign_pipeline.py::redesign_card` (Instagram, `timeout=300`) → lalu
    `check_generated` (QA vision penuh) dan untuk team radio OCR (`radio_text_matches`).
  - `app/services/speaker_design.py` (News + Facebook, `size="1024x1536"`) → lalu
    `identity_score(speaker_bytes, generated) < IDENTITY_THRESHOLD (0.40)` → `None` (template) → `_finish()`
    (crop atas 4:5 → 1080x1350).
- Gambar referensi → data URL: `visual_engine._image_to_b64(path)` (sisi terpanjang 1536 px, JPEG q92).
- Vision 9router: `app/services/design_images.py::_vision_chat(content, max_tokens, temperature, context)`
  dan `_vision_datauri(bytes)`. Model sudah dikunci ke `FlashFast` (aturan pemilik).
- `AICopyEvent` (`app/models/ai_copy_events.py`): `context`, `fanpage_id`, `outcome`, `models_tried`
  (String 256), `final_provider`, `error_message` (Text), `latency_ms`.
- Enkripsi: `app/services/encryption.py::encrypt/decrypt`. Redis: `redis.from_url(get_settings().redis_url, decode_responses=True)`.
- Settings API: `app/schemas/settings.py`, `app/api/settings.py` (pola token: `has_*` di output,
  field plain di input → `encrypt`). UI: `frontend/src/app/(dashboard)/settings/page.tsx`.
- `visual_engine_daily_max` **belum** ada di schema/API/UI (hanya di model) → harus ditambahkan (B2).
- Alembic head saat ini: `c80000000024`.
- Antrian Celery: IG recreate sudah ke `visual` (`ig_recreate.py:329`, `api/radar.py`). News/FB render
  ke antrian default.

### B1. Model + migrasi

`app/models/settings.py` — kolom baru di `Settings`:

| Kolom | Tipe | Default | Arti |
|---|---|---|---|
| `gptcli_enabled` | Boolean | `false` | saklar global |
| `gptcli_base_url` | String(256) | `http://172.17.0.1:8099` | alamat gpt serve |
| `gptcli_token_encrypted` | String(512) | null | token Bearer (terenkripsi) |
| `gptcli_fanpage_ids` | ARRAY(Integer) | `{}` | kosong = semua fanpage; isi = hanya itu |
| `gptcli_thread` | String(64) | `studio` | |
| `gptcli_size` | String(16) | `1080x1350` | |
| `ai_day_start` | String(5) | `05:30` | awal siang WIB (= akhir jam tidur gpt serve) |
| `codex_share_percent` | Integer | 35 | porsi maks Codex di siang |
| `codex_share_window_jobs` | Integer | 40 | jumlah job siang terakhir untuk menghitung porsi |
| `codex_overflow_queue_len` | Integer | 3 | antrian `visual` ≥ ini → utamakan Codex (masih dalam jatah porsi) |
| `codex_share_max_pct` | Integer | 60 | porsi siang hanya selama pemakaian 5 jam Codex < ini |
| `codex_fallback_max_pct` | Integer | 85 | cadangan boleh sampai ini |
| `codex_night_max_pct` | Integer | 70 | malam berhenti di ini |
| `codex_rest_pct` | Integer | 95 | ≥ ini → Codex istirahat |
| `codex_window_capacity_est` | Integer | 60 | perkiraan kapasitas 5 jam (gambar) untuk estimasi persen kalau 9router tidak memberi angka |
| `codex_night_min_gap_s` | Integer | 240 | jeda malam antar gambar Codex |
| `codex_night_gap_jitter_s` | Integer | 60 | jitter ± |
| `codex_usage_url` | String(512) | null | endpoint pemakaian Codex di 9router (dari temuan A3; kosong = estimasi sendiri) |

Ubah default `visual_engine_daily_max` di model **tidak** perlu; nilai produksi diset pemilik ke 170
lewat UI (B2). Migrasi Alembic baru `c80000000025_gptcli_engine.py` (`down_revision = "c80000000024"`),
`server_default` untuk semua kolom, downgrade menghapus kolom.

### B2. Schema, API, UI Settings

- `app/schemas/settings.py`: input semua kolom B1 (token sebagai `gptcli_token: Optional[str]`
  plain) + `visual_engine_daily_max`; output semua kecuali token → `has_gptcli_token: bool`.
- `app/api/settings.py`: `gptcli_token` → `encrypt` → `gptcli_token_encrypted` (pola sama seperti
  `nine_router_api_key`); validasi angka (persen 0–100, `gptcli_base_url` harus `http(s)://`).
- UI `settings/page.tsx`: kartu baru **"AI Image Engine (ChatGPT / Codex)"** di dekat kartu 9Router:
  - saklar gpt-cli, base URL, token (write-only, tampil "tersimpan"), daftar fanpage (multi-select
    dari daftar fanpage), thread, size;
  - kelompok "Codex": porsi siang %, panjang antrian limpahan, batas % (porsi/cadangan/malam/istirahat),
    kapasitas perkiraan, jeda malam + jitter, URL pemakaian Codex;
  - "Batas harian Codex" (`visual_engine_daily_max`);
  - ringkasan status hari ini (dari endpoint baru B11): jumlah per engine, porsi Codex siang, estimasi
    pemakaian 5 jam Codex.

### B3. Klien gpt serve — `app/services/gptcli_client.py` (baru)

```python
@dataclass(frozen=True)
class GptcliConfig:   # dibaca dari Settings, cache 30 s seperti nine_router.get_nine_router_config
    enabled: bool; base_url: str; token: str; fanpage_ids: tuple[int, ...]; thread: str; size: str

class GptcliError(Exception):
    def __init__(self, type_: str, message: str, *, status: int | None, retry_after_s: int | None, extra: dict): ...

@dataclass(frozen=True)
class GptcliResult:
    png: bytes; meta: dict   # blok "gptcli" apa adanya

def get_gptcli_config(db) -> GptcliConfig
def gptcli_applies(cfg, fanpage_id) -> bool          # enabled and (not fanpage_ids or fanpage_id in fanpage_ids) and token
def request_id_for(prompt: str, images: list[str]) -> str   # "cs-" + sha256(prompt + "\n".join(images)).hexdigest()[:24]
def job_timeout_s(n_images: int) -> int               # 0→120, 1–2→300, ≥3→600
def generate_image(cfg, prompt, images, *, request_id) -> GptcliResult
```
- `generate_image`: `httpx.post(f"{base_url}/v1/images/generations", json={prompt, images, size, thread},
  headers={Authorization, X-Request-Id}, timeout=job_timeout_s(n)+120)`.
- 200 → decode `data[0].b64_json` (validasi PNG dengan PIL; gagal → `GptcliError("bad_response")`).
- Non-200 → parse `error.type`/`message` + header `Retry-After` → `GptcliError`.
- `httpx.ConnectError`/`ConnectTimeout` → `GptcliError("unreachable")`; `ReadTimeout` → `GptcliError("timeout")`.
- **Status sementara di Redis:** untuk `sleeping`, `quota`, `soft_cap`, `logged_out`, `unavailable`,
  `cloudflare_challenge`, `unreachable` simpan `gptcli:unavailable_until` = sekarang + (`Retry-After`
  atau default: sleeping→sampai `ai_day_start`, quota→10800, soft_cap→3600, logged_out/cloudflare→1800,
  unavailable/unreachable→300). Selama masih berlaku, app **tidak** memanggil gpt serve (langsung Codex).
- Jangan pernah mencatat token. Sanitasi pesan error seperti `sanitize_err` di `visual_engine.py`.

### B4. Anggaran Codex — `app/services/codex_budget.py` (baru)

```python
@dataclass(frozen=True)
class CodexUsage:
    window_pct: float          # pemakaian jendela 5 jam, 0–100
    weekly_pct: float | None
    resets_at: datetime | None
    source: Literal["9router", "estimate"]

def get_codex_usage(db, now) -> CodexUsage
def codex_resting(db, now) -> bool       # Redis "codex:rest_until" (diset saat 9router 429 / pct ≥ rest)
def mark_codex_rest(until: datetime)
def wait_night_gap(db, now) -> None      # malam saja; tidur sampai jeda terpenuhi (maks gap+jitter)
```
- Kalau `codex_usage_url` terisi (dari A3): ambil, parse sesuai format temuan A3, cache 60 s di Redis.
  Gagal ambil → jatuh ke estimasi (jangan menganggap 0%).
- Estimasi: `count(AICopyEvent context='image_gen', final_provider='9router', created_at > now-5h)
  / codex_window_capacity_est * 100`.
- Batas mingguan (kalau ada): kalau `weekly_pct > (hari_berjalan_minggu_ini / 7) * 100 + 15`,
  porsi siang dianggap habis (`share` tidak boleh dipakai), cadangan tetap boleh.
- Jeda malam: Redis `codex:last_at`; target jeda = `codex_night_min_gap_s ± random(jitter)`;
  `time.sleep(sisa)` hanya di worker (job malam memang lewat `worker-visual`, satu slot).

### B5. Pemilih engine — `app/services/engine_router.py` (baru)

```python
@dataclass(frozen=True)
class EnginePlan:
    order: tuple[Literal["gptcli", "9router"], ...]   # urutan yang dicoba
    reason: str                                       # "night", "share", "overflow", "gptcli_unavailable", "gptcli_off", ...
    defer_until: datetime | None = None               # tidak ada engine yang boleh → tunda

def plan_engines(db, fanpage_id, now, *, exclude: frozenset[str] = frozenset()) -> EnginePlan
```
Logika (WIB):
1. gpt-cli tidak berlaku untuk fanpage ini (`gptcli_applies` false) → `("9router",)` reason `gptcli_off`
   (**perilaku lama persis**, termasuk batas harian & QA lama).
2. **Malam** (00:00 ≤ t < `ai_day_start`): kandidat hanya Codex.
   - Codex istirahat, atau `window_pct ≥ codex_night_max_pct`, atau batas harian Codex habis →
     `defer_until` = paling awal antara reset Codex (kalau diketahui) dan `ai_day_start` hari ini.
   - Selain itu → `("9router",)` reason `night` (+ `wait_night_gap` sebelum memanggil).
3. **Siang**:
   - gpt serve sedang tidak tersedia (Redis `gptcli:unavailable_until`) → `("9router",)` reason
     `gptcli_unavailable` (cadangan, batas `codex_fallback_max_pct`).
   - Hitung porsi Codex dari `codex_share_window_jobs` event siang terakhir yang memang memilih antara
     dua engine (`reason` ∈ {share, overflow, gptcli_first}); jangan ikut menghitung event cadangan.
   - Jatah porsi masih ada **dan** `window_pct < codex_share_max_pct` **dan** Codex tidak istirahat:
     - antrian Redis `visual` (`LLEN visual`) ≥ `codex_overflow_queue_len` → `("9router","gptcli")` reason `overflow`;
     - atau acak (`random() < sisa_jatah`) → `("9router","gptcli")` reason `share`.
   - Selain itu → `("gptcli","9router")` reason `gptcli_first`.
4. `exclude` menghapus engine dari `order` (dipakai saat hasil ChatGPT ditolak cek wajah → Codex saja).
5. Codex di posisi mana pun hanya boleh dipakai kalau `window_pct < codex_fallback_max_pct` (siang) /
   `codex_night_max_pct` (malam), tidak istirahat, dan batas harian (`visual_engine_daily_max`, hanya
   menghitung `final_provider='9router'`) belum habis. Kalau tidak boleh → dibuang dari `order`.
6. `order` kosong → siang: `VisualEngineError('quota', ...)` (pemanggil jatuh ke template seperti
   sekarang); malam: `defer_until` diisi.

### B6. `app/services/visual_engine.py`

- Tambah:
  ```python
  @dataclass(frozen=True)
  class GenerationResult:
      image: bytes
      engine: Literal["gptcli", "9router"]
      meta: dict           # gptcli: blok gptcli; 9router: {}
      event_id: int | None

  class VisualEngineDeferred(Exception):
      def __init__(self, until: datetime, reason: str): ...

  def generate_ex(db, req, *, timeout=600, fanpage_id=None, exclude=frozenset()) -> GenerationResult
  def generate(db, req, *, timeout=600, fanpage_id=None) -> bytes   # = generate_ex(...).image (kompatibel)
  ```
- `generate_ex`:
  1. `plan = plan_engines(...)`; `plan.defer_until` → `raise VisualEngineDeferred`.
  2. Untuk tiap engine di `plan.order`:
     - `gptcli`: `images = [_image_to_b64(p) for p in req.refs]`, `rid = request_id_for(...)`,
       `gptcli_client.generate_image(...)`. Sukses → selesai. `GptcliError` → simpan alasan, lanjut.
     - `9router`: jalur lama **tanpa diubah** (2 percobaan, sanitasi, klasifikasi error), dibungkus
       fungsi `_generate_nine_router(...)`. Malam: `codex_budget.wait_night_gap` dulu; setelah selesai
       set `codex:last_at`. 429/quota dari 9router → `mark_codex_rest(...)`.
  3. Batas harian lama dipindah ke dalam cabang 9router dan hanya menghitung
     `final_provider='9router'`. Saat gpt-cli tidak berlaku (`gptcli_off`), tetap hitung seperti
     sekarang (semua `image_gen`) supaya perilaku lama sama persis.
  4. **Satu** `AICopyEvent(context='image_gen')` per panggilan:
     - `final_provider`: engine yang berhasil, atau null;
     - `outcome`: `success` (engine pertama berhasil), `recovered` (engine pertama gagal, berikutnya
       berhasil), `failed` (semua gagal);
     - `models_tried`: mis. `gptcli>cx/gpt-image-2.5` (≤ 256 char);
     - `error_message` (Text): `plan=<reason>; gptcli:<type>: <pesan>; 9router: <pesan>`. Untuk
       `refused`: sertakan teks penolakan sampai 1.500 karakter + `chat_url`. Untuk sukses gpt-cli:
       `plan=<reason>; chat_url=…; attempts=…; queue_wait_s=…`.
  5. Semua gagal → `VisualEngineError` dengan `kind` dari engine terakhir (perilaku pemanggil lama tetap).

### B7. Cek wajah ganda — `app/services/identity_check.py` (baru)

```python
@dataclass(frozen=True)
class IdentityResult:
    passed: bool
    sface: float | None        # None = wajah tidak terdeteksi / model tak tersedia
    gemini: Literal["yes", "no", "error", "skipped"]
    reason: str

def check_identity(ref_bytes: bytes, out_bytes: bytes, *, subject: str, context: str) -> IdentityResult
```
1. `s = speaker_design.identity_score(ref_bytes, out_bytes)` (SFace lokal; mengembalikan 0.0 kalau
   tidak ada wajah/model → perlakukan 0.0 dari "tidak terdeteksi" sebagai **ragu**, bukan ditolak;
   bedakan dengan `face_info(out_bytes)` / `_detect_faces`).
2. Wajah terdeteksi jelas di hasil **dan** `s < 0.25` → `passed=False`, gemini `skipped`.
3. Selain itu **selalu** tanya Gemini lewat `_vision_chat` (context `visual_identity`, temperature 0):
   konten = [teks instruksi, `_vision_datauri(ref)`, `_vision_datauri(out)`], minta **JSON ketat**:
   `{"same_person": true|false, "confidence": 0-1, "notes": "..."}`. Instruksi: bandingkan wajah orang
   utama (subject) di gambar 2 dengan gambar 1; abaikan pakaian, helm, latar, gaya; helm/visor
   tertutup sepenuhnya → nilai dari fitur yang terlihat; ragu → `false`.
4. `passed = same_person is True and confidence ≥ 0.6`. JSON tak terbaca / error → `passed=False`,
   gemini `error` (fail-closed).
5. Catat satu `AICopyEvent(context='image_identity', outcome='success'|'failed', final_provider='gptcli',
   error_message=f"sface={s:.2f} gemini=… conf=… {reason}")`.

### B8. `app/services/redesign_pipeline.py` (Instagram)

Ganti `gen_bytes = generate(...)` menjadi `res = generate_ex(...)` (tangkap `VisualEngineDeferred`, lihat B10).
- `res.engine == "gptcli"`:
  1. `check_identity(hero_ref_bytes, res.image, subject=analysis.main_subject, context="ig")`.
  2. Layout `team_radio`: OCR seperti di `check_generated` (pakai fungsi OCR + `radio_text_matches`
     yang sama). OCR error → dianggap gagal (fail-closed).
  3. Lolos → **lewati** `check_generated`, lanjut langkah render seperti hasil yang lolos QA.
  4. Gagal → `res = generate_ex(..., exclude={"gptcli"})` lalu jalur QA lama (`check_generated`) untuk
     hasil Codex. Codex juga gagal/QA gagal → perilaku lama (template / gagal untuk konten `other`).
- `res.engine == "9router"`: tidak berubah.
- Catat di `notes` engine yang dipakai dan hasil cek wajah.

### B9. `app/services/speaker_design.py` (News + Facebook)

- Ganti `visual_engine.generate(...)` menjadi `generate_ex(...)`; untuk gpt-cli kirim ukuran dari
  `gptcli_size` (1080x1350) — ukuran `GenerationRequest.size` untuk 9router tetap `1024x1536`.
- `engine == "gptcli"`: `check_identity(speaker_bytes, res.image, subject=speaker, context="news"|"facebook")`.
  Gagal → `generate_ex(..., exclude={"gptcli"})` → cek lama `identity_score < IDENTITY_THRESHOLD`.
- `engine == "9router"`: cek lama tidak berubah.
- `_finish()` tetap dipanggil untuk keduanya.
- `compose_speaker_design` saat ini menelan semua exception dan mengembalikan `None`. Biarkan, **kecuali**
  `VisualEngineDeferred` yang harus diteruskan ke pemanggil (B10).

### B10. Penundaan malam (`VisualEngineDeferred`)

Hanya terjadi malam saat Codex tidak boleh dipakai. Job harus **ditunda, bukan dibuang dan bukan
jatuh ke template**.
- Pelajari mekanisme klaim/re-dispatch yang ada (`render_pending_designs`, `recover_stuck_renders`,
  status `pending_design`/`rendering`, `render_ig_recreate`, `run_preview_redesign`) dan pilih cara
  paling bersih supaya job dijalankan lagi **setelah** `until` dan tidak diambil ulang lebih awal oleh
  beat. Usulan: kolom baru `publish_jobs.design_not_before` (DateTime, null) yang dihormati
  `render_pending_designs`/`recover_stuck_renders`, + status dikembalikan ke `pending_design`
  (atau padanannya untuk IG recreate), + `apply_async(..., countdown=…, queue=<sama>)`.
- Tidak boleh memakan `max_retries` task.
- Catat event `context='image_gen'`, `outcome='deferred'`, `error_message=f"until=…; reason=…"`.
- Tes: job malam ditunda, tidak diambil beat sebelum `until`, dijalankan setelahnya.

### B11. Routing ke `worker-visual` + endpoint status

- `app/services/engine_router.py::render_queue_for(db, fanpage, content_kind) -> str | None`:
  `"visual"` kalau gpt-cli berlaku untuk fanpage itu **dan** toggle AI konten itu menyala
  (`news_visual_engine == "chatgpt"` untuk news, `fb_visual_engine == "chatgpt"` untuk facebook);
  selain itu `None` (antrian default, perilaku lama).
- Pakai di titik dispatch (cek ulang nomor baris di `main` terbaru):
  - `app/tasks/news_copywriter.py` (`render_design.apply_async(..., countdown=stagger)`)
  - `app/tasks/design_renderer.py` (`render_design.delay(job_id)` di `render_pending_designs`,
    `render_facebook_photo.delay(job_id)`)
  - `app/tasks/facebook_photo.py` (`render_facebook_photo.apply_async(..., countdown=…)`)
  - `app/api/publish_jobs.py` (dua `render_design.delay`)
  Gunakan `apply_async(args=[...], queue=q)` hanya kalau `q` tidak None.
- Endpoint baru `GET /settings/ai-engine-status` (auth sama seperti settings): hari ini per engine
  (success/recovered/failed/deferred), porsi Codex siang, `CodexUsage`, `gptcli:unavailable_until`,
  panjang antrian `visual`. Dipakai kartu Settings (B2).

### B12. Monitoring Hermes

- `app/scripts/visual_engine_health.py`: tambahkan (tanpa merusak field lama yang dipakai runbook):
  - `by_engine`: jumlah per `final_provider` dan per `outcome` (6 jam);
  - `gptcli_errors`: jumlah per `error.type` gpt-cli (6 jam);
  - `refusals`: daftar event baru dengan `gptcli:refused` (id, waktu, fanpage, cuplikan 300 char, `chat_url`);
  - `cloudflare`: daftar event baru dengan `cloudflare_challenge`;
  - `identity_rejects`: jumlah `image_identity` gagal (6 jam);
  - `codex_usage`: `CodexUsage`;
  - `consecutive_failures` hanya menghitung `outcome='failed'` (`deferred` tidak dihitung).
- `docs/runbooks/visual-engine-incidents.md` (bahasa Indonesia, gaya yang sama):
  - **penolakan** → kirim setiap event baru (dedupe per id, gabung beberapa dalam satu pesan): fanpage,
    layout, teks penolakan, link chat, "post tetap jalan lewat Codex/template";
  - **cloudflare_challenge** → sekali per 6 jam, minta pemilik cek VNC;
  - **gagal total ≥ 3 beruntun** → sekali per 6 jam;
  - **cek wajah ditolak ≥ 5 dalam 6 jam** → sekali per 6 jam (indikasi prompt/foto referensi bermasalah);
  - quota/soft_cap/sleeping/logout/proxy gpt serve → **tidak** dari runbook ini (sudah ditangani
    `gpt-health`/`gpt-heal`); Codex istirahat (≥ 95%) → sekali per jendela.

### B13. Tes (pytest, gaya `tests/test_visual_engine.py` — mock httpx/DB)

- `test_gptcli_client.py`: sukses decode PNG; tiap `error.type` → `GptcliError` dengan type benar;
  `Retry-After` dibaca; connect error → `unreachable`; read timeout → `timeout`; `request_id_for` stabil
  dan berbeda bila gambar berbeda; `job_timeout_s`; Redis unavailable-until diset per type.
- `test_engine_router.py`: gpt-cli off → `("9router",)` + batas harian lama; malam → hanya Codex /
  defer saat ≥ 70% atau batas habis; siang porsi < 35% + antrian ≥ 3 → overflow; porsi sudah 35% →
  gpt-cli dulu walau antrian panjang; gpt serve unavailable → Codex cadangan; Codex ≥ 85% → dibuang;
  `exclude`; fanpage di luar daftar.
- `test_codex_budget.py`: estimasi persen; parse sumber 9router (fixture dari A3); fallback ke estimasi
  bila gagal; rest; jeda malam (mock `time.sleep`).
- `test_visual_engine.py` (tambahan): gpt-cli sukses tanpa memanggil 9router; tiap error gpt-cli → 9router
  (`recovered`); dua-duanya gagal → `failed` + `VisualEngineError`; isi event; `generate()` lama tetap
  mengembalikan bytes; batas harian hanya menghitung 9router saat gpt-cli berlaku.
- `test_identity_check.py`: SFace jelas rendah → tolak tanpa Gemini; SFace tinggi + Gemini no → tolak;
  wajah tak terdeteksi + Gemini yes → lolos; Gemini error / JSON rusak → tolak; confidence < 0.6 → tolak.
- `test_redesign_pipeline*.py`: hasil gpt-cli lolos cek wajah → `check_generated` tidak dipanggil;
  gagal → Codex + QA lama; team radio OCR salah/error → Codex.
- `test_speaker_design_wiring.py`: analog.
- Penundaan malam (B10) dan routing `render_queue_for` (B11).
- `tests/scripts/test_visual_engine_health.py`: field baru.
- Jalankan seluruh suite backend yang relevan; tidak boleh ada tes lama yang rusak.

### B14. Dokumentasi

- `docs/runbooks/gptcli-engine.md` (baru): cara menyalakan/mematikan, token, daftar fanpage, arti angka
  Codex %, membaca Logs (`image_gen`, `image_identity`), hubungan dengan `gpt-health`/`gpt-heal`,
  langkah darurat (matikan saklar = kembali ke perilaku lama).
- `docs/codemap/design-images.md` dan `docs/codemap/rendering-and-recreate.md`: engine baru, cek wajah
  ganda, routing antrian.

### B15. Selesai fase B

- Semua tes hijau. Commit kecil per bagian dengan pesan jelas. Push ke branch kerja.
- Ringkasan untuk pemilik: daftar file yang berubah, migrasi, setting baru beserta default,
  cara mematikan.

---

## 6. FASE C — Deploy & aktivasi (pemilik / dengan izin pemilik)

1. Merge + deploy seperti biasa (build image, `alembic upgrade head`, restart service). Saklar masih mati
   → tidak ada perubahan perilaku.
2. Settings app:
   - isi token gpt serve (dari `/etc/gpt-serve.env`), base URL `http://172.17.0.1:8099`;
   - daftar fanpage gpt-cli = **GP Racing** saja;
   - batas harian Codex (`visual_engine_daily_max`) = **170**;
   - URL pemakaian Codex = hasil A3 (kalau ada); angka % biarkan default.
3. Fanpage GP Racing: pastikan toggle AI editing yang ingin diuji menyala (`visual_engine`,
   `news_visual_engine`, `fb_visual_engine` = `chatgpt`).
4. Prasyarat lolos: A1, A2, A4 (uji 14 tidak tercampur).
5. Nyalakan `gptcli_enabled`.
6. Pantau 1–2 jam pertama: Logs (`image_gen`, `image_identity`), `/health` gpt serve, hasil post.
   Darurat: matikan saklar → kembali ke perilaku lama seketika.

---

## 7. FASE D — Evaluasi & perluasan

- Beberapa hari di GP Racing. Bandingkan per engine: `success`/`recovered`/`failed`/`deferred`,
  alasan error gpt-cli terbanyak, penolakan cek wajah, porsi Codex siang aktual, pemakaian Codex %.
- Lihat hasil visual bersama pemilik.
- Setel ulang: `GPT_SERVE_DAILY_CAP` (naik bertahap 140 → 160 → 180 kalau tidak pernah `quota`),
  persen Codex, kapasitas perkiraan Codex.
- Perluas: tambah fanpage satu per satu ke daftar gpt-cli, atau kosongkan daftar (= semua fanpage).
- Kalau kebutuhan > ±300/hari: akun ChatGPT kedua (`GPT_SERVE_PROFILES=utama,kedua`, login VNC,
  proxy per profil bila perlu), lalu `worker-visual` concurrency 2.
- Tahap 2: Pinterest + Discussion (toggle + desain/prompt AI baru), memakai engine & aturan yang sama.

---

## 8. Risiko & catatan

- **Pencampuran antar job di satu thread ChatGPT** (uji 14). Mitigasi: header "abaikan gambar
  sebelumnya" di gpt serve, `MAX_TURNS=15`, percakapan baru setelah jam tidur, cek wajah ganda.
  Kalau uji 14 gagal → turunkan `MAX_TURNS`.
- **Gemini terlalu longgar/ketat** di cek wajah → pantau `image_identity`; ambang confidence (0.6) dan
  batas SFace (0.25/0.40) bisa dijadikan setting kalau perlu.
- **Kuota Codex tidak terbaca dari 9router** → estimasi sendiri (kapasitas 60/5 jam) + deteksi 429.
  Setel `codex_window_capacity_est` dari data nyata.
- **Throughput:** satu `worker-visual`. Perkiraan siang: 140 × ±3 menit (ChatGPT) + 125 × ±1,5 menit
  (Codex) ≈ 10 jam dalam 18,5 jam siang. Malam: ≤ ±42 gambar × ±5,5 menit ≈ 4 jam dalam 5,5 jam.
- **Fail-closed** berarti lebih banyak jatuh ke Codex/template saat Gemini/OCR bermasalah — sesuai
  aturan pemilik (akurasi di atas ketersediaan).
- Saklar mati = perilaku lama 100%. Ini jalur mundur tercepat.
