from fastapi import FastAPI, Request, HTTPException, File, UploadFile, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from jose import JWTError, jwt
from passlib.context import CryptContext
pwd_context = CryptContext(schemes=["sha256_crypt"], deprecated="auto")
from datetime import datetime, timedelta
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
import os
import base64
import requests
from bs4 import BeautifulSoup
import fitz
from PIL import Image
import io
import logging

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SECRET_KEY = os.getenv("SECRET_KEY", "1dfde8934a76b45f9aa8a7c69405fd71c135112a0c9aa05297fb413b5a4456f5")
ALGORITMA = "HS256"
WAKTU_TOKEN_BERLAKU = 7 * 24 * 60  # 7 hari

INSTRUKSI_SISTEM = """
Anda adalah Alina AI, asisten cerdas yang dikembangkan di Indonesia oleh Tim Alina.
Aturan wajib:
1. Jika diminta memperkenalkan diri atau ditanya siapa pengembang/pembuat/asalmu, jawab: "Saya Alina AI, asisten cerdas yang dikembangkan di Indonesia oleh Tim Alina."
2. Jangan pernah menyebutkan nama perusahaan luar, negara lain, atau model dasar yang digunakan.
3. Jawab dalam bahasa Indonesia yang jelas, lembut, sopan, dan mudah dimengerti.
4. Berikan jawaban yang akurat dan bermanfaat.
5. Tawarkan sesuatu yang dapat kamu berikan kepada pengguna tentang topik yang sedang dibahas sebagai penutup jawabanmu.
"""

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")

if not GEMINI_API_KEY:
    raise ValueError("⚠️ GEMINI_API_KEY wajib ada di berkas .env!")

from google import genai
from groq import Groq

client_gemini = genai.Client(api_key=GEMINI_API_KEY)
model_gemini = "gemini-2.5-flash"

client_groq = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
model_groq = "llama3-8b-8192"

model_mistral = "mistral-tiny"

def panggil_ai(teks_pengguna: str) -> str:
    pesan_lengkap = f"{INSTRUKSI_SISTEM}\n\nPertanyaan: {teks_pengguna}"

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
                timeout=20
            )
            res.raise_for_status()
            return res.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning(f"⚠️ OpenRouter gagal: {str(e)} | Lanjut ke Groq...")

    if client_groq:
        try:
            res = client_groq.chat.completions.create(
                model=model_groq,
                messages=[{"role": "user", "content": pesan_lengkap}],
                timeout=20
            )
            return res.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"⚠️ Groq gagal: {str(e)} | Lanjut ke Gemini...")

    try:
        res = client_gemini.models.generate_content(model=model_gemini, contents=pesan_lengkap)
        if res.text:
            return res.text.strip()
    except Exception as e:
        logger.warning(f"⚠️ Gemini gagal: {str(e)} | Lanjut ke Mistral...")

    if MISTRAL_API_KEY:
        try:
            res = requests.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {MISTRAL_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": model_mistral,
                    "messages": [{"role": "user", "content": pesan_lengkap}],
                    "max_tokens": 2048
                },
                timeout=20
            )
            res.raise_for_status()
            return res.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.error(f"⚠️ Mistral gagal: {str(e)}")

    return "❌ Maaf, layanan sedang tidak tersedia. Silakan coba lagi nanti."


def panggil_ai_gambar(pesan: str, data_gambar: bytes, mime_type: str) -> str:
    instruksi = f"{INSTRUKSI_SISTEM}\n\nPerintah: {pesan}"
    try:
        res = client_gemini.models.generate_content(
            model=model_gemini,
            contents=[
                instruksi,
                {"mime_type": mime_type, "data": base64.b64encode(data_gambar).decode("utf-8")}
            ]
        )
        return res.text.strip() if res.text else "Tidak ada jawaban yang dihasilkan."
    except Exception as e:
        logger.error(f"❌ Pemrosesan gambar gagal: {str(e)}")
        return "❌ Tidak dapat membaca atau memproses gambar saat ini."


from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
limiter = Limiter(key_func=get_remote_address)

from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from typing import Optional

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./alina.db")

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class PenggunaDB(Base):
    __tablename__ = "pengguna"

    id = Column(Integer, primary_key=True, index=True)
    nama_pengguna = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True)
    sandi_hash = Column(String, nullable=False)
    dibuat_pada = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

app = FastAPI(title="Alina AI", version="1.3.0", description="Asisten Cerdas Alina AI")
app.mount("/frontend", StaticFiles(directory="frontend", html=True), name="frontend")
app.mount("/src/assets", StaticFiles(directory="src/assets", html=False), name="assets")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://alina.id"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

MAKSIMAL_UKURAN_FILE = 4 * 1024 * 1024  # 4 MB
FORMAT_DIDUKUNG = [".jpg", ".jpeg", ".png", ".webp", ".pdf", ".txt"]

enkripsi_sandi = CryptContext(schemes=["sha256_crypt"], deprecated="auto")
skema_oauth = OAuth2PasswordBearer(tokenUrl="api/login")

