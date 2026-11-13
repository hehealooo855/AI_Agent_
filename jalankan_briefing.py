"""
jalankan_briefing.py - FASE 5: Penjalan briefing terjadwal (dipanggil GitHub Actions).

Contoh:
    python jalankan_briefing.py --jenis harian                     # DRY-RUN: cetak saja, tidak mengirim, tidak menulis log
    python jalankan_briefing.py --jenis harian --kirim             # kirim sungguhan
    python jalankan_briefing.py --jenis harian --hanya direktur    # satu penerima (username)
    python jalankan_briefing.py --jenis harian --kontak-csv kontak.csv --tanpa-sheet   # uji lokal tanpa Google Sheets

Alur: muat data (app_loader) -> baca Kontak + users.csv -> per penerima: bangun briefing sesuai hak akses -> (opsional)
narasi AI terverifikasi -> kirim per kanal -> catat ke sheet BriefingLog (kunci anti-ganda: username|jenis|tanggal-WIB).

Sheet 'Kontak' (di spreadsheet yang sama dengan 'Tugas'): username | telegram_chat_id | email | aktif (kosong/Y = aktif, N = nonaktif)
Hak akses penerima TIDAK diambil dari Kontak, melainkan dari users.csv (role, sales_name).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import ai_tasks as K
import ai_tools
from briefing import Briefing, bangun_briefing, render_teks, tambah_narasi
from anomali import AnomaliConfig
import pengirim as P

HEADER_LOG = ["kunci", "waktu", "username", "jenis", "status", "saluran", "detail"]


# =============================================================================
# Penyimpanan log (Sheet atau memori untuk uji)
# =============================================================================
class MemoriLog:
    def __init__(self):
        self.baris: list[list[str]] = []

    def terkirim(self) -> set[str]:
        return {r[0] for r in self.baris if r[4] == "terkirim"}

    def catat(self, kunci: str, username: str, jenis: str, status: str, saluran: str, detail: str = "") -> None:
        self.baris.append([kunci, K.sekarang().strftime("%Y-%m-%d %H:%M:%S"), username, jenis, status, saluran, detail[:200]])


class SheetLog(MemoriLog):
    def __init__(self, ws: Any):
        super().__init__()
        self.ws = ws
        nilai = ws.get_all_values()
        if not nilai:
            ws.append_row(HEADER_LOG)
        else:
            self.baris = [r + [""] * (len(HEADER_LOG) - len(r)) for r in nilai[1:]]

    def catat(self, kunci, username, jenis, status, saluran, detail="") -> None:
        super().catat(kunci, username, jenis, status, saluran, detail)
        self.ws.append_row(self.baris[-1])


# =============================================================================
# Penerima: Kontak (siapa & ke mana) x users.csv (hak akses)
# =============================================================================
@dataclass
class Penerima:
    username: str
    role: str
    sales_name: str
    telegram: str = ""
    email: str = ""


def baca_users(path: str) -> dict[str, dict]:
    """Hanya kolom username/role/sales_name yang dipakai; kolom lain (termasuk password) tidak disentuh."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {(r.get("username") or "").strip().lower(): {"role": (r.get("role") or "").strip().lower(),
                                                              "sales_name": (r.get("sales_name") or "").strip()}
                for r in csv.DictReader(f) if (r.get("username") or "").strip()}


def susun_penerima(kontak: list[dict], users: dict[str, dict]) -> tuple[list[Penerima], list[str]]:
    hasil, lewat = [], []
    for k in kontak:
        u = (k.get("username") or "").strip().lower()
        if not u or (k.get("aktif") or "").strip().upper() == "N":
            continue
        if u not in users:
            lewat.append(f"{u}: tidak ada di users.csv (dilewati)")
            continue
        tg, em = (k.get("telegram_chat_id") or "").strip(), (k.get("email") or "").strip()
        if not tg and not em:
            lewat.append(f"{u}: tidak punya telegram_chat_id/email (dilewati)")
            continue
        hasil.append(Penerima(u, users[u]["role"], users[u]["sales_name"], tg, em))
    return hasil, lewat


