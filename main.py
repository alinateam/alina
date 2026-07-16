from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
import os
import logging
import requests
import time
import uuid
import psutil
from datetime import datetime, timedelta
from collections import defaultdict
from supabase import create_client, Client
import boto3
from botocore.config import Config
from typing import Optional, List, Dict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Alina AI", version="2.4.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

if not os.path.exists("static"):
    os.makedirs("static")

app.mount("/static", StaticFiles(directory="static"), name="static")

INSTRUKSI_SISTEM = f"""
Anda adalah Alina AI, asisten cerdas yang dikembangkan di Indonesia oleh Tim Alina.
Tanggal saat ini: {datetime.now().strftime('%d %B %Y')}.
Aturan wajib:
1. Jawab selalu dalam bahasa Indonesia atau bahasa yang digunakan pengguna dengan lembut, sopan, jelas, dan mudah dimengerti.
2. Jika ditanya siapa Anda atau pembuatnya, jawab: "Saya Alina AI, kecerdasan buatan yang dikembangkan di Indonesia oleh Tim Alina."
3. Jawab lengkap, akurat dan sesuai konteks percakapan sebelumnya.
4. Jika tidak tahu jawaban, katakan dengan jujur.
5. Jika diminta merangkum, berikan ringkasan yang padat, jelas, dan mencakup poin-poin penting.
6. Tawarkan informasi tambahan yang relevan sebagai penutup jawaban.
"""

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "alina-sementara")
SUPABASE_MAX_MB = 40
SUPABASE_PINDAH_MB = 15
MAKS_UKURAN_FILE_MB = 4
TAUTAN_BERLAKU_DETIK = 60 * 60 * 24 * 7 

B2_KEY_ID = os.getenv("B2_KEY_ID", "")
B2_APPLICATION_KEY = os.getenv("B2_APPLICATION_KEY", "")
B2_ENDPOINT = os.getenv("B2_ENDPOINT", "s3.us-east-005.backblazeb2.com")
B2_BUCKET = os.getenv("B2_BUCKET", "alina-utama")
B2_MAX_MB = 9 * 1024
B2_SISA_MB = 8 * 1024

MAKS_KONTEKS = 6 
BATAS_PERMINTAAN_MENIT = 15 
BATAS_PERMINTAAN_JAM = 150 
JENIS_FILE_DIIZINKAN = {"image/jpeg", "image/png", "image/gif", "image/webp"}
EKSTENSI_DIIZINKAN = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

RIWAYAT_OBROLAN: List[Dict] = []
KONTEKS_PERCAKAPAN: List[Dict] = []
PEMANTAUAN_AKSES: Dict[str, List[float]] = defaultdict(list)
CADANGAN_KONFIGURASI: Dict = {}

URUTAN_MODEL = [
    {"nama": "Groq", "aktif": False, "client": None, "model": "llama-3.1-8b-instant"},
    {"nama": "Gemini", "aktif": False, "client": None, "model": "gemini-2.0-flash-lite"},
    {"nama": "OpenRouter", "aktif": False, "model": "google/gemini-2.0-flash-lite-preview-02-05:free"},
    {"nama": "Mistral", "aktif": False, "model": "mistral-small-latest"}
]

if GROQ_API_KEY:
    try:
        from groq import Groq
        URUTAN_MODEL[0]["client"] = Groq(api_key=GROQ_API_KEY)
        URUTAN_MODEL[0]["aktif"] = True
        logger.info("✅ Groq siap digunakan")
    except Exception as e:
        logger.warning(f"⚠️ Groq gagal dimuat: {e}")

if GEMINI_API_KEY:
    try:
        import google.genai as genai
        URUTAN_MODEL[1]["client"] = genai.Client(api_key=GEMINI_API_KEY)
        URUTAN_MODEL[1]["aktif"] = True
        logger.info("✅ Gemini siap digunakan")
    except Exception as e:
        logger.warning(f"⚠️ Gemini gagal dimuat: {e}")

if OPENROUTER_API_KEY:
    URUTAN_MODEL[2]["aktif"] = True
    logger.info("✅ OpenRouter siap digunakan")

if MISTRAL_API_KEY:
    URUTAN_MODEL[3]["aktif"] = True
    logger.info("✅ Mistral siap digunakan")

