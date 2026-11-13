"""
daftar_pesaing.py - FASE 6D: daftar pesaing per Merk dengan urutan cadangan.

Urutan sumber (yang pertama tersedia dipakai):
  1. Gemini            - hasil baru / cache 6 jam dari ai_pesaing (tanpa Google Search grounding)
  2. Cache lama        - hasil Gemini sukses terakhir untuk Merk itu (walau sudah lewat 6 jam)
  3. Google Sheet      - daftar tetap, sheet "Pesaing" (kolom: Merk, Pesaing; pesaing dipisah koma)
  4. Daftar awal kode  - DAFTAR_AWAL di bawah (nilai awal, silakan dikoreksi)

Yang boleh MENGUBAH daftar tetap di Google Sheet: role manager dan direktur (akun fauziah berrole manager, jadi
otomatis termasuk). Role lain hanya membaca. Streamlit tidak di-import di modul ini.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional

import ai_pesaing

_log = logging.getLogger("daftar_pesaing")

NAMA_SHEET = "Pesaing"
HEADERS = ["Merk", "Pesaing"]
ROLE_EDITOR = frozenset({"manager", "direktur"})
MAKS_PESAING = 4

# Daftar awal (cadangan terakhir). Nilai awal untuk dikoreksi: sesuaikan dengan pasar Anda.
DAFTAR_AWAL: dict[str, list[str]] = {
    "Biore": ["Garnier", "Nivea", "Pond's", "Simple"],
    "Emina": ["Maybelline", "Pixy", "Sariayu", "Barenbliss"],
    "Wardah": ["Sariayu", "Pixy", "Maybelline", "Mustika Ratu"],
    "Kahf": ["Garnier Men", "Nivea Men", "Vaseline Men"],
}

SUMBER_LABEL = {
    "gemini": "Gemini",
    "cache_lama": "hasil Gemini terakhir (tersimpan)",
    "sheet": "daftar tetap di Google Sheet",
    "kode": "daftar awal di kode",
}


def _kunci(x: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(x or "").lower())


def boleh_edit(role: Any) -> bool:
    return str(role or "").strip().lower() in ROLE_EDITOR


def bersihkan(teks: Any, merk: Any, maks: int = MAKS_PESAING) -> list[str]:
    """'a, b,, B' -> ['a', 'b']: tanpa duplikat, tanpa Merk itu sendiri, nama berpola aman."""
    hasil: list[str] = []
    terlihat = {_kunci(merk)}
    for t in str(teks or "").split(","):
        t = t.strip()
        if t and ai_pesaing._POLA_NAMA.match(t) and _kunci(t) not in terlihat:
            terlihat.add(_kunci(t))
            hasil.append(t)
    return hasil[:maks]


# ----------------------------------------------------------------------------- penyimpanan Google Sheet
class SheetDaftar:
    """Pembungkus tipis worksheet 'Pesaing'. `ws` bisa diganti objek palsu untuk uji (get_all_values/update/append_row)."""

    def __init__(self, ws: Any):
        self.ws = ws

    def baca(self) -> dict[str, list[str]]:
        baris = self.ws.get_all_values()
        hasil: dict[str, list[str]] = {}
        for r in baris[1:]:
            if len(r) >= 2 and r[0].strip():
                hasil[_kunci(r[0])] = bersihkan(r[1], r[0])
        return hasil

    def simpan(self, merk: str, pesaing: list[str]) -> None:
        nilai = ", ".join(pesaing)
        baris = self.ws.get_all_values()
        if not baris or [c.strip() for c in baris[0][:2]] != HEADERS:
            self.ws.update(range_name="A1:B1", values=[HEADERS])
            baris = [HEADERS] + baris[1:]
        for i, r in enumerate(baris[1:], start=2):
            if r and _kunci(r[0]) == _kunci(merk):
                self.ws.update(range_name=f"B{i}", values=[[nilai]])
                return
        self.ws.append_row([merk, nilai])


def jelaskan_galat(e: Exception) -> str:
    """Petunjuk ramah dari JENIS galat saja (teks mentah/secret tidak pernah ditampilkan)."""
    tipe = type(e).__name__
    kode = getattr(getattr(e, "response", None), "status_code", None)
    if tipe == "KeyError":
        petunjuk = "Secret belum ada di secrets.toml (gcp_service_account atau TASK_SHEET_ID)."
    elif tipe == "SpreadsheetNotFound" or kode == 404:
        petunjuk = "Spreadsheet tidak ditemukan: periksa TASK_SHEET_ID dan share ke email service account."
    elif kode == 403:
        petunjuk = ("Akses ditolak: share spreadsheet sebagai Editor ke email service account, dan pastikan "
                    "Google Sheets API aktif di project Google Cloud.")
    elif kode == 429:
        petunjuk = "Batas permintaan Google Sheets sedang penuh. Coba lagi semenit lagi."
    elif tipe == "ModuleNotFoundError":
        petunjuk = "Library gspread belum terpasang (pip install gspread)."
    else:
        petunjuk = "Penyebab tidak dikenali."
    return f"{petunjuk} (jenis galat: {tipe}{f', kode {kode}' if kode else ''})"


@dataclass(frozen=True)
class DaftarPesaing:
    pesaing: tuple
    asal: str                 # kunci SUMBER_LABEL
    catatan: str = ""         # pesan ramah untuk layar (kosong bila Gemini berhasil)

    @property
    def label(self) -> str:
        return SUMBER_LABEL.get(self.asal, self.asal)


def tentukan(merk: str, hasil_gemini: Optional[ai_pesaing.HasilPesaing],
             baca_sheet: Optional[Callable[[], dict[str, list[str]]]] = None) -> Optional[DaftarPesaing]:
    """None = Gemini belum dipicu (isi awal dari daftar tetap tanpa pesan galat). Tidak pernah melempar error."""
    if hasil_gemini is not None and hasil_gemini.ok and hasil_gemini.pesaing:
        return DaftarPesaing(tuple(p["nama"] for p in hasil_gemini.pesaing), "gemini")
    alasan = (hasil_gemini.pesan if hasil_gemini is not None and not hasil_gemini.ok else "")
    lama = ai_pesaing.terakhir_baik(merk) if hasil_gemini is not None else None
    if lama is not None:
        return DaftarPesaing(tuple(p["nama"] for p in lama.pesaing), "cache_lama",
                             f"{alasan} Memakai {SUMBER_LABEL['cache_lama']} untuk {merk}.".strip())
    daftar_sheet: list[str] = []
    if baca_sheet is not None:
        try:
            daftar_sheet = baca_sheet().get(_kunci(merk), [])
        except Exception as e:  # noqa: BLE001 - teks galat mentah tidak ditampilkan/dilog
            _log.warning("Baca sheet pesaing gagal (tipe=%s)", type(e).__name__)
    if daftar_sheet:
        return DaftarPesaing(tuple(daftar_sheet), "sheet",
                             f"{alasan} Memakai {SUMBER_LABEL['sheet']} untuk {merk}.".strip() if alasan else "")
    awal = {_kunci(k): v for k, v in DAFTAR_AWAL.items()}.get(_kunci(merk), [])
    return DaftarPesaing(tuple(awal), "kode",
                         f"{alasan} Memakai {SUMBER_LABEL['kode']} untuk {merk}.".strip() if alasan else "")
