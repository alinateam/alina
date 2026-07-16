from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
import os
import logging
import requests
import time
import uuid
from datetime import datetime, timedelta
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

app = FastAPI(title="Alina AI", version="2.2.0")

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

RIWAYAT_OBROLAN: List[Dict] = []

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

def catat_riwayat(pertanyaan: str, jawaban: str):
    """Simpan riwayat obrolan"""
    RIWAYAT_OBROLAN.append({
        "waktu": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "tanya": pertanyaan,
        "jawab": jawaban
    })
    if len(RIWAYAT_OBROLAN) > 50:
        RIWAYAT_OBROLAN.pop(0)

def dapatkan_info_supabase() -> tuple[float, List[Dict]]:
    if not supabase:
        return 0.0, []
    try:
        daftar = supabase.storage.from_(SUPABASE_BUCKET).list()
        total_byte = sum(f["metadata"].get("size", 0) for f in daftar if "metadata" in f)
        daftar_urut = sorted(daftar, key=lambda x: x.get("created_at", ""))
        logger.info(f"📊 Supabase: {ukuran_ke_mb(total_byte)} MB terpakai")
        return ukuran_ke_mb(total_byte), daftar_urut
    except Exception as e:
        logger.error(f"❌ Gagal membaca Supabase: {e}")
        return 0.0, []

def pindah_ke_backblaze(file: Dict) -> bool:
    if not supabase or not b2:
        return False
    try:
        nama = file["name"]
        ukuran = ukuran_ke_mb(file["metadata"].get("size", 0))
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
    logger.warning(f"⚠️ Supabase hampir penuh, mulai pindah {SUPABASE_PINDAH_MB} MB file terlama")
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
        logger.info(f"📊 Backblaze: {ukuran_ke_mb(total_byte)} MB terpakai")
        return ukuran_ke_mb(total_byte), daftar_urut
    except Exception as e:
        logger.error(f"❌ Gagal membaca Backblaze: {e}")
        return 0.0, []

def cek_dan_bersihkan_b2() -> None:
    total, daftar = dapatkan_info_b2()
    if total < B2_MAX_MB or not daftar:
        return
    logger.warning(f"⚠️ Backblaze hampir penuh, hapus file terlama hingga tersisa {B2_SISA_MB} MB")
    for f in daftar:
        if total <= B2_SISA_MB:
            break
        b2.delete_object(Bucket=B2_BUCKET, Key=f["Key"])
        total -= ukuran_ke_mb(f["Size"])
        logger.info(f"🗑️ Hapus file lama: {f['Key']}")
    logger.info(f"✅ Selesai bersihkan, tersisa {total:.2f} MB")

def buat_gambar(deskripsi: str) -> str:
    try:
        prompt_lengkap = f"{deskripsi}, kualitas tinggi, tajam, warna cerah, resolusi 1024x1024, detail jelas, tidak ada cacat, gaya alami"
        url_panjang = f"https://image.pollinations.ai/prompt/{requests.utils.quote(prompt_lengkap)}?width=1024&height=1024&nologo=true&seed={os.urandom(4).hex()}"

        res_gambar = requests.get(url_panjang, timeout=25)
        res_gambar.raise_for_status()

        ukuran_file = ukuran_ke_mb(len(res_gambar.content))
        if ukuran_file > MAKS_UKURAN_FILE_MB:
            logger.warning(f"⚠️ Ukuran file terlalu besar: {ukuran_file} MB > {MAKS_UKURAN_FILE_MB} MB")
            return f"❌ Ukuran file terlalu besar ({ukuran_file:.1f} MB). Maksimal yang diizinkan {MAKS_UKURAN_FILE_MB} MB."

        nama_file = buat_nama_file(deskripsi)
        logger.info(f"📥 Menerima gambar: {nama_file} | Ukuran: {ukuran_file} MB")

        url_tautan = ""
        if supabase:
            try:
                supabase.storage.from_(SUPABASE_BUCKET).upload(
                    path=nama_file,
                    file=res_gambar.content,
                    file_options={"content-type": "image/jpeg"}
                )
                logger.info(f"✅ Berhasil disimpan ke Supabase: {nama_file}")
                cek_dan_pindah_supabase()
                url_tautan = supabase.storage.from_(SUPABASE_BUCKET).get_public_url(nama_file)
            except Exception as e:
                logger.warning(f"⚠️ Simpan ke Supabase gagal, coba Backblaze: {e}")
                if b2:
                    b2.put_object(Bucket=B2_BUCKET, Key=f"langsung/{nama_file}", Body=res_gambar.content, ContentType="image/jpeg")
                    cek_dan_bersihkan_b2()
                    url_tautan = b2.generate_presigned_url(
                        "get_object",
                        Params={"Bucket": B2_BUCKET, "Key": f"langsung/{nama_file}"},
                        ExpiresIn=TAUTAN_BERLAKU_DETIK
                    )
                else:
                    raise Exception("Tidak ada penyimpanan yang tersedia")
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
        logger.error(f"❌ Pembuatan gambar gagal: {str(e)}")
        return "❌ Maaf, fitur pembuatan gambar sedang dalam perbaikan. Silakan coba lagi nanti."

