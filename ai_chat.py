"""
ai_chat.py - FASE 2: Tampilan chat "Asisten AI" untuk Streamlit.

Cara pakai di app.py (lihat panduan integrasi):

    import ai_chat
    with t_ai:
        ai_chat.render_asisten_ai(df, user_role, user_name, cfg, catat=log_activity)

Secrets yang dibaca (sama dengan ai_tes_koneksi.py):
    GEMINI_API_KEY   (wajib)
    GEMINI_MODEL     (opsional, default gemini-3.5-flash-lite)

Catatan desain:
  * Seluruh isi chat dibungkus @st.fragment -> mengirim pertanyaan TIDAK menjalankan ulang seluruh dashboard.
  * Pada rerun penuh dashboard, tab ini hanya menggambar riwayat (murah); perhitungan baru berjalan saat ada pertanyaan.
  * Riwayat chat disimpan per sesi dan dihapus otomatis bila user yang login berganti.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

import pandas as pd
import streamlit as st

import ai_agent
import ai_tools

MODEL_DEFAULT = "gemini-3.5-flash-lite"
MAKS_PERTANYAAN_SESI = 40       # pagar biaya/kuota per sesi login

CONTOH_PERTANYAAN = [
    "Bagaimana omset bulan ini dibanding periode sebelumnya?",
    "Bagaimana pencapaian target bulan ini?",
    "Toko mana yang sudah 30 hari tidak order?",
    "Outlet mana yang omsetnya turun paling dalam bulan ini?",
    "Toko yang beli Wardah tapi belum beli Emina bulan ini?",
    "Apa 10 produk terlaris bulan ini?",
]

_K_PESAN, _K_PEMILIK, _K_JUMLAH, _K_ANTRIAN = "ai_pesan", "ai_pemilik", "ai_jumlah", "ai_antrian"


@st.cache_resource(show_spinner=False)
def _buat_client(api_key: str):
    from google import genai
    return genai.Client(api_key=api_key)


def _baca_secret(nama: str, default: Optional[str] = None) -> Optional[str]:
    try:
        return st.secrets.get(nama, default)
    except Exception:  # secrets.toml tidak ada / rusak
        return default


def _gambar_jawaban(pesan: dict) -> None:
    st.markdown(pesan["teks"])
    if pesan.get("angka_tak_terverifikasi"):
        st.warning("⚠️ Angka berikut tidak ditemukan persis di hasil perhitungan, jadi jangan dipakai sebelum dicek: "
                   + ", ".join(f"`{a}`" for a in pesan["angka_tak_terverifikasi"]))
    if pesan.get("panggilan"):
        with st.expander(f"🔍 Lihat perhitungan ({len(pesan['panggilan'])} alat dipakai)"):
            st.caption("Semua angka dihitung oleh program (ai_tools.py). AI hanya memilih alat dan menjelaskan hasilnya.")
            for p in pesan["panggilan"]:
                st.markdown(f"**{p['alat']}**  `{p['argumen'] or 'parameter default'}`")
                st.json(p["hasil"], expanded=False)


@st.fragment
def _isi_chat(df: pd.DataFrame, role: str, sales_name: str, config: ai_tools.ToolsConfig,
              catat: Optional[Callable[[str, str], Any]], client: Any, model: str) -> None:
    # Riwayat hanya milik user yang sedang login
    if st.session_state.get(_K_PEMILIK) != sales_name:
        st.session_state[_K_PEMILIK] = sales_name
        st.session_state[_K_PESAN] = []
        st.session_state[_K_JUMLAH] = 0
        st.session_state[_K_ANTRIAN] = None
    riwayat: list[dict] = st.session_state[_K_PESAN]

    kiri, kanan = st.columns([5, 1])
    with kiri:
        st.caption("Tanyakan omset, target, toko tidur, outlet turun order, cross-sell, atau top produk. "
                   "Hak akses mengikuti akun Anda.")
    with kanan:
        if st.button("🧹 Hapus chat", use_container_width=True, disabled=not riwayat):
            riwayat.clear()          # in-place: bagian bawah langsung menggambar keadaan kosong

    tempat_contoh = st.empty()       # placeholder agar tombol contoh bisa dihapus begitu ada pertanyaan
    if not riwayat:
        with tempat_contoh.container():
            st.markdown("**Contoh pertanyaan:**")
            kolom = st.columns(2)
            for i, contoh in enumerate(CONTOH_PERTANYAAN):
                if kolom[i % 2].button(contoh, key=f"ai_contoh_{i}", use_container_width=True):
                    st.session_state[_K_ANTRIAN] = contoh

    for m in riwayat:
        with st.chat_message("user" if m["peran"] == "user" else "assistant"):
            if m["peran"] == "user":
                st.markdown(m["teks"])
            else:
                _gambar_jawaban(m)

    pertanyaan = st.chat_input("Tanya soal penjualan Anda...", key="ai_input") or st.session_state.get(_K_ANTRIAN)
    st.session_state[_K_ANTRIAN] = None
    if not pertanyaan:
        return
    tempat_contoh.empty()

    if st.session_state[_K_JUMLAH] >= MAKS_PERTANYAAN_SESI:
        st.info(f"Batas {MAKS_PERTANYAAN_SESI} pertanyaan per sesi tercapai. Login ulang untuk melanjutkan.")
        return

    with st.chat_message("user"):
        st.markdown(pertanyaan)
    with st.chat_message("assistant"):
        with st.spinner("Menghitung..."):
            hasil = ai_agent.tanya_asisten(
                pertanyaan, riwayat=[{"peran": m["peran"], "teks": m["teks"]} for m in riwayat],
                df=df, role=role, sales_name=sales_name, config=config, client=client, model=model)
        if hasil.galat:
            st.error(hasil.galat)
            if hasil.panggilan:                     # tampilkan perhitungan parsial agar tetap berguna
                _gambar_jawaban({"teks": "", "panggilan": hasil.panggilan})
        else:
            pesan = {"peran": "asisten", "teks": hasil.teks, "panggilan": hasil.panggilan,
                     "angka_tak_terverifikasi": hasil.angka_tak_terverifikasi}
            _gambar_jawaban(pesan)

    st.session_state[_K_JUMLAH] += 1
    if not hasil.galat:                             # galat tidak masuk riwayat agar tidak mencemari konteks
        riwayat.append({"peran": "user", "teks": pertanyaan})
        riwayat.append(pesan)
    if catat:
        try:
            catat(sales_name, f"AI TANYA: {pertanyaan[:120]}" + (" [GALAT]" if hasil.galat else ""))
        except Exception:  # audit log tidak boleh merusak chat
            pass


def render_asisten_ai(df: pd.DataFrame, role: str, sales_name: str, config: ai_tools.ToolsConfig,
                      catat: Optional[Callable[[str, str], Any]] = None,
                      client: Any = None, model: Optional[str] = None) -> None:
    """Titik masuk dari app.py. `client`/`model` hanya untuk pengujian (default: dari st.secrets)."""
    st.subheader("🤖 Asisten AI")
    if client is None:
        api_key = _baca_secret("GEMINI_API_KEY")
        if not api_key:
            st.warning("GEMINI_API_KEY belum diatur di secrets, jadi Asisten AI belum aktif.")
            return
        client = _buat_client(str(api_key))
    model = model or _baca_secret("GEMINI_MODEL", MODEL_DEFAULT)
    _isi_chat(df, role, sales_name, config, catat, client, model)
