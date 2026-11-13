import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import datetime
import time
import re
import pytz
import io 
import os
import hashlib
import numpy as np
import pyotp  
import qrcode 
from calendar import monthrange
from itertools import combinations
from collections import Counter
import calendar
import concurrent.futures
import streamlit.components.v1 as components 

# --- LIBRARY UNTUK TABEL EXCEL-LIKE ---
try:
    from st_aggrid import AgGrid, GridOptionsBuilder, JsCode, ColumnsAutoSizeMode
    AGGRID_AVAILABLE = True
except ImportError:
    AGGRID_AVAILABLE = False

# --- 1. KONFIGURASI HALAMAN ---
st.set_page_config(
    page_title="Dashboard Sales", 
    layout="wide", 
    initial_sidebar_state="expanded"
)

# --- MENGAKTIFKAN FITUR ZOOM DI HP (MOBILE RESPONSIVE) ---
components.html("""
<script>
    // Memaksa browser HP untuk mengizinkan zoom hingga 5x lipat
    var viewportMeta = window.parent.document.querySelector('meta[name="viewport"]');
    if (viewportMeta) {
        viewportMeta.setAttribute('content', 'width=device-width, initial-scale=1.0, maximum-scale=5.0, user-scalable=yes');
    } else {
        var meta = window.parent.document.createElement('meta');
        meta.name = "viewport";
        meta.content = "width=device-width, initial-scale=1.0, maximum-scale=5.0, user-scalable=yes";
        window.parent.document.head.appendChild(meta);
    }
</script>
""", height=0, width=0)

# --- INIT SESSION STATE UNTUK KEAMANAN (ANTI BRUTE-FORCE) ---
if 'failed_attempts' not in st.session_state:
    st.session_state['failed_attempts'] = {}
if 'lockout_until' not in st.session_state:
    st.session_state['lockout_until'] = {}

# --- AUTO LOGOUT (INACTIVITY TIMEOUT: 15 MENIT) ---
TIMEOUT_SECONDS = 900 
if 'last_activity' in st.session_state and st.session_state.get('logged_in', False):
    if time.time() - st.session_state['last_activity'] > TIMEOUT_SECONDS:
        st.session_state.clear()
        st.session_state['logged_out_due_to_inactivity'] = True
        st.rerun()
st.session_state['last_activity'] = time.time()

# Custom CSS & Tema Corporate Blue
st.markdown("""
<style>
    /* ==========================================================
       OPTIMALISASI KHUSUS LAYAR HP (MOBILE RESPONSIVE TWEAKS)
       ========================================================== */
    @media screen and (max-width: 768px) {
        /* 1. Perkecil font tabel bawaan Streamlit di HP agar muat banyak */
        div[data-testid="stDataFrame"] {
            font-size: 11px !important;
        }
        
        /* 2. Pastikan tabel bisa digeser ke kanan-kiri dengan mulus (Horizontal Scroll) */
        div[data-testid="stDataFrame"] > div {
            overflow-x: auto !important;
            -webkit-overflow-scrolling: touch !important; /* Efek geser mulus di iPhone/Android */
        }

        /* 3. Perkecil ukuran font pada Angka Metrik agar tidak berantakan */
        [data-testid="stMetricValue"] div {
            font-size: 24px !important; 
        }
        
        /* 4. Kurangi padding di pinggir layar agar ruang data lebih lega */
        .block-container {
            padding-left: 1rem !important;
            padding-right: 1rem !important;
        }
    }

    .metric-card {
        border: 1px solid #e6e6e6; padding: 20px; border-radius: 10px;
        background-color: #ffffff; box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    .stProgress > div > div > div > div {
        background-image: linear-gradient(to right, #3498db, #f1c40f, #2ecc71);
    }
    div[data-testid="stDataFrame"] div[role="gridcell"] {
        white-space: pre-wrap !important; 
    }
    
    /* MENYEMBUNYIKAN WATERMARK & TOMBOL MANAGE APP STREAMLIT */
    /* ==========================================================
       MEMBLOKIR TOOLBAR BAWAAN STREAMLIT (DOWNLOAD, SEARCH, DLL)
       ========================================================== */
    [data-testid="stElementToolbar"] {
        display: none !important;
        visibility: hidden !important;
    }
    
    [data-testid="stElementToolbarButton"] {
        display: none !important;
    }
    #MainMenu {visibility: hidden !important;}
    footer {visibility: hidden !important;}
    .stDeployButton {display: none !important;}
    [data-testid="stAppDeployButton"] {display: none !important;}
    .viewerBadge_container__1QSob {display: none !important;}
    div[data-testid="manage-app-button"] {display: none !important;}
    
    /* PERBESAR FONT METRIC BAWAAN STREAMLIT */
    [data-testid="stMetricLabel"] p {
        font-size: 18px !important;
        font-weight: 600 !important;
    }
    [data-testid="stMetricValue"] div {
        font-size: 36px !important;
        font-weight: bold !important;
    }
    
    /* ==========================================================
       TARGET SUPER AGRESIF UNTUK WARNA TAB BIRU CORPORATE 
       ========================================================== */
       
    /* 1. Efek Active (Saat tab sedang diklik/dibuka) - Paksa teks menjadi biru */
    div[data-testid="stTabs"] button[aria-selected="true"] p,
    div[data-testid="stTabs"] button[aria-selected="true"] span,
    div[data-testid="stTabs"] button[aria-selected="true"] div {
        color: #2980b9 !important;
        font-weight: 700 !important;
    }

    /* 2. Efek Hover (Saat kursor menyentuh tab) */
    div[data-testid="stTabs"] button[role="tab"]:hover p,
    div[data-testid="stTabs"] button[role="tab"]:hover span {
        color: #3498db !important;
    }

    /* 3. Garis bawah tab yang aktif */
    div[data-testid="stTabs"] div[data-baseweb="tab-highlight"] {
        background-color: #2980b9 !important;
    }
    
    /* 4. Sembunyikan border bawaan Streamlit agar garis biru terlihat rapi */
    div[data-testid="stTabs"] button[aria-selected="true"] {
        border-bottom-color: transparent !important;
    }

""", unsafe_allow_html=True)

# ==========================================
# MASTER KALENDER LIBUR NASIONAL 2026 (INDONESIA)
# ==========================================
HOLIDAYS_2026 = [
    '2026-01-01', '2026-02-14', '2026-02-17', '2026-03-19', '2026-03-20', 
    '2026-04-03', '2026-05-01', '2026-05-14', '2026-05-26', '2026-06-01', 
    '2026-06-16', '2026-08-17', '2026-08-25', '2026-12-25'
]

PROVINCE_MAPPING = {

}

def map_city_to_province(city_name):
    if pd.isna(city_name): return "LAIN-LAIN"
    c = str(city_name).upper().strip()
    if c in ["", "-", "NAN", "0.0", "NONE", "NULL", "0"]: return "LAIN-LAIN"
    c = re.sub(r'^(KECAMATAN|KEC\.|KEC|KABUPATEN|KAB\.|KAB|KOTA)\s+', '', c).strip()
    for province, cities in PROVINCE_MAPPING.items():
        for city in cities:
            if re.search(rf'\b{city}\b', c): return province
    return "LAIN-LAIN"

# Target bulanan (Rp) - DIHITUNG DARI DATA: rata-rata omset Agu-Okt 2026 x 1,10 (growth 10%), dibulatkan Rp100.000.
# Ini angka awal (usulan berbasis histori); ganti langsung di sini dengan target resmi.
TARGET_DATABASE = {'TIM UTAMA': {'Biore': 364200000,
               'Citra': 674400000,
               'Emina': 3091500000,
               'Eucerin': 147700000,
               'Garnier': 1242500000,
               'Gatsby': 907300000,
               'Glow & Lovely': 469800000,
               'Kahf': 498200000,
               "L'Oreal Paris": 741900000,
               'Make Over': 1696900000,
               'Maybelline': 2070600000,
               'Mustika Ratu': 317100000,
               'Nivea': 1600200000,
               'Pixy': 1476900000,
               "Pond's": 1657600000,
               'Sariayu': 373900000,
               'Vaseline': 919900000,
               'Wardah': 5065200000}}

ESTIMASI_TARGET_BULANAN = {

}

# Target bulanan (Rp) - DIHITUNG DARI DATA: rata-rata omset Agu-Okt 2026 x 1,10 (growth 10%), dibulatkan Rp100.000.
# Ini angka awal (usulan berbasis histori); ganti langsung di sini dengan target resmi.
INDIVIDUAL_TARGETS = {'AGUS SANTOSO': {'Biore': 5700000,
                  'Citra': 7100000,
                  'Emina': 40100000,
                  'Eucerin': 2600000,
                  'Garnier': 16000000,
                  'Gatsby': 10400000,
                  'Glow & Lovely': 5600000,
                  'Kahf': 3900000,
                  "L'Oreal Paris": 8300000,
                  'Make Over': 18600000,
                  'Maybelline': 24200000,
                  'Mustika Ratu': 3500000,
                  'Nivea': 17400000,
                  'Pixy': 18000000,
                  "Pond's": 21200000,
                  'Sariayu': 4800000,
                  'Vaseline': 9700000,
                  'Wardah': 62300000},
 'ALDI GUNADI': {'Biore': 1900000,
                 'Citra': 2900000,
                 'Emina': 14500000,
                 'Garnier': 4900000,
                 'Gatsby': 4000000,
                 'Glow & Lovely': 1700000,
                 'Kahf': 2400000,
                 "L'Oreal Paris": 3100000,
                 'Make Over': 9000000,
                 'Maybelline': 9800000,
                 'Mustika Ratu': 1400000,
                 'Nivea': 7700000,
                 'Pixy': 5500000,
                 "Pond's": 7800000,
                 'Sariayu': 1500000,
                 'Vaseline': 5000000,
                 'Wardah': 27600000},
 'ANDI RAHMAN': {'Biore': 5400000,
                 'Citra': 14300000,
                 'Emina': 53700000,
                 'Eucerin': 5000000,
                 'Garnier': 26700000,
                 'Gatsby': 14400000,
                 'Glow & Lovely': 8400000,
                 'Kahf': 8900000,
                 "L'Oreal Paris": 10300000,
                 'Make Over': 26100000,
                 'Maybelline': 34900000,
                 'Mustika Ratu': 4700000,
                 'Nivea': 23200000,
                 'Pixy': 25300000,
                 "Pond's": 26900000,
                 'Sariayu': 7700000,
                 'Vaseline': 15000000,
                 'Wardah': 86300000},
 'BAYU HARTONO': {'Biore': 1200000,
                  'Citra': 1900000,
                  'Emina': 13900000,
                  'Eucerin': 100000,
                  'Garnier': 4700000,
                  'Gatsby': 2900000,
                  'Glow & Lovely': 1600000,
                  'Kahf': 2200000,
                  "L'Oreal Paris": 3100000,
                  'Make Over': 6700000,
                  'Maybelline': 7900000,
                  'Mustika Ratu': 2100000,
                  'Nivea': 7400000,
                  'Pixy': 8900000,
                  "Pond's": 5400000,
                  'Sariayu': 1400000,
                  'Vaseline': 3800000,
                  'Wardah': 26200000},
 'BUDI GUNADI': {'Biore': 9200000,
                 'Citra': 15000000,
                 'Emina': 70600000,
                 'Eucerin': 300000,
                 'Garnier': 26600000,
                 'Gatsby': 18400000,
                 'Glow & Lovely': 14800000,
                 'Kahf': 14400000,
                 "L'Oreal Paris": 21900000,
                 'Make Over': 48400000,
                 'Maybelline': 51600000,
                 'Mustika Ratu': 8600000,
                 'Nivea': 37600000,
                 'Pixy': 29900000,
                 "Pond's": 35400000,
                 'Sariayu': 9600000,
                 'Vaseline': 19500000,
                 'Wardah': 110300000},
 'BUDI HAKIM': {'Biore': 7600000,
                'Citra': 13600000,
                'Emina': 56500000,
                'Eucerin': 2000000,
                'Garnier': 21400000,
                'Gatsby': 15000000,
                'Glow & Lovely': 10900000,
                'Kahf': 11100000,
                "L'Oreal Paris": 10500000,
                'Make Over': 32900000,
                'Maybelline': 46400000,
                'Mustika Ratu': 5300000,
                'Nivea': 32700000,
                'Pixy': 25000000,
                "Pond's": 32100000,
                'Sariayu': 5600000,
                'Vaseline': 15900000,
                'Wardah': 91800000},
 'BUDI SETIAWAN': {'Biore': 15100000,
                   'Citra': 23300000,
                   'Emina': 111800000,
                   'Eucerin': 1900000,
                   'Garnier': 45000000,
                   'Gatsby': 33900000,
                   'Glow & Lovely': 15300000,
                   'Kahf': 10800000,
                   "L'Oreal Paris": 40300000,
                   'Make Over': 60400000,
                   'Maybelline': 73300000,
                   'Mustika Ratu': 11800000,
                   'Nivea': 60400000,
                   'Pixy': 50700000,
                   "Pond's": 57500000,
                   'Sariayu': 9800000,
                   'Vaseline': 29200000,
                   'Wardah': 175100000},
 'CANDRA HAKIM': {'Biore': 4000000,
                  'Citra': 8500000,
                  'Emina': 29400000,
                  'Eucerin': 1400000,
                  'Garnier': 14600000,
                  'Gatsby': 13600000,
                  'Glow & Lovely': 3500000,
                  'Kahf': 6700000,
                  "L'Oreal Paris": 8800000,
                  'Make Over': 17900000,
                  'Maybelline': 21600000,
                  'Mustika Ratu': 3200000,
                  'Nivea': 15600000,
                  'Pixy': 17100000,
                  "Pond's": 18200000,
                  'Sariayu': 5200000,
                  'Vaseline': 12200000,
                  'Wardah': 59200000},
 'CANDRA HARAHAP': {'Biore': 5600000,
                    'Citra': 9800000,
                    'Emina': 47000000,
                    'Eucerin': 3300000,
                    'Garnier': 19500000,
                    'Gatsby': 13000000,
                    'Glow & Lovely': 5300000,
                    'Kahf': 8600000,
                    "L'Oreal Paris": 13900000,
                    'Make Over': 23700000,
                    'Maybelline': 33400000,
                    'Mustika Ratu': 5500000,
                    'Nivea': 23900000,
                    'Pixy': 19300000,
                    "Pond's": 22600000,
                    'Sariayu': 7600000,
                    'Vaseline': 12200000,
                    'Wardah': 72100000},
 'CANDRA HARTONO': {'Biore': 6400000,
                    'Citra': 11200000,
                    'Emina': 42000000,
                    'Eucerin': 300000,
                    'Garnier': 19100000,
                    'Gatsby': 15500000,
                    'Glow & Lovely': 5900000,
                    'Kahf': 5600000,
                    "L'Oreal Paris": 7500000,
                    'Make Over': 20200000,
                    'Maybelline': 34500000,
                    'Mustika Ratu': 4000000,
                    'Nivea': 26500000,
                    'Pixy': 22800000,
                    "Pond's": 22100000,
                    'Sariayu': 8600000,
                    'Vaseline': 14000000,
                    'Wardah': 67900000},
 'CANDRA NUGROHO': {'Biore': 3000000,
                    'Citra': 5400000,
                    'Emina': 26900000,
                    'Eucerin': 700000,
                    'Garnier': 12800000,
                    'Gatsby': 6000000,
                    'Glow & Lovely': 4300000,
                    'Kahf': 4100000,
                    "L'Oreal Paris": 6100000,
                    'Make Over': 11700000,
                    'Maybelline': 21000000,
                    'Mustika Ratu': 3200000,
                    'Nivea': 12800000,
                    'Pixy': 14700000,
                    "Pond's": 14600000,
                    'Sariayu': 2800000,
                    'Vaseline': 5600000,
                    'Wardah': 35900000},
 'CANDRA SUSANTO': {'Biore': 9900000,
                    'Citra': 13900000,
                    'Emina': 74400000,
                    'Eucerin': 8300000,
                    'Garnier': 32300000,
                    'Gatsby': 19900000,
                    'Glow & Lovely': 9600000,
                    'Kahf': 13200000,
                    "L'Oreal Paris": 9100000,
                    'Make Over': 39400000,
                    'Maybelline': 44500000,
                    'Mustika Ratu': 6700000,
                    'Nivea': 41700000,
                    'Pixy': 33400000,
                    "Pond's": 35300000,
                    'Sariayu': 9600000,
                    'Vaseline': 21100000,
                    'Wardah': 123700000},
 'EKO MARPAUNG': {'Biore': 7400000,
                  'Citra': 14100000,
                  'Emina': 58900000,
                  'Eucerin': 1800000,
                  'Garnier': 23600000,
                  'Gatsby': 24800000,
                  'Glow & Lovely': 8700000,
                  'Kahf': 10900000,
                  "L'Oreal Paris": 13500000,
                  'Make Over': 34000000,
                  'Maybelline': 42400000,
                  'Mustika Ratu': 6600000,
                  'Nivea': 23400000,
                  'Pixy': 25600000,
                  "Pond's": 28400000,
                  'Sariayu': 6600000,
                  'Vaseline': 18400000,
                  'Wardah': 95200000},
 'EKO SETIAWAN': {'Biore': 7000000,
                  'Citra': 6600000,
                  'Emina': 32500000,
                  'Eucerin': 1300000,
                  'Garnier': 14200000,
                  'Gatsby': 9500000,
                  'Glow & Lovely': 4500000,
                  'Kahf': 5500000,
                  "L'Oreal Paris": 7300000,
                  'Make Over': 14100000,
                  'Maybelline': 24100000,
                  'Mustika Ratu': 3800000,
                  'Nivea': 16900000,
                  'Pixy': 14600000,
                  "Pond's": 15900000,
                  'Sariayu': 5000000,
                  'Vaseline': 8800000,
                  'Wardah': 53700000},
 'FAJAR NUGROHO': {'Biore': 5400000,
                   'Citra': 9400000,
                   'Emina': 50300000,
                   'Eucerin': 2300000,
                   'Garnier': 18600000,
                   'Gatsby': 13100000,
                   'Glow & Lovely': 8100000,
                   'Kahf': 4800000,
                   "L'Oreal Paris": 7900000,
                   'Make Over': 27700000,
                   'Maybelline': 36600000,
                   'Mustika Ratu': 7400000,
                   'Nivea': 26100000,
                   'Pixy': 26300000,
                   "Pond's": 33500000,
                   'Sariayu': 4200000,
                   'Vaseline': 13500000,
                   'Wardah': 79300000},
 'FAJAR RAHMAN': {'Biore': 7600000,
                  'Citra': 16500000,
                  'Emina': 63700000,
                  'Eucerin': 3700000,
                  'Garnier': 20900000,
                  'Gatsby': 16200000,
                  'Glow & Lovely': 10000000,
                  'Kahf': 8100000,
                  "L'Oreal Paris": 15700000,
                  'Make Over': 32400000,
                  'Maybelline': 48600000,
                  'Mustika Ratu': 5100000,
                  'Nivea': 38900000,
                  'Pixy': 28900000,
                  "Pond's": 35800000,
                  'Sariayu': 5600000,
                  'Vaseline': 19100000,
                  'Wardah': 100200000},
 'FERRY HARAHAP': {'Biore': 6900000,
                   'Citra': 14200000,
                   'Emina': 74300000,
                   'Eucerin': 7200000,
                   'Garnier': 28800000,
                   'Gatsby': 20400000,
                   'Glow & Lovely': 13300000,
                   'Kahf': 10900000,
                   "L'Oreal Paris": 17000000,
                   'Make Over': 37100000,
                   'Maybelline': 41600000,
                   'Mustika Ratu': 8600000,
                   'Nivea': 41300000,
                   'Pixy': 40600000,
                   "Pond's": 44300000,
                   'Sariayu': 10500000,
                   'Vaseline': 23600000,
                   'Wardah': 124200000},
 'FERRY HUTAPEA': {'Biore': 2400000,
                   'Citra': 3800000,
                   'Emina': 19100000,
                   'Eucerin': 900000,
                   'Garnier': 6100000,
                   'Gatsby': 4700000,
                   'Glow & Lovely': 4300000,
                   'Kahf': 1900000,
                   "L'Oreal Paris": 3900000,
                   'Make Over': 12300000,
                   'Maybelline': 12300000,
                   'Mustika Ratu': 1800000,
                   'Nivea': 8600000,
                   'Pixy': 5900000,
                   "Pond's": 8300000,
                   'Sariayu': 2600000,
                   'Vaseline': 4700000,
                   'Wardah': 33300000},
 'GUNAWAN GUNADI': {'Biore': 2600000,
                    'Citra': 7400000,
                    'Emina': 33800000,
                    'Eucerin': 900000,
                    'Garnier': 12800000,
                    'Gatsby': 8400000,
                    'Glow & Lovely': 4300000,
                    'Kahf': 4200000,
                    "L'Oreal Paris": 10100000,
                    'Make Over': 13500000,
                    'Maybelline': 16300000,
                    'Mustika Ratu': 3900000,
                    'Nivea': 14100000,
                    'Pixy': 12900000,
                    "Pond's": 14900000,
                    'Sariayu': 3400000,
                    'Vaseline': 6300000,
                    'Wardah': 43200000},
 'GUNAWAN RAMADHAN': {'Biore': 7900000,
                      'Citra': 14700000,
                      'Emina': 65400000,
                      'Eucerin': 4100000,
                      'Garnier': 23800000,
                      'Gatsby': 23100000,
                      'Glow & Lovely': 8700000,
                      'Kahf': 13500000,
                      "L'Oreal Paris": 13800000,
                      'Make Over': 35300000,
                      'Maybelline': 49100000,
                      'Mustika Ratu': 5700000,
                      'Nivea': 33400000,
                      'Pixy': 32500000,
                      "Pond's": 44100000,
                      'Sariayu': 9100000,
                      'Vaseline': 18500000,
                      'Wardah': 112200000},
 'HASAN FIRMANSYAH': {'Biore': 5700000,
                      'Citra': 9600000,
                      'Emina': 40700000,
                      'Eucerin': 1400000,
                      'Garnier': 18600000,
                      'Gatsby': 10800000,
                      'Glow & Lovely': 6700000,
                      'Kahf': 7000000,
                      "L'Oreal Paris": 8100000,
                      'Make Over': 28000000,
                      'Maybelline': 27400000,
                      'Mustika Ratu': 3800000,
                      'Nivea': 21100000,
                      'Pixy': 17700000,
                      "Pond's": 20300000,
                      'Sariayu': 4800000,
                      'Vaseline': 11000000,
                      'Wardah': 70400000},
 'INTAN RAHMAN': {'Biore': 8200000,
                  'Citra': 9900000,
                  'Emina': 55900000,
                  'Eucerin': 7100000,
                  'Garnier': 16600000,
                  'Gatsby': 18500000,
                  'Glow & Lovely': 7100000,
                  'Kahf': 8800000,
                  "L'Oreal Paris": 15900000,
                  'Make Over': 29600000,
                  'Maybelline': 35800000,
                  'Mustika Ratu': 3800000,
                  'Nivea': 27600000,
                  'Pixy': 26600000,
                  "Pond's": 29200000,
                  'Sariayu': 5800000,
                  'Vaseline': 16300000,
                  'Wardah': 88100000},
 'IRWAN SANTOSO': {'Biore': 5000000,
                   'Citra': 9000000,
                   'Emina': 51000000,
                   'Eucerin': 1800000,
                   'Garnier': 27000000,
                   'Gatsby': 15300000,
                   'Glow & Lovely': 7900000,
                   'Kahf': 5500000,
                   "L'Oreal Paris": 12800000,
                   'Make Over': 27300000,
                   'Maybelline': 31700000,
                   'Mustika Ratu': 3700000,
                   'Nivea': 30600000,
                   'Pixy': 26700000,
                   "Pond's": 30100000,
                   'Sariayu': 5700000,
                   'Vaseline': 16200000,
                   'Wardah': 81900000},
 'IRWAN SETIAWAN': {'Biore': 6600000,
                    'Citra': 11600000,
                    'Emina': 50600000,
                    'Eucerin': 4100000,
                    'Garnier': 26000000,
                    'Gatsby': 13700000,
                    'Glow & Lovely': 7900000,
                    'Kahf': 7500000,
                    "L'Oreal Paris": 18900000,
                    'Make Over': 26300000,
                    'Maybelline': 35700000,
                    'Mustika Ratu': 6400000,
                    'Nivea': 24000000,
                    'Pixy': 24000000,
                    "Pond's": 28500000,
                    'Sariayu': 7900000,
                    'Vaseline': 16000000,
                    'Wardah': 89500000},
 'IRWAN SYAHPUTRA': {'Biore': 6300000,
                     'Citra': 11000000,
                     'Emina': 45300000,
                     'Eucerin': 1500000,
                     'Garnier': 17100000,
                     'Gatsby': 14900000,
                     'Glow & Lovely': 7300000,
                     'Kahf': 4300000,
                     "L'Oreal Paris": 15300000,
                     'Make Over': 27900000,
                     'Maybelline': 27200000,
                     'Mustika Ratu': 4300000,
                     'Nivea': 23200000,
                     'Pixy': 24900000,
                     "Pond's": 27700000,
                     'Sariayu': 6000000,
                     'Vaseline': 12900000,
                     'Wardah': 85700000},
 'JEFRI SUSANTO': {'Biore': 1800000,
                   'Citra': 2500000,
                   'Emina': 12900000,
                   'Eucerin': 200000,
                   'Garnier': 6200000,
                   'Gatsby': 3200000,
                   'Glow & Lovely': 1600000,
                   'Kahf': 3400000,
                   "L'Oreal Paris": 3600000,
                   'Make Over': 9600000,
                   'Maybelline': 11800000,
                   'Mustika Ratu': 300000,
                   'Nivea': 7700000,
                   'Pixy': 8100000,
                   "Pond's": 5900000,
                   'Sariayu': 2000000,
                   'Vaseline': 3300000,
                   'Wardah': 26200000},
 'JOKO KUSUMA': {'Biore': 8400000,
                 'Citra': 15900000,
                 'Emina': 61800000,
                 'Eucerin': 3100000,
                 'Garnier': 29200000,
                 'Gatsby': 19700000,
                 'Glow & Lovely': 10600000,
                 'Kahf': 15300000,
                 "L'Oreal Paris": 14900000,
                 'Make Over': 33800000,
                 'Maybelline': 47400000,
                 'Mustika Ratu': 9300000,
                 'Nivea': 31300000,
                 'Pixy': 37100000,
                 "Pond's": 32000000,
                 'Sariayu': 5900000,
                 'Vaseline': 19600000,
                 'Wardah': 104500000},
 'JOKO LUBIS': {'Biore': 3900000,
                'Citra': 8000000,
                'Emina': 36200000,
                'Eucerin': 1400000,
                'Garnier': 13900000,
                'Gatsby': 11200000,
                'Glow & Lovely': 6000000,
                'Kahf': 6200000,
                "L'Oreal Paris": 11300000,
                'Make Over': 19200000,
                'Maybelline': 23400000,
                'Mustika Ratu': 4400000,
                'Nivea': 17700000,
                'Pixy': 15900000,
                "Pond's": 23100000,
                'Sariayu': 3400000,
                'Vaseline': 9700000,
                'Wardah': 56800000},
 'LINA PRATAMA': {'Biore': 8400000,
                  'Citra': 17500000,
                  'Emina': 79600000,
                  'Eucerin': 3200000,
                  'Garnier': 27900000,
                  'Gatsby': 20700000,
                  'Glow & Lovely': 14400000,
                  'Kahf': 12100000,
                  "L'Oreal Paris": 17000000,
                  'Make Over': 40800000,
                  'Maybelline': 53100000,
                  'Mustika Ratu': 6600000,
                  'Nivea': 44700000,
                  'Pixy': 34400000,
                  "Pond's": 39300000,
                  'Sariayu': 9600000,
                  'Vaseline': 21200000,
                  'Wardah': 130300000},
 'LINA RAMADHAN': {'Biore': 3000000,
                   'Citra': 4200000,
                   'Emina': 19900000,
                   'Eucerin': 1800000,
                   'Garnier': 5900000,
                   'Gatsby': 4300000,
                   'Glow & Lovely': 3900000,
                   'Kahf': 2800000,
                   "L'Oreal Paris": 6300000,
                   'Make Over': 12800000,
                   'Maybelline': 16700000,
                   'Mustika Ratu': 2400000,
                   'Nivea': 11300000,
                   'Pixy': 9900000,
                   "Pond's": 11500000,
                   'Sariayu': 2800000,
                   'Vaseline': 9300000,
                   'Wardah': 32600000},
 'LUKMAN HAKIM': {'Biore': 9800000,
                  'Citra': 15700000,
                  'Emina': 65100000,
                  'Eucerin': 3600000,
                  'Garnier': 25900000,
                  'Gatsby': 22700000,
                  'Glow & Lovely': 13900000,
                  'Kahf': 10400000,
                  "L'Oreal Paris": 8200000,
                  'Make Over': 29100000,
                  'Maybelline': 46500000,
                  'Mustika Ratu': 6500000,
                  'Nivea': 35100000,
                  'Pixy': 33300000,
                  "Pond's": 31600000,
                  'Sariayu': 7300000,
                  'Vaseline': 21000000,
                  'Wardah': 121500000},
 'MAWAR FIRMANSYAH': {'Biore': 2700000,
                      'Citra': 5400000,
                      'Emina': 27800000,
                      'Eucerin': 2300000,
                      'Garnier': 12000000,
                      'Gatsby': 7000000,
                      'Glow & Lovely': 5200000,
                      'Kahf': 3400000,
                      "L'Oreal Paris": 5700000,
                      'Make Over': 15500000,
                      'Maybelline': 21600000,
                      'Mustika Ratu': 3600000,
                      'Nivea': 13700000,
                      'Pixy': 13400000,
                      "Pond's": 12800000,
                      'Sariayu': 4100000,
                      'Vaseline': 5700000,
                      'Wardah': 43600000},
 'MAWAR HUTAPEA': {'Biore': 11900000,
                   'Citra': 23100000,
                   'Emina': 113900000,
                   'Eucerin': 2600000,
                   'Garnier': 43100000,
                   'Gatsby': 30600000,
                   'Glow & Lovely': 15600000,
                   'Kahf': 14700000,
                   "L'Oreal Paris": 25200000,
                   'Make Over': 58000000,
                   'Maybelline': 69700000,
                   'Mustika Ratu': 10700000,
                   'Nivea': 50100000,
                   'Pixy': 54100000,
                   "Pond's": 59100000,
                   'Sariayu': 10700000,
                   'Vaseline': 37000000,
                   'Wardah': 181100000},
 'MAWAR MAULANA': {'Biore': 4800000,
                   'Citra': 9700000,
                   'Emina': 44500000,
                   'Eucerin': 2400000,
                   'Garnier': 18300000,
                   'Gatsby': 10500000,
                   'Glow & Lovely': 5900000,
                   'Kahf': 7900000,
                   "L'Oreal Paris": 10900000,
                   'Make Over': 21000000,
                   'Maybelline': 27100000,
                   'Mustika Ratu': 3300000,
                   'Nivea': 22900000,
                   'Pixy': 19000000,
                   "Pond's": 18600000,
                   'Sariayu': 6700000,
                   'Vaseline': 10300000,
                   'Wardah': 67200000},
 'MAWAR RAMADHAN': {'Biore': 5900000,
                    'Citra': 9900000,
                    'Emina': 57100000,
                    'Eucerin': 2200000,
                    'Garnier': 21200000,
                    'Gatsby': 15300000,
                    'Glow & Lovely': 6500000,
                    'Kahf': 9300000,
                    "L'Oreal Paris": 13000000,
                    'Make Over': 33900000,
                    'Maybelline': 34500000,
                    'Mustika Ratu': 7400000,
                    'Nivea': 29600000,
                    'Pixy': 20700000,
                    "Pond's": 29400000,
                    'Sariayu': 5300000,
                    'Vaseline': 15800000,
                    'Wardah': 83300000},
 'MAYA UTOMO': {'Biore': 2400000,
                'Citra': 5400000,
                'Emina': 19200000,
                'Eucerin': 2300000,
                'Garnier': 8700000,
                'Gatsby': 5600000,
                'Glow & Lovely': 4200000,
                'Kahf': 1600000,
                "L'Oreal Paris": 7600000,
                'Make Over': 12500000,
                'Maybelline': 8200000,
                'Mustika Ratu': 3600000,
                'Nivea': 11500000,
                'Pixy': 9900000,
                "Pond's": 8600000,
                'Sariayu': 1600000,
                'Vaseline': 6500000,
                'Wardah': 39800000},
 'NURUL BATUBARA': {'Biore': 4600000,
                    'Citra': 10800000,
                    'Emina': 42500000,
                    'Eucerin': 1600000,
                    'Garnier': 16900000,
                    'Gatsby': 13700000,
                    'Glow & Lovely': 6500000,
                    'Kahf': 5600000,
                    "L'Oreal Paris": 8100000,
                    'Make Over': 27000000,
                    'Maybelline': 28000000,
                    'Mustika Ratu': 5300000,
                    'Nivea': 21900000,
                    'Pixy': 22500000,
                    "Pond's": 26400000,
                    'Sariayu': 5700000,
                    'Vaseline': 15500000,
                    'Wardah': 68800000},
 'NURUL LESMANA': {'Biore': 7400000,
                   'Citra': 16100000,
                   'Emina': 81600000,
                   'Eucerin': 2600000,
                   'Garnier': 28200000,
                   'Gatsby': 22700000,
                   'Glow & Lovely': 11800000,
                   'Kahf': 13800000,
                   "L'Oreal Paris": 14700000,
                   'Make Over': 46900000,
                   'Maybelline': 46800000,
                   'Mustika Ratu': 7400000,
                   'Nivea': 44400000,
                   'Pixy': 39600000,
                   "Pond's": 40200000,
                   'Sariayu': 7600000,
                   'Vaseline': 23900000,
                   'Wardah': 123700000},
 'NURUL NUGROHO': {'Biore': 6900000,
                   'Citra': 15700000,
                   'Emina': 53000000,
                   'Eucerin': 1400000,
                   'Garnier': 23900000,
                   'Gatsby': 19000000,
                   'Glow & Lovely': 11300000,
                   'Kahf': 9800000,
                   "L'Oreal Paris": 17100000,
                   'Make Over': 34300000,
                   'Maybelline': 45700000,
                   'Mustika Ratu': 5000000,
                   'Nivea': 24600000,
                   'Pixy': 27900000,
                   "Pond's": 27900000,
                   'Sariayu': 5400000,
                   'Vaseline': 19300000,
                   'Wardah': 90300000},
 'PUTRA MAULANA': {'Biore': 6000000,
                   'Citra': 13600000,
                   'Emina': 59500000,
                   'Eucerin': 3000000,
                   'Garnier': 31300000,
                   'Gatsby': 19900000,
                   'Glow & Lovely': 11200000,
                   'Kahf': 7700000,
                   "L'Oreal Paris": 13200000,
                   'Make Over': 35600000,
                   'Maybelline': 38200000,
                   'Mustika Ratu': 6400000,
                   'Nivea': 27800000,
                   'Pixy': 33200000,
                   "Pond's": 34800000,
                   'Sariayu': 8500000,
                   'Vaseline': 19700000,
                   'Wardah': 101200000},
 'RAHMAT NASUTION': {'Biore': 4200000,
                     'Citra': 9400000,
                     'Emina': 38300000,
                     'Eucerin': 2500000,
                     'Garnier': 15400000,
                     'Gatsby': 10100000,
                     'Glow & Lovely': 4100000,
                     'Kahf': 4500000,
                     "L'Oreal Paris": 9600000,
                     'Make Over': 25400000,
                     'Maybelline': 25000000,
                     'Mustika Ratu': 4300000,
                     'Nivea': 21400000,
                     'Pixy': 22000000,
                     "Pond's": 21900000,
                     'Sariayu': 4600000,
                     'Vaseline': 12100000,
                     'Wardah': 60500000},
 'RAHMAT PERMANA': {'Biore': 4800000,
                    'Citra': 9300000,
                    'Emina': 44300000,
                    'Eucerin': 400000,
                    'Garnier': 17900000,
                    'Gatsby': 11100000,
                    'Glow & Lovely': 6100000,
                    'Kahf': 7200000,
                    "L'Oreal Paris": 14100000,
                    'Make Over': 16600000,
                    'Maybelline': 28500000,
                    'Mustika Ratu': 3600000,
                    'Nivea': 23000000,
                    'Pixy': 17800000,
                    "Pond's": 23500000,
                    'Sariayu': 5100000,
                    'Vaseline': 11000000,
                    'Wardah': 63400000},
 'RATNA HARTONO': {'Biore': 13900000,
                   'Citra': 21600000,
                   'Emina': 102900000,
                   'Eucerin': 2700000,
                   'Garnier': 44400000,
                   'Gatsby': 33700000,
                   'Glow & Lovely': 15100000,
                   'Kahf': 18100000,
                   "L'Oreal Paris": 34600000,
                   'Make Over': 59800000,
                   'Maybelline': 75100000,
                   'Mustika Ratu': 8900000,
                   'Nivea': 47400000,
                   'Pixy': 46700000,
                   "Pond's": 55100000,
                   'Sariayu': 12600000,
                   'Vaseline': 27600000,
                   'Wardah': 170000000},
 'RINA NUGROHO': {'Biore': 8200000,
                  'Citra': 12300000,
                  'Emina': 63300000,
                  'Eucerin': 6200000,
                  'Garnier': 25700000,
                  'Gatsby': 21400000,
                  'Glow & Lovely': 9300000,
                  'Kahf': 10000000,
                  "L'Oreal Paris": 7500000,
                  'Make Over': 36700000,
                  'Maybelline': 38000000,
                  'Mustika Ratu': 5200000,
                  'Nivea': 25700000,
                  'Pixy': 27200000,
                  "Pond's": 31700000,
                  'Sariayu': 8300000,
                  'Vaseline': 20100000,
                  'Wardah': 102100000},
 'RUDI MAULANA': {'Biore': 10500000,
                  'Citra': 18800000,
                  'Emina': 84400000,
                  'Eucerin': 7200000,
                  'Garnier': 32700000,
                  'Gatsby': 23700000,
                  'Glow & Lovely': 10500000,
                  'Kahf': 17500000,
                  "L'Oreal Paris": 21700000,
                  'Make Over': 43000000,
                  'Maybelline': 54700000,
                  'Mustika Ratu': 9200000,
                  'Nivea': 41100000,
                  'Pixy': 39000000,
                  "Pond's": 48600000,
                  'Sariayu': 10900000,
                  'Vaseline': 29300000,
                  'Wardah': 134700000},
 'SARI FIRMANSYAH': {'Biore': 3800000,
                     'Citra': 8100000,
                     'Emina': 43400000,
                     'Eucerin': 1100000,
                     'Garnier': 15400000,
                     'Gatsby': 11800000,
                     'Glow & Lovely': 6500000,
                     'Kahf': 6900000,
                     "L'Oreal Paris": 9200000,
                     'Make Over': 24500000,
                     'Maybelline': 31300000,
                     'Mustika Ratu': 3000000,
                     'Nivea': 19900000,
                     'Pixy': 21600000,
                     "Pond's": 26300000,
                     'Sariayu': 5800000,
                     'Vaseline': 11800000,
                     'Wardah': 69100000},
 'SITI KUSUMA': {'Biore': 9500000,
                 'Citra': 18200000,
                 'Emina': 98000000,
                 'Eucerin': 4000000,
                 'Garnier': 37300000,
                 'Gatsby': 27500000,
                 'Glow & Lovely': 14600000,
                 'Kahf': 16000000,
                 "L'Oreal Paris": 19000000,
                 'Make Over': 59000000,
                 'Maybelline': 67400000,
                 'Mustika Ratu': 9200000,
                 'Nivea': 50100000,
                 'Pixy': 42900000,
                 "Pond's": 47800000,
                 'Sariayu': 13400000,
                 'Vaseline': 31200000,
                 'Wardah': 153600000},
 'TAUFIK LESMANA': {'Biore': 7000000,
                    'Citra': 12200000,
                    'Emina': 61000000,
                    'Eucerin': 1900000,
                    'Garnier': 21700000,
                    'Gatsby': 16700000,
                    'Glow & Lovely': 11000000,
                    'Kahf': 11100000,
                    "L'Oreal Paris": 13600000,
                    'Make Over': 44300000,
                    'Maybelline': 39500000,
                    'Mustika Ratu': 6100000,
                    'Nivea': 37900000,
                    'Pixy': 32500000,
                    "Pond's": 35100000,
                    'Sariayu': 9100000,
                    'Vaseline': 20600000,
                    'Wardah': 114100000},
 'TONO NUGROHO': {'Biore': 7700000,
                  'Citra': 16200000,
                  'Emina': 73900000,
                  'Eucerin': 5100000,
                  'Garnier': 29700000,
                  'Gatsby': 26000000,
                  'Glow & Lovely': 10000000,
                  'Kahf': 13900000,
                  "L'Oreal Paris": 17100000,
                  'Make Over': 45700000,
                  'Maybelline': 51200000,
                  'Mustika Ratu': 8100000,
                  'Nivea': 41800000,
                  'Pixy': 37600000,
                  "Pond's": 42000000,
                  'Sariayu': 9500000,
                  'Vaseline': 22900000,
                  'Wardah': 125000000},
 'USMAN NUGROHO': {'Biore': 5700000,
                   'Citra': 9800000,
                   'Emina': 48100000,
                   'Eucerin': 700000,
                   'Garnier': 21100000,
                   'Gatsby': 14400000,
                   'Glow & Lovely': 6500000,
                   'Kahf': 9100000,
                   "L'Oreal Paris": 11700000,
                   'Make Over': 30500000,
                   'Maybelline': 31400000,
                   'Mustika Ratu': 5400000,
                   'Nivea': 29700000,
                   'Pixy': 21600000,
                   "Pond's": 30000000,
                   'Sariayu': 4400000,
                   'Vaseline': 13100000,
                   'Wardah': 81400000},
 'VINA SANTOSO': {'Biore': 2800000,
                  'Citra': 6700000,
                  'Emina': 35000000,
                  'Eucerin': 2000000,
                  'Garnier': 14400000,
                  'Gatsby': 9500000,
                  'Glow & Lovely': 5000000,
                  'Kahf': 5900000,
                  "L'Oreal Paris": 5500000,
                  'Make Over': 16000000,
                  'Maybelline': 21900000,
                  'Mustika Ratu': 2800000,
                  'Nivea': 16500000,
                  'Pixy': 19500000,
                  "Pond's": 15400000,
                  'Sariayu': 5000000,
                  'Vaseline': 8900000,
                  'Wardah': 57300000},
 'VINA SUSANTO': {'Biore': 4200000,
                  'Citra': 7400000,
                  'Emina': 37500000,
                  'Eucerin': 1800000,
                  'Garnier': 14800000,
                  'Gatsby': 9500000,
                  'Glow & Lovely': 6700000,
                  'Kahf': 7500000,
                  "L'Oreal Paris": 8200000,
                  'Make Over': 18800000,
                  'Maybelline': 21800000,
                  'Mustika Ratu': 4500000,
                  'Nivea': 23500000,
                  'Pixy': 19100000,
                  "Pond's": 21600000,
                  'Sariayu': 4300000,
                  'Vaseline': 14900000,
                  'Wardah': 65300000},
 'WAHYU DAMANIK': {'Biore': 11100000,
                   'Citra': 20000000,
                   'Emina': 92500000,
                   'Eucerin': 2800000,
                   'Garnier': 38000000,
                   'Gatsby': 28800000,
                   'Glow & Lovely': 12700000,
                   'Kahf': 10000000,
                   "L'Oreal Paris": 24600000,
                   'Make Over': 48200000,
                   'Maybelline': 61400000,
                   'Mustika Ratu': 9000000,
                   'Nivea': 51400000,
                   'Pixy': 39600000,
                   "Pond's": 52900000,
                   'Sariayu': 8400000,
                   'Vaseline': 25800000,
                   'Wardah': 148500000},
 'YOGA HAKIM': {'Biore': 6000000,
                'Citra': 17000000,
                'Emina': 57700000,
                'Eucerin': 1800000,
                'Garnier': 22600000,
                'Gatsby': 11700000,
                'Glow & Lovely': 7400000,
                'Kahf': 11500000,
                "L'Oreal Paris": 12700000,
                'Make Over': 30800000,
                'Maybelline': 37300000,
                'Mustika Ratu': 6700000,
                'Nivea': 31500000,
                'Pixy': 29900000,
                "Pond's": 30400000,
                'Sariayu': 6200000,
                'Vaseline': 18600000,
                'Wardah': 102900000},
 'ZULKIFLI GUNADI': {'Biore': 10700000,
                     'Citra': 21800000,
                     'Emina': 91600000,
                     'Eucerin': 5100000,
                     'Garnier': 40200000,
                     'Gatsby': 28300000,
                     'Glow & Lovely': 13800000,
                     'Kahf': 16800000,
                     "L'Oreal Paris": 24400000,
                     'Make Over': 46400000,
                     'Maybelline': 60500000,
                     'Mustika Ratu': 10700000,
                     'Nivea': 45700000,
                     'Pixy': 43400000,
                     "Pond's": 54100000,
                     'Sariayu': 10300000,
                     'Vaseline': 25600000,
                     'Wardah': 151900000},
 'ZULKIFLI RAHMAN': {'Biore': 4900000,
                     'Citra': 11000000,
                     'Emina': 54200000,
                     'Eucerin': 2900000,
                     'Garnier': 16100000,
                     'Gatsby': 16900000,
                     'Glow & Lovely': 8600000,
                     'Kahf': 9900000,
                     "L'Oreal Paris": 10600000,
                     'Make Over': 24000000,
                     'Maybelline': 28600000,
                     'Mustika Ratu': 5300000,
                     'Nivea': 25100000,
                     'Pixy': 22900000,
                     "Pond's": 25000000,
                     'Sariayu': 7100000,
                     'Vaseline': 13500000,
                     'Wardah': 83400000},
 'ZULKIFLI SIREGAR': {'Biore': 7300000,
                      'Citra': 16400000,
                      'Emina': 68500000,
                      'Eucerin': 1800000,
                      'Garnier': 24800000,
                      'Gatsby': 19700000,
                      'Glow & Lovely': 7600000,
                      'Kahf': 13500000,
                      "L'Oreal Paris": 17900000,
                      'Make Over': 36700000,
                      'Maybelline': 46400000,
                      'Mustika Ratu': 8000000,
                      'Nivea': 38100000,
                      'Pixy': 36800000,
                      "Pond's": 34900000,
                      'Sariayu': 10900000,
                      'Vaseline': 26600000,
                      'Wardah': 115800000}}

