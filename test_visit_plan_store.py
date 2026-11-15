import datetime, pytz
import visit_plan_store as vp

W = pytz.timezone("Asia/Jakarta")
def t(y, m, d, h=0, mi=0): return W.localize(datetime.datetime(y, m, d, h, mi))

def test_waktu_reset():
    assert vp.waktu_reset_berikutnya(t(2026, 11, 11, 9)) == t(2026, 11, 16, 12)   # Rabu -> Senin
    assert vp.waktu_reset_berikutnya(t(2026, 11, 16, 10)) == t(2026, 11, 16, 12)  # Senin pagi -> hari itu
    assert vp.waktu_reset_berikutnya(t(2026, 11, 16, 13)) == t(2026, 11, 23, 12)  # Senin lewat jam 12 -> minggu depan

def test_simpan_muat_dan_reset(tmp_path):
    f = str(tmp_path / "vp.csv")
    vp.simpan({"Toko A": dict(route="Selasa", step5=True, action="X", followup=True),
               "Toko B": dict(route="Rabu", step5=True, action="Y", followup=False)}, t(2026, 11, 11, 9), f)
    d = vp.muat(t(2026, 11, 15, 20), f)            # sebelum Senin 12:00 -> belum reset
    assert d["Toko A"]["followup"] and d["Toko A"]["step5"] and d["Toko A"]["route"] == "Selasa"
    d = vp.muat(t(2026, 11, 16, 12, 1), f)         # setelah Senin 12:00 -> reset
    assert not d["Toko A"]["followup"] and not d["Toko A"]["step5"]
    assert d["Toko A"]["route"] == "Selasa" and d["Toko A"]["action"] == "X"
    assert d["Toko B"]["step5"] is True            # follow up tidak dicentang -> tidak pernah reset

def test_simpan_tidak_menimpa_toko_lain_dan_waktu_tidak_bergeser(tmp_path):
    f = str(tmp_path / "vp.csv")
    vp.simpan({"A": dict(route="Senin", followup=True)}, t(2026, 11, 11, 9), f)
    vp.simpan({"B": dict(route="Jumat")}, t(2026, 11, 14, 9), f)
    vp.simpan({"A": dict(route="Kamis", followup=True)}, t(2026, 11, 15, 9), f)
    d = vp.muat(t(2026, 11, 15, 10), f)
    assert set(d) == {"A", "B"} and d["A"]["route"] == "Kamis"
    assert d["A"]["followup_at"].startswith("2026-11-11")
