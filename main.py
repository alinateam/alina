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

app = FastAPI(title="Alina AI", version="1.4.6")

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
1. Jawab selalu dalam bahasa Indonesia yang sopan, jelas, dan mudah dimengerti.
2. Jika ditanya siapa Anda atau pembuatnya, jawab: "Saya Alina AI, asisten cerdas yang dikembangkan di Indonesia oleh Tim Alina."
3. Jawab dengan lengkap dan akurat sesuai pengetahuan terbaru.
4. Jika tidak ada hasil pencarian, jawab berdasarkan data yang kamu miliki.
5. Tawarkan sesuatu tentang topik yang sedang dibahas oleh pengguna sebagai penutup jawaban yang kamu berikan.
"""

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")

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

def buat_gambar(deskripsi: str) -> str:
    if not OPENROUTER_API_KEY or len(OPENROUTER_API_KEY) < 10:
        return "❌ Fitur pembuatan gambar belum dikonfigurasi. Silakan masukkan kunci API OpenRouter terlebih dahulu."

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
                "prompt": f"{deskripsi}, kualitas tinggi, tajam, warna cerah, resolusi tinggi, tidak ada cacat",
                "n": 1,
                "size": "1024x1024",
                "response_format": "url"
            },
            timeout=45
        )
        res.raise_for_status()
        data = res.json()

        if "data" in data and len(data["data"]) > 0 and "url" in data["data"][0]:
            url_gambar = data["data"][0]["url"]
            return f"✅ Berikut gambar yang Anda minta:\n\n![Gambar Hasil Buatan Alina]({url_gambar})\n\n*Klik gambar untuk melihat ukuran penuh*"
        else:
            logger.warning(f"⚠️ Hasil pembuatan gambar tidak valid: {data}")
            return "❌ Berhasil diproses, tapi gambar tidak dapat diambil. Silakan coba deskripsi lain."

    except requests.exceptions.HTTPError as e:
        logger.warning(f"⚠️ Kesalahan API OpenRouter: {str(e)}")
        if res.status_code == 401:
            return "❌ Kunci API OpenRouter tidak valid atau sudah kadaluarsa."
        elif res.status_code == 402:
            return "❌ Kuota penggunaan OpenRouter sudah habis."
        else:
            return "❌ Layanan pembuatan gambar sedang bermasalah. Silakan coba lagi nanti."

    except Exception as e:
        logger.warning(f"❌ Gagal membuat gambar: {str(e)}")
        return "❌ Maaf, saya tidak dapat membuat gambar saat ini. Silakan coba deskripsi yang lebih sederhana."

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

    if teks.startswith(("buat gambar", "gambarkan", "bikin gambar")):
        return buat_gambar(pertanyaan)

    kata_kunci_cari = [
        "cari", "info terbaru", "berita", "data terbaru",
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
                model="llama3-8b-32768",  # ✅ Model baru yang aktif
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
            return res.json()["choices"][0]["message"]["content"].strip()
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

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