# =============================================================================
# Penjalan utama (semua dependensi disuntik -> bisa diuji tanpa jaringan)
# =============================================================================
@dataclass
class Dependensi:
    df: Any
    config: ai_tools.ToolsConfig
    tugas_df: Any                                  # DataFrame tugas atau None bila Sheet tidak tersedia
    penerima: list[Penerima]
    log: MemoriLog
    pengirim: dict[str, Any]
    client: Any = None
    model: str = ""
    acfg: AnomaliConfig = field(default_factory=AnomaliConfig)


@dataclass
class Ringkasan:
    terkirim: int = 0
    dilewati: int = 0
    gagal: int = 0
    narasi_dipakai: int = 0
    teks: dict[str, str] = field(default_factory=dict)       # username -> teks (untuk dry-run & uji)


def jalankan(jenis: str, dep: Dependensi, kirim: bool, hanya: Optional[str] = None, pakai_narasi: bool = True,
             maks_narasi: int = 60, hari_ini: Optional[dt.date] = None, cetak: Callable[[str], None] = print,
             jeda: float = 0.05) -> Ringkasan:
    hari_ini = hari_ini or K.sekarang().date()
    sudah = dep.log.terkirim() if kirim else set()
    out = Ringkasan()
    narasi_terpakai = 0

    for p in dep.penerima:
        if hanya and p.username != hanya.strip().lower():
            continue
        kunci = f"{p.username}|{jenis}|{hari_ini.isoformat()}"
        if kirim and kunci in sudah:
            cetak(f"[lewat] {p.username}: sudah terkirim hari ini")
            out.dilewati += 1
            continue
        try:
            b: Briefing = bangun_briefing(jenis, dep.df, p.role, p.sales_name, dep.config, dep.tugas_df, dep.acfg,
                                          hari_ini, penerima=p.username)
        except ai_tools.ToolError as e:
            cetak(f"[lewat] {p.username}: {e}")
            out.dilewati += 1
            continue
        except Exception as e:  # noqa: BLE001 - satu penerima gagal tidak boleh menghentikan yang lain
            cetak(f"[GAGAL] {p.username}: gagal menyusun briefing ({type(e).__name__})")
            out.gagal += 1
            if kirim:
                dep.log.catat(kunci, p.username, jenis, "gagal", "-", f"susun:{type(e).__name__}")
            continue

        if pakai_narasi and dep.client is not None and narasi_terpakai < maks_narasi:
            narasi_terpakai += 1
            if tambah_narasi(b, dep.client, dep.model):
                out.narasi_dipakai += 1
        for w in b.peringatan:
            cetak(f"[catatan] {p.username}: {w}")

        teks = render_teks(b, judul_peran=f"{p.username} ({p.role})")
        out.teks[p.username] = teks
        if not kirim:
            cetak(f"\n===== [DRY-RUN] {p.username} ({p.role}) -> telegram:{'ya' if p.telegram else 'tidak'} email:{'ya' if p.email else 'tidak'} =====\n{teks}")
            continue

        saluran_ok, saluran_gagal = [], []
        subjek = f"Briefing {jenis} {hari_ini:%d/%m/%Y}"
        for nama, tujuan in (("telegram", p.telegram), ("email", p.email)):
            if not tujuan:
                continue
            kanal = dep.pengirim.get(nama)
            if kanal is None:
                saluran_gagal.append(f"{nama}:tidak dikonfigurasi")
                continue
            try:
                kanal.kirim(tujuan, subjek, teks)
                saluran_ok.append(nama)
            except Exception as e:  # noqa: BLE001
                saluran_gagal.append(f"{nama}:{e if isinstance(e, P.PengirimError) else type(e).__name__}")
            time.sleep(jeda)
        if saluran_ok:
            dep.log.catat(kunci, p.username, jenis, "terkirim", ",".join(saluran_ok), "; ".join(saluran_gagal))
            out.terkirim += 1
            cetak(f"[ok] {p.username}: {','.join(saluran_ok)}" + (f" (gagal: {'; '.join(saluran_gagal)})" if saluran_gagal else ""))
        else:
            dep.log.catat(kunci, p.username, jenis, "gagal", "-", "; ".join(saluran_gagal))
            out.gagal += 1
            cetak(f"[GAGAL] {p.username}: {'; '.join(saluran_gagal)}")
    return out


