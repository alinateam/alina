from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import os
import logging
import requests
import json
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Alina AI", version="1.4.5")

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
Tanggal saat ini adalah {datetime.now().strftime('%d %B %Y')}.
Aturan wajib:
1. Jawab selalu dalam bahasa Indonesia yang sopan, lembut, dan mudah dimengerti.
2. Jika ditanya siapa Anda atau pembuatnya, jawab: "Saya Alina AI, asisten cerdas yang dikembangkan di Indonesia oleh Tim Alina."
3. Jika pertanyaan membutuhkan data terbaru, berita, atau keadaan saat ini, gunakan hasil pencarian yang disediakan.
4. Jawab secara lengkap dan bermanfaat.
"""

# Kunci API
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")

# ==========================================
# Inisialisasi Model
# ==========================================
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

# ==========================================
# FUNGSI: MEMBUAT GAMBAR
# ==========================================
def buat_gambar(deskripsi: str) -> str:
    if not OPENROUTER_API_KEY:
        return "❌ Fitur pembuatan gambar belum dikonfigurasi."
    try:
        res = requests.post(
            "https://openrouter.ai/api/v1/images/generations",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "HTTP-Referer": "https://alina.id",
                "X-Title": "Alina AI",
                "Content-Type": "application/json"
            },
            json={
                "model": "stabilityai/stable-diffusion-xl-base-1.0",
                "prompt": deskripsi + ", kualitas tinggi, tajam, detail jelas",
                "n": 1,
                "size": "1024x1024"
            },
            timeout=30
        )
        res.raise_for_status()
        data = res.json()
        url_gambar = data["data"][0]["url"]
        return f"✅ Berikut gambar yang Anda minta:\n\n![Gambar]({url_gambar})"
    except Exception as e:
        logger.warning(f"❌ Gagal buat gambar: {str(e)}")
        return "❌ Maaf, tidak dapat membuat gambar saat ini."

# ==========================================
# FUNGSI: CARI INFORMASI TERBARU / BROWSING
# ==========================================
def cari_informasi(kueri: str) -> str:
    # Tambahkan konteks agar hasil lebih relevan
    kueri_lengkap = f"{kueri} Indonesia terbaru"

    # Opsi 1: Pakai SerpApi jika ada kuncinya (hasil paling lengkap & akurat)
    if SERPAPI_KEY and SERPAPI_KEY.strip():
        try:
            res = requests.get(
                "https://serpapi.com/search",
                params={
                    "q": kueri_lengkap,
                    "api_key": SERPAPI_KEY,
                    "engine": "google",
                    "hl": "id",
                    "gl": "id",
                    "num": 5
                },
                timeout=20
            )
            res.raise_for_status()
            data = res.json()

            hasil = "🔍 **Informasi Terbaru:**\n\n"

            # Ambil jawaban langsung jika ada
            if "answer_box" in data and data["answer_box"].get("snippet"):
                hasil += f"**Jawaban:**\n{data['answer_box']['snippet']}\n\n"
                if "link" in data["answer_box"]:
                    hasil += f"🔗 Sumber: {data['answer_box']['link']}\n\n"

            # Ambil hasil pencarian utama
            if "organic_results" in data and len(data["organic_results"]) > 0:
                if "Jawaban:" not in hasil:
                    hasil += "**Ringkasan Hasil:**\n"
                for idx, item in enumerate(data["organic_results"][:3], 1):
                    judul = item.get("title", "Tanpa Judul")
                    ringkasan = item.get("snippet", "Tidak ada ringkasan")
                    tautan = item.get("link", "#")
                    hasil += f"{idx}. **{judul}**\n{ringkasan}\n🔗 {tautan}\n\n"
                return hasil

            return "🔍 Pencarian selesai, berikut informasi yang ditemukan:\n\n" + hasil

        except Exception as e:
            logger.warning(f"⚠️ SerpApi gagal: {str(e)}")

    # Opsi 2: Cadangan - Pakai DuckDuckGo dengan format yang lebih baik
    try:
        res = requests.get(
            "https://api.duckduckgo.com/",
            params={
                "q": kueri_lengkap,
                "format": "json",
                "no_html": 1,
                "no_redirect": 1,
                "skip_disambig": 1,
                "kl": "id-id"
            },
            timeout=15
        )
        res.raise_for_status()
        data = res.json()

        hasil = "🔍 **Informasi yang Ditemukan:**\n\n"
        ditemukan = False

        # Ambil jawaban utama
        if data.get("AbstractText"):
            hasil += f"{data['AbstractText']}\n\n"
            if data.get("AbstractURL"):
                hasil += f"🔗 Sumber: {data['AbstractURL']}\n"
            ditemukan = True

        # Jika tidak ada jawaban utama, ambil topik terkait
        elif data.get("RelatedTopics") and len(data["RelatedTopics"]) > 0:
            hasil += "**Informasi Terkait:**\n"
            for idx, topik in enumerate(data["RelatedTopics"][:3], 1):
                if "Text" in topik:
                    hasil += f"{idx}. {topik['Text']}\n"
                    if "FirstURL" in topik:
                        hasil += f"🔗 {topik['FirstURL']}\n\n"
            ditemukan = True

        # Jika tetap tidak ada hasil, kembalikan ke model AI untuk menjawab berdasarkan pengetahuannya
        if ditemukan:
            return hasil
        else:
            logger.info("⚠️ Tidak ada hasil pencarian, gunakan jawaban bawaan AI")
            return None  # Mengembalikan None agar sistem beralih ke jawaban model

    except Exception as e:
        logger.warning(f"⚠️ DuckDuckGo gagal: {str(e)}")
        return None

# ==========================================
# FUNGSI UTAMA
# ==========================================
def dapatkan_jawaban(pertanyaan: str) -> str:
    teks = pertanyaan.strip().lower()

    # Deteksi perintah khusus
    if teks.startswith((
        "buat gambar", "gambarkan", "buatkan gambar", "tampilkan gambar",
        "bikin gambar", "gambar"
    )):
        return buat_gambar(pertanyaan)
    
    # Daftar kata kunci untuk memicu pencarian otomatis
    kata_kunci_cari = [
        "cari", "info terbaru", "berita", "jelaskan terbaru", "data terbaru",
        "siapa presiden", "kepala negara", "perdana menteri", "presiden indonesia",
        "berapa harga", "kurs", "nilai tukar", "cuaca", "suhu", "iklim",
        "berita hari ini", "update", "terkini", "saat ini", "sekarang",
        "hasil", "kemenangan", "perkembangan", "status", "jumlah", "statistik"
    ]

    # Cek apakah butuh pencarian
    butuh_cari = any(kata in teks for kata in kata_kunci_cari)
    if butuh_cari:
        return cari_informasi(pertanyaan)

    pesan_lengkap = f"{INSTRUKSI_SISTEM}\n\nPertanyaan: {pertanyaan}"

    # 1. OpenRouter
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
                    "max_tokens": 2048
                },
                timeout=25
            )
            res.raise_for_status()
            return res.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning(f"⚠️ OpenRouter gagal: {str(e)}")

    # 2. Groq
    if client_groq:
        try:
            res = client_groq.chat.completions.create(
                model="llama3-8b-32768",
                messages=[{"role": "user", "content": pesan_lengkap}],
                timeout=20
            )
            return res.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"⚠️ Groq gagal: {str(e)}")

    # 3. Gemini
    if client_gemini and model_gemini:
        try:
            res = client_gemini.models.generate_content(model=model_gemini, contents=pesan_lengkap)
            if res.text:
                return res.text.strip()
        except Exception as e:
            logger.warning(f"⚠️ Gemini gagal: {str(e)}")

    # 4. Mistral
    if MISTRAL_API_KEY:
        try:
            res = requests.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {MISTRAL_API_KEY}"},
                json={"model": "mistral-small-latest", "messages": [{"role": "user", "content": pesan_lengkap}], "max_tokens": 2048},
                timeout=25
            )
            res.raise_for_status()
            return res.json()["choices"][0]["message"].strip()
        except Exception as e:
            logger.warning(f"⚠️ Mistral gagal: {str(e)}")

    return "❌ Maaf, layanan sedang tidak tersedia saat ini."

class PesanMasuk(BaseModel):
    pesan: str

@app.get("/")
def halaman_utama():
    return FileResponse("static/index.html")

@app.post("/api/tanya")
async def tanya_alina(data: PesanMasuk):
    jawaban = dapatkan_jawaban(data.pesan)
    return {"jawaban": jawaban}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
