"""
Penyimpanan permanen Master Visit Plan (CSV lokal, satu rute bersama untuk semua akun).

Aturan reset (sesuai permintaan):
  - Saat 'Follow Up Done' dicentang, waktunya dicatat (WIB).
  - Pada hari Senin jam 12:00 WIB berikutnya, centang '5-Step Visit Done' dan
    'Follow Up Done' toko itu direset. Hari kunjungan & Consultative Action tetap.
  - Toko yang Follow Up-nya belum dicentang tidak pernah direset otomatis.
"""
import os
import threading
import datetime

import pandas as pd
import pytz

FILE_VISIT_PLAN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "visit_plan.csv")
KOLOM = ["Nama Outlet", "route", "step5", "action", "followup", "followup_at"]
DEFAULT_ACTION = "Cek Stok & Penawaran Baru"
_WIB = pytz.timezone("Asia/Jakarta")
_FMT = "%Y-%m-%d %H:%M:%S"
_LOCK = threading.Lock()


def _wib(now=None):
    if now is None:
        return datetime.datetime.now(_WIB)
    return now.astimezone(_WIB) if now.tzinfo else _WIB.localize(now)


def waktu_reset_berikutnya(ts):
    """Senin 12:00 WIB pertama yang jatuh SETELAH ts."""
    ts = _wib(ts)
    selisih = (0 - ts.weekday()) % 7  # 0 = Senin
    kandidat = (ts + datetime.timedelta(days=selisih)).replace(hour=12, minute=0, second=0, microsecond=0)
    if kandidat <= ts:
        kandidat += datetime.timedelta(days=7)
    return kandidat


def _baca_mentah(file):
    if not os.path.isfile(file):
        return {}
    try:
        df = pd.read_csv(file, dtype=str, keep_default_na=False)
    except Exception:
        return {}
    hasil = {}
    for _, r in df.iterrows():
        nama = r.get("Nama Outlet", "")
        if not nama:
            continue
        hasil[nama] = {
            "route": r.get("route", ""),
            "step5": r.get("step5", "") == "True",
            "action": r.get("action", "") or DEFAULT_ACTION,
            "followup": r.get("followup", "") == "True",
            "followup_at": r.get("followup_at", ""),
        }
    return hasil


def _tulis_atomik(data, file):
    baris = [{"Nama Outlet": k, **v} for k, v in data.items()]
    df = pd.DataFrame(baris, columns=KOLOM)
    tmp = f"{file}.{os.getpid()}.{threading.get_ident()}.tmp"
    df.to_csv(tmp, index=False)
    os.replace(tmp, file)  # atomik: file tidak pernah setengah tertulis


def _terapkan_reset(data, now):
    berubah = False
    for v in data.values():
        if not v["followup"]:
            continue
        try:
            dicentang = _WIB.localize(datetime.datetime.strptime(v["followup_at"], _FMT))
        except (ValueError, TypeError):
            v["followup_at"] = now.strftime(_FMT)  # data lama tanpa waktu: mulai hitung dari sekarang
            berubah = True
            continue
        if now >= waktu_reset_berikutnya(dicentang):
            v["step5"] = False
            v["followup"] = False
            v["followup_at"] = ""
            berubah = True
    return berubah


def muat(now=None, file=FILE_VISIT_PLAN):
    """Baca semua rute tersimpan + terapkan reset Senin 12:00. Return {outlet: {...}}."""
    now = _wib(now)
    with _LOCK:
        data = _baca_mentah(file)
        if _terapkan_reset(data, now):
            _tulis_atomik(data, file)
        return data


def simpan(perubahan, now=None, file=FILE_VISIT_PLAN):
    """
    perubahan: {outlet: {'route','step5','action','followup'}}.
    Hanya toko yang dikirim yang diubah; toko lain di CSV (mis. dari filter/akun lain) tetap aman.
    """
    now = _wib(now)
    with _LOCK:
        data = _baca_mentah(file)
        _terapkan_reset(data, now)
        for outlet, v in perubahan.items():
            lama = data.get(outlet)
            route = v.get("route")
            route = "" if route is None or (isinstance(route, float) and pd.isna(route)) else str(route)
            followup = bool(v.get("followup", False))
            if not followup:
                waktu = ""
            elif lama and lama["followup"] and lama["followup_at"]:
                waktu = lama["followup_at"]  # sudah tercentang sebelumnya: jangan geser waktunya
            else:
                waktu = now.strftime(_FMT)
            data[outlet] = {
                "route": route,
                "step5": bool(v.get("step5", False)),
                "action": v.get("action") or DEFAULT_ACTION,
                "followup": followup,
                "followup_at": waktu,
            }
        _tulis_atomik(data, file)
