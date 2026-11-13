"""
ai_tasks_eval.py - FASE 4: Evaluasi efektivitas & kinerja tugas kunjungan (TANPA import streamlit).

Prinsip: "Program yang menghitung, AI yang menjelaskan."
  * Semua hitungan ada di fungsi murni yang menerima DataFrame tugas + DataFrame penjualan sebagai argumen
    (bisa diuji tanpa Sheets/Streamlit/internet).
  * Hak akses diterapkan DI DALAM fungsi (bukan di UI): penjualan lewat ai_tools.siapkan_data (TLS + RLS),
    tugas lewat ai_tasks.tugas_terlihat. Rincian per sales hanya untuk approver (akses penuh / supervisor).
  * Jujur soal korelasi vs sebab-akibat: selalu ada kelompok pembanding + ukuran sampel. Di bawah ambang sampel
    hasilnya 'belum cukup data' (tanpa angka persen), bukan kesimpulan.

Asumsi default (semua ada di EvalConfig, ubah di sana bila keputusan bisnis berbeda):
  (a) Berhasil      : outlet order lagi dalam N hari (default 30; UI juga menyediakan 14) sejak tugas ditandai selesai.
                      Hari selesai ikut dihitung. Tugas yang jendela N harinya belum lengkap ('belum matang') tidak dinilai.
  (b) Pembanding    : toko serupa TANPA tugas pada tanggal yang sama = outlet yang memenuhi kondisi pemicu yang sama
                      pada tanggal selesai tugas (mis. >= 30 hari tidak order) dan tidak pernah punya catatan tugas apa pun,
                      dibobot ulang menurut ukuran toko (omset historis) agar toko besar vs kecil tidak menyesatkan.
                      'Omset sebelum vs sesudah' ditampilkan sebagai pelengkap (bukan pembanding utama).
  (c) Ambang sampel : >= 20 tugas dinilai untuk angka keseluruhan, >= 10 untuk rincian (jenis/bulan/sales),
                      >= 30 outlet pembanding unik.
  (d) Siapa melihat : sales hanya dirinya; supervisor tugas pada merk yang dikelolanya; manager/direktur/fauziah semua.

Cara pakai AI: modul ini mendaftarkan 3 alat ke ai_tools.TOOL_REGISTRY saat di-import. Data tugas diambil lewat
penyedia (`pasang_penyedia`) yang dipasang app (lihat ai_tasks_eval_ui.daftarkan_penyedia). Bila Sheet tidak tersedia,
alat mengembalikan {"ok": False, "pesan": ...} (galat ramah), bukan error.
"""
from __future__ import annotations

import datetime as dt
import difflib
import functools
import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

import ai_tasks as K
import ai_tools
from ai_tools import ToolError

# =============================================================================
# 1. KONFIGURASI & KONSTANTA
# =============================================================================
PESAN_SHEET = ("Data tugas belum bisa dibaca karena Google Sheet tidak tersedia. "
               "Coba lagi beberapa saat; belum ada angka evaluasi yang bisa dihitung.")
PERINGATAN_KAUSAL = ("Ini HUBUNGAN YANG TERAMATI, bukan bukti sebab-akibat: toko yang dikunjungi dipilih (tidak acak) "
                     "dan bisa berbeda dari toko pembanding dalam hal yang tidak terukur.")
BELUM_CUKUP = "belum cukup data"

STATUS_VALID = ("usulan", "disetujui", "dikerjakan", "selesai", "ditolak")
STATUS_DITUGASKAN = ("disetujui", "dikerjakan", "selesai")
STATUS_TERBUKA = ("usulan", "disetujui", "dikerjakan")
JENIS_EVAL = ("toko_tidur", "outlet_turun_order", "cross_sell_brand")   # jenis yang punya definisi pembanding
_ALIAS_STATUS = {
    "done": "selesai", "finish": "selesai", "finished": "selesai", "complete": "selesai", "completed": "selesai",
    "tuntas": "selesai", "setuju": "disetujui", "approved": "disetujui", "approve": "disetujui", "acc": "disetujui",
    "tolak": "ditolak", "rejected": "ditolak", "reject": "ditolak", "proses": "dikerjakan", "diproses": "dikerjakan",
    "in progress": "dikerjakan", "on progress": "dikerjakan", "progress": "dikerjakan", "open": "usulan", "baru": "usulan",
}
_FORMAT_TANGGAL = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y %H:%M:%S",
                   "%d/%m/%Y %H:%M", "%d/%m/%Y", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d-%m-%Y", "%d/%m/%y")
_KOSONG_UP = {v.upper() for v in ai_tools.NILAI_KOSONG}

PARAM_DESC_EVAL = {
    "n_hari": "Jendela keberhasilan: outlet dianggap 'order lagi' bila order dalam N hari setelah tugas selesai (1-365). "
              "Kosongkan untuk default (30). Pilihan umum: 14 atau 30.",
    "jenis": "Jenis tugas: toko_tidur, outlet_turun_order, cross_sell_brand, atau cross_sell_sku. Kosongkan untuk semua jenis.",
    "tugas_mulai": "Awal filter tanggal tugas, YYYY-MM-DD (efektivitas: tanggal tugas selesai; alat lain: tanggal tugas dibuat). "
                   "Kosongkan untuk tanpa batas awal.",
    "tugas_selesai": "Akhir filter tanggal tugas, YYYY-MM-DD (efektivitas: tanggal tugas selesai; alat lain: tanggal tugas dibuat). "
                     "Kosongkan untuk tanpa batas akhir.",
}


