from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import os
import logging
import requests
import json
import base64
import time
import uuid
from datetime import datetime, timedelta
from supabase import create_client, Client
import boto3
from botocore.config import Config
from typing import Optional, List, Dict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Alina AI", version="2.1.0")

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
1. Jawab selalu dalam bahasa Indonesia atau bahasa apapun yang digunakan oleh pengguna dengan lembut, sopan, jelas, dan mudah dimengerti.
2. Jika ditanya siapa Anda atau pembuatnya, jawab: "Saya Alina AI, kecerdasan buatan yang dikembangkan di Indonesia oleh Tim Alina."
3. Jawab semua pertanyaan dengan lengkap, akurat dan sesuai pengetahuan terbaru.
4. Jika tidak ada hasil pencarian, jawab berdasarkan data yang kamu miliki.
5. Tawarkan sesuatu tentang topik yang sedang dibahas oleh pengguna sebagai penutup jawaban yang kamu berikan.
"""

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
HUGGINGFACE_API_KEY = os.getenv("HUGGINGFACE_API_KEY", "")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "alina-sementara")
SUPABASE_MAX_MB = 40
SUPABASE_PINDAH_MB = 15
B2_KEY_ID = os.getenv("B2_KEY_ID", "")
B2_APPLICATION_KEY = os.getenv("B2_APPLICATION_KEY", "")
B2_ENDPOINT = os.getenv("B2_ENDPOINT", "s3.us-east-005.backblazeb2.com")
B2_BUCKET = os.getenv("B2_BUCKET", "alina-utama")
B2_MAX_MB = 9 * 1024
B2_SISA_MB = 8 * 1024

model_gemini = None
client_gemini = None
if GEMINI_API_KEY:
    try:
        import google.genai as genai
        client_gemini = genai.Client(api_key=GEMINI_API_KEY)
        model_gemini = "gemini-2.0-flash-lite"
        logger.info("✅ Gemini berhasil dimuat")
    except Exception as e:
        logger.warning(f"⚠️ Gemini tidak dapat dimuat: {str(e)}")

client_groq = None
if GROQ_API_KEY:
    try:
        from groq import Groq
        client_groq = Groq(api_key=GROQ_API_KEY)
        logger.info("✅ Groq berhasil dimuat")
    except Exception as e:
        logger.warning(f"⚠️ Groq tidak dapat dimuat: {str(e)}")

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

def ukuran_ke_mb(byte: int) -> float:
    return round(byte / (1024 * 1024), 2)

def buat_nama_file(deskripsi: str) -> str:
    waktu = datetime.now().strftime("%Y%m%d-%H%M%S")
    id_unik = str(uuid.uuid4())[:8]
    return f"{waktu}_{id_unik}.jpg"

def dapatkan_info_supabase() -> tuple[float, List[Dict]]:
    if not supabase:
        return 0.0, []
    try:
        daftar = supabase.storage.from_(SUPABASE_BUCKET).list()
        total_byte = sum(f["metadata"].get("size", 0) for f in daftar if "metadata" in f)
        daftar_urut = sorted(daftar, key=lambda x: x.get("created_at", ""))
        return ukuran_ke_mb(total_byte), daftar_urut
    except Exception as e:
        logger.warning(f"⚠️ Gagal membaca Supabase: {e}")
        return 0.0, []

def pindah_ke_backblaze(file: Dict) -> bool:
    if not supabase or not b2:
        return False
    try:
        nama = file["name"]
        data = supabase.storage.from_(SUPABASE_BUCKET).download(nama)
        b2.put_object(Bucket=B2_BUCKET, Key=f"arsip/{nama}", Body=data, ContentType="image/jpeg")
        supabase.storage.from_(SUPABASE_BUCKET).remove([nama])
        logger.info(f"✅ Pindah: {nama} → Backblaze")
        return True
    except Exception as e:
        logger.warning(f"⚠️ Gagal pindah {nama}: {e}")
        return False

def cek_dan_pindah_supabase() -> None:
    total, daftar = dapatkan_info_supabase()
    logger.info(f"ℹ️ Supabase: {total} MB / {SUPABASE_MAX_MB} MB")
    if total < SUPABASE_MAX_MB or not daftar:
        return
    logger.warning(f"⚠️ Supabase penuh! Pindahkan {SUPABASE_PINDAH_MB} MB file terlama...")
    terpindah = 0.0
    for f in daftar:
        if terpindah >= SUPABASE_PINDAH_MB:
            break
        ukuran = ukuran_ke_mb(f["metadata"].get("size", 0))
        if pindah_ke_backblaze(f):
            terpindah += ukuran
    logger.info(f"✅ Selesai pindah total {terpindah:.2f} MB")

def dapatkan_info_b2() -> tuple[float, List[Dict]]:
    if not b2:
        return 0.0, []
    try:
        res = b2.list_objects_v2(Bucket=B2_BUCKET, Prefix="arsip/")
        daftar = res.get("Contents", [])
        total_byte = sum(obj["Size"] for obj in daftar)
        daftar_urut = sorted(daftar, key=lambda x: x["LastModified"])
        return ukuran_ke_mb(total_byte), daftar_urut
    except Exception as e:
        logger.warning(f"⚠️ Gagal membaca Backblaze: {e}")
        return 0.0, []

def cek_dan_bersihkan_b2() -> None:
    total, daftar = dapatkan_info_b2()
    logger.info(f"ℹ️ Backblaze: {total} MB / {B2_MAX_MB} MB")
    if total < B2_MAX_MB or not daftar:
        return
    logger.warning(f"⚠️ Backblaze penuh! Hapus file terlama hingga tersisa {B2_SISA_MB} MB...")
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
        nama_file = buat_nama_file(deskripsi)

        if supabase:
            try:
                supabase.storage.from_(SUPABASE_BUCKET).upload(
                    path=nama_file,
                    file=res_gambar.content,
                    file_options={"content-type": "image/jpeg"}
                )
                logger.info(f"✅ Gambar disimpan ke Supabase: {nama_file}")
                cek_dan_pindah_supabase()
                url_tautan = supabase.storage.from_(SUPABASE_BUCKET).get_public_url(nama_file)
            except Exception as e:
                logger.warning(f"⚠️ Simpan ke Supabase gagal, coba simpan ke Backblaze: {e}")
                if b2:
                    b2.put_object(Bucket=B2_BUCKET, Key=f"langsung/{nama_file}", Body=res_gambar.content, ContentType="image/jpeg")
                    cek_dan_bersihkan_b2()
                    url_tautan = b2.generate_presigned_url(
                        "get_object",
                        Params={"Bucket": B2_BUCKET, "Key": f"langsung/{nama_file}"},
                        ExpiresIn=86400
                    )
                else:
                    raise Exception("Tidak ada penyimpanan yang tersedia")
        else:
            res_pendek = requests.get(f"https://tinyurl.com/api-create.php?url={url_panjang}", timeout=15)
            res_pendek.raise_for_status()
            url_tautan = res_pendek.text.strip()

        return f"""✅ Berikut gambar yang Anda minta:

