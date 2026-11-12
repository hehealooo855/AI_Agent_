"""
ai_tools.py - FASE 1: Alat Hitung untuk modul "Asisten AI".

Prinsip: "Program yang menghitung, AI yang menjelaskan."
  * Semua angka, agregasi, filter role (TLS + RLS) dihitung di sini dengan pandas murni.
  * AI (Gemini) nanti hanya MEMANGGIL fungsi di sini dan MENJELASKAN hasilnya.
  * df, role, dan sales_name SELALU disuntikkan oleh program (lihat `jalankan_tool`),
    bukan dipilih oleh AI -> AI tidak bisa melihat data di luar hak akses user.

File ini TIDAK meng-import app.py (akan menjalankan Streamlit & menyebabkan import melingkar).
TARGET_DATABASE & INDIVIDUAL_TARGETS dikirim lewat `ToolsConfig`.

Contoh pemakaian di app.py (setelah df = load_data(fast_mode)):

    import ai_tools
    cfg = ai_tools.ToolsConfig(target_database=TARGET_DATABASE,
                               individual_targets=INDIVIDUAL_TARGETS)
    hasil = ai_tools.ringkasan_omset(df, user_role, user_name, cfg,
                                     mulai="2026-11-01", selesai="2026-11-11")

Setiap alat mengembalikan dict yang aman di-JSON-kan:
    {"ok": True, "alat": "...", ...}   atau   {"ok": False, "alat": "...", "pesan": "..."}
"""
from __future__ import annotations

import datetime as dt
import functools
import inspect
from dataclasses import dataclass
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

# =============================================================================
# 1. KONFIGURASI & KONSTANTA
# =============================================================================
KOLOM_WAJIB = ["Tanggal", "Nama Outlet", "Penjualan", "Merk", "Nama Barang", "Jumlah"]
KOLOM_OPSIONAL = ["Qty", "No Faktur", "Kota", "Provinsi"]  # fitur terkait dilewati bila tidak ada

# app.py mengisi tanggal yang gagal dibaca dengan 2000-01-01 -> dianggap tanggal tidak valid
TANGGAL_PLACEHOLDER = pd.Timestamp("2000-01-01")
NILAI_KOSONG = {"", "-", "nan", "NaN", "NAN", "None", "NONE", "0", "0.0", "."}


@dataclass(frozen=True)
class ToolsConfig:
    """Semua angka/aturan yang bisa diubah ada di sini, bukan di dalam fungsi."""

    target_database: dict          # {"TIM UTAMA": {"Wardah": 5065200000, ...}}  (per supervisor -> per merk)
    individual_targets: dict       # {"AGUS SANTOSO": {"Biore": 5700000, ...}}    (per sales -> per merk)

    # --- hak akses (disamakan dengan main_dashboard di app.py) ---
    role_akses_penuh: tuple = ("manager", "direktur")
    nama_akses_penuh: tuple = ("fauziah",)
    role_tanpa_batas_waktu: tuple = ("direktur",)   # TLS: selain role ini hanya melihat tahun berjalan

    # --- ambang bisnis (keputusan Fase 1) ---
    ambang_toko_tidur_hari: int = 30        # tidak order >= 30 hari dari tanggal data terakhir
    ambang_turun_persen: float = 30.0       # omset turun >= 30% vs periode sebelumnya (sama panjang)

    # --- warna status pencapaian (sama dengan get_color_achv di app.py) ---
    batas_hijau: float = 85.0
    batas_kuning: float = 50.0

    # --- batas keluaran agar jawaban AI tidak kebanjiran baris ---
    maks_baris: int = 50


class ToolError(ValueError):
    """Kesalahan yang aman ditampilkan ke user/AI (bukan bug)."""


# =============================================================================
# 2. UTILITAS UMUM
# =============================================================================
def format_idr(nilai: float) -> str:
    """Sama dengan format_idr di app.py: 'Rp 1.234.567'."""
    try:
        return f"Rp {float(nilai):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return "Rp 0"


