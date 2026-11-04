"""
Dashboard Sales Dinamis (Plug & Play)
=====================================
Satu aplikasi untuk semua client: upload CSV/Excel, petakan kolom lewat dropdown,
isi target lewat sidebar, lalu dapatkan tabel performa Sales & Merk secara otomatis.

Jalankan:
    pip install -r requirements.txt
    streamlit run app_sales_dinamis.py

Prinsip desain (supaya tidak pernah KeyError):
    1. Semua kolom client dipetakan ke NAMA INTERNAL tetap (_sales, _brand, _value, ...)
       tepat satu kali, di fungsi build_canonical(). Kode dashboard hanya memakai nama internal.
    2. Kolom dibaca berdasarkan POSISI (bukan nama) -> aman dari spasi tersembunyi & header ganda.
    3. Nilai penjualan, tanggal, dan teks dibersihkan & divalidasi; baris bermasalah dilaporkan.
    4. Kolom opsional (tanggal, kota, qty, outlet) boleh kosong; tab terkait otomatis menyesuaikan.
"""
from __future__ import annotations

import csv
import difflib
import hashlib
import io
import json
import math
import re
import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
from packaging.version import Version

# ======================================================================
# 0. KONFIGURASI
# ======================================================================
NONE = "— tidak dipakai —"
UNKNOWN = "(Tidak diketahui)"
UNITS = {"Rupiah": 1.0, "Ribu": 1e3, "Juta": 1e6, "Miliar": 1e9}


def _wide_kwargs() -> dict:
    """Streamlit >=1.50 memakai width='stretch'; versi lama use_container_width=True."""
    try:
        if Version(st.__version__) >= Version("1.50.0"):
            return {"width": "stretch"}
    except Exception:
        pass
    return {"use_container_width": True}


WIDE = _wide_kwargs()


@dataclass(frozen=True)
class Role:
    key: str  # id peran
    label: str  # label di dropdown
    kind: str  # text | number | date  (dipakai untuk auto-deteksi)
    required: bool
    internal: str  # nama kolom internal (tetap untuk semua client)
    display: str  # nama kolom di tabel hasil
    aliases: tuple  # kemungkinan nama kolom di file client


ROLES = (
    Role("sales", "Nama Sales", "text", True, "_sales", "Sales",
         ("sales", "salesman", "salesperson", "sales name", "nama sales", "nama salesman",
          "sales rep", "sales person", "petugas", "penjualan", "tenaga penjual", "karyawan sales")),
    Role("brand", "Merk / Brand", "text", True, "_brand", "Merk",
         ("merk", "merek", "brand", "nama merk", "nama merek", "nama brand", "brand name")),
    Role("value", "Nilai Penjualan (Rp)", "number", True, "_value", "Realisasi",
         ("jumlah", "nilai", "nilai penjualan", "omzet", "omset", "revenue", "amount", "total",
          "sales amount", "net sales", "netto", "subtotal", "total penjualan", "total harga",
          "nilai transaksi", "gross", "dpp", "harga total", "sales value")),
    Role("date", "Tanggal Transaksi", "date", False, "_date", "Tanggal",
         ("tanggal", "tgl", "date", "tanggal faktur", "tgl faktur", "tanggal transaksi",
          "invoice date", "order date", "transaction date", "tanggal order", "periode")),
    Role("city", "Kota / Wilayah", "text", False, "_city", "Kota",
         ("kota", "city", "kabupaten", "kab kota", "wilayah", "area", "region", "cabang", "nama kota")),
    Role("qty", "Qty / Kuantitas", "number", False, "_qty", "Qty",
         ("qty", "quantity", "kuantitas", "jumlah barang", "jumlah qty", "unit", "pcs", "volume", "kts")),
    Role("outlet", "Outlet / Customer", "text", False, "_outlet", "Outlet",
         ("nama outlet", "outlet", "toko", "nama toko", "customer", "pelanggan",
          "nama customer", "nama pelanggan", "client")),
)
ROLE = {r.key: r for r in ROLES}
S, B, V, D, C, Q, O = (ROLE[k].internal for k in ("sales", "brand", "value", "date", "city", "qty", "outlet"))


