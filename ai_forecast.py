"""
ai_forecast.py - FASE 6B: alat prediksi omset untuk modul "Asisten AI".

Prinsip: "Program yang menghitung, AI yang menjelaskan." Semua angka dihitung di sini (numpy/pandas + statsmodels
ETS) dan dikembalikan sebagai dict JSON-aman. Setiap prakiraan WAJIB membawa:
  * metode (model ETS terpilih lewat AICc pada tiap data latih),
  * periode backtest rolling-origin + jumlah lipatan + horizon,
  * ukuran galat sMAPE dan MAPE (model, baseline naif, baseline naif musiman),
  * interval ketidakpastian (tingkat 80 persen) + cakupan empirisnya pada backtest,
  * pembanding baseline naif musiman. Bila model TIDAK mengalahkan baseline, hasil melaporkannya apa adanya
    (mengalahkan_baseline_naif_musiman=False, rekomendasi_acuan="baseline_naif_musiman").
  * penolakan (ok=False, kode="histori_pendek") bila histori mingguan tidak cukup untuk backtest yang bermakna.

Granularitas: mingguan (Senin-Minggu) per Merk (atau total omset yang boleh diakses user). Data yang dipakai adalah
data yang SUDAH disaring ai_tools.siapkan_data (TLS + RLS), jadi role non-direktur hanya meramal dari histori tahun
berjalan dan hasilnya diberi catatan.

Kalimat "musiman": bila histori belum cukup untuk musiman tahunan (52 minggu), pembanding musiman memakai siklus 4
minggu (pola gajian/bulanan) dan hasil diberi label musiman_tahunan_teruji=False.

statsmodels diimpor malas (hanya saat dipakai). File ini TIDAK meng-import streamlit.
"""
from __future__ import annotations

import copy
import hashlib
import threading
import time
import warnings
from dataclasses import dataclass
from statistics import NormalDist
from typing import Callable, Optional

import numpy as np
import pandas as pd

import ai_tools
from ai_tools import ToolError, _gagal, _ok, format_idr


# =============================================================================
# 1. KONFIGURASI & GALAT
# =============================================================================
@dataclass(frozen=True)
class ForecastConfig:
    min_minggu: int = 26               # histori mingguan minimum untuk meramal sama sekali
    horizon_default: int = 4
    horizon_maks: int = 8
    min_latih: int = 18                # panjang data latih minimum pada lipatan backtest pertama
    langkah: int = 2                   # jarak antar titik asal (origin) backtest, dalam minggu
    maks_lipatan: int = 8
    min_lipatan: int = 3
    tingkat_interval: float = 0.80
    musiman_tahunan: int = 52
    musiman_pendek: int = 4
    min_latih_musiman_tahunan: int = 104   # ETS musiman 52 hanya dipasang bila data latih >= 2 siklus penuh
    hari_minggu_lengkap: int = 5       # minggu terakhir dianggap lengkap bila data sampai >= Sabtu (weekday 5)
    maks_minggu_kosong_persen: float = 40.0
    riwayat_ditampilkan: int = 26
    ttl_cache_detik: int = 6 * 3600


FORECAST_CONFIG = ForecastConfig()


class ForecastDitolak(ToolError):
    """Penolakan terukur (histori pendek, deret jarang, dst.) -> dikembalikan sebagai ok=False + kode."""

    def __init__(self, pesan: str, kode: str, **extra):
        super().__init__(pesan)
        self.kode, self.extra = kode, extra


class ForecastTidakTersedia(ToolError):
    """Pustaka forecasting belum terpasang."""


MesinFn = Callable[[np.ndarray, int, int, float], dict]


