import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import google.generativeai as genai

# ==========================================
# 1. KONFIGURASI HALAMAN & API (HARDCODED)
# ==========================================
# GANTI TEKS DI BAWAH INI DENGAN API KEY ANDA YANG ASLI
API_KEY = "AIzaSyB6FDzxu6Sl7eHPUMPqrIAfaOthzSehYeY"

try:
    genai.configure(api_key=API_KEY)
except Exception as e:
    st.error("Gagal mengonfigurasi API Key. Pastikan Key sudah benar.")
    st.stop()

st.set_page_config(page_title="Terminal Trading AI", layout="wide", page_icon="📈")
st.title("📈 Terminal Eksekusi & Intelijen Makro")

# Menggunakan model terbaru
model = genai.GenerativeModel('gemini-2.5-flash')

# ==========================================
# 2. FUNGSI PENGAMBILAN DATA
# ==========================================
@st.cache_data(ttl=300)
def tarik_data_historis(simbol, periode="1mo"):
    try:
        aset = yf.Ticker(simbol)
        df = aset.history(period=periode)
        return df
    except Exception as e:
        return None

# ==========================================
# 3. MATEMATIKA BANDARMOLOGI & RISK MANAGEMENT
# ==========================================
def hitung_floor_price(df, periode_akumulasi=10):
    if len(df) < periode_akumulasi:
        periode_akumulasi = len(df)
    akumulasi_df = df.tail(periode_akumulasi).copy()
    akumulasi_df['Typical_Price'] = (akumulasi_df['High'] + akumulasi_df['Low'] + akumulasi_df['Close']) / 3
    floor_price = (akumulasi_df['Typical_Price'] * akumulasi_df['Volume']).sum() / akumulasi_df['Volume'].sum()
    return floor_price

def hitung_lot_aman(modal_rdn, resiko_persen, harga_masuk, floor_price_bandar):
    toleransi_kerugian_rp = modal_rdn * (resiko_persen / 100)
    titik_cl = floor_price_bandar * 0.99 # Buffer 1% di bawah Floor Price
    jarak_cl_per_lembar = harga_masuk - titik_cl
    
    if jarak_cl_per_lembar <= 0:
         return 0, titik_cl, "Harga sudah di bawah modal bandar. JANGAN BELI (Distribusi/Bandar Out)."
         
    maks_lembar = toleransi_kerugian_rp / jarak_cl_per_lembar
    maks_lot = int(maks_lembar / 100)
    return maks_lot, titik_cl, "Aman untuk masuk."

# ==========================================
# 4. OTAK AI (AGENTS)
# ==========================================
def agen_remora_v2(data_df, nama_aset):
    floor_price = hitung_floor_price(data_df)
    harga_sekarang = data_df['Close'].iloc[-1]
    vol_sekarang = data_df['Volume'].iloc[-1]
    rata_vol = data_df['Volume'].tail(20).mean()
    
    prompt = f"""
    Bertindaklah sebagai Hengky Adinata, penganut aliran 'Remora' (Smart Money Tracker).
    Aturan utama Anda: Jangan trading jika bandar sedang distribusi. CL HANYA jika bandar keluar.
    
    Data {nama_aset} hari ini:
    - Harga Terakhir: {harga_sekarang}
    - Floor Price (Modal Rata-rata Bandar 10 hari terakhir): {floor_price:.2f}
    - Volume Hari Ini vs Rata-rata 20 Hari: {vol_sekarang/rata_vol:.1f}x
    
    Berikan instruksi eksekusi yang TEGAS dan JELAS dalam format berikut:
    1. STATUS: (Pilih salah satu: BUY / HOLD / NO BUY - BANDAR OUT)
    2. ANALISIS MOMENTUM: (Jelaskan secara ringkas apakah bandar sedang akumulasi atau distribusi berdasarkan posisi harga terhadap floor price dan lonjakan volume).
    3. TARGET & CUT LOSS: (Sebutkan CL harus di bawah {floor_price:.2f} karena itu adalah modal bandar).
    """
    return model.generate_content(prompt).text, floor_price

def agen_timothy(data_df, nama_aset):
    harga_sekarang = data_df['Close'].iloc[-1]
    harga_bulan_lalu = data_df['Close'].iloc[0]
    persentase_perubahan = ((harga_sekarang - harga_bulan_lalu) / harga_bulan_lalu) * 100
    
    prompt = f"""
    Bertindaklah sebagai Timothy Ronald, analis makroekonomi yang pro terhadap 'hard assets'.
    
    Aset: {nama_aset}.
    Perubahan harga 1 bulan terakhir: {persentase_perubahan:.2f}%.
    Harga saat ini: {harga_sekarang}.
    
    Berikan analisis eksekusi dengan format:
    1. KEPUTUSAN ALOKASI: (Akumulasi / Tahan / Hindari)
    2. PANDANGAN MAKRO: (Hubungkan performa aset ini dengan kondisi makro ekonomi, inflasi, atau fiat).
    3. HORIZON INVESTASI & CL: (Kapan skenario investasi ini batal/invalid secara struktural makro).
    """
    return model.generate_content(prompt).text