supabase: Optional[Client] = None
if SUPABASE_URL and SUPABASE_SERVICE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        logger.info("✅ Supabase Storage terhubung")
    except Exception as e:
        logger.warning(f"⚠️ Gagal terhubung ke Supabase: {str(e)}")

b2 = None
if B2_KEY_ID and B2_APPLICATION_KEY:
    try:
        b2 = boto3.client(
            "s3",
            endpoint_url=f"https://{B2_ENDPOINT}",
            aws_access_key_id=B2_KEY_ID,
            aws_secret_access_key=B2_APPLICATION_KEY,
            config=Config(signature_version="s3v4"),
            region_name="us-east-005"
        )
        logger.info("✅ Backblaze B2 terhubung")
    except Exception as e:
        logger.warning(f"⚠️ Gagal terhubung ke Backblaze B2: {str(e)}")

def cek_batasan_akses(ip_pengguna: str) -> bool:
    sekarang = time.time()
    PEMANTAUAN_AKSES[ip_pengguna] = [waktu for waktu in PEMANTAUAN_AKSES[ip_pengguna] if sekarang - waktu < 3600]
    
    hitungan_menit = sum(1 for waktu in PEMANTAUAN_AKSES[ip_pengguna] if sekarang - waktu < 60)
    hitungan_jam = len(PEMANTAUAN_AKSES[ip_pengguna])

    if hitungan_menit >= BATAS_PERMINTAAN_MENIT:
        logger.warning(f"🚫 Batas akses tercapai: IP {ip_pengguna} → {hitungan_menit} permintaan/menit")
        return False
    if hitungan_jam >= BATAS_PERMINTAAN_JAM:
        logger.warning(f"🚫 Batas akses tercapai: IP {ip_pengguna} → {hitungan_jam} permintaan/jam")
        return False

    PEMANTAUAN_AKSES[ip_pengguna].append(sekarang)
    return True

def verifikasi_file(nama_file: str, tipe_konten: str = None) -> bool:
    ekstensi = os.path.splitext(nama_file.lower())[1]
    if ekstensi not in EKSTENSI_DIIZINKAN:
        logger.warning(f"⚠️ Ekstensi tidak diizinkan: {ekstensi} pada {nama_file}")
        return False
    if tipe_konten and tipe_konten not in JENIS_FILE_DIIZINKAN:
        logger.warning(f"⚠️ Tipe konten tidak diizinkan: {tipe_konten} pada {nama_file}")
        return False
    return True

def dapatkan_status_server() -> Dict:
    try:
        penggunaan_cpu = psutil.cpu_percent(interval=0.5)
        memori = psutil.virtual_memory()
        penggunaan_memori = memori.percent
        ruang_disk = psutil.disk_usage('/')
        return {
            "waktu": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
            "cpu_persen": penggunaan_cpu,
            "memori_persen": penggunaan_memori,
            "memori_terpakai": f"{round(memori.used / (1024**3), 2)} GB",
            "memori_total": f"{round(memori.total / (1024**3), 2)} GB",
            "disk_persen": round((ruang_disk.used / ruang_disk.total) * 100, 1),
            "status": "Normal" if penggunaan_cpu < 85 and penggunaan_memori < 85 else "Tinggi"
        }
    except Exception as e:
        logger.error(f"❌ Gagal memantau sumber daya: {e}")
        return {"status": "Tidak dapat dibaca", "error": str(e)}

def simpan_cadangan_konfigurasi() -> None:
    global CADANGAN_KONFIGURASI
    try:
        CADANGAN_KONFIGURASI = {
            "tanggal_cadangan": datetime.now().isoformat(),
            "batasan": {
                "maks_konteks": MAKS_KONTEKS,
                "batas_menit": BATAS_PERMINTAAN_MENIT,
                "batas_jam": BATAS_PERMINTAAN_JAM,
                "maks_ukuran_file_mb": MAKS_UKURAN_FILE_MB
            },
            "penyimpanan": {
                "supabase_bucket": SUPABASE_BUCKET,
                "supabase_maks_mb": SUPABASE_MAX_MB,
                "b2_bucket": B2_BUCKET,
                "tautan_berlaku_hari": TAUTAN_BERLAKU_DETIK // 86400
            },
            "model_aktif": [m["nama"] for m in URUTAN_MODEL if m["aktif"]]
        }
        logger.info("💾 Cadangan konfigurasi berhasil diperbarui")
    except Exception as e:
        logger.error(f"❌ Gagal menyimpan cadangan konfigurasi: {e}")