# =============================================================================
# 2. DERET MINGGUAN
# =============================================================================
def deret_mingguan(d: pd.DataFrame, tanggal_acuan: pd.Timestamp, cfg: Optional[ForecastConfig] = None) -> pd.Series:
    """Omset per minggu Senin-Minggu (index = tanggal Senin), tanpa celah. Minggu terakhir yang belum lengkap
    dibuang, dan minggu kosong di awal dipangkas."""
    cfg = cfg or FORECAST_CONFIG
    if d is None or d.empty:
        return pd.Series(dtype=float)
    t = pd.to_datetime(d["Tanggal"]).dt.normalize()
    senin = t - pd.to_timedelta(t.dt.weekday, unit="D")
    s = d["Jumlah"].astype(float).groupby(senin).sum()
    acuan = pd.Timestamp(tanggal_acuan).normalize()
    senin_acuan = acuan - pd.Timedelta(days=int(acuan.weekday()))
    if acuan.weekday() < cfg.hari_minggu_lengkap:
        s = s[s.index < senin_acuan]
    if s.empty:
        return pd.Series(dtype=float)
    s = s.reindex(pd.date_range(s.index.min(), s.index.max(), freq="7D"), fill_value=0.0)
    positif = s.to_numpy() > 0
    if not positif.any():
        return pd.Series(dtype=float)
    return s.iloc[int(positif.argmax()):]


# =============================================================================
# 3. MESIN: baseline (numpy murni) + ETS (statsmodels)
# =============================================================================
def baseline_naif(y: np.ndarray, h: int) -> np.ndarray:
    return np.full(h, float(y[-1]))


def baseline_naif_musiman(y: np.ndarray, h: int, m: int) -> np.ndarray:
    n = len(y)
    return np.array([float(y[n - m + (i % m)]) for i in range(h)])


def _nama_ets(trend: Optional[str], damped: bool, seasonal: Optional[str]) -> str:
    t = "N" if trend is None else ("Ad" if damped else "A")
    return f"ETS(A,{t},{'N' if seasonal is None else 'A'})"


def mesin_ets_statsmodels(y: np.ndarray, h: int, m: int, level: float) -> dict:
    """Pasang beberapa kandidat ETS pada `y`, pilih AICc terkecil, kembalikan prakiraan + interval.
    m = periode musiman (0 = tanpa musiman). Kandidat musiman hanya dicoba bila len(y) >= 2*m."""
    try:
        from statsmodels.tsa.exponential_smoothing.ets import ETSModel
    except ImportError:
        raise ForecastTidakTersedia("Pustaka statsmodels belum terpasang di server (pip install statsmodels).") from None
    n = len(y)
    kandidat = [(None, False, None), ("add", True, None)]
    if m and m >= 2 and n >= 2 * m:
        kandidat += [(None, False, "add"), ("add", True, "add")]
    terbaik = None
    for trend, damped, seas in kandidat:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model = ETSModel(y, error="add", trend=trend, damped_trend=damped, seasonal=seas,
                                 seasonal_periods=(m if seas else None),
                                 initialization_method=("heuristic" if seas else "estimated"))
                res = model.fit(disp=False, maxiter=300)
            aicc = float(res.aicc)
            if np.isfinite(aicc) and (terbaik is None or aicc < terbaik[0]):
                terbaik = (aicc, res, _nama_ets(trend, damped, seas))
        except Exception:  # noqa: BLE001 - satu kandidat gagal tidak membatalkan yang lain
            continue
    if terbaik is None:
        raise ValueError("Semua kandidat ETS gagal dipasang.")
    _, res, nama = terbaik
    mean = np.asarray(res.forecast(h), dtype=float)
    try:
        sf = res.get_prediction(start=n, end=n + h - 1).summary_frame(alpha=1.0 - level)
        lower, upper = sf["pi_lower"].to_numpy(dtype=float), sf["pi_upper"].to_numpy(dtype=float)
        if not (np.isfinite(lower).all() and np.isfinite(upper).all()):
            raise ValueError("interval tidak terhingga")
        metode = "analitik ETS"
    except Exception:  # noqa: BLE001 - cadangan: aproksimasi dari residu
        sigma = float(np.nanstd(np.asarray(res.resid, dtype=float), ddof=1))
        z = NormalDist().inv_cdf(0.5 + level / 2.0)
        setengah = z * sigma * np.sqrt(np.arange(1, h + 1))
        lower, upper, metode = mean - setengah, mean + setengah, "aproksimasi residu (sigma*akar(langkah))"
    return {"mean": mean, "lower": lower, "upper": upper, "nama": nama, "interval_metode": metode}