class DataPengguna(BaseModel):
    nama_pengguna: str
    email: Optional[str] = None

class DataPenggunaLengkap(DataPengguna):
    sandi: str

class Token(BaseModel):
    akses_token: str
    tipe_token: str

class PesanMasuk(BaseModel):
    pesan: str

def verifikasi_sandi(sandi_masuk: str, sandi_simpan: str):
    return enkripsi_sandi.verify(sandi_masuk, sandi_simpan)

def buat_sandi_terenkripsi(sandi: str):
    return enkripsi_sandi.hash(sandi)

def buat_token_akses(data: dict, berlakunya: Optional[timedelta] = None):
    data_diubah = data.copy()
    if berlakunya:
        habis = datetime.utcnow() + berlakunya
    else:
        habis = datetime.utcnow() + timedelta(minutes=15)
    data_diubah.update({"exp": habis})
    return jwt.encode(data_diubah, SECRET_KEY, algorithm=ALGORITMA)

async def dapatkan_pengguna_saat_ini(
    token: str = Depends(skema_oauth), 
    db = Depends(get_db)
):
    kredensial_salah = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Tidak dapat memverifikasi kredensial",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        muatan = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITMA])
        nama_pengguna: str = muatan.get("sub")
        if nama_pengguna is None:
            raise kredensial_salah
    except JWTError:
        raise kredensial_salah

    pengguna = db.query(PenggunaDB).filter(PenggunaDB.nama_pengguna == nama_pengguna).first()
    if not pengguna:
        raise kredensial_salah
    
    return pengguna

@app.post("/api/daftar")
@limiter.limit("10/hour")
async def daftar(request: Request, akun: DataPenggunaLengkap, db = Depends(get_db)):
    cek_nama = db.query(PenggunaDB).filter(PenggunaDB.nama_pengguna == akun.nama_pengguna).first()
    if cek_nama:
        raise HTTPException(status_code=400, detail="Nama pengguna sudah dipakai")

    if akun.email:
        cek_email = db.query(PenggunaDB).filter(PenggunaDB.email == akun.email).first()
        if cek_email:
            raise HTTPException(status_code=400, detail="Email sudah terdaftar")

    pengguna_baru = PenggunaDB(
        nama_pengguna=akun.nama_pengguna,
        email=akun.email,
        sandi_hash=buat_sandi_terenkripsi(akun.sandi)
    )
    db.add(pengguna_baru)
    db.commit()
    db.refresh(pengguna_baru)

    logger.info(f"Akun baru dibuat: {akun.nama_pengguna}")
    return {"pesan": "Pendaftaran berhasil! Silakan masuk ke Alina AI."}

@app.post("/api/login", response_model=Token)
@limiter.limit("15/hour")
async def masuk(request: Request, form_data: OAuth2PasswordRequestForm = Depends(), db = Depends(get_db)):
    pengguna = db.query(PenggunaDB).filter(PenggunaDB.nama_pengguna == form_data.username).first()
    
    if not pengguna or not verifikasi_sandi(form_data.password, pengguna.sandi_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nama pengguna atau sandi salah",
        )
    
    waktu_berlaku = timedelta(minutes=WAKTU_TOKEN_BERLAKU)
    token_akses = buat_token_akses(
        data={"sub": pengguna.nama_pengguna}, berlakunya=waktu_berlaku
    )
    return {"akses_token": token_akses, "tipe_token": "bearer"}

@app.get("/")
async def halaman_utama():
    return FileResponse("static/index.html")

@app.post("/api/tanya")
async def tanya_alina(data: PesanMasuk):
    pesan = data.pesan.lower()
    if "halo" in pesan or "hai" in pesan:
        jawaban = "Halo! Senang bisa bertemu denganmu 😊 Ada yang bisa saya bantu?"
    elif "nama" in pesan:
        jawaban = "Nama saya Alina AI, asisten pintar yang siap membantu menjawab pertanyaan dan menemani ngobrol."
    elif "terima kasih" in pesan:
        jawaban = "Sama-sama! Senang bisa membantu. Kalau butuh bantuan lagi, panggil saja ya!"
    else:
        jawaban = f"Saya mengerti pesanmu: *{data.pesan}*\n\nSaat ini saya masih dalam tahap pengembangan, tapi nanti saya akan bisa menjawab lebih lengkap dan akurat!"
    return {"jawaban": jawaban}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)

@app.get("/api/profil")
@limiter.limit("60/minute")
async def profil(request: Request, akun = Depends(dapatkan_pengguna_saat_ini)):
    return {
        "nama_pengguna": akun.nama_pengguna,
        "email": akun.email,
        "dibuat_pada": akun.dibuat_pada.strftime("%d-%m-%Y")
    }

def baca_pdf(file_bytes):
    try:
        teks = ""
        with fitz.open(stream=file_bytes, filetype="pdf") as doc:
            batas = min(len(doc), 50)
            for halaman in doc[:batas]:
                teks += halaman.get_text()
        return teks[:10000]
    except Exception as e:
        logger.error(f"Gagal baca PDF: {str(e)}")
        raise ValueError("File PDF rusak atau tidak dapat dibaca.")

