"""
patch_app_mvp_simpan_permanen.py - Master Visit Plan: rute kunjungan TIDAK hilang lagi setelah logout.
Pakai:  python patch_app_mvp_simpan_permanen.py app.py

Masalah: "Simpan Rute Kunjungan" hanya menulis ke st.session_state['mvp_saved_data']. Saat logout, app.py menghapus
SELURUH session_state, jadi semua isian (hari kunjungan, 5-Step, Consultative Action, Follow Up) ikut hilang.
Perbaikan: simpan ke Google Sheet (worksheet baru 'RuteKunjungan', di spreadsheet yang sama dengan TASK_SHEET_ID),
lalu muat ulang sekali per sesi login. Hanya baris yang BERUBAH yang ditulis (upsert per Nama Outlet).
Tidak butuh secret/library baru (pakai gcp_service_account + TASK_SHEET_ID + gspread yang sudah dipakai fitur Tugas).
Idempotent, CRLF dipertahankan. Tiga titik diubah; logika lain tidak disentuh.
"""
import sys

p = sys.argv[1] if len(sys.argv) > 1 else "app.py"
raw = open(p, "rb").read().decode("utf-8")
crlf = "\r\n" in raw
t = raw.replace("\r\n", "\n")
if "mvp_simpan_ke_sheet" in t:
    print("app.py sudah dipatch"); sys.exit(0)

# ---- 1) fungsi bantu (level modul, tepat sebelum load_users) ----------------------------------------------------
JANGKAR_FUNGSI = "def load_users():"
assert t.count(JANGKAR_FUNGSI) == 1, "def load_users() tidak ditemukan persis satu kali; ubah manual"
BANTU = '''MVP_SHEET_NAME = "RuteKunjungan"
MVP_HEADERS = ["outlet", "route", "step5", "action", "followup", "diubah_oleh", "diubah_pada"]
MVP_ACTION_DEFAULT = "Cek Stok & Penawaran Baru"


@st.cache_resource(show_spinner=False)
def _mvp_buka_ws():
    """Worksheet 'RuteKunjungan' di spreadsheet TASK_SHEET_ID (dibuat otomatis bila belum ada)."""
    import gspread
    gc = gspread.service_account_from_dict(dict(st.secrets["gcp_service_account"]))
    sh = gc.open_by_key(st.secrets["TASK_SHEET_ID"])
    try:
        ws = sh.worksheet(MVP_SHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=MVP_SHEET_NAME, rows=5000, cols=len(MVP_HEADERS))
    if ws.row_values(1)[:len(MVP_HEADERS)] != MVP_HEADERS:
        ws.update(range_name="A1:G1", values=[MVP_HEADERS])
    return ws


def _mvp_bool(v):
    return str(v).strip().upper() in ("1", "TRUE", "YA", "Y")


def mvp_muat_dari_sheet():
    """Baca semua rute tersimpan -> {nama_outlet: {route, step5, action, followup}}."""
    baris = _mvp_buka_ws().get_all_values()
    hasil = {}
    for r in baris[1:]:
        r = (list(r) + [""] * len(MVP_HEADERS))[:len(MVP_HEADERS)]
        if not r[0].strip():
            continue
        hasil[r[0]] = {
            'route': r[1],
            'step5': _mvp_bool(r[2]),
            'action': r[3] or MVP_ACTION_DEFAULT,
            'followup': _mvp_bool(r[4]),
        }
    return hasil


def mvp_simpan_ke_sheet(perubahan, user):
    """Upsert per Nama Outlet. `perubahan` = {nama_outlet: {route, step5, action, followup}} (hanya yang berubah)."""
    if not perubahan:
        return 0
    ws = _mvp_buka_ws()
    nomor = {}
    for i, r in enumerate(ws.get_all_values()[1:], start=2):
        if r and r[0].strip():
            nomor[r[0]] = i
    waktu = datetime.datetime.now(pytz.timezone('Asia/Jakarta')).strftime("%Y-%m-%d %H:%M:%S")
    update, baru = [], []
    for outlet, v in perubahan.items():
        isi = [outlet, v.get('route') or "", "1" if v.get('step5') else "0", v.get('action') or MVP_ACTION_DEFAULT,
               "1" if v.get('followup') else "0", str(user), waktu]
        if outlet in nomor:
            update.append({"range": f"A{nomor[outlet]}:G{nomor[outlet]}", "values": [isi]})
        else:
            baru.append(isi)
    if update:
        ws.batch_update(update, value_input_option="RAW")
    if baru:
        ws.append_rows(baru, value_input_option="RAW")
    return len(perubahan)


'''
t = t.replace(JANGKAR_FUNGSI, BANTU + JANGKAR_FUNGSI)

