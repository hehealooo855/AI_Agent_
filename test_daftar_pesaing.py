"""
test_daftar_pesaing.py - uji urutan cadangan (Gemini -> cache lama -> Google Sheet -> kode) dan izin edit.

Jalankan:  pytest test_daftar_pesaing.py -v
"""
from __future__ import annotations

import ai_pesaing as ap
import daftar_pesaing as dp

KUOTA = ap.HasilPesaing.gagal("galat_model", "Kuota/batas permintaan Gemini sedang penuh. Coba lagi beberapa menit lagi.")


def _ok(*nama):
    return ap.HasilPesaing(ok=True, pesaing=tuple({"nama": n, "alasan": "", "berita_promo": [], "produk": []} for n in nama),
                           dibuat=1.0, model="m")


def setup_function(_):
    ap.kosongkan_cache()


def test_gemini_berhasil_dipakai_tanpa_catatan():
    d = dp.tentukan("Biore", _ok("Garnier", "Nivea"), lambda: {"biore": ["X"]})
    assert d.asal == "gemini" and d.pesaing == ("Garnier", "Nivea") and d.catatan == ""


def test_belum_dipicu_pakai_sheet_lalu_kode_tanpa_pesan_galat():
    d = dp.tentukan("Biore", None, lambda: {"biore": ["Dove", "Lux"]})
    assert d.asal == "sheet" and d.pesaing == ("Dove", "Lux") and d.catatan == ""
    d = dp.tentukan("Biore", None, lambda: {})
    assert d.asal == "kode" and d.pesaing == tuple(dp.DAFTAR_AWAL["Biore"])


def test_kuota_penuh_pakai_cache_lama_dan_pesan_menyebut_sumber():
    ap._terakhir_baik["biore"] = _ok("Garnier")
    d = dp.tentukan("Biore", KUOTA, lambda: {"biore": ["Dove"]})
    assert d.asal == "cache_lama" and d.pesaing == ("Garnier",)
    assert "Kuota" in d.catatan and "terakhir" in d.catatan and "Biore" in d.catatan


def test_kuota_penuh_tanpa_cache_pakai_sheet():
    d = dp.tentukan("Biore", KUOTA, lambda: {"biore": ["Dove", "Lux"]})
    assert d.asal == "sheet" and d.pesaing == ("Dove", "Lux")
    assert "Kuota" in d.catatan and "Google Sheet" in d.catatan


def test_kuota_penuh_sheet_gagal_pakai_kode():
    def rusak():
        raise RuntimeError("detail-mentah-rahasia")
    d = dp.tentukan("Biore", KUOTA, rusak)
    assert d.asal == "kode" and d.pesaing == tuple(dp.DAFTAR_AWAL["Biore"])
    assert "daftar awal di kode" in d.catatan and "detail-mentah" not in d.catatan


def test_merk_tak_dikenal_semua_kosong_tidak_melempar():
    d = dp.tentukan("MerkBaru", KUOTA, None)
    assert d.asal == "kode" and d.pesaing == ()


def test_izin_edit_hanya_manager_dan_direktur():
    assert dp.boleh_edit("manager") and dp.boleh_edit("Direktur") and dp.boleh_edit(" MANAGER ")
    for r in ("sales", "supervisor", "", None):
        assert not dp.boleh_edit(r)


def test_bersihkan():
    assert dp.bersihkan("Nivea, Dove,, DOVE, Biore, <x>, Lux, Pond's, Simple", "Biore") == ["Nivea", "Dove", "Lux", "Pond's"]


class WsPalsu:
    def __init__(self, baris=None):
        self.baris = baris if baris is not None else [["Merk", "Pesaing"]]

    def get_all_values(self):
        return [list(r) for r in self.baris]

    def update(self, range_name, values):
        if range_name == "A1:B1":
            self.baris[0:1] = values
        else:
            self.baris[int(range_name[1:]) - 1][1] = values[0][0]

    def append_row(self, r):
        self.baris.append(list(r))


def test_sheet_simpan_baru_lalu_perbarui_dan_baca():
    ws = WsPalsu([])
    s = dp.SheetDaftar(ws)
    s.simpan("Biore", ["Garnier", "Nivea"])
    s.simpan("Emina", ["Pixy"])
    s.simpan("biore", ["Dove"])
    assert ws.baris == [["Merk", "Pesaing"], ["Biore", "Dove"], ["Emina", "Pixy"]]
    assert s.baca() == {"biore": ["Dove"], "emina": ["Pixy"]}


def test_jelaskan_galat_tanpa_teks_mentah():
    class Resp: status_code = 403
    class APIError(Exception):
        response = Resp()
    assert "Editor" in dp.jelaskan_galat(APIError("rahasia-mentah")) and "rahasia" not in dp.jelaskan_galat(APIError("rahasia-mentah"))
    assert "secrets.toml" in dp.jelaskan_galat(KeyError("gcp_service_account"))
    assert "tidak dikenali" in dp.jelaskan_galat(RuntimeError("x"))