# ======================================================================
# 1. UTILITAS FORMAT
# ======================================================================
def fmt_num(v, d: int = 0) -> str:
    """1234567.8 -> '1.234.568' (format Indonesia)."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "–"
    s = f"{v:,.{d}f}"
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def fmt_compact(v: float) -> str:
    a = abs(v)
    for div, suf in ((1e12, "T"), (1e9, "M"), (1e6, "Jt")):
        if a >= div:
            return f"Rp {fmt_num(v / div, 2)} {suf}"
    return f"Rp {fmt_num(v)}"


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


# ======================================================================
# 2. PEMBACAAN FILE (CSV / Excel) — kebal terhadap format yang berbeda-beda
# ======================================================================
def is_excel(name: str) -> bool:
    return name.lower().endswith((".xlsx", ".xlsm", ".xls"))


def detect_encoding(raw: bytes) -> str:
    head = raw[:200_000]
    try:
        head.decode("utf-8-sig")
        return "utf-8-sig"
    except UnicodeDecodeError as e:
        return "utf-8-sig" if e.start >= len(head) - 4 else "latin-1"


def detect_sep(raw: bytes, enc: str) -> str:
    text = raw[:100_000].decode(enc, errors="ignore")
    lines = [ln for ln in text.splitlines()[:15] if ln.strip()]
    if not lines:
        return ","
    return max(",;\t|", key=lambda sep: float(np.median([ln.count(sep) for ln in lines])))


def _read(raw: bytes, name: str, sheet, header_row: int, usecols=None, nrows=None) -> pd.DataFrame:
    skip = max(header_row - 1, 0)
    if is_excel(name):
        return pd.read_excel(io.BytesIO(raw), sheet_name=sheet or 0, header=0,
                             skiprows=skip, usecols=usecols, nrows=nrows)
    enc = detect_encoding(raw)
    return pd.read_csv(io.BytesIO(raw), sep=detect_sep(raw, enc), encoding=enc, skiprows=skip,
                       usecols=usecols, nrows=nrows, low_memory=False, on_bad_lines="skip")


def clean_headers(cols) -> tuple:
    """Rapikan nama kolom: trim, rapatkan spasi, isi yang kosong, buat unik."""
    out, seen = [], {}
    for i, c in enumerate(cols):
        name = re.sub(r"\s+", " ", str(c)).strip() or f"Kolom {i + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name} ({seen[name]})"
        else:
            seen[name] = 1
        out.append(name)
    return tuple(out)


@st.cache_data(show_spinner=False)
def list_sheets(_raw: bytes, fhash: str) -> list:
    return pd.ExcelFile(io.BytesIO(_raw)).sheet_names


@st.cache_data(show_spinner=False)
def read_preview(_raw: bytes, fhash: str, name: str, sheet) -> pd.DataFrame:
    """30 baris pertama tanpa header — untuk menebak baris header."""
    if is_excel(name):
        return pd.read_excel(io.BytesIO(_raw), sheet_name=sheet or 0, header=None, nrows=30)
    enc = detect_encoding(_raw)
    return pd.read_csv(io.BytesIO(_raw), sep=detect_sep(_raw, enc), encoding=enc, header=None,
                       names=range(60), nrows=30, on_bad_lines="skip", dtype=str)


def guess_header_row(preview: pd.DataFrame) -> int:
    """Baris header = baris pertama dengan jumlah sel teks (non-angka) mendekati maksimum."""
    def n_text(row) -> int:
        return sum(isinstance(v, str) and v.strip() != "" and math.isnan(parse_number(v)) for v in row)

    counts = [n_text(r) for r in preview.itertuples(index=False)]
    if not counts or max(counts) == 0:
        return 1
    thr = max(1, 0.8 * max(counts))
    return next(i for i, c in enumerate(counts) if c >= thr) + 1


@st.cache_data(show_spinner=False)
def read_header(_raw: bytes, fhash: str, name: str, sheet, header_row: int):
    """Baca 200 baris contoh -> (nama kolom bersih, DataFrame contoh)."""
    sample = _read(_raw, name, sheet, header_row, nrows=200)
    names = clean_headers(sample.columns)
    sample.columns = names
    return names, sample


@st.cache_data(show_spinner="Membaca data…", max_entries=3)
def load_columns(_raw: bytes, fhash: str, name: str, sheet, header_row: int,
                 names: tuple, positions: tuple) -> pd.DataFrame:
    """Baca HANYA kolom yang dipetakan (berdasarkan posisi) -> cepat & hemat memori."""
    df = _read(_raw, name, sheet, header_row, usecols=list(positions))
    df.columns = [names[i] for i in sorted(positions)]
    return df


# ======================================================================
# 3. PEMBERSIHAN: angka, tanggal, teks
# ======================================================================
def parse_number(x) -> float:
    """'Rp 1.250.000' / '1,250,000.50' / '(500)' / 1250000 -> float. Tidak valid -> NaN."""
    if x is None:
        return math.nan
    if isinstance(x, (int, float, np.integer, np.floating)):
        return float(x)
    s = str(x).strip().lower()
    if not s:
        return math.nan
    s = re.sub(r"^(rp\.?|idr|usd|\$)\s*", "", s).strip()
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1].strip()
    if s.startswith("-"):
        neg, s = True, s[1:].strip()
    if s.endswith("-"):
        neg, s = True, s[:-1].strip()
    if not re.fullmatch(r"\d[\d.,]*", s):
        return math.nan

    def thousands_ok(parts) -> bool:
        return all(len(p) == 3 for p in parts[1:]) and 1 <= len(parts[0]) <= 3

    if "." in s and "," in s:
        dec = "." if s.rfind(".") > s.rfind(",") else ","
        thou = "," if dec == "." else "."
        left, _, right = s.rpartition(dec)
        parts = left.split(thou)
        if not thousands_ok(parts):
            return math.nan
        val = float("".join(parts) + "." + right)
    elif "." in s or "," in s:
        sep = "." if "." in s else ","
        parts = s.split(sep)
        if len(parts) > 2:  # beberapa pemisah -> pasti ribuan
            if not thousands_ok(parts):
                return math.nan
            val = float("".join(parts))
        else:
            left, right = parts
            if len(right) == 3 and 1 <= len(left) <= 3 and left != "0":  # '1.250' = 1250
                val = float(left + right)
            else:
                val = float(left + "." + right)
    else:
        val = float(s)
    return -val if neg else val


def to_number(s: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
        return pd.to_numeric(s, errors="coerce").astype("float64")
    uniq = pd.unique(s.dropna())
    mapping = {u: parse_number(u) for u in uniq}  # hanya nilai unik -> cepat
    return s.map(mapping).astype("float64")


def parse_date_strings(x: pd.Series, dayfirst: bool) -> pd.Series:
    """Teks -> tanggal. Format yang diawali tahun (2026-01-13) SELALU dibaca tahun-bulan-hari;
    format lain (13/01/2026) mengikuti opsi dayfirst. Tiap nilai diparse sendiri-sendiri."""
    x = x.astype(str).str.strip()
    iso = x.str.match(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}")
    parts = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if iso.any():
            parts.append(pd.to_datetime(x[iso], errors="coerce", format="mixed", dayfirst=False))
        if (~iso).any():
            parts.append(pd.to_datetime(x[~iso], errors="coerce", format="mixed", dayfirst=dayfirst))
    return pd.concat(parts).reindex(x.index)


def to_datetime_fast(s: pd.Series, dayfirst: bool) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(s):
        return s
    uniq = pd.Series(pd.unique(s.dropna()))  # parse nilai unik saja -> cepat untuk jutaan baris
    if uniq.empty:
        return pd.to_datetime(s, errors="coerce")
    if pd.api.types.is_numeric_dtype(uniq):  # serial date Excel
        conv = pd.to_datetime(uniq, unit="D", origin="1899-12-30", errors="coerce")
    else:
        conv = parse_date_strings(uniq, dayfirst)
    return pd.to_datetime(s.map(dict(zip(uniq, conv))), errors="coerce")


def clean_text(s: pd.Series, normalize: bool) -> pd.Series:
    """Trim, rapatkan spasi, isi kosong -> UNKNOWN, (opsional) samakan huruf besar/kecil."""
    s = s.astype("string").str.strip().str.replace(r"\s+", " ", regex=True)
    s = s.mask(s.str.casefold().isin(["", "nan", "none", "null", "<na>"])).fillna(UNKNOWN)
    if normalize:  # 'WARDAH' / 'wardah ' / 'Wardah' -> satu nama (yang paling sering muncul)
        key = s.str.casefold()
        freq = pd.DataFrame({"k": key, "s": s}).value_counts().reset_index(name="n")
        best = freq.drop_duplicates("k").set_index("k")["s"]
        s = key.map(best)
    return s.astype(str).astype("category")


# ======================================================================
# 4. AUTO-DETEKSI PEMETAAN KOLOM
# ======================================================================
@dataclass
class ColProfile:
    num: float  # proporsi nilai yang berupa angka
    date: float  # proporsi nilai yang berupa tanggal
    median: float  # median nilai absolut (jika numerik)


def profile_column(s: pd.Series) -> ColProfile:
    s = s.dropna()
    if s.empty:
        return ColProfile(0, 0, 0)
    if pd.api.types.is_datetime64_any_dtype(s):
        return ColProfile(0, 1, 0)
    if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
        return ColProfile(1, 0, float(np.nanmedian(np.abs(s.astype(float)))))
    vals = s.astype(str).head(200)
    nums = vals.map(parse_number)
    num_ratio = float(nums.notna().mean())
    date_ratio = 0.0
    if num_ratio < 0.5:
        date_ratio = float(parse_date_strings(vals, True).notna().mean())
    median = float(nums.abs().median()) if num_ratio >= 0.5 else 0.0
    return ColProfile(num_ratio, date_ratio, median)


def _score(role: Role, col: str, prof: ColProfile) -> float:
    n = _norm(col)
    name_score = 0.0
    for alias in role.aliases:
        a = _norm(alias)
        if n == a:
            s = 1.0
        elif len(n) >= 3 and (a in n or n in a):
            s = 0.75
        else:
            s = difflib.SequenceMatcher(None, n, a).ratio() * 0.6
        name_score = max(name_score, s)
    if role.kind == "text":
        compat = 1.0 if (prof.num < 0.5 and prof.date < 0.8) else 0.25
    elif role.kind == "number":
        compat = 1.0 if prof.num >= 0.8 else 0.1
    else:
        compat = 1.0 if prof.date >= 0.8 else 0.1
    bonus = 0.0
    if role.key == "value" and compat == 1.0:  # pemecah seri: nilai uang biasanya lebih besar dari qty
        bonus = min(math.log10(max(prof.median, 1.0)), 12) / 12 * 0.05
    return name_score * compat + bonus


def suggest_mapping(sample: pd.DataFrame) -> dict:
    """Penugasan serakah: pasangan (peran, kolom) skor tertinggi dulu, tiap kolom dipakai sekali."""
    profiles = {c: profile_column(sample[c]) for c in sample.columns}
    cands = sorted(((_score(r, c, p), r.key, c) for r in ROLES for c, p in profiles.items()), reverse=True)
    result, used = {r.key: None for r in ROLES}, set()
    for score, rkey, col in cands:
        if score < 0.45 or result[rkey] is not None or col in used:
            continue
        result[rkey] = col
        used.add(col)
    return result


# ======================================================================
# 5. DATA KANONIK — satu-satunya tempat nama kolom client disentuh
# ======================================================================
@st.cache_data(show_spinner="Membersihkan data…", max_entries=3)
def build_canonical(_raw: pd.DataFrame, cache_key: tuple, mapping_items: tuple,
                    dayfirst: bool, normalize: bool):
    """cache_key hanya untuk caching (fhash, sheet, header). Return (df kanonik, laporan kualitas)."""
    mapping = dict(mapping_items)
    report = {"baris_file": int(len(_raw))}
    out = pd.DataFrame(index=_raw.index)

    out[V] = to_number(_raw[mapping["value"]])
    keep = out[V].notna()
    report["baris_nilai_tidak_valid"] = int((~keep).sum())
    report["baris_nilai_negatif"] = int((out[V] < 0).sum())

    for role in ROLES:
        col = mapping.get(role.key)
        if role.kind == "text" and col:
            out[role.internal] = clean_text(_raw[col], normalize)
            report[f"kosong_{role.key}"] = int((_raw[col].isna()).sum())
        elif role.key == "qty" and col:
            out[Q] = to_number(_raw[col]).fillna(0.0)
        elif role.key == "date" and col:
            out[D] = to_datetime_fast(_raw[col], dayfirst)
            report["baris_tanggal_tidak_valid"] = int(out[D].isna().sum())

    out = out[keep].reset_index(drop=True)
    report["baris_dipakai"] = int(len(out))
    return out, report


# ======================================================================
# 6. AGREGASI
# ======================================================================
def status_label(p) -> str:
    if p is None or (isinstance(p, float) and math.isnan(p)):
        return "—"
    return "🟢 Tercapai" if p >= 100 else ("🟡 Hampir" if p >= 80 else "🔴 Di bawah")


def performance_table(df: pd.DataFrame, dim: str, label: str, targets: dict,
                      allowed: set | None = None, with_qty: bool = False) -> pd.DataFrame:
    """Tabel performa per dimensi (sales/merk/kota): realisasi, target, pencapaian, gap, kontribusi."""
    agg = {"Realisasi": (V, "sum"), "Transaksi": (V, "size")}
    if with_qty and Q in df.columns:
        agg["Qty"] = (Q, "sum")
    g = df.groupby(dim, observed=True).agg(**agg)
    g.index = g.index.astype(str)

    t = pd.Series({k: v for k, v in targets.items() if v and v > 0}, dtype="float64")
    if allowed is not None and len(t):
        t = t[t.index.isin(allowed)]
    g = g.reindex(g.index.union(t.index)).fillna(0)  # entitas bertarget tapi tanpa penjualan tetap muncul
    g["Target"] = t.reindex(g.index)
    g["Pencapaian (%)"] = np.where(g["Target"] > 0, g["Realisasi"] / g["Target"] * 100, np.nan)
    g["Gap"] = g["Realisasi"] - g["Target"]
    total = g["Realisasi"].sum()
    g["Kontribusi (%)"] = g["Realisasi"] / total * 100 if total else 0.0
    g["Status"] = g["Pencapaian (%)"].map(status_label)
    g = g.sort_values("Realisasi", ascending=False).rename_axis(label).reset_index()
    g.insert(0, "Peringkat", np.arange(1, len(g) + 1))
    cols = ["Peringkat", label, "Realisasi", "Target", "Pencapaian (%)", "Gap", "Kontribusi (%)", "Status", "Transaksi"]
    if "Qty" in g.columns:
        cols.append("Qty")
    return g[cols]


def pivot_sales_brand(df: pd.DataFrame) -> pd.DataFrame:
    pv = df.pivot_table(index=S, columns=B, values=V, aggfunc="sum", fill_value=0, observed=True)
    pv.index, pv.columns = pv.index.astype(str), pv.columns.astype(str)
    pv["TOTAL"] = pv.sum(axis=1)
    pv = pv.sort_values("TOTAL", ascending=False)
    pv.loc["TOTAL"] = pv.sum()
    return pv.rename_axis("Sales")


def monthly_trend(df: pd.DataFrame) -> pd.DataFrame:
    t = df.groupby(df[D].dt.to_period("M"))[V].sum()
    return pd.DataFrame({"Bulan": t.index.astype(str), "Realisasi": t.values})


# ======================================================================
# 7. TARGET (disimpan di session_state, bisa diekspor/impor sebagai profil client)
# ======================================================================
def default_targets() -> dict:
    return {"unit": "Juta", "total": 0.0, "sales": {}, "brand": {}}


def init_state():
    ss = st.session_state
    ss.setdefault("targets", default_targets())
    ss.setdefault("tgt_ver", 0)
    ss.setdefault("opts", {"dayfirst": True, "normalize": True})


def _fill_targets(kind: str, names: list):
    ss = st.session_state
    val = float(ss.get(f"fill_{kind}", 0.0) or 0.0)
    ss["targets"][kind] = {n: val for n in names}
    ss["tgt_ver"] += 1


def _reset_targets():
    st.session_state["targets"] = default_targets()
    st.session_state["tgt_ver"] += 1


def target_editor(kind: str, label: str, names: list):
    """Tabel target yang bisa diedit. Base DataFrame DIBEKUKAN per versi agar edit tidak hilang."""
    ss = st.session_state
    store = ss["targets"][kind]
    tag = hashlib.md5("|".join(names).encode()).hexdigest()[:8]
    base_key = f"_base_{kind}_{ss['tgt_ver']}_{tag}"
    for k in [k for k in ss if k.startswith(f"_base_{kind}_") and k != base_key]:
        del ss[k]
    if base_key not in ss:
        ss[base_key] = pd.DataFrame({label: names, "Target": [float(store.get(n, 0.0)) for n in names]})
    edited = st.data_editor(
        ss[base_key], hide_index=True, disabled=[label], height=260,
        key=f"ed_{kind}_{ss['tgt_ver']}_{tag}",
        column_config={"Target": st.column_config.NumberColumn("Target", min_value=0.0, format="%.2f")},
        **WIDE,
    )
    for n, v in zip(edited[label], edited["Target"]):
        store[n] = 0.0 if pd.isna(v) else float(v)


def sidebar_targets(sales_names: list, brand_names: list) -> dict:
    """Form target di sidebar. Return target efektif (sudah dikali satuan, dalam Rupiah)."""
    ss = st.session_state
    T = ss["targets"]
    st.header("4️⃣ Target")
    T["unit"] = st.selectbox("Satuan input target", list(UNITS), index=list(UNITS).index(T["unit"]))
    unit = T["unit"]
    T["total"] = st.number_input(f"Target total periode ({unit})", min_value=0.0, value=float(T["total"]),
                                 step=1.0, format="%.0f" if unit == "Rupiah" else "%.2f",
                                 help="Kosongkan (0) jika target total ingin dihitung dari jumlah target per sales.")
    for kind, title, names in (("sales", "Target per Sales", sales_names), ("brand", "Target per Merk", brand_names)):
        with st.expander(f"🎯 {title} ({len(names)})"):
            c1, c2 = st.columns([3, 2])
            c1.number_input(f"Isi cepat ({unit})", min_value=0.0, value=0.0, key=f"fill_{kind}",
                            label_visibility="collapsed", placeholder="Isi cepat")
            c2.button("Isi semua", key=f"btn_fill_{kind}", on_click=_fill_targets, args=(kind, names))
            target_editor(kind, ROLE[kind].display, names)
    st.button("♻️ Reset semua target", on_click=_reset_targets)

    mult = UNITS[unit]
    return {
        "total": T["total"] * mult,
        "sales": {k: v * mult for k, v in T["sales"].items() if v and v > 0},
        "brand": {k: v * mult for k, v in T["brand"].items() if v and v > 0},
    }


# ======================================================================
# 8. PROFIL CLIENT (simpan mapping + target -> pakai ulang untuk client yang sama)
# ======================================================================
def build_profile(mapping: dict) -> dict:
    ss = st.session_state
    return {"version": 1, "mapping": mapping, "options": ss["opts"], "targets": ss["targets"]}


def queue_profile():
    """Callback uploader profil: simpan dulu, diterapkan di awal run berikutnya (sebelum widget dibuat)."""
    f = st.session_state.get("profile_file")
    if f is None:
        return
    try:
        st.session_state["_pending_profile"] = json.loads(f.getvalue().decode("utf-8-sig"))
    except Exception as e:  # noqa: BLE001
        st.session_state["_profile_error"] = f"Profil tidak valid: {e}"


def apply_pending_profile(columns: tuple):
    ss = st.session_state
    p = ss.pop("_pending_profile", None)
    if not p:
        return
    lookup = {_norm(c): c for c in columns}
    missing = []
    for key, col in (p.get("mapping") or {}).items():
        if key not in ROLE:
            continue
        if col is None:
            ss["mapping"][key] = None
        elif _norm(col) in lookup:
            ss["mapping"][key] = lookup[_norm(col)]
        else:
            missing.append(f"{ROLE[key].label} → '{col}'")
    t = p.get("targets") or {}
    ss["targets"] = {**default_targets(), **{k: t[k] for k in ("unit", "total", "sales", "brand") if k in t}}
    ss["opts"] = {**ss["opts"], **(p.get("options") or {})}
    ss["tgt_ver"] += 1
    ss["_profile_msg"] = ("Profil diterapkan." if not missing else
                          "Profil diterapkan, tetapi kolom ini tidak ditemukan di file: " + "; ".join(missing))


# ======================================================================
# 9. EKSPOR EXCEL
# ======================================================================
def to_excel_bytes(sheets: dict) -> bytes:
    buf = io.BytesIO()
    try:
        import xlsxwriter  # noqa: F401
        engine = "xlsxwriter"
    except ImportError:
        engine = "openpyxl"
    with pd.ExcelWriter(buf, engine=engine) as xw:
        for name, df in sheets.items():
            df.to_excel(xw, sheet_name=name[:31], index=isinstance(df.index, pd.Index) and df.index.name is not None)
            if engine == "xlsxwriter":
                ws, wb = xw.sheets[name[:31]], xw.book
                num = wb.add_format({"num_format": "#,##0"})
                dec = wb.add_format({"num_format": "#,##0.0"})
                offset = 1 if df.index.name is not None else 0
                if offset:
                    ws.set_column(0, 0, 24)
                for i, col in enumerate(df.columns, start=offset):
                    is_num = pd.api.types.is_numeric_dtype(df[col])
                    fmt = dec if (is_num and "(%)" in str(col)) else (num if is_num else None)
                    ws.set_column(i, i, max(len(str(col)) + 2, 14), fmt)
                ws.freeze_panes(1, offset + 1)
    return buf.getvalue()


# ======================================================================
# 10. KOMPONEN TAMPILAN
# ======================================================================
def show_table(df: pd.DataFrame, money_cols=(), height: int | None = None):
    int_cols = [c for c in ("Transaksi", "Qty", "Peringkat") if c in df.columns]
    fmt = {c: fmt_num for c in list(money_cols) + int_cols if c in df.columns}
    for c in ("Pencapaian (%)", "Kontribusi (%)"):
        if c in df.columns:
            fmt[c] = lambda v: fmt_num(v, 1)
    cfg = {}
    if "Pencapaian (%)" in df.columns:
        cfg["Pencapaian (%)"] = st.column_config.ProgressColumn(
            "Pencapaian (%)", min_value=0, max_value=100, format="%.0f%%")
    st.dataframe(df.style.format(fmt, na_rep="–"), hide_index=True, column_config=cfg,
                 height=height, **WIDE)


def bar_chart(table: pd.DataFrame, label: str, top_n: int, title: str):
    d = table.head(top_n)
    has_t = d["Target"].notna().any()
    if has_t:
        long = d.melt(id_vars=label, value_vars=["Realisasi", "Target"], var_name="Jenis", value_name="Nilai")
        fig = px.bar(long, x="Nilai", y=label, color="Jenis", orientation="h", barmode="group", title=title)
    else:
        fig = px.bar(d, x="Realisasi", y=label, orientation="h", title=title)
    fig.update_yaxes(autorange="reversed", title=None)
    fig.update_xaxes(title=None)
    fig.update_layout(height=max(340, 28 * len(d) * (2 if has_t else 1) + 120),
                      margin=dict(l=10, r=10, t=50, b=10), legend_title=None)
    st.plotly_chart(fig, **WIDE)


def donut_chart(table: pd.DataFrame, label: str, title: str, top_n: int = 8):
    d = table[[label, "Realisasi"]].copy()
    d = d[d["Realisasi"] > 0]
    if len(d) > top_n:
        rest = d.iloc[top_n:]["Realisasi"].sum()
        d = pd.concat([d.head(top_n), pd.DataFrame({label: ["Lainnya"], "Realisasi": [rest]})])
    fig = px.pie(d, names=label, values="Realisasi", hole=0.5, title=title)
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=50, b=10))
    st.plotly_chart(fig, **WIDE)


def welcome():
    st.info("👈 Mulai dengan meng-upload file **CSV / Excel** penjualan client di sidebar.")
    st.markdown(
        """