TARGET_EC_HARIAN = {
}

SUPERVISOR_TOTAL_TARGETS = {k: sum(v.values()) for k, v in TARGET_DATABASE.items()}
TARGET_NASIONAL_VAL = sum(SUPERVISOR_TOTAL_TARGETS.values())

BRAND_ALIASES = {
}

SALES_MAPPING = {
}

# =========================================================================
# 🔧 KONFIGURASI DINAMIS (BARU)
# - Daftar MERK & SALES dibaca otomatis dari isi data (unique values).
# - Target diisi langsung pada TARGET_DATABASE (per brand) & INDIVIDUAL_TARGETS (per sales) di atas.
# =========================================================================

# Opsional: nama di kolom Penjualan yang HARUS dianggap Non-Sales (mis. admin/fakturis).
# Kosongkan jika tidak ada. Contoh: SALES_EXCLUDE = ["ERNI ST"]
SALES_EXCLUDE = []

# Nama file cache data. Dinaikkan versinya agar cache lama (hasil kode sebelumnya) otomatis tidak terpakai.
PARQUET_FILE = "master_database_penjualan_v2.parquet"

def log_activity(user, action):
    log_file = 'audit_log.csv'
    timestamp = get_current_time_wib().strftime("%Y-%m-%d %H:%M:%S")
    new_log = pd.DataFrame([[timestamp, user, action]], columns=['Timestamp', 'User', 'Action'])
    if not os.path.isfile(log_file): new_log.to_csv(log_file, index=False)
    else: new_log.to_csv(log_file, mode='a', header=False, index=False)

def get_current_time_wib():
    return datetime.datetime.now(pytz.timezone('Asia/Jakarta'))

def format_idr(value):
    try: return f"Rp {value:,.0f}".replace(",", ".")
    except: return "Rp 0"

def generate_daily_token():
    try: secret_salt = st.secrets["APP_SALT"]
    except: secret_salt = "RAHASIA_PERUSAHAAN_2025" 
    now_wib = get_current_time_wib()
    time_key = now_wib.strftime("%Y-%m-%d-%p")
    raw_string = f"{time_key}-{secret_salt}"
    hash_object = hashlib.sha256(raw_string.encode())
    hex_dig = hash_object.hexdigest()
    numeric_filter = filter(str.isdigit, hex_dig)
    numeric_string = "".join(numeric_filter)
    return numeric_string[:4].ljust(4, '0')