def remora_scanner(list_emiten):
    saham_terdeteksi = []
    for simbol in list_emiten:
        df = tarik_data_historis(simbol, periode="1mo")
        if df is not None and len(df) > 20:
            vol_sekarang = df['Volume'].iloc[-1]
            avg_vol = df['Volume'].tail(20).mean()
            # Kriteria: Volume meledak lebih dari 2.5x rata-rata
            if vol_sekarang > (avg_vol * 2.5):
                change = ((df['Close'].iloc[-1] - df['Close'].iloc[-2]) / df['Close'].iloc[-2]) * 100
                saham_terdeteksi.append({
                    "Simbol": simbol,
                    "Lonjakan Vol": f"{vol_sekarang/avg_vol:.2f}x",
                    "Perubahan Harga": f"{change:.2f}%"
                })
    return pd.DataFrame(saham_terdeteksi)

# ==========================================
# 5. ANTARMUKA PENGGUNA (UI DASHBOARD)
# ==========================================
tab_utama, tab_scanner = st.tabs(["🎯 Analisis & Eksekusi Aset", "🔍 Remora Scanner (Batch)"])

with tab_utama:
    st.markdown("### Pilih Aset untuk Dianalisis")
    col1, col2 = st.columns([1, 3])
    with col1:
        pilihan_aset = st.selectbox(
            "Daftar Pantau:",
            ("PTRO.JK", "CUAN.JK", "BBCA.JK", "BTC-USD", "GC=F", "^JKSE")
        )
        nama_label = {
            "PTRO.JK": "Petrosea", "CUAN.JK": "Petrindo Jaya", "BBCA.JK": "Bank BCA",
            "BTC-USD": "Bitcoin", "GC=F": "Gold", "^JKSE": "IHSG"
        }

    with col2:
        if st.button("🔄 Update Harga & Eksekusi AI", type="primary", use_container_width=True):
            with st.spinner(f"Menarik data dari bursa untuk {nama_label[pilihan_aset]}..."):
                df = tarik_data_historis(pilihan_aset)
                
            if df is not None and not df.empty:
                harga_terkini = df['Close'].iloc[-1]
                st.success(f"Data {nama_label[pilihan_aset]} diupdate! Harga Terakhir: **{harga_terkini:,.2f}**")
                
                # PERBAIKAN: Menggunakan col_remora dan col_timothy agar bersebelahan
                col_remora, col_timothy = st.columns(2)
                
                with col_remora:
                    st.subheader("🦈 Agen Hengky (Remora)")
                    with st.spinner("Menghitung VWAP dan Momentum..."):
                        hasil_remora, modal_bandar = agen_remora_v2(df, nama_label[pilihan_aset])
                        st.info(hasil_remora)
                        
                    st.markdown("#### 🧮 Terminal Eksekusi (Risk Management)")
                    saldo = st.number_input("Saldo RDN Aktif (Rp)", value=10000000, step=1000000, key="saldo")
                    resiko = st.slider("Toleransi Risiko (%)", min_value=0.5, max_value=5.0, value=1.0, step=0.1, key="resiko")
                    
                    st.caption(f"📍 Estimasi Modal Bandar (Floor Price): **Rp {modal_bandar:,.0f}**")
                    
                    lot, titik_cl, pesan = hitung_lot_aman(saldo, resiko, harga_terkini, modal_bandar)
                    if lot > 0:
                        st.success(f"✅ **Beli Maksimal: {lot} Lot**")
                        st.warning(f"🚨 **Cut Loss Jika Tutup di Bawah: Rp {titik_cl:,.0f}**")
                    else:
                        st.error(f"❌ {pesan}")

                with col_timothy:
                    st.subheader("📈 Agen Timothy (Makro)")
                    with st.spinner("Menyusun pandangan makro..."):
                        hasil_timothy = agen_timothy(df, nama_label[pilihan_aset])
                        st.warning(hasil_timothy)
            else:
                st.error("Gagal menarik data bursa.")

with tab_scanner:
    st.markdown("### 📡 Pemindai Anomali Volume (Remora Detector)")
    st.write("Fitur ini akan memindai daftar saham untuk mencari lonjakan volume > 2.5x dari rata-rata 20 hari.")
    
    watchlist_default = "PTRO.JK, CUAN.JK, BREN.JK, AMMN.JK, BBCA.JK, BMRI.JK, ASII.JK, TLKM.JK"
    input_emiten = st.text_input("Masukkan Kode Saham (Pisahkan dengan koma):", value=watchlist_default)
    
    if st.button("Mulai Pemindaian 🦈", use_container_width=True):
        list_saham = [x.strip() for x in input_emiten.split(",")]
        with st.spinner("Memindai data volume... Ini mungkin memakan waktu beberapa saat."):
            hasil_scan = remora_scanner(list_saham)
            
            if not hasil_scan.empty:
                st.success("Anomali ditemukan pada saham berikut!")
                st.dataframe(hasil_scan, use_container_width=True)
            else:
                st.info("Tidak ada saham yang memenuhi kriteria lonjakan volume hari ini.")