def ukuran_ke_mb(byte: int) -> float:
    return round(byte / (1024 * 1024), 2)

def buat_nama_file(deskripsi: str) -> str:
    waktu = datetime.now().strftime("%Y%m%d-%H%M%S")
    id_unik = str(uuid.uuid4())[:8]
    return f"{waktu}_{id_unik}.jpg"

def catat_riwayat(pertanyaan: str, jawaban: str):
    global RIWAYAT_OBROLAN, KONTEKS_PERCAKAPAN
    
    RIWAYAT_OBROLAN.append({
        "waktu": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "tanya": pertanyaan,
        "jawab": jawaban
    })
    if len(RIWAYAT_OBROLAN) > 50:
        RIWAYAT_OBROLAN.pop(0)
    
    KONTEKS_PERCAKAPAN.append({"role": "user", "content": pertanyaan})
    KONTEKS_PERCAKAPAN.append({"role": "assistant", "content": jawaban})
    
    if len(KONTEKS_PERCAKAPAN) > MAKS_KONTEKS * 2:
        KONTEKS_PERCAKAPAN = KONTEKS_PERCAKAPAN[-(MAKS_KONTEKS * 2):]

def reset_konteks():
    global KONTEKS_PERCAKAPAN
    KONTEKS_PERCAKAPAN = []
    return "✅ Konteks percakapan telah dihapus. Kita bisa mulai topik baru."

def dapatkan_info_supabase() -> tuple[float, List[Dict]]:
    if not supabase:
        return 0.0, []
    try:
        daftar = supabase.storage.from_(SUPABASE_BUCKET).list()
        daftar_valid = [
            f for f in daftar
            if isinstance(f, dict) and "name" in f and "metadata" in f
            and isinstance(f["metadata"], dict) and "size" in f["metadata"]
        ]
        total_byte = sum(f["metadata"]["size"] for f in daftar_valid)
        daftar_urut = sorted(daftar_valid, key=lambda x: x.get("created_at", ""))
        logger.info(f"📊 Supabase: {ukuran_ke_mb(total_byte)} MB | File: {len(daftar_valid)}")
        return ukuran_ke_mb(total_byte), daftar_urut
    except Exception as e:
        logger.warning(f"⚠️ Tidak bisa membaca Supabase: {str(e)}")
        return 0.0, []

def pindah_ke_backblaze(file: Dict) -> bool:
    if not supabase or not b2:
        return False
    try:
        nama = file["name"]
        if not verifikasi_file(nama):
            logger.warning(f"🚫 File tidak aman dilewati: {nama}")
            return False
        ukuran = ukuran_ke_mb(file["metadata"]["size"])
        data = supabase.storage.from_(SUPABASE_BUCKET).download(nama)
        b2.put_object(Bucket=B2_BUCKET, Key=f"arsip/{nama}", Body=data, ContentType="image/jpeg")
        supabase.storage.from_(SUPABASE_BUCKET).remove([nama])
        logger.info(f"📤 Pindah: {nama} ({ukuran} MB) → Backblaze")
        return True
    except Exception as e:
        logger.error(f"❌ Gagal pindah {nama}: {e}")
        return False

def cek_dan_pindah_supabase() -> None:
    total, daftar = dapatkan_info_supabase()
    if total < SUPABASE_MAX_MB or not daftar:
        return
    logger.warning(f"⚠️ Supabase hampir penuh, pindahkan {SUPABASE_PINDAH_MB} MB terlama")
    terpindah = 0.0
    for f in daftar:
        if terpindah >= SUPABASE_PINDAH_MB:
            break
        ukuran = ukuran_ke_mb(f["metadata"]["size"])
        if pindah_ke_backblaze(f):
            terpindah += ukuran
    logger.info(f"✅ Selesai pindah {terpindah:.2f} MB")

def dapatkan_info_b2() -> tuple[float, List[Dict]]:
    if not b2:
        return 0.0, []
    try:
        res = b2.list_objects_v2(Bucket=B2_BUCKET, Prefix="arsip/")
        daftar = res.get("Contents", [])
        total_byte = sum(obj["Size"] for obj in daftar)
        daftar_urut = sorted(daftar, key=lambda x: x["LastModified"])
        logger.info(f"📊 Backblaze: {ukuran_ke_mb(total_byte)} MB terpakai")
        return ukuran_ke_mb(total_byte), daftar_urut
    except Exception as e:
        logger.error(f"❌ Gagal membaca Backblaze: {e}")
        return 0.0, []