def render_custom_progress(title, current, target):
    if not target:
        st.caption(f"🎯 {title}: target belum diinput (Realisasi {format_idr(current)}). Isi di TARGET_DATABASE / INDIVIDUAL_TARGETS pada kode.")
        return
    pct = (current / target) * 100
    visual_pct = min(pct, 100)
    
    if pct < 50: bar_color = "linear-gradient(90deg, #ff4b4b, #e74c3c)" 
    elif 50 <= pct < 85: bar_color = "linear-gradient(90deg, #f1c40f, #f39c12)" 
    else: bar_color = "linear-gradient(90deg, #2ecc71, #27ae60)" 
    
    st.markdown(f"""
    <div style="margin-bottom: 20px; background-color: #fcfcfc; padding: 15px; border-radius: 12px; border: 1px solid #eee; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
        <div style="display: flex; justify-content: space-between; margin-bottom: 8px;">
            <span style="font-weight: 700; font-size: 15px; color: #34495e;">{title}</span>
            <span style="font-weight: 600; color: #555; font-size: 14px;">{format_idr(current)} <span style="color:#999; font-weight:normal;">/ {format_idr(target)}</span></span>
        </div>
        <div style="width: 100%; background-color: #ecf0f1; border-radius: 20px; height: 26px; position: relative; overflow: hidden;">
            <div style="width: {visual_pct}%; background: {bar_color}; height: 100%; border-radius: 20px; transition: width 0.8s ease;"></div>
            <div style="position: absolute; top: 0; left: 0; width: 100%; height: 100%;
                        display: flex; align-items: center; justify-content: center;
                        z-index: 10; font-weight: 800; font-size: 13px; color: #222;
                        text-shadow: 0px 0px 4px #fff;">
                {pct:.1f}%
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

@st.cache_data(ttl=43200) 
def load_data_from_url():
    
    urls = [
        "https://docs.google.com/spreadsheets/d/e/2PACX-1vSh15AcgdIN3Jww24W_8OTDNHkIf2jzUOodA1TZSWDYbtkb2hqq494WQzHdYeoABz88SQ2t2c7x7x7a/pub?output=csv"
    ]
    def fetch_url(url):
        if url.strip() != "" and url.startswith("http") and "LINK_SHEET" not in url:
            try:
                url_with_ts = f"{url}&t={int(time.time())}"
                return pd.read_csv(url_with_ts, dtype=str, on_bad_lines='skip')
            except Exception as e:
                try:
                    return pd.read_csv(url, dtype=str, on_bad_lines='skip')
                except Exception as e2:
                    print(f"⚠️ Gagal menarik data dari {url}. Detail Error: {e2}")
                    return None
        return None

    all_dfs = []
    with concurrent.futures.ThreadPoolExecutor() as executor:
        results = executor.map(fetch_url, urls)
        for res in results:
            if res is not None and not res.empty:
                all_dfs.append(res)
                
    if not all_dfs: return None
        
    df = pd.concat(all_dfs, ignore_index=True)
    df.columns = df.columns.str.strip()
    
    # --- 1. PENYATUAN NAMA KOLOM BRUTAL ---
    def gabungkan_kolom(df, target_name, aliases):
        if target_name not in df.columns: df[target_name] = np.nan
        for alias in aliases:
            matched_cols = [c for c in df.columns if c.upper() == alias.upper() and c != target_name]
            for match in matched_cols:
                df[target_name] = df[target_name].fillna(df[match])
        return df

    df = gabungkan_kolom(df, 'Nama Outlet', ['NAMA CUSTOMER', 'CUSTOMER', 'NAMA TOKO', 'TOKO', 'PELANGGAN', 'OUTLET', 'NAMA OUTLET'])
    df = gabungkan_kolom(df, 'Kode_Global', ['KODE CUSTOMER', 'KODE COSTUMER', 'KODE OUTLET', 'KODE TOKO', 'KODE GLOBAL', 'KODE_GLOBAL'])
    df = gabungkan_kolom(df, 'Penjualan', ['SALES', 'SALESMAN', 'NAMA SALES', 'PENJUALAN'])
    df = gabungkan_kolom(df, 'Merk', ['BRAND', 'PRODUK', 'MERK'])
    df = gabungkan_kolom(df, 'Qty', ['QTY', 'KUANTITAS', 'QUANTITY'])
    
    faktur_cols = [c for c in df.columns if 'faktur' in c.lower() or 'bukti' in c.lower() or 'invoice' in c.lower()]
    if faktur_cols: df = gabungkan_kolom(df, 'No Faktur', faktur_cols)

    # --- 2. PEMBERSIHAN DATA DASAR ---
    if 'Kode_Global' not in df.columns: df['Kode_Global'] = "-"
    
    if 'Nama Barang' in df.columns:
        df = df[~df['Nama Barang'].astype(str).str.match(r'^(Total|Jumlah)', case=False, na=False)]
        df['Nama Barang'] = df['Nama Barang'].fillna("-")

    if 'Nama Outlet' in df.columns:
        df = df[~df['Nama Outlet'].astype(str).str.match(r'^(Total|Jumlah|Subtotal|Grand|Rekap)', case=False, na=False)]
        df['Nama Outlet'] = df['Nama Outlet'].fillna("-")
        df = df[df['Nama Outlet'].astype(str).str.strip() != ''] 
        df = df[df['Nama Outlet'].astype(str).str.lower() != 'nan']
    else:
        df['Nama Outlet'] = "-" 

    def clean_rupiah(x):
        s = str(x).upper().replace('RP', '').replace(' ', '').strip()
        if not s or s == '-': return 0.0
        is_negative = False
        if (s.startswith('(') and s.endswith(')')) or s.startswith('-') or s.endswith('-') or s.startswith('–') or s.startswith('—'):
            is_negative = True
        s = re.sub(r'[,.]\d{2}$', '', s) 
        s = re.sub(r'[^\d]', '', s) 
        try: 
            val = float(s)
            return -val if is_negative else val
        except: 
            return 0.0

    if 'Qty' in df.columns:
        df['Qty'] = pd.to_numeric(df['Qty'].astype(str).str.replace(r'[^\d.-]', '', regex=True), errors='coerce').fillna(0)
    else:
        df['Qty'] = 0.0

    if 'Jumlah' in df.columns: df['Jumlah'] = df['Jumlah'].apply(clean_rupiah)
    else: df['Jumlah'] = 0.0

    if 'Tanggal' in df.columns:
        tanggal_raw = df['Tanggal'].astype(str).str.strip()
        d1 = pd.to_datetime(tanggal_raw, format='%d/%m/%Y', errors='coerce')
        d2 = pd.to_datetime(tanggal_raw, format='%d-%m-%Y', errors='coerce')
        d3 = pd.to_datetime(tanggal_raw, dayfirst=True, errors='coerce', format='mixed')
        # BARU: format ISO (yyyy-mm-dd) dibaca lebih dulu agar hari & bulan tidak tertukar oleh dayfirst=True
        d0 = pd.to_datetime(tanggal_raw.str[:10], format='%Y-%m-%d', errors='coerce')
        df['Tanggal'] = d0.fillna(d1).fillna(d2).fillna(d3).fillna(pd.to_datetime('2000-01-01'))
    else:
        df['Tanggal'] = pd.to_datetime('2000-01-01')

    if 'Penjualan' in df.columns:
        # --- KAMERA PENGINTAI AUDIT MENTAH ---
        df['Sales_Asli_Fakturis'] = df['Penjualan'].copy()
        
        # 1. Ambil daftar sales resmi OTOMATIS dari isi data (unique values), digabung nama yang ada di target
        _blank_sales = {'NAN', 'NONE', '', '-', '0', '0.0'}
        _raw_names = df['Penjualan'].dropna().astype(str).str.upper().str.strip().unique()
        _data_names = {SALES_MAPPING.get(n, n) for n in _raw_names}
        _exclude_names = {str(n).upper().strip() for n in SALES_EXCLUDE}
        valid_sales_names = sorted((_data_names | set(INDIVIDUAL_TARGETS.keys()) | set(TARGET_DATABASE.keys())) - _blank_sales - _exclude_names)
        valid_sales_set = set(valid_sales_names)
        valid_sales_names_sorted = sorted(valid_sales_names, key=len, reverse=True)
        
        # 2. Fungsi Mesin Pelacak Cerdas
        def smart_sales_mapper(raw_val):
            raw_upper = str(raw_val).upper().strip()
            # Hanya tangkap yang benar-benar kosong
            if raw_upper in ['NAN', 'NONE', '', '-', '0', '0.0']: return 'Non-Sales'
            if raw_upper in SALES_MAPPING: raw_upper = SALES_MAPPING[raw_upper]
            if raw_upper in valid_sales_set: return raw_upper  # jalur cepat: sudah nama resmi
            for official_name in valid_sales_names_sorted:
                pattern = r'\b' + re.escape(official_name) + r'\b'
                if re.search(pattern, raw_upper): return official_name
            return raw_upper # Erni ST tetap akan keluar sebagai 'ERNI ST' di tahap ini
            
        # Mapper dijalankan pada nilai unik saja (hasil sama, jauh lebih cepat untuk data besar)
        _sales_str = df['Penjualan'].astype(str)
        _sales_cache = {v: smart_sales_mapper(v) for v in _sales_str.unique()}
        df['Penjualan'] = _sales_str.map(_sales_cache)
        
        # --- KAMERA PENGINTAI STATUS SEBELUM AUTO-HEALING ---
        df['Status_Awal_Mesin'] = df['Penjualan'].copy()
        
        # --- 3. AUTO-HEALING CERDAS DULUAN ---
        # Hanya menyembuhkan baris yang murni kosong ('Non-Sales'), Erni ST aman karena belum dilabeli Non-Sales
        df_valid = df[df['Penjualan'].isin(valid_sales_names)]
        outlet_to_sales = df_valid.groupby('Nama Outlet')['Penjualan'].first().to_dict()
        mask_non = df['Penjualan'] == 'Non-Sales'
        df.loc[mask_non, 'Penjualan'] = df.loc[mask_non, 'Nama Outlet'].map(outlet_to_sales).fillna('Non-Sales')
        
        # --- 4. SAPU BERSIH NAMA ASING ---
        # Setelah penyembuhan selesai, buang Erni ST (dan nama di luar target lainnya) ke Non-Sales agar dasbor tetap bersih
        df.loc[~df['Penjualan'].isin(valid_sales_names), 'Penjualan'] = 'Non-Sales'
        
        df['Penjualan'] = df['Penjualan'].astype('category')
    else:
        df['Penjualan'] = 'Non-Sales'
        df['Sales_Asli_Fakturis'] = '-'
        df['Status_Awal_Mesin'] = 'Non-Sales'

    def normalize_brand(raw_brand):
        raw_upper = str(raw_brand).upper()
        for target_brand, keywords in BRAND_ALIASES.items(): 
            for keyword in keywords:
                if keyword in raw_upper: return target_brand
        return raw_brand
        
    if 'Merk' in df.columns:
        df['Merk'] = df['Merk'].fillna("-").astype(str).str.strip().replace({'': '-', 'nan': '-', 'NaN': '-', 'None': '-'}).apply(normalize_brand)
        # BARU: samakan variasi penulisan merk (besar/kecil huruf, spasi ganda) ke ejaan yang paling sering muncul di data
        _brand_key = df['Merk'].str.upper().str.replace(r'\s+', ' ', regex=True)
        _brand_canon = df.groupby(_brand_key)['Merk'].agg(lambda s: s.value_counts().idxmax())
        df['Merk'] = _brand_key.map(_brand_canon).astype('category')
    else:
        df['Merk'] = "-"
    
    cols_to_convert = ['Kota', 'Nama Outlet', 'No Faktur', 'Kode_Global']
    for col in cols_to_convert:
        if col in df.columns: 
            df[col] = df[col].fillna("-").astype(str).str.strip()
            df[col] = df[col].replace({'nan': '-', 'NaN': '-', '0.0': '-', 'None': '-', '': '-'})

    # --- 3. RADAR DETEKTIF PROVINSI ---
    df = gabungkan_kolom(df, 'Provinsi', ['PROPINSI', 'PROVINCE', 'PROV', 'WILAYAH'])
    df = gabungkan_kolom(df, 'Kota', ['CITY', 'KABUPATEN', 'KAB', 'DAERAH', 'LOKASI'])

    def determine_province(row):
        valid_provinces = {
            "ACEH", "SUMATERA UTARA", "SUMATERA BARAT", "RIAU", "KEPULAUAN RIAU", 
            "JAMBI", "SUMATERA SELATAN", "BANGKA BELITUNG", "BENGKULU", "LAMPUNG", 
            "DKI JAKARTA", "JAWA BARAT", "BANTEN", "JAWA TENGAH", "DI YOGYAKARTA", 
            "JAWA TIMUR", "BALI", "NUSA TENGGARA BARAT", "NUSA TENGGARA TIMUR", 
            "KALIMANTAN BARAT", "KALIMANTAN TENGAH", "KALIMANTAN SELATAN", "KALIMANTAN TIMUR", 
            "KALIMANTAN UTARA", "SULAWESI UTARA", "SULAWESI TENGAH", "SULAWESI SELATAN", 
            "SULAWESI TENGGARA", "SULAWESI BARAT", "GORONTALO", "MALUKU", "MALUKU UTARA", 
            "PAPUA BARAT", "PAPUA", "PAPUA SELATAN", "PAPUA TENGAH", "PAPUA PEGUNUNGAN", "PAPUA BARAT DAYA"
        }
        abbreviations = {
            "SUMUT": "SUMATERA UTARA", "SUMBAR": "SUMATERA BARAT", "KEPRI": "KEPULAUAN RIAU",
            "SUMSEL": "SUMATERA SELATAN", "BABEL": "BANGKA BELITUNG", "DKI": "DKI JAKARTA",
            "JAKARTA": "DKI JAKARTA", "JABAR": "JAWA BARAT", "JATENG": "JAWA TENGAH",
            "DIY": "DI YOGYAKARTA", "JOGJA": "DI YOGYAKARTA", "YOGYAKARTA": "DI YOGYAKARTA",
            "JATIM": "JAWA TIMUR", "NTB": "NUSA TENGGARA BARAT", "NTT": "NUSA TENGGARA TIMUR",
            "KALBAR": "KALIMANTAN BARAT", "KALTENG": "KALIMANTAN TENGAH", "KALSEL": "KALIMANTAN SELATAN",
            "KALTIM": "KALIMANTAN TIMUR", "KALUT": "KALIMANTAN UTARA", "SULUT": "SULAWESI UTARA",
            "SULTENG": "SULAWESI TENGAH", "SULSEL": "SULAWESI SELATAN", "SULTRA": "SULAWESI TENGGARA",
            "SULBAR": "SULAWESI BARAT", "MALUT": "MALUKU UTARA", "NAD": "ACEH"
        }
        
        p_raw = str(row.get('Provinsi', '')).strip().upper()
        c_raw = str(row.get('Kota', '')).strip().upper()
        o_raw = str(row.get('Nama Outlet', '')).strip().upper()
        
        if p_raw in ['NAN', '-', 'NONE', '0']: p_raw = ""
        if c_raw in ['NAN', '-', 'NONE', '0']: c_raw = ""
        if o_raw in ['NAN', '-', 'NONE', '0']: o_raw = ""

        # 1. JALAN PINTAS (DICTIONARY KHUSUS DARI NAMA OUTLET/KOTA)
        # Sangat ampuh untuk menindaklanjuti data "LAIN-LAIN"
        shortcut_map = {
            "USU": "SUMATERA UTARA", "MARELAN": "SUMATERA UTARA", "TEMBUNG": "SUMATERA UTARA", "SOLO MART": "SUMATERA UTARA",
            "BELAWAN": "SUMATERA UTARA", "KRAKATAU": "SUMATERA UTARA", "DOLOK SANGGUL": "SUMATERA UTARA",
            "PENYABUNGAN": "SUMATERA UTARA", "CIKAMPAK": "SUMATERA UTARA", "R.PRAPAT": "SUMATERA UTARA",
            "MENCIRIM": "SUMATERA UTARA", "SAMBAS": "SUMATERA UTARA", "LINTONG LINTA": "SUMATERA UTARA",
            "K. TANJUNG": "SUMATERA UTARA", "K.TANJUNG": "SUMATERA UTARA", "INDRAPURA": "SUMATERA UTARA",
            "P. PASAR": "SUMATERA UTARA", "P.BESI": "SUMATERA UTARA", "P. BESI": "SUMATERA UTARA",
            "TEBING": "SUMATERA UTARA", "LHOKSUKON": "ACEH", "CALANG": "ACEH", "LANGSA": "ACEH", 
            "BANDA ACEH": "ACEH", "BIREUEN": "ACEH", "BAGAN BATU": "SUMATERA UTARA", "B.BATU": "SUMATERA UTARA"
        }
        
        for key, prov in shortcut_map.items():
            if f" {key} " in f" {o_raw} " or f" {key} " in f" {c_raw} " or o_raw.endswith(f"({key})") or o_raw.endswith(f"( {key} )"):
                return prov

        # 2. CITY-FIRST OVERRIDE (Paksa Kota menjadi Patokan Mutlak)
        if c_raw:
            for prov_name, cities in PROVINCE_MAPPING.items():
                if c_raw in cities: return prov_name
            for prov_name, cities in PROVINCE_MAPPING.items():
                for city in cities:
                    if f" {city} " in f" {c_raw} " or c_raw.startswith(f"{city} ") or c_raw.endswith(f" {city}"):
                        return prov_name

        # 3. CEK PROVINSI (Hanya jika Kota tidak valid)
        if p_raw:
            if p_raw in valid_provinces: return p_raw
            if p_raw in abbreviations: return abbreviations[p_raw]
            try:
                import difflib
                matches = difflib.get_close_matches(p_raw, valid_provinces, n=1, cutoff=0.8)
                if matches: return matches[0]
            except: pass

        # 4. TEROPONG NAMA OUTLET (Fallback Terakhir)
        if o_raw:
            for prov_name, cities in PROVINCE_MAPPING.items():
                for city in cities:
                    if len(city) >= 4: 
                        if f" {city} " in f" {o_raw} " or o_raw.endswith(f" {city}") or o_raw.startswith(f"{city} "):
                            return prov_name
                            
        return "LAIN-LAIN"

    df['Provinsi'] = df.apply(determine_province, axis=1)

    # --- 4. AUTO-HEALING CERDAS (Kode Spesifik per Brand) ---
    df['Nama_Pencocokan'] = df['Nama Outlet'].astype(str).str.strip().str.upper()
    df['Kunci_Kode'] = list(zip(df['Nama_Pencocokan'], df['Merk']))
    
    # MENGGUNAKAN .last() AGAR SISTEM MENJADIKAN DATA ENTRY TERBARU SEBAGAI KIBLAT KEBENARAN
    valid_kodes = df[~df['Kode_Global'].isin(['-', '', 'NAN', 'NONE', '0.0', '0'])].groupby('Kunci_Kode')['Kode_Global'].last()
    df['Kode_Global'] = df['Kunci_Kode'].map(valid_kodes).fillna(df['Kode_Global'])
    
    valid_provs = df[~df['Provinsi'].isin(['-', '', 'LAIN-LAIN', 'NAN', 'NONE'])].groupby('Nama_Pencocokan')['Provinsi'].last()
    valid_kotas = df[~df['Kota'].isin(['-', '', 'NAN', 'NONE'])].groupby('Nama_Pencocokan')['Kota'].last()
    
    df['Provinsi'] = df['Nama_Pencocokan'].map(valid_provs).fillna(df['Provinsi'])
    df['Kota'] = df['Nama_Pencocokan'].map(valid_kotas).fillna(df['Kota'])
    
    df = df.drop(columns=['Nama_Pencocokan', 'Kunci_Kode'])
    
    try: df.to_parquet(PARQUET_FILE, index=False)
    except: pass 
            
    return df

def load_data(fast_mode=False):
    if fast_mode and os.path.exists(PARQUET_FILE):
        try:
            df_cached = pd.read_parquet(PARQUET_FILE)
            # BARU: cache dianggap rusak bila hampir semua sales tercatat 'Non-Sales' -> baca ulang dari sumber
            if not (len(df_cached) > 0 and (df_cached['Penjualan'].astype(str) == 'Non-Sales').mean() > 0.9):
                return df_cached
        except Exception:
            pass
    return load_data_from_url()

# =========================================================================
# PIVOT FAST ENGINE (MENDUKUNG VALUE & QTY)
# =========================================================================
def generate_pivot_fast(df_pivot_source, selected_merk_excel, selected_tahun_excel_tuple, group_cols_tuple, metric_choice):
    group_cols = list(group_cols_tuple)

    if not df_pivot_source.empty:
        if selected_merk_excel != "SEMUA":
            final_mask = df_pivot_source['Merk'] == selected_merk_excel
        else:
            final_mask = pd.Series(True, index=df_pivot_source.index)
            
        df_filtered = df_pivot_source[final_mask].copy()
        if df_filtered.empty: return pd.DataFrame()

        cols_to_keep = group_cols.copy()
        if 'Nama Outlet' not in cols_to_keep:
            cols_to_keep.append('Nama Outlet')
        if 'Merk' not in cols_to_keep:
            cols_to_keep.append('Merk')

        df_excel = df_filtered[df_filtered['Tanggal'].dt.year.isin(selected_tahun_excel_tuple)].copy()
        
        if not df_excel.empty:
            df_excel['Bulan Angka'] = df_excel['Tanggal'].dt.month
            
            if metric_choice == "Value & QTY":
                pivot_sales = pd.pivot_table(df_excel, values=['Qty', 'Jumlah'], index=['Nama Outlet', 'Merk'], columns='Bulan Angka', aggfunc='sum', fill_value=0)
                flat_cols = [f"{col[1]}_{'Value' if col[0]=='Jumlah' else 'QTY'}" for col in pivot_sales.columns]
                pivot_sales.columns = flat_cols
                pivot_sales = pivot_sales.reset_index()
            else:
                val_col = 'Jumlah' if metric_choice == "Value (Omset)" else 'Qty'
                pivot_sales = pd.pivot_table(df_excel, values=val_col, index=['Nama Outlet', 'Merk'], columns='Bulan Angka', aggfunc='sum', fill_value=0).reset_index()
                
            df_sorted = df_filtered.sort_values(by=['Nama Outlet', 'Kode_Global'], ascending=[True, False])
            base_customers = df_sorted.drop_duplicates(subset=['Nama Outlet', 'Merk'], keep='first')[cols_to_keep]
            master_pivot = pd.merge(base_customers, pivot_sales, on=['Nama Outlet', 'Merk'], how='left').fillna(0)
            
            if metric_choice == "Value & QTY":
                for i in range(1, 13):
                    if f"{i}_QTY" not in master_pivot.columns: master_pivot[f"{i}_QTY"] = 0.0
                    if f"{i}_Value" not in master_pivot.columns: master_pivot[f"{i}_Value"] = 0.0
            else:
                for i in range(1, 13):
                    if i not in master_pivot.columns: master_pivot[i] = 0.0
        else:
            df_sorted = df_filtered.sort_values(by=['Nama Outlet', 'Kode_Global'], ascending=[True, False])
            master_pivot = df_sorted.drop_duplicates(subset=['Nama Outlet', 'Merk'], keep='first')[cols_to_keep]
            if metric_choice == "Value & QTY":
                for i in range(1, 13):
                    master_pivot[f"{i}_QTY"] = 0.0; master_pivot[f"{i}_Value"] = 0.0
            else:
                for i in range(1, 13): master_pivot[i] = 0.0
            
        return master_pivot
    return pd.DataFrame()

def load_users():
    try: return pd.read_csv('users.csv')
    except: return pd.DataFrame()

def save_user_secret(username, secret_key):
    df = load_users()
    if 'secret_key' not in df.columns: df['secret_key'] = None
    df.loc[df['username'] == username, 'secret_key'] = secret_key
    df.to_csv('users.csv', index=False)

@st.cache_data(ttl=3600, show_spinner=False)
def compute_association_rules(df):
    if 'No Faktur' not in df.columns or 'Nama Barang' not in df.columns: return None
    item_support = df.groupby('Nama Barang')['No Faktur'].nunique()
    total_transactions = df['No Faktur'].nunique()
    pair_df = df.groupby('No Faktur')['Nama Barang'].apply(lambda x: list(combinations(sorted(x.dropna().unique()), 2)) if len(x.dropna().unique()) > 1 else [])
    pairs = [p for sublist in pair_df for p in sublist]
    pair_support = Counter(pairs)
    
    rules = []
    for (A, B), supp_ab in pair_support.items():
        conf_ab = supp_ab / item_support[A]
        conf_ba = supp_ab / item_support[B]
        rules.append({'antecedent': A, 'consequent': B, 'support': supp_ab / total_transactions, 'confidence': conf_ab})
        rules.append({'antecedent': B, 'consequent': A, 'support': supp_ab / total_transactions, 'confidence': conf_ba})
    if not rules: return None
    rules_df = pd.DataFrame(rules).drop_duplicates().sort_values('confidence', ascending=False)
    rules_df = rules_df[rules_df['confidence'] > 0.5]  
    return rules_df

@st.cache_data(ttl=3600, show_spinner=False)
def get_cross_sell_recommendations(df):
    rules_df = compute_association_rules(df)
    if rules_df is None or rules_df.empty: return None
    
    outlet_purchases = df.groupby('Nama Outlet')['Nama Barang'].apply(lambda x: set(x.dropna())).to_dict()
    recommendations = []
    for outlet, purchased in outlet_purchases.items():
        if not purchased: continue
        sales_arr = df[df['Nama Outlet'] == outlet]['Penjualan'].unique()
        sales = sales_arr[0] if len(sales_arr) > 0 else "-"
        possible_recs = {}
        for item in purchased:
            matching_rules = rules_df[rules_df['antecedent'] == item]
            for _, rule in matching_rules.iterrows():
                consequent = rule['consequent']
                if consequent not in purchased:
                    conf = rule['confidence']
                    if consequent not in possible_recs or conf > possible_recs[consequent][1]:
                        possible_recs[consequent] = (item, conf)
        if possible_recs:
            top_consequent, (antecedent, conf) = max(possible_recs.items(), key=lambda x: x[1][1])
            rec_text = f"Tawarkan {top_consequent}, karena {conf*100:.0f}% toko yang beli {antecedent} juga membelinya."
            recommendations.append({'Toko': outlet, 'Sales': sales, 'Rekomendasi': rec_text})
    if recommendations: return pd.DataFrame(recommendations)
    return None

@st.fragment
def render_pivot_fragment(df_scope_all, role):
    list_merk_excel = sorted(df_scope_all['Merk'].dropna().astype(str).unique())
    list_tahun = sorted(df_scope_all['Tanggal'].dt.year.dropna().unique(), reverse=True)
    
    grp_cols = []
    if 'Kode_Global' in df_scope_all.columns: 
        grp_cols.append('Kode_Global'); kd_asal = 'Kode_Global'
    else:
        df_scope_all['Kode_Global'] = "-"; grp_cols.append('Kode_Global'); kd_asal = 'Kode_Global'
        
    if 'Nama Outlet' in df_scope_all.columns: grp_cols.append('Nama Outlet')
    else: df_scope_all['Nama Outlet'] = "-"; grp_cols.append('Nama Outlet')
    
    if 'Provinsi' in df_scope_all.columns: grp_cols.append('Provinsi')
    else: df_scope_all['Provinsi'] = "-"; grp_cols.append('Provinsi')
    
    if 'Kota' in df_scope_all.columns: grp_cols.append('Kota')
    else: df_scope_all['Kota'] = "-"; grp_cols.append('Kota')

    with st.form(key='pivot_filter_form'):
        col_piv1, col_piv2 = st.columns(2)
        with col_piv1: selected_merk_excel = st.selectbox("🎯 Pilih Merk:", ["SEMUA"] + list_merk_excel)
        with col_piv2: selected_tahun_excel = st.multiselect("🗓️ Pilih Tahun:", list_tahun, default=list_tahun)
            
        metric_choice = st.radio("📊 Tampilkan Data Berdasarkan:", ["Value (Omset)", "QTY (Kuantitas)", "Value & QTY"], horizontal=True)
        st.markdown("#### 🔎 Filter Spesifik (Batch Processing)")
        
        list_kode_all = sorted(df_scope_all[kd_asal].astype(str).unique())
        list_nama_all = sorted(df_scope_all['Nama Outlet'].astype(str).unique())
        list_provinsi_all = sorted(df_scope_all['Provinsi'].astype(str).unique())
        list_kota_all = sorted(df_scope_all['Kota'].astype(str).unique())

        col_f1, col_f2, col_f3, col_f4 = st.columns(4)
        with col_f1: filter_kode = st.multiselect("Kode Customer:", list_kode_all, placeholder="Pilih Kode...")
        with col_f2: filter_nama = st.multiselect("Nama Outlet:", list_nama_all, placeholder="Pilih Outlet...")
        with col_f3: filter_provinsi = st.multiselect("Provinsi:", list_provinsi_all, placeholder="Pilih Provinsi...")
        with col_f4: filter_kota = st.multiselect("Kota:", list_kota_all, placeholder="Pilih Kota...")

        maximize_toggle = st.toggle("🗖 Mode Layar Penuh (Tabel Super Lebar)")
        submit_button = st.form_submit_button(label='🚀 Terapkan Filter (Super Cepat)', use_container_width=True)

    master_pivot = generate_pivot_fast(df_scope_all, selected_merk_excel, tuple(selected_tahun_excel), tuple(grp_cols), metric_choice)

    if not master_pivot.empty:
        bulan_indo_map = {1: 'Januari', 2: 'Februari', 3: 'Maret', 4: 'April', 5: 'Mei', 6: 'Juni', 7: 'Juli', 8: 'Agustus', 9: 'September', 10: 'Oktober', 11: 'November', 12: 'Desember'}
        
        cols_to_keep = []
        for c in grp_cols:
            if c in master_pivot.columns: cols_to_keep.append(c)
        if 'Merk' in master_pivot.columns and 'Merk' not in cols_to_keep: cols_to_keep.append('Merk')
        
        if metric_choice == "Value & QTY":
            for i in range(1, 13):
                cols_to_keep.extend([f"{i}_QTY", f"{i}_Value"])
        else:
            cols_to_keep.extend(list(range(1, 13)))
            
        master_pivot = master_pivot[cols_to_keep]
        
        ren_map = {'Kode_Global': 'Kode Customer', 'Nama Outlet': 'Nama Customer'}
        num_cols = []
        if metric_choice == "Value & QTY":
            for i in range(1, 13):
                ren_map[f"{i}_QTY"] = f"{bulan_indo_map[i]} (QTY)"
                ren_map[f"{i}_Value"] = f"{bulan_indo_map[i]} (Value)"
                num_cols.extend([f"{bulan_indo_map[i]} (QTY)", f"{bulan_indo_map[i]} (Value)"])
            master_pivot = master_pivot.rename(columns=ren_map)
            master_pivot['Total QTY'] = master_pivot[[f"{bulan_indo_map[i]} (QTY)" for i in range(1,13)]].sum(axis=1)
            master_pivot['Total Value'] = master_pivot[[f"{bulan_indo_map[i]} (Value)" for i in range(1,13)]].sum(axis=1)
            num_cols.extend(['Total QTY', 'Total Value'])
        else:
            for i in range(1, 13): ren_map[i] = bulan_indo_map[i]
            master_pivot = master_pivot.rename(columns=ren_map)
            num_cols = list(bulan_indo_map.values())
            if metric_choice == "QTY (Kuantitas)":
                master_pivot['Total QTY'] = master_pivot[num_cols].sum(axis=1)
                num_cols.append('Total QTY')
            else:
                master_pivot['Total Penjualan'] = master_pivot[num_cols].sum(axis=1)
                num_cols.append('Total Penjualan')
        
        if 'Kode Customer' not in master_pivot.columns: master_pivot['Kode Customer'] = "-"
        if 'Nama Customer' not in master_pivot.columns: master_pivot['Nama Customer'] = "-"
        if 'Merk' not in master_pivot.columns: master_pivot['Merk'] = "-"
        if 'Provinsi' not in master_pivot.columns: master_pivot['Provinsi'] = "-"
        if 'Kota' not in master_pivot.columns: master_pivot['Kota'] = "-"

        df_filtered = master_pivot.copy()
        if filter_kode: df_filtered = df_filtered[df_filtered['Kode Customer'].astype(str).isin(filter_kode)]
        if filter_nama: df_filtered = df_filtered[df_filtered['Nama Customer'].astype(str).isin(filter_nama)]
        if filter_provinsi: df_filtered = df_filtered[df_filtered['Provinsi'].astype(str).isin(filter_provinsi)]
        if filter_kota: df_filtered = df_filtered[df_filtered['Kota'].astype(str).isin(filter_kota)]

        st.caption(f"Menampilkan {len(df_filtered)} baris data.")

        if maximize_toggle:
            st.markdown("""<style>header {display: none !important;} [data-testid="stSidebar"] {display: none !important;} .block-container { max-width: 100% !important; padding-top: 1rem !important; padding-right: 1rem !important; padding-left: 1rem !important; padding-bottom: 1rem !important; }</style>""", unsafe_allow_html=True)
            st.info("Mode Layar Penuh aktif. Hilangkan centang pada toggle 'Mode Layar Penuh' di atas untuk kembali.")

        if not df_filtered.empty:
            cols_reordered = ['Kode Customer', 'Nama Customer', 'Merk', 'Provinsi', 'Kota'] + num_cols
            cols_reordered = [c for c in cols_reordered if c in df_filtered.columns]
            
            df_display = df_filtered[cols_reordered].copy()
            df_display = df_display.loc[:, ~df_display.columns.duplicated()]
            
            total_dict = {col: "" for col in df_display.columns}
            total_dict['Kode Customer'] = "GRAND TOTAL" 
            for col in num_cols:
                if col in df_display.columns: total_dict[col] = float(df_display[col].sum()) if pd.notnull(df_display[col].sum()) else 0.0
                    
            df_display_export = pd.concat([df_display, pd.DataFrame([total_dict])], ignore_index=True)
            
            if AGGRID_AVAILABLE:
                gb = GridOptionsBuilder.from_dataframe(df_display)
                currency_formatter = JsCode("""function(params) { if (!params.value) return '-'; var val=Number(params.value); return isNaN(val)?params.value:'Rp '+val.toLocaleString('id-ID'); }""")
                qty_formatter = JsCode("""function(params) { if (!params.value) return '-'; var val=Number(params.value); return isNaN(val)?params.value:val.toLocaleString('id-ID'); }""")
                
                for col in df_display.columns:
                    if col in num_cols:
                        if "QTY" in col.upper() or metric_choice == "QTY (Kuantitas)":
                            gb.configure_column(col, type=["numericColumn"], headerClass="right-aligned-header", filter='agNumberColumnFilter', floatingFilter=True, valueFormatter=qty_formatter)
                        else:
                            gb.configure_column(col, type=["numericColumn"], headerClass="right-aligned-header", filter='agNumberColumnFilter', floatingFilter=True, valueFormatter=currency_formatter)
                    elif col in ['Kode Customer', 'Nama Customer', 'Merk']:
                        gb.configure_column(col, pinned='left', filter='agSetColumnFilter', floatingFilter=True)
                    else:
                        gb.configure_column(col, filter='agSetColumnFilter', floatingFilter=True)
                
                gb.configure_default_column(resizable=True, sortable=True)
                gb.configure_grid_options(getRowHeight=JsCode("function(p){return p.node.rowPinned==='bottom'?45:40;}"), headerHeight=45, floatingFiltersHeight=40, pinnedBottomRowData=[total_dict], getRowStyle=JsCode("function(p){if(p.node.rowPinned==='bottom'){return {'background-color':'#FFFF00 !important','font-weight':'bold !important','color':'black !important','border-top':'3px solid #333 !important'};} return null;}"))
                
                custom_css = {".ag-root-wrapper": {"font-family": "sans-serif !important"}, ".ag-header-cell-label": {"font-size": "14px !important", "color": "white !important", "font-weight": "bold !important"}, ".ag-header-cell": {"background-color": "#2980b9 !important", "border-right": "1px solid #555555 !important"}, ".ag-header": {"background-color": "#2980b9 !important", "border-bottom": "1px solid #555555 !important"}, ".ag-header-row-column-filter": {"background-color": "#2980b9 !important"}, ".ag-header .ag-icon": {"color": "white !important", "fill": "white !important"}, ".ag-cell": {"font-size": "14px !important", "font-weight": "500 !important", "color": "black !important", "background-color": "white !important", "border-right": "1px solid #555555 !important", "border-bottom": "1px solid #555555 !important", "display": "flex", "align-items": "center"}, ".ag-row-hover .ag-cell": {"background-color": "#e3f2fd !important"}, ".ag-floating-filter-input input": {"font-size": "13px !important", "background-color": "white !important", "color": "black !important", "border-radius": "3px !important", "padding": "2px 5px !important", "border": "1px solid #ccc !important"}, ".right-aligned-header .ag-header-cell-label": {"justify-content": "flex-end !important"}, ".ag-floating-bottom-container .ag-row, .ag-pinned-left-floating-bottom .ag-row": {"border-top": "3px solid #333 !important"}, ".ag-floating-bottom-container .ag-cell, .ag-pinned-left-floating-bottom .ag-cell": {"font-size": "14px !important", "background-color": "#FFFF00 !important", "color": "black !important", "font-weight": "bold !important", "border-right": "none !important"}}
                try: AgGrid(df_display, gridOptions=gb.build(), allow_unsafe_jscode=True, theme='balham', height=600, fit_columns_on_grid_load=False, custom_css=custom_css, enable_enterprise_modules=True)
                except Exception: st.dataframe(df_display_export, use_container_width=True)
            else: st.error("Library st_aggrid belum terpasang.")
        else: st.info("Data Kosong setelah difilter.")
            
        user_role_lower = role.lower()
        if user_role_lower == 'direktur':
            output = io.BytesIO()
            has_data_to_export = 'df_display_export' in locals() and not df_display_export.empty
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                if has_data_to_export:
                    df_display_export.to_excel(writer, index=False, sheet_name='Master Data')
                    workbook = writer.book
                    worksheet = writer.sheets['Master Data']
                    watermark_text = f"CONFIDENTIAL DOCUMENT | TRACKED USER: {st.session_state.get('sales_name', 'Unknown')} | DOWNLOADED: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    worksheet.set_header(f'&C&10{watermark_text}')
                    worksheet.set_column('F:AZ', None, workbook.add_format({'num_format': '#,##0'})) 
                    worksheet.set_row(len(df_display_export), 30, workbook.add_format({'bold': True, 'bg_color': '#FFFF00', 'border': 1, 'num_format': '#,##0', 'font_color': 'black'}))
                else: pd.DataFrame(["Data Kosong"]).to_excel(writer, index=False, sheet_name='Kosong')
            if has_data_to_export:
                st.download_button(label="📥 Download Laporan Excel (XLSX) - DRM Protected", data=output.getvalue(), file_name=f"Laporan_Master_{selected_merk_excel}_{datetime.date.today()}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

def login_page():
    st.markdown("<br><br><h1 style='text-align: center;'>🦅 Executive Command Center</h1>", unsafe_allow_html=True)
    
    if st.session_state.get('logged_out_due_to_inactivity', False):
        st.warning("⏱️ Sesi Anda telah berakhir karena tidak ada aktivitas selama 15 menit. Silakan login kembali demi keamanan.")
        st.session_state['logged_out_due_to_inactivity'] = False

    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        with st.container(border=True):
            st.markdown(f"<div style='text-align:center; color:#888; font-size:12px;'>Sistem Terproteksi</div>", unsafe_allow_html=True)
            if 'login_step' not in st.session_state: st.session_state['login_step'] = 'credentials'
            if 'temp_user_data' not in st.session_state: st.session_state['temp_user_data'] = None
            
            if st.session_state['login_step'] == 'credentials':
                with st.form("login_form"):
                    username = st.text_input("Username")
                    password = st.text_input("Password", type="password")
                    
                    st.markdown("<br>", unsafe_allow_html=True)
                    captcha_val = st.slider("Geser slider ke ujung kanan (100) untuk verifikasi manusia 🤖", 0, 100, 0)
                    st.markdown("<br>", unsafe_allow_html=True)
                    
                    submitted = st.form_submit_button("Verifikasi Akun", use_container_width=True)
                    
                    if submitted:
                        if username in st.session_state.get('lockout_until', {}):
                            if time.time() < st.session_state['lockout_until'][username]:
                                remaining = int((st.session_state['lockout_until'][username] - time.time()) / 60)
                                st.error(f"🔒 Akses ditolak! Akun terkunci karena percobaan gagal beruntun. Coba lagi dalam {remaining + 1} menit.")
                                return
                            else:
                                st.session_state['failed_attempts'][username] = 0
                                del st.session_state['lockout_until'][username]

                        if captcha_val != 100:
                            st.error("🚨 Verifikasi Captcha gagal! Geser slider hingga angka 100.")
                        else:
                            users = load_users()
                            if users.empty: st.error("Database user (users.csv) tidak ditemukan.")
                            else:
                                match = users[(users['username'] == username) & (users['password'] == password)]
                                if match.empty:
                                    st.error("Username atau Password salah.")
                                    log_activity(username, "FAILED LOGIN - WRONG PASS")
                                    st.session_state['failed_attempts'][username] = st.session_state['failed_attempts'].get(username, 0) + 1
                                    if st.session_state['failed_attempts'][username] >= 3:
                                        st.session_state['lockout_until'][username] = time.time() + 600
                                        st.error("🔒 Akun dikunci selama 10 menit karena 3x percobaan gagal.")
                                else:
                                    st.session_state['failed_attempts'][username] = 0
                                    user_row = match.iloc[0]
                                    user_role = user_row['role']
                                    user_sales_name = user_row['sales_name']
                                    
                                    if user_role in ['direktur', 'manager']:
                                        st.session_state['logged_in'] = True
                                        st.session_state['role'] = user_role
                                        st.session_state['sales_name'] = user_sales_name
                                        log_activity(user_sales_name, "LOGIN SUCCESS")
                                        st.rerun()
                                    else:
                                        st.session_state['temp_user_data'] = user_row
                                        st.session_state['login_step'] = '2fa_check'
                                        st.rerun()
                                    
            elif st.session_state['login_step'] == '2fa_check':
                user_data = st.session_state['temp_user_data']
                username_2fa = user_data['username']
                
                # Cek apakah akun sedang terkunci akibat gagal berkali-kali
                if username_2fa in st.session_state.get('lockout_until', {}):
                    if time.time() < st.session_state['lockout_until'][username_2fa]:
                        remaining = int((st.session_state['lockout_until'][username_2fa] - time.time()) / 60)
                        st.error(f"🔒 Akses ditolak! Akun terkunci. Coba lagi dalam {remaining + 1} menit.")
                        if st.button("Kembali ke Awal"):
                            st.session_state['login_step'] = 'credentials'
                            st.rerun()
                        return
                
                # Tampilan Antarmuka Pengisian Token
                st.write(f"Halo, **{user_data['sales_name']}** 👋")
                st.caption("Silakan masukkan Token Master Harian untuk masuk ke dalam dasbor.")
                
                code_input = st.text_input("🔑 Masukkan Token (4 Digit):", max_chars=4)
                
                if st.button("Masuk", use_container_width=True):
                    # Panggil fungsi Token Master untuk dicocokkan
                    token_hari_ini = generate_daily_token()
                    
                    if code_input == token_hari_ini:
                        st.session_state['failed_attempts'][username_2fa] = 0
                        st.session_state['logged_in'] = True
                        st.session_state['role'] = user_data['role']
                        st.session_state['sales_name'] = user_data['sales_name']
                        log_activity(user_data['sales_name'], "LOGIN SUCCESS (TOKEN MASTER)")
                        st.rerun()
                    else:
                        st.error("❌ Token Salah atau Kadaluarsa!")
                        log_activity(user_data['sales_name'], "FAILED LOGIN - WRONG TOKEN")
                        st.session_state['failed_attempts'][username_2fa] = st.session_state['failed_attempts'].get(username_2fa, 0) + 1
                        
                        # Kunci akun jika gagal 3 kali
                        if st.session_state['failed_attempts'][username_2fa] >= 3:
                            st.session_state['lockout_until'][username_2fa] = time.time() + 600
                            st.error("🔒 Akun dikunci selama 10 menit karena 3x percobaan gagal.")
                
                st.markdown("<br>", unsafe_allow_html=True)
                if st.button("Kembali"):
                    st.session_state['login_step'] = 'credentials'
                    st.rerun()

def main_dashboard():
    def get_color_achv(val):
        try:
            if val < 0.50: return '#ffcccc' 
            elif val < 0.85: return '#fff2cc' 
            else: return '#d1e7dd' 
        except:
            return ''

    def add_aggressive_watermark():
        user_name = st.session_state.get('sales_name', 'User')
        role_name = st.session_state.get('role', 'staff')
        
        if role_name != 'direktur':
            st.markdown(f"""
            <style>
            .watermark-container {{ position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; z-index: 99999; pointer-events: none; overflow: hidden; display: flex; flex-wrap: wrap; opacity: 0.15; }}
            .watermark-text {{ font-family: 'Arial', sans-serif; font-size: 16px; color: #555; font-weight: 700; transform: rotate(-30deg); white-space: nowrap; margin: 20px; user-select: none; }}
            </style>
            <div class="watermark-container">{''.join([f'<div class="watermark-text">{user_name} • CONFIDENTIAL • {get_current_time_wib().strftime("%H:%M")}</div>' for _ in range(300)])}</div>
            <script>
            window.addEventListener('blur', () => {{ document.body.style.filter = 'blur(20px) brightness(0.4)'; document.body.style.backgroundColor = '#000'; }});
            window.addEventListener('focus', () => {{ document.body.style.filter = 'none'; document.body.style.backgroundColor = '#fff'; }});
            document.addEventListener('keydown', (e) => {{ if ((e.ctrlKey || e.metaKey) && (e.key === 'p' || e.key === 's')) {{ e.preventDefault(); alert('⚠️ Action Disabled for Security Reasons!'); }} }});
            </script>
            """, unsafe_allow_html=True)
    
    add_aggressive_watermark()

    if st.session_state['role'] != 'direktur':
        st.markdown("<style>@media print { body { display: none !important; } } body { -webkit-user-select: none; -moz-user-select: none; -ms-user-select: none; user-select: none; } img { pointer-events: none; }</style>", unsafe_allow_html=True)

    with st.sidebar:
        st.write("## 👤 User Profile")
        st.info(f"{st.session_state['sales_name']}\n\nRole: {st.session_state['role'].upper()}")
        
        fast_mode = st.toggle("⚡ Mode Performa Tinggi", value=True, help="Membaca data dari memori (Cache). Matikan jika Anda baru saja menambah data di Google Sheets dan ingin sistem menarik data terbaru.")
        
        st.markdown("---")
        st.write("### 🎬 Mode Presentasi")
        is_presentation_mode = st.toggle("🔦 Aktifkan Sorotan Layar", value=False)
        
        if is_presentation_mode:
            components.html("""
            <script>
                const overlay = window.parent.document.createElement('div');
                overlay.id = 'presentation-spotlight';
                overlay.style.position = 'fixed';
                overlay.style.top = '0';
                overlay.style.left = '0';
                overlay.style.width = '100vw';
                overlay.style.height = '100vh';
                overlay.style.pointerEvents = 'none';
                overlay.style.zIndex = '99998';
                overlay.style.background = 'radial-gradient(circle 250px at 50vw 50vh, transparent 0%, rgba(0, 0, 0, 0.8) 100%)';
                
                const existing = window.parent.document.getElementById('presentation-spotlight');
                if (existing) existing.remove();
                window.parent.document.body.appendChild(overlay);

                window.parent.document.addEventListener('mousemove', function(e) {
                    overlay.style.background = `radial-gradient(circle 250px at ${e.clientX}px ${e.clientY}px, transparent 0%, rgba(0, 0, 0, 0.8) 100%)`;
                });
            </script>
            """, height=0, width=0)
        else:
            components.html("""
            <script>
                const existing = window.parent.document.getElementById('presentation-spotlight');
                if (existing) existing.remove();
            </script>
            """, height=0, width=0)

        if st.session_state['role'] in ['manager', 'direktur'] or st.session_state.get('sales_name', '').lower() == 'fauziah':
            st.markdown("---")
            with st.expander("🔐 Admin Zone", expanded=False):
                token_hari_ini = generate_daily_token()
                st.write(f"Token Master: `{token_hari_ini}`")
                
                target_sales = st.text_input("Nama Sales (Generate QR)", placeholder="Ketik nama (mis: Wira)...")
                if target_sales:
                    users_df = load_users()
                    if target_sales in users_df['username'].values:
                        user_record = users_df[users_df['username'] == target_sales].iloc[0]
                        current_secret = user_record.get('secret_key', None)
                        if pd.isna(current_secret) or current_secret == "" or current_secret is None:
                            current_secret = pyotp.random_base32()
                            save_user_secret(target_sales, current_secret)
                            st.success(f"Secret Key Dibuat!")
                        uri = pyotp.totp.TOTP(current_secret).provisioning_uri(name=user_record['sales_name'], issuer_name="Distributor App")
                        qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=150x150&data={uri}"
                        st.image(qr_url, width=150)
                    else: st.error("Username tidak ditemukan.")
                
                if st.button("🔄 Force Sync Database", use_container_width=True):
                    st.cache_data.clear() 
                    if os.path.exists(PARQUET_FILE):
                        os.remove(PARQUET_FILE) 
                    st.success("Tersinkronisasi!")
                    time.sleep(1)
                    st.rerun()
                    
                # ==========================================
                # TAMBAHAN: FITUR BACA AUDIT LOG (LOGIN/LOGOUT)
                # ==========================================
                st.markdown("---")
                st.write("🕵️ **Riwayat Login & Logout**")
                
                if os.path.isfile('audit_log.csv'):
                    try:
                        # Baca data log yang sudah dicatat oleh sistem
                        df_log = pd.read_csv('audit_log.csv')
                        
                        # Urutkan dari waktu terbaru (Z ke A)
                        df_log = df_log.sort_values(by='Timestamp', ascending=False)
                        
                        # Tampilkan preview 5 aktivitas terakhir agar sidebar tidak kepanjangan
                        st.dataframe(df_log.head(5), use_container_width=True, hide_index=True)
                        
                        # Buat tombol download untuk menarik data penuh
                        if st.session_state['role'] == 'direktur':
                            csv_log = df_log.to_csv(index=False).encode('utf-8')
                            st.download_button(
                                label="📥 Download Full Log (CSV)",
                                data=csv_log,
                                file_name=f"Audit_Log_Aktivitas_{datetime.date.today()}.csv",
                                mime="text/csv",
                                use_container_width=True
                            )
                        else:
                            st.info("🔒 Hak akses Download Log hanya untuk Direktur.")
                    except Exception as e:
                        st.caption("⚠️ File log sedang digunakan atau tidak dapat dibaca.")
                else:
                    st.caption("Belum ada data aktivitas di sistem.")
                # ==========================================
        
        if st.button("🚪 Logout", use_container_width=True):
            log_activity(st.session_state['sales_name'], "LOGOUT")
            st.session_state['logged_in'] = False
            for key in list(st.session_state.keys()): del st.session_state[key]
            st.rerun()
        st.markdown("---")
            
    df = load_data(fast_mode)
    if df is None or df.empty:
        st.error("⚠️ Gagal memuat data! Periksa koneksi internet atau Link CSV Google Sheet Anda.")
        return

    # =========================================================
    # ⏳ TIME-LEVEL SECURITY (TLS) - PEMOTONGAN DATA HISTORIS
    # =========================================================
    # Otomatis mendeteksi tahun berjalan berdasarkan jam server.
    # (Catatan: Jika Anda ingin tes manual, Anda bisa ganti menjadi: tahun_sekarang = 2026)
    tahun_sekarang = datetime.date.today().year
    
    # Cek apakah akun yang login memiliki role 'direktur'
    is_direktur = st.session_state.get('role', '').lower() == 'direktur'
    
    # Jika BUKAN direktur, buang semua transaksi di bawah tahun berjalan dari memori
    if not is_direktur:
        df = df[df['Tanggal'].dt.year >= tahun_sekarang]
    # =========================================================

    user_role = st.session_state['role']
    user_name = st.session_state['sales_name']
    role = user_role
    my_name = user_name
    my_name_key = my_name.strip().upper()
    
    is_supervisor_account = my_name_key in TARGET_DATABASE
    # =========================================================
    # 🛡️ ROW-LEVEL SECURITY (RLS) - PROTEKSI AKSES MUTLAK
    # =========================================================
    if role not in ['manager', 'direktur'] and my_name.lower() != 'fauziah':
        if is_supervisor_account:
            # Supervisor HANYA mewarisi data dari Brand yang dikelolanya
            allowed_brands = list(TARGET_DATABASE[my_name_key].keys())
            df = df[df['Merk'].isin(allowed_brands)]
        else:
            # Salesman biasa HANYA mewarisi data transaksinya sendiri
            df = df[df['Penjualan'] == my_name_key]
    # =========================================================
    
    if role in ['manager', 'direktur'] or my_name.lower() == 'fauziah':
        sales_list = ["SEMUA"] + sorted(list(df['Penjualan'].dropna().astype(str).unique()))
        brands_list = sorted(df['Merk'].dropna().astype(str).unique())
        outlets_list = sorted(df['Nama Outlet'].dropna().astype(str).unique())
    elif is_supervisor_account:
        my_brands = TARGET_DATABASE[my_name_key].keys()
        df_spv_raw = df[df['Merk'].isin(my_brands)]
        sales_list = ["SEMUA"] + sorted(list(df_spv_raw['Penjualan'].dropna().astype(str).unique()))
        brands_list = sorted(df_spv_raw['Merk'].dropna().astype(str).unique())
        outlets_list = sorted(df_spv_raw['Nama Outlet'].dropna().astype(str).unique())
    else:
        sales_list = [my_name]
        df_sales_raw = df[df['Penjualan'] == my_name]
        brands_list = sorted(df_sales_raw['Merk'].dropna().astype(str).unique())
        outlets_list = sorted(df_sales_raw['Nama Outlet'].dropna().astype(str).unique())

    today = datetime.date.today()
    if 'start_date' not in st.session_state:
        st.session_state['start_date'] = df['Tanggal'].max().date().replace(day=1)
        st.session_state['end_date'] = df['Tanggal'].max().date()
        
    st.title("🚀 Executive Dashboard")
    st.markdown("---")

    # =========================================================
    # HIERARKI RUANG LINGKUP (UI & BACKGROUND LOGIC)
    # =========================================================
    # 1. Cek apakah user adalah Direktur, Manager, atau Fauziah (Akses Penuh)
    is_full_access = (role.lower() in ['direktur', 'manager']) or (my_name.lower() == 'fauziah')

    if is_full_access:
        st.markdown("### 👤 User")
        list_ijl = ["IJL"] + list(TARGET_DATABASE.keys())
        selected_ijl = st.selectbox("Pilih User Dashboard:", list_ijl, index=0)
        
    # 2. Jika user adalah Supervisor, kunci menu hanya untuk namanya sendiri
    elif is_supervisor_account:
        st.markdown("### 👤 User")
        # st.selectbox dikunci (disabled=True) agar tidak bisa diklik
        selected_ijl = st.selectbox("Pilih User Dashboard:", [my_name_key], disabled=True)
        
    # 3. Untuk Sales biasa, jalankan default
    else:
        selected_ijl = "IJL" 
    # =========================================================
        
    st.sidebar.markdown("### ⚙️ Panel Filter Executive")
    with st.sidebar.form("main_filter_form"):
        
        # Mengunci batas kalender agar menyesuaikan dengan data yang tersedia di memori
        # (Jika Supervisor, kalender tidak akan bisa digeser mundur melewati 1 Januari tahun ini)
        min_kalender = df['Tanggal'].min().date() if not df.empty else today
        max_kalender = df['Tanggal'].max().date() if not df.empty else today
        
        date_range = st.date_input(
            "Rentang Waktu", 
            [st.session_state['start_date'], st.session_state['end_date']],
            min_value=min_kalender,
            max_value=max_kalender
        )
        
        target_sales_filter = st.selectbox("Pantau Kinerja Sales / Tim:", sales_list)
        pilih_merk = st.multiselect("Filter Spesifik Merk:", brands_list)
        pilih_outlet = st.multiselect("Filter Spesifik Outlet:", outlets_list)
        
        submit_main_filter = st.form_submit_button("🚀 Terapkan Filter", use_container_width=True)

    df_scope_all = df.copy()

    if selected_ijl != "IJL":
        brands_in_ijl = TARGET_DATABASE[selected_ijl].keys()
        df_scope_all = df_scope_all[df_scope_all['Merk'].isin(brands_in_ijl)]

    if target_sales_filter != "SEMUA":
        df_scope_all = df_scope_all[df_scope_all['Penjualan'] == target_sales_filter]

    if pilih_merk:
        df_scope_all = df_scope_all[df_scope_all['Merk'].isin(pilih_merk)]

    if pilih_outlet:
        df_scope_all = df_scope_all[df_scope_all['Nama Outlet'].isin(pilih_outlet)]

    if len(date_range) == 2:
        start_date, end_date = date_range
        df_active = df_scope_all[(df_scope_all['Tanggal'].dt.date >= start_date) & (df_scope_all['Tanggal'].dt.date <= end_date)]
        ref_date = end_date
    else:
        df_active = df_scope_all
        start_date = df_scope_all['Tanggal'].min().date() if not df_scope_all.empty else today
        end_date = df_scope_all['Tanggal'].max().date() if not df_scope_all.empty else today
        ref_date = end_date

    df_active_tab = df_active.copy()

    current_omset_total = df_active['Jumlah'].sum()
    
    # --- 1. PEMBUATAN DATAFRAME PERIODE SEBELUMNYA (df_prev) ---
    if len(date_range) == 2:
        try:
            prev_start = start_date.replace(month=start_date.month - 1) if start_date.month > 1 else start_date.replace(year=start_date.year - 1, month=12)
            prev_end = end_date.replace(month=end_date.month - 1) if end_date.month > 1 else end_date.replace(year=end_date.year - 1, month=12)
        except ValueError:
            prev_start = start_date - datetime.timedelta(days=30)
            prev_end = end_date - datetime.timedelta(days=30)
            
        df_prev = df_scope_all[(df_scope_all['Tanggal'].dt.date >= prev_start) & (df_scope_all['Tanggal'].dt.date <= prev_end)]
        delta_label = f"vs {prev_start.strftime('%d %b')} - {prev_end.strftime('%d %b')}"
    else:
        prev_date = ref_date - datetime.timedelta(days=1)
        df_prev = df_scope_all[df_scope_all['Tanggal'].dt.date == prev_date]
        delta_label = f"vs {prev_date.strftime('%d %b')}"

    # --- 2. KALKULASI SELISIH OMSET, OUTLET, & TRANSAKSI ---
    # Omset
    omset_prev_period = df_prev['Jumlah'].sum()
    delta_val = current_omset_total - omset_prev_period

    # Outlet (Unik)
    outlet_current = df_active['Nama Outlet'].nunique()
    outlet_prev = df_prev['Nama Outlet'].nunique()
    delta_outlet = outlet_current - outlet_prev

    # Transaksi (Nomor Faktur Unik)
    if 'No Faktur' in df_active.columns:
        valid_faktur = df_active['No Faktur'].astype(str)
        valid_faktur = valid_faktur[~valid_faktur.isin(['nan', 'None', '', '-', '0', '.'])]
        valid_faktur = valid_faktur[valid_faktur.str.len() > 2]
        transaksi_current = valid_faktur.nunique()
        
        valid_faktur_prev = df_prev['No Faktur'].astype(str)
        valid_faktur_prev = valid_faktur_prev[~valid_faktur_prev.isin(['nan', 'None', '', '-', '0', '.'])]
        valid_faktur_prev = valid_faktur_prev[valid_faktur_prev.str.len() > 2]
        transaksi_prev = valid_faktur_prev.nunique()
    else: 
        transaksi_current = len(df_active)
        transaksi_prev = len(df_prev)
        
    delta_transaksi = transaksi_current - transaksi_prev

    # --- 3. RENDER UI METRIC KE LAYAR ---
    c1, c2, c3 = st.columns(3)
    
    # 1. Render Omset
    delta_str = format_idr(abs(delta_val))
    if delta_val < 0: delta_html = f"<span style='color: #f39c12; font-weight: bold; font-size: 14px;'>▼ - {delta_str} ({delta_label})</span>"
    elif delta_val > 0: delta_html = f"<span style='color: #2ecc71; font-weight: bold; font-size: 14px;'>▲ + {delta_str} ({delta_label})</span>"
    else: delta_html = f"<span style='color: #95a5a6; font-weight: bold; font-size: 14px;'>▬ {delta_str} ({delta_label})</span>"

    c1.markdown(f"""
    <div style="padding: 0px 0px;">
        <p style="margin:0; font-size: 18px; font-weight: 600; color: inherit; padding-bottom: 0.25rem;">💰 Total Omset (Periode)</p>
        <div style="font-size: 36px; font-weight: bold; color: inherit; line-height: 1.2;">{format_idr(current_omset_total)}</div>
        {delta_html}
    </div>
    """, unsafe_allow_html=True)
    
    # 2. Render Outlet Aktif
    if delta_outlet < 0: delta_outlet_html = f"<span style='color: #f39c12; font-weight: bold; font-size: 14px;'>▼ {delta_outlet} ({delta_label})</span>"
    elif delta_outlet > 0: delta_outlet_html = f"<span style='color: #2ecc71; font-weight: bold; font-size: 14px;'>▲ + {delta_outlet} ({delta_label})</span>"
    else: delta_outlet_html = f"<span style='color: #95a5a6; font-weight: bold; font-size: 14px;'>▬ {delta_outlet} ({delta_label})</span>"
    
    c2.markdown(f"""
    <div style="padding: 0px 0px;">
        <p style="margin:0; font-size: 18px; font-weight: 600; color: inherit; padding-bottom: 0.25rem;">🏪 Outlet Aktif</p>
        <div style="font-size: 36px; font-weight: bold; color: inherit; line-height: 1.2;">{outlet_current}</div>
        {delta_outlet_html}
    </div>
    """, unsafe_allow_html=True)
    
    # 3. Render Transaksi
    if delta_transaksi < 0: delta_trx_html = f"<span style='color: #f39c12; font-weight: bold; font-size: 14px;'>▼ {delta_transaksi} ({delta_label})</span>"
    elif delta_transaksi > 0: delta_trx_html = f"<span style='color: #2ecc71; font-weight: bold; font-size: 14px;'>▲ + {delta_transaksi} ({delta_label})</span>"
    else: delta_trx_html = f"<span style='color: #95a5a6; font-weight: bold; font-size: 14px;'>▬ {delta_transaksi} ({delta_label})</span>"
    
    c3.markdown(f"""
    <div style="padding: 0px 0px;">
        <p style="margin:0; font-size: 18px; font-weight: 600; color: inherit; padding-bottom: 0.25rem;">🧾 Transaksi</p>
        <div style="font-size: 36px; font-weight: bold; color: inherit; line-height: 1.2;">{transaksi_current}</div>
        {delta_trx_html}
    </div>
    """, unsafe_allow_html=True)

    if role in ['manager', 'direktur'] or is_supervisor_account or target_sales_filter in INDIVIDUAL_TARGETS or target_sales_filter.upper() in TARGET_DATABASE:
        st.markdown("### 🎯 Target Monitor")
        if target_sales_filter == "SEMUA":
            if selected_ijl != "IJL":
                target_val = sum(TARGET_DATABASE[selected_ijl].values())
                title = f"🏢 Target {selected_ijl}"
            else:
                target_val = TARGET_NASIONAL_VAL
                title = "🏢 Target IJL (All Team)"
            
            realisasi = df_active['Jumlah'].sum()
            render_custom_progress(title, realisasi, target_val)
        elif target_sales_filter in INDIVIDUAL_TARGETS:
            st.info(f"📋 Target Spesifik: {target_sales_filter}")
            targets_map = INDIVIDUAL_TARGETS[target_sales_filter]
            for brand, target_val in targets_map.items():
                realisasi_brand = df_active[df_active['Merk'] == brand]['Jumlah'].sum()
                render_custom_progress(f"👤 {brand} - {target_sales_filter}", realisasi_brand, target_val)
        elif target_sales_filter.upper() in TARGET_DATABASE:
             spv_name = target_sales_filter.upper()
             target_pribadi = SUPERVISOR_TOTAL_TARGETS.get(spv_name, 0)
             render_custom_progress(f"👤 Target Tim {spv_name}", df_active['Jumlah'].sum(), target_pribadi)
        st.markdown("---")

    t1, t2, t_detail_sales, t_detail_harian, t3, t5, t_forecast, t4, t_audit, t_ai, t_eval, t_pasar = st.tabs(["📊 Rapor Brand", "📈 Tren Harian", "👥 Detail Tim", "📝 Detail Sales", "🏆 Top Produk", "🚀 Kejar Omset", "🔮 Prediksi Omset", "📋 Data Rincian", "⚠️ Audit Fakturis", "🤖 Asisten AI", "📈 Evaluasi Tugas", "🌐 Pasar & Forecast"])
    
    with t_ai:
        import ai_tools, ai_chat
        try:  # FASE 4: alat evaluasi tugas untuk Asisten AI (bila modul belum ada, dashboard tetap jalan)
            import ai_tasks_eval_ui
            ai_tasks_eval_ui.daftarkan_penyedia()
        except ImportError:
            pass
        cfg_ai = ai_tools.ToolsConfig(target_database=TARGET_DATABASE, individual_targets=INDIVIDUAL_TARGETS)
        ai_chat.render_asisten_ai(df, user_role, user_name, cfg_ai, catat=log_activity)

    with t_eval:  # FASE 4
        try:
            import ai_tasks_eval_ui
        except ImportError:
            st.info("Modul evaluasi tugas (ai_tasks_eval_ui.py / ai_tasks_eval.py) belum ada di repo.")
        else:
            ai_tasks_eval_ui.render_evaluasi(df, user_role, user_name, cfg_ai)

    with t_pasar:  # FASE 6: tren pasar vs internal + forecast omset
        try:
            import market_panel
        except ImportError:
            st.info("Modul panel pasar (market_panel.py) belum ada di repo.")
        else:
            market_panel.render_pasar_forecast(df, user_role, user_name, cfg_ai)

    with t1:
        if role in ['manager', 'direktur'] or my_name.lower() == 'fauziah': loop_source = TARGET_DATABASE.items()
        elif is_supervisor_account: loop_source = {my_name_key: TARGET_DATABASE[my_name_key]}.items()
        else: loop_source = None

        if loop_source and (target_sales_filter == "SEMUA" or target_sales_filter.upper() in TARGET_DATABASE):
            st.subheader(f"🏆 Ranking Brand & Detail Sales {('- ' + selected_ijl) if selected_ijl != 'IJL' else ''}")
            
            dict_mtd_brand = df_active_tab.groupby('Merk')['Jumlah'].sum().to_dict() if not df_active_tab.empty else {}
            
            def get_salesmen_dict(df_to_group):
                res = {}
                if not df_to_group.empty:
                    grouped = df_to_group.groupby(['Merk', 'Penjualan'])['Jumlah'].sum()
                    for (b, s), val in grouped.items():
                        if val != 0:
                            if b not in res: res[b] = {}
                            res[b][s] = val
                return res
                
            salesmen_mtd_master = get_salesmen_dict(df_active_tab)
            
            temp_grouped_data = [] 
            for spv, brands_dict in loop_source:
                if selected_ijl != "IJL" and spv != selected_ijl:
                    continue
                    
                for brand, target in brands_dict.items():
                    realisasi_brand = dict_mtd_brand.get(brand, 0.0) 
                    pct_brand = (realisasi_brand / target * 100) if target > 0 else 0
                    brand_row = {
                        "Rank": 0, "Brand / Salesman": brand, "Supervisor": spv, "Target": format_idr(target),
                        "Realisasi": format_idr(realisasi_brand), "Ach (%)": f"{pct_brand:.0f}%",
                        "Bar": pct_brand / 100, "Progress (Detail %)": pct_brand / 100 
                    }
                    sales_rows_list = []
                    
                    salesmen_for_this_brand = salesmen_mtd_master.get(brand, {})
                    for s_name, s_targets in INDIVIDUAL_TARGETS.items():
                        if brand in s_targets:
                            t_indiv = s_targets[brand]
                            r_indiv = salesmen_for_this_brand.get(s_name, 0.0)
                            pct_indiv = (r_indiv / t_indiv * 100) if t_indiv > 0 else 0
                            sales_rows_list.append({
                                "Rank": "", "Brand / Salesman": f"   └─ {s_name}", "Supervisor": "", 
                                "Target": format_idr(t_indiv), "Realisasi": format_idr(r_indiv),
                                "Ach (%)": f"{pct_indiv:.0f}%", "Bar": pct_indiv / 100,
                                "Progress (Detail %)": pct_indiv / 100 
                            })
                    temp_grouped_data.append({"parent": brand_row, "children": sales_rows_list, "sort_val": realisasi_brand})

            temp_grouped_data.sort(key=lambda x: x['sort_val'], reverse=True)
            final_summary_data = []
            for idx, group in enumerate(temp_grouped_data, 1):
                group['parent']['Rank'] = idx 
                final_summary_data.append(group['parent'])
                final_summary_data.extend(group['children'])

            df_summ = pd.DataFrame(final_summary_data)
            if not df_summ.empty:
                cols = ['Rank'] + [c for c in df_summ.columns if c != 'Rank']
                df_summ = df_summ[cols]
                
                # --- FIX PYARROW: Konversi paksa kolom Rank menjadi teks ---
                df_summ['Rank'] = df_summ['Rank'].astype(str)
                
                def style_rows(row):
                    val = row['Progress (Detail %)']
                    bg_color = get_color_achv(val)
                    if row["Supervisor"]: 
                        return [f'background-color: {bg_color}; color: black; font-weight: bold; border-top: 2px solid white'] * len(row)
                    else: 
                        return [f'background-color: {bg_color}; color: #333; opacity: 0.9; border-bottom: 1px solid #eee'] * len(row)
                        
                st.dataframe(
                    df_summ.style.apply(style_rows, axis=1).hide(axis="columns", subset=['Progress (Detail %)']),
                    use_container_width=True, hide_index=True,
                    column_config={
                        "Rank": st.column_config.TextColumn("🏆 Rank", width="small"),
                        "Brand / Salesman": st.column_config.TextColumn("Brand / Salesman", width="medium"),
                        "Bar": st.column_config.ProgressColumn("Progress", format=" ", min_value=0, max_value=1)
                    }
                )
            else: st.warning("Tidak ada data untuk ditampilkan pada ruang lingkup ini.")
        elif target_sales_filter in INDIVIDUAL_TARGETS: st.info("Lihat progress bar di atas untuk detail target individu.")
        else:
            sales_brands = df_active_tab['Merk'].unique()
            indiv_data = []
            dict_mtd_brand = df_active_tab.groupby('Merk')['Jumlah'].sum().to_dict() if not df_active_tab.empty else {}
            for brand in sales_brands:
                owner, target = "-", 0
                for spv, b_dict in TARGET_DATABASE.items():
                    if brand in b_dict: owner, target = spv, b_dict[brand]; break
                if target > 0:
                    real = dict_mtd_brand.get(brand, 0.0)
                    pct = (real/target)*100
                    indiv_data.append({"Brand": brand, "Owner": owner, "Target Tim": format_idr(target), "Kontribusi": format_idr(real), "Ach (%)": f"{pct:.1f}%", "Pencapaian": pct/100})
            if indiv_data: 
                df_indiv = pd.DataFrame(indiv_data).sort_values("Kontribusi", ascending=False)
                def style_indiv(row):
                    val = row['Pencapaian']
                    bg = get_color_achv(val)
                    return [f'background-color: {bg}; color: black;' if col == 'Ach (%)' else '' for col in row.index]
                st.dataframe(df_indiv.style.apply(style_indiv, axis=1), use_container_width=True, hide_index=True, column_config={"Pencapaian": st.column_config.ProgressColumn("Bar", format=" ", min_value=0, max_value=1)})
            else: st.warning("Tidak ada data target brand.")

    with t2:
        st.subheader("📈 Tren Harian")
        if not df_active_tab.empty:
            daily = df_active_tab.groupby('Tanggal')['Jumlah'].sum().reset_index()
            fig_line = px.line(daily, x='Tanggal', y='Jumlah', markers=True)
            fig_line.update_traces(line_color='#2980b9', line_width=3)
            
            # Kunci sumbu agar tidak ter-zoom di HP
            fig_line.update_layout(xaxis=dict(fixedrange=True), yaxis=dict(fixedrange=True))
            
            # Matikan toolbar bawaan Plotly
            st.plotly_chart(fig_line, use_container_width=True, config={'displayModeBar': False})

    with t_detail_sales:
        st.subheader("👥 Detail Sales Team per Brand")
        allowed_brands = []
        if role in ['manager', 'direktur']:
            for spv_brands in TARGET_DATABASE.values(): allowed_brands.extend(spv_brands.keys())
        elif is_supervisor_account: allowed_brands = list(TARGET_DATABASE[my_name_key].keys())
        
        if selected_ijl != "IJL":
            allowed_brands = [b for b in allowed_brands if b in TARGET_DATABASE[selected_ijl].keys()]
            
        if allowed_brands:
            selected_brand_detail = st.selectbox("Pilih Brand untuk Detail Sales:", sorted(set(allowed_brands)))
            if selected_brand_detail:
                sales_stats = []
                total_brand_sales = 0
                total_brand_target = 0
                
                h_1_date = end_date - datetime.timedelta(days=1)
                last_day_of_month = calendar.monthrange(end_date.year, end_date.month)[1]
                remaining_workdays = 0
                for day_int in range(end_date.day, last_day_of_month + 1):
                    c_date = datetime.date(end_date.year, end_date.month, day_int)
                    if c_date.weekday() != 6 and c_date.strftime('%Y-%m-%d') not in HOLIDAYS_2026:
                        remaining_workdays += 1
                        
                safe_remaining_days = remaining_workdays if remaining_workdays > 0 else 1
                
                df_brand_active = df_active_tab[df_active_tab['Merk'] == selected_brand_detail]
                dict_sales_mtd = df_brand_active.groupby('Penjualan')['Jumlah'].sum().to_dict()
                
                df_brand_h1 = df_scope_all[(df_scope_all['Tanggal'].dt.date == h_1_date) & (df_scope_all['Merk'] == selected_brand_detail)]
                dict_sales_h1 = df_brand_h1.groupby('Penjualan')['Jumlah'].sum().to_dict()
                dict_toko_h1 = df_brand_h1.groupby('Penjualan')['Nama Outlet'].nunique().to_dict()
                dict_toko_mtd = df_brand_active.groupby('Penjualan')['Nama Outlet'].nunique().to_dict()
                
                # --- TAMBAHAN: Tarik data omset murni hari ini (Sistem Komputer) ---
                today_sys_date = end_date
                df_brand_today = df_scope_all[(df_scope_all['Tanggal'].dt.date == today_sys_date) & (df_scope_all['Merk'] == selected_brand_detail)]
                dict_sales_today = df_brand_today.groupby('Penjualan')['Jumlah'].sum().to_dict()
                
                for sales_name, targets in INDIVIDUAL_TARGETS.items():
                    if selected_brand_detail in targets:
                        t_pribadi = targets[selected_brand_detail]
                        real_sales = dict_sales_mtd.get(sales_name, 0.0)
                        omset_h1 = dict_sales_h1.get(sales_name, 0.0)
                        toko_h1 = dict_toko_h1.get(sales_name, 0)
                        total_toko_mtd = dict_toko_mtd.get(sales_name, 0)
                        
                        # --- TAMBAHAN: Ambil omset hari ini dari dictionary ---
                        omset_hari_ini = dict_sales_today.get(sales_name, 0.0)
                        
                        gap_sales = t_pribadi - real_sales
                        if gap_sales < 0:
                            gap_sales = 0
                            
                        target_harian = gap_sales / safe_remaining_days
                        
                        sales_stats.append({
                            "Nama Sales": sales_name, 
                            "Target Pribadi": format_idr(t_pribadi), 
                            "Realisasi": format_idr(real_sales),
                            "Ach %": f"{(real_sales/t_pribadi)*100:.1f}%" if t_pribadi > 0 else "0%", 
                            "Gap Sales": format_idr(gap_sales),
                            "Target Harian": format_idr(target_harian),
                            "Omset Hari ini": format_idr(omset_hari_ini), # <--- KOLOM BARU DISISIPKAN DI SINI
                            "Omset H-1": format_idr(omset_h1),
                            "Toko H-1": toko_h1, 
                            "AO MTD": total_toko_mtd,
                            "_real": real_sales, "_target": t_pribadi
                        })
                        total_brand_sales += real_sales; total_brand_target += t_pribadi
                
                if sales_stats:
                    df_sales_stats = pd.DataFrame(sales_stats).drop(columns=["_real", "_target"])
                    def style_sales_stats(row):
                        try: val = float(row['Ach %'].replace('%', '')) / 100
                        except: val = 0
                        bg = get_color_achv(val)
                        return [f'background-color: {bg}; color: black;' if col == 'Ach %' else '' for col in row.index]
                    
                    st.dataframe(df_sales_stats.style.apply(style_sales_stats, axis=1), use_container_width=True)
                    
                    m1, m2, m3 = st.columns(3)
                    m1.metric(f"Total Target {selected_brand_detail}", format_idr(total_brand_target))
                    m2.metric(f"Total Omset {selected_brand_detail}", format_idr(total_brand_sales))
                    ach_total = (total_brand_sales/total_brand_target)*100 if total_brand_target > 0 else 0
                    m3.metric("Total Ach %", f"{ach_total:.1f}%")
                else: st.info(f"Belum ada data target sales individu untuk brand {selected_brand_detail}")
        else: st.info("Menu ini khusus untuk melihat detail tim sales per brand.")

    with t_detail_harian:
        st.subheader("📝 Monitoring Salesman Harian")
        st.caption("Rekapitulasi pencapaian target harian, EC, dan rincian orderan per toko layaknya Excel.")
        
        @st.fragment
        def render_detail_sales_harian():
            df_base_harian = df.copy()
            
            if df_base_harian.empty:
                st.info("Tidak ada data dasar.")
                return
                
            # --- KAMUS SEMENTARA: DALAM & LUAR KOTA ---
            # --- 1. KAMUS MASTER MODERN TRADE (MT) ---
            DAFTAR_MT = [
            ]

            # --- 2. KAMUS MASTER LUAR KOTA (GABUNGAN AREA 1, 2, & 3) ---
            DAFTAR_LUAR_KOTA = [

            ]
            
            # --- 3. KAMUS MASTER DALAM KOTA (GABUNGAN AREA 1, 2, & 3) ---
            DAFTAR_DALAM_KOTA = [

            ]

            # --- 4. KAMUS ALIAS / KATA KUNCI (MENGATASI SALAH KETIK FAKTURIS) ---
            STORE_ALIASES = {

            }

            # --- MESIN TURBO: PEMETAAN OTOMATIS (VECTORIZATION PANDAS) ---
            # Jauh lebih cepat (0.05 detik) karena tidak menggunakan loop baris per baris
            
            s_col = df_base_harian['Penjualan'].astype(str).str.upper().str.strip()
            k_col = df_base_harian['Kota'].astype(str).str.upper().str.strip()
            t_col = df_base_harian['Nama Outlet'].astype(str).str.upper().str.strip()

            # Normalisasi nama toko berdasarkan Kamus Alias secara instan
            for alias, target_name in STORE_ALIASES.items():
                t_col = t_col.str.replace(alias, target_name, regex=False)
            
            # Inisialisasi Nilai Default
            df_base_harian['Area'] = 'Area Lainnya'
            df_base_harian['Kategori Wilayah'] = 'Lainnya / Belum Terpetakan'
            
            # --- LAYER 1: PRIORITAS UTAMA (MODERN TRADE / MT) ---
            set_mt = set(DAFTAR_MT)
            mask_mt = t_col.isin(set_mt) | s_col.isin(['RISKA MT', 'ROZY MT'])

            df_base_harian.loc[mask_mt, 'Area'] = 'Area MT'
            df_base_harian.loc[mask_mt, 'Kategori Wilayah'] = 'Semua MT'
            
            # --- LAYER 2: WILAYAH SALESMAN & AREA REGULER (NON-MT) ---
            # Hanya memproses toko yang TIDAK masuk keranjang MT (Mencegah Overlap)
            non_mt_mask = ~mask_mt

            mask_sri = s_col.str.contains('SRI RAHMADHANI') | s_col.str.contains('SRI RAMADHANI')
            mask_siantar = k_col.str.contains('SIANTAR') | k_col.str.contains('PEMATANGSIANTAR')
            
            df_base_harian.loc[non_mt_mask & mask_sri & mask_siantar, 'Area'] = 'Area Siantar'
            df_base_harian.loc[non_mt_mask & s_col.isin(['BASTIAN']), 'Area'] = 'Area 3'
            df_base_harian.loc[non_mt_mask & s_col.isin(['GANI', 'HAMZAH', 'FANDI', 'RIZKI']), 'Area'] = 'Area 2'
            df_base_harian.loc[non_mt_mask & s_col.isin(['FERY', 'SANTI', 'DINA']), 'Area'] = 'Area 1'
            df_base_harian.loc[non_mt_mask & s_col.isin(['RAPI', 'WIRA', 'DEVI', 'MAWAR', 'ADE', 'THERESYA', 'DWI']), 'Area'] = 'Area 1 & 3'
            df_base_harian.loc[non_mt_mask & (s_col == 'BAYU'), 'Area'] = 'Area 2 & 3'
            
            # --- LAYER 3: KLASIFIKASI DALAM / LUAR KOTA (UNTUK AREA REGULER) ---
            set_dalam = set(DAFTAR_DALAM_KOTA)
            set_luar = set(DAFTAR_LUAR_KOTA)
            
            df_base_harian.loc[non_mt_mask & t_col.isin(set_dalam), 'Kategori Wilayah'] = 'Dalam Kota'
            df_base_harian.loc[non_mt_mask & t_col.isin(set_luar), 'Kategori Wilayah'] = 'Luar Kota'

            # 1. UI FILTER RENTANG WAKTU, MULTI SALESMAN, LOKASI, BRAND & KATEGORI
            list_sales = sorted(df_base_harian['Penjualan'].astype(str).unique())
            list_sales = [s for s in list_sales if s != 'Non-Sales']
            
            list_provinsi = sorted(df_base_harian['Provinsi'].astype(str).dropna().unique()) if 'Provinsi' in df_base_harian.columns else []
            list_kota = sorted(df_base_harian['Kota'].astype(str).dropna().unique()) if 'Kota' in df_base_harian.columns else []
            list_brand = sorted(df_base_harian['Merk'].astype(str).dropna().unique())
            list_area = sorted(df_base_harian['Area'].astype(str).unique())
            
            with st.form(key="form_filter_harian"):
                col_h1, col_h2 = st.columns(2)
                with col_h1:
                    f_tanggal = st.date_input("🗓️ Pilih Rentang Tanggal:", value=(datetime.date.today(), datetime.date.today()))
                with col_h2:
                    # Menambahkan opsi Auto-Detect jika kolom ini dibiarkan kosong namun filter Area diisi
                    f_sales = st.multiselect("👤 Pilih Salesman (Kosongkan jika ingin menarik dari Area):", list_sales)
                
                st.markdown("<hr style='margin: 0.5em 0;'>", unsafe_allow_html=True)
                
                # Membelah menjadi 5 kolom agar Filter Kategori bisa masuk
                col_f1, col_f2, col_f3, col_f4, col_f5 = st.columns(5)
                with col_f1:
                    f_area = st.multiselect("📍 Area:", list_area)
                with col_f2:
                    f_kategori = st.selectbox("🏘️ Kategori:", ["Semua", "Dalam Kota", "Luar Kota"])
                with col_f3:
                    f_prov = st.multiselect("🗺️ Provinsi:", list_provinsi)
                with col_f4:
                    f_kota = st.multiselect("🏙️ Kota:", list_kota)
                with col_f5:
                    f_brand = st.multiselect("📦 Brand:", list_brand)
                    
                submit_harian = st.form_submit_button("🔍 Tampilkan Detail")
                
            # --- LOGIKA AUTO-DETECT SALESMAN & VALIDASI ---
            # Jika user memilih Area tapi mengosongkan Salesman, sistem akan otomatis mengambil semua sales di Area tersebut
            if submit_harian and not f_sales and f_area:
                f_sales = df_base_harian[df_base_harian['Area'].isin(f_area)]['Penjualan'].astype(str).unique().tolist()
                
            if not f_sales or not submit_harian:
                st.info("Silakan pilih rentang tanggal, nama salesman (atau pilih Area), lalu klik 'Tampilkan Detail'.")
                return
                
            if isinstance(f_tanggal, tuple):
                if len(f_tanggal) == 2:
                    start_date, end_date = f_tanggal
                else:
                    start_date = end_date = f_tanggal[0]
            else:
                start_date = end_date = f_tanggal

            # --- PENYARINGAN DATA MASTER SECARA GLOBAL ---
            df_master_filtered = df_base_harian.copy()
            
            # Eksekusi Seluruh Filter
            if f_area:
                df_master_filtered = df_master_filtered[df_master_filtered['Area'].isin(f_area)]
            if f_kategori != "Semua":
                df_master_filtered = df_master_filtered[df_master_filtered['Kategori Wilayah'] == f_kategori]
            if f_prov and 'Provinsi' in df_master_filtered.columns:
                df_master_filtered = df_master_filtered[df_master_filtered['Provinsi'].isin(f_prov)]
            if f_kota and 'Kota' in df_master_filtered.columns:
                df_master_filtered = df_master_filtered[df_master_filtered['Kota'].isin(f_kota)]
            if f_brand:
                df_master_filtered = df_master_filtered[df_master_filtered['Merk'].isin(f_brand)]

            # 2. LOGIKA HARI KERJA PERIODE & SISA BULAN
            period_workdays = 0
            curr_date = start_date
            while curr_date <= end_date:
                if curr_date.weekday() != 6 and curr_date.strftime('%Y-%m-%d') not in HOLIDAYS_2026:
                    period_workdays += 1
                curr_date += datetime.timedelta(days=1)
            if period_workdays == 0: period_workdays = 1
            
            last_day_of_month = calendar.monthrange(start_date.year, start_date.month)[1]
            remaining_workdays = 0
            for day_int in range(start_date.day, last_day_of_month + 1):
                c_date = datetime.date(start_date.year, start_date.month, day_int)
                if c_date.weekday() != 6 and c_date.strftime('%Y-%m-%d') not in HOLIDAYS_2026:
                    remaining_workdays += 1
            safe_rem_days = remaining_workdays if remaining_workdays > 0 else 1
            
            start_str = start_date.strftime('%Y-%m-%d')
            end_str = end_date.strftime('%Y-%m-%d')
            start_of_month = start_date.replace(day=1)
            month_str = start_of_month.strftime('%Y-%m-%d')

            # 3. FUNGSI PEMBUAT TABEL HTML
            def build_sales_html_table(data_list):
                html_rows = ""
                tot_target = 0
                tot_omset = 0
                
                for idx, data in enumerate(data_list, 1):
                    brand = data['Brand']
                    rowspan = len(data['Outlets']) + 1 if len(data['Outlets']) > 0 else 2 
                    
                    tot_target += data['Target Periode']
                    tot_omset += data['Omset Period']
                    
                    fmt_target = format_idr(data['Target Periode'])
                    fmt_omset = format_idr(data['Omset Period'])
                    
                    html_rows += f'''
                    <tr>
                        <td class="bg-blue" rowspan="{rowspan}"><b>{idx}</b></td>
                        <td class="bg-blue" rowspan="{rowspan}"><b>{brand}</b></td>
                        <td class="bg-yellow"><b>{data['Target EC']}</b></td>
                        <td class="bg-yellow"><b>{data['EC']}</b></td>
                        <td class="bg-yellow" rowspan="{rowspan}"><b>{fmt_target}</b></td>
                        <td class="bg-yellow text-right"><b>{fmt_omset}</b></td>
                        <td class="bg-pink" rowspan="{rowspan}">{data['Achiev']:.0f}%</td>
                    </tr>
                    '''
                    if len(data['Outlets']) > 0:
                        for out in data['Outlets']:
                            fmt_out_omset = format_idr(out['Jumlah'])
                            html_rows += f'''
                            <tr>
                                <td class="bg-blue text-left" colspan="2">{out['Nama Outlet']}</td>
                                <td class="bg-blue text-right">{fmt_out_omset}</td>
                            </tr>
                            '''
                    else:
                        html_rows += f'''
                            <tr>
                                <td class="bg-blue text-left" colspan="2">-</td>
                                <td class="bg-blue text-right">Rp 0</td>
                            </tr>
                            '''
                            
                tot_achiev = (tot_omset / tot_target) * 100 if tot_target > 0 else 0
                fmt_tot_target = format_idr(tot_target)
                fmt_tot_omset = format_idr(tot_omset)
                
                return f'''
                <table class="sales-table">
                    <thead>
                        <tr>
                            <th width="5%">No</th>
                            <th width="15%">Brand</th>
                            <th width="10%">Target EC</th>
                            <th width="12%">Effective Call</th>
                            <th width="20%">Target Sales Periode</th>
                            <th width="20%">Nominal Orderan</th>
                            <th width="8%">Achiev</th>
                        </tr>
                    </thead>
                    <tbody>
                        {html_rows}
                        <tr>
                            <td class="bg-yellow" colspan="4"><b>Total</b></td>
                            <td class="bg-yellow text-right"><b>{fmt_tot_target}</b></td>
                            <td class="bg-yellow text-right"><b>{fmt_tot_omset}</b></td>
                            <td class="bg-pink">{tot_achiev:.0f}%</td>
                        </tr>
                    </tbody>
                </table>
                '''

            # 4. PROSES DATA KESELURUHAN DAN INDIVIDU
            html_style = '''
            <style>
            .sales-table { width: 100%; border-collapse: collapse; font-family: Calibri, sans-serif; font-size: 15px; margin-top: 10px; margin-bottom: 40px;}
            .sales-table th, .sales-table td { border: 1px solid #000; padding: 6px 10px; text-align: center; vertical-align: middle; }
            .sales-table th { background-color: #f2f2f2; font-weight: bold; }
            .bg-yellow { background-color: #ffff00 !important; color: black !important; }
            .bg-blue { background-color: #bdd7ee !important; color: black !important; }
            .bg-pink { background-color: #f8cbad !important; font-weight: bold !important; color: #c00000 !important; }
            .text-left { text-align: left !important; }
            .text-right { text-align: right !important; }
            </style>
            '''
            
            all_html_output = html_style + '<div style="background-color: white; padding: 20px; border-radius: 10px; overflow-x: auto; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">'
            
            agg_data = {}
            individual_htmls = ""
            
            for sales in f_sales:
                # Menggunakan df_master_filtered agar omset dan RO terkunci sesuai filter lokasi
                df_period = df_master_filtered[(df_master_filtered['Tanggal'].dt.strftime('%Y-%m-%d') >= start_str) & (df_master_filtered['Tanggal'].dt.strftime('%Y-%m-%d') <= end_str) & (df_master_filtered['Penjualan'] == sales)]
                df_mtd = df_master_filtered[(df_master_filtered['Tanggal'].dt.strftime('%Y-%m-%d') >= month_str) & (df_master_filtered['Tanggal'].dt.strftime('%Y-%m-%d') < start_str) & (df_master_filtered['Penjualan'] == sales)]
                
                sales_targets = INDIVIDUAL_TARGETS.get(sales, {})
                brands_sold_period = df_period[df_period['Jumlah'] > 0]['Merk'].unique()
                brands_with_target = list(sales_targets.keys())
                brands_to_show = sorted(list(set(list(brands_sold_period) + brands_with_target)))
                
                sales_table_data = []
                for brand in brands_to_show:
                    target_bulanan = sales_targets.get(brand, 0)
                    omset_mtd_h1 = df_mtd[df_mtd['Merk'] == brand]['Jumlah'].sum()
                    
                    gap = target_bulanan - omset_mtd_h1
                    if gap < 0: gap = 0
                    target_harian = gap / safe_rem_days if target_bulanan > 0 else 0
                    target_periode = target_harian * period_workdays
                    
                    df_brand_period = df_period[df_period['Merk'] == brand]
                    omset_period = df_brand_period['Jumlah'].sum()
                    
                    outlets = df_brand_period.groupby('Nama Outlet')['Jumlah'].sum().reset_index()
                    outlets = outlets[outlets['Jumlah'] > 0].sort_values('Jumlah', ascending=False)
                    ec = len(outlets)
                    achiev = (omset_period / target_periode) * 100 if target_periode > 0 else 0
                    
                    df_hist_brand = df_master_filtered[(df_master_filtered['Penjualan'] == sales) & (df_master_filtered['Merk'] == brand) & (df_master_filtered['Tanggal'].dt.strftime('%Y-%m-%d') <= end_str)]
                    total_ro = df_hist_brand['Nama Outlet'].nunique()
                    target_ec_bulanan = total_ro * 0.60
                    
                    ec_mtd_h1 = df_mtd[df_mtd['Merk'] == brand]['Nama Outlet'].nunique()
                    gap_ec = target_ec_bulanan - ec_mtd_h1
                    if gap_ec < 0: gap_ec = 0
                    
                    target_ec_harian = int(np.ceil(gap_ec / safe_rem_days)) if target_ec_bulanan > 0 else 0
                    target_ec_periode = target_ec_harian * period_workdays
                    
                    if target_bulanan > 0 or omset_period > 0 or target_ec_periode > 0:
                        sales_table_data.append({'Brand': brand, 'Target EC': target_ec_periode, 'EC': ec, 'Target Periode': target_periode, 'Omset Period': omset_period, 'Achiev': achiev, 'Outlets': outlets.to_dict('records')})
                        
                        if brand not in agg_data:
                            agg_data[brand] = {'Target EC': 0, 'EC': 0, 'Target Periode': 0, 'Omset Period': 0, 'Outlets': []}
                        agg_data[brand]['Target EC'] += target_ec_periode
                        agg_data[brand]['EC'] += ec
                        agg_data[brand]['Target Periode'] += target_periode
                        agg_data[brand]['Omset Period'] += omset_period
                        agg_data[brand]['Outlets'].extend(outlets.to_dict('records'))
                        
                if sales_table_data:
                    individual_htmls += f'<h3 style="color:black; margin-top:30px; border-bottom: 2px solid black;">Tabel Rincian: {sales}</h3>'
                    individual_htmls += build_sales_html_table(sales_table_data)
                    
            if agg_data:
                agg_table_data = []
                for brand, data in agg_data.items():
                    achiev = (data['Omset Period'] / data['Target Periode']) * 100 if data['Target Periode'] > 0 else 0
                    df_outlets = pd.DataFrame(data['Outlets'])
                    if not df_outlets.empty:
                        df_outlets = df_outlets.groupby('Nama Outlet')['Jumlah'].sum().reset_index().sort_values('Jumlah', ascending=False)
                        outlets_list = df_outlets.to_dict('records')
                    else:
                        outlets_list = []
                        
                    agg_table_data.append({'Brand': brand, 'Target EC': data['Target EC'], 'EC': data['EC'], 'Target Periode': data['Target Periode'], 'Omset Period': data['Omset Period'], 'Achiev': achiev, 'Outlets': outlets_list})
                
                sales_names = ", ".join(f_sales)
                all_html_output += f'''
                    <div style="margin-bottom: 20px; color: black; font-family: Calibri, sans-serif;">
                        <table style="border: none; font-size: 15px; text-align: left; width: 450px;">
                            <tr><td colspan="2" style="padding-bottom: 10px; font-size: 16px;">Monitoring Salesman (Periode)</td></tr>
                            <tr><td width="35%">Rentang Tanggal</td><td><b>{start_date.strftime('%d/%m/%Y')} s/d {end_date.strftime('%d/%m/%Y')}</b></td></tr>
                            <tr><td>Salesman Terpilih</td><td><b>{sales_names}</b></td></tr>
                        </table>
                    </div>
                '''
                all_html_output += '<h3 style="color:black; background-color:#e7e6e6; padding:10px; text-align:center;">TABEL KESELURUHAN (GABUNGAN)</h3>'
                all_html_output += build_sales_html_table(agg_table_data)
                all_html_output += individual_htmls
            else:
                all_html_output += "<p style='color:black;'>Tidak ada transaksi atau target pada rentang waktu dan salesman tersebut.</p>"
                
            all_html_output += '</div>'
            
            # Merender seluruh string HTML dalam satu layar iframe
            components.html(all_html_output, height=1200, scrolling=True)
            
        # Panggil fragment ke layar
        render_detail_sales_harian()

    with t3:
        st.subheader("📊 Pareto Analysis (80/20 Rule)")
        selected_brand_t3 = st.selectbox("Pilih Brand (Filter Top Produk & Outlet):", ["SEMUA"] + sorted(df_active_tab['Merk'].dropna().astype(str).unique()))
        df_t3 = df_active_tab if selected_brand_t3 == "SEMUA" else df_active_tab[df_active_tab['Merk'] == selected_brand_t3]

        st.caption("Produk yang berkontribusi terhadap 80% dari total omset.")
        
        grouped_barang = df_t3.groupby('Nama Barang')['Jumlah'].sum().reset_index().sort_values('Jumlah', ascending=False)
        total_omset_pareto = grouped_barang['Jumlah'].sum()
        
        if total_omset_pareto > 0:
            pareto_df = grouped_barang.copy()
            pareto_df['Kontribusi %'] = (pareto_df['Jumlah'] / total_omset_pareto) * 100
            pareto_df['Cumulative %'] = pareto_df['Kontribusi %'].cumsum()
            
            top_performers = pareto_df[pareto_df['Cumulative %'] <= 80].copy()
            top_performers.insert(0, '🏆 Rank', range(1, len(top_performers) + 1))
            
            col_pareto1, col_pareto2 = st.columns(2)
            col_pareto1.metric("Total SKU Terjual", len(pareto_df))
            col_pareto2.metric("Produk Kontributor Utama (80%)", len(top_performers))
            
            st.dataframe(
                top_performers[['🏆 Rank', 'Nama Barang', 'Jumlah', 'Kontribusi %']].style.format({'Jumlah': 'Rp {:,.0f}', 'Kontribusi %': '{:.2f}%'}),
                use_container_width=True, hide_index=True
            )
        
        st.divider()
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("📦 Top 10 Produk")
            top_prod = grouped_barang.head(10).copy() if not grouped_barang.empty else pd.DataFrame(columns=['Nama Barang', 'Jumlah'])
            fig_bar = px.bar(top_prod, x='Jumlah', y='Nama Barang', orientation='h', text_auto='.2s')
            fig_bar.update_layout(yaxis={'categoryorder':'total ascending', 'fixedrange': True}, xaxis={'fixedrange': True})
            st.plotly_chart(fig_bar, use_container_width=True, config={'displayModeBar': False})
        with c2:
            st.subheader("🏪 Top 10 Outlet")
            top_out = df_t3.groupby('Nama Outlet')['Jumlah'].sum().nlargest(10).reset_index()
            fig_out = px.bar(top_out, x='Jumlah', y='Nama Outlet', orientation='h', text_auto='.2s', color_discrete_sequence=['#2980b9'])
            fig_out.update_layout(yaxis={'categoryorder':'total ascending', 'fixedrange': True}, xaxis={'fixedrange': True})
            st.plotly_chart(fig_out, use_container_width=True, config={'displayModeBar': False})
            
    with t5:
        st.subheader("🚀 Kejar Omset (Actionable Insights)")
        st.write("#### 🚨 Toko Tidur (Potensi Hilang)")
        st.caption("Toko yang bertransaksi di masa lalu tetapi TIDAK bertransaksi di periode yang dipilih.")
        
        all_outlets = df_scope_all['Nama Outlet'].unique()
        active_outlets = df_active_tab['Nama Outlet'].unique()
        sleeping_outlets = list(set(all_outlets) - set(active_outlets))
        
        if sleeping_outlets:
            st.warning(f"Ada {len(sleeping_outlets)} toko yang belum order di periode ini.")
            with st.expander("Lihat Daftar Toko Tidur"):
                last_trx = []
                sleeping_df = df_scope_all[df_scope_all['Nama Outlet'].isin(sleeping_outlets)]
                last_dates = sleeping_df.groupby('Nama Outlet')['Tanggal'].max()
                sales_handlers = sleeping_df.groupby('Nama Outlet')['Penjualan'].first()
                
                today_date = datetime.date.today()
                
                for outlet in sleeping_outlets:
                    last_date = last_dates.get(outlet, pd.to_datetime('2000-01-01'))
                    sales_handler = sales_handlers.get(outlet, "-")
                    
                    if last_date.year == 2000:
                        terakhir_order_str = "Belum Pernah Order (Toko Master)"
                        hari_sejak = 99999
                    else:
                        terakhir_order_str = last_date.strftime('%d %b %Y')
                        hari_sejak = (today_date - last_date.date()).days
                        
                    last_trx.append({"Nama Toko": outlet, "Sales": sales_handler, "Terakhir Order": terakhir_order_str, "Hari Sejak Order Terakhir": hari_sejak})
                
                df_sleeping = pd.DataFrame(last_trx).sort_values("Hari Sejak Order Terakhir")
                
                # --- FIX PYARROW: Konversi seluruh kolom menjadi teks sebelum di-replace ---
                df_sleeping["Hari Sejak Order Terakhir"] = df_sleeping["Hari Sejak Order Terakhir"].astype(str).replace("99999", "Baru/Master")
                
                st.dataframe(df_sleeping, use_container_width=True)
        else: st.success("Semua toko langganan sudah order di periode ini.")

        st.divider()
        
        # --- FUNGSI HELPER PEMOTONG WAKTU (Digunakan oleh White Space & AI) ---
        def filter_waktu_cs(df_source, pilihan_tahun, pilihan_bulan):
            df_res = df_source.copy()
            if pilihan_tahun:
                df_res = df_res[df_res['Tanggal'].dt.year.isin(pilihan_tahun)]
                
            thn_now = datetime.date.today().year
            bln_now = datetime.date.today().month
            list_bln = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
            
            if pilihan_bulan == "Bulan Ini":
                df_res = df_res[(df_res['Tanggal'].dt.year == thn_now) & (df_res['Tanggal'].dt.month == bln_now)]
            elif pilihan_bulan in list_bln:
                bln_angka = list_bln.index(pilihan_bulan) + 1
                df_res = df_res[df_res['Tanggal'].dt.month == bln_angka]
            return df_res

        list_bulan_indo = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
        opsi_filter_waktu = ["All Time", "Bulan Ini"] + list_bulan_indo
        list_tahun_tersedia = sorted(df_scope_all['Tanggal'].dt.year.dropna().unique(), reverse=True)
        relevant_brands_all = sorted(df_scope_all['Merk'].dropna().astype(str).unique())

        # =========================================================
        # 💎 PELUANG CROSS-SELLING (WHITE SPACE DUAL-TIME)
        # =========================================================
        st.write("#### 💎 Peluang Cross-Selling (White Space Analysis)")
        st.caption("Bandingkan histori pembelian Brand Acuan dengan target konversi Brand Target pada rentang waktu yang berbeda.")
        
        with st.form(key='white_space_form'):
            col_ws_kiri, col_ws_kanan = st.columns(2)
            
            with col_ws_kiri:
                st.markdown("**🔍 SUMBER (Toko yang sudah beli...)**")
                ws_brand_acuan = st.selectbox("Pilih Brand Acuan:", relevant_brands_all, index=0)
                ws_tahun_acuan = st.multiselect("🗓️ Tahun Acuan:", list_tahun_tersedia, default=list_tahun_tersedia, key="ws_thn_acuan")
                ws_bulan_acuan = st.selectbox("⏳ Bulan Acuan:", opsi_filter_waktu, index=0, key="ws_bln_acuan")
                
            with col_ws_kanan:
                st.markdown("**🎯 TARGET (...tapi BELUM beli)**")
                default_target_idx = 1 if len(relevant_brands_all) > 1 else 0
                ws_brand_target = st.selectbox("Pilih Brand Target:", relevant_brands_all, index=default_target_idx)
                ws_tahun_target = st.multiselect("🗓️ Tahun Target:", list_tahun_tersedia, default=list_tahun_tersedia, key="ws_thn_target")
                
                # Mengatur index default menjadi "Bulan Ini" (index ke-1 di array) untuk Target
                ws_bulan_target = st.selectbox("⏳ Bulan Target:", opsi_filter_waktu, index=1, key="ws_bln_target") 
                
            submit_ws = st.form_submit_button("🚀 Analisis Peluang Cross-Selling", use_container_width=True)

        # Proses White Space jika form disubmit
        if submit_ws:
            if ws_brand_acuan == ws_brand_target:
                st.warning("⚠️ Brand Acuan dan Brand Target tidak boleh sama.")
            else:
                # Membelah DataFrame menjadi dua himpunan waktu yang berbeda
                df_acuan_filtered = filter_waktu_cs(df_scope_all[df_scope_all['Merk'] == ws_brand_acuan], ws_tahun_acuan, ws_bulan_acuan)
                df_target_filtered = filter_waktu_cs(df_scope_all[df_scope_all['Merk'] == ws_brand_target], ws_tahun_target, ws_bulan_target)
                
                outlets_buy_acuan = df_acuan_filtered['Nama Outlet'].unique()
                
                # Gunakan Set() agar proses pencarian 10x lebih cepat
                outlets_buy_target = set(df_target_filtered['Nama Outlet'].unique())
                
                opportunities = []
                for outlet in outlets_buy_acuan:
                    if outlet not in outlets_buy_target:
                        # Tarik nama sales dari data acuan
                        sales_name = df_acuan_filtered[df_acuan_filtered['Nama Outlet'] == outlet]['Penjualan'].iloc[0]
                        opportunities.append({"Nama Toko": outlet, "Salesman": sales_name, "Potensi": f"Tawarkan {ws_brand_target}"})
                
                if opportunities:
                    st.info(f"Ditemukan {len(opportunities)} Toko yang beli {ws_brand_acuan} ({ws_bulan_acuan}) tapi belum beli {ws_brand_target} ({ws_bulan_target}).")
                    st.dataframe(pd.DataFrame(opportunities), use_container_width=True)
                else: 
                    st.success(f"Hebat! Semua toko dari data acuan sudah membeli {ws_brand_target} pada periode target yang ditentukan.")
        
        st.divider()
        
        # =========================================================
        # FRAGMENT AI DAN SKU CROSS-SELLING 
        # =========================================================
        @st.fragment
        def render_cross_sell_sku_ai(df_sumber):
            st.write("#### 🧠 Analisis Cross-Selling Detail (Brand & SKU)")
            st.caption("Eksplorasi cerdas hingga level barang tanpa memuat ulang dasbor utama.")

            col_w1, col_w2 = st.columns(2)
            with col_w1:
                list_tahun_ai = sorted(df_sumber['Tanggal'].dt.year.dropna().unique(), reverse=True)
                ai_tahun = st.multiselect("🗓️ Pilih Tahun:", list_tahun_ai, default=list_tahun_ai, key="ai_sk_thn")
            with col_w2:
                list_bulan_indo = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
                opsi_bulan = ["All Time", "Bulan Ini"] + list_bulan_indo
                ai_bulan = st.selectbox("⏳ Rentang Bulan:", opsi_bulan, index=0, key="ai_sk_bln")

            df_ai_filtered = filter_waktu_cs(df_sumber, ai_tahun, ai_bulan)

            col_f1, col_f2 = st.columns(2)
            with col_f1:
                list_brand = ["Semua Brand"] + sorted(df_ai_filtered['Merk'].dropna().astype(str).unique().tolist())
                pilih_brand = st.selectbox("Pilih Brand:", list_brand, index=0, key="ai_sk_brand")
            
            with col_f2:
                if pilih_brand != "Semua Brand":
                    list_sku = ["Semua SKU"] + sorted(df_ai_filtered[df_ai_filtered['Merk'] == pilih_brand]['Nama Barang'].dropna().astype(str).unique().tolist())
                else:
                    list_sku = ["Semua SKU"] + sorted(df_ai_filtered['Nama Barang'].dropna().astype(str).unique().tolist())
                
                pilih_sku = st.selectbox("Pilih Barang (SKU):", list_sku, index=0, key="ai_sk_sku")

            if st.button("🚀 Jalankan Analisis SKU & Brand", use_container_width=True):
                if pilih_brand == "Semua Brand" and pilih_sku == "Semua SKU":
                    recs_df = get_cross_sell_recommendations(df_ai_filtered)
                    if recs_df is not None and not recs_df.empty:
                        st.success(f"Ditemukan {len(recs_df)} rekomendasi kombinasi barang secara umum.")
                        st.dataframe(recs_df, use_container_width=True)
                    else:
                        st.info("Belum ada pola pembelian yang kuat pada periode ini.")
                
                elif pilih_brand != "Semua Brand" and pilih_sku == "Semua SKU":
                    df_brand_only = df_ai_filtered[df_ai_filtered['Merk'] == pilih_brand]
                    recs_df = get_cross_sell_recommendations(df_brand_only)
                    if recs_df is not None and not recs_df.empty:
                        st.success(f"Ditemukan {len(recs_df)} rekomendasi silang khusus untuk produk {pilih_brand}.")
                        st.dataframe(recs_df, use_container_width=True)
                    else:
                        st.info(f"Belum ada pola cross-selling internal yang kuat untuk {pilih_brand}.")
                
                else:
                    merk_acuan = pilih_brand
                    if merk_acuan == "Semua Brand":
                        merk_search = df_ai_filtered[df_ai_filtered['Nama Barang'] == pilih_sku]['Merk'].iloc[0]
                        merk_acuan = merk_search
                    
                    toko_beli_brand = df_ai_filtered[df_ai_filtered['Merk'] == merk_acuan]['Nama Outlet'].unique()
                    
                    toko_beli_sku = set(df_ai_filtered[df_ai_filtered['Nama Barang'] == pilih_sku]['Nama Outlet'].unique())
                    
                    opportunities = []
                    for outlet in toko_beli_brand:
                        if outlet not in toko_beli_sku:
                            sales_name = df_ai_filtered[df_ai_filtered['Nama Outlet'] == outlet]['Penjualan'].iloc[0]
                            opportunities.append({
                                "Nama Toko": outlet, 
                                "Salesman": sales_name, 
                                "Potensi Tembakan": f"Tawarkan {pilih_sku}"
                            })
                    
                    if opportunities:
                        st.info(f"Ditemukan {len(opportunities)} Toko yang berlangganan merk {merk_acuan} tapi BELUM pernah mengambil {pilih_sku}.")
                        st.dataframe(pd.DataFrame(opportunities), use_container_width=True)
                    else:
                        st.success(f"Luar biasa! Semua toko pelanggan {merk_acuan} sudah rutin mengambil {pilih_sku}.")

        # Memanggil fungsi ke layar
        render_cross_sell_sku_ai(df_scope_all)
        
        st.divider()
        st.write("#### 🗺️ Master Visit Plan (Fokus 80/20 Customer Priority)")
        st.caption("Tabel interaktif (bisa dicentang/diedit). Terapkan 5 Step Sales Visit, Consultative Selling, dan Fast Follow Up pada toko-toko penyumbang 80% omset ini.")
        
        # --- 1. INISIALISASI MEMORI SESI LOKAL ---
        if 'mvp_saved_data' not in st.session_state:
            st.session_state['mvp_saved_data'] = {}
            
        mvp_df = df_active_tab.groupby(['Nama Outlet'])['Jumlah'].sum().reset_index().sort_values('Jumlah', ascending=False)
        total_mvp_omset = mvp_df['Jumlah'].sum()
        
        if total_mvp_omset > 0:
            mvp_df['Cum %'] = (mvp_df['Jumlah'] / total_mvp_omset).cumsum() * 100
            top_outlets_mvp = mvp_df[mvp_df['Cum %'] <= 80].copy()
            
            sales_dict = df_active_tab.groupby('Nama Outlet')['Penjualan'].first().to_dict()
            top_outlets_mvp['Salesman'] = top_outlets_mvp['Nama Outlet'].map(sales_dict)
            
            top_outlets_mvp.insert(0, 'Prioritas', range(1, len(top_outlets_mvp) + 1))
            top_outlets_mvp['📍 Route Plan (Hari)'] = ""
            top_outlets_mvp['📋 5-Step Visit Done'] = False
            top_outlets_mvp['💡 Consultative Action'] = "Cek Stok & Penawaran Baru"
            top_outlets_mvp['🚀 Follow Up Done'] = False
            
            top_outlets_mvp['Omset Historis'] = top_outlets_mvp['Jumlah'].apply(format_idr)
            
            # --- 2. TARIK DATA DARI MEMORI JIKA SUDAH PERNAH DISIMPAN ---
            for idx, row in top_outlets_mvp.iterrows():
                outlet_name = row['Nama Outlet']
                if outlet_name in st.session_state['mvp_saved_data']:
                    saved_vals = st.session_state['mvp_saved_data'][outlet_name]
                    top_outlets_mvp.at[idx, '📍 Route Plan (Hari)'] = saved_vals.get('route', "")
                    top_outlets_mvp.at[idx, '📋 5-Step Visit Done'] = saved_vals.get('step5', False)
                    top_outlets_mvp.at[idx, '💡 Consultative Action'] = saved_vals.get('action', "Cek Stok & Penawaran Baru")
                    top_outlets_mvp.at[idx, '🚀 Follow Up Done'] = saved_vals.get('followup', False)
            
            st.info(f"🎯 Ditemukan {len(top_outlets_mvp)} Toko Prioritas Utama yang mewakili 80% omset Anda. Jadikan daftar ini sebagai panduan rute harian!")
            
            # --- 3. BUNGKUS DENGAN FORM AGAR TIDAK LAG/REFRESH ---
            with st.form("form_master_visit_plan"):
                edited_mvp = st.data_editor(
                    top_outlets_mvp[['Prioritas', 'Nama Outlet', 'Salesman', 'Omset Historis', '📍 Route Plan (Hari)', '📋 5-Step Visit Done', '💡 Consultative Action', '🚀 Follow Up Done']],
                    use_container_width=True,
                    hide_index=True,
                    disabled=['Prioritas', 'Nama Outlet', 'Salesman', 'Omset Historis'], 
                    column_config={
                        "📍 Route Plan (Hari)": st.column_config.SelectboxColumn("Pilih Hari Kunjungan", options=["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu"], required=False),
                    }
                )
                
                # Tombol Simpan di dalam Form
                submit_btn = st.form_submit_button("💾 Simpan Rute Kunjungan", type="primary", use_container_width=True)
                
                if submit_btn:
                    # Simpan isian user ke dalam Session State (Kamus Memori)
                    for _, row in edited_mvp.iterrows():
                        st.session_state['mvp_saved_data'][row['Nama Outlet']] = {
                            'route': row['📍 Route Plan (Hari)'],
                            'step5': row['📋 5-Step Visit Done'],
                            'action': row['💡 Consultative Action'],
                            'followup': row['🚀 Follow Up Done']
                        }
                    st.success("✅ Perencanaan Rute Kunjungan Berhasil Disimpan di Memori Lokal!")
                    time.sleep(1)
                    st.rerun() # Render ulang untuk menghilangkan status 'loading' pada tombol
        else:
            st.info("Belum ada data transaksi yang cukup untuk membuat Master Visit Plan.")
            
    with t_forecast:
        st.subheader("🔮 Prediksi Omset (Forecasting)")
        st.info("Prediksi tren omset 30 hari ke depan berdasarkan data historis harian.")
        
        df_forecast = df_scope_all[df_scope_all['Tanggal'].dt.year > 2000].groupby('Tanggal')['Jumlah'].sum().reset_index().sort_values('Tanggal')
        
        if len(df_forecast) > 10:
            df_forecast['Date_Ordinal'] = df_forecast['Tanggal'].apply(lambda x: x.toordinal())
            x = df_forecast['Date_Ordinal'].values; y = df_forecast['Jumlah'].values
            z = np.polyfit(x, y, 1); p = np.poly1d(z)
            last_date = df_forecast['Tanggal'].max()
            future_days = 30
            future_dates = [last_date + datetime.timedelta(days=i) for i in range(1, future_days + 1)]
            future_ordinals = [d.toordinal() for d in future_dates]
            future_values = p(future_ordinals)
            
            df_history = df_forecast[['Tanggal', 'Jumlah']].copy()
            df_history['Type'] = 'Historis'
            df_future = pd.DataFrame({'Tanggal': future_dates, 'Jumlah': future_values})
            df_future['Type'] = 'Prediksi'
            df_combined = pd.concat([df_history, df_future])
            
            fig_forecast = px.line(df_combined, x='Tanggal', y='Jumlah', color='Type', line_dash='Type', color_discrete_map={'Historis': '#2980b9', 'Prediksi': '#e74c3c'})
            fig_forecast.update_layout(
                title="Proyeksi Omset 30 Hari Kedepan", 
                xaxis_title="Tanggal", 
                yaxis_title="Omset",
                xaxis=dict(fixedrange=True), # Kunci sumbu X
                yaxis=dict(fixedrange=True)  # Kunci sumbu Y
            )
            
            # Matikan toolbar bawaan Plotly
            st.plotly_chart(fig_forecast, use_container_width=True, config={'displayModeBar': False})
            trend = "NAIK 📈" if z[0] > 0 else "TURUN 📉"
            st.write(f"Analisa Tren: Berdasarkan data historis, tren penjualan terlihat {trend}.")
        else: st.warning("Data belum cukup untuk melakukan prediksi (minimal 10 hari transaksi).")

    with t4:
        st.subheader("📋 Data Rincian & Analisis Spesifik")

        tab_pivot, tab_sku, tab_growth, tab_ba = st.tabs(["📊 Pivot Data Customer", "🛒 Detail SKU per Toko", "📈 Rekap Growth Brand", "🎯 Pencapaian Target BA"])
        
        with tab_pivot:
            render_pivot_fragment(df_scope_all, role)

        with tab_sku:
            st.markdown("### 🛒 Detail SKU per Toko")
            df_sku_base = df_scope_all.copy()
            
            list_merk_sku = sorted(df_sku_base['Merk'].dropna().astype(str).unique())
            list_merk_sku = [m for m in list_merk_sku if m != "-"]
            list_tahun_sku = sorted(df_sku_base['Tanggal'].dt.year.dropna().unique(), reverse=True)
            kd_asal = 'Kode_Global' if 'Kode_Global' in df_sku_base.columns else 'Kode Customer'
            
            # Memasukkan semua filter ke dalam satu Form Box agar rapi dan tidak auto-refresh
            with st.form(key='sku_filter_form'):
                col_s1, col_s2 = st.columns(2)
                with col_s1:
                    selected_merk_sku = st.selectbox("🎯 Pilih Merk:", ["SEMUA"] + list_merk_sku, key='merk_sku')
                with col_s2:
                    selected_tahun_sku = st.multiselect("🗓️ Pilih Tahun:", list_tahun_sku, default=list_tahun_sku, key='tahun_sku')
                    
                metric_choice_sku = st.radio("📊 Tampilkan Data Berdasarkan:", ["Value (Omset)", "QTY (Kuantitas)", "Value & QTY"], horizontal=True, key='metric_sku_radio')
                    
                df_sku_for_options = df_sku_base.copy()
                if selected_merk_sku != "SEMUA":
                    df_sku_for_options = df_sku_for_options[df_sku_for_options['Merk'] == selected_merk_sku]
                    
                list_sku_cascading = sorted(df_sku_for_options['Nama Barang'].dropna().astype(str).unique())
                list_kode_all_sku = sorted(df_sku_for_options[kd_asal].astype(str).unique())
                list_nama_all_sku = sorted(df_sku_for_options['Nama Outlet'].astype(str).unique())
                list_provinsi_all_sku = sorted(df_sku_for_options['Provinsi'].astype(str).unique())
                list_kota_all_sku = sorted(df_sku_for_options['Kota'].astype(str).unique())

                st.markdown("#### 🔎 Filter Spesifik (Batch Processing)")
                
                col_f1, col_f2, col_f3, col_f4 = st.columns(4)
                with col_f1: filter_kode_sku = st.multiselect("Kode Customer:", list_kode_all_sku, placeholder="Pilih Kode...")
                with col_f2: filter_nama_sku = st.multiselect("Nama Outlet:", list_nama_all_sku, placeholder="Pilih Outlet...")
                with col_f3: filter_provinsi_sku = st.multiselect("Provinsi:", list_provinsi_all_sku, placeholder="Pilih Provinsi...")
                with col_f4: filter_kota_sku = st.multiselect("Kota:", list_kota_all_sku, placeholder="Pilih Kota...")
                
                filter_sku_spesifik = st.multiselect("📦 Nama Barang (SKU):", list_sku_cascading, placeholder="Pilih SKU spesifik (Kosongkan untuk melihat semua)...")

                maximize_toggle_sku = st.toggle("🗖 Mode Layar Penuh (Tabel Super Lebar)", key='fs_sku')
                submit_button_sku = st.form_submit_button(label='🚀 Terapkan Filter (Super Cepat)', use_container_width=True)

            df_sku_filtered = df_sku_for_options.copy() 

            if selected_tahun_sku:
                df_sku_filtered = df_sku_filtered[df_sku_filtered['Tanggal'].dt.year.isin(selected_tahun_sku)]

            if filter_kode_sku: df_sku_filtered = df_sku_filtered[df_sku_filtered[kd_asal].astype(str).isin(filter_kode_sku)]
            if filter_nama_sku: df_sku_filtered = df_sku_filtered[df_sku_filtered['Nama Outlet'].astype(str).isin(filter_nama_sku)]
            if filter_provinsi_sku: df_sku_filtered = df_sku_filtered[df_sku_filtered['Provinsi'].astype(str).isin(filter_provinsi_sku)]
            if filter_kota_sku: df_sku_filtered = df_sku_filtered[df_sku_filtered['Kota'].astype(str).isin(filter_kota_sku)]
            if filter_sku_spesifik: df_sku_filtered = df_sku_filtered[df_sku_filtered['Nama Barang'].astype(str).isin(filter_sku_spesifik)]

            st.caption(f"Menampilkan transaksi dari {df_sku_filtered['Nama Outlet'].nunique()} toko.")

            if maximize_toggle_sku:
                st.markdown("""
                <style>
                    header {display: none !important;}
                    [data-testid="stSidebar"] {display: none !important;}
                    .block-container { max-width: 100% !important; padding-top: 1rem !important; padding-right: 1rem !important; padding-left: 1rem !important; padding-bottom: 1rem !important; }
                </style>
                """, unsafe_allow_html=True)
                st.info("ℹ️ Mode Layar Penuh aktif. Hilangkan centang pada toggle 'Mode Layar Penuh' di atas untuk kembali.")

            if not df_sku_filtered.empty:
                df_sku_filtered['Bulan Angka'] = df_sku_filtered['Tanggal'].dt.month
                
                if filter_sku_spesifik:
                    index_col = 'Nama Outlet'
                    display_col = 'Nama Toko'
                else:
                    index_col = 'Nama Barang'
                    display_col = 'Nama Barang'
                    
                bulan_indo_map = {1: 'Januari', 2: 'Februari', 3: 'Maret', 4: 'April', 5: 'Mei', 6: 'Juni', 7: 'Juli', 8: 'Agustus', 9: 'September', 10: 'Oktober', 11: 'November', 12: 'Desember'}
                
                if metric_choice_sku == "Value & QTY":
                    pivot_sku = pd.pivot_table(df_sku_filtered, values=['Qty', 'Jumlah'], index=index_col, columns='Bulan Angka', aggfunc='sum', fill_value=0)
                    flat_cols = [f"{col[1]}_{'Value' if col[0]=='Jumlah' else 'QTY'}" for col in pivot_sku.columns]
                    pivot_sku.columns = flat_cols
                    pivot_sku = pivot_sku.reset_index().rename(columns={index_col: display_col})
                    for i in range(1, 13):
                        if f"{i}_QTY" not in pivot_sku.columns: pivot_sku[f"{i}_QTY"] = 0.0
                        if f"{i}_Value" not in pivot_sku.columns: pivot_sku[f"{i}_Value"] = 0.0
                    ren_map = {}
                    num_cols_sku = []
                    for i in range(1, 13):
                        ren_map[f"{i}_QTY"] = f"{bulan_indo_map[i]} (QTY)"
                        ren_map[f"{i}_Value"] = f"{bulan_indo_map[i]} (Value)"
                        num_cols_sku.extend([f"{bulan_indo_map[i]} (QTY)", f"{bulan_indo_map[i]} (Value)"])
                    pivot_sku = pivot_sku.rename(columns=ren_map)
                    pivot_sku['Total QTY'] = pivot_sku[[f"{bulan_indo_map[i]} (QTY)" for i in range(1,13)]].sum(axis=1)
                    pivot_sku['Total Value'] = pivot_sku[[f"{bulan_indo_map[i]} (Value)" for i in range(1,13)]].sum(axis=1)
                    num_cols_sku.extend(['Total QTY', 'Total Value'])
                else:
                    val_col = 'Jumlah' if metric_choice_sku == "Value (Omset)" else 'Qty'
                    pivot_sku = pd.pivot_table(df_sku_filtered, values=val_col, index=index_col, columns='Bulan Angka', aggfunc='sum', fill_value=0).reset_index()
                    pivot_sku = pivot_sku.rename(columns={index_col: display_col})
                    for i in range(1, 13):
                        if i not in pivot_sku.columns: pivot_sku[i] = 0.0
                    cols_sku = [display_col] + list(range(1, 13))
                    pivot_sku = pivot_sku[cols_sku]
                    num_cols_sku = list(bulan_indo_map.values())
                    pivot_sku.columns = [display_col] + num_cols_sku
                    if metric_choice_sku == "QTY (Kuantitas)":
                        pivot_sku['Total QTY'] = pivot_sku[num_cols_sku].sum(axis=1)
                        num_cols_sku.append('Total QTY')
                    else:
                        pivot_sku['Total Penjualan'] = pivot_sku[num_cols_sku].sum(axis=1)
                        num_cols_sku.append('Total Penjualan')
                
                total_dict_sku = {col: "" for col in pivot_sku.columns}
                total_dict_sku[display_col] = "GRAND TOTAL"
                for col in num_cols_sku:
                    val = pivot_sku[col].sum()
                    total_dict_sku[col] = float(val) if pd.notnull(val) else 0.0
                
                df_display_sku = pivot_sku.copy()
                df_display_sku = df_display_sku.loc[:, ~df_display_sku.columns.duplicated()]
                
                df_display_sku[display_col] = df_display_sku[display_col].astype(str)
                df_display_sku_export = pd.concat([df_display_sku, pd.DataFrame([total_dict_sku])], ignore_index=True)
                
                if AGGRID_AVAILABLE:
                    gb_sku = GridOptionsBuilder.from_dataframe(df_display_sku)
                    
                    currency_formatter = JsCode("""
                    function(params) {
                        if (params.value === null || params.value === undefined || params.value === "") return '-';
                        var val = Number(params.value);
                        if (isNaN(val)) return params.value; 
                        return (val < 0 ? '-' : '') + 'Rp ' + Math.abs(val).toLocaleString('id-ID');
                    }
                    """)
                    
                    qty_formatter = JsCode("""
                    function(params) {
                        if (params.value === null || params.value === undefined || params.value === "") return '-';
                        var val = Number(params.value);
                        if (isNaN(val)) return params.value; 
                        return val.toLocaleString('id-ID');
                    }
                    """)
                    
                    for col in df_display_sku.columns:
                        if col in num_cols_sku:
                            if "QTY" in col.upper() or metric_choice_sku == "QTY (Kuantitas)":
                                gb_sku.configure_column(col, type=["numericColumn"], headerClass="right-aligned-header", filter='agNumberColumnFilter', floatingFilter=True, valueFormatter=qty_formatter)
                            else:
                                gb_sku.configure_column(col, type=["numericColumn"], headerClass="right-aligned-header", filter='agNumberColumnFilter', floatingFilter=True, valueFormatter=currency_formatter)
                        elif col == display_col:
                            gb_sku.configure_column(col, pinned='left', filter='agSetColumnFilter', floatingFilter=True)
                        else:
                            gb_sku.configure_column(col, filter='agSetColumnFilter', floatingFilter=True)
                    
                    gb_sku.configure_default_column(resizable=True, sortable=True)
                    
                    getRowHeightSKU = JsCode("""
                    function(params) {
                        if (params.node.rowPinned === 'bottom') return 45;
                        return 40;
                    }
                    """)
                    
                    gb_sku.configure_grid_options(
                        getRowHeight=getRowHeightSKU,
                        headerHeight=45,
                        floatingFiltersHeight=40,
                        pinnedBottomRowData=[total_dict_sku]
                    )
                    
                    getRowStyleSKU = JsCode("""
                    function(params) {
                        if (params.node.rowPinned === 'bottom') {
                            return { 'background-color': '#FFFF00 !important', 'font-weight': 'bold !important', 'color': 'black !important', 'border-top': '3px solid #333 !important' };
                        }
                        return null;
                    }
                    """)
                    gb_sku.configure_grid_options(getRowStyle=getRowStyleSKU)
                    
                    gridOptions_sku = gb_sku.build()
                    
                    custom_css_sku = {
                        ".ag-root-wrapper": {"font-family": "sans-serif !important"},
                        ".ag-header-cell-label": {"font-size": "14px !important", "color": "white !important", "font-weight": "bold !important"},
                        ".ag-header-cell": {"background-color": "#2980b9 !important", "border-right": "1px solid #555555 !important"},
                        ".ag-header": {"background-color": "#2980b9 !important", "border-bottom": "1px solid #555555 !important"},
                        ".ag-header-row-column-filter": {"background-color": "#2980b9 !important"},
                        ".ag-header .ag-icon": {"color": "white !important", "fill": "white !important"},
                        ".ag-cell": {"font-size": "14px !important", "font-weight": "500 !important", "color": "black !important", "background-color": "white !important", "border-right": "1px solid #555555 !important", "border-bottom": "1px solid #555555 !important", "display": "flex", "align-items": "center"},
                        ".ag-row-hover .ag-cell": {"background-color": "#e3f2fd !important"},
                        ".ag-floating-filter-input input": {"font-size": "13px !important", "background-color": "white !important", "color": "black !important", "border-radius": "3px !important", "padding": "2px 5px !important", "border": "1px solid #ccc !important"},
                        ".right-aligned-header .ag-header-cell-label": {"justify-content": "flex-end !important"},
                        ".ag-floating-bottom-container .ag-row, .ag-pinned-left-floating-bottom .ag-row": {"border-top": "3px solid #333 !important"},
                        ".ag-floating-bottom-container .ag-cell, .ag-pinned-left-floating-bottom .ag-cell": {
                            "font-size": "14px !important", "background-color": "#FFFF00 !important", "color": "black !important", "font-weight": "bold !important", "border-right": "none !important"
                        }
                    }
                    
                    try:
                        AgGrid(
                            df_display_sku,
                            gridOptions=gridOptions_sku,
                            allow_unsafe_jscode=True,
                            theme='balham', 
                            height=600,
                            fit_columns_on_grid_load=False,
                            custom_css=custom_css_sku,
                            enable_enterprise_modules=True
                        )
                    except Exception as e:
                        st.warning("⚠️ Tabel interaktif AgGrid mengalami kendala dengan server. Menampilkan tabel standar sebagai alternatif.")
                        st.dataframe(df_display_sku_export, use_container_width=True)
                else:
                    st.error("Library st_aggrid belum terpasang. Fitur Smart Filter tidak bisa ditampilkan.")
                
                user_role_lower = role.lower()
                if user_role_lower == 'direktur':
                    output_sku = io.BytesIO()
                    has_sku_data = 'df_display_sku_export' in locals() and not df_display_sku_export.empty
                    with pd.ExcelWriter(output_sku, engine='xlsxwriter') as writer:
                        if has_sku_data:
                            df_display_sku_export.to_excel(writer, index=False, sheet_name='Detail SKU')
                            workbook = writer.book
                            worksheet = writer.sheets['Detail SKU']
                            user_identity = f"{st.session_state.get('sales_name', 'Unknown')} ({st.session_state.get('role', 'Unknown').upper()})"
                            time_stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            watermark_text = f"CONFIDENTIAL DOCUMENT | TRACKED USER: {user_identity} | DOWNLOADED: {time_stamp} | DO NOT DISTRIBUTE"
                            worksheet.set_header(f'&C&10{watermark_text}')
                            
                            format1 = workbook.add_format({'num_format': '#,##0'})
                            worksheet.set_column('B:N', None, format1)
                            
                            if 'df_display_sku_export' in locals() and not df_display_sku_export.empty:
                                last_row_idx_sku = len(df_display_sku_export)
                                bold_yellow_format_sku = workbook.add_format({'bold': True, 'bg_color': '#FFFF00', 'border': 1, 'num_format': '#,##0', 'font_color': 'black'})
                                worksheet.set_row(last_row_idx_sku, 30, bold_yellow_format_sku)
                        else:
                            pd.DataFrame(["Data Kosong"]).to_excel(writer, index=False, sheet_name='Kosong')
                            
                    if has_sku_data:
                        st.download_button(
                            label="📥 Download Detail SKU (Excel)",
                            data=output_sku.getvalue(),
                            file_name=f"Detail_SKU_{datetime.date.today()}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        )
            else:
                st.warning("Tidak ada transaksi untuk filter tersebut.")

        with tab_growth:
            st.markdown("### 📈 Rekap Growth Brand")
            
            # --- HELPER FUNCTION UNTUK RENDER AGGRID GROWTH (WARNA DINAMIS + FOOTER KUNING + EXCEL EXPORT) ---
            def render_growth_aggrid(df_growth, total_dict_growth=None, pct_col=None, file_prefix="Growth", brand_name=""):
                if not AGGRID_AVAILABLE:
                    st.dataframe(pd.concat([df_growth, pd.DataFrame([total_dict_growth])] if total_dict_growth else [df_growth]), use_container_width=True)
                    return

                gb_growth = GridOptionsBuilder.from_dataframe(df_growth)
                
                currency_formatter = JsCode("""
                function(params) {
                    if (params.value === null || params.value === undefined || params.value === "") return '-';
                    var val = Number(params.value);
                    if (isNaN(val)) return params.value; 
                    return 'Rp ' + val.toLocaleString('id-ID');
                }
                """)
                
                pct_formatter = JsCode("""
                function(params) {
                    if (params.value === null || params.value === undefined || params.value === "") return '-';
                    var val = Number(params.value);
                    if (isNaN(val)) return params.value; 
                    return (val * 100).toFixed(1) + '%';
                }
                """)
                
                # LOGIKA WARNA PERSENTASE
                pct_cell_style = JsCode("""
                function(params) {
                    if (params.node.rowPinned === 'bottom') {
                        return { 'background-color': '#FFFF00', 'font-weight': 'bold', 'color': 'black', 'border-top': '3px solid #333', 'border-right': '1px solid #555555' };
                    }
                    var val = Number(params.value);
                    if (!isNaN(val)) {
                        if (val < 0.50) return { 'background-color': '#ffcccc', 'color': 'black', 'border-right': '1px solid #555555', 'border-bottom': '1px solid #555555', 'font-weight': '500' };
                        else if (val >= 0.50 && val < 0.85) return { 'background-color': '#fff2cc', 'color': 'black', 'border-right': '1px solid #555555', 'border-bottom': '1px solid #555555', 'font-weight': '500' };
                        else return { 'background-color': '#d1e7dd', 'color': 'black', 'border-right': '1px solid #555555', 'border-bottom': '1px solid #555555', 'font-weight': '500' };
                    }
                    return null;
                }
                """)

                for col in df_growth.columns:
                    if col == pct_col:
                        gb_growth.configure_column(col, valueFormatter=pct_formatter, cellStyle=pct_cell_style, headerClass="right-aligned-header", type=["numericColumn"])
                    elif 'SALES' in col.upper():
                        gb_growth.configure_column(col, valueFormatter=currency_formatter, headerClass="right-aligned-header", type=["numericColumn"])
                    else:
                        gb_growth.configure_column(col)

                gb_growth.configure_default_column(resizable=True, sortable=True)
                
                getRowHeightGrowth = JsCode("""
                function(params) {
                    if (params.node.rowPinned === 'bottom') return 45;
                    return 40;
                }
                """)
                
                grid_opts = { "getRowHeight": getRowHeightGrowth, "headerHeight": 45 }
                if total_dict_growth: grid_opts["pinnedBottomRowData"] = [total_dict_growth]
                gb_growth.configure_grid_options(**grid_opts)
                
                getRowStyleGrowth = JsCode("""
                function(params) {
                    if (params.node.rowPinned === 'bottom') {
                        return { 'background-color': '#FFFF00', 'font-weight': 'bold', 'color': 'black', 'border-top': '3px solid #333' };
                    }
                    return null;
                }
                """)
                gb_growth.configure_grid_options(getRowStyle=getRowStyleGrowth)
                
                custom_css_growth = {
                    ".ag-root-wrapper": {"font-family": "sans-serif !important"},
                    ".ag-header-cell-label": {"font-size": "14px !important", "color": "white !important", "font-weight": "bold !important"},
                    ".ag-header-cell": {"background-color": "#2980b9 !important", "border-right": "1px solid #555555 !important"},
                    ".ag-header": {"background-color": "#2980b9 !important", "border-bottom": "1px solid #555555 !important"},
                    ".ag-header-row-column-filter": {"background-color": "#2980b9 !important"},
                    ".ag-header .ag-icon": {"color": "white !important", "fill": "white !important"},
                    ".ag-cell": {"font-size": "14px !important", "font-weight": "500 !important", "color": "black !important", "background-color": "white !important", "border-right": "1px solid #555555 !important", "border-bottom": "1px solid #555555 !important", "display": "flex", "align-items": "center"},
                    ".ag-row-hover .ag-cell": {"background-color": "#e3f2fd !important"},
                    ".ag-floating-filter-input input": {"font-size": "13px !important", "background-color": "white !important", "color": "black !important", "border-radius": "3px !important", "padding": "2px 5px !important", "border": "1px solid #ccc !important"},
                    ".right-aligned-header .ag-header-cell-label": {"justify-content": "flex-end !important"},
                    ".ag-floating-bottom-container .ag-row, .ag-pinned-left-floating-bottom .ag-row": {"border-top": "3px solid #333 !important"},
                    ".ag-floating-bottom-container .ag-cell, .ag-pinned-left-floating-bottom .ag-cell": {
                        "font-size": "14px !important", "background-color": "#FFFF00 !important", "color": "black !important", "font-weight": "bold !important", "border-right": "none !important"
                    }
                }
                
                try:
                    AgGrid(
                        df_growth,
                        gridOptions=gb_growth.build(),
                        allow_unsafe_jscode=True,
                        theme='balham',
                        height=400 if not total_dict_growth else 460,
                        fit_columns_on_grid_load=True,
                        custom_css=custom_css_growth,
                        enable_enterprise_modules=True
                    )
                except Exception:
                    st.dataframe(pd.concat([df_growth, pd.DataFrame([total_dict_growth])] if total_dict_growth else [df_growth]), use_container_width=True)

                # --- EXCEL EXPORT BUTTON ---
                user_role_lower = st.session_state.get('role', 'staff').lower()
                if user_role_lower == 'direktur':
                    df_export = pd.concat([df_growth, pd.DataFrame([total_dict_growth])], ignore_index=True) if total_dict_growth else df_growth.copy()
                    
                    output = io.BytesIO()
                    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                        df_export.to_excel(writer, index=False, sheet_name=file_prefix[:30])
                        workbook = writer.book
                        worksheet = writer.sheets[file_prefix[:30]]
                        user_identity = f"{st.session_state.get('sales_name', 'Unknown')} ({st.session_state.get('role', 'Unknown').upper()})"
                        time_stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        watermark_text = f"CONFIDENTIAL DOCUMENT | TRACKED USER: {user_identity} | DOWNLOADED: {time_stamp} | DO NOT DISTRIBUTE"
                        worksheet.set_header(f'&C&10{watermark_text}')
                        
                        format1 = workbook.add_format({'num_format': '#,##0'})
                        format_pct = workbook.add_format({'num_format': '0.0%'})
                        
                        for col_num, col_name in enumerate(df_export.columns):
                            col_upper = col_name.upper()
                            if col_name in ['RO', 'AO', 'NOO']:
                                worksheet.set_column(col_num, col_num, 10, format1)
                            elif '%' in col_upper or 'GROWTH' in col_upper or 'ACHV' in col_upper:
                                worksheet.set_column(col_num, col_num, 12, format_pct)
                            elif col_upper not in ['MONTH', 'COSTUMER', 'YEAR', 'NAMA OUTLET']:
                                worksheet.set_column(col_num, col_num, 15, format1)
                            else:
                                worksheet.set_column(col_num, col_num, 20)
                        
                        if total_dict_growth:
                            bold_yellow = workbook.add_format({'bold': True, 'bg_color': '#FFFF00', 'border': 1, 'font_color': 'black', 'num_format': '#,##0'})
                            worksheet.set_row(len(df_export), 30, bold_yellow)
                            
                    today_str = datetime.date.today().strftime("%Y%m%d")
                    file_name_clean = re.sub(r'[^A-Za-z0-9_]', '_', f"{file_prefix}_{brand_name}_{today_str}") + ".xlsx"
                    st.download_button(label=f"📥 Download {file_prefix.replace('_', ' ')} (Excel)", data=output.getvalue(), file_name=file_name_clean, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

            # -----------------------------------------------------------------------------------------
            list_merk_growth = sorted(df['Merk'].dropna().astype(str).unique())
            list_merk_growth = [m for m in list_merk_growth if m != "-"]
            
            # Tarik daftar provinsi yang tersedia di database
            list_prov_growth = sorted(df['Provinsi'].dropna().astype(str).unique())
            list_prov_growth = [p for p in list_prov_growth if p not in ["-", "LAIN-LAIN", "", "nan", "None"]]
            
            if list_merk_growth:
                col_grow1, col_grow2 = st.columns(2)
                with col_grow1:
                    brand_growth = st.selectbox("Pilih Brand untuk Analisis Growth:", list_merk_growth)
                with col_grow2:
                    provinsi_growth = st.multiselect("Pilih Provinsi (Kosongkan untuk melihat SEMUA):", list_prov_growth)
                
                df_team_all = df.copy()
                
                # --- APPLY FILTER SALES ---
                if target_sales_filter != "SEMUA":
                    if target_sales_filter.upper() in TARGET_DATABASE:
                        tim_sales_list = list(TARGET_DATABASE[target_sales_filter.upper()].keys())
                        df_team_all = df_team_all[df_team_all['Penjualan'].isin(tim_sales_list)]
                    else:
                        df_team_all = df_team_all[df_team_all['Penjualan'] == target_sales_filter]
                
                # --- APPLY FILTER PROVINSI ---
                if provinsi_growth:
                    df_team_all = df_team_all[df_team_all['Provinsi'].isin(provinsi_growth)]
                
                # FILTER MURNI MEREK
                is_valid_ro = df_team_all['Merk'] == brand_growth
                is_target_brand = df_team_all['Merk'] == brand_growth

                if not df_team_all.empty:
                    df_team_all['Tahun'] = df_team_all['Tanggal'].dt.year
                    df_team_all['Bulan'] = df_team_all['Tanggal'].dt.month
                    df_team_all['Bulan-Tahun'] = df_team_all['Tanggal'].dt.to_period('M')
                    
                    min_period_2026 = pd.Period('2026-01', freq='M')
                    df_base = df_team_all[(df_team_all['Bulan-Tahun'] < min_period_2026) & is_valid_ro]
                    
                    # LOGIKA BARU: MENGHITUNG MURNI BERDASARKAN NAMA OUTLET (Seperti Pivot)
                    ro_accumulated = set(df_base['Nama Outlet'].dropna().unique())
                    growth_data = []
                    
                    for m in range(1, 13):
                        period_str = f"2026-{m:02d}"
                        period = pd.Period(period_str, freq='M')
                        
                        df_ao_current = df_team_all[(df_team_all['Bulan-Tahun'] == period) & is_target_brand]
                        current_ao = set(df_ao_current['Nama Outlet'].dropna().unique())
                        sales = df_ao_current['Jumlah'].sum()
                        
                        noo = len(current_ao - ro_accumulated)
                        
                        df_ro_current = df_team_all[(df_team_all['Bulan-Tahun'] == period) & is_valid_ro]
                        ro_accumulated.update(df_ro_current['Nama Outlet'].dropna().unique())
                        
                        ro = len(ro_accumulated)
                        ao = len(current_ao)
                        ao_vs_ro = (ao / ro) if ro > 0 else 0
                        
                        growth_data.append({
                            'Year': 2026, 'Month': m, 'SALES': sales, 'RO': ro, 'AO': ao, 'AO VS RO %': ao_vs_ro, 'NOO': noo
                        })
                    
                    df_growth_all = pd.DataFrame(growth_data)
                    
                    if not df_growth_all.empty:
                        bulan_dict_short = {1:'Jan', 2:'Feb', 3:'Mar', 4:'Apr', 5:'May', 6:'Jun', 7:'Jul', 8:'Aug', 9:'Sep', 10:'Oct', 11:'Nov', 12:'Dec'}
                        
                        st.divider()
                        st.write(f"#### Tabel 1: Aktivitas Outlet {brand_growth} (Tahun 2026)")
                        df_2026 = df_growth_all[df_growth_all['Year'] == 2026].copy()
                        
                        display_2026 = []
                        for m in range(1, 13):
                            row = df_2026[df_2026['Month'] == m]
                            if not row.empty:
                                r = row.iloc[0]
                                display_2026.append({
                                    'MONTH': f"{bulan_dict_short[m]}-26",
                                    'SALES': r['SALES'],
                                    'RO': int(r['RO']), 'AO': int(r['AO']),
                                    'AO VS RO %': r['AO VS RO %'],
                                    'NOO': int(r['NOO'])
                                })
                            else:
                                display_2026.append({'MONTH': f"{bulan_dict_short[m]}-26", 'SALES': 0.0, 'RO': 0, 'AO': 0, 'AO VS RO %': 0.0, 'NOO': 0})
                        
                        # Render Tabel 1 dengan AgGrid Corporate
                        df_display_t1 = pd.DataFrame(display_2026)
                        render_growth_aggrid(df_display_t1, total_dict_growth=None, pct_col='AO VS RO %', file_prefix="Aktivitas_Outlet", brand_name=brand_growth)
                        
                        st.divider()
                        col_g1, col_g2 = st.columns(2)
                        
                        df_2025 = df_team_all[df_team_all['Tahun'] == 2025] 
                        df_2026_sales = df_growth_all[df_growth_all['Year'] == 2026]
                        
                        def get_sales_2025(df_yr, m, brand_name):
                            res = df_yr[(df_yr['Bulan'] == m) & (df_yr['Merk'] == brand_name)]['Jumlah']
                            return res.sum() if not res.empty else 0
                            
                        def get_sales_2026(df_yr, m):
                            res = df_yr[df_yr['Month'] == m]['SALES']
                            return res.sum() if not res.empty else 0

                        tot_2025 = 0
                        tot_2026 = 0
                        
                        with col_g1:
                            st.write(f"#### Tabel 2: {brand_growth} 2025 vs 2026 Sales Growth")
                            yoy_data = []
                            for m in range(1, 13):
                                s25 = get_sales_2025(df_2025, m, brand_growth)
                                s26 = get_sales_2026(df_2026_sales, m)
                                tot_2025 += s25
                                tot_2026 += s26
                                growth = ((s26 - s25) / s25) if s25 > 0 else (1 if s26 > 0 else 0)
                                yoy_data.append({
                                    'MONTH': bulan_dict_short[m], 'SALES 2025': float(s25), 'SALES 2026': float(s26), 'Growth MTM': float(growth)
                                })
                            
                            df_t2 = pd.DataFrame(yoy_data)
                            tot_growth = ((tot_2026 - tot_2025) / tot_2025) if tot_2025 > 0 else (1 if tot_2026 > 0 else 0)
                            total_dict_t2 = {'MONTH': 'Total Sales', 'SALES 2025': float(tot_2025), 'SALES 2026': float(tot_2026), 'Growth MTM': float(tot_growth)}
                            
                            # Render Tabel 2 dengan AgGrid Corporate
                            render_growth_aggrid(df_t2, total_dict_growth=total_dict_t2, pct_col='Growth MTM', file_prefix="Sales_Growth", brand_name=brand_growth)
                        
                        with col_g2:
                            st.write(f"#### Tabel 3: Quarterly Growth")
                            q_data = []
                            for q, m_start in [('Q1', 1), ('Q2', 4), ('Q3', 7), ('Q4', 10)]:
                                m_end = m_start + 2
                                q_2025 = sum(get_sales_2025(df_2025, m, brand_growth) for m in range(m_start, m_end + 1))
                                q_2026 = sum(get_sales_2026(df_2026_sales, m) for m in range(m_start, m_end + 1))
                                
                                q_growth = ((q_2026 - q_2025) / q_2025) if q_2025 > 0 else (1 if q_2026 > 0 else 0)
                                q_data.append({
                                    'MONTH': f"Total {q}", 'SALES 2025': float(q_2025), 'SALES 2026': float(q_2026), 'Growth MTM': float(q_growth)
                                })
                            
                            df_q = pd.DataFrame(q_data)
                            
                            # Render Tabel 3 dengan AgGrid Corporate
                            render_growth_aggrid(df_q, total_dict_growth=total_dict_t2, pct_col='Growth MTM', file_prefix="Quarterly_Growth", brand_name=brand_growth)
                else:
                    st.info(f"Belum ada data untuk brand {brand_growth}.")
            else:
                st.info("Tidak ada data.")

        with tab_ba:
            st.markdown("### 🎯 Pencapaian Target BA per Brand (Tahun 2026)")
            
            # --- HELPER FUNCTION UNTUK RENDER AGGRID BA (WARNA DINAMIS + FOOTER KUNING + EXCEL EXPORT) ---
            def render_ba_aggrid(df_ba, total_dict_ba=None, file_prefix="Target_BA", brand_name=""):
                if not AGGRID_AVAILABLE:
                    st.dataframe(pd.concat([df_ba, pd.DataFrame([total_dict_ba])] if total_dict_ba else [df_ba]), use_container_width=True)
                    return

                gb_ba = GridOptionsBuilder.from_dataframe(df_ba)
                
                currency_formatter = JsCode("""
                function(params) {
                    if (params.value === null || params.value === undefined || params.value === "") return '-';
                    var val = Number(params.value);
                    if (isNaN(val)) return params.value; 
                    return 'Rp ' + val.toLocaleString('id-ID');
                }
                """)
                
                pct_formatter = JsCode("""
                function(params) {
                    if (params.value === null || params.value === undefined || params.value === "") return '-';
                    var val = Number(params.value);
                    if (isNaN(val)) return params.value; 
                    return (val * 100).toFixed(1) + '%';
                }
                """)
                
                pct_cell_style = JsCode("""
                function(params) {
                    if (params.node.rowPinned === 'bottom') {
                        return { 'background-color': '#FFFF00', 'font-weight': 'bold', 'color': 'black', 'border-top': '3px solid #333', 'border-right': '1px solid #555555' };
                    }
                    var val = Number(params.value);
                    if (!isNaN(val)) {
                        if (val < 0.50) return { 'background-color': '#ffcccc', 'color': 'black', 'border-right': '1px solid #555555', 'border-bottom': '1px solid #555555', 'font-weight': '500' };
                        else if (val >= 0.50 && val < 0.85) return { 'background-color': '#fff2cc', 'color': 'black', 'border-right': '1px solid #555555', 'border-bottom': '1px solid #555555', 'font-weight': '500' };
                        else return { 'background-color': '#d1e7dd', 'color': 'black', 'border-right': '1px solid #555555', 'border-bottom': '1px solid #555555', 'font-weight': '500' };
                    }
                    return null;
                }
                """)

                for col in df_ba.columns:
                    if col == 'ACHV':
                        gb_ba.configure_column(col, valueFormatter=pct_formatter, cellStyle=pct_cell_style, headerClass="right-aligned-header", type=["numericColumn"])
                    elif col != 'Costumer':
                        gb_ba.configure_column(col, valueFormatter=currency_formatter, headerClass="right-aligned-header", type=["numericColumn"])
                    else:
                        # PINNED LEFT UNTUK KOLOM COSTUMER + ENTERPRISE FILTER
                        gb_ba.configure_column(col, pinned='left', filter='agSetColumnFilter', floatingFilter=True)

                gb_ba.configure_default_column(resizable=True, sortable=True)
                
                getRowHeightBA = JsCode("""
                function(params) {
                    if (params.node.rowPinned === 'bottom') return 45;
                    return 40;
                }
                """)
                
                grid_opts = { "getRowHeight": getRowHeightBA, "headerHeight": 45, "floatingFiltersHeight": 40 }
                if total_dict_ba: grid_opts["pinnedBottomRowData"] = [total_dict_ba]
                gb_ba.configure_grid_options(**grid_opts)
                
                getRowStyleBA = JsCode("""
                function(params) {
                    if (params.node.rowPinned === 'bottom') {
                        return { 'background-color': '#FFFF00', 'font-weight': 'bold', 'color': 'black', 'border-top': '3px solid #333' };
                    }
                    return null;
                }
                """)
                gb_ba.configure_grid_options(getRowStyle=getRowStyleBA)
                
                custom_css_ba = {
                    ".ag-root-wrapper": {"font-family": "sans-serif !important"},
                    ".ag-header-cell-label": {"font-size": "14px !important", "color": "white !important", "font-weight": "bold !important"},
                    ".ag-header-cell": {"background-color": "#2980b9 !important", "border-right": "1px solid #555555 !important"},
                    ".ag-header": {"background-color": "#2980b9 !important", "border-bottom": "1px solid #555555 !important"},
                    ".ag-header-row-column-filter": {"background-color": "#2980b9 !important"},
                    ".ag-header .ag-icon": {"color": "white !important", "fill": "white !important"},
                    ".ag-cell": {"font-size": "14px !important", "font-weight": "500 !important", "color": "black !important", "background-color": "white !important", "border-right": "1px solid #555555 !important", "border-bottom": "1px solid #555555 !important", "display": "flex", "align-items": "center"},
                    ".ag-row-hover .ag-cell": {"background-color": "#e3f2fd !important"},
                    ".ag-floating-filter-input input": {"font-size": "13px !important", "background-color": "white !important", "color": "black !important", "border-radius": "3px !important", "padding": "2px 5px !important", "border": "1px solid #ccc !important"},
                    ".right-aligned-header .ag-header-cell-label": {"justify-content": "flex-end !important"},
                    ".ag-floating-bottom-container .ag-row, .ag-pinned-left-floating-bottom .ag-row": {"border-top": "3px solid #333 !important"},
                    ".ag-floating-bottom-container .ag-cell, .ag-pinned-left-floating-bottom .ag-cell": {
                        "font-size": "14px !important", "background-color": "#FFFF00 !important", "color": "black !important", "font-weight": "bold !important", "border-right": "none !important"
                    }
                }
                
                try:
                    AgGrid(
                        df_ba,
                        gridOptions=gb_ba.build(),
                        allow_unsafe_jscode=True,
                        theme='balham',
                        height=400 if not total_dict_ba else 460,
                        fit_columns_on_grid_load=False,
                        custom_css=custom_css_ba,
                        enable_enterprise_modules=True
                    )
                except Exception:
                    st.dataframe(pd.concat([df_ba, pd.DataFrame([total_dict_ba])] if total_dict_ba else [df_ba]), use_container_width=True)

                # --- EXCEL EXPORT BUTTON ---
                user_role_lower = st.session_state.get('role', 'staff').lower()
                if user_role_lower == 'direktur':
                    df_export = pd.concat([df_ba, pd.DataFrame([total_dict_ba])], ignore_index=True) if total_dict_ba else df_ba.copy()
                    
                    output = io.BytesIO()
                    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                        df_export.to_excel(writer, index=False, sheet_name=file_prefix[:30])
                        workbook = writer.book
                        worksheet = writer.sheets[file_prefix[:30]]
                        user_identity = f"{st.session_state.get('sales_name', 'Unknown')} ({st.session_state.get('role', 'Unknown').upper()})"
                        time_stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        watermark_text = f"CONFIDENTIAL DOCUMENT | TRACKED USER: {user_identity} | DOWNLOADED: {time_stamp} | DO NOT DISTRIBUTE"
                        worksheet.set_header(f'&C&10{watermark_text}')
                        
                        format1 = workbook.add_format({'num_format': '#,##0'})
                        format_pct = workbook.add_format({'num_format': '0.0%'})
                        
                        for col_num, col_name in enumerate(df_export.columns):
                            col_upper = col_name.upper()
                            if '%' in col_upper or 'ACHV' in col_upper:
                                worksheet.set_column(col_num, col_num, 12, format_pct)
                            elif col_upper not in ['COSTUMER']:
                                worksheet.set_column(col_num, col_num, 15, format1)
                            else:
                                worksheet.set_column(col_num, col_num, 30)
                        
                        if total_dict_ba:
                            bold_yellow = workbook.add_format({'bold': True, 'bg_color': '#FFFF00', 'border': 1, 'font_color': 'black', 'num_format': '#,##0'})
                            worksheet.set_row(len(df_export), 30, bold_yellow)
                            
                    today_str = datetime.date.today().strftime("%Y%m%d")
                    file_name_clean = re.sub(r'[^A-Za-z0-9_]', '_', f"{file_prefix}_{brand_name}_{today_str}") + ".xlsx"
                    st.download_button(label=f"📥 Download {file_prefix.replace('_', ' ')} (Excel)", data=output.getvalue(), file_name=file_name_clean, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

            # -----------------------------------------------------------------------------------------
            TARGET_BA_PER_BRAND = {
           
            }
            
            available_ba_brands = list(TARGET_BA_PER_BRAND.keys())
            selected_ba_brand = st.selectbox("Pilih Brand untuk melihat Target BA:", available_ba_brands)
            
            if selected_ba_brand:
                current_target_dict = TARGET_BA_PER_BRAND[selected_ba_brand]
                
                df_ba_all = df_scope_all[
                    (df_scope_all['Merk'] == selected_ba_brand) & 
                    (df_scope_all['Nama Outlet'].isin(current_target_dict.keys())) & 
                    (df_scope_all['Tanggal'].dt.year == 2026)
                ].copy()
                
                bulan_dict_ba = {1:'Januari', 2:'Februari', 3:'Maret', 4:'April', 5:'Mei', 6:'Juni', 7:'Juli', 8:'Agustus', 9:'September', 10:'Oktober', 11:'November', 12:'Desember'}
                
                ba_df = pd.DataFrame(list(current_target_dict.items()), columns=['Costumer', 'Target BA'])
                
                if not df_ba_all.empty:
                    df_ba_all['Bulan Angka'] = df_ba_all['Tanggal'].dt.month
                    pivot_ba = pd.pivot_table(df_ba_all, values='Jumlah', index='Nama Outlet', columns='Bulan Angka', aggfunc='sum', fill_value=0)
                    
                    for m in range(1, 13):
                        if m not in pivot_ba.columns:
                            pivot_ba[m] = 0
                            
                    pivot_ba = pivot_ba[list(range(1, 13))]
                    pivot_ba.columns = [bulan_dict_ba[m] for m in pivot_ba.columns]
                    pivot_ba = pivot_ba.reset_index().rename(columns={'Nama Outlet': 'Costumer'})
                    
                    merged_ba = pd.merge(ba_df, pivot_ba, on='Costumer', how='left').fillna(0)
                else:
                    merged_ba = ba_df.copy()
                    for m in range(1, 13):
                        merged_ba[bulan_dict_ba[m]] = 0
                
                st.write(f"Rekap Keseluruhan Toko BA untuk Brand `{selected_ba_brand}` (2026)")
                
                # Tambahkan Grand Total untuk Tabel 1 BA
                total_dict_ba1 = {col: "" for col in merged_ba.columns}
                total_dict_ba1['Costumer'] = "GRAND TOTAL"
                for col in list(bulan_dict_ba.values()) + ['Target BA']:
                    if col in merged_ba.columns:
                        total_dict_ba1[col] = float(merged_ba[col].sum())
                        
                # Render Tabel 1 dengan AgGrid Corporate
                render_ba_aggrid(merged_ba, total_dict_ba=total_dict_ba1, file_prefix="Rekap_Toko_BA", brand_name=selected_ba_brand)
                
                st.divider()
                
                selected_month_ba = st.selectbox(f"Pilih Bulan untuk Detail Achievement ({selected_ba_brand}):", list(bulan_dict_ba.values()))
                
                achv_data = []
                total_target = 0
                total_achv = 0
                
                for idx, row in merged_ba.iterrows():
                    costumer = row['Costumer']
                    target = row['Target BA']
                    pencapaian = row[selected_month_ba]
                    achv_pct = (pencapaian / target) if target > 0 else 0
                    
                    total_target += target
                    total_achv += pencapaian
                    
                    achv_data.append({
                        'Costumer': costumer,
                        'Target BA': float(target),
                        f'Pencapaian {selected_month_ba}': float(pencapaian),
                        'ACHV': float(achv_pct)
                    })
                
                df_achv = pd.DataFrame(achv_data)
                
                total_dict_ba2 = {
                    'Costumer': 'GRAND TOTAL',
                    'Target BA': float(total_target),
                    f'Pencapaian {selected_month_ba}': float(total_achv),
                    'ACHV': float(total_achv/total_target) if total_target > 0 else 0.0
                }
                
                st.write(f"Tabel Pencapaian Target BA `{selected_ba_brand}` - {selected_month_ba} 2026")
                
                # Render Tabel 2 dengan AgGrid Corporate
                render_ba_aggrid(df_achv, total_dict_ba=total_dict_ba2, file_prefix=f"Achv_BA_{selected_month_ba}", brand_name=selected_ba_brand)

                with t_audit:
                    st.subheader("⚠️ Mesin Audit Input Fakturis")
                    st.caption("Cross-Validation Matrix: Mendeteksi kelalaian input, salah rute toko, dan pelanggaran merk (Dilengkapi Form Filter Cepat).")
                    
                    @st.fragment
                    def render_audit_fragment():
                        df_audit_base = df.copy()
                        
                        if df_audit_base.empty:
                            st.info("Tidak ada data dasar untuk diaudit.")
                            return

                        # ==========================================
                        # 1. DATABASE TERITORIAL ABSOLUT
                        # ==========================================
                        SET_AREA_1 = set([
                       ])

                        SET_AREA_2 = set([
                        ])

                        SET_AREA_3 = set([
                        ])

                        SET_MT = set([
                         ])

                        # --- TAMBAHAN KAMUS ALIAS AGAR DIKENALI OLEH MESIN AUDIT ---
                        STORE_ALIASES = {
                           
                        }

                        # Peta Hak Milik Salesman ke Area
                        SALES_AREA_MAP = {
                       }

                        # Peta Otoritas Merk
                        otoritas_brand = {}
                        for spv, brands_dict in TARGET_DATABASE.items():
                            otoritas_brand[spv] = list(brands_dict.keys())
                        for sales, targets in INDIVIDUAL_TARGETS.items():
                            if sales not in otoritas_brand: otoritas_brand[sales] = []
                            otoritas_brand[sales].extend(list(targets.keys()))

                        # --- 2. UI FILTER AUDIT DENGAN FORM ---
                        st.markdown("#### 🔎 Filter Audit Cepat")
                        
                        list_tahun = sorted(df_audit_base['Tanggal'].dt.year.dropna().unique(), reverse=True)
                        list_bulan = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
                        list_merk = sorted(df_audit_base['Merk'].astype(str).unique())
                        list_sales = sorted(df_audit_base['Penjualan'].astype(str).unique())
                        
                        thn_now = datetime.date.today().year
                        default_thn = thn_now if thn_now in list_tahun else (list_tahun[0] if list_tahun else thn_now)
                        
                        with st.form(key="form_filter_audit"):
                            col_f1, col_f2, col_f3, col_f4 = st.columns(4)
                            with col_f1: 
                                f_tahun = st.selectbox("🗓️ Tahun:", list_tahun, index=list_tahun.index(default_thn) if default_thn in list_tahun else 0)
                            with col_f2:
                                f_bulan = st.multiselect("⏳ Bulan (Kosongkan untuk Semua):", list_bulan)
                            with col_f3:
                                f_merk = st.multiselect("📦 Merk (Kosongkan untuk Semua):", list_merk)
                            with col_f4:
                                f_sales = st.multiselect("👤 Sales (Kosongkan untuk Semua):", list_sales)
                                
                            submit_filter = st.form_submit_button("🔍 Terapkan Filter")

                        # --- 3. POTONG DATA SEBELUM DIAUDIT ---
                        df_to_audit = df_audit_base.copy()
                        df_to_audit = df_to_audit[df_to_audit['Tanggal'].dt.year == f_tahun]
                        
                        if f_bulan:
                            bulan_angka = [list_bulan.index(b) + 1 for b in f_bulan]
                            df_to_audit = df_to_audit[df_to_audit['Tanggal'].dt.month.isin(bulan_angka)]
                        if f_merk:
                            df_to_audit = df_to_audit[df_to_audit['Merk'].isin(f_merk)]
                        if f_sales:
                            df_to_audit = df_to_audit[df_to_audit['Penjualan'].isin(f_sales)]

                        # --- 4. EKSEKUSI PEMERIKSAAN BARIS PER BARIS (AUDIT 2.0) ---
                        if not df_to_audit.empty:
                            anomali_data = []
                            
                            for _, row in df_to_audit.iterrows():
                                kasus = []
                                toko_asli = str(row.get('Nama Outlet', '')).strip().upper()
                                
                                # Terapkan Alias agar nama toko seragam saat diaudit
                                toko = toko_asli
                                for alias, target_name in STORE_ALIASES.items():
                                    if alias in toko:
                                        toko = toko.replace(alias, target_name)

                                merk = row['Merk']
                                sales_asli = str(row.get('Sales_Asli_Fakturis', '-')).strip()
                                if sales_asli.lower() in ['nan', 'none', '', '-']:
                                    sales_asli = 'Kosong (Belum Diinput)'
                                    
                                sales_clean = row['Penjualan']
                                status_awal = row.get('Status_Awal_Mesin', 'Non-Sales')
                                tanggal_val = row['Tanggal']
                                no_faktur = str(row.get('No Faktur', '-')).strip()
                                
                                # Cek Kekosongan Input
                                if status_awal == 'Non-Sales' and sales_clean != 'Non-Sales':
                                    kasus.append("❌ Input nama kosong/berantakan")
                                elif status_awal == 'Non-Sales' and sales_clean == 'Non-Sales':
                                    kasus.append("❌ Input kosong & Toko tidak punya histori")
                                    
                                if sales_clean != 'Non-Sales':
                                    # Aturan 1: Cek Wewenang Merk
                                    allowed_brands = otoritas_brand.get(sales_clean, [])
                                    if merk not in allowed_brands and merk != "-":
                                        kasus.append(f"⛔ Pelanggaran Merk: {sales_clean} dilarang jual {merk}")
                                    
                                    # Aturan 2: Cek Kepemilikan Area & NOO
                                    toko_areas = []
                                    is_registered = False
                                    
                                    if toko in SET_MT:
                                        is_registered = True
                                        toko_areas.append('Area MT')
                                    if toko in SET_AREA_1:
                                        is_registered = True
                                        toko_areas.append('Area 1')
                                    if toko in SET_AREA_2:
                                        is_registered = True
                                        toko_areas.append('Area 2')
                                    if toko in SET_AREA_3:
                                        is_registered = True
                                        toko_areas.append('Area 3')
                                        
                                    if not is_registered:
                                        kasus.append("⚠️ Toko Belum Terdaftar / Salah Ketik")
                                    else:
                                        # Pengecualian Overlap Toko Ganda (MT & Area 3)
                                        if toko in ['WILLOW MART', 'V- MART COFFEE', 'V-MART COFFEE']:
                                            toko_areas = ['Area MT', 'Area 3']
                                        
                                        # Cek Teritori Siantar Khusus Sri Ramadhani
                                        if 'SRI RAHMADHANI' in sales_clean or 'SRI RAMADHANI' in sales_clean:
                                            kota_toko = str(row.get('Kota', '')).upper()
                                            if 'SIANTAR' not in kota_toko and 'PEMATANGSIANTAR' not in kota_toko:
                                                kasus.append(f"🗺️ Pelanggaran Teritori: {sales_clean} hanya boleh di Siantar")
                                        else:
                                            # Cek Bentrok Area Umum
                                            allowed_areas = SALES_AREA_MAP.get(sales_clean, [])
                                            # Jika salesman tidak ada di map (misal: Aswin), biarkan bebas / tidak di-flag area
                                            if allowed_areas:
                                                # Jika Area Toko TIDAK ADA SATUPUN yang cocok dengan Area Salesman
                                                if not any(a in allowed_areas for a in toko_areas):
                                                    toko_area_str = " & ".join(toko_areas)
                                                    kasus.append(f"🗺️ Pelanggaran Wilayah: Toko ini milik {toko_area_str}")
                                
                                if kasus:
                                    inv_key = f"{toko}_{tanggal_val.strftime('%Y%m%d')}" if no_faktur in ['-', '', 'nan', 'None'] else no_faktur
                                    
                                    anomali_data.append({
                                        'Tanggal_Raw': tanggal_val,
                                        'Tanggal': tanggal_val.strftime('%d %b %Y') if pd.notnull(tanggal_val) else "-",
                                        'No Faktur': no_faktur,
                                        'Invoice_Key': inv_key,
                                        'Nama Toko': toko_asli,
                                        'Merk': merk,
                                        'Ketik Fakturis (Asli)': sales_asli,
                                        'Pemilik Sah (Sistem)': sales_clean,
                                        'Omset': row['Jumlah'],
                                        'Analisis Pelanggaran': " | ".join(kasus)
                                    })

                            if anomali_data:
                                df_anomali_raw = pd.DataFrame(anomali_data)
                                
                                # --- 5. KONSOLIDASI OTO-SUM ---
                                cols_group = ['Tanggal', 'No Faktur', 'Nama Toko', 'Merk', 'Ketik Fakturis (Asli)', 'Pemilik Sah (Sistem)', 'Analisis Pelanggaran', 'Invoice_Key']
                                df_consolidated = df_anomali_raw.groupby(cols_group, as_index=False)['Omset'].sum()
                                
                                df_consolidated = df_consolidated.drop(columns=['Invoice_Key'])
                                df_consolidated = df_consolidated.sort_values(by='Omset', ascending=False)
                                
                                # --- 6. RENDER KE LAYAR ---
                                c_aud1, c_aud2 = st.columns([3, 1])
                                c_aud1.error(f"🚨 ALERT: Menampilkan {len(df_consolidated)} faktur bermasalah setelah konsolidasi!")
                                
                                if st.session_state.get('role') == 'direktur':
                                    csv_audit = df_consolidated.to_csv(index=False).encode('utf-8')
                                    c_aud2.download_button(
                                        label="📥 Download Berkas Bukti (CSV)",
                                        data=csv_audit,
                                        file_name=f"Audit_Fakturis_{datetime.date.today()}.csv",
                                        mime="text/csv",
                                        use_container_width=True
                                    )

                                def style_audit(r): return ['background-color: #ffe6e6; color: black;' for _ in r]
                                
                                st.dataframe(
                                    df_consolidated.style.apply(style_audit, axis=1).format({'Omset': 'Rp {:,.0f}'}),
                                    use_container_width=True, hide_index=True
                                )
                            else:
                                st.success("✅ Hebat! Tidak ada faktur bermasalah. Semua input fakturis dan rute salesman bersih 100%.")
                        else:
                            st.info("Tidak ada data transaksi pada rentang filter yang Anda pilih.")
                            
                    # Panggil fragment ke layar
                    render_audit_fragment()


if 'logged_in' not in st.session_state: st.session_state['logged_in'] = False

if st.session_state['logged_in']: main_dashboard()
else: login_page()
