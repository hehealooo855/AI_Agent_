import streamlit as st
import yfinance as yf
import pandas as pd

# Konfigurasi Halaman Dashboard
st.set_page_config(page_title="Multi-Agent Terminal", layout="wide")
st.title("Terminal Intelijen Makro & Ekuity")

# Fungsi untuk menarik data harga terakhir (Pengganti Scraping TradingView)
def tarik_harga_terbaru():
    # Daftar simbol ticker (IHSG, Bitcoin, Gold, dan saham lokal misalnya BBCA.JK)
    tickers = {
        "IHSG": "^JKSE",
        "Bitcoin": "BTC-USD",
        "Gold": "GC=F",
        "BBCA": "BBCA.JK"
    }
    
    data_harga = {}
    
    for nama, simbol in tickers.items():
        aset = yf.Ticker(simbol)
        # Menarik data 1 hari terakhir
        hist = aset.history(period="1d")
        if not hist.empty:
            harga_terakhir = hist['Close'].iloc[-1]
            volume_terakhir = hist['Volume'].iloc[-1]
            data_harga[nama] = {"Harga": harga_terakhir, "Volume": volume_terakhir}
        else:
            data_harga[nama] = {"Harga": "N/A", "Volume": "N/A"}
            
    return data_harga

# Layout Bagian Atas: Tombol Update dan Indikator Harga
col_header, col_btn = st.columns([4, 1])

with col_header:
    st.markdown("### Pantauan Harga Real-Time")
    
with col_btn:
    # Ini adalah tombol ajaib Anda
    tombol_update = st.button("🔄 Update Harga Sekarang", use_container_width=True)

# Logika ketika tombol ditekan
if tombol_update:
    with st.spinner("Mengambil data dari bursa..."):
        harga_terkini = tarik_harga_terbaru()
        
        # Menampilkan harga dalam metrik yang rapi
        col1, col2, col3, col4 = st.columns(4)
        
        # Format angka agar mudah dibaca
        col1.metric("IHSG", f"{harga_terkini['IHSG']['Harga']:,.2f}")
        col2.metric("Bitcoin (USD)", f"${harga_terkini['Bitcoin']['Harga']:,.2f}")
        col3.metric("Gold (USD)", f"${harga_terkini['Gold']['Harga']:,.2f}")
        col4.metric("BBCA (IDR)", f"Rp {harga_terkini['BBCA']['Harga']:,.0f}")
        
        st.success("Data harga berhasil diperbarui!")
        
st.divider()

# Placeholder untuk Tab Multi-Agent kita nanti
st.markdown("### Analisis Multi-Agent")
tab_remora, tab_timothy, tab_astronacci, tab_news = st.tabs([
    "🦈 Agen Remora (Volume)", 
    "📈 Agen Timothy (Makro)", 
    "🕰️ Agen Astronacci (Time-Cycle)", 
    "📰 Radar Berita"
])

with tab_remora:
    st.info("Klik tombol Update di atas untuk menyuruh AI menganalisis lonjakan volume BBCA hari ini.")