def cek_dan_bersihkan_b2() -> None:
    total, daftar = dapatkan_info_b2()
    if total < B2_MAX_MB or not daftar:
        return
    logger.warning(f"⚠️ Backblaze hampir penuh, hapus file hingga tersisa {B2_SISA_MB} MB")
    for f in daftar:
        if total <= B2_SISA_MB:
            break
        b2.delete_object(Bucket=B2_BUCKET, Key=f["Key"])
        total -= ukuran_ke_mb(f["Size"])
        logger.info(f"🗑️ Hapus: {f['Key']}")
    logger.info(f"✅ Selesai bersihkan, tersisa {total:.2f} MB")

def buat_gambar(deskripsi: str) -> str:
    try:
        prompt_lengkap = f"{deskripsi}, kualitas tinggi, tajam, warna cerah, resolusi 1024x1024, detail jelas, tidak ada cacat, gaya alami"
        url_panjang = f"https://image.pollinations.ai/prompt/{requests.utils.quote(prompt_lengkap)}?width=1024&height=1024&nologo=true&seed={os.urandom(4).hex()}"

        res_gambar = requests.get(url_panjang, timeout=25)
        res_gambar.raise_for_status()

        ukuran_file = ukuran_ke_mb(len(res_gambar.content))
        if ukuran_file > MAKS_UKURAN_FILE_MB:
            return f"❌ Ukuran terlalu besar ({ukuran_file:.1f} MB). Maksimal {MAKS_UKURAN_FILE_MB} MB."

        nama_file = buat_nama_file(deskripsi)
        if not verifikasi_file(nama_file, "image/jpeg"):
            return "❌ Jenis file tidak diizinkan."

        url_tautan = ""
        if supabase:
            try:
                supabase.storage.from_(SUPABASE_BUCKET).upload(
                    path=nama_file,
                    file=res_gambar.content,
                    file_options={"content-type": "image/jpeg"}
                )
                cek_dan_pindah_supabase()
                url_tautan = supabase.storage.from_(SUPABASE_BUCKET).get_public_url(nama_file)
            except Exception:
                if b2:
                    b2.put_object(Bucket=B2_BUCKET, Key=f"langsung/{nama_file}", Body=res_gambar.content, ContentType="image/jpeg")
                    cek_dan_bersihkan_b2()
                    url_tautan = b2.generate_presigned_url(
                        "get_object",
                        Params={"Bucket": B2_BUCKET, "Key": f"langsung/{nama_file}"},
                        ExpiresIn=TAUTAN_BERLAKU_DETIK
                    )
        else:
            res_pendek = requests.get(f"https://tinyurl.com/api-create.php?url={url_panjang}", timeout=15)
            res_pendek.raise_for_status()
            url_tautan = res_pendek.text.strip()

        hasil = f"""✅ Berikut gambar yang Anda minta:

🔗 {url_tautan}

*Tautan berlaku selama 7 hari*"""
        catat_riwayat(deskripsi, hasil)
        return hasil

    except Exception as e:
        logger.error(f"❌ Gambar gagal: {e}")
        return "❌ Maaf, fitur pembuatan gambar sedang bermasalah."

def cari_informasi(kueri: str) -> str | None:
    kueri_lengkap = f"{kueri} Indonesia terbaru"
    hasil_gabung = []

    if SERPAPI_KEY and len(SERPAPI_KEY) > 10:
        try:
            res = requests.get(
                "https://serpapi.com/search",
                params={"q": kueri_lengkap, "api_key": SERPAPI_KEY, "engine": "google", "hl": "id", "gl": "id", "num": 3},
                timeout=20
            )
            res.raise_for_status()
            data = res.json()
            if "answer_box" in data and data["answer_box"].get("snippet"):
                hasil_gabung.append(f"📌 {data['answer_box']['snippet']}")
            if "organic_results" in data:
                for item in data["organic_results"][:2]:
                    if item.get("snippet"):
                        hasil_gabung.append(f"• {item['snippet']}")
        except Exception as e:
            logger.debug(f"SerpApi error: {e}")

    try:
        res = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": kueri_lengkap, "format": "json", "no_html": 1, "no_redirect": 1, "kl": "id-id"},
            timeout=15
        )
        res.raise_for_status()
        data = res.json()
        if data.get("AbstractText"):
            hasil_gabung.append(f"ℹ️ {data['AbstractText']}")
    except Exception as e:
        logger.debug(f"DuckDuckGo error: {e}")

    if not hasil_gabung:
        return None

    teks_panjang = "\n\n".join(hasil_gabung)
    if len(teks_panjang) > 600:
        return f"🔍 **Informasi Terbaru:**\n\n{buat_rangkuman(teks_panjang)}\n\n*Sumber: Hasil pencarian terpercaya*"
    else:
        return f"🔍 **Informasi Terbaru:**\n\n{teks_panjang}"