**Cara kerja**
1. **Upload** file data penjualan (CSV, XLSX, XLS). Pilih *sheet* dan *baris header* bila perlu.
2. **Pemetaan kolom** terdeteksi otomatis — periksa dropdown-nya, ubah bila keliru, lalu klik *Terapkan*.
3. Isi **target** di sidebar (total, per sales, atau per merk) — bisa diubah kapan saja.
4. Lihat **performa Sales & Merk**, lalu unduh laporan Excel.
5. Simpan **profil client** (JSON) agar mapping & target tidak perlu diisi ulang bulan depan.
        """
    )


# ======================================================================
# 11. MAIN
# ======================================================================
def main():
    st.set_page_config(page_title="Dashboard Sales Dinamis", page_icon="📊", layout="wide")
    init_state()
    ss = st.session_state
    st.title("📊 Dashboard Sales — Plug & Play")

    # ---------- 1) Upload ----------
    with st.sidebar:
        st.header("1️⃣ Data")
        up = st.file_uploader("Upload CSV / Excel", type=["csv", "xlsx", "xlsm", "xls"], key="data_file")
        with st.expander("📁 Muat profil client (JSON)"):
            st.file_uploader("Profil client", type=["json"], key="profile_file",
                             on_change=queue_profile, label_visibility="collapsed")
            if ss.get("_profile_error"):
                st.error(ss.pop("_profile_error"))
            if ss.get("_profile_msg"):
                st.caption(ss.pop("_profile_msg"))

    if up is None:
        for k in ("_fkey", "_skey", "_msig"):
            ss.pop(k, None)
        welcome()
        st.stop()

    raw = up.getvalue()
    fhash = hashlib.md5(raw).hexdigest()

    with st.sidebar:
        if ss.get("_fkey") != fhash:
            ss["_fkey"] = fhash
            ss.pop("sheet", None)
        sheet = None
        if is_excel(up.name):
            try:
                sheet = st.selectbox("Sheet", list_sheets(raw, fhash), key="sheet")
            except Exception as e:  # noqa: BLE001
                st.error(f"Tidak bisa membuka Excel: {e}\n\n(.xls lama butuh `pip install xlrd`)")
                st.stop()
        skey = f"{fhash}|{sheet}"
        if ss.get("_skey") != skey:
            ss["_skey"] = skey
            try:
                ss["header_row"] = guess_header_row(read_preview(raw, fhash, up.name, sheet))
            except Exception:  # noqa: BLE001
                ss["header_row"] = 1
        header_row = int(st.number_input("Baris header (nama kolom)", min_value=1, max_value=200, step=1,
                                         key="header_row", help="Terdeteksi otomatis. Ubah jika file punya judul di atas tabel."))

    try:
        names, sample = read_header(raw, fhash, up.name, sheet, header_row)
    except Exception as e:  # noqa: BLE001
        st.error(f"Gagal membaca file: {e}")
        st.stop()
    if len(names) < 3:
        st.error("Kolom terbaca kurang dari 3. Periksa pilihan sheet / baris header di sidebar.")
        st.dataframe(sample.head(10), **WIDE)
        st.stop()

    # ---------- 2) Pemetaan kolom ----------
    sig0 = f"{fhash}|{sheet}|{header_row}"
    if ss.get("_msig") != sig0:
        ss["_msig"] = sig0
        ss["mapping"] = suggest_mapping(sample)
    apply_pending_profile(names)

    with st.sidebar:
        st.header("2️⃣ Pemetaan Kolom")
        st.caption("Terdeteksi otomatis — periksa, ubah bila perlu, lalu klik **Terapkan**.")
        options = [NONE] + list(names)
        with st.form("mapping_form"):
            chosen = {}
            for r in ROLES:
                cur = ss["mapping"].get(r.key) or NONE
                cur = cur if cur in options else NONE
                chosen[r.key] = st.selectbox(f"{r.label}{' *' if r.required else ''}", options,
                                             index=options.index(cur), key=f"map_{r.key}_{sig0}")
            dayfirst = st.checkbox("Format tanggal hari-dulu (31/12/2026)", value=ss["opts"]["dayfirst"])
            normalize = st.checkbox("Samakan huruf besar/kecil & spasi pada nama", value=ss["opts"]["normalize"])
            st.form_submit_button("✅ Terapkan pemetaan", type="primary")
    ss["mapping"] = {k: (None if v == NONE else v) for k, v in chosen.items()}
    ss["opts"] = {"dayfirst": dayfirst, "normalize": normalize}
    mapping = ss["mapping"]

    missing = [r.label for r in ROLES if r.required and not mapping[r.key]]
    used = [c for c in mapping.values() if c]
    dups = sorted({c for c in used if used.count(c) > 1})
    if missing or dups:
        if missing:
            st.warning("Kolom wajib belum dipetakan: **" + ", ".join(missing) + "**")
        if dups:
            st.error("Satu kolom dipakai untuk lebih dari satu peran: **" + ", ".join(dups) + "**")
        st.caption("Contoh 10 baris pertama file:")
        st.dataframe(sample.head(10), **WIDE)
        st.stop()

    # ---------- 3) Muat & bersihkan ----------
    try:
        positions = tuple(sorted(names.index(c) for c in set(used)))
        raw_df = load_columns(raw, fhash, up.name, sheet, header_row, names, positions)
        mkey = tuple(sorted((k, v) for k, v in mapping.items() if v))
        canon, report = build_canonical(raw_df, (fhash, sheet, header_row), mkey, dayfirst, normalize)
    except Exception as e:  # noqa: BLE001
        st.error(f"Gagal memproses data dengan pemetaan ini: {e}")
        st.stop()
    if canon.empty:
        st.error("Tidak ada baris dengan nilai penjualan yang valid. Periksa kolom *Nilai Penjualan*.")
        st.stop()

    has_date, has_city, has_qty = D in canon, C in canon, Q in canon
    sig = hashlib.md5(f"{sig0}|{mkey}".encode()).hexdigest()[:8]
    by_value = canon.groupby(S, observed=True)[V].sum().sort_values(ascending=False)
    sales_names = [str(x) for x in by_value.index]
    brand_names = [str(x) for x in canon.groupby(B, observed=True)[V].sum().sort_values(ascending=False).index]

    # ---------- Filter ----------
    with st.sidebar:
        st.header("3️⃣ Filter")
        mask = pd.Series(True, index=canon.index)
        date_note = ""
        if has_date and canon[D].notna().any():
            dmin, dmax = canon[D].min().date(), canon[D].max().date()
            rng = st.date_input("Periode", value=(dmin, dmax), min_value=dmin, max_value=dmax, key=f"f_date_{sig}")
            if isinstance(rng, (tuple, list)) and len(rng) == 2 and (rng[0], rng[1]) != (dmin, dmax):
                s0, e0 = pd.Timestamp(rng[0]), pd.Timestamp(rng[1]) + pd.Timedelta(days=1)
                mask &= (canon[D] >= s0) & (canon[D] < e0)
                date_note = f"{rng[0]:%d %b %Y} – {rng[1]:%d %b %Y}"
        sel_sales = st.multiselect("Sales", sales_names, key=f"f_sales_{sig}", placeholder="Semua sales")
        sel_brand = st.multiselect("Merk", brand_names, key=f"f_brand_{sig}", placeholder="Semua merk")
        sel_city = []
        if has_city:
            city_names = [str(x) for x in canon.groupby(C, observed=True)[V].sum().sort_values(ascending=False).index]
            sel_city = st.multiselect("Kota", city_names, key=f"f_city_{sig}", placeholder="Semua kota")
        if sel_sales:
            mask &= canon[S].isin(sel_sales)
        if sel_brand:
            mask &= canon[B].isin(sel_brand)
        if sel_city:
            mask &= canon[C].isin(sel_city)

    # ---------- 4) Target ----------
    with st.sidebar:
        targets = sidebar_targets(sales_names, brand_names)

    df = canon[mask]
    if df.empty:
        st.warning("Tidak ada data untuk kombinasi filter ini.")
        st.stop()

    allowed_sales = set(sel_sales) if sel_sales else set(sales_names)
    allowed_brand = set(sel_brand) if sel_brand else set(brand_names)
    t_sales = {k: v for k, v in targets["sales"].items() if k in allowed_sales}
    t_brand = {k: v for k, v in targets["brand"].items() if k in allowed_brand}

    # Target total: input manual > jumlah target sales > jumlah target merk
    if targets["total"] > 0:
        target_total, target_src = targets["total"], "input total"
    elif sum(t_sales.values()) > 0:
        target_total, target_src = sum(t_sales.values()), "jumlah target sales"
    elif sum(t_brand.values()) > 0:
        target_total, target_src = sum(t_brand.values()), "jumlah target merk"
    else:
        target_total, target_src = 0.0, ""

    # ---------- Agregasi (sekali, dipakai tab & ekspor) ----------
    tbl_sales = performance_table(df, S, ROLE["sales"].display, t_sales, allowed_sales, with_qty=has_qty)
    tbl_brand = performance_table(df, B, ROLE["brand"].display, t_brand, allowed_brand, with_qty=has_qty)
    tbl_city = performance_table(df, C, ROLE["city"].display, {}, None, with_qty=has_qty) if has_city else None
    pv = pivot_sales_brand(df)
    trend = monthly_trend(df) if has_date and df[D].notna().any() else None

    total = float(df[V].sum())
    pct = total / target_total * 100 if target_total > 0 else None

    # ---------- Ekspor ----------
    with st.sidebar:
        st.header("5️⃣ Ekspor & Profil")
        info = pd.DataFrame({"Keterangan": ["Total realisasi", "Target total", "Pencapaian (%)", "Periode filter"],
                             "Nilai": [total, target_total or None, round(pct, 1) if pct else None, date_note or "Semua"]})
        sheets = {"Info": info, "Sales": tbl_sales, "Merk": tbl_brand, "Sales x Merk": pv}
        if tbl_city is not None:
            sheets["Kota"] = tbl_city
        if trend is not None:
            sheets["Tren Bulanan"] = trend
        st.download_button("📥 Laporan Excel", to_excel_bytes(sheets), "laporan_sales.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.download_button("💾 Simpan profil client (JSON)",
                           json.dumps(build_profile(mapping), ensure_ascii=False, indent=2),
                           "profil_client.json", mime="application/json")

    # ======================================================================
    # TAMPILAN UTAMA
    # ======================================================================
    caption = f"File: **{up.name}**" + (f" · Sheet: **{sheet}**" if sheet else "") + f" · {len(df):,} baris".replace(",", ".")
    if date_note:
        caption += f" · Periode: {date_note}"
    st.caption(caption)

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Total Realisasi", fmt_compact(total), help=f"Rp {fmt_num(total)}")
    k2.metric("Target", fmt_compact(target_total) if target_total else "—",
              help=f"Sumber: {target_src}" if target_src else "Isi target di sidebar (4️⃣)")
    k3.metric("Pencapaian", f"{fmt_num(pct, 1)}%" if pct is not None else "—")
    k4.metric("Gap ke Target", fmt_compact(total - target_total) if target_total else "—")
    k5.metric("Sales aktif / Merk", f"{df[S].nunique()} / {df[B].nunique()}")
    if pct is not None:
        st.progress(min(pct / 100, 1.0), text=f"{fmt_num(pct, 1)}% dari target {fmt_compact(target_total)}")
    if target_total and (sel_sales or sel_brand or sel_city or date_note):
        st.caption("⚠️ Filter aktif: realisasi dihitung sesuai filter, sedangkan target tidak otomatis menyesuaikan.")

    labels = ["📊 Ringkasan", "👤 Sales", "🏷️ Merk", "🧩 Sales × Merk"]
    if tbl_city is not None:
        labels.append("🏙️ Kota")
    labels.append("🔍 Kualitas Data")
    tabs = st.tabs(labels)
    tab_iter = iter(tabs)

    with next(tab_iter):  # Ringkasan
        c1, c2 = st.columns([3, 2])
        with c1:
            bar_chart(tbl_sales, "Sales", 15, "Top 15 Sales")
        with c2:
            donut_chart(tbl_brand, "Merk", "Kontribusi Merk")
        if trend is not None:
            fig = px.bar(trend, x="Bulan", y="Realisasi", title="Tren Realisasi Bulanan")
            fig.update_layout(height=340, margin=dict(l=10, r=10, t=50, b=10))
            st.plotly_chart(fig, **WIDE)
        else:
            st.caption("Tren bulanan muncul jika kolom *Tanggal Transaksi* dipetakan.")

    with next(tab_iter):  # Sales
        show_table(tbl_sales, money_cols=("Realisasi", "Target", "Gap"), height=520)

    with next(tab_iter):  # Merk
        c1, c2 = st.columns([3, 2])
        with c1:
            show_table(tbl_brand, money_cols=("Realisasi", "Target", "Gap"), height=520)
        with c2:
            bar_chart(tbl_brand, "Merk", 12, "Realisasi vs Target per Merk")

    with next(tab_iter):  # Sales x Merk
        st.caption("Realisasi (Rp) per Sales × Merk")
        pv_view = pv.reset_index()
        show_table(pv_view, money_cols=[c for c in pv_view.columns if c != "Sales"], height=480)
        top = pv.drop(index="TOTAL").drop(columns="TOTAL").head(20)
        fig = px.imshow(top, aspect="auto", color_continuous_scale="Blues", title="Heatmap Top 20 Sales × Merk")
        fig.update_layout(height=520, margin=dict(l=10, r=10, t=50, b=10), coloraxis_showscale=False)
        st.plotly_chart(fig, **WIDE)

    if tbl_city is not None:
        with next(tab_iter):  # Kota
            c1, c2 = st.columns([3, 2])
            with c1:
                show_table(tbl_city.drop(columns=["Target", "Pencapaian (%)", "Gap", "Status"]),
                           money_cols=("Realisasi",), height=520)
            with c2:
                bar_chart(tbl_city, "Kota", 15, "Top 15 Kota")

    with next(tab_iter):  # Kualitas data
        st.subheader("Pemetaan yang dipakai")
        st.dataframe(pd.DataFrame({"Peran": [r.label for r in ROLES],
                                   "Kolom di file client": [mapping[r.key] or "—" for r in ROLES]}),
                     hide_index=True, **WIDE)
        st.subheader("Laporan kualitas")
        m1, m2, m3 = st.columns(3)
        m1.metric("Baris di file", fmt_num(report["baris_file"]))
        m2.metric("Baris dipakai", fmt_num(report["baris_dipakai"]))
        m3.metric("Baris dibuang (nilai tidak valid)", fmt_num(report["baris_nilai_tidak_valid"]))
        notes = []
        if report["baris_nilai_negatif"]:
            notes.append(f"{fmt_num(report['baris_nilai_negatif'])} baris bernilai negatif (retur?) — tetap dihitung.")
        if report.get("baris_tanggal_tidak_valid"):
            notes.append(f"{fmt_num(report['baris_tanggal_tidak_valid'])} baris tanggal tidak terbaca — "
                         "tidak ikut saat filter periode aktif. Coba ubah opsi format tanggal.")
        for key in ("sales", "brand", "city", "outlet"):
            n = report.get(f"kosong_{key}", 0)
            if n:
                notes.append(f"{fmt_num(n)} baris tanpa {ROLE[key].label} → dikelompokkan sebagai “{UNKNOWN}”.")
        for n in notes:
            st.warning(n)
        if not notes:
            st.success("Tidak ada masalah kualitas data yang terdeteksi.")
        st.caption("Contoh data setelah dibersihkan:")
        show = canon.head(100).rename(columns={r.internal: r.display for r in ROLES})
        st.dataframe(show, hide_index=True, **WIDE)


if __name__ == "__main__":
    main()
