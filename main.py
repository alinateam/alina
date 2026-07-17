from fastapi import FastAPI, Request, HTTPException, status, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.security import OAuth2AuthorizationCodeBearer
from pydantic import BaseModel
import os
import logging
import requests
import time
import uuid
import psutil
import httpx
from jose import JWTError, jwt
from datetime import datetime, timedelta
from collections import defaultdict
from supabase import create_client, Client
import boto3
from botocore.config import Config
from typing import Optional, List, Dict
from sqlalchemy import create_engine, Column, String, DateTime, Text, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Alina AI",
    version="2.5.1",
    docs_url=None,
    redoc_url=None
)

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

@app.exception_handler(404)
async def halaman_tidak_ditemukan(request: Request, exc):
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"pesan": "Halaman tidak ditemukan"}
    )

INSTRUKSI_SISTEM = f"""
Anda adalah Alina AI, asisten cerdas yang dikembangkan di Indonesia oleh Tim Alina.
Tanggal saat ini: {datetime.now().strftime('%d %B %Y')}.
Aturan wajib:
1. Jawab selalu dalam bahasa Indonesia atau bahasa yang digunakan pengguna dengan lembut, sopan, jelas, dan mudah dimengerti.
2. Jika ditanya siapa Anda atau pembuatnya, jawab: "Saya Alina AI, kecerdasan buatan yang dikembangkan di Indonesia oleh Tim Alina."
3. Jawab lengkap, akurat dan sesuai konteks percakapan sebelumnya.
4. Jika tidak tahu jawaban, katakan dengan jujur.
5. Jika diminta merangkum, berikan ringkasan yang padat, jelas, dan mencakup semua poin penting.
6. Tawarkan informasi tambahan yang relevan sebagai penutup jawaban.
"""

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
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

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/callback").strip()
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
SECRET_KEY = os.getenv("SECRET_KEY", str(uuid.uuid4()))
ALGORITMA_JWT = "HS256"
MASA_BERLAKU_SESI = 7

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
engine = None
SessionLocal = None
Base = declarative_base()
DB_AKTIF = False

class PenggunaDB(Base):
    __tablename__ = "pengguna"
    google_id = Column(String, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    nama = Column(String)
    dibuat_pada = Column(DateTime, default=datetime.utcnow)
    terakhir_masuk = Column(DateTime, default=datetime.utcnow)

class RiwayatDB(Base):
    __tablename__ = "riwayat_percakapan"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    google_id = Column(String, ForeignKey("pengguna.google_id", ondelete="CASCADE"))
    waktu = Column(DateTime, default=datetime.utcnow)
    tanya = Column(Text, nullable=False)
    jawab = Column(Text, nullable=False)

if DATABASE_URL:
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    try:
        engine = create_engine(
            DATABASE_URL,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 10}
        )
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        Base.metadata.create_all(bind=engine)
        DB_AKTIF = True
        logger.info("✅ Database PostgreSQL terhubung & tabel siap")
    except Exception as e:
        logger.warning(f"⚠️ Gagal terhubung ke database: {e}")
        logger.info("ℹ️ Fitur login & riwayat akan dinonaktifkan sementara")
else:
    logger.info("ℹ️ Variabel DATABASE_URL belum diisi, fitur login dinonaktifkan")

def get_db():
    if not DB_AKTIF or not SessionLocal:
        yield None
        return

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

oauth2_scheme = OAuth2AuthorizationCodeBearer(
    authorizationUrl="https://accounts.google.com/o/oauth2/v2/auth",
    tokenUrl="https://oauth2.googleapis.com/token",
    auto_error=False
)

async def dapatkan_pengguna_saat_ini(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    if not DB_AKTIF:
        raise HTTPException(status_code=503, detail="Sistem login belum dikonfigurasi")
    kredensial_salah = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Tidak dapat memverifikasi kredensial",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise kredensial_salah
    try:
        muatan = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITMA_JWT])
        google_id: str = muatan.get("sub")
        if google_id is None:
            raise kredensial_salah
    except JWTError:
        raise kredensial_salah

    if not db:
        raise HTTPException(status_code=503, detail="Database tidak tersedia")
        
    pengguna = db.query(PenggunaDB).filter(PenggunaDB.google_id == google_id).first()
    if pengguna is None:
        raise kredensial_salah
    return pengguna

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
        supabase.storage.list_buckets()
        logger.info("✅ Supabase Storage terhubung dan siap")
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

PEMANTAUAN_AKSES: Dict[str, List[float]] = defaultdict(list)

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

CADANGAN_KONFIGURASI: Dict = {}
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

def catat_riwayat_pengguna(db: Session, google_id: str, pertanyaan: str, jawaban: str):
    if not DB_AKTIF or not db:
        return
    try:
        baru = RiwayatDB(google_id=google_id, tanya=pertanyaan, jawab=jawaban)
        db.add(baru)
        db.commit()
        db.refresh(baru)
    except Exception as e:
        logger.warning(f"⚠️ Gagal menyimpan riwayat: {e}")

def reset_konteks() -> str:
    return "✅ Konteks percakapan telah dihapus. Kita bisa mulai topik baru."

