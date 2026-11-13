"""
ai_tasks.py - FASE 3: Dari insight menjadi tugas (logika inti, TANPA import streamlit).

Alur:  usulan -> (disetujui | ditolak) -> dikerjakan -> selesai
  * Program/AI hanya MENGUSULKAN. Baris masuk ke Sheet hanya lewat `kirim_usulan`, yang dipanggil tombol konfirmasi.
  * Prioritas, tenggat, deduplikasi, dan izin perpindahan status ditentukan aturan Python di sini, bukan AI.
  * Teks 'tindakan' dan 'alasan' dibuat dari template + angka hasil ai_tools (selalu akurat).

Asumsi default (ubah lewat TaskConfig / fungsi izin bila keputusan bisnis berbeda):
  * Yang boleh mengusulkan  : akses penuh (manager/direktur/fauziah) dan supervisor (data merk miliknya).
  * Yang boleh menyetujui   : akses penuh untuk semua tugas; supervisor hanya tugas yang kolom 'brand'-nya merk miliknya.
  * Sales                   : hanya melihat tugasnya sendiri; boleh mulai mengerjakan, menyelesaikan, dan mengisi catatan.
  * Deduplikasi             : outlet + jenis + brand yang sama diblokir bila masih aktif atau dibuat <= 14 hari lalu.
"""
from __future__ import annotations

import datetime as dt
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import pandas as pd
import pytz

import ai_tools

# =============================================================================
# 1. KONSTANTA & KONFIGURASI
# =============================================================================
HEADERS = [
    "id", "dibuat_pada", "dibuat_oleh", "sales", "outlet", "brand", "jenis",
    "tindakan", "alasan", "prioritas", "tenggat", "status",
    "disetujui_oleh", "disetujui_pada", "catatan_sales", "selesai_pada",
]  # sama persis dengan rancangan di ai_tes_koneksi.py
AUDIT_HEADERS = ["waktu", "user", "aksi", "id_tugas", "detail"]

JENIS_TUGAS = {
    "toko_tidur": "Toko tidur",
    "outlet_turun_order": "Outlet turun order",
    "cross_sell_brand": "Cross-sell brand",
    "cross_sell_sku": "Cross-sell SKU",
}
STATUS_AKHIR = ("selesai", "ditolak")
STATUS_BERJALAN = ("disetujui", "dikerjakan")
TRANSISI = {"usulan": ("disetujui", "ditolak"), "disetujui": ("dikerjakan", "selesai"), "dikerjakan": ("selesai",)}
URUT_PRIORITAS = {"tinggi": 0, "sedang": 1, "rendah": 2}
WIB = pytz.timezone("Asia/Jakarta")
MAKS_CATATAN = 500


@dataclass(frozen=True)
class TaskConfig:
    dedup_hari: int = 14                      # masa pendinginan per outlet + jenis + brand
    tenggat_hari: dict = field(default_factory=lambda: {"tinggi": 3, "sedang": 7, "rendah": 14})
    persentil_tinggi: float = 0.25            # 25% skor teratas = prioritas tinggi
    persentil_sedang: float = 0.60            # sampai 60% = sedang, sisanya rendah
    maks_usulan: int = 30                     # batas usulan per sekali kirim


@dataclass
class Hasil:
    ok: bool
    pesan: str
    jumlah: int = 0


def sekarang() -> dt.datetime:
    """Waktu WIB (naive) agar konsisten dengan app.py."""
    return dt.datetime.now(WIB).replace(tzinfo=None)


def _t(x: Any) -> str:
    return "" if x is None else str(x).strip()


def _up(x: Any) -> str:
    return _t(x).upper()