🔗 {url_tautan}

*Klik tautan untuk melihat gambar ukuran penuh*"""

    except Exception as e:
        logger.warning(f"⚠️ Pembuatan gambar gagal: {str(e)}")
        return "❌ Maaf, fitur pembuatan gambar sedang dalam perbaikan. Silakan coba lagi nanti."

def cari_informasi(kueri: str) -> str | None:
    kueri_lengkap = f"{kueri} Indonesia terbaru"

    if SERPAPI_KEY and len(SERPAPI_KEY) > 10:
        try:
            res = requests.get(
                "https://serpapi.com/search",
                params={
                    "q": kueri_lengkap,
                    "api_key": SERPAPI_KEY,
                    "engine": "google",
                    "hl": "id",
                    "gl": "id",
                    "num": 3
                },
                timeout=20
            )
            res.raise_for_status()
            data = res.json()

            if "answer_box" in data and data["answer_box"].get("snippet"):
                return f"🔍 **Informasi Terbaru:**\n\n{data['answer_box']['snippet']}\n\n🔗 Sumber: {data['answer_box'].get('link', 'Tidak tersedia')}"

            if "organic_results" in data and len(data["organic_results"]) > 0:
                hasil = "🔍 **Informasi Terbaru:**\n\n"
                for idx, item in enumerate(data["organic_results"][:2], 1):
                    hasil += f"{idx}. **{item.get('title','')}**\n{item.get('snippet','')}\n🔗 {item.get('link','')}\n\n"
                return hasil

        except Exception as e:
            logger.warning(f"⚠️ SerpApi gagal: {str(e)}")

    try:
        res = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": kueri_lengkap, "format": "json", "no_html": 1, "no_redirect": 1, "kl": "id-id"},
            timeout=15
        )
        res.raise_for_status()
        data = res.json()
        if data.get("AbstractText"):
            return f"🔍 **Informasi:**\n\n{data['AbstractText']}\n\n🔗 Sumber: {data.get('AbstractURL', 'Tidak tersedia')}"
    except Exception as e:
        logger.warning(f"⚠️ DuckDuckGo gagal: {str(e)}")

    logger.info("⚠️ Tidak ada hasil pencarian, lanjut ke jawaban AI")
    return None

def dapatkan_jawaban(pertanyaan: str) -> str:
    teks = pertanyaan.strip().lower()

    if teks.startswith(("buat gambar", "gambarkan", "bikin gambar", "buatkan gambar", "gambar", "lukis")):
        return buat_gambar(pertanyaan)

    kata_kunci_cari = [
        "cari", "info terbaru", "berita", "data terbaru", "informasi",
        "siapa presiden", "presiden indonesia", "wakil presiden",
        "saat ini", "sekarang", "hari ini", "bulan ini", "tahun ini",
        "berapa harga", "kurs", "cuaca", "statistik", "jumlah"
    ]

    butuh_cari = any(kata in teks for kata in kata_kunci_cari)
    if butuh_cari:
        hasil_cari = cari_informasi(pertanyaan)
        if hasil_cari:
            return hasil_cari

    pesan_lengkap = f"{INSTRUKSI_SISTEM}\n\nPertanyaan: {pertanyaan}"

    if OPENROUTER_API_KEY:
        try:
            res = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "HTTP-Referer": "https://alina.id",
                    "X-Title": "Alina AI",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "google/gemini-2.0-flash-lite-preview-02-05:free",
                    "messages": [{"role": "user", "content": pesan_lengkap}],
                    "max_tokens": 1024
                },
                timeout=25
            )
            res.raise_for_status()
            return res.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning(f"⚠️ OpenRouter gagal: {str(e)}")

    if client_groq:
        try:
            res = client_groq.chat.completions.create(
                model="llama-3.1-8b-instant", 
                messages=[{"role": "user", "content": pesan_lengkap}],
                timeout=20
            )
            return res.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"⚠️ Groq gagal: {str(e)}")

    if client_gemini and model_gemini:
        try:
            res = client_gemini.models.generate_content(model=model_gemini, contents=pesan_lengkap)
            if res.text:
                return res.text.strip()
        except Exception as e:
            logger.warning(f"⚠️ Gemini gagal: {str(e)}")

    if MISTRAL_API_KEY:
        try:
            res = requests.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {MISTRAL_API_KEY}"},
                json={"model": "mistral-small-latest", "messages": [{"role": "user", "content": pesan_lengkap}], "max_tokens": 1024},
                timeout=25
            )
            res.raise_for_status()
            return res.json()["choices"][0]["message"].strip()
        except Exception as e:
            logger.warning(f"⚠️ Mistral gagal: {str(e)}")

    return "❌ Maaf, saat ini tidak dapat memproses permintaan. Silakan coba lagi nanti."

class PesanMasuk(BaseModel):
    pesan: str

@app.get("/")
def halaman_utama():
    return FileResponse("static/index.html")

@app.post("/api/tanya")
async def tanya_alina(data: PesanMasuk):
    jawaban = dapatkan_jawaban(data.pesan)
    return {"jawaban": jawaban}

import threading
def jadwal_pengecekan():
    while True:
        try:
            cek_dan_pindah_supabase()
            cek_dan_bersihkan_b2()
            logger.info("✅ Pengecekan terjadwal selesai")
        except Exception as e:
            logger.warning(f"⚠️ Pengecekan gagal: {e}")
        time.sleep(6 * 3600)

threading.Thread(target=jadwal_pengecekan, daemon=True).start()

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