def dapatkan_info_supabase() -> tuple[float, List[Dict]]:
    if not supabase:
        logger.debug("ℹ️ Supabase tidak terhubung, dilewati")
        return 0.0, []
    try:
        daftar = supabase.storage.from_(SUPABASE_BUCKET).list()
        if not isinstance(daftar, list):
            raise ValueError(f"Respons tidak valid: {daftar}")

        daftar_valid = []
        for berkas in daftar:
            if isinstance(berkas, dict) and "name" in berkas:
                if berkas["name"] == ".emptyFolderPlaceholder":
                    continue
                ukuran = berkas.get("metadata", {}).get("size", 0)
                daftar_valid.append({
                    "name": berkas["name"],
                    "metadata": {"size": ukuran},
                    "created_at": berkas.get("created_at", "")
                })

        total_byte = sum(item["metadata"]["size"] for item in daftar_valid)
        daftar_urut = sorted(daftar_valid, key=lambda x: x.get("created_at", ""))
        logger.info(f"📊 Supabase {SUPABASE_BUCKET}: {ukuran_ke_mb(total_byte)} MB | File: {len(daftar_valid)}")
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
            except Exception as e:
                logger.warning(f"⚠️ Simpan ke Supabase gagal, pindah ke Backblaze: {e}")
                if b2:
                    b2.put_object(
                        Bucket=B2_BUCKET,
                        Key=f"langsung/{nama_file}",
                        Body=res_gambar.content,
                        ContentType="image/jpeg"
                    )
                    cek_dan_bersihkan_b2()
                    url_tautan = b2.generate_presigned_url(
                        "get_object",
                        Params={"Bucket": B2_BUCKET, "Key": f"langsung/{nama_file}"},
                        ExpiresIn=TAUTAN_BERLAKU_DETIK
                    )
        elif b2:
            b2.put_object(
                Bucket=B2_BUCKET,
                Key=f"langsung/{nama_file}",
                Body=res_gambar.content,
                ContentType="image/jpeg"
            )
            cek_dan_bersihkan_b2()
            url_tautan = b2.generate_presigned_url(
                "get_object",
                Params={"Bucket": B2_BUCKET, "Key": f"langsung/{nama_file}"},
                ExpiresIn=TAUTAN_BERLAKU_DETIK
            )
        else:
            url_tautan = url_panjang
            logger.info("ℹ️ Tidak ada penyimpanan aktif, menggunakan tautan langsung dari sumber")

        return f"""✅ Berikut gambar yang Anda minta:

🔗 {url_tautan}

*Tautan berlaku selama 7 hari*"""

    except Exception as e:
        logger.error(f"❌ Gambar gagal dibuat: {e}")
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
                    messages=[{"role": "system", "content": pesan_sistem}, {"role": "user", "content": pertanyaan}],
                    max_tokens=1024,
                    temperature=0.7
                )
                return res.choices[0].message.content.strip()

            elif model["nama"] == "Gemini":
                pesan_tergabung = f"{pesan_sistem}\n\nPertanyaan: {pertanyaan}"
                res = model["client"].models.generate_content(model=model["model"], contents=pesan_tergabung)
                return res.text.strip()

            elif model["nama"] == "OpenRouter":
                res = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "HTTP-Referer": "https://alina.id", "X-Title": "Alina AI", "Content-Type": "application/json"},
                    json={"model": model["model"], "messages": [{"role": "system", "content": pesan_sistem}, {"role": "user", "content": pertanyaan}], "max_tokens": 1024},
                    timeout=25
                )
                res.raise_for_status()
                return res.json()["choices"][0]["message"]["content"].strip()

            elif model["nama"] == "Mistral":
                res = requests.post(
                    "https://api.mistral.ai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {MISTRAL_API_KEY}"},
                    json={"model": model["model"], "messages": [{"role": "system", "content": pesan_sistem}, {"role": "user", "content": pertanyaan}], "max_tokens": 1024},
                    timeout=25
                )
                res.raise_for_status()
                return res.json()["choices"][0]["message"].strip()

        except Exception as e:
            logger.warning(f"⚠️ Model {model['nama']} gagal: {e}")
            continue

    return "❌ Maaf, semua model sedang sibuk atau tidak tersedia. Silakan coba lagi nanti."

@app.get("/auth/google")
def masuk_dengan_google():
    if not GOOGLE_CLIENT_ID or not GOOGLE_REDIRECT_URI:
        return JSONResponse(status_code=500, content={"pesan": "Konfigurasi login belum lengkap"})
    return RedirectResponse(
        f"https://accounts.google.com/o/oauth2/v2/auth?"
        f"client_id={GOOGLE_CLIENT_ID}&"
        f"redirect_uri={GOOGLE_REDIRECT_URI}&"
        f"response_type=code&"
        f"scope=openid%20email%20profile&"
        f"access_type=offline&"
        f"prompt=consent",
        status_code=302
    )