def buat_rangkuman(teks: str) -> str:
    prompt = f"""
    Buatlah ringkasan yang padat, jelas, dan mencakup semua poin penting dari teks berikut ini.
    Gunakan bahasa Indonesia yang mudah dimengerti.

    Teks:
    {teks}

    Ringkasan:
    """
    return tanya_model(prompt, hanya_rangkuman=True)

def tanya_model(pertanyaan: str, hanya_rangkuman: bool = False) -> str:
    pesan_sistem = INSTRUKSI_SISTEM
    if hanya_rangkuman:
        pesan_sistem += "\nLangsung berikan ringkasannya saja, tidak perlu penjelasan tambahan."

    for model in URUTAN_MODEL:
        if not model["aktif"]:
            continue
        try:
            logger.info(f"🔍 Menggunakan model: {model['nama']}")
            
            if model["nama"] == "Groq":
                res = model["client"].chat.completions.create(
                    model=model["model"],
                    messages=[{"role": "system", "content": pesan_sistem}] + KONTEKS_PERCAKAPAN + [{"role": "user", "content": pertanyaan}],
                    max_tokens=1024,
                    temperature=0.7
                )
                return res.choices[0].message.content.strip()

            elif model["nama"] == "Gemini":
                pesan_tergabung = f"{pesan_sistem}\n\nRiwayat obrolan:\n{KONTEKS_PERCAKAPAN}\n\nPertanyaan: {pertanyaan}"
                res = model["client"].models.generate_content(model=model["model"], contents=pesan_tergabung)
                return res.text.strip()

            elif model["nama"] == "OpenRouter":
                res = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "HTTP-Referer": "https://alina.id", "X-Title": "Alina AI", "Content-Type": "application/json"},
                    json={"model": model["model"], "messages": [{"role": "system", "content": pesan_sistem}] + KONTEKS_PERCAKAPAN + [{"role": "user", "content": pertanyaan}], "max_tokens": 1024},
                    timeout=25
                )
                res.raise_for_status()
                return res.json()["choices"][0]["message"]["content"].strip()

            elif model["nama"] == "Mistral":
                res = requests.post(
                    "https://api.mistral.ai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {MISTRAL_API_KEY}"},
                    json={"model": model["model"], "messages": [{"role": "system", "content": pesan_sistem}] + KONTEKS_PERCAKAPAN + [{"role": "user", "content": pertanyaan}], "max_tokens": 1024},
                    timeout=25
                )
                res.raise_for_status()
                return res.json()["choices"][0]["message"].strip()

        except Exception as e:
            logger.warning(f"⚠️ Model {model['nama']} gagal: {e}")
            continue

    return "❌ Maaf, semua model sedang sibuk atau tidak tersedia. Silakan coba lagi nanti."

def dapatkan_jawaban(pertanyaan: str, ip_pengguna: str) -> str:
    teks = pertanyaan.strip().lower()

    if not cek_batasan_akses(ip_pengguna):
        return "⚠️ Terlalu banyak permintaan! Mohon tunggu sebentar sebelum mencoba lagi."

    if teks in ["reset", "hapus konteks", "mulai baru"]:
        return reset_konteks()
    if teks == "status server":
        return f"📊 **Status Server:**\n\n" + "\n".join([f"• {k}: {v}" for k, v in dapatkan_status_server().items()])
    if teks == "lihat cadangan":
        return f"💾 **Cadangan Konfigurasi:**\n\n" + "\n".join([f"• {k}: {v}" for k, v in CADANGAN_KONFIGURASI.items()])

    if teks.startswith(("buat gambar", "gambarkan", "bikin gambar", "gambar", "lukis")):
        return buat_gambar(pertanyaan)

    if teks.startswith(("rangkum", "ringkas", "buat ringkasan", "rangkumkan")):
        hasil = buat_rangkuman(pertanyaan.split(" ", 1)[1])
        catat_riwayat(pertanyaan, hasil)
        return hasil

    kata_cari = ["cari", "info terbaru", "berita", "data terbaru", "saat ini", "sekarang", "hari ini", "berapa harga", "kurs", "cuaca"]
    if any(kata in teks for kata in kata_cari):
        hasil_cari = cari_informasi(pertanyaan)
        if hasil_cari:
            catat_riwayat(pertanyaan, hasil_cari)
            return hasil_cari

    jawaban = tanya_model(pertanyaan)
    catat_riwayat(pertanyaan, jawaban)
    return jawaban