@dataclass(frozen=True)
class EvalConfig:
    hari_jendela: int = 30                 # N bawaan
    jendela_pilihan: tuple = (14, 30)      # pilihan di UI
    termasuk_hari_selesai: bool = True     # order pada hari tugas ditandai selesai ikut dihitung
    min_tugas: int = 20                    # ambang sampel angka keseluruhan
    min_tugas_subgrup: int = 10            # ambang sampel per jenis / bulan / sales
    min_kontrol: int = 30                  # minimal outlet pembanding UNIK
    jumlah_stratum: int = 3                # pembobotan ulang menurut ukuran toko (tersier)
    min_stratum: int = 5                   # minimal outlet pembanding per stratum (kurang -> pakai seluruh pembanding)
    hari_aktif_cross: int = 90             # cross-sell: outlet dianggap aktif bila order dalam X hari sebelum tugas selesai
    hari_banding_turun: int = 30           # turun order: panjang periode pembanding (hari)
    min_tugas_kinerja: int = 10            # ambang sampel rasio kinerja
    tenggat_segera_hari: int = 3           # 'segera jatuh tempo'
    usulan_menunggu_lama_hari: int = 7
    maks_baris: int = 15
    maks_bulan: int = 12
    toleransi_masa_depan_hari: int = 1


# =============================================================================
# 2. UTILITAS & PENYEDIA DATA TUGAS
# =============================================================================
def _s(x: Any) -> str:
    if x is None:
        return ""
    try:
        if pd.isna(x):
            return ""
    except (TypeError, ValueError):
        pass
    return str(x).strip()


def _kunci_outlet(x: Any) -> str:
    return re.sub(r"\s+", " ", _s(x)).upper()


def _pct(x: float) -> float:
    return round(float(x) * 100.0, 1)


_PENYEDIA: Optional[Callable[[], pd.DataFrame]] = None


def pasang_penyedia(fn: Optional[Callable[[], pd.DataFrame]]) -> None:
    """Pasang fungsi tanpa argumen yang mengembalikan DataFrame tugas (mis. baca Sheet). None = lepas."""
    global _PENYEDIA
    _PENYEDIA = fn


def _ambil_tugas() -> pd.DataFrame:
    if _PENYEDIA is None:
        raise ToolError(PESAN_SHEET)
    try:
        d = _PENYEDIA()
    except Exception:  # noqa: BLE001 - detail galat (bisa memuat ID/URL) tidak boleh bocor ke AI/UI
        raise ToolError(PESAN_SHEET) from None
    if d is None or not isinstance(d, pd.DataFrame):
        raise ToolError(PESAN_SHEET)
    return d