_MESIN_AKTIF: Optional[MesinFn] = None


def atur_mesin(mesin: Optional[MesinFn]) -> None:
    """Ganti mesin (untuk pengujian). None = kembali ke ETS statsmodels."""
    global _MESIN_AKTIF
    _MESIN_AKTIF = mesin


def _mesin_aktif() -> MesinFn:
    return _MESIN_AKTIF or mesin_ets_statsmodels


# =============================================================================
# 4. METRIK
# =============================================================================
def smape(a: np.ndarray, f: np.ndarray) -> float:
    a, f = np.asarray(a, float), np.asarray(f, float)
    pembagi = np.abs(a) + np.abs(f)
    nilai = np.where(pembagi == 0, 0.0, 2.0 * np.abs(a - f) / np.where(pembagi == 0, 1.0, pembagi))
    return float(np.mean(nilai) * 100.0)


def mape(a: np.ndarray, f: np.ndarray) -> Optional[float]:
    a, f = np.asarray(a, float), np.asarray(f, float)
    ok = a != 0
    if not ok.any():
        return None
    return float(np.mean(np.abs(a[ok] - f[ok]) / np.abs(a[ok])) * 100.0)


# =============================================================================
# 5. BACKTEST ROLLING-ORIGIN
# =============================================================================
def _asal_lipatan(n: int, h: int, langkah: int, min_latih: int, maks: int) -> list[int]:
    """Titik asal t: latih = y[:t], uji = y[t:t+h]. Mundur dari ujung deret dengan jarak `langkah`."""
    hasil, t = [], n - h
    while t >= min_latih and len(hasil) < maks:
        hasil.append(t)
        t -= langkah
    return sorted(hasil)


def _pilih_musiman(n: int, h: int, cfg: ForecastConfig) -> Optional[tuple[int, list[int]]]:
    """Periode musiman terpanjang yang masih menyisakan >= min_lipatan lipatan dengan data latih >= periode itu."""
    for m in (cfg.musiman_tahunan, cfg.musiman_pendek):
        asal = _asal_lipatan(n, h, cfg.langkah, max(cfg.min_latih, m), cfg.maks_lipatan)
        if len(asal) >= cfg.min_lipatan:
            return m, asal
    return None


def _m_ets(n_latih: int, m: int, cfg: ForecastConfig) -> int:
    if m == cfg.musiman_tahunan:
        return m if n_latih >= cfg.min_latih_musiman_tahunan else 0
    return m if n_latih >= 2 * m else 0


def _kutip(x: float, d: int = 2) -> Optional[float]:
    return None if x is None or not np.isfinite(x) else round(float(x), d)


