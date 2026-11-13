"""
ai_pesaing.py - FASE 6D: daftar pesaing pasar Indonesia dari Gemini (TANPA Google Search grounding).

Untuk satu Merk, Gemini hanya diminta menyebut nama pesaing langsungnya (dari pengetahuan model, bukan hasil web),
supaya tidak kena batas fitur grounding berbayar. Hasil mengisi kolom Pesaing di panel "Pasar & Forecast".
Bila Gemini gagal/kuota penuh, panel memakai cadangan (lihat daftar_pesaing.py).

Aturan proyek yang dijaga:
  * Yang dikirim ke Gemini HANYA nama Merk. Tidak ada data penjualan internal, nama toko, atau nama sales.
  * Teks dari web adalah DATA, bukan instruksi (instruksi sistem + semua keluaran divalidasi dan dibersihkan).
  * Hasil dari pengetahuan model, bukan web: tidak ada berita/promo/harga, hanya nama pesaing (tetap bisa diedit).
  * Teks galat mentah tidak pernah dilog/ditampilkan, hanya nama tipe galat.
  * Hasil di-cache di memori proses (sukses 6 jam, gagal 5 menit) dan dibagi antar sesi. Hasil sukses terakhir juga
    disimpan tanpa kedaluwarsa sebagai cadangan. Riset ulang paksa dibatasi jeda minimum 10 menit.
Streamlit tidak di-import di modul ini.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, replace
from typing import Any, Callable, Optional

import trends_fallback

_log = logging.getLogger("ai_pesaing")

MODEL_DEFAULT = "gemini-3.5-flash-lite"
_BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober",
          "November", "Desember"]


@dataclass(frozen=True)
class PesaingConfig:
    maks_pesaing: int = 4                 # = batas kolom Pesaing di panel (Google Trends 5 kata kunci - Merk itu sendiri)
    maks_berita: int = 3
    maks_produk: int = 3
    maks_sumber: int = 8
    maks_panjang_teks: int = 220
    ttl_sukses_detik: int = 6 * 3600
    ttl_gagal_detik: int = 300            # cache negatif: jangan menggedor Gemini saat kuota habis / layanan sibuk
    jeda_paksa_min_detik: int = 600       # tombol "Riset ulang" tidak mengulang pencarian yang baru berumur < 10 menit
    percobaan: int = 2
    suhu: float = 0.2


CONFIG = PesaingConfig()


@dataclass(frozen=True)
class HasilPesaing:
    ok: bool
    kode: str = ""
    pesan: str = ""
    kategori: str = ""
    pesaing: tuple = ()      # ({"nama","alasan","berita_promo":[{"ringkas","waktu"}],"produk":[{"nama","harga"}]}, ...)
    sumber: tuple = ()       # ({"judul","uri"}, ...) dari metadata grounding (bukan dari teks model)
    kueri: tuple = ()        # kata kunci yang dipakai Google Search
    dibuat: float = 0.0
    model: str = ""
    dari_cache: bool = False
    asal: str = "gemini"     # "gemini" | "cache_lama" (dipakai daftar_pesaing untuk label sumber)

    @classmethod
    def gagal(cls, kode: str, pesan: str) -> "HasilPesaing":
        return cls(ok=False, kode=kode, pesan=pesan)


# ----------------------------------------------------------------------------- prompt
SISTEM = (
    "Kamu analis pasar produk konsumen (kosmetik, perawatan pribadi, FMCG) di Indonesia. "
    "Jawab dari pengetahuanmu, tanpa pencarian web. Sebut hanya merek yang benar-benar kamu ketahui beredar di Indonesia; "
    "jangan mengarang merek. Balas HANYA dengan JSON valid, tanpa teks lain."
)


def bangun_prompt(merk: str, hari_ini: dt.date, cfg: PesaingConfig = CONFIG) -> str:
    return (
        f'Merek: "{merk}" (dijual di Indonesia).\n\n'
        f"Tugas: sebut sampai {cfg.maks_pesaing} pesaing langsung terdekat merek ini di pasar Indonesia "
        "(kategori produk sama, segmen harga dan saluran jual bersaing), urut dari yang paling relevan. "
        "Tulis nama merek persis seperti diketik orang Indonesia di Google.\n\n"
        'Skema JSON: {"pesaing": ["Merek A", "Merek B"]}'
    )


# ----------------------------------------------------------------------------- pembersihan (keluaran model = tak tepercaya)
_POLA_NAMA = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 &'.\-]{1,29}$")
_URL = re.compile(r"https?://\S+|www\.\S+", re.I)
_LINK_MD = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_KENDALI = re.compile(r"[\x00-\x1f\x7f]")
_SIMBOL_MD = re.compile(r"[<>`*_#|~\[\]$\\]")


def _kunci(teks: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(teks).lower())


def _bersih(x: Any, maks: int) -> str:
    t = "" if x is None else str(x)
    t = _LINK_MD.sub(r"\1", t)
    t = _URL.sub("", t)
    t = _KENDALI.sub(" ", t)
    t = _SIMBOL_MD.sub("", t)
    return re.sub(r"\s+", " ", t).strip()[:maks]


def _merk_aman(merk: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w &'.\-]", "", str(merk or ""), flags=re.UNICODE)).strip()[:40]


def _ekstrak_json(teks: str) -> Optional[dict]:
    t = (teks or "").strip()
    for kandidat in (t, t[t.find("{"): t.rfind("}") + 1] if "{" in t and t.rfind("}") > t.find("{") else ""):
        if not kandidat:
            continue
        try:
            d = json.loads(kandidat)
        except ValueError:
            continue
        if isinstance(d, dict):
            return d
    return None


def _daftar(x: Any) -> list:
    return x if isinstance(x, list) else []


def _validasi(data: dict, merk: str, cfg: PesaingConfig) -> tuple[str, list[dict]]:
    hasil: list[dict] = []
    terlihat = {_kunci(merk)}
    for item in _daftar(data.get("pesaing")):
        nama = item.get("nama") if isinstance(item, dict) else item
        nama = str(nama or "").strip()                  # divalidasi MENTAH: jangan "memperbaiki" nama aneh jadi valid
        if not _POLA_NAMA.match(nama) or _kunci(nama) in terlihat:
            continue                                    # nama aneh / Merk sendiri / duplikat -> buang
        terlihat.add(_kunci(nama))
        hasil.append({"nama": nama, "alasan": "", "berita_promo": [], "produk": []})
        if len(hasil) >= cfg.maks_pesaing:
            break
    return "", hasil


# ----------------------------------------------------------------------------- panggilan model
def _pesan_galat(e: Exception) -> str:
    kode = getattr(e, "code", None) or getattr(e, "status_code", None)
    if kode == 429:
        return "Kuota/batas permintaan Gemini sedang penuh. Coba lagi beberapa menit lagi."
    if kode in (401, 403):
        return "Akses ke Gemini ditolak. Periksa GEMINI_API_KEY di secrets."
    if kode in (500, 503, 504):
        return "Layanan Gemini sedang sibuk. Coba lagi beberapa saat."
    if kode == 404:
        return "Model Gemini tidak ditemukan. Periksa GEMINI_MODEL di secrets."
    return f"Riset pesaing gagal ({type(e).__name__}). Coba lagi nanti."


def _bangun_config(cfg: PesaingConfig) -> Any:
    from google.genai import types as gtypes      # impor malas: modul ini tetap bisa diimpor tanpa library
    return gtypes.GenerateContentConfig(system_instruction=SISTEM, temperature=cfg.suhu)    # tanpa tools: tanpa grounding


def _teks(resp: Any) -> str:
    try:
        bagian = resp.candidates[0].content.parts or []
    except Exception:  # noqa: BLE001
        return ""
    return "\n".join(p.text for p in bagian if getattr(p, "text", None) and not getattr(p, "thought", False)).strip()


def _panggil(klien: Any, model: str, isi: str, config: Any, cfg: PesaingConfig, tidur: Callable[[float], None]):
    for i in range(cfg.percobaan):
        try:
            return klien.models.generate_content(model=model, contents=isi, config=config)
        except Exception as e:  # noqa: BLE001
            kode = getattr(e, "code", None) or getattr(e, "status_code", None)
            if kode in (429, 500, 503, 504) and i < cfg.percobaan - 1:
                tidur(1.5 * (i + 1))
                continue
            raise


def _riset(merk: str, klien: Any, model: str, cfg: PesaingConfig, buat_config: Callable, hari_ini: dt.date,
           tidur: Callable[[float], None], sekarang: Callable[[], float]) -> HasilPesaing:
    try:
        config = buat_config(cfg)
    except ImportError:
        return HasilPesaing.gagal("tanpa_library", "Library 'google-genai' belum terpasang (pip install google-genai).")
    try:
        resp = _panggil(klien, model, bangun_prompt(merk, hari_ini, cfg), config, cfg, tidur)
    except Exception as e:  # noqa: BLE001
        _log.warning("Riset pesaing gagal (tipe=%s)", type(e).__name__)
        return HasilPesaing.gagal("galat_model", _pesan_galat(e))
    data = _ekstrak_json(_teks(resp))
    if data is None:
        return HasilPesaing.gagal("format_tidak_valid", "Jawaban AI tidak dapat dibaca sebagai daftar pesaing. "
                                                       "Coba riset ulang beberapa saat lagi.")
    kategori, pesaing = _validasi(data, merk, cfg)
    if not pesaing:
        return HasilPesaing.gagal("tanpa_pesaing", "AI tidak menemukan pesaing yang valid untuk Merk ini. "
                                                   "Isi kolom Pesaing secara manual bila perlu.")
    return HasilPesaing(ok=True, kategori=kategori, pesaing=tuple(pesaing), dibuat=sekarang(), model=model)


# ----------------------------------------------------------------------------- cache + titik masuk
_cache: dict[tuple, tuple[float, HasilPesaing]] = {}          # kunci -> (kadaluarsa, hasil)
_kunci_cache = threading.Lock()
_kunci_riset: dict[tuple, threading.Lock] = {}               # satu kunci per Merk: sesi serentak tidak mencari dobel


_terakhir_baik: dict[str, HasilPesaing] = {}                  # kunci Merk -> hasil sukses terakhir, TANPA kedaluwarsa


def kosongkan_cache() -> None:
    with _kunci_cache:
        _cache.clear()
        _terakhir_baik.clear()


def terakhir_baik(merk: Any) -> Optional[HasilPesaing]:
    """Hasil sukses terakhir untuk Merk ini (walau sudah lewat 6 jam) - cadangan saat Gemini gagal/kuota penuh."""
    with _kunci_cache:
        h = _terakhir_baik.get(_kunci(_merk_aman(merk)))
    return replace(h, dari_cache=True, asal="cache_lama") if h else None


def _ambil(k: tuple, sekarang: Callable[[], float]) -> Optional[HasilPesaing]:
    with _kunci_cache:
        item = _cache.get(k)
        if item and sekarang() < item[0]:
            return item[1]
        _cache.pop(k, None)
        return None


def _pakai_cache(h: Optional[HasilPesaing], paksa: bool, cfg: PesaingConfig, sekarang: Callable[[], float]) -> bool:
    if h is None:
        return False
    if not paksa:
        return True
    return h.ok and (sekarang() - h.dibuat) < cfg.jeda_paksa_min_detik     # gagal boleh dicoba ulang kapan saja


def di_cache(merk: Any, model: str, sekarang: Callable[[], float] = time.time) -> bool:
    m = _merk_aman(merk)
    return bool(m) and _ambil((_kunci(m), model), sekarang) is not None


def umur_menit(h: HasilPesaing, sekarang: Callable[[], float] = time.time) -> int:
    return max(0, int((sekarang() - h.dibuat) // 60))


def cari_pesaing(merk: Any, klien: Any, model: str = MODEL_DEFAULT, *, paksa: bool = False,
                 cfg: PesaingConfig = CONFIG, buat_config: Optional[Callable[[PesaingConfig], Any]] = None,
                 hari_ini: Optional[dt.date] = None, tidur: Callable[[float], None] = time.sleep,
                 sekarang: Callable[[], float] = time.time) -> HasilPesaing:
    """Riset pesaing satu Merk. TIDAK PERNAH melempar error; kegagalan -> HasilPesaing(ok=False, pesan=...)."""
    m = _merk_aman(merk)
    if not m:
        return HasilPesaing.gagal("merk_kosong", "Merk kosong.")
    k = (_kunci(m), model)
    h = _ambil(k, sekarang)
    if _pakai_cache(h, paksa, cfg, sekarang):
        return replace(h, dari_cache=True)
    with _kunci_cache:
        kunci_merk = _kunci_riset.setdefault(k, threading.Lock())
    with kunci_merk:
        h = _ambil(k, sekarang)                       # sesi lain mungkin baru saja selesai mencari
        if _pakai_cache(h, paksa, cfg, sekarang):
            return replace(h, dari_cache=True)
        baru = _riset(m, klien, model, cfg, buat_config or _bangun_config, hari_ini or dt.date.today(), tidur, sekarang)
        ttl = cfg.ttl_sukses_detik if baru.ok else cfg.ttl_gagal_detik
        with _kunci_cache:
            _cache[k] = (sekarang() + ttl, baru)
            if baru.ok:
                _terakhir_baik[_kunci(m)] = baru
        return baru