# ---- 2) muat dari Sheet sekali per sesi login -------------------------------------------------------------------
LAMA_INIT = """        if 'mvp_saved_data' not in st.session_state:
            st.session_state['mvp_saved_data'] = {}
"""
BARU_INIT = """        if 'mvp_saved_data' not in st.session_state:
            st.session_state['mvp_saved_data'] = {}
        if not st.session_state.get('mvp_sheet_loaded'):
            try:
                st.session_state['mvp_saved_data'] = mvp_muat_dari_sheet()
                st.session_state['mvp_sheet_loaded'] = True
            except Exception as e_mvp:  # teks galat mentah tidak ditampilkan
                try:
                    from daftar_pesaing import jelaskan_galat
                    _info_mvp = jelaskan_galat(e_mvp)
                except Exception:
                    _info_mvp = type(e_mvp).__name__
                st.warning(f"⚠️ Rute tersimpan belum bisa dimuat dari Google Sheet. {_info_mvp}")
"""
assert t.count(LAMA_INIT) == 1, "blok inisialisasi mvp_saved_data tidak ditemukan persis"
t = t.replace(LAMA_INIT, BARU_INIT)

# ---- 3) tombol simpan -> tulis ke Sheet --------------------------------------------------------------------------
LAMA_SIMPAN = """                if submit_btn:
                    # Simpan isian user ke dalam Session State (Kamus Memori)
                    for _, row in edited_mvp.iterrows():
                        st.session_state['mvp_saved_data'][row['Nama Outlet']] = {
                            'route': row['📍 Route Plan (Hari)'],
                            'step5': row['📋 5-Step Visit Done'],
                            'action': row['💡 Consultative Action'],
                            'followup': row['🚀 Follow Up Done']
                        }
                    st.success("✅ Perencanaan Rute Kunjungan Berhasil Disimpan di Memori Lokal!")
"""
BARU_SIMPAN = """                if submit_btn:
                    # Kumpulkan isian; hanya baris yang BERBEDA dari data tersimpan/default yang ditulis ke Sheet
                    _perubahan_mvp = {}
                    for _, row in edited_mvp.iterrows():
                        _r = row['📍 Route Plan (Hari)']
                        _baru = {
                            'route': _r if isinstance(_r, str) else "",
                            'step5': bool(row['📋 5-Step Visit Done']),
                            'action': row['💡 Consultative Action'] or MVP_ACTION_DEFAULT,
                            'followup': bool(row['🚀 Follow Up Done'])
                        }
                        _lama = st.session_state['mvp_saved_data'].get(row['Nama Outlet'], {
                            'route': "", 'step5': False, 'action': MVP_ACTION_DEFAULT, 'followup': False})
                        if {k: (_lama.get(k) or ("" if k in ('route', 'action') else False)) for k in _baru} != _baru:
                            _perubahan_mvp[row['Nama Outlet']] = _baru
                    try:
                        mvp_simpan_ke_sheet(_perubahan_mvp, st.session_state.get('sales_name', ''))
                        st.session_state['mvp_saved_data'].update(_perubahan_mvp)
                        st.success(f"✅ Rute Kunjungan tersimpan permanen ({len(_perubahan_mvp)} toko diperbarui).")
                    except Exception as e_mvp:  # isian tetap dipertahankan selama sesi ini
                        st.session_state['mvp_saved_data'].update(_perubahan_mvp)
                        try:
                            from daftar_pesaing import jelaskan_galat
                            _info_mvp = jelaskan_galat(e_mvp)
                        except Exception:
                            _info_mvp = type(e_mvp).__name__
                        st.error(f"❌ Gagal menyimpan ke Google Sheet, isian hanya tersimpan sementara. {_info_mvp}")
"""
assert t.count(LAMA_SIMPAN) == 1, "blok submit_btn tidak ditemukan persis"
t = t.replace(LAMA_SIMPAN, BARU_SIMPAN)

out = t.replace("\n", "\r\n") if crlf else t
open(p, "wb").write(out.encode("utf-8"))
print("OK: app.py dipatch (CRLF dipertahankan)" if crlf else "OK: app.py dipatch")