def proses_gambar(file_bytes):
    try:
        img = Image.open(io.BytesIO(file_bytes))
        if img.width > 1200 or img.height > 1200:
            img.thumbnail((1200, 1200))
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=85)
            return buffer.getvalue()
        return file_bytes
    except Exception as e:
        logger.error(f"Gagal proses gambar: {str(e)}")
        raise ValueError("Bukan file gambar yang valid.")

@app.post("/api/unggah")
@limiter.limit("20/minute")
async def proses_file(
    request: Request,
    file: UploadFile = File(...),
    pesan: str = "Jelaskan isi file ini dengan jelas dalam bahasa Indonesia atau bahasa yang digunakan oleh pengguna",
    akun = Depends(dapatkan_pengguna_saat_ini)
):
    try:
        konten = await file.read()
        if len(konten) > MAKSIMAL_UKURAN_FILE:
            raise HTTPException(status_code=413, detail=f"File terlalu besar. Maksimal {MAKSIMAL_UKURAN_FILE//1024//1024} MB.")

        nama = file.filename.lower()
        if not any(nama.endswith(ekst) for ekst in FORMAT_DIDUKUNG):
            raise HTTPException(status_code=400, detail=f"Format tidak didukung: {', '.join(FORMAT_DIDUKUNG)}")

        if nama.endswith(".pdf"):
            teks = baca_pdf(konten)
            konten_isi = f"{pesan}\n\n--- ISI DOKUMEN ---\n{teks}"
            hasil = panggil_ai(konten_isi)
            return {"jenis": "teks", "konten": hasil}

        elif nama.endswith(".txt"):
            teks = konten.decode("utf-8", errors="replace")
            konten_isi = f"{pesan}\n\n--- ISI FILE ---\n{teks}"
            hasil = panggil_ai(konten_isi)
            return {"jenis": "teks", "konten": hasil}

        elif nama.endswith((".jpg", ".jpeg", ".png", ".webp")):
            data_gambar = proses_gambar(konten)
            hasil = panggil_ai_gambar(pesan, data_gambar, file.content_type)
            return {"jenis": "teks", "konten": hasil}

        else:
            raise HTTPException(status_code=400, detail="File tidak dapat diproses.")

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Kesalahan unggah: {str(e)}")
        raise HTTPException(status_code=500, detail="Kesalahan server Alina AI.")

def cari_di_internet(pertanyaan):
    try:
        url = f"https://html.duckduckgo.com/html/?q={pertanyaan.replace(' ', '+')}"
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")
        hasil = []
        for blok in soup.select(".result__body")[:3]:
            judul = blok.select_one(".result__title")
            deskripsi = blok.select_one(".result__snippet")
            if judul and deskripsi:
                hasil.append(f"📌 {judul.get_text(strip=True)}\n{deskripsi.get_text(strip=True)}")
        return "\n\n".join(hasil) if hasil else ""
    except Exception as e:
        logger.warning(f"Gagal cari informasi: {str(e)}")
        return ""

@app.post("/api/obrolan")
@limiter.limit("30/minute")
async def obrolan(request: Request, data: PesanMasuk, akun = Depends(dapatkan_pengguna_saat_ini)):
    try:
        teks = data.pesan.strip()
        if not teks:
            raise HTTPException(status_code=400, detail="Pesan tidak boleh kosong.")

        kata_gambar = ["buat gambar", "gambarkan", "buatkan gambar", "ilustrasi", "edit gambar", "gambar", "foto"]
        if any(kata in teks.lower() for kata in kata_gambar):
            try:
                url = f"https://image.pollinations.ai/prompt/{teks.replace(' ', '%20')}?width=768&height=768&nologo=true&model=flux"
                res = requests.get(url, timeout=20)
                res.raise_for_status()
                b64 = f"data:image/png;base64,{base64.b64encode(res.content).decode('utf-8')}"
                return {"jenis": "gambar", "teks": "✅ Berikut hasil gambar dari Alina AI:", "konten": b64}
            except Exception as e:
                logger.error(f"Gagal membuat gambar: {str(e)}")
                return {"jenis": "teks", "konten": "❌ Maaf, Alina AI tidak bisa membuat gambar saat ini."}

        kata_cari = ["terbaru", "hari ini", "sekarang", "harga", "berita", "jadwal", "cuaca", "update"]
        if any(kata in teks.lower() for kata in kata_cari):
            info = cari_di_internet(teks)
            prompt = f"Jawab pertanyaan ini: {teks}" + (f"\n\nGunakan informasi berikut jika relevan: {info}" if info else "")
        else:
            prompt = teks

        hasil = panggil_ai(prompt)
        return {"jenis": "teks", "konten": hasil}

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Kesalahan obrolan: {str(e)}")
        raise HTTPException(status_code=500, detail="Kesalahan server Alina AI.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)