from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Alina AI", version="1.0.0")

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

class PesanMasuk(BaseModel):
    pesan: str

@app.get("/")
def halaman_utama():
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
        jawaban = f"Saya mengerti pesanmu: {data.pesan}\n\nSaat ini saya masih dalam tahap pengembangan, tapi nanti saya akan bisa menjawab lebih lengkap dan akurat!"

    return {"jawaban": jawaban}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