# =============================================================================
# CLI
# =============================================================================
def _buka_sheets(env: dict[str, str]):
    import gspread
    gc = gspread.service_account_from_dict(json.loads(env["GCP_SERVICE_ACCOUNT_JSON"]))
    sh = gc.open_by_key(env["TASK_SHEET_ID"])

    def ws(nama: str, kolom: int):
        try:
            return sh.worksheet(nama)
        except gspread.WorksheetNotFound:
            return sh.add_worksheet(title=nama, rows=2000, cols=kolom)
    return ws


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Briefing penjualan terjadwal")
    ap.add_argument("--jenis", choices=["harian", "mingguan"], required=True)
    ap.add_argument("--kirim", action="store_true", help="kirim sungguhan (tanpa ini = dry-run)")
    ap.add_argument("--hanya", help="hanya username ini")
    ap.add_argument("--tanpa-narasi", action="store_true")
    ap.add_argument("--maks-narasi", type=int, default=60, help="batas panggilan Gemini per run (pagar biaya)")
    ap.add_argument("--app", default="app.py")
    ap.add_argument("--users", default="users.csv")
    ap.add_argument("--kontak-csv", help="baca kontak dari CSV lokal (uji); default: sheet 'Kontak'")
    ap.add_argument("--tanpa-sheet", action="store_true", help="jangan sentuh Google Sheets (dry-run/uji lokal)")
    a = ap.parse_args(argv)

    os.environ.setdefault("TZ", "Asia/Jakarta")
    if hasattr(time, "tzset"):
        time.tzset()
    env = dict(os.environ)

    from app_loader import muat_data_penjualan
    df, target_db, indiv = muat_data_penjualan(a.app)                  # RuntimeError -> job gagal (sengaja)
    config = ai_tools.ToolsConfig(target_database=target_db, individual_targets=indiv)
    users = baca_users(a.users)

    tugas_df, log, kontak = None, MemoriLog(), []
    if a.kontak_csv:
        with open(a.kontak_csv, encoding="utf-8-sig", newline="") as f:
            kontak = list(csv.DictReader(f))
    if not a.tanpa_sheet:
        try:
            ws = _buka_sheets(env)
            store = K.SheetStore(ws("Tugas", len(K.HEADERS)))
            tugas_df = store.baca()
            if not a.kontak_csv:
                nilai = ws("Kontak", 4).get_all_values()
                kontak = [dict(zip(nilai[0], r)) for r in nilai[1:]] if nilai else []
            if a.kirim:
                log = SheetLog(ws("BriefingLog", len(HEADER_LOG)))
        except Exception as e:  # noqa: BLE001
            print(f"[peringatan] Google Sheets tidak dapat dibuka ({type(e).__name__}).")
            if a.kirim:
                print("[GAGAL] Mode kirim membutuhkan Sheets (Kontak + BriefingLog); dibatalkan agar tidak mengirim ganda.")
                return 1
    penerima, lewat = susun_penerima(kontak, users)
    for w in lewat:
        print(f"[lewat] {w}")
    if not penerima:
        print("Tidak ada penerima yang valid.")
        return 1 if a.kirim else 0

    client, model = None, env.get("GEMINI_MODEL") or "gemini-3.5-flash-lite"
    if not a.tanpa_narasi and env.get("GEMINI_API_KEY"):
        try:
            from google import genai
            client = genai.Client(api_key=env["GEMINI_API_KEY"])
        except Exception as e:  # noqa: BLE001
            print(f"[peringatan] Gemini tidak aktif ({type(e).__name__}); briefing dikirim tanpa narasi.")

    dep = Dependensi(df, config, tugas_df, penerima, log, P.buat_pengirim(env), client, model)
    r = jalankan(a.jenis, dep, a.kirim, a.hanya, not a.tanpa_narasi, a.maks_narasi)
    print(f"\nSelesai ({'KIRIM' if a.kirim else 'DRY-RUN'}): terkirim={r.terkirim} dilewati={r.dilewati} gagal={r.gagal} narasi={r.narasi_dipakai}")
    if a.kirim and r.gagal and not r.terkirim:
        return 1                                                    # semua gagal -> job merah di Actions
    return 0


if __name__ == "__main__":
    sys.exit(main())