def hitung_prediksi(y: pd.Series, h: int, cfg: Optional[ForecastConfig] = None, mesin: Optional[MesinFn] = None) -> dict:
    """Inti perhitungan. y: omset mingguan (index = Senin, berurutan). Melempar ForecastDitolak bila ditolak."""
    cfg = cfg or FORECAST_CONFIG
    mesin = mesin or _mesin_aktif()
    n = int(len(y))
    butuh = max(cfg.min_minggu, cfg.min_latih + h + (cfg.min_lipatan - 1) * cfg.langkah)
    if n < butuh:
        raise ForecastDitolak(
            f"Histori omset mingguan baru {n} minggu; untuk horizon {h} minggu dibutuhkan minimal {butuh} minggu agar "
            f"backtest bermakna. Prakiraan ditolak daripada menebak.", "histori_pendek", n_minggu=n, minimum_minggu=butuh)
    v = y.to_numpy(dtype=float)
    persen_kosong = float((v <= 0).mean() * 100.0)
    if persen_kosong > cfg.maks_minggu_kosong_persen:
        raise ForecastDitolak("Terlalu banyak minggu tanpa omset (deret putus-putus); prakiraan tidak dapat diandalkan.",
                              "deret_jarang", persen_minggu_tanpa_omset=_kutip(persen_kosong))
    pilihan = _pilih_musiman(n, h, cfg)
    if pilihan is None:                       # tidak terjadi bila n >= butuh, dijaga untuk keamanan
        raise ForecastDitolak("Histori tidak cukup untuk backtest rolling-origin.", "histori_pendek", n_minggu=n,
                              minimum_minggu=butuh)
    m_base, asal = pilihan
    level = cfg.tingkat_interval

    aktual, p_model, p_naif, p_musim, di_dalam, nama_lipatan = [], [], [], [], [], []
    for t in asal:
        latih, uji = v[:t], v[t:t + h]
        try:
            out = mesin(latih, h, _m_ets(t, m_base, cfg), level)
        except ForecastTidakTersedia:
            raise
        except Exception:  # noqa: BLE001 - lipatan gagal dilewati, tidak diisi dengan angka pengganti
            continue
        mean = np.asarray(out["mean"], float)
        aktual.append(uji)
        p_model.append(np.maximum(mean, 0.0))
        p_naif.append(baseline_naif(latih, h))
        p_musim.append(baseline_naif_musiman(latih, h, m_base))
        di_dalam.append((uji >= np.asarray(out["lower"], float)) & (uji <= np.asarray(out["upper"], float)))
        nama_lipatan.append(out.get("nama", "model"))
    if len(aktual) < cfg.min_lipatan:
        raise ForecastDitolak("Model gagal dipasang pada sebagian besar lipatan backtest; prakiraan dibatalkan.",
                              "model_gagal", lipatan_berhasil=len(aktual))

    A, M, N, S = (np.concatenate(x) for x in (aktual, p_model, p_naif, p_musim))
    cakupan = float(np.concatenate(di_dalam).mean() * 100.0)
    sm_model, sm_naif, sm_musim = smape(A, M), smape(A, N), smape(A, S)
    mp_model, mp_naif, mp_musim = mape(A, M), mape(A, N), mape(A, S)
    menang_musim, menang_naif = sm_model < sm_musim, sm_model < sm_naif

    # prakiraan akhir: pasang ulang pada SELURUH data
    try:
        akhir = mesin(v, h, _m_ets(n, m_base, cfg), level)
    except ForecastTidakTersedia:
        raise
    except Exception:  # noqa: BLE001
        raise ForecastDitolak("Model gagal dipasang pada seluruh data; prakiraan dibatalkan.", "model_gagal") from None
    mean_f = np.maximum(np.asarray(akhir["mean"], float), 0.0)
    low_f = np.maximum(np.asarray(akhir["lower"], float), 0.0)
    up_f = np.maximum(np.asarray(akhir["upper"], float), 0.0)
    base_f = baseline_naif_musiman(v, h, m_base)
    minggu_depan = [y.index[-1] + pd.Timedelta(days=7 * (i + 1)) for i in range(h)]

    def _rp(x: float) -> int:
        return int(round(float(x)))

    prakiraan = [{"minggu_mulai": w, "titik": _rp(mean_f[i]), "titik_rp": format_idr(mean_f[i]),
                  "bawah": _rp(low_f[i]), "bawah_rp": format_idr(low_f[i]),
                  "atas": _rp(up_f[i]), "atas_rp": format_idr(up_f[i])} for i, w in enumerate(minggu_depan)]
    prak_base = [{"minggu_mulai": w, "titik": _rp(base_f[i]), "titik_rp": format_idr(base_f[i])}
                 for i, w in enumerate(minggu_depan)]

    sm_m, sm_s, sm_n = _kutip(sm_model), _kutip(sm_musim), _kutip(sm_naif)
    if menang_musim:
        kesimpulan = (f"Model {akhir.get('nama', 'model')} lebih akurat daripada baseline naif musiman pada backtest "
                      f"(sMAPE {sm_m:.2f} persen vs {sm_s:.2f} persen). Bukti terbatas pada {len(aktual)} lipatan.")
    else:
        kesimpulan = (f"Model {akhir.get('nama', 'model')} TIDAK mengalahkan baseline naif musiman pada backtest "
                      f"(sMAPE {sm_m:.2f} persen vs {sm_s:.2f} persen). Gunakan baseline sebagai acuan; "
                      f"prakiraan model tidak boleh disajikan sebagai lebih baik.")
    catatan = []
    if m_base != cfg.musiman_tahunan:
        catatan.append("Histori belum cukup untuk menguji musiman tahunan (Lebaran, Harbolnas, dll.). 'Musiman' di sini "
                       "adalah siklus 4 minggu (pola gajian/bulanan); lonjakan tahunan tidak tertangkap.")
    if cakupan < level * 100.0 - 15.0:
        catatan.append(f"Interval cenderung terlalu sempit: pada backtest hanya {cakupan:.2f} persen nilai aktual masuk "
                       f"interval {level * 100:.0f} persen.")
    if len(aktual) < 5 or abs(sm_model - sm_musim) < 0.1 * max(sm_musim, 1e-9):
        catatan.append("Selisih akurasi kecil dan/atau lipatan backtest sedikit; perbedaan model vs baseline bisa kebetulan.")
    catatan.append("Prakiraan adalah proyeksi statistik dari pola historis, bukan target atau janji. Peristiwa di luar pola "
                   "(promo besar, stok kosong, perubahan harga) tidak diperhitungkan.")

    ts = y.index
    return {
        "satuan": "omset per minggu (Senin-Minggu), Rupiah",
        "data": {"n_minggu": n, "minggu_pertama": ts[0], "minggu_terakhir": ts[-1], "jumlah_minggu_tanpa_omset": int((v <= 0).sum())},
        "metode": {
            "model": akhir.get("nama", "model"),
            "pemilihan": "AICc terkecil di antara kandidat ETS, diulang pada setiap data latih backtest",
            "interval_tingkat_persen": int(round(level * 100)),
            "interval_metode": akhir.get("interval_metode", "tidak diketahui"),
            "periode_musiman_baseline_minggu": int(m_base),
            "musiman_ets_minggu": int(_m_ets(n, m_base, cfg)),
            "musiman_tahunan_teruji": bool(m_base == cfg.musiman_tahunan),
        },
        "backtest": {
            "jenis": "rolling-origin (latih diperpanjang, uji = horizon minggu berikutnya)",
            "horizon_minggu": int(h), "langkah_minggu": int(cfg.langkah), "n_lipatan": int(len(aktual)),
            "latih_minimum_minggu": int(asal[0]),
            "periode_uji_awal": ts[asal[0]], "periode_uji_akhir": ts[asal[-1] + h - 1],
            "smape_model_persen": sm_m, "smape_baseline_naif_persen": sm_n, "smape_baseline_naif_musiman_persen": sm_s,
            "mape_model_persen": _kutip(mp_model), "mape_baseline_naif_persen": _kutip(mp_naif),
            "mape_baseline_naif_musiman_persen": _kutip(mp_musim),
            "cakupan_interval_persen": _kutip(cakupan),
            "kekuatan_bukti": "terbatas",
        },
        "mengalahkan_baseline_naif_musiman": bool(menang_musim),
        "mengalahkan_baseline_naif": bool(menang_naif),
        "rekomendasi_acuan": "model" if menang_musim else "baseline_naif_musiman",
        "kesimpulan": kesimpulan,
        "prakiraan": prakiraan,
        "total_horizon_titik": _rp(mean_f.sum()), "total_horizon_titik_rp": format_idr(mean_f.sum()),
        "prakiraan_baseline_naif_musiman": prak_base,
        "riwayat_mingguan": [{"minggu": w, "omset": _rp(x)} for w, x in zip(ts[-cfg.riwayat_ditampilkan:], v[-cfg.riwayat_ditampilkan:])],
        "catatan": catatan,
    }


