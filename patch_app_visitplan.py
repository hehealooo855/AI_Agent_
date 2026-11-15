"""
Patch (v3): alihkan penyimpanan Master Visit Plan dari Google Sheet ke visit_plan.csv.
Hanya mengganti 2 titik pemanggilan di app.py:
    mvp_muat_dari_sheet()                -> visit_plan_store.muat(...)
    mvp_simpan_ke_sheet(_perubahan_mvp, ...) -> visit_plan_store.simpan(...)
Fungsi lama tetap ada (tidak terpakai lagi), jadi tidak ada impor/definisi yang rusak.

Jalankan di folder yang sama dengan app.py dan visit_plan_store.py:  python patch_app_visitplan.py
Backup otomatis: app.py.bak_visitplan. Bila ada anchor tidak cocok -> app.py tidak diubah + diag_visitplan.txt.
"""
import re, shutil, sys

PATH = "app.py"
raw = open(PATH, "rb").read().decode("utf-8")
crlf = "\r\n" in raw
asli = raw.replace("\r\n", "\n")
src = asli

if "visit_plan_store" in src:
    sys.exit("Sudah ter-patch, tidak ada yang diubah.")

def diag(alasan):
    baris = asli.split("\n")
    idx = [i for i, l in enumerate(baris) if re.search(r"mvp_|Master Visit Plan", l)]
    potongan = "\n".join(f"{i+1}: {baris[i]}" for i in idx) or "(tidak ada baris terkait mvp_)"
    open("diag_visitplan.txt", "w", encoding="utf-8").write(f"Alasan: {alasan}\n\n{potongan}\n")
    sys.exit(f"GAGAL: {alasan}\napp.py tidak diubah. Kirim isi diag_visitplan.txt ke Claude.")

def ganti_sekali(pola, baru, nama):
    global src
    ketemu = re.findall(pola, src)
    if len(ketemu) != 1:
        diag(f"[{nama}] ditemukan {len(ketemu)}x (harus 1x)")
    src = re.sub(pola, lambda m: baru, src)

# 1) import
ganti_sekali(r"\nimport calendar\n", "\nimport calendar\nimport visit_plan_store\n", "import")

# 2) muat: baca CSV (selain def-nya sendiri)
ganti_sekali(r"(?<!def )mvp_muat_dari_sheet\(\)", "visit_plan_store.muat(get_current_time_wib())", "muat")

# 3) simpan: tulis CSV
ganti_sekali(r"(?<!def )mvp_simpan_ke_sheet\(_perubahan_mvp, st\.session_state\.get\('sales_name', ''\)\)",
             "visit_plan_store.simpan(_perubahan_mvp, get_current_time_wib())", "simpan")

# 4) baca ulang CSV di setiap render (file lokal cepat; rute akun lain & reset Senin 12:00 selalu segar)
ganti_sekali(r"if not st\.session_state\.get\('mvp_sheet_loaded'\):",
             "if True:  # CSV lokal: dibaca ulang tiap render", "reload")

# 5) teks pesan (opsional, tidak menggagalkan patch)
src = src.replace("belum bisa dimuat dari Google Sheet.", "belum bisa dimuat dari file visit_plan.csv.")
src = src.replace("Gagal menyimpan ke Google Sheet, isian hanya tersimpan sementara.",
                  "Gagal menyimpan ke file visit_plan.csv, isian hanya tersimpan sementara.")

try:
    compile(src, PATH, "exec")
except SyntaxError as e:
    diag(f"hasil patch tidak lolos cek sintaks ({e})")

shutil.copy(PATH, PATH + ".bak_visitplan")
open(PATH, "wb").write((src.replace("\n", "\r\n") if crlf else src).encode("utf-8"))
print("OK: app.py ter-patch. Backup: app.py.bak_visitplan")
