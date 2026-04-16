import streamlit as st
import yfinance as yf
import pandas as pd
import google.generativeai as genai

# ==========================================
# 1. KONFIGURASI HALAMAN & API
# ==========================================
st.set_page_config(page_title="Multi-Agent Terminal", layout="wide", page_icon="📈")
st.title("📈 Terminal Intelijen Makro & Ekuity")

# Sidebar untuk API Key (Praktik aman untuk aplikasi lokal)
with st.sidebar:
    st.header("⚙️ Pengaturan Sistem")
    api_key = st.text_input("Masukkan Gemini API Key", type="password")
    if api_key:
        genai.configure(api_key=api_key)
    st.markdown("---")
    st.caption("Dikembangkan menggunakan arsitektur Multi-Agent.")

# Pastikan API Key diisi sebelum lanjut
if not api_key:
    st.warning("⚠️ Silakan masukkan Gemini API Key di sidebar sebelah kiri untuk mengaktifkan AI.")
    st.stop()

# Inisialisasi Model AI
model = genai.GenerativeModel('gemini-2.5-flash')# Menggunakan model flash agar respons cepat

# ==========================================
# 2. FUNGSI PENGAMBILAN DATA (DATA INGESTION)
# ==========================================
@st.cache_data(ttl=300) # Cache data selama 5 menit agar tidak terus-terusan hit API yfinance
def tarik_data_historis(simbol, periode="1mo"):
    try:
        aset = yf.Ticker(simbol)
        df = aset.history(period=periode)
        return df
    except Exception as e:
        return None

# ==========================================
# 3. FUNGSI OTAK AGEN (AI PROMPTING)
# ==========================================
def agen_remora(data_df, nama_aset):
    # Menyaring data 5 hari terakhir untuk dianalisis AI
    data_ringkas = data_df.tail(5)[['Open', 'High', 'Low', 'Close', 'Volume']].to_string()
    
    prompt = f"""
    Bertindaklah sebagai 'Agen Remora', seorang analis bandarmologi kuantitatif profesional.
    Tugas Anda HANYA menganalisis anomali harga dan lonjakan volume transaksi. Abaikan sentimen berita.
    
    Berikut adalah data harga dan volume 5 hari terakhir untuk aset {nama_aset}:
    {data_ringkas}
    
    Berikan analisis singkat (maksimal 3 paragraf) mengenai:
    1. Apakah ada indikasi akumulasi atau distribusi institusi berdasarkan pergerakan volume dan harga penutupan?
    2. Tingkat probabilitas (Rendah/Menengah/Tinggi) adanya pergerakan harga signifikan dalam waktu dekat.
    Gunakan gaya bahasa teknis, tegas, dan langsung pada intinya.
    """
    
    respons = model.generate_content(prompt)
    return respons.text
import numpy as np

# ==========================================
# 5. FUNGSI MATEMATIKA BANDARMOLOGI (HENGKY A.)
# ==========================================
def hitung_floor_price(df, periode_akumulasi=10):
    """
    Menghitung VWAP (Volume Weighted Average Price) 
    sebagai proxy 'Floor Price' / Harga Modal Bandar.
    """
    # Mengambil N hari terakhir (fase akumulasi)
    akumulasi_df = df.tail(periode_akumulasi).copy()
    
    # Typical Price = (High + Low + Close) / 3
    akumulasi_df['Typical_Price'] = (akumulasi_df['High'] + akumulasi_df['Low'] + akumulasi_df['Close']) / 3
    
    # VWAP = Sum(Typical Price * Volume) / Sum(Volume)
    floor_price = (akumulasi_df['Typical_Price'] * akumulasi_df['Volume']).sum() / akumulasi_df['Volume'].sum()
    return floor_price

# ==========================================
# 6. FUNGSI MANAJEMEN RISIKO (POSITION SIZING)
# ==========================================
def hitung_lot_aman(modal_rdn, resiko_persen, harga_masuk, floor_price_bandar):
    # Risiko Rupiah yang siap hilang
    toleransi_kerugian_rp = modal_rdn * (resiko_persen / 100)
    
    # Jarak CL: Harga masuk ke Floor Price (Bandar out di bawah floor price)
    # Kita beri buffer 1% di bawah floor price agar tidak kena false breakdown
    titik_cl = floor_price_bandar * 0.99 
    
    jarak_cl_per_lembar = harga_masuk - titik_cl
    
    if jarak_cl_per_lembar <= 0:
         return 0, titik_cl, "Harga sudah di bawah modal bandar. JANGAN BELI."
         
    # Maksimal beli (dalam lembar) = Toleransi Rp / Kerugian per lembar
    maks_lembar = toleransi_kerugian_rp / jarak_cl_per_lembar
    maks_lot = int(maks_lembar / 100)
    
    return maks_lot, titik_cl, "Aman untuk masuk."
def agen_remora_v2(data_df, nama_aset):
    # Hitung Floor Price Bandar
    floor_price = hitung_floor_price(data_df)
    harga_sekarang = data_df['Close'].iloc[-1]
    vol_sekarang = data_df['Volume'].iloc[-1]
    rata_vol = data_df['Volume'].tail(20).mean()
    
    prompt = f"""
    Bertindaklah sebagai Hengky Adinata, penganut aliran 'Remora' (Smart Money Tracker).
    Aturan utama Anda: Jangan trading jika bandar sedang distribusi. CL HANYA jika bandar keluar.
    
    Data {nama_aset} hari ini:
    - Harga Terakhir: {harga_sekarang}
    - Floor Price (Modal Rata-rata Bandar): {floor_price:.2f}
    - Volume Hari Ini vs Rata-rata: {vol_sekarang/rata_vol:.1f}x
    
    Berikan instruksi eksekusi yang TEGAS dan JELAS:
    1. STATUS KEPUTUSAN: Jawab dengan 'BUY', 'HOLD', atau 'NO BUY / BANDAR OUT'.
    2. ANALISIS MOMENTUM: Jelaskan apakah bandar sedang akumulasi (mendekati floor price dengan volume kering) atau distribusi.
    3. INSTRUKSI CL: Di mana titik batal skenarionya? (Harus di bawah Floor Price).
    """
    
    respons = model.generate_content(prompt)
    return respons.text, floor_price
