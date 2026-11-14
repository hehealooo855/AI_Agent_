"""
patch_app_mvp_tampilkan_galat.py - Master Visit Plan: pesan GAGAL simpan tidak lagi hilang setelah 1 detik.
Pakai (setelah patch_app_mvp_simpan_permanen.py):  python patch_app_mvp_tampilkan_galat.py app.py

Masalah: setelah tombol simpan, kode selalu `time.sleep(1); st.rerun()` sehingga st.error ikut terhapus.
Perbaikan: rerun hanya bila penyimpanan ke Google Sheet BERHASIL; bila gagal, pesan error tetap di layar.
Idempotent, CRLF dipertahankan.
"""
import sys

p = sys.argv[1] if len(sys.argv) > 1 else "app.py"
raw = open(p, "rb").read().decode("utf-8")
crlf = "\r\n" in raw
t = raw.replace("\r\n", "\n")
assert "mvp_simpan_ke_sheet" in t, "jalankan dulu patch_app_mvp_simpan_permanen.py"
if "_mvp_ok" in t:
    print("app.py sudah dipatch"); sys.exit(0)

A1 = """                    try:
                        mvp_simpan_ke_sheet(_perubahan_mvp, st.session_state.get('sales_name', ''))
"""
B1 = """                    _mvp_ok = False
                    try:
                        mvp_simpan_ke_sheet(_perubahan_mvp, st.session_state.get('sales_name', ''))
                        _mvp_ok = True
"""
assert t.count(A1) == 1, "blok try simpan tidak ditemukan persis"
t = t.replace(A1, B1)

A2 = """                    time.sleep(1)
                    st.rerun() # Render ulang untuk menghilangkan status 'loading' pada tombol
"""
B2 = """                    if _mvp_ok:
                        time.sleep(1)
                        st.rerun() # Render ulang untuk menghilangkan status 'loading' pada tombol
"""
assert t.count(A2) == 1, "blok rerun tidak ditemukan persis"
t = t.replace(A2, B2)

open(p, "wb").write((t.replace("\n", "\r\n") if crlf else t).encode("utf-8"))
print("OK: app.py dipatch")