@app.get("/", response_class=HTMLResponse)
def halaman_utama():
    return """
    <!DOCTYPE html>
    <html lang="id">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Alina AI - AI-nya Orang Indonesia</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <link href="https://cdn.jsdelivr.net/npm/font-awesome@4.7.0/css/font-awesome.min.css" rel="stylesheet">
        <style>
        /* Perbaikan transisi yang stabil */
        .sidebar {
            transition: all 0.3s ease;
            overflow: hidden;
            width: 280px;
        }
        .sidebar.tertutup {
            width: 0;
            min-width: 0;
            border-right: none;
        }
        </style>
    </head>
    <body class="bg-gray-50 h-screen flex flex-col">
        <!-- Bar Atas -->
        <div class="bg-white shadow-sm border-b px-4 py-3 flex items-center justify-between">
            <div class="flex items-center gap-3">
                <!-- Tombol Buka -->
                <button id="tombolBuka" onclick="toggleSidebar()" class="text-gray-700 hover:text-blue-600 p-2 rounded hover:bg-gray-100">
                    <i class="fa fa-chevron-right fa-lg"></i>
                </button>

                <!-- Logo + Judul -->
                <div class="flex items-center gap-3">
                    <!-- Jalur logo diperbaiki, tambahkan pengecekan -->
                    <img src="/static/asset/logo.png" alt="Logo Alina" class="h-9 w-auto object-contain" onerror="this.style.display='none'">
                    <div>
                        <h1 class="text-xl font-bold text-gray-800 leading-tight">Alina AI</h1>
                        <p class="text-sm text-gray-500 italic">AI-nya Orang Indonesia</p>
                    </div>
                </div>
            </div>
            <small class="text-gray-500 text-xs">v2.4.1 - Keamanan & Pemantauan Aktif</small>
        </div>

        <!-- Konten Utama -->
        <div class="flex flex-1 overflow-hidden">
            <!-- Sidebar Riwayat -->
            <div id="sidebar" class="sidebar bg-white border-r shadow-sm">
                <div class="p-3 border-b flex items-center justify-between">
                    <h3 class="text-base font-semibold text-gray-700 whitespace-nowrap">Riwayat Obrolan</h3>
                    <div class="flex gap-2">
                        <button onclick="resetSemua()" class="text-gray-600 hover:text-blue-600 p-1.5 rounded hover:bg-gray-100" title="Mulai Baru">
                            <i class="fa fa-refresh"></i>
                        </button>
                        <button id="tombolTutup" onclick="toggleSidebar()" class="text-gray-600 hover:text-blue-600 p-1.5 rounded hover:bg-gray-100" title="Sembunyikan Riwayat">
                            <i class="fa fa-chevron-left"></i>
                        </button>
                    </div>
                </div>
                <div id="riwayat" class="p-3 overflow-y-auto h-[calc(100vh-120px)] text-sm space-y-2">
                    <p class="text-gray-400 text-center py-6">Belum ada riwayat</p>
                </div>
            </div>

            <!-- Area Obrolan -->
            <div id="areaObrolan" class="flex-1 flex flex-col h-full">
                <div id="kontenChat" class="flex-1 p-4 overflow-y-auto space-y-4 bg-gray-50"></div>
                <div class="p-3 bg-white border-t flex gap-2">
                    <input type="text" id="pesan" placeholder="Ketik pesan | Perintah: reset, status server, rangkum teks..." 
                           class="flex-1 border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                    <button onclick="kirimPesan()" class="bg-blue-600 hover:bg-blue-700 text-white px-5 py-2 rounded-lg transition">
                        <i class="fa fa-paper-plane"></i>
                    </button>
                </div>
            </div>
        </div>

        <!-- Semua fungsi JS diletakkan di akhir agar pasti terbaca -->
        <script>
        // Fungsi utama buka/tutup sidebar
        function toggleSidebar() {
            const sidebar = document.getElementById('sidebar');
            const tombolBuka = document.getElementById('tombolBuka');
            
            if (sidebar.classList.contains('tertutup')) {
                sidebar.classList.remove('tertutup');
                tombolBuka.classList.add('hidden');
            } else {
                sidebar.classList.add('tertutup');
                tombolBuka.classList.remove('hidden');
            }
        }

        async function kirimPesan() {
            const input = document.getElementById('pesan');
            const teks = input.value.trim();
            if(!teks) return;
            input.value = '';
            tampilkanPesan('Anda', teks);
            tampilkanPesan('Alina', 'Sedang memproses...');

            try {
                const res = await fetch('/api/tanya', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({pesan: teks})
                });
                const data = await res.json();
                gantiPesanTerakhir('Alina', data.jawaban);
                muatRiwayat();
            } catch(e) {
                gantiPesanTerakhir('Alina', '❌ Terjadi kesalahan. Silakan coba lagi.');
            }
        }

        function tampilkanPesan(pengirim, teks) {
            const kotak = document.getElementById('kontenChat');
            const balon = document.createElement('div');
            balon.className = `p-3 rounded-lg max-w-[88%] whitespace-pre-wrap ${pengirim==='Anda' ? 'bg-blue-100 ml-auto text-gray-800' : 'bg-white border border-gray-200 text-gray-800'}`;
            balon.innerHTML = `<b>${pengirim}:</b><br>${teks.replace(/\n/g, '<br>')}`;
            kotak.appendChild(balon);
            kotak.scrollTop = kotak.scrollHeight;
        }

        function gantiPesanTerakhir(pengirim, teks) {
            const kotak = document.getElementById('kontenChat');
            const balon = kotak.lastChild;
            balon.innerHTML = `<b>${pengirim}:</b><br>${teks.replace(/\n/g, '<br>')}`;
        }

        async function muatRiwayat() {
            const res = await fetch('/api/riwayat');
            const data = await res.json();
            const kotak = document.getElementById('riwayat');
            if(data.length === 0) {
                kotak.innerHTML = '<p class="text-gray-400 text-center py-6">Belum ada riwayat</p>';
                return;
            }
            kotak.innerHTML = '';
            data.reverse().forEach(item => {
                const el = document.createElement('div');
                el.className = 'p-2 border border-gray-200 rounded hover:bg-gray-100 cursor-pointer transition text-gray-700';
                el.innerHTML = `<small class="text-gray-400">${item.waktu}</small><br><b>Tanya:</b> ${item.tanya.slice(0,45)}...`;
                el.onclick = () => tampilkanLengkap(item);
                kotak.appendChild(el);
            });
        }

        function tampilkanLengkap(item) {
            document.getElementById('kontenChat').innerHTML = '';
            tampilkanPesan('Anda', item.tanya);
            tampilkanPesan('Alina', item.jawab);
        }

        async function resetSemua() {
            await fetch('/api/tanya', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({pesan: "reset"})});
            document.getElementById('kontenChat').innerHTML = '';
            muatRiwayat();
        }

        // Muat riwayat saat halaman terbuka
        window.onload = muatRiwayat;
        </script>
    </body>
    </html>
    """

@app.get("/api/riwayat")
def dapatkan_riwayat():
    return RIWAYAT_OBROLAN

class PesanMasuk(BaseModel):
    pesan: str

@app.post("/api/tanya")
async def tanya_alina(data: PesanMasuk, request: Request):
    ip_pengguna = request.client.host
    jawaban = dapatkan_jawaban(data.pesan, ip_pengguna)
    return {"jawaban": jawaban}

import threading
def tugas_rutin():
    while True:
        try:
            simpan_cadangan_konfigurasi()
            cek_dan_pindah_supabase()
            cek_dan_bersihkan_b2()
            logger.info("✅ Tugas rutin selesai")
        except Exception as e:
            logger.error(f"❌ Tugas rutin gagal: {e}")
        time.sleep(6 * 3600)

threading.Thread(target=tugas_rutin, daemon=True).start()

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