def _py(obj: Any) -> Any:
    """Ubah tipe numpy/pandas menjadi tipe Python biasa agar aman untuk JSON."""
    if isinstance(obj, dict):
        return {str(k): _py(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_py(v) for v in obj]
    if isinstance(obj, (pd.Timestamp, dt.datetime, dt.date)):
        return None if pd.isna(obj) else pd.Timestamp(obj).strftime("%Y-%m-%d")
    if isinstance(obj, np.str_):
        return str(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        return None if np.isnan(obj) else float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def _persen(bagian: float, total: float, desimal: int = 2) -> Optional[float]:
    if not total:
        return None
    return round(float(bagian) / float(total) * 100.0, desimal)


def _bulatkan_rp(nilai: float) -> int:
    return int(round(float(nilai)))


def _ok(alat: str, **isi) -> dict:
    return _py({"ok": True, "alat": alat, **isi})


def _gagal(alat: str, pesan: str, **isi) -> dict:
    return _py({"ok": False, "alat": alat, "pesan": pesan, **isi})


TOOL_REGISTRY: dict[str, Callable[..., dict]] = {}


def _tool(fn: Callable[..., dict]) -> Callable[..., dict]:
    """Daftarkan alat ke registry + tangkap ToolError menjadi {"ok": False}."""

    @functools.wraps(fn)
    def pembungkus(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ToolError as e:
            return _gagal(fn.__name__, str(e))

    TOOL_REGISTRY[fn.__name__] = pembungkus
    return pembungkus


def _faktur_valid(seri: pd.Series) -> pd.Series:
    """Nomor faktur yang sah (aturan sama dengan hitung 'Transaksi' di app.py: bukan kosong, panjang > 2)."""
    s = seri.astype(str).str.strip()
    return s[~s.isin(NILAI_KOSONG) & (s.str.len() > 2)]


def _outlet_valid(d: pd.DataFrame) -> pd.DataFrame:
    s = d["Nama Outlet"].astype(str).str.strip()
    return d[~s.isin(NILAI_KOSONG)]


# =============================================================================
# 3. HAK AKSES: TLS (batas waktu) + RLS (batas baris)  -- logika disalin dari main_dashboard()
# =============================================================================
@dataclass(frozen=True)
class Akses:
    role: str
    nama: str          # huruf besar, cocok dengan kolom 'Penjualan'
    level: str         # "penuh" | "supervisor" | "sales"
    merk_boleh: tuple = ()
    batas_waktu: bool = True   # True = hanya tahun berjalan


@dataclass(frozen=True)
class DataAman:
    df: pd.DataFrame
    akses: Akses
    tanggal_acuan: pd.Timestamp     # tanggal transaksi terakhir pada data yang BOLEH diakses user
    catatan: tuple = ()


def tentukan_akses(role: str, sales_name: str, config: ToolsConfig) -> Akses:
    role_n = str(role or "").strip().lower()
    nama = str(sales_name or "").strip()
    if not nama:
        raise ToolError("Akses ditolak: identitas user (sales_name) tidak tersedia.")
    nama_up = nama.upper()
    batas_waktu = role_n not in config.role_tanpa_batas_waktu

    if role_n in config.role_akses_penuh or nama.lower() in config.nama_akses_penuh:
        return Akses(role_n, nama_up, "penuh", (), batas_waktu)
    if nama_up in config.target_database:
        return Akses(role_n, nama_up, "supervisor", tuple(config.target_database[nama_up].keys()), batas_waktu)
    # role tidak dikenal / sales biasa -> paling ketat (least privilege)
    return Akses(role_n, nama_up, "sales", (), batas_waktu)


def siapkan_data(df: pd.DataFrame, role: str, sales_name: str, config: ToolsConfig,
                 hari_ini: Optional[dt.date] = None) -> DataAman:
    """Validasi kolom, buang tanggal tidak valid, lalu terapkan TLS + RLS."""
    if df is None or len(df) == 0:
        raise ToolError("Data penjualan kosong atau belum termuat.")
    kurang = [k for k in KOLOM_WAJIB if k not in df.columns]
    if kurang:
        raise ToolError(f"Kolom wajib tidak ditemukan di DataFrame: {kurang}")

    akses = tentukan_akses(role, sales_name, config)
    d = df
    if not pd.api.types.is_datetime64_any_dtype(d["Tanggal"]):
        d = d.assign(Tanggal=pd.to_datetime(d["Tanggal"], errors="coerce"))
    d = d[d["Tanggal"] > TANGGAL_PLACEHOLDER]          # NaT & placeholder 2000-01-01 ikut terbuang

    catatan = []
    if akses.batas_waktu:                               # TLS
        tahun = (hari_ini or dt.date.today()).year
        d = d[d["Tanggal"].dt.year >= tahun]
        catatan.append(f"Histori dibatasi sejak 1 Januari {tahun} untuk role ini; toko yang tidak "
                       f"bertransaksi sama sekali di {tahun} tidak terlihat.")

    if akses.level == "supervisor":                     # RLS
        d = d[d["Merk"].isin(akses.merk_boleh)]
    elif akses.level == "sales":
        d = d[d["Penjualan"] == akses.nama]

    d = d.reset_index(drop=True)
    if d.empty:
        raise ToolError("Tidak ada data transaksi yang dapat diakses untuk user ini.")
    return DataAman(d, akses, d["Tanggal"].max().normalize(), tuple(catatan))


# =============================================================================
# 4. PERIODE & FILTER OPSIONAL
# =============================================================================
def _ke_tanggal(x: Any) -> Optional[pd.Timestamp]:
    if x is None or x == "":
        return None
    ts = pd.to_datetime(x, errors="coerce")
    if pd.isna(ts):
        raise ToolError(f"Format tanggal tidak dikenali: {x!r} (gunakan YYYY-MM-DD).")
    return ts.normalize()


def _tentukan_periode(acuan: pd.Timestamp, mulai: Any, selesai: Any) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Default: tanggal 1 bulan data terakhir s/d tanggal data terakhir (sama dengan default dashboard)."""
    akhir = _ke_tanggal(selesai) or acuan
    awal = _ke_tanggal(mulai) or akhir.replace(day=1)
    if awal > akhir:
        raise ToolError(f"Tanggal mulai ({awal:%Y-%m-%d}) lebih besar dari tanggal selesai ({akhir:%Y-%m-%d}).")
    return awal, akhir


def _periode_sebelumnya(awal: pd.Timestamp, akhir: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Periode tepat sebelum `awal` dengan jumlah hari yang sama."""
    n = (akhir - awal).days + 1
    prev_akhir = awal - pd.Timedelta(days=1)
    return prev_akhir - pd.Timedelta(days=n - 1), prev_akhir


def _mask(d: pd.DataFrame, awal: pd.Timestamp, akhir: pd.Timestamp) -> pd.Series:
    return (d["Tanggal"] >= awal) & (d["Tanggal"] < akhir + pd.Timedelta(days=1))


def _label_periode(awal: pd.Timestamp, akhir: pd.Timestamp) -> dict:
    return {"mulai": awal, "selesai": akhir, "jumlah_hari": (akhir - awal).days + 1}


def _cocokkan(seri: pd.Series, teks: str, label: str, banyak: bool = False) -> list[str]:
    """Cocokkan teks bebas dari AI ke nilai nyata di data (persis dulu, lalu 'mengandung')."""
    t = str(teks).strip().upper()
    nilai = pd.Series(seri.dropna().astype(str).unique())
    tepat = nilai[nilai.str.strip().str.upper() == t]
    if len(tepat):
        return list(tepat)
    mirip = nilai[nilai.str.upper().str.contains(t, regex=False)]
    if len(mirip) == 0:
        raise ToolError(f"{label} '{teks}' tidak ditemukan pada data yang boleh diakses.")
    if len(mirip) > 1 and not banyak:
        raise ToolError(f"{label} '{teks}' ambigu. Kandidat: {sorted(mirip)[:10]}")
    return list(mirip)


def _filter_opsional(d: pd.DataFrame, sales: Optional[str] = None, merk: Optional[str] = None,
                     outlet: Optional[str] = None) -> pd.DataFrame:
    if sales:
        d = d[d["Penjualan"].astype(str).isin(_cocokkan(d["Penjualan"], sales, "Sales"))]
    if merk:
        d = d[d["Merk"].astype(str).isin(_cocokkan(d["Merk"], merk, "Merk"))]
    if outlet:
        d = d[d["Nama Outlet"].astype(str).isin(_cocokkan(d["Nama Outlet"], outlet, "Outlet", banyak=True))]
    return d


def _batas(n: Optional[int], config: ToolsConfig, default: int) -> int:
    try:
        n = int(n) if n is not None else default
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, config.maks_baris))


def _status(pct: Optional[float], config: ToolsConfig) -> str:
    if pct is None:
        return "target belum diisi"
    if pct < config.batas_kuning:
        return "merah"
    if pct < config.batas_hijau:
        return "kuning"
    return "hijau"


def _sales_terakhir_per_outlet(d: pd.DataFrame) -> pd.Series:
    """Sales yang menangani transaksi terbaru tiap outlet."""
    idx = d.groupby("Nama Outlet", observed=True)["Tanggal"].idxmax()
    return d.loc[idx].set_index("Nama Outlet")["Penjualan"].astype(str)


# =============================================================================
# 5. ALAT 1 - RINGKASAN OMSET
# =============================================================================
def _metrik(x: pd.DataFrame) -> dict:
    return {
        "omset": float(x["Jumlah"].sum()),
        "qty": float(x["Qty"].sum()) if "Qty" in x.columns else None,
        "transaksi_faktur": int(_faktur_valid(x["No Faktur"]).nunique()) if "No Faktur" in x.columns else None,
        "outlet_aktif": int(_outlet_valid(x)["Nama Outlet"].nunique()),
    }


@_tool
def ringkasan_omset(df: pd.DataFrame, role: str, sales_name: str, config: ToolsConfig,
                    mulai: Optional[str] = None, selesai: Optional[str] = None,
                    sales: Optional[str] = None, merk: Optional[str] = None,
                    outlet: Optional[str] = None, top_n: int = 5) -> dict:
    """Ringkasan omset periode: omset, qty, jumlah faktur, outlet aktif, perbandingan dengan periode
    sebelumnya (sama panjang), serta kontributor teratas per merk dan per sales."""
    data = siapkan_data(df, role, sales_name, config)
    awal, akhir = _tentukan_periode(data.tanggal_acuan, mulai, selesai)
    pa, pb = _periode_sebelumnya(awal, akhir)
    d = _filter_opsional(data.df, sales, merk, outlet)
    n = _batas(top_n, config, 5)

    cur, prev = d[_mask(d, awal, akhir)], d[_mask(d, pa, pb)]
    m_cur, m_prev = _metrik(cur), _metrik(prev)
    selisih = m_cur["omset"] - m_prev["omset"]

    def _top(kolom: str) -> list[dict]:
        g = cur.groupby(kolom, observed=True)["Jumlah"].sum().sort_values(ascending=False).head(n)
        total = m_cur["omset"]
        return [{"nama": str(k), "omset": _bulatkan_rp(v), "omset_rp": format_idr(v),
                 "kontribusi_persen": _persen(v, total)} for k, v in g.items()]

    catatan = list(data.catatan)
    if pa < d["Tanggal"].min().normalize():
        catatan.append("Periode pembanding sebagian/seluruhnya berada di luar data yang boleh diakses; "
                       "perubahan vs periode sebelumnya bisa tidak mewakili.")

    return _ok(
        "ringkasan_omset",
        periode=_label_periode(awal, akhir),
        periode_pembanding=_label_periode(pa, pb),
        filter={"sales": sales, "merk": merk, "outlet": outlet},
        omset=_bulatkan_rp(m_cur["omset"]), omset_rp=format_idr(m_cur["omset"]),
        omset_rata_harian=_bulatkan_rp(m_cur["omset"] / ((akhir - awal).days + 1)),
        qty=m_cur["qty"], transaksi_faktur=m_cur["transaksi_faktur"], outlet_aktif=m_cur["outlet_aktif"],
        pembanding={
            "omset": _bulatkan_rp(m_prev["omset"]), "omset_rp": format_idr(m_prev["omset"]),
            "selisih_omset": _bulatkan_rp(selisih), "selisih_omset_rp": format_idr(selisih),
            "perubahan_persen": _persen(selisih, m_prev["omset"]) if m_prev["omset"] > 0 else None,
            "selisih_outlet_aktif": m_cur["outlet_aktif"] - m_prev["outlet_aktif"],
            "selisih_transaksi": (m_cur["transaksi_faktur"] - m_prev["transaksi_faktur"])
            if m_cur["transaksi_faktur"] is not None else None,
        },
        top_merk=_top("Merk"),
        top_sales=_top("Penjualan") if data.akses.level != "sales" else [],
        catatan=catatan,
    )


# =============================================================================
# 6. ALAT 2 - PENCAPAIAN TARGET  (target dibandingkan "apa adanya" dengan periode terpilih)
# =============================================================================
def _baris_target(nama_kolom: str, nama: str, target: float, realisasi: float, config: ToolsConfig) -> dict:
    pct = _persen(realisasi, target) if target and target > 0 else None
    return {
        nama_kolom: nama,
        "target": _bulatkan_rp(target), "target_rp": format_idr(target),
        "realisasi": _bulatkan_rp(realisasi), "realisasi_rp": format_idr(realisasi),
        "pencapaian_persen": pct,
        "kekurangan": _bulatkan_rp(max(target - realisasi, 0)),
        "kekurangan_rp": format_idr(max(target - realisasi, 0)),
        "status": _status(pct, config),
    }


@_tool
def pencapaian_target(df: pd.DataFrame, role: str, sales_name: str, config: ToolsConfig,
                      mulai: Optional[str] = None, selesai: Optional[str] = None,
                      tim: Optional[str] = None, sales: Optional[str] = None) -> dict:
    """Pencapaian target (realisasi vs target) per tim/merk atau per sales/merk untuk periode terpilih.
    Target tidak diproporsikan: dibandingkan apa adanya, sama seperti Target Monitor di dashboard."""
    data = siapkan_data(df, role, sales_name, config)
    akses = data.akses
    awal, akhir = _tentukan_periode(data.tanggal_acuan, mulai, selesai)
    dp = data.df[_mask(data.df, awal, akhir)]
    periode = _label_periode(awal, akhir)

    catatan = ["Target dibandingkan apa adanya dengan realisasi pada periode terpilih (tanpa proporsi hari)."]
    satu_bulan_penuh = (awal.day == 1 and awal.month == akhir.month and awal.year == akhir.year)
    if not satu_bulan_penuh:
        catatan.append("Periode bukan satu bulan kalender yang dimulai tanggal 1; persentase mungkin tidak "
                       "sebanding dengan target.")

    # --- tentukan target sales vs target tim ---
    sales_target: Optional[str] = None
    if akses.level == "sales":
        sales_target = akses.nama
    elif sales:
        if str(sales).strip().upper() in config.target_database:      # dashboard mengizinkan memilih nama tim
            tim = sales
        else:
            sales_target = _cocokkan(data.df["Penjualan"], sales, "Sales")[0]

    # ---------- A. Level sales ----------
    if sales_target:
        target_map = config.individual_targets.get(sales_target)
        ds = dp[dp["Penjualan"].astype(str) == sales_target]
        total_real = float(ds["Jumlah"].sum())
        if not target_map:
            return _ok("pencapaian_target", level="sales", sales=sales_target, periode=periode,
                       target_tersedia=False, realisasi=_bulatkan_rp(total_real), realisasi_rp=format_idr(total_real),
                       catatan=catatan + [f"{sales_target} belum punya target individu di INDIVIDUAL_TARGETS."])
        per_merk = ds.groupby("Merk", observed=True)["Jumlah"].sum()
        rincian = [_baris_target("merk", b, t, float(per_merk.get(b, 0.0)), config) for b, t in target_map.items()]
        t_tot = float(sum(target_map.values()))
        r_tot = float(sum(r["realisasi"] for r in rincian))
        return _ok("pencapaian_target", level="sales", sales=sales_target, periode=periode, target_tersedia=True,
                   total=_baris_target("nama", sales_target, t_tot, r_tot, config),
                   rincian_merk=rincian, omset_di_luar_target=_bulatkan_rp(total_real - r_tot), catatan=catatan)

    # ---------- B. Level tim (supervisor -> merk) ----------
    if akses.level == "supervisor":
        tim_tersedia = [akses.nama]
    else:
        tim_tersedia = list(config.target_database.keys())
    if tim:
        cocok = [t for t in tim_tersedia if t.upper() == str(tim).strip().upper()]
        if not cocok:
            cocok = [t for t in tim_tersedia if str(tim).strip().upper() in t.upper()]
        if not cocok:
            raise ToolError(f"Tim '{tim}' tidak ditemukan atau di luar hak akses. Tim tersedia: {tim_tersedia}")
        tim_tersedia = cocok

    per_merk = dp.groupby("Merk", observed=True)["Jumlah"].sum()
    daftar_tim, t_all, r_all = [], 0.0, 0.0
    for nama_tim in tim_tersedia:
        merk_map = config.target_database[nama_tim]
        rincian = [_baris_target("merk", b, t, float(per_merk.get(b, 0.0)), config) for b, t in merk_map.items()]
        rincian.sort(key=lambda r: r["realisasi"], reverse=True)
        t_tim, r_tim = float(sum(merk_map.values())), float(sum(r["realisasi"] for r in rincian))
        t_all, r_all = t_all + t_tim, r_all + r_tim
        daftar_tim.append({**_baris_target("tim", nama_tim, t_tim, r_tim, config), "rincian_merk": rincian})

    hasil = dict(level="tim", periode=periode, target_tersedia=True,
                 total=_baris_target("nama", "TOTAL" if len(daftar_tim) > 1 else daftar_tim[0]["tim"], t_all, r_all, config),
                 tim=daftar_tim, catatan=catatan)
    if akses.level == "penuh" and not tim:      # tampilan nasional: tunjukkan omset merk yang belum ber-target
        merk_bertarget = {b for m in config.target_database.values() for b in m}
        tanpa = dp[~dp["Merk"].astype(str).isin(merk_bertarget)]["Jumlah"].sum()
        hasil["omset_merk_tanpa_target"] = _bulatkan_rp(tanpa)
        hasil["realisasi_total_semua_merk"] = _bulatkan_rp(dp["Jumlah"].sum())
    return _ok("pencapaian_target", **hasil)


# =============================================================================
# 7. ALAT 3 - TOKO TIDUR / OUTLET BERHENTI ORDER
# =============================================================================
@_tool
def toko_tidur(df: pd.DataFrame, role: str, sales_name: str, config: ToolsConfig,
               ambang_hari: Optional[int] = None, sales: Optional[str] = None,
               merk: Optional[str] = None, top_n: int = 20) -> dict:
    """Daftar outlet yang tidak order >= ambang hari (default 30) dihitung dari tanggal transaksi terakhir
    di data. Diurutkan dari omset historis terbesar (paling berharga untuk dikejar). Bisa dibatasi per merk."""
    data = siapkan_data(df, role, sales_name, config)
    ambang = int(ambang_hari) if ambang_hari is not None else config.ambang_toko_tidur_hari
    if ambang < 1:
        raise ToolError("ambang_hari minimal 1.")
    acuan = data.tanggal_acuan                      # dihitung SEBELUM filter merk/sales agar acuan tidak bergeser
    d = _outlet_valid(_filter_opsional(data.df, sales, merk))
    if d.empty:
        raise ToolError("Tidak ada transaksi pada filter tersebut.")

    g = d.groupby("Nama Outlet", observed=True)
    tabel = pd.DataFrame({
        "terakhir": g["Tanggal"].max(),
        "omset_historis": g["Jumlah"].sum(),
        "jumlah_faktur": g["No Faktur"].nunique() if "No Faktur" in d.columns else g.size(),
    })
    tabel["sales"] = _sales_terakhir_per_outlet(d)
    tabel["hari_sejak_order"] = (acuan - tabel["terakhir"].dt.normalize()).dt.days
    tidur = tabel[tabel["hari_sejak_order"] >= ambang].sort_values("omset_historis", ascending=False)

    n = _batas(top_n, config, 20)
    baris = [{
        "outlet": o, "sales": r["sales"], "order_terakhir": r["terakhir"],
        "hari_sejak_order": int(r["hari_sejak_order"]),
        "omset_historis": _bulatkan_rp(r["omset_historis"]), "omset_historis_rp": format_idr(r["omset_historis"]),
        "jumlah_faktur": int(r["jumlah_faktur"]),
    } for o, r in tidur.head(n).iterrows()]

    sebaran = {
        f"{ambang}-{ambang + 29} hari": int(((tidur.hari_sejak_order >= ambang) & (tidur.hari_sejak_order < ambang + 30)).sum()),
        f"{ambang + 30}-{ambang + 59} hari": int(((tidur.hari_sejak_order >= ambang + 30) & (tidur.hari_sejak_order < ambang + 60)).sum()),
        f"{ambang + 60}+ hari": int((tidur.hari_sejak_order >= ambang + 60).sum()),
    }
    return _ok(
        "toko_tidur",
        definisi=f"Outlet tanpa order >= {ambang} hari sampai {acuan:%Y-%m-%d} (tanggal transaksi terakhir di data).",
        tanggal_acuan=acuan, ambang_hari=ambang, filter={"sales": sales, "merk": merk},
        total_outlet_dianalisis=int(len(tabel)), jumlah_toko_tidur=int(len(tidur)),
        persen_dari_outlet=_persen(len(tidur), len(tabel)),
        total_omset_historis_toko_tidur=_bulatkan_rp(tidur["omset_historis"].sum()),
        total_omset_historis_toko_tidur_rp=format_idr(tidur["omset_historis"].sum()),
        sebaran_lama_tidur=sebaran, daftar=baris, ditampilkan=len(baris), catatan=list(data.catatan),
    )


# =============================================================================
# 8. ALAT 4 - OUTLET TURUN ORDER
# =============================================================================
@_tool
def outlet_turun_order(df: pd.DataFrame, role: str, sales_name: str, config: ToolsConfig,
                       mulai: Optional[str] = None, selesai: Optional[str] = None,
                       ambang_persen: Optional[float] = None, sales: Optional[str] = None,
                       merk: Optional[str] = None, top_n: int = 20) -> dict:
    """Outlet yang omsetnya turun >= ambang persen (default 30%) dibanding periode sebelumnya yang sama
    panjang. Outlet yang sebelumnya order tapi kini nol ditandai 'berhenti total'. Urut dari penurunan Rp terbesar."""
    data = siapkan_data(df, role, sales_name, config)
    ambang = float(ambang_persen) if ambang_persen is not None else config.ambang_turun_persen
    if not 0 < ambang <= 100:
        raise ToolError("ambang_persen harus di antara 0 dan 100.")
    awal, akhir = _tentukan_periode(data.tanggal_acuan, mulai, selesai)
    pa, pb = _periode_sebelumnya(awal, akhir)
    d = _outlet_valid(_filter_opsional(data.df, sales, merk))

    cur = d[_mask(d, awal, akhir)].groupby("Nama Outlet", observed=True)["Jumlah"].sum()
    prev = d[_mask(d, pa, pb)].groupby("Nama Outlet", observed=True)["Jumlah"].sum()
    t = pd.concat([prev.rename("sebelumnya"), cur.rename("sekarang")], axis=1).fillna(0.0)
    t = t[t["sebelumnya"] > 0]                                   # hanya outlet yang punya basis pembanding
    t["selisih"] = t["sekarang"] - t["sebelumnya"]
    t["turun_persen"] = (-t["selisih"] / t["sebelumnya"] * 100.0)
    turun = t[t["turun_persen"] >= ambang].sort_values("selisih")  # selisih paling negatif dulu

    if not turun.empty:
        sales_map = _sales_terakhir_per_outlet(d)
        turun = turun.assign(sales=sales_map.reindex(turun.index).values)

    n = _batas(top_n, config, 20)
    baris = [{
        "outlet": o, "sales": r["sales"],
        "omset_sebelumnya": _bulatkan_rp(r["sebelumnya"]), "omset_sebelumnya_rp": format_idr(r["sebelumnya"]),
        "omset_sekarang": _bulatkan_rp(r["sekarang"]), "omset_sekarang_rp": format_idr(r["sekarang"]),
        "selisih": _bulatkan_rp(r["selisih"]), "selisih_rp": format_idr(r["selisih"]),
        "turun_persen": round(float(r["turun_persen"]), 1),
        "berhenti_total": bool(r["sekarang"] <= 0),
    } for o, r in turun.head(n).iterrows()]

    catatan = list(data.catatan)
    if pa < d["Tanggal"].min().normalize():
        catatan.append("Periode pembanding sebagian/seluruhnya di luar data yang boleh diakses; "
                       "hasil bisa terlalu sedikit/tidak lengkap.")
    return _ok(
        "outlet_turun_order",
        definisi=f"Outlet dengan omset turun >= {ambang:g}% dibanding periode sebelumnya (sama panjang).",
        periode=_label_periode(awal, akhir), periode_pembanding=_label_periode(pa, pb),
        ambang_persen=ambang, filter={"sales": sales, "merk": merk},
        outlet_dengan_pembanding=int(len(t)), jumlah_outlet_turun=int(len(turun)),
        jumlah_berhenti_total=int((turun["sekarang"] <= 0).sum()) if not turun.empty else 0,
        total_penurunan_rp=format_idr(turun["selisih"].sum() if not turun.empty else 0),
        total_penurunan=_bulatkan_rp(turun["selisih"].sum() if not turun.empty else 0),
        daftar=baris, ditampilkan=len(baris), catatan=catatan,
    )


# =============================================================================
# 9. ALAT 5A - CROSS-SELL BRAND (WHITE SPACE: beli brand acuan, belum beli brand target)
# =============================================================================
@_tool
def cross_sell_brand(df: pd.DataFrame, role: str, sales_name: str, config: ToolsConfig,
                     merk_acuan: str = "", merk_target: str = "",
                     mulai_acuan: Optional[str] = None, selesai_acuan: Optional[str] = None,
                     mulai_target: Optional[str] = None, selesai_target: Optional[str] = None,
                     sales: Optional[str] = None, top_n: int = 20) -> dict:
    """Outlet yang membeli merk_acuan (pada periode acuan; default seluruh data) tetapi BELUM membeli
    merk_target (pada periode target; default bulan berjalan di data). Urut dari omset merk acuan terbesar."""
    if not merk_acuan or not merk_target:
        raise ToolError("merk_acuan dan merk_target wajib diisi.")
    data = siapkan_data(df, role, sales_name, config)
    d = _filter_opsional(data.df, sales=sales)
    m_acuan = _cocokkan(d["Merk"], merk_acuan, "Merk acuan")[0]
    m_target = _cocokkan(d["Merk"], merk_target, "Merk target")[0]
    if m_acuan == m_target:
        raise ToolError("Merk acuan dan merk target tidak boleh sama.")

    # Periode acuan: default seluruh data; periode target: default bulan data terakhir (sama seperti dashboard)
    a1 = _ke_tanggal(mulai_acuan) or d["Tanggal"].min().normalize()
    a2 = _ke_tanggal(selesai_acuan) or data.tanggal_acuan
    t2 = _ke_tanggal(selesai_target) or data.tanggal_acuan
    t1 = _ke_tanggal(mulai_target) or t2.replace(day=1)
    if a1 > a2 or t1 > t2:
        raise ToolError("Tanggal mulai tidak boleh melebihi tanggal selesai.")

    d = _outlet_valid(d)
    acuan = d[(d["Merk"].astype(str) == m_acuan) & _mask(d, a1, a2)]
    sudah_target = set(d[(d["Merk"].astype(str) == m_target) & _mask(d, t1, t2)]["Nama Outlet"])
    peluang = acuan[~acuan["Nama Outlet"].isin(sudah_target)]

    g = peluang.groupby("Nama Outlet", observed=True)
    tabel = pd.DataFrame({"omset_acuan": g["Jumlah"].sum(), "order_acuan_terakhir": g["Tanggal"].max()})
    if not tabel.empty:
        tabel["sales"] = _sales_terakhir_per_outlet(peluang)
    tabel = tabel.sort_values("omset_acuan", ascending=False)

    n = _batas(top_n, config, 20)
    baris = [{"outlet": o, "sales": r["sales"], "omset_acuan": _bulatkan_rp(r["omset_acuan"]),
              "omset_acuan_rp": format_idr(r["omset_acuan"]), "order_acuan_terakhir": r["order_acuan_terakhir"],
              "saran": f"Tawarkan {m_target}"} for o, r in tabel.head(n).iterrows()]
    total_acuan = int(acuan["Nama Outlet"].nunique())
    return _ok(
        "cross_sell_brand",
        merk_acuan=m_acuan, merk_target=m_target,
        periode_acuan=_label_periode(a1, a2), periode_target=_label_periode(t1, t2),
        outlet_beli_acuan=total_acuan, outlet_peluang=int(len(tabel)),
        persen_peluang=_persen(len(tabel), total_acuan),
        daftar=baris, ditampilkan=len(baris), catatan=list(data.catatan),
    )


# =============================================================================
# 10. ALAT 5B - CROSS-SELL SKU (aturan asosiasi per faktur, vektorisasi pandas)
# =============================================================================
@_tool
def cross_sell_sku(df: pd.DataFrame, role: str, sales_name: str, config: ToolsConfig,
                   mulai: Optional[str] = None, selesai: Optional[str] = None,
                   sales: Optional[str] = None, merk: Optional[str] = None,
                   min_confidence: float = 0.5, min_faktur: int = 5,
                   top_n: int = 20, maks_sku: int = 300, maks_item_per_faktur: int = 40) -> dict:
    """Pasangan SKU yang sering dibeli bersamaan dalam satu faktur dan rekomendasi per outlet:
    tawarkan SKU B ke outlet yang sudah beli SKU A tetapi belum pernah membeli B.
    confidence = % faktur yang memuat A yang juga memuat B. lift > 1 berarti pasangan lebih sering dari kebetulan."""
    data = siapkan_data(df, role, sales_name, config)
    if "No Faktur" not in data.df.columns:
        raise ToolError("Kolom 'No Faktur' tidak tersedia; analisis cross-sell SKU tidak dapat dihitung.")
    if not 0 < float(min_confidence) <= 1:
        raise ToolError("min_confidence harus di antara 0 dan 1 (contoh 0.5 = 50%).")
    min_faktur = max(1, int(min_faktur))

    d = _filter_opsional(data.df, sales, merk)
    if mulai or selesai:
        awal, akhir = _tentukan_periode(data.tanggal_acuan, mulai, selesai)
        d = d[_mask(d, awal, akhir)]
    else:
        awal, akhir = d["Tanggal"].min().normalize(), data.tanggal_acuan

    d = _outlet_valid(d)
    d = d[_faktur_valid(d["No Faktur"]).reindex(d.index).notna()]
    barang = d["Nama Barang"].astype(str).str.strip()
    d = d[~barang.isin(NILAI_KOSONG)]
    if d.empty:
        raise ToolError("Tidak ada transaksi dengan faktur & nama barang valid pada filter tersebut.")

    trx = pd.DataFrame({"faktur": d["No Faktur"].astype(str).str.strip(),
                        "item": d["Nama Barang"].astype(str).str.strip()}).drop_duplicates()
    n_faktur = trx["faktur"].nunique()
    cnt_item = trx.groupby("item").size()
    item_ok = cnt_item[cnt_item >= min_faktur].nlargest(int(maks_sku)).index
    trx = trx[trx["item"].isin(item_ok)]
    ukuran = trx.groupby("faktur")["item"].transform("size")
    trx = trx[(ukuran > 1) & (ukuran <= int(maks_item_per_faktur))]

    kosong = {"aturan": [], "rekomendasi_outlet": []}
    if trx.empty:
        return _ok("cross_sell_sku", periode=_label_periode(awal, akhir), jumlah_faktur=int(n_faktur),
                   jumlah_aturan=0, jumlah_outlet_direkomendasikan=0, **kosong,
                   catatan=list(data.catatan) + ["Tidak ada pasangan SKU yang memenuhi batas minimal faktur."])

    pasang = trx.merge(trx.rename(columns={"item": "B"}), on="faktur")
    pasang = pasang[pasang["item"] != pasang["B"]]
    aturan = pasang.groupby(["item", "B"]).size().rename("faktur_bersama").reset_index().rename(columns={"item": "A"})
    aturan["confidence"] = aturan["faktur_bersama"] / aturan["A"].map(cnt_item)
    aturan["support"] = aturan["faktur_bersama"] / n_faktur
    aturan["lift"] = aturan["confidence"] / (aturan["B"].map(cnt_item) / n_faktur)
    aturan = aturan[(aturan["faktur_bersama"] >= min_faktur) & (aturan["confidence"] >= float(min_confidence))]
    aturan = aturan.sort_values(["confidence", "lift", "faktur_bersama"], ascending=False)

    n = _batas(top_n, config, 20)
    daftar_aturan = [{
        "jika_beli": r.A, "tawarkan": r.B, "faktur_bersama": int(r.faktur_bersama),
        "confidence_persen": round(r.confidence * 100, 1), "lift": round(float(r.lift), 2),
        "support_persen": round(r.support * 100, 2),
    } for r in aturan.head(n).itertuples()]

    # --- rekomendasi per outlet: sudah beli A, belum pernah beli B (dalam data yang dianalisis) ---
    oi = pd.DataFrame({"outlet": d["Nama Outlet"].astype(str), "item": d["Nama Barang"].astype(str).str.strip()}).drop_duplicates()
    kand = oi.merge(aturan.rename(columns={"A": "item"}), on="item")
    kand = kand.merge(oi.rename(columns={"item": "B"}).assign(sudah=1), on=["outlet", "B"], how="left")
    kand = kand[kand["sudah"].isna()].sort_values(["confidence", "lift"], ascending=False)
    terbaik = kand.drop_duplicates("outlet")

    rekom = []
    if not terbaik.empty:
        omset_outlet = d.groupby(d["Nama Outlet"].astype(str))["Jumlah"].sum()
        sales_map = _sales_terakhir_per_outlet(d)
        terbaik = terbaik.assign(omset=terbaik["outlet"].map(omset_outlet),
                                 sales=terbaik["outlet"].map(sales_map))
        terbaik = terbaik.sort_values(["omset"], ascending=False)       # outlet besar dulu
        rekom = [{
            "outlet": r.outlet, "sales": r.sales, "tawarkan": r.B, "karena_sudah_beli": r.item,
            "confidence_persen": round(r.confidence * 100, 1), "lift": round(float(r.lift), 2),
            "omset_outlet_rp": format_idr(r.omset),
        } for r in terbaik.head(n).itertuples()]

    return _ok(
        "cross_sell_sku", periode=_label_periode(awal, akhir), filter={"sales": sales, "merk": merk},
        jumlah_faktur=int(n_faktur), jumlah_aturan=int(len(aturan)),
        jumlah_outlet_direkomendasikan=int(len(terbaik)),
        parameter={"min_confidence": float(min_confidence), "min_faktur": min_faktur, "maks_sku": int(maks_sku)},
        aturan=daftar_aturan, rekomendasi_outlet=rekom, catatan=list(data.catatan),
    )


# =============================================================================
# 11. ALAT 6 - TOP PRODUK (Pareto 80/20 seperti tab Top Produk)
# =============================================================================
@_tool
def top_produk(df: pd.DataFrame, role: str, sales_name: str, config: ToolsConfig,
               mulai: Optional[str] = None, selesai: Optional[str] = None,
               sales: Optional[str] = None, merk: Optional[str] = None, outlet: Optional[str] = None,
               urut_berdasarkan: str = "omset", top_n: int = 10) -> dict:
    """Produk teratas menurut omset (atau qty) beserta kontribusi %, kumulatif %, dan jumlah produk
    yang membentuk 80% omset (aturan Pareto yang sama dengan tab Top Produk)."""
    data = siapkan_data(df, role, sales_name, config)
    awal, akhir = _tentukan_periode(data.tanggal_acuan, mulai, selesai)
    d = _filter_opsional(data.df, sales, merk, outlet)
    d = d[_mask(d, awal, akhir)]
    d = d[~d["Nama Barang"].astype(str).str.strip().isin(NILAI_KOSONG)]

    kunci = str(urut_berdasarkan).strip().lower()
    if kunci not in ("omset", "qty"):
        raise ToolError("urut_berdasarkan harus 'omset' atau 'qty'.")
    if kunci == "qty" and "Qty" not in d.columns:
        raise ToolError("Kolom 'Qty' tidak tersedia.")
    if d.empty:
        return _ok("top_produk", periode=_label_periode(awal, akhir), jumlah_sku=0, daftar=[],
                   catatan=list(data.catatan) + ["Tidak ada transaksi pada periode/filter tersebut."])

    g = d.groupby("Nama Barang", observed=True)
    tabel = pd.DataFrame({
        "omset": g["Jumlah"].sum(),
        "qty": g["Qty"].sum() if "Qty" in d.columns else np.nan,
        "faktur": g["No Faktur"].nunique() if "No Faktur" in d.columns else g.size(),
        "outlet": g["Nama Outlet"].nunique(),
    }).sort_values("omset" if kunci == "omset" else "qty", ascending=False)

    total_omset = float(tabel["omset"].sum())
    tabel["kontribusi"] = tabel["omset"] / total_omset * 100.0 if total_omset else 0.0
    tabel["kumulatif"] = tabel["kontribusi"].cumsum()
    n80 = int((tabel["kumulatif"] <= 80).sum()) or 1               # aturan dashboard: kumulatif <= 80%

    n = _batas(top_n, config, 10)
    baris = [{
        "peringkat": i, "produk": nama,
        "omset": _bulatkan_rp(r["omset"]), "omset_rp": format_idr(r["omset"]),
        "qty": None if pd.isna(r["qty"]) else float(r["qty"]),
        "jumlah_faktur": int(r["faktur"]), "jumlah_outlet": int(r["outlet"]),
        "kontribusi_persen": round(float(r["kontribusi"]), 2), "kumulatif_persen": round(float(r["kumulatif"]), 2),
    } for i, (nama, r) in enumerate(tabel.head(n).iterrows(), start=1)]

    return _ok(
        "top_produk", periode=_label_periode(awal, akhir), filter={"sales": sales, "merk": merk, "outlet": outlet},
        urut_berdasarkan=kunci, jumlah_sku=int(len(tabel)), produk_pembentuk_80_persen=n80,
        total_omset=_bulatkan_rp(total_omset), total_omset_rp=format_idr(total_omset),
        daftar=baris, ditampilkan=len(baris), catatan=list(data.catatan),
    )


# =============================================================================
# 11b. KONTEKS DATA (bukan alat; dipakai Fase 2 untuk menyusun prompt sistem)
# =============================================================================
def konteks_data(df: pd.DataFrame, role: str, sales_name: str, config: ToolsConfig,
                 hari_ini: Optional[dt.date] = None) -> dict:
    """Gambaran data yang BOLEH diakses user (level akses, rentang tanggal, daftar merk & sales).
    Dipakai agen AI agar tahu nama merk/sales yang valid. Raise ToolError bila tidak ada data."""
    data = siapkan_data(df, role, sales_name, config, hari_ini)
    d = data.df
    daftar_sales = [data.akses.nama] if data.akses.level == "sales" else sorted(
        s for s in d["Penjualan"].astype(str).unique() if s not in NILAI_KOSONG and s != "Non-Sales")
    return _py({
        "level": data.akses.level,
        "tanggal_awal": d["Tanggal"].min().normalize(),
        "tanggal_akhir": data.tanggal_acuan,
        "merk": sorted(m for m in d["Merk"].astype(str).unique() if m not in NILAI_KOSONG),
        "sales": daftar_sales,
        "catatan": list(data.catatan),
    })


# =============================================================================
# 12. PINTU MASUK UNTUK AI (Fase 2): whitelist + injeksi identitas oleh program
# =============================================================================
_PARAMETER_DISUNTIK = {"df", "role", "sales_name", "config"}


def daftar_tool() -> list[dict]:
    """Deskripsi singkat tiap alat (nama, fungsi, parameter yang boleh diisi AI) -> bahan prompt/function-calling."""
    hasil = []
    for nama, fn in TOOL_REGISTRY.items():
        sig = inspect.signature(fn)
        params = {p: (None if v.default is inspect.Parameter.empty else v.default)
                  for p, v in sig.parameters.items() if p not in _PARAMETER_DISUNTIK}
        hasil.append({"nama": nama, "deskripsi": " ".join((fn.__doc__ or "").split()), "parameter": params})
    return hasil


def jalankan_tool(nama: str, argumen: Optional[dict], *, df: pd.DataFrame, role: str,
                  sales_name: str, config: ToolsConfig) -> dict:
    """Jalankan alat atas permintaan AI. Hanya alat terdaftar; argumen asing ditolak; df/role/sales_name
    SELALU dari program (argumen AI dengan nama itu diabaikan) sehingga AI tidak bisa menaikkan hak akses."""
    fn = TOOL_REGISTRY.get(nama)
    if fn is None:
        return _gagal(nama, f"Alat '{nama}' tidak dikenal. Alat tersedia: {sorted(TOOL_REGISTRY)}")
    argumen = {k: v for k, v in (argumen or {}).items() if k not in _PARAMETER_DISUNTIK}
    boleh = set(inspect.signature(fn).parameters) - _PARAMETER_DISUNTIK
    asing = sorted(set(argumen) - boleh)
    if asing:
        return _gagal(nama, f"Parameter tidak dikenal: {asing}. Parameter yang diizinkan: {sorted(boleh)}")
    try:
        return fn(df, role, sales_name, config, **argumen)
    except Exception as e:  # noqa: BLE001 - jangan biarkan error mentah bocor ke AI/UI
        return _gagal(nama, f"Terjadi kesalahan saat menghitung ({type(e).__name__}). Periksa argumen atau data.")