@app.get("/auth/callback")
async def proses_callback_google(code: str, db: Session = Depends(get_db)):
    if not DB_AKTIF:
        return JSONResponse(status_code=503, content={"pesan": "Sistem login belum dikonfigurasi"})
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return JSONResponse(status_code=500, content={"pesan": "Konfigurasi login belum lengkap"})

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            res_token = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "redirect_uri": GOOGLE_REDIRECT_URI,
                    "grant_type": "authorization_code"
                }
            )
            res_token.raise_for_status()
            data_token = res_token.json()

            res_profil = await client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {data_token['access_token']}"}
            )
            res_profil.raise_for_status()
            data_profil = res_profil.json()

        google_id = data_profil["sub"]
        email = data_profil["email"]
        nama = data_profil.get("name", "Pengguna Alina")

        if not db:
            return JSONResponse(status_code=503, content={"pesan": "Database tidak tersedia, gagal menyimpan data akun"})

        pengguna = db.query(PenggunaDB).filter(PenggunaDB.google_id == google_id).first()
        if not pengguna:
            pengguna = PenggunaDB(google_id=google_id, email=email, nama=nama)
            db.add(pengguna)
            db.commit()
            db.refresh(pengguna)
            logger.info(f"✅ Akun baru dibuat: {email}")
        else:
            pengguna.terakhir_masuk = datetime.utcnow()
            db.commit()
            logger.info(f"✅ Pengguna masuk: {email}")

        token_sesi = jwt.encode(
            {"sub": google_id, "exp": datetime.utcnow() + timedelta(days=MASA_BERLAKU_SESI)},
            SECRET_KEY,
            algorithm=ALGORITMA_JWT
        )

        return JSONResponse(
            content={
                "pesan": "Masuk berhasil",
                "nama": nama,
                "email": email,
                "token": token_sesi
            }
        )

    except Exception as e:
        logger.error(f"❌ Gagal proses login: {e}")
        return JSONResponse(status_code=400, content={"pesan": "Gagal masuk, coba lagi nanti"})

@app.get("/")
def halaman_utama():
    return FileResponse("static/index.html", media_type="text/html")

@app.get("/api/riwayat")
def dapatkan_riwayat(pengguna: PenggunaDB = Depends(dapatkan_pengguna_saat_ini), db: Session = Depends(get_db)):
    if not db:
        raise HTTPException(status_code=503, detail="Riwayat tidak tersedia karena database belum terhubung")
    riwayat = db.query(RiwayatDB).filter(RiwayatDB.google_id == pengguna.google_id).order_by(RiwayatDB.waktu.desc()).limit(50).all()
    return [
        {
            "waktu": r.waktu.strftime("%d/%m/%Y %H:%M"),
            "tanya": r.tanya,
            "jawab": r.jawab
        } for r in riwayat
    ]

class PesanMasuk(BaseModel):
    pesan: str

@app.post("/api/tanya")
async def tanya_alina(
    data: PesanMasuk,
    request: Request,
    pengguna: Optional[PenggunaDB] = Depends(dapatkan_pengguna_saat_ini),
    db: Optional[Session] = Depends(get_db)
):
    ip_pengguna = request.client.host
    teks = data.pesan.strip().lower()

    if not cek_batasan_akses(ip_pengguna):
        return {"jawaban": "⚠️ Terlalu banyak permintaan! Mohon tunggu sebentar sebelum mencoba lagi."}

    if teks in ["reset", "hapus konteks", "mulai baru"]:
        return {"jawaban": reset_konteks()}
    if teks == "status server":
        return {"jawaban": f"📊 **Status Server:**\n\n" + "\n".join([f"• {k}: {v}" for k, v in dapatkan_status_server().items()])}
    if teks == "lihat cadangan":
        return {"jawaban": f"💾 **Cadangan Konfigurasi:**\n\n" + "\n".join([f"• {k}: {v}" for k, v in CADANGAN_KONFIGURASI.items()])}

    if teks.startswith(("buat gambar", "gambarkan", "bikin gambar", "gambar", "lukis")):
        hasil = buat_gambar(data.pesan)
        if DB_AKTIF and pengguna and db:
            catat_riwayat_pengguna(db, pengguna.google_id, data.pesan, hasil)
        return {"jawaban": hasil}

    if teks.startswith(("rangkum", "ringkas", "buat ringkasan", "rangkumkan")):
        hasil = buat_rangkuman(data.pesan.split(" ", 1)[1])
        if DB_AKTIF and pengguna and db:
            catat_riwayat_pengguna(db, pengguna.google_id, data.pesan, hasil)
        return {"jawaban": hasil}

    kata_cari = ["cari", "info terbaru", "berita", "data terbaru", "saat ini", "sekarang", "hari ini", "berapa harga", "kurs", "cuaca"]
    if any(kata in teks for kata in kata_cari):
        hasil_cari = cari_informasi(data.pesan)
        if hasil_cari:
            if DB_AKTIF and pengguna and db:
                catat_riwayat_pengguna(db, pengguna.google_id, data.pesan, hasil_cari)
            return {"jawaban": hasil_cari}

    jawaban = tanya_model(data.pesan)
    if DB_AKTIF and pengguna and db:
        catat_riwayat_pengguna(db, pengguna.google_id, data.pesan, jawaban)
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