# =============================================================================
# 6. CACHE HASIL (kunci = isi deret + parameter -> tidak bisa bocor antar user yang datanya berbeda)
# =============================================================================
_CACHE: dict = {}
_KUNCI_CACHE = threading.Lock()


def _kunci(y: pd.Series, h: int, cfg: ForecastConfig, mesin: MesinFn) -> str:
    ringkas = hashlib.sha256(np.round(y.to_numpy(dtype=float), 2).tobytes()).hexdigest()
    return f"{ringkas}|{y.index[-1]:%Y%m%d}|{h}|{cfg.tingkat_interval}|{id(mesin)}"


def bersihkan_cache() -> None:
    with _KUNCI_CACHE:
        _CACHE.clear()


def _horizon(h: Optional[int], cfg: ForecastConfig) -> int:
    try:
        h = int(h) if h is not None else cfg.horizon_default
    except (TypeError, ValueError):
        h = cfg.horizon_default
    return max(1, min(h, cfg.horizon_maks))


# =============================================================================
# 7. ALAT: prediksi_omset  (terdaftar ke ai_tools.TOOL_REGISTRY)
# =============================================================================
@ai_tools._tool
def prediksi_omset(df: pd.DataFrame, role: str, sales_name: str, config: ai_tools.ToolsConfig,
                   merk: Optional[str] = None, horizon_minggu: Optional[int] = None) -> dict:
    """Prediksi omset mingguan beberapa minggu ke depan (maks 8) untuk satu Merk atau seluruh omset yang boleh
    diakses, memakai ETS (statsmodels). Selalu menyertakan metode, periode backtest rolling-origin, galat sMAPE/MAPE,
    interval ketidakpastian 80 persen, dan pembanding baseline naif musiman; bila model tidak mengalahkan baseline,
    hal itu dilaporkan. Menolak bila histori mingguan terlalu pendek. Prakiraan bukan target atau janji."""
    cfg = FORECAST_CONFIG
    h = _horizon(horizon_minggu, cfg)
    data = ai_tools.siapkan_data(df, role, sales_name, config)                  # TLS + RLS
    d, cakupan = data.df, "semua omset yang dapat diakses user ini"
    if merk:
        nama = ai_tools._cocokkan(d["Merk"], merk, "Merk")[0]
        d, cakupan = d[d["Merk"].astype(str) == nama], f"Merk {nama}"
    y = deret_mingguan(d, data.tanggal_acuan, cfg)
    if y.empty:
        return _gagal("prediksi_omset", "Tidak ada omset mingguan yang dapat dipakai untuk diprediksi.", kode="histori_pendek",
                      n_minggu=0, minimum_minggu=cfg.min_minggu)
    mesin = _mesin_aktif()
    kunci = _kunci(y, h, cfg, mesin)
    with _KUNCI_CACHE:
        item = _CACHE.get(kunci)
    if item and time.time() - item[0] < cfg.ttl_cache_detik:
        return copy.deepcopy(item[1])
    try:
        hasil = hitung_prediksi(y, h, cfg, mesin)
    except ForecastDitolak as e:
        return _gagal("prediksi_omset", str(e), kode=e.kode, cakupan=cakupan, **e.extra)
    catatan = list(data.catatan) + hasil.pop("catatan")
    if data.akses.batas_waktu:
        catatan.append("Role ini hanya melihat histori tahun berjalan, sehingga musiman tahunan tidak bisa diuji.")
    out = _ok("prediksi_omset", cakupan=cakupan, tanggal_acuan_data=data.tanggal_acuan, **hasil, catatan=catatan)
    with _KUNCI_CACHE:
        if len(_CACHE) >= 64:
            _CACHE.pop(min(_CACHE, key=lambda k: _CACHE[k][0]))
        _CACHE[kunci] = (time.time(), out)
    return copy.deepcopy(out)


PARAM_DESC_FORECAST = {
    "horizon_minggu": "Jumlah minggu ke depan yang diprediksi (1-8, default 4).",
}
