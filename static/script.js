const formPesan = document.getElementById('formPesan');
const inputPesan = document.getElementById('inputPesan');
const chatBox = document.getElementById('chatBox');
const tombolMode = document.getElementById('modeGelap');

tombolMode.addEventListener('click', () => {
    document.body.classList.toggle('dark');
    tombolMode.textContent = document.body.classList.contains('dark') ? '☀️' : '🌙';
});

formPesan.addEventListener('submit', async (e) => {
    e.preventDefault();
    const teks = inputPesan.value.trim();
    if (!teks) return;

    tambahPesan(teks, 'pengguna');
    inputPesan.value = '';

    const status = tambahPesan('Sedang mengetik...', 'alina');

    try {
        const res = await fetch('/api/tanya', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pesan: teks })
        });
        const data = await res.json();
        status.querySelector('.teks').textContent = data.jawaban;
    } catch (err) {
        status.querySelector('.teks').textContent = '❌ Maaf, terjadi kesalahan saat menghubungi Alina.';
    }
});

function tambahPesan(teks, pengirim) {
    const div = document.createElement('div');
    div.className = `pesan ${pengirim}`;
    div.innerHTML = `
        <img src="/static/assets/${pengirim === 'alina' ? 'logo.png' : 'user.png'}" class="avatar">
        <div class="teks">${teks}</div>
    `;
    chatBox.appendChild(div);
    chatBox.scrollTop = chatBox.scrollHeight;
    return div;
}