def cari_informasi(kueri: str) -> str | None:
    kueri_lengkap = f"{kueri} Indonesia terbaru"

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
                hasil = f"🔍 **Informasi Terbaru:**\n\n{data['answer_box']['snippet']}\n\n🔗 Sumber: {data['answer_box'].get('link', 'Tidak tersedia')}"
                catat_riwayat(kueri, hasil)
                return hasil
            if "organic_results" in data and len(data["organic_results"]) > 0:
                hasil = "🔍 **Informasi Terbaru:**\n\n"
                for idx, item in enumerate(data["organic_results"][:2], 1):
                    hasil += f"{idx}. **{item.get('title','')}**\n{item.get('snippet','')}\n🔗 {item.get('link','')}\n\n"
                catat_riwayat(kueri, hasil)
                return hasil
        except Exception as e:
            logger.warning(f"⚠️ SerpApi gagal: {str(e)}")

    try:
        res = requests.get("https://api.duckduckgo.com/", params={"q": kueri_lengkap, "format": "json", "no_html": 1, "no_redirect": 1, "kl": "id-id"}, timeout=15)
        res.raise_for_status()
        data = res.json()
        if data.get("AbstractText"):
            hasil = f"🔍 **Informasi:**\n\n{data['AbstractText']}\n\n🔗 Sumber: {data.get('AbstractURL', 'Tidak tersedia')}"
            catat_riwayat(kueri, hasil)
            return hasil
    except Exception as e:
        logger.warning(f"⚠️ DuckDuckGo gagal: {str(e)}")

    return None

