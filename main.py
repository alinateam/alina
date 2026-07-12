from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import os
import logging
import requests
import json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Alina AI", version="1.4.1")

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

INSTRUKSI_SISTEM = """
Anda adalah Alina AI, asisten cerdas yang dikembangkan di Indonesia oleh Tim Alina.
Aturan wajib:
1. Jawab selalu dalam bahasa Indonesia yang sopan, lembut, dan mudah dimengerti.
2. Jika ditanya siapa Anda atau pembuatnya, jawab: "Saya Alina AI, asisten cerdas yang dikembangkan di Indonesia oleh Tim Alina."
3. Jangan menyebutkan nama model atau layanan AI luar.
4. Jawab secara lengkap dan bermanfaat.
5. Jika diminta membuat gambar atau mencari informasi terbaru, laksanakan sesuai kemampuan yang tersedia.
"""

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")

model_gemini = None
if GEMINI_API_KEY:
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        model_gemini = genai.GenerativeModel("gemini-2.0-flash")
    except Exception as e:
        logger.warning(f"Gemini tidak dapat dimuat: {str(e)}")

client_groq = None
if GROQ_API_KEY:
    try:
        from groq import Groq
        client_groq = Groq(api_key=GROQ_API_KEY)
    except Exception as e:
        logger.warning(f"Groq tidak dapat dimuat: {str(e)}")

model_mistral = "mistral-tiny"

def buat_gambar(deskripsi: str) -> str:
    if not OPENROUTER_API_KEY:
        return "❌ Fitur gambar belum dikonfigurasi."
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
                "prompt": deskripsi + ", kualitas tinggi, tajam",
                "n": 1,
                "size": "1024x1024"
            },
            timeout=30
        )
        res.raise_for_status()
        data = res.json()
        url_gambar = data["data"][0]["url"]
        return f"✅ Berikut gambarnya:\n\n![Gambar]({url_gambar})"
    except Exception as e:
        logger.warning(f"Gagal buat gambar: {str(e)}")
        return "❌ Tidak dapat membuat gambar saat ini."

def cari_informasi(kueri: str) -> str:
    if SERPAPI_KEY:
        try:
            res = requests.get(
                "https://serpapi.com/search",
                params={"q": kueri, "api_key": SERPAPI_KEY, "engine": "google", "hl": "id", "num": 3},
                timeout=20
            )
            res.raise_for_status()
            data = res.json()
            hasil = "🔍 **Informasi Terbaru:**\n\n"
            if "organic_results" in data:
                for idx, item in enumerate(data["organic_results"][:3], 1):
                    hasil += f"{idx}. **{item.get('title','')}**\n{item.get('snippet','')}\nSumber: {item.get('link','')}\n\n"
                return hasil
        except Exception as e:
            logger.warning(f"SerpApi gagal: {str(e)}")

    try:
        res = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": kueri, "format": "json", "no_html": 1, "no_redirect": 1},
            timeout=15
        )
        res.raise_for_status()
        data = res.json()
        if data.get("AbstractText"):
            return f"🔍 **Informasi:**\n\n{data['AbstractText']}\n\nSumber: {data.get('AbstractURL', 'Tidak tersedia')}"
        else:
            return "🔍 Silakan perjelas pertanyaan Anda agar saya bisa cari informasi yang sesuai."
    except Exception as e:
        logger.warning(f"DuckDuckGo gagal: {str(e)}")
        return "❌ Tidak dapat mengakses informasi terbaru."

def dapatkan_jawaban(pertanyaan: str) -> str:
    teks = pertanyaan.strip().lower()

    if teks.startswith(("buat gambar", "gambarkan", "buatkan gambar", "tampilkan gambar")):
        return buat_gambar(pertanyaan)
    
    if teks.startswith(("cari", "info terbaru", "berita", "jelaskan terbaru", "data terbaru")):
        return cari_informasi(pertanyaan)

    pesan_lengkap = f"{INSTRUKSI_SISTEM}\n\nPertanyaan: {pertanyaan}"

    if OPENROUTER_API_KEY:
        try:
            res = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "HTTP-Referer": "https://alina.id",
                    "X-Title": "Alina AI"
                },
                json={
                    "model": "google/gemini-flash-1.5",
                    "messages": [{"role": "user", "content": pesan_lengkap}],
                    "max_tokens": 2048
                },
                timeout=25
            )
            res.raise_for_status()
            return res.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning(f"OpenRouter gagal: {str(e)}")

    if client_groq:
        try:
            res = client_groq.chat.completions.create(
                model="llama3-8b-8192",
                messages=[{"role": "user", "content": pesan_lengkap}],
                timeout=20
            )
            return res.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"Groq gagal: {str(e)}")

    if model_gemini:
        try:
            res = model_gemini.generate_content(pesan_lengkap)
            if res.text:
                return res.text.strip()
        except Exception as e:
            logger.warning(f"Gemini gagal: {str(e)}")

    if MISTRAL_API_KEY:
        try:
            res = requests.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {MISTRAL_API_KEY}"},
                json={"model": model_mistral, "messages": [{"role": "user", "content": pesan_lengkap}], "max_tokens": 2048},
                timeout=20
            )
            res.raise_for_status()
            return res.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning(f"Mistral gagal: {str(e)}")

    return "❌ Maaf, semua layanan sedang tidak tersedia. Silakan coba lagi nanti atau periksa konfigurasi kunci API."

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