# =============================================================================
# 2. PENYIMPANAN (Google Sheets lewat gspread; ws bisa diganti objek palsu untuk uji)
# =============================================================================
def _kode_kolom(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


class SheetStore:
    """Pembungkus tipis worksheet 'Tugas' (+ 'AuditLog'). Semua pemanggilan API dicoba ulang bila kena 429."""

    def __init__(self, ws_tugas: Any, ws_audit: Any = None, jeda: Callable[[float], None] = time.sleep):
        self.ws, self.ws_audit, self._jeda = ws_tugas, ws_audit, jeda

    def _coba(self, fn: Callable, *a, **k):
        for i in range(3):
            try:
                return fn(*a, **k)
            except Exception as e:  # noqa: BLE001
                kode = getattr(e, "code", None) or getattr(getattr(e, "response", None), "status_code", None)
                if kode in (429, 500, 503) and i < 2:
                    self._jeda(2 * (i + 1))
                    continue
                raise

    def pastikan_header(self) -> None:
        h = self._coba(self.ws.row_values, 1)
        if not h:
            self._coba(self.ws.append_row, HEADERS)
        elif h[:len(HEADERS)] != HEADERS:
            raise ValueError("Header sheet 'Tugas' berbeda dari rancangan. Perbaiki baris 1 agar sama dengan: " + ", ".join(HEADERS))
        if self.ws_audit is not None and not self._coba(self.ws_audit.row_values, 1):
            self._coba(self.ws_audit.append_row, AUDIT_HEADERS)

    def baca(self) -> pd.DataFrame:
        nilai = self._coba(self.ws.get_all_values)
        if not nilai or len(nilai) < 2:
            return pd.DataFrame(columns=HEADERS, dtype=str)
        kepala = nilai[0]
        baris = [(r + [""] * len(kepala))[:len(kepala)] for r in nilai[1:] if any(_t(c) for c in r)]
        df = pd.DataFrame(baris, columns=kepala, dtype=str)
        for k in HEADERS:
            if k not in df.columns:
                df[k] = ""
        df = df[HEADERS].copy()
        df["status"] = df["status"].map(lambda s: _t(s).lower())
        return df

    def tambah(self, baris: list[dict]) -> None:
        if baris:
            self._coba(self.ws.append_rows, [[_t(b.get(k, "")) for k in HEADERS] for b in baris])

    def cari(self, id_tugas: str) -> Optional[tuple[int, dict]]:
        """Baca ULANG langsung dari Sheet (bukan cache) -> (nomor_baris, dict) atau None."""
        kolom_id = self._coba(self.ws.col_values, 1)
        if id_tugas not in kolom_id:
            return None
        nomor = kolom_id.index(id_tugas) + 1
        r = self._coba(self.ws.row_values, nomor)
        d = {k: _t(r[i]) if i < len(r) else "" for i, k in enumerate(HEADERS)}
        d["status"] = d["status"].lower()
        return nomor, d

    def ubah(self, nomor: int, perubahan: dict) -> None:
        data = [{"range": f"{_kode_kolom(HEADERS.index(k) + 1)}{nomor}", "values": [[_t(v)]]} for k, v in perubahan.items()]
        self._coba(self.ws.batch_update, data)

    def audit(self, user: str, aksi: str, id_tugas: str = "", detail: str = "") -> None:
        if self.ws_audit is None:
            return
        try:
            self._coba(self.ws_audit.append_row, [sekarang().strftime("%Y-%m-%d %H:%M:%S"), _t(user), aksi, id_tugas, _t(detail)[:300]])
        except Exception:  # noqa: BLE001 - log audit tidak boleh menggagalkan aksi utama
            pass


# =============================================================================
# 3. IZIN (maker/checker sederhana berbasis ai_tools.Akses)
# =============================================================================
def boleh_mengusulkan(akses: ai_tools.Akses) -> bool:
    return akses.level in ("penuh", "supervisor")


def boleh_menyetujui(akses: ai_tools.Akses, baris: dict) -> bool:
    if akses.level == "penuh":
        return True
    if akses.level == "supervisor":
        return _up(baris.get("brand")) in {m.upper() for m in akses.merk_boleh}
    return False


def milik_sales(akses: ai_tools.Akses, baris: dict) -> bool:
    return akses.level == "sales" and _up(baris.get("sales")) == akses.nama


def boleh_mengerjakan(akses: ai_tools.Akses, baris: dict) -> bool:
    return milik_sales(akses, baris) or boleh_menyetujui(akses, baris)


def tugas_terlihat(df: pd.DataFrame, akses: ai_tools.Akses) -> pd.DataFrame:
    if df.empty or akses.level == "penuh":
        return df
    if akses.level == "supervisor":
        merk = {m.upper() for m in akses.merk_boleh}
        return df[df["brand"].map(_up).isin(merk) | (df["dibuat_oleh"].map(_up) == akses.nama)]
    return df[df["sales"].map(_up) == akses.nama]


def tandai_terlambat(df: pd.DataFrame, sekarang_: Optional[dt.datetime] = None) -> pd.DataFrame:
    hari_ini = pd.Timestamp((sekarang_ or sekarang()).date())
    tenggat = pd.to_datetime(df["tenggat"], errors="coerce")
    df = df.copy()
    df["terlambat"] = (tenggat < hari_ini) & df["status"].isin(STATUS_BERJALAN)
    return df


# =============================================================================
# 4. MEMBUAT USULAN DARI HASIL ai_tools
# =============================================================================
def _sales_valid(s: Any) -> bool:
    return _up(s) not in {"", "-", "NAN", "NONE", "NON-SALES"}


def _dari_toko_tidur(h: dict) -> list[dict]:
    brand = _t((h.get("filter") or {}).get("merk")) or "-"
    return [dict(outlet=r["outlet"], sales=r["sales"], brand=brand, jenis="toko_tidur", skor=r["omset_historis"], minimal="",
                 tindakan=f"Kunjungi {r['outlet']} dan cari tahu kendala order ulang",
                 alasan=f"Tidak order {r['hari_sejak_order']} hari (terakhir {r['order_terakhir']}); omset historis {r['omset_historis_rp']}")
            for r in h.get("daftar", [])]


def _dari_turun_order(h: dict) -> list[dict]:
    brand = _t((h.get("filter") or {}).get("merk")) or "-"
    p = h.get("periode", {})
    return [dict(outlet=r["outlet"], sales=r["sales"], brand=brand, jenis="outlet_turun_order", skor=abs(r["selisih"]),
                 minimal="tinggi" if r.get("berhenti_total") else "",
                 tindakan=f"Kunjungi {r['outlet']} dan pulihkan order" + (" (berhenti total)" if r.get("berhenti_total") else ""),
                 alasan=(f"Omset turun {r['turun_persen']}% dari {r['omset_sebelumnya_rp']} ke {r['omset_sekarang_rp']} "
                         f"(periode {p.get('mulai')} s/d {p.get('selesai')} vs periode sebelumnya yang sama panjang)"))
            for r in h.get("daftar", [])]


def _dari_cross_brand(h: dict) -> list[dict]:
    pt = h.get("periode_target", {})
    return [dict(outlet=r["outlet"], sales=r["sales"], brand=h["merk_target"], jenis="cross_sell_brand", skor=r["omset_acuan"], minimal="",
                 tindakan=f"Tawarkan {h['merk_target']} ke {r['outlet']}",
                 alasan=(f"Sudah membeli {h['merk_acuan']} ({r['omset_acuan_rp']}) tetapi belum membeli {h['merk_target']} "
                         f"pada {pt.get('mulai')} s/d {pt.get('selesai')}"))
            for r in h.get("daftar", [])]


def _dari_cross_sku(h: dict) -> list[dict]:
    return [dict(outlet=r["outlet"], sales=r["sales"], brand="-", jenis="cross_sell_sku", skor=r["omset_outlet"], minimal="",
                 tindakan=f"Tawarkan {r['tawarkan']} ke {r['outlet']}",
                 alasan=(f"Outlet sudah membeli {r['karena_sudah_beli']}; {r['confidence_persen']}% faktur yang memuat produk itu "
                         f"juga memuat {r['tawarkan']} (lift {r['lift']})"))
            for r in h.get("rekomendasi_outlet", [])]


_PEMBANGUN = {"toko_tidur": _dari_toko_tidur, "outlet_turun_order": _dari_turun_order,
              "cross_sell_brand": _dari_cross_brand, "cross_sell_sku": _dari_cross_sku}


def bangun_usulan(hasil_alat: dict) -> tuple[list[dict], list[dict]]:
    """Hasil alat ai_tools -> (usulan mentah, dilewati). Usulan tanpa sales penanggung jawab dilewati."""
    if not hasil_alat.get("ok"):
        return [], [{"outlet": "-", "alasan": hasil_alat.get("pesan", "Alat gagal dijalankan")}]
    fn = _PEMBANGUN.get(hasil_alat.get("alat"))
    if fn is None:
        return [], [{"outlet": "-", "alasan": f"Alat '{hasil_alat.get('alat')}' tidak dapat dijadikan tugas"}]
    usul, lewat = [], []
    for u in fn(hasil_alat):
        if not _sales_valid(u["sales"]):
            lewat.append({"outlet": u["outlet"], "alasan": "Sales penanggung jawab tidak diketahui"})
            continue
        u["sales"] = _up(u["sales"])
        usul.append(u)
    return usul, lewat


def tetapkan_prioritas(skor: list[float], cfg: TaskConfig) -> list[str]:
    n = len(skor)
    urut = sorted(range(n), key=lambda i: -skor[i])
    hasil = [""] * n
    for rank, i in enumerate(urut):
        p = rank / n
        hasil[i] = "tinggi" if p < cfg.persentil_tinggi else "sedang" if p < cfg.persentil_sedang else "rendah"
    return hasil


def _kunci(outlet: Any, jenis: Any, brand: Any) -> tuple[str, str, str]:
    return _up(outlet), _t(jenis), _up(brand) or "-"


def kunci_terblokir(tugas_df: pd.DataFrame, cfg: TaskConfig, now: dt.datetime) -> set:
    """Kunci (outlet, jenis, brand) yang tidak boleh dibuatkan tugas baru."""
    if tugas_df is None or tugas_df.empty:
        return set()
    dibuat = pd.to_datetime(tugas_df["dibuat_pada"], errors="coerce")
    batas = pd.Timestamp(now) - pd.Timedelta(days=cfg.dedup_hari)
    terblokir = (~tugas_df["status"].isin(STATUS_AKHIR)) | (dibuat >= batas)
    return {_kunci(r.outlet, r.jenis, r.brand) for r in tugas_df[terblokir].itertuples()}


def siapkan_baris(usulan: list[dict], dibuat_oleh: str, tugas_df: pd.DataFrame, cfg: TaskConfig = TaskConfig(),
                  now: Optional[dt.datetime] = None) -> tuple[list[dict], list[dict]]:
    """Usulan mentah -> baris siap kirim (id, prioritas, tenggat terisi) + daftar yang dilewati (duplikat / melebihi batas)."""
    now = now or sekarang()
    blok, dilihat, sisa, lewat = kunci_terblokir(tugas_df, cfg, now), set(), [], []
    for u in usulan:
        k = _kunci(u["outlet"], u["jenis"], u["brand"])
        if k in blok or k in dilihat:
            lewat.append({"outlet": u["outlet"], "alasan": f"Sudah ada tugas aktif atau dibuat <= {cfg.dedup_hari} hari lalu"})
            continue
        dilihat.add(k)
        sisa.append(u)

    prio = tetapkan_prioritas([u["skor"] for u in sisa], cfg)
    for u, p in zip(sisa, prio):
        u["prioritas"] = "tinggi" if u.get("minimal") == "tinggi" else p
    sisa.sort(key=lambda u: (URUT_PRIORITAS[u["prioritas"]], -u["skor"]))
    for u in sisa[cfg.maks_usulan:]:
        lewat.append({"outlet": u["outlet"], "alasan": f"Melebihi batas {cfg.maks_usulan} usulan per kirim"})
    sisa = sisa[:cfg.maks_usulan]

    baris = []
    for u in sisa:
        baris.append({
            "id": f"T{now:%Y%m%d}-{uuid.uuid4().hex[:6].upper()}", "dibuat_pada": now.strftime("%Y-%m-%d %H:%M:%S"),
            "dibuat_oleh": _up(dibuat_oleh), "sales": u["sales"], "outlet": u["outlet"], "brand": u["brand"], "jenis": u["jenis"],
            "tindakan": u["tindakan"], "alasan": u["alasan"], "prioritas": u["prioritas"],
            "tenggat": (now.date() + dt.timedelta(days=cfg.tenggat_hari[u["prioritas"]])).isoformat(),
            "status": "usulan", "disetujui_oleh": "", "disetujui_pada": "", "catatan_sales": "", "selesai_pada": "",
        })
    return baris, lewat


def kirim_usulan(store: SheetStore, baris: list[dict], akses: ai_tools.Akses, cfg: TaskConfig = TaskConfig(),
                 now: Optional[dt.datetime] = None) -> Hasil:
    """Dipanggil tombol konfirmasi. Memeriksa izin + deduplikasi ULANG terhadap data Sheet terbaru sebelum menulis."""
    if not boleh_mengusulkan(akses):
        return Hasil(False, "Role Anda tidak boleh membuat usulan tugas.")
    if not baris:
        return Hasil(False, "Tidak ada usulan yang dipilih.")
    now = now or sekarang()
    blok = kunci_terblokir(store.baca(), cfg, now)
    final = [b for b in baris[:cfg.maks_usulan] if _kunci(b["outlet"], b["jenis"], b["brand"]) not in blok]
    for b in final:                                        # lapisan pertahanan: usulan selalu berstatus 'usulan' & atas nama pengirim
        b["status"], b["dibuat_oleh"] = "usulan", akses.nama
    store.tambah(final)
    for b in final:
        store.audit(akses.nama, "USULAN_DIBUAT", b["id"], f"{b['jenis']} | {b['outlet']} | {b['sales']}")
    dilewati = len(baris) - len(final)
    return Hasil(True, f"{len(final)} usulan tersimpan" + (f", {dilewati} dilewati (duplikat/melebihi batas)." if dilewati else "."), len(final))


# =============================================================================
# 5. PERUBAHAN STATUS & CATATAN (dengan deteksi tabrakan)
# =============================================================================
def ubah_status(store: SheetStore, id_tugas: str, akses: ai_tools.Akses, status_baru: str, status_dilihat: str,
                now: Optional[dt.datetime] = None) -> Hasil:
    """`status_dilihat` = status yang tampil di layar user. Bila Sheet sudah berubah (diubah orang lain) -> ditolak."""
    now = now or sekarang()
    ketemu = store.cari(id_tugas)
    if ketemu is None:
        return Hasil(False, f"Tugas {id_tugas} tidak ditemukan.")
    nomor, baris = ketemu
    status_kini = baris["status"]
    if status_kini != _t(status_dilihat).lower():
        return Hasil(False, f"Status tugas sudah berubah menjadi '{status_kini}' (mungkin oleh orang lain). Muat ulang lalu coba lagi.")
    if status_baru not in TRANSISI.get(status_kini, ()):
        return Hasil(False, f"Perpindahan status '{status_kini}' -> '{status_baru}' tidak diizinkan.")
    if status_baru in ("disetujui", "ditolak"):
        izin = boleh_menyetujui(akses, baris)
    else:
        izin = boleh_mengerjakan(akses, baris)
    if not izin:
        return Hasil(False, "Anda tidak berwenang mengubah tugas ini.")

    stempel = now.strftime("%Y-%m-%d %H:%M:%S")
    perubahan = {"status": status_baru}
    if status_baru in ("disetujui", "ditolak"):
        perubahan.update(disetujui_oleh=akses.nama, disetujui_pada=stempel)
    if status_baru == "selesai":
        perubahan["selesai_pada"] = stempel
    store.ubah(nomor, perubahan)
    store.audit(akses.nama, f"STATUS_{status_baru.upper()}", id_tugas, f"dari {status_kini}")
    return Hasil(True, f"Tugas {id_tugas} sekarang '{status_baru}'.", 1)


def simpan_catatan(store: SheetStore, id_tugas: str, akses: ai_tools.Akses, teks: str) -> Hasil:
    ketemu = store.cari(id_tugas)
    if ketemu is None:
        return Hasil(False, f"Tugas {id_tugas} tidak ditemukan.")
    nomor, baris = ketemu
    if not milik_sales(akses, baris):
        return Hasil(False, "Hanya sales pemilik tugas yang boleh mengisi catatan.")
    if baris["status"] not in STATUS_BERJALAN:
        return Hasil(False, "Catatan hanya bisa diisi saat tugas berstatus disetujui/dikerjakan.")
    store.ubah(nomor, {"catatan_sales": _t(teks)[:MAKS_CATATAN]})
    store.audit(akses.nama, "CATATAN_DIISI", id_tugas)
    return Hasil(True, "Catatan tersimpan.", 1)
