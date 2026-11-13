"""
trends_fallback.py - pengambilan Google Trends (pytrends) yang TIDAK PERNAH melempar galat ke UI.

  * ambil_tren_google()   : pytrends + retry backoff + cache sukses/gagal -> HasilTren (df selalu valid)
  * df_kosong()           : DataFrame kosong dengan skema sama seperti tabel search_trend_series
  * SumberDenganFallback  : adaptor untuk ai_market_tools.daftarkan_sumber (Supabase dulu, pytrends cadangan)
  * pasang_fallback()     : pasang adaptor (idempoten, aman dipanggil di tiap rerun Streamlit)

Konvensi proyek dijaga: teks galat mentah tidak pernah dilog/ditampilkan, hanya nama tipe galat.
Streamlit tidak di-import di level modul.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional

import pandas as pd

_log = logging.getLogger("ai_trends")

KOLOM_SERI = ["series_date", "keyword", "brand", "value"]  # skema search_trend_series
PETA_DEFAULT = {"Wardah": "wardah", "Emina": "emina", "Make Over": "make over", "Skintific": "skintific"}
PESAN_TRENDS_GAGAL = ("Data tren pencarian Google sedang dibatasi atau tidak dapat diakses. "
                      "Bagian lain tetap tersedia; coba lagi beberapa menit lagi.")


@dataclass(frozen=True)
class TrendsConfig:
    geo: str = "ID"
    timeframe: str = "today 3-m"      # <= 9 bulan -> data HARIAN (sesuai skema seri harian)
    hl: str = "id-ID"
    tz: int = -420                    # WIB (UTC+7); konvensi pytrends: menit, tanda terbalik
    timeout: tuple = (5, 10)
    maks_percobaan: int = 3
    jeda_awal_detik: float = 2.0      # 2s, 4s, ... + jitter
    ttl_sukses_detik: int = 6 * 3600
    ttl_gagal_detik: int = 300        # cache negatif: jangan menggedor Google saat IP sedang diblokir
    maks_kata_kunci: int = 5          # batas Google Trends per permintaan (skala relatif dibagi bersama)


TRENDS_CONFIG = TrendsConfig()


@dataclass(frozen=True)
class HasilTren:
    df: pd.DataFrame
    ok: bool
    pesan: str = ""


def df_kosong() -> pd.DataFrame:
    return pd.DataFrame({"series_date": pd.Series(dtype="datetime64[ns]"), "keyword": pd.Series(dtype="object"),
                         "brand": pd.Series(dtype="object"), "value": pd.Series(dtype="float64")})


# ----------------------------------------------------------------------------- cache TTL (sukses & gagal)
_cache: dict[Any, tuple[float, HasilTren]] = {}
_kunci_cache = threading.Lock()


def _cache_ambil(k: Any) -> Optional[HasilTren]:
    with _kunci_cache:
        item = _cache.get(k)
        if item and time.time() < item[0]:
            return item[1]
        _cache.pop(k, None)
        return None


def _cache_simpan(k: Any, hasil: HasilTren, ttl: int) -> None:
    with _kunci_cache:
        _cache[k] = (time.time() + ttl, hasil)


# ----------------------------------------------------------------------------- inti
def _ke_skema(mentah: Optional[pd.DataFrame], peta: Mapping[str, str]) -> pd.DataFrame:
    if mentah is None or mentah.empty:
        return df_kosong()
    d = mentah
    if "isPartial" in d.columns:                      # periode terakhir yang belum selesai: buang
        d = d[~d["isPartial"].astype(bool)]
    d = d.drop(columns=["isPartial"], errors="ignore")
    if d.empty:
        return df_kosong()
    d.index.name = "series_date"
    panjang = d.reset_index().melt(id_vars="series_date", var_name="keyword", value_name="value")
    balik = {kw: br for br, kw in peta.items()}
    panjang["brand"] = panjang["keyword"].map(balik)
    panjang["series_date"] = pd.to_datetime(panjang["series_date"])
    panjang["value"] = pd.to_numeric(panjang["value"], errors="coerce")
    hasil = panjang.dropna(subset=["value", "brand"])[KOLOM_SERI].reset_index(drop=True)
    return hasil if len(hasil) else df_kosong()


def _satu_kali(peta: Mapping[str, str], cfg: TrendsConfig) -> pd.DataFrame:
    from pytrends.request import TrendReq  # impor malas
    # retries/backoff_factor sengaja 0: kombinasi >0 rusak di urllib3 2.x (method_whitelist); retry dikerjakan di bawah
    py = TrendReq(hl=cfg.hl, tz=cfg.tz, timeout=cfg.timeout, retries=0, backoff_factor=0)
    py.build_payload(list(peta.values()), timeframe=cfg.timeframe, geo=cfg.geo)
    return _ke_skema(py.interest_over_time(), peta)


def ambil_tren_google(peta_kata_kunci: Mapping[str, str], cfg: TrendsConfig = TRENDS_CONFIG, *,
                      paksa_segar: bool = False) -> HasilTren:
    """peta_kata_kunci = {brand: kata_kunci}. Tidak pernah melempar; gagal -> HasilTren(df_kosong(), ok=False)."""
    peta = dict(list(peta_kata_kunci.items())[: cfg.maks_kata_kunci])
    if not peta:
        return HasilTren(df_kosong(), False, "Tidak ada kata kunci yang diminta.")
    k = (tuple(sorted(peta.items())), cfg.geo, cfg.timeframe)
    if not paksa_segar and (hit := _cache_ambil(k)) is not None:
        return HasilTren(hit.df.copy(), hit.ok, hit.pesan)

    for i in range(cfg.maks_percobaan):
        try:
            df = _satu_kali(peta, cfg)
            hasil = HasilTren(df, True, "" if len(df) else "Google Trends tidak mengembalikan data untuk kata kunci ini.")
            _cache_simpan(k, hasil, cfg.ttl_sukses_detik)
            return HasilTren(df.copy(), hasil.ok, hasil.pesan)
        except ImportError:
            _log.warning("pytrends belum terpasang")
            break
        except Exception as e:  # noqa: BLE001 - 429/timeout/blokir IP/format respons berubah: semuanya ditangani sama
            _log.warning("Google Trends gagal (percobaan=%d/%d, tipe=%s)", i + 1, cfg.maks_percobaan, type(e).__name__)
            if i < cfg.maks_percobaan - 1:
                time.sleep(cfg.jeda_awal_detik * (2 ** i) + random.uniform(0, 1))
    gagal = HasilTren(df_kosong(), False, PESAN_TRENDS_GAGAL)
    _cache_simpan(k, gagal, cfg.ttl_gagal_detik)
    return gagal


def tampilkan_info_bila_gagal(hasil: HasilTren) -> None:
    """Untuk dipakai langsung di halaman Streamlit: pesan ramah (st.info), bukan error merah."""
    if hasil.ok and hasil.pesan == "":
        return
    try:
        import streamlit as st
        st.info(hasil.pesan or PESAN_TRENDS_GAGAL)
    except Exception:  # noqa: BLE001
        pass


# ----------------------------------------------------------------------------- adaptor sumber untuk ai_market_tools
class SumberDenganFallback:
    """Kueri 'seri_pencarian': Supabase dulu; bila galat/kosong -> pytrends. Kueri lain diteruskan apa adanya.
    Bila keduanya gagal: galat ramah milik Supabase tetap dilempar (alat pasar mengubahnya jadi ok=False -> st.info),
    atau DataFrame kosong bila Supabase memang berhasil tetapi kosong."""

    def __init__(self, utama: Any, peta: Mapping[str, str] = PETA_DEFAULT, cfg: TrendsConfig = TRENDS_CONFIG):
        self.utama, self.peta, self.cfg = utama, dict(peta), cfg

    def tarik(self, kueri: str, **param) -> pd.DataFrame:
        if kueri != "seri_pencarian":
            return self.utama.tarik(kueri, **param)
        galat: Optional[Exception] = None
        try:
            df = self.utama.tarik(kueri, **param)
            if df is not None and len(df):
                return df
        except Exception as e:  # noqa: BLE001
            galat = e
        cadangan = ambil_tren_google(self.peta, self.cfg)
        if cadangan.ok and len(cadangan.df):
            d = cadangan.df
            mulai, selesai = param.get("mulai"), param.get("selesai")
            if mulai is not None:
                d = d[d["series_date"] >= pd.Timestamp(mulai)]
            if selesai is not None:
                d = d[d["series_date"] <= pd.Timestamp(selesai)]
            return d.head(int(param.get("batas", len(d)))).reset_index(drop=True)
        if galat is not None:
            raise galat
        return df_kosong()


def pasang_fallback(ai_market_tools: Any, peta: Optional[Mapping[str, str]] = None,
                    cfg: Optional[TrendsConfig] = None) -> None:
    """Idempoten. Panggil sekali sebelum panel pasar dirender."""
    sekarang = ai_market_tools._dapat_sumber()
    if isinstance(sekarang, SumberDenganFallback):
        return
    ai_market_tools.daftarkan_sumber(SumberDenganFallback(sekarang, peta or PETA_DEFAULT, cfg or TRENDS_CONFIG))