def dapatkan_jawaban(pertanyaan: str) -> str:
    teks = pertanyaan.strip().lower()

    if teks.startswith(("buat gambar", "gambarkan", "bikin gambar", "buatkan gambar", "gambar", "lukis")):
        return buat_gambar(pertanyaan)

    kata_kunci_cari = ["cari", "info terbaru", "berita", "data terbaru", "informasi", "siapa presiden", "presiden indonesia", "saat ini", "sekarang", "hari ini", "berapa harga", "kurs", "cuaca"]
    butuh_cari = any(kata in teks for kata in kata_kunci_cari)
    if butuh_cari:
        hasil_cari = cari_informasi(pertanyaan)
        if hasil_cari:
            return hasil_cari

    pesan_lengkap = f"{INSTRUKSI_SISTEM}\n\nPertanyaan: {pertanyaan}"
    jawaban = "❌ Maaf, saat ini tidak dapat memproses permintaan."

    if OPENROUTER_API_KEY:
        try:
            res = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "HTTP-Referer": "https://alina.id", "X-Title": "Alina AI", "Content-Type": "application/json"},
                json={"model": "google/gemini-2.0-flash-lite-preview-02-05:free", "messages": [{"role": "user", "content": pesan_lengkap}], "max_tokens": 1024},
                timeout=25
            )
            res.raise_for_status()
            jawaban = res.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning(f"⚠️ OpenRouter gagal: {str(e)}")

    if jawaban.startswith("❌") and client_groq:
        try:
            res = client_groq.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "user", "content": pesan_lengkap}], timeout=20)
            jawaban = res.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"⚠️ Groq gagal: {str(e)}")

    if jawaban.startswith("❌") and client_gemini:
        try:
            res = client_gemini.models.generate_content(model=model_gemini, contents=pesan_lengkap)
            if res.text:
                jawaban = res.text.strip()
        except Exception as e:
            logger.warning(f"⚠️ Gemini gagal: {str(e)}")

    if jawaban.startswith("❌") and MISTRAL_API_KEY:
        try:
            res = requests.post("https://api.mistral.ai/v1/chat/completions", headers={"Authorization": f"Bearer {MISTRAL_API_KEY}"}, json={"model": "mistral-small-latest", "messages": [{"role": "user", "content": pesan_lengkap}], "max_tokens": 1024}, timeout=25)
            res.raise_for_status()
            jawaban = res.json()["choices"][0]["message"].strip()
        except Exception as e:
            logger.warning(f"⚠️ Mistral gagal: {str(e)}")

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
        <title>Alina AI</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <link href="https://cdn.jsdelivr.net/npm/font-awesome@4.7.0/css/font-awesome.min.css" rel="stylesheet">
    </head>
    <body class="bg-gray-100 h-screen flex">
        <!-- Sidebar Riwayat -->
        <div id="sidebar" class="w-80 bg-white shadow-lg transition-all duration-300 ease-in-out">
            <div class="p-4 border-b flex justify-between items-center">
                <h3 class="text-lg font-semibold text-gray-800">Riwayat Obrolan</h3>
                <button onclick="toggleSidebar()" class="text-gray-600 hover:text-gray-900">
                    <i class="fa fa-chevron-left fa-lg"></i>
                </button>
            </div>
            <div id="riwayat" class="p-3 overflow-y-auto h-[calc(100vh-70px)] text-sm space-y-3">
                <p class="text-gray-500 text-center">Belum ada riwayat</p>
            </div>
        </div>

        <!-- Konten Utama -->
        <div class="flex-1 flex flex-col">
            <div class="bg-white shadow p-3 flex items-center">
                <button id="tombolBuka" onclick="toggleSidebar()" class="mr-3 text-gray-700 hover:text-gray-900 hidden">
                    <i class="fa fa-chevron-right fa-lg"></i>
                </button>
                <h1 class="text-xl font-bold text-gray-800">Alina AI</h1>
            </div>
            <div id="konten-chat" class="flex-1 p-4 overflow-y-auto space-y-4 bg-gray-50"></div>
            <div class="p-4 bg-white border-t flex gap-2">
                <input type="text" id="pesan" placeholder="Ketik pertanyaan Anda..." class="flex-1 border rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-400">
                <button onclick="kirimPesan()" class="bg-blue-600 hover:bg-blue-700 text-white px-5 py-2 rounded-lg">
                    <i class="fa fa-paper-plane"></i>
                </button>
            </div>
        </div>

        <script>
        function toggleSidebar() {
            const sidebar = document.getElementById('sidebar');
            const tombolBuka = document.getElementById('tombolBuka');
            if(sidebar.classList.contains('w-80')) {
                sidebar.classList.remove('w-80');
                sidebar.classList.add('w-0');
                tombolBuka.classList.remove('hidden');
            } else {
                sidebar.classList.remove('w-0');
                sidebar.classList.add('w-80');
                tombolBuka.classList.add('hidden');
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
                gantiPesanTerakhir('Alina', '❌ Terjadi kesalahan, silakan coba lagi.');
            }
        }

        function tampilkanPesan(pengirim, teks) {
            const kotak = document.getElementById('konten-chat');
            const balon = document.createElement('div');
            balon.className = `p-3 rounded-lg max-w-[85%] ${pengirim==='Anda' ? 'bg-blue-100 ml-auto' : 'bg-white border'}`;
            balon.innerHTML = `<b>${pengirim}:</b><br>${teks.replace(/\n/g, '<br>')}`;
            kotak.appendChild(balon);
            kotak.scrollTop = kotak.scrollHeight;
        }

        function gantiPesanTerakhir(pengirim, teks) {
            const kotak = document.getElementById('konten-chat');
            const balon = kotak.lastChild;
            balon.innerHTML = `<b>${pengirim}:</b><br>${teks.replace(/\n/g, '<br>')}`;
        }

        async function muatRiwayat() {
            const res = await fetch('/api/riwayat');
            const data = await res.json();
            const kotak = document.getElementById('riwayat');
            if(data.length === 0) {
                kotak.innerHTML = '<p class="text-gray-500 text-center">Belum ada riwayat</p>';
                return;
            }
            kotak.innerHTML = '';
            data.forEach(item => {
                const el = document.createElement('div');
                el.className = 'p-2 border rounded hover:bg-gray-50 cursor-pointer';
                el.innerHTML = `<small class="text-gray-500">${item.waktu}</small><br><b>Tanya:</b> ${item.tanya.slice(0,40)}...`;
                el.onclick = () => tampilkanLengkap(item);
                kotak.appendChild(el);
            });
        }

        function tampilkanLengkap(item) {
            document.getElementById('konten-chat').innerHTML = '';
            tampilkanPesan('Anda', item.tanya);
            tampilkanPesan('Alina', item.jawab);
        }

        muatRiwayat();
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
            logger.error(f"❌ Pengecekan gagal: {e}")
        time.sleep(6 * 3600)

threading.Thread(target=jadwal_pengecekan, daemon=True).start()

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