with tab_remora:
    # Bagian Analisis AI
    hasil_remora, modal_bandar = agen_remora_v2(df, nama_label[pilihan_aset])
    st.markdown(hasil_remora)
    
    st.divider()
    
    # Bagian Modul Eksekusi / Position Sizing
    st.markdown("### 🧮 Terminal Eksekusi (Risk Management)")
    col_a, col_b = st.columns(2)
    
    with col_a:
        saldo = st.number_input("Saldo RDN Aktif (Rp)", value=10000000, step=1000000)
        resiko = st.slider("Toleransi Risiko per Trade (%)", min_value=0.5, max_value=5.0, value=1.0, step=0.1)
        
    with col_b:
        st.info(f"📍 Floor Price Bandar (Estimasi VWAP): **Rp {modal_bandar:,.0f}**")
        
        if st.button("Hitung Maksimal Beli (Lot)"):
            lot, titik_cl, pesan = hitung_lot_aman(saldo, resiko, harga_terkini, modal_bandar)
            
            if lot > 0:
                st.success(f"✅ **Beli Maksimal: {lot} Lot**")
                st.warning(f"🚨 **Cut Loss Jika Harga Tutup di Bawah: Rp {titik_cl:,.0f}**")
                st.caption(f"Jika terkena CL, kerugian maksimal Anda adalah Rp {saldo*(resiko/100):,.0f} ({resiko}% dari saldo).")
            else:
                st.error(pesan)

def agen_timothy(data_df, nama_aset):
    harga_sekarang = data_df['Close'].iloc[-1]
    harga_bulan_lalu = data_df['Close'].iloc[0]
    persentase_perubahan = ((harga_sekarang - harga_bulan_lalu) / harga_bulan_lalu) * 100
    
    prompt = f"""
    Bertindaklah sebagai analis makroekonomi yang pro terhadap 'hard assets' (seperti Bitcoin dan Emas) 
    dan sangat kritis terhadap sistem uang fiat, pelemahan daya beli, dan inflasi.
    
    Aset yang dianalisis: {nama_aset}.
    Perubahan harga 1 bulan terakhir: {persentase_perubahan:.2f}%.
    Harga saat ini: {harga_sekarang}.
    
    Berikan analisis naratif (maksimal 3 paragraf) mengenai:
    1. Bagaimana performa aset ini mencerminkan kondisi makro ekonomi global saat ini (inflasi, kebijakan bank sentral)?
    2. Apakah aset ini layak dipegang untuk melindungi kekayaan (wealth preservation) dari pelemahan nilai mata uang?
    Gunakan gaya bahasa naratif yang provokatif, meyakinkan, dan berfokus pada pelestarian kelas menengah.
    """
    
    respons = model.generate_content(prompt)
    return respons.text

# ==========================================
# 4. ANTARMUKA PENGGUNA (UI DASHBOARD)
# ==========================================
st.markdown("### 🔍 Pilih Aset untuk Dianalisis")

col1, col2 = st.columns([1, 3])
with col1:
    pilihan_aset = st.selectbox(
        "Instrumen:",
        ("BTC-USD", "GC=F", "^JKSE", "BBCA.JK", "CUAN.JK", "PTRO.JK")
    )
    nama_label = {
        "BTC-USD": "Bitcoin", "GC=F": "Gold (Emas)", "^JKSE": "IHSG",
        "BBCA.JK": "Bank BCA", "CUAN.JK": "Petrindo Jaya", "PTRO.JK": "Petrosea"
    }

with col2:
    if st.button("🚀 Jalankan Analisis Multi-Agent", type="primary", use_container_width=True):
        with st.spinner(f"Menarik data dari bursa untuk {nama_label[pilihan_aset]}..."):
            df = tarik_data_historis(pilihan_aset)
            
        if df is not None and not df.empty:
            harga_terkini = df['Close'].iloc[-1]
            st.success(f"Data berhasil ditarik! Harga Terakhir {nama_label[pilihan_aset]}: **{harga_terkini:,.2f}**")
            st.divider()
            
            # Menjalankan AI Agents
            tab_remora, tab_timothy = st.tabs(["🦈 Agen Remora (Volume & Bandarmologi)", "📈 Agen Timothy (Makro & Fiat)"])
            
            with tab_remora:
                with st.spinner("Agen Remora sedang menganalisis jejak institusi..."):
                    hasil_remora = agen_remora(df, nama_label[pilihan_aset])
                    st.markdown(hasil_remora)
                    with st.expander("Lihat Data Mentah (5 Hari Terakhir)"):
                        st.dataframe(df.tail(5))
                        
            with tab_timothy:
                with st.spinner("Agen Timothy sedang menyusun narasi makro..."):
                    hasil_timothy = agen_timothy(df, nama_label[pilihan_aset])
                    st.markdown(hasil_timothy)
        else:
            st.error("Gagal menarik data. Periksa koneksi internet atau simbol aset.")