def _aman(nama: str) -> Callable:
    """Ubah ToolError menjadi {"ok": False, ...} agar konsisten dengan alat ai_tools."""
    def deko(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def p(*a, **k):
            try:
                return fn(*a, **k)
            except ToolError as e:
                return ai_tools._gagal(nama, str(e))
        return p
    return deko


# =============================================================================
# 3. PEMBERSIHAN DATA TUGAS (edit manual di Sheet, salah ketik, tanggal rusak)
# =============================================================================
def normalisasi_status(x: Any) -> tuple[str, bool]:
    """-> (status baku atau '' bila tidak dikenal, apakah diperbaiki dari salah ketik/alias)."""
    dasar = _s(x).lower()
    if dasar in STATUS_VALID:
        return dasar, False
    t = re.sub(r"[\s_\-\.]+", " ", dasar).strip()
    if t in STATUS_VALID:
        return t, False
    if t in _ALIAS_STATUS:
        return _ALIAS_STATUS[t], True
    mirip = difflib.get_close_matches(t, STATUS_VALID, n=1, cutoff=0.8) if t else []
    return (mirip[0], True) if mirip else ("", False)


def _parse_satu(t: str) -> pd.Timestamp:
    if not t:
        return pd.NaT
    for f in _FORMAT_TANGGAL:
        try:
            return pd.Timestamp(dt.datetime.strptime(t, f))
        except ValueError:
            continue
    return pd.NaT


def parse_tanggal(seri: pd.Series) -> pd.Series:
    nilai = seri.map(_s)
    peta = {v: _parse_satu(v) for v in nilai.unique()}
    return pd.to_datetime(pd.Series([peta[v] for v in nilai], index=seri.index, dtype="object"))


_FLAG = ["f_status_diperbaiki", "f_status_tidak_dikenal", "f_selesai_tanpa_tanggal", "f_selesai_pada_diabaikan",
         "f_tanggal_tak_terbaca", "f_tanggal_masa_depan", "f_selesai_sebelum_dibuat", "f_selesai_sebelum_disetujui",
         "f_disetujui_tanpa_tanggal", "f_id_duplikat", "f_tanpa_id", "f_jenis_tidak_dikenal"]
_LABEL_FLAG = {
    "f_status_diperbaiki": "status salah ketik/alias (dibetulkan otomatis)",
    "f_status_tidak_dikenal": "status tidak dikenal (baris tidak ikut dihitung)",
    "f_selesai_tanpa_tanggal": "status selesai tetapi selesai_pada kosong/tidak terbaca (ikut rasio selesai, tidak ikut ketepatan waktu/efektivitas)",
    "f_selesai_pada_diabaikan": "selesai_pada terisi padahal status bukan selesai (tanggal diabaikan)",
    "f_tanggal_tak_terbaca": "ada tanggal yang formatnya tidak terbaca",
    "f_tanggal_masa_depan": "tanggal dibuat/disetujui/selesai di masa depan (diabaikan)",
    "f_selesai_sebelum_dibuat": "selesai_pada lebih awal dari dibuat_pada (diabaikan)",
    "f_selesai_sebelum_disetujui": "selesai_pada lebih awal dari disetujui_pada (tidak ikut rata-rata durasi)",
    "f_disetujui_tanpa_tanggal": "sudah disetujui/dikerjakan/selesai tetapi disetujui_pada kosong",
    "f_id_duplikat": "id ganda (baris lama dibuang, yang terakhir dipakai)",
    "f_tanpa_id": "baris tanpa id",
    "f_jenis_tidak_dikenal": "jenis tugas tidak dikenal (tidak dievaluasi)",
}


def bersihkan_tugas(tugas_df: Optional[pd.DataFrame], sekarang: Optional[dt.datetime] = None) -> pd.DataFrame:
    """DataFrame mentah dari Sheet -> DataFrame bersih + kolom bantu (*_dt, outlet_k, durasi_hari, f_*).
    Tidak mengubah input. Raise ToolError bila tugas_df bukan DataFrame (mis. Sheet tidak tersedia)."""
    if tugas_df is None or not isinstance(tugas_df, pd.DataFrame):
        raise ToolError(PESAN_SHEET)
    now = pd.Timestamp(sekarang or K.sekarang())
    d = tugas_df.copy()
    for k in K.HEADERS:
        if k not in d.columns:
            d[k] = ""
    d = d[K.HEADERS].copy()
    for k in K.HEADERS:
        d[k] = d[k].map(_s)
    d = d[(d != "").any(axis=1)].copy()

    ada_id = d["id"] != ""
    lama = ada_id & d.duplicated("id", keep="last")
    ganda = ada_id & d.duplicated("id", keep=False) & ~lama
    d = d[~lama].copy()
    d["f_id_duplikat"] = ganda[d.index]
    d["f_tanpa_id"] = d["id"] == ""

    st_ = d["status"].map(normalisasi_status)
    d["status"] = st_.map(lambda x: x[0])
    d["f_status_diperbaiki"] = st_.map(lambda x: x[1])
    d["f_status_tidak_dikenal"] = d["status"] == ""

    tak_terbaca = pd.Series(False, index=d.index)
    masa_depan = pd.Series(False, index=d.index)
    batas = now + pd.Timedelta(days=1)
    for src, dst in (("dibuat_pada", "dibuat_dt"), ("disetujui_pada", "disetujui_dt"),
                     ("tenggat", "tenggat_dt"), ("selesai_pada", "selesai_dt")):
        d[dst] = parse_tanggal(d[src])
        tak_terbaca |= (d[src] != "") & d[dst].isna()
        if dst != "tenggat_dt":                       # tenggat memang boleh di masa depan
            fut = d[dst] > batas
            masa_depan |= fut
            d.loc[fut, dst] = pd.NaT
    d["f_tanggal_tak_terbaca"], d["f_tanggal_masa_depan"] = tak_terbaca, masa_depan

    bukan = (d["status"] != "selesai") & d["selesai_dt"].notna()
    d["f_selesai_pada_diabaikan"] = bukan
    d.loc[bukan, "selesai_dt"] = pd.NaT
    sblm_dibuat = d["selesai_dt"].notna() & d["dibuat_dt"].notna() & (d["selesai_dt"] < d["dibuat_dt"])
    d["f_selesai_sebelum_dibuat"] = sblm_dibuat
    d.loc[sblm_dibuat, "selesai_dt"] = pd.NaT
    d["f_selesai_tanpa_tanggal"] = (d["status"] == "selesai") & d["selesai_dt"].isna()
    d["f_disetujui_tanpa_tanggal"] = d["status"].isin(STATUS_DITUGASKAN) & d["disetujui_dt"].isna()

    d["durasi_hari"] = (d["selesai_dt"] - d["disetujui_dt"]).dt.total_seconds() / 86400.0
    neg = d["durasi_hari"] < 0
    d["f_selesai_sebelum_disetujui"] = neg
    d.loc[neg, "durasi_hari"] = np.nan

    d["sales"] = d["sales"].map(str.upper)
    d["dibuat_oleh"] = d["dibuat_oleh"].map(str.upper)
    d["brand"] = d["brand"].map(lambda x: _s(x).upper() or "-")
    d["jenis"] = d["jenis"].map(lambda x: re.sub(r"\s+", "_", _s(x).lower()))
    d["f_jenis_tidak_dikenal"] = ~d["jenis"].isin(K.JENIS_TUGAS)
    d["prioritas"] = d["prioritas"].map(str.lower)
    d["outlet_k"] = d["outlet"].map(_kunci_outlet)
    return d.reset_index(drop=True)


def ringkas_kualitas(v: pd.DataFrame) -> dict:
    """Hitungan masalah data PADA BARIS YANG BOLEH DILIHAT user (agar tidak membocorkan data orang lain)."""
    hasil = {"jumlah_baris": int(len(v))}
    for f in _FLAG:
        n = int(v[f].sum()) if f in v.columns and len(v) else 0
        if n:
            hasil[_LABEL_FLAG[f]] = n
    return hasil


# =============================================================================
# 4. AKSES & FILTER
# =============================================================================
def _boleh_per_sales(akses: ai_tools.Akses) -> bool:
    return akses.level in ("penuh", "supervisor")           # = approver (lihat ai_tasks.boleh_menyetujui)


def _validasi_n(n: Any, cfg: EvalConfig) -> int:
    if n is None or n == "":
        return int(cfg.hari_jendela)
    try:
        n = int(n)
    except (TypeError, ValueError):
        raise ToolError("n_hari harus bilangan bulat (mis. 14 atau 30).") from None
    if not 1 <= n <= 365:
        raise ToolError("n_hari harus di antara 1 dan 365.")
    return n


def _pilih_jenis(jenis: Any) -> Optional[str]:
    if jenis is None or _s(jenis) == "":
        return None
    t = re.sub(r"\s+", "_", _s(jenis).lower())
    for kode, label in K.JENIS_TUGAS.items():
        if t == kode or t == re.sub(r"\s+", "_", label.lower()):
            return kode
    raise ToolError(f"jenis '{jenis}' tidak dikenal. Pilihan: {sorted(K.JENIS_TUGAS)}")


def _filter_tugas(v: pd.DataFrame, sales: Any, jenis: Any, mulai: Any, selesai: Any, kolom_tgl: str,
                  hanya_bertanggal: bool = False) -> pd.DataFrame:
    """Filter jenis/sales (hanya dalam data yang boleh dilihat) dan rentang tanggal pada `kolom_tgl`."""
    j = _pilih_jenis(jenis)
    if j:
        v = v[v["jenis"] == j]
    if _s(sales):
        nama = ai_tools._cocokkan(pd.Series(v["sales"].unique()), str(sales), "Sales")
        v = v[v["sales"].isin(nama)]
    a, b = ai_tools._ke_tanggal(mulai), ai_tools._ke_tanggal(selesai)
    if a is not None or b is not None:
        tgl = v[kolom_tgl].dt.normalize()
        ok = pd.Series(True, index=v.index)
        if a is not None:
            ok &= tgl >= a
        if b is not None:
            ok &= tgl <= b
        v = v[ok | (v[kolom_tgl].isna() & (not hanya_bertanggal))]
    return v


# =============================================================================
# 5. MESIN PEMBANDING (win-back vs toko serupa tanpa tugas)
# =============================================================================
def _siapkan_penjualan(d: pd.DataFrame) -> pd.DataFrame:
    outlet_k = d["Nama Outlet"].map(_kunci_outlet)
    s = pd.DataFrame({"outlet_k": outlet_k, "hari": d["Tanggal"].dt.normalize(),
                      "merk_k": d["Merk"].map(lambda x: _s(x).upper()),
                      "jumlah": pd.to_numeric(d["Jumlah"], errors="coerce").fillna(0.0)})
    return s[~s["outlet_k"].isin(_KOSONG_UP)].reset_index(drop=True)


def _harian(s: pd.DataFrame) -> pd.DataFrame:
    """Omset per outlet per hari; hanya hari dengan omset > 0 yang dianggap 'order' (retur/diskon bersih tidak dihitung)."""
    x = s.groupby(["outlet_k", "hari"], as_index=False)["jumlah"].sum()
    return x[x["jumlah"] > 0].reset_index(drop=True)


def _jumlah(daily: pd.DataFrame, a: pd.Timestamp, b: pd.Timestamp) -> pd.Series:
    x = daily[(daily["hari"] >= a) & (daily["hari"] <= b)]
    return x.groupby("outlet_k")["jumlah"].sum()


def _kond_tidur(daily: pd.DataFrame, D: pd.Timestamp, ambang_hari: int) -> pd.Series:
    """Outlet yang pada tanggal D sudah >= ambang hari tidak order. Nilai = omset historis sebelum D (ukuran toko)."""
    x = daily[daily["hari"] < D]
    if x.empty:
        return pd.Series(dtype=float)
    g = x.groupby("outlet_k")
    terakhir, hist = g["hari"].max(), g["jumlah"].sum()
    return hist[(D - terakhir).dt.days >= ambang_hari]


def _kond_turun(daily: pd.DataFrame, D: pd.Timestamp, L: int, ambang_persen: float) -> pd.Series:
    """Outlet yang omset L hari terakhir sebelum D turun >= ambang% vs L hari sebelumnya. Nilai = omset periode pembanding."""
    sehari = pd.Timedelta(days=1)
    cur = _jumlah(daily, D - pd.Timedelta(days=L), D - sehari)
    prev = _jumlah(daily, D - pd.Timedelta(days=2 * L), D - pd.Timedelta(days=L) - sehari)
    prev = prev[prev > 0]
    cur = cur.reindex(prev.index).fillna(0.0)
    return prev[(prev - cur) / prev * 100.0 >= ambang_persen]


def _kond_cross(daily_all: pd.DataFrame, daily_brand: pd.DataFrame, D: pd.Timestamp, cfg: EvalConfig) -> pd.Series:
    """Outlet aktif (order apa pun dalam X hari) tetapi belum membeli brand target dalam L hari sebelum D."""
    sehari = pd.Timedelta(days=1)
    aktif = _jumlah(daily_all, D - pd.Timedelta(days=cfg.hari_aktif_cross), D - sehari)
    aktif = aktif[aktif > 0]
    beli = _jumlah(daily_brand, D - pd.Timedelta(days=cfg.hari_banding_turun), D - sehari)
    return aktif[~aktif.index.isin(beli[beli > 0].index)]


_KOLOM_KASUS = ["id", "sales", "outlet_k", "jenis", "brand", "anchor", "bulan", "kunci", "berhasil", "omset_pasca",
                "omset_sebelum", "sebanding", "ref_rate", "ref_omset", "punya_ss"]


def _hitung_kasus(t: pd.DataFrame, s: pd.DataFrame, dikecualikan: set, cfg: EvalConfig, n_hari: int,
                  ambang_hari: int, ambang_persen: float) -> tuple[pd.DataFrame, dict]:
    """Untuk tiap kelompok (jenis, brand, tanggal selesai): hasil tiap tugas + kelompok pembanding.
    Pembanding = outlet berkondisi sama pada tanggal itu, dikurangi semua outlet yang pernah punya catatan tugas.
    Referensi tiap tugas = tingkat order-lagi pembanding pada stratum ukuran-toko yang sama."""
    daily_all, cache = _harian(s), {}

    def daily_brand(b: str) -> pd.DataFrame:
        if b == "-":
            return daily_all
        if b not in cache:
            cache[b] = _harian(s[s["merk_k"] == b])
        return cache[b]

    sehari, baris, kontrol = pd.Timedelta(days=1), [], {}
    for (jenis, brand, anchor), g in t.groupby(["jenis", "brand", "anchor"], sort=False):
        d_b = daily_brand(brand)
        if jenis == "toko_tidur":
            ukuran = _kond_tidur(d_b, anchor, ambang_hari)
        elif jenis == "outlet_turun_order":
            ukuran = _kond_turun(d_b, anchor, cfg.hari_banding_turun, ambang_persen)
        else:
            ukuran = _kond_cross(daily_all, d_b, anchor, cfg)
        awal_h = anchor if cfg.termasuk_hari_selesai else anchor + sehari
        hasil_c = _jumlah(d_b, awal_h, anchor + pd.Timedelta(days=n_hari))
        sebelum = _jumlah(d_b, anchor - pd.Timedelta(days=n_hari), anchor - sehari)

        idx_k = ukuran.index[~ukuran.index.isin(dikecualikan)]
        uk = ukuran.loc[idx_k].to_numpy(dtype=float)
        ber_k = np.asarray(idx_k.isin(hasil_c.index), dtype=float)
        oms_k = hasil_c.reindex(idx_k).fillna(0.0).to_numpy(dtype=float)
        n_k = len(idx_k)
        if n_k >= cfg.min_stratum * cfg.jumlah_stratum:
            edges = np.unique(np.quantile(uk, np.linspace(0, 1, cfg.jumlah_stratum + 1)[1:-1]))
        else:
            edges = np.array([])
        bin_k = np.searchsorted(edges, uk, side="right")
        rate_all = float(ber_k.mean()) if n_k else np.nan
        omset_all = float(oms_k.mean()) if n_k else np.nan
        n_b, rate_b, omset_b = {}, {}, {}
        for b in np.unique(bin_k):
            m = bin_k == b
            n_b[int(b)], rate_b[int(b)], omset_b[int(b)] = int(m.sum()), float(ber_k[m].mean()), float(oms_k[m].mean())

        kunci = f"{jenis}|{brand}|{anchor:%Y-%m-%d}"
        kontrol[kunci] = {"outlets": set(idx_k), "n": n_k}
        for r in g.itertuples():
            o = r.outlet_k
            sebanding = o in ukuran.index
            ref_rate = ref_omset = np.nan
            if sebanding and n_k > 0:
                bt = int(np.searchsorted(edges, float(ukuran.loc[o]), side="right"))
                if n_b.get(bt, 0) >= cfg.min_stratum:
                    ref_rate, ref_omset = rate_b[bt], omset_b[bt]
                else:
                    ref_rate, ref_omset = rate_all, omset_all
            baris.append({
                "id": r.id, "sales": r.sales, "outlet_k": o, "jenis": jenis, "brand": brand, "anchor": anchor,
                "bulan": f"{anchor:%Y-%m}", "kunci": kunci, "berhasil": bool(o in hasil_c.index),
                "omset_pasca": float(hasil_c.get(o, 0.0)), "omset_sebelum": float(sebelum.get(o, 0.0)),
                "sebanding": bool(sebanding), "ref_rate": ref_rate, "ref_omset": ref_omset,
                "punya_ss": jenis != "toko_tidur"})
    return pd.DataFrame(baris, columns=_KOLOM_KASUS), kontrol


def _ci_selisih(p1: float, n1: int, p2: float, n2: int, z: float = 1.96) -> tuple[float, float]:
    """Selang kepercayaan ~95% selisih dua proporsi (koreksi Agresti-Caffo: tetap wajar saat p mendekati 0/1)."""
    a1, a2 = (p1 * n1 + 1) / (n1 + 2), (p2 * n2 + 1) / (n2 + 2)
    se = math.sqrt(a1 * (1 - a1) / (n1 + 2) + a2 * (1 - a2) / (n2 + 2))
    return (a1 - a2) - z * se, (a1 - a2) + z * se


def _ringkas(cases: pd.DataFrame, kontrol: dict, cfg: EvalConfig, min_t: int) -> dict:
    """Ringkasan satu kelompok tugas. Di bawah ambang sampel -> 'belum cukup data' TANPA angka persen."""
    n = len(cases)
    sb = cases[cases["sebanding"] & cases["ref_rate"].notna()] if n else cases
    n_sb = len(sb)
    ctrl: set = set()
    for k in (set(sb["kunci"]) if n_sb else set()):
        ctrl |= kontrol[k]["outlets"]
    n_ctrl = len(ctrl)
    out: dict = {"n_tugas_dievaluasi": n, "n_outlet_unik": int(cases["outlet_k"].nunique()) if n else 0,
                 "n_sebanding": n_sb, "n_kontrol_outlet_unik": n_ctrl}

    alasan = None
    if n < min_t:
        alasan = f"tugas selesai yang sudah bisa dinilai baru {n} (minimal {min_t})"
    elif n_sb < min_t:
        alasan = f"tugas yang toko-nya masih memenuhi kondisi pemicu saat selesai baru {n_sb} (minimal {min_t})"
    elif n_ctrl < cfg.min_kontrol:
        alasan = f"toko pembanding tanpa tugas baru {n_ctrl} outlet unik (minimal {cfg.min_kontrol})"
    if alasan:
        out.update(kesimpulan=BELUM_CUKUP, penafsiran=f"Belum cukup data untuk menyimpulkan: {alasan}.")
        return out

    p1, p2 = float(sb["berhasil"].mean()), float(sb["ref_rate"].mean())
    lo, hi = _ci_selisih(p1, n_sb, p2, n_ctrl)
    selisih = p1 - p2
    if lo > 0:
        kes = "ada hubungan positif yang teramati"
    elif hi < 0:
        kes = "ada hubungan negatif yang teramati"
    else:
        kes = "tidak ada perbedaan yang jelas"
    oms_t, oms_c = float(sb["omset_pasca"].mean()), float(sb["ref_omset"].mean())
    out.update(
        win_back_semua_persen=_pct(cases["berhasil"].mean()),
        win_back_tugas_persen=_pct(p1), win_back_kontrol_persen=_pct(p2),
        selisih_poin=round(selisih * 100, 1), ci95_selisih_poin=[round(lo * 100, 1), round(hi * 100, 1)],
        omset_pasca_rata_tugas=int(round(oms_t)), omset_pasca_rata_tugas_rp=ai_tools.format_idr(oms_t),
        omset_pasca_rata_kontrol=int(round(oms_c)), omset_pasca_rata_kontrol_rp=ai_tools.format_idr(oms_c),
        kesimpulan=kes)
    out["penafsiran"] = (
        f"Hubungan yang teramati: {_pct(p1)}% toko yang dikunjungi order lagi vs {_pct(p2)}% toko serupa tanpa tugas "
        f"(selisih {round(selisih * 100, 1)} poin; kisaran {round(lo * 100, 1)} sampai {round(hi * 100, 1)}; "
        f"sampel {n_sb} tugas vs {n_ctrl} outlet pembanding). {kes.capitalize()}. Ini bukan bukti sebab-akibat.")
    ss = cases[cases["punya_ss"]]
    if len(ss) >= min_t:
        a, b = float(ss["omset_sebelum"].mean()), float(ss["omset_pasca"].mean())
        out["sebelum_sesudah"] = {
            "n": int(len(ss)), "omset_sebelum_rata": int(round(a)), "omset_sebelum_rata_rp": ai_tools.format_idr(a),
            "omset_sesudah_rata": int(round(b)), "omset_sesudah_rata_rp": ai_tools.format_idr(b),
            "catatan": "Tanpa pembanding; rentan 'kembali ke rata-rata' (toko yang sedang turun cenderung naik sendiri). "
                       "Jangan dibaca sebagai efek tugas."}
    return out


# =============================================================================
# 6. ALAT 1 - EFEKTIVITAS TUGAS
# =============================================================================
@_aman("efektivitas_tugas")
def efektivitas_tugas(tugas_df: pd.DataFrame, df: pd.DataFrame, role: str, sales_name: str,
                      config: ai_tools.ToolsConfig, eval_cfg: Optional[EvalConfig] = None,
                      n_hari: Optional[int] = None, jenis: Optional[str] = None, sales: Optional[str] = None,
                      tugas_mulai: Optional[str] = None, tugas_selesai: Optional[str] = None,
                      sekarang: Optional[dt.datetime] = None) -> dict:
    """Efektivitas tugas kunjungan (FUNGSI MURNI): berapa persen outlet order lagi dalam N hari setelah tugas selesai
    (win-back), omset pasca-kunjungan, dibandingkan dengan toko serupa TANPA tugas. Menyertakan ukuran sampel dan
    'belum cukup data' bila sampel kecil. Hasilnya hubungan yang teramati, bukan bukti sebab-akibat."""
    cfg = eval_cfg or EvalConfig()
    N = _validasi_n(n_hari, cfg)
    now = pd.Timestamp(sekarang or K.sekarang())
    data = ai_tools.siapkan_data(df, role, sales_name, config, hari_ini=now.date())     # TLS + RLS penjualan
    akses, acuan = data.akses, data.tanggal_acuan
    bersih = bersihkan_tugas(tugas_df, now)
    dikecualikan = set(bersih["outlet_k"]) - {""}            # semua outlet yang pernah punya catatan tugas (hanya dipakai internal)
    v = K.tugas_terlihat(bersih, akses)                      # RLS tugas
    sel = _filter_tugas(v[v["status"] == "selesai"], sales, jenis, tugas_mulai, tugas_selesai, "selesai_dt")

    tanpa_tgl = sel[sel["selesai_dt"].isna()]
    t = sel[sel["selesai_dt"].notna()].copy()
    t["anchor"] = t["selesai_dt"].dt.normalize()
    a, b = ai_tools._ke_tanggal(tugas_mulai), ai_tools._ke_tanggal(tugas_selesai)
    if a is not None:
        t = t[t["anchor"] >= a]
    if b is not None:
        t = t[t["anchor"] <= b]
    tanpa_outlet = t["outlet_k"] == ""
    tak_didukung = ~t["jenis"].isin(JENIS_EVAL) | ((t["jenis"] == "cross_sell_brand") & (t["brand"] == "-"))
    tidak_dievaluasi = t[tak_didukung & ~tanpa_outlet]["jenis"].value_counts().to_dict()
    t = t[~tak_didukung & ~tanpa_outlet]
    matang = (t["anchor"] + pd.Timedelta(days=N)) <= acuan
    belum_matang = int((~matang).sum())
    t = t[matang]

    s = _siapkan_penjualan(data.df)
    cases, kontrol = _hitung_kasus(t, s, dikecualikan, cfg, N, int(config.ambang_toko_tidur_hari),
                                   float(config.ambang_turun_persen)) if len(t) else (pd.DataFrame(columns=_KOLOM_KASUS), {})

    ringkasan = _ringkas(cases, kontrol, cfg, cfg.min_tugas)
    per_jenis = []
    for j in sorted(cases["jenis"].unique()):
        per_jenis.append({"jenis": j, "label": K.JENIS_TUGAS.get(j, j),
                          **_ringkas(cases[cases["jenis"] == j], kontrol, cfg, cfg.min_tugas_subgrup)})
    per_bulan = [{"bulan": m, **_ringkas(cases[cases["bulan"] == m], kontrol, cfg, cfg.min_tugas_subgrup)}
                 for m in sorted(cases["bulan"].unique())][-cfg.maks_bulan:]
    per_sales_ok = _boleh_per_sales(akses)
    per_sales = []
    if per_sales_ok:
        urut = cases["sales"].value_counts().head(cfg.maks_baris).index
        per_sales = [{"sales": nm, **_ringkas(cases[cases["sales"] == nm], kontrol, cfg, cfg.min_tugas_subgrup)} for nm in urut]

    catatan = list(data.catatan) + [
        PERINGATAN_KAUSAL,
        "Pembanding = outlet yang memenuhi kondisi pemicu yang sama pada tanggal tugas selesai dan tidak pernah punya "
        "catatan tugas apa pun, dibobot menurut ukuran toko. Ambang toko tidur/turun order memakai nilai default sistem.",
    ]
    if akses.level != "penuh":
        catatan.append("Penjualan yang dihitung hanya yang terlihat oleh akun ini (sales: transaksinya sendiri; supervisor: "
                       "merk yang dikelola), sehingga order dari sales/merk lain tidak terhitung dan sampel bisa lebih kecil.")
    if not per_sales_ok:
        catatan.append("Rincian per sales hanya tersedia untuk approver (akses penuh/supervisor).")
    if belum_matang:
        catatan.append(f"{belum_matang} tugas selesai belum dinilai karena jendela {N} hari belum lengkap sampai {acuan:%Y-%m-%d}.")
    return ai_tools._ok(
        "efektivitas_tugas",
        definisi=(f"Berhasil = outlet order lagi dalam {N} hari sejak tugas ditandai selesai"
                  f"{' (hari selesai ikut dihitung)' if cfg.termasuk_hari_selesai else ''}. "
                  f"Pembanding = toko serupa tanpa tugas pada tanggal yang sama."),
        jendela_hari=N, tanggal_acuan=acuan,
        filter={"jenis": _pilih_jenis(jenis), "sales": sales, "tugas_mulai": tugas_mulai, "tugas_selesai": tugas_selesai},
        tugas_selesai_total=int(len(sel)), belum_matang=belum_matang, selesai_tanpa_tanggal=int(len(tanpa_tgl)),
        tidak_dievaluasi=tidak_dievaluasi,
        ringkasan=ringkasan, per_jenis=per_jenis, per_bulan=per_bulan,
        per_sales=per_sales, per_sales_tersedia=per_sales_ok,
        kualitas_data=ringkas_kualitas(v), peringatan=PERINGATAN_KAUSAL, catatan=catatan)


# =============================================================================
# 7. ALAT 2 - KINERJA PENYELESAIAN TUGAS
# =============================================================================
def _metrik_kinerja(d: pd.DataFrame, now: pd.Timestamp, min_t: int) -> dict:
    """d = tugas yang sudah ditugaskan (disetujui/dikerjakan/selesai). Hitungan selalu ada; rasio hanya bila n >= min_t."""
    hari_ini = now.normalize()
    sel = d[d["status"] == "selesai"]
    jt = d[d["tenggat_dt"].notna() & (d["tenggat_dt"].dt.normalize() <= hari_ini)]
    jt_sel = int((jt["status"] == "selesai").sum())
    sv = sel[sel["selesai_dt"].notna() & sel["tenggat_dt"].notna()]
    tepat = int((sv["selesai_dt"].dt.normalize() <= sv["tenggat_dt"].dt.normalize()).sum())
    dur = sel["durasi_hari"].dropna()
    lewat = d[d["status"].isin(("disetujui", "dikerjakan")) & d["tenggat_dt"].notna()
              & (d["tenggat_dt"].dt.normalize() < hari_ini)]

    def rasio(k: int, n: int) -> Optional[float]:
        return _pct(k / n) if n >= min_t else None

    return {
        "n_ditugaskan": int(len(d)), "n_selesai": int(len(sel)), "n_berjalan_lewat_tenggat": int(len(lewat)),
        "rasio_selesai_persen": rasio(len(sel), len(d)),
        "n_jatuh_tempo": int(len(jt)), "n_jatuh_tempo_selesai": jt_sel,
        "rasio_selesai_jatuh_tempo_persen": rasio(jt_sel, len(jt)),
        "n_ketepatan_waktu_dinilai": int(len(sv)), "tepat_waktu_persen": rasio(tepat, len(sv)),
        "n_durasi_dinilai": int(len(dur)),
        "rata_hari_persetujuan_ke_selesai": round(float(dur.mean()), 1) if len(dur) >= min_t else None,
        "median_hari_persetujuan_ke_selesai": round(float(dur.median()), 1) if len(dur) >= min_t else None,
        "status_sampel": "cukup" if len(d) >= min_t else BELUM_CUKUP,
    }


@_aman("kinerja_penyelesaian_tugas")
def kinerja_penyelesaian_tugas(tugas_df: pd.DataFrame, role: str, sales_name: str, config: ai_tools.ToolsConfig,
                               eval_cfg: Optional[EvalConfig] = None, sales: Optional[str] = None,
                               jenis: Optional[str] = None, tugas_mulai: Optional[str] = None,
                               tugas_selesai: Optional[str] = None, sekarang: Optional[dt.datetime] = None) -> dict:
    """Kinerja penyelesaian tugas (FUNGSI MURNI): rasio selesai, rasio selesai dari yang sudah jatuh tempo,
    ketepatan waktu, dan rata-rata hari dari persetujuan ke selesai. Filter tanggal = tanggal tugas dibuat."""
    cfg = eval_cfg or EvalConfig()
    now = pd.Timestamp(sekarang or K.sekarang())
    akses = ai_tools.tentukan_akses(role, sales_name, config)
    bersih = bersihkan_tugas(tugas_df, now)
    v = K.tugas_terlihat(bersih, akses)
    v = _filter_tugas(v, sales, jenis, tugas_mulai, tugas_selesai, "dibuat_dt")
    d = v[v["status"].isin(STATUS_DITUGASKAN)]
    mt = cfg.min_tugas_kinerja

    per_sales_ok = _boleh_per_sales(akses)
    per_sales = []
    if per_sales_ok and len(d):
        for nm in d["sales"].value_counts().head(cfg.maks_baris).index:
            per_sales.append({"sales": nm, **_metrik_kinerja(d[d["sales"] == nm], now, mt)})
    catatan = ["Rasio selesai memuat tugas yang belum jatuh tempo; gunakan 'rasio_selesai_jatuh_tempo_persen' untuk "
               "perbandingan yang lebih adil.",
               f"Rasio hanya ditampilkan bila minimal {mt} tugas; di bawah itu 'belum cukup data'."]
    if not per_sales_ok:
        catatan.append("Rincian per sales hanya tersedia untuk approver (akses penuh/supervisor).")
    return ai_tools._ok(
        "kinerja_penyelesaian_tugas", tanggal_referensi=now.normalize(),
        filter={"jenis": _pilih_jenis(jenis), "sales": sales, "tugas_mulai": tugas_mulai, "tugas_selesai": tugas_selesai},
        definisi="Tugas yang dihitung = berstatus disetujui/dikerjakan/selesai. Tepat waktu = selesai pada/ sebelum tenggat.",
        jumlah_ditolak=int((v["status"] == "ditolak").sum()), jumlah_usulan_menunggu=int((v["status"] == "usulan").sum()),
        ringkasan=_metrik_kinerja(d, now, mt),
        per_jenis=[{"jenis": j, "label": K.JENIS_TUGAS.get(j, j), **_metrik_kinerja(d[d["jenis"] == j], now, mt)}
                   for j in sorted(d["jenis"].unique())],
        per_sales=per_sales, per_sales_tersedia=per_sales_ok,
        kualitas_data=ringkas_kualitas(v), catatan=catatan)


# =============================================================================
# 8. ALAT 3 - RINGKASAN TUGAS TERBUKA
# =============================================================================
@_aman("ringkasan_tugas_terbuka")
def ringkasan_tugas_terbuka(tugas_df: pd.DataFrame, role: str, sales_name: str, config: ai_tools.ToolsConfig,
                            eval_cfg: Optional[EvalConfig] = None, sales: Optional[str] = None,
                            jenis: Optional[str] = None, top_n: int = 10,
                            sekarang: Optional[dt.datetime] = None) -> dict:
    """Ringkasan tugas yang masih terbuka (usulan/disetujui/dikerjakan): jumlah per status & prioritas, yang lewat
    tenggat, yang segera jatuh tempo, usulan yang lama menunggu persetujuan, dan daftar terlambat."""
    cfg = eval_cfg or EvalConfig()
    now = pd.Timestamp(sekarang or K.sekarang())
    hari_ini = now.normalize()
    akses = ai_tools.tentukan_akses(role, sales_name, config)
    v = K.tugas_terlihat(bersihkan_tugas(tugas_df, now), akses)
    v = _filter_tugas(v, sales, jenis, None, None, "dibuat_dt")
    o = v[v["status"].isin(STATUS_TERBUKA)].copy()
    tg = o["tenggat_dt"].dt.normalize()
    berjalan = o["status"].isin(("disetujui", "dikerjakan"))
    o["hari_terlambat"] = (hari_ini - tg).dt.days
    terlambat = berjalan & tg.notna() & (tg < hari_ini)
    segera = berjalan & tg.notna() & (tg >= hari_ini) & (tg <= hari_ini + pd.Timedelta(days=cfg.tenggat_segera_hari))
    umur = (hari_ini - o["dibuat_dt"].dt.normalize()).dt.days
    menunggu_lama = (o["status"] == "usulan") & (umur >= cfg.usulan_menunggu_lama_hari)

    try:
        n = max(1, min(int(top_n), cfg.maks_baris))
    except (TypeError, ValueError):
        n = 10
    daftar = o[terlambat].sort_values("hari_terlambat", ascending=False).head(n)
    per_sales_ok = _boleh_per_sales(akses)
    per_sales = []
    if per_sales_ok and len(o):
        for nm in o["sales"].value_counts().head(cfg.maks_baris).index:
            m = o["sales"] == nm
            per_sales.append({"sales": nm, "terbuka": int(m.sum()), "terlambat": int((m & terlambat).sum()),
                              "segera_jatuh_tempo": int((m & segera).sum())})
    return ai_tools._ok(
        "ringkasan_tugas_terbuka", tanggal_referensi=hari_ini,
        filter={"jenis": _pilih_jenis(jenis), "sales": sales},
        jumlah_terbuka=int(len(o)),
        per_status={k: int((o["status"] == k).sum()) for k in STATUS_TERBUKA},
        per_prioritas={k: int((o["prioritas"] == k).sum()) for k in ("tinggi", "sedang", "rendah")},
        prioritas_tidak_diketahui=int((~o["prioritas"].isin(("tinggi", "sedang", "rendah"))).sum()),
        terlambat=int(terlambat.sum()), segera_jatuh_tempo=int(segera.sum()),
        usulan_menunggu_lama=int(menunggu_lama.sum()), tenggat_tidak_valid=int((berjalan & tg.isna()).sum()),
        daftar_terlambat=[{"id": r.id, "outlet": r.outlet, "sales": r.sales, "jenis": r.jenis, "status": r.status,
                           "tenggat": r.tenggat_dt, "hari_terlambat": int(r.hari_terlambat)} for r in daftar.itertuples()],
        per_sales=per_sales, per_sales_tersedia=per_sales_ok, kualitas_data=ringkas_kualitas(v),
        catatan=([] if per_sales_ok else ["Rincian per sales hanya tersedia untuk approver (akses penuh/supervisor)."]))


# =============================================================================
# 9. PENDAFTARAN KE ai_tools.TOOL_REGISTRY  (df/role/sales_name/config disuntik program; tugas lewat penyedia)
# =============================================================================
def _alat(nama: str) -> Callable:
    def deko(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def p(*a, **k):
            try:
                return fn(*a, **k)
            except ToolError as e:
                return ai_tools._gagal(nama, str(e))
        p.__name__ = nama
        ai_tools.TOOL_REGISTRY[nama] = p
        return p
    return deko


@_alat("efektivitas_tugas")
def _alat_efektivitas(df: pd.DataFrame, role: str, sales_name: str, config: ai_tools.ToolsConfig,
                      n_hari: Optional[int] = None, jenis: Optional[str] = None, sales: Optional[str] = None,
                      tugas_mulai: Optional[str] = None, tugas_selesai: Optional[str] = None) -> dict:
    """Efektivitas tugas kunjungan: persen outlet yang order lagi dalam N hari setelah tugas selesai (win-back),
    omset pasca-kunjungan, dan perbandingan dengan toko serupa tanpa tugas, lengkap dengan ukuran sampel. Bila sampel
    kecil hasilnya 'belum cukup data'. Hasil adalah hubungan yang teramati, bukan bukti sebab-akibat."""
    return efektivitas_tugas(_ambil_tugas(), df, role, sales_name, config, n_hari=n_hari, jenis=jenis, sales=sales,
                             tugas_mulai=tugas_mulai, tugas_selesai=tugas_selesai)


@_alat("kinerja_penyelesaian_tugas")
def _alat_kinerja(df: pd.DataFrame, role: str, sales_name: str, config: ai_tools.ToolsConfig,
                  jenis: Optional[str] = None, sales: Optional[str] = None,
                  tugas_mulai: Optional[str] = None, tugas_selesai: Optional[str] = None) -> dict:
    """Kinerja penyelesaian tugas kunjungan: rasio selesai, ketepatan waktu, dan rata-rata hari dari persetujuan ke
    selesai, per jenis dan (khusus approver) per sales."""
    return kinerja_penyelesaian_tugas(_ambil_tugas(), role, sales_name, config, jenis=jenis, sales=sales,
                                      tugas_mulai=tugas_mulai, tugas_selesai=tugas_selesai)


@_alat("ringkasan_tugas_terbuka")
def _alat_terbuka(df: pd.DataFrame, role: str, sales_name: str, config: ai_tools.ToolsConfig,
                  jenis: Optional[str] = None, sales: Optional[str] = None, top_n: int = 10) -> dict:
    """Ringkasan tugas kunjungan yang masih terbuka: jumlah per status/prioritas, yang lewat tenggat, segera jatuh
    tempo, usulan yang lama menunggu, dan daftar tugas terlambat."""
    return ringkasan_tugas_terbuka(_ambil_tugas(), role, sales_name, config, jenis=jenis, sales=sales, top_n=top_n)
