"""
market_panel.py - FASE 6C: panel dashboard "Pasar & Forecast".

Dua grafik, keduanya memanggil ALAT YANG SAMA dengan Asisten AI (ai_tools.jalankan_tool), jadi angka di panel dan
di chat selalu identik dan hak akses (TLS + RLS) ikut berlaku:
  1. Tren pasar vs internal  -> tren_pasar_vs_internal   (indeks pencarian relatif vs omset mingguan per Merk)
  2. Forecast omset          -> prediksi_omset           (riwayat, prakiraan, interval 80%, baseline naif musiman)

Kecepatan dashboard: tab Streamlit tidak malas (semua tab dijalankan di setiap rerun), jadi perhitungan HANYA
dijalankan saat tombol ditekan dan hasilnya disimpan di st.session_state. Tarikan Supabase di-cache 1 jam dan hasil
forecast 6 jam di dalam alatnya; rerun karena widget lain tidak memicu perhitungan ulang.
"""
from __future__ import annotations

import functools
import logging
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import ai_forecast  # noqa: F401  (import mendaftarkan prediksi_omset)
import ai_market_tools  # noqa: F401  (import mendaftarkan alat pasar)
import ai_chat
import ai_pesaing
import daftar_pesaing
import ai_tools
import trends_fallback
_log = logging.getLogger("market_panel")
LABEL_DUGAAN = "Dugaan yang perlu dicek - bukan bukti sebab-akibat."

def _jalankan(nama: str, df: pd.DataFrame, role: str, name: str, cfg: ai_tools.ToolsConfig, **arg) -> dict:
    return ai_tools.jalankan_tool(nama, arg, df=df, role=role, sales_name=name, config=cfg)


def _pct(x: Optional[float]) -> str:
    return "-" if x is None else f"{x:+.2f}%"


# ----------------------------------------------------------------------------- grafik (fungsi murni: dict -> Figure)
def grafik_pasar_vs_internal(h: dict) -> go.Figure:
    s = pd.DataFrame(h["seri_mingguan"])
    s["minggu"] = pd.to_datetime(s["minggu"])
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_bar(x=s["minggu"], y=s["omset"], name="Omset internal (Rp)", opacity=0.55, secondary_y=False,
                customdata=s["omset_rp"], hovertemplate="%{customdata}<extra>Omset</extra>")
    fig.add_scatter(x=s["minggu"], y=s["indeks_pasar"], name="Indeks pencarian pasar (relatif 0-100)", mode="lines+markers",
                    secondary_y=True)
    fig.update_layout(title=f"{h['merk']}: pasar vs internal, {h['n_minggu_sama']} minggu yang sama",
                      legend=dict(orientation="h", y=-0.2), margin=dict(t=50, b=10))
    fig.update_yaxes(title_text="Omset per minggu (Rp)", secondary_y=False)
    fig.update_yaxes(title_text="Indeks pencarian (relatif)", secondary_y=True, showgrid=False)
    return fig


def grafik_forecast(h: dict) -> go.Figure:
    riw = pd.DataFrame(h["riwayat_mingguan"])
    riw["minggu"] = pd.to_datetime(riw["minggu"])
    pra = pd.DataFrame(h["prakiraan"])
    pra["minggu_mulai"] = pd.to_datetime(pra["minggu_mulai"])
    base = pd.DataFrame(h["prakiraan_baseline_naif_musiman"])
    base["minggu_mulai"] = pd.to_datetime(base["minggu_mulai"])
    tingkat = h["metode"]["interval_tingkat_persen"]
    # titik sambung: nilai riwayat terakhir agar garis tidak terputus
    x_sambung, y_sambung = riw["minggu"].iloc[-1], riw["omset"].iloc[-1]
    fig = go.Figure()
    fig.add_scatter(x=riw["minggu"], y=riw["omset"], name="Riwayat omset mingguan", mode="lines")
    fig.add_scatter(x=list(pra["minggu_mulai"]) + list(pra["minggu_mulai"])[::-1],
                    y=list(pra["atas"]) + list(pra["bawah"])[::-1], fill="toself", mode="lines",
                    line=dict(width=0), opacity=0.25, name=f"Interval {tingkat}%", hoverinfo="skip")
    fig.add_scatter(x=[x_sambung] + list(pra["minggu_mulai"]), y=[y_sambung] + list(pra["titik"]), name=f"Prakiraan ({h['metode']['model']})",
                    mode="lines+markers", customdata=[""] + list(pra["titik_rp"]), hovertemplate="%{customdata}<extra>Prakiraan</extra>")
    fig.add_scatter(x=[x_sambung] + list(base["minggu_mulai"]), y=[y_sambung] + list(base["titik"]),
                    name="Baseline naif musiman", mode="lines", line=dict(dash="dash"))
    fig.update_layout(title=f"Prakiraan omset mingguan - {h['cakupan']}", yaxis_title="Omset per minggu (Rp)",
                      legend=dict(orientation="h", y=-0.2), margin=dict(t=50, b=10))
    return fig

def grafik_pesaing(d: pd.DataFrame, merk: str) -> go.Figure:
    """Indeks pencarian Google mingguan: Merk vs pesaing, diambil dalam SATU permintaan (skala sama)."""
    g = d.assign(minggu=d["series_date"] - pd.to_timedelta(d["series_date"].dt.weekday, unit="D"))
    g = g.groupby(["minggu", "brand"])["value"].agg(["mean", "count"]).reset_index()
    g = g[g["count"] >= 4]                                  # hanya minggu lengkap
    fig = go.Figure()
    for b, s in g.groupby("brand"):
        fig.add_scatter(x=s["minggu"], y=s["mean"].round(1), name=b, mode="lines+markers",
                        line=dict(width=4 if b == merk else 2))
    fig.update_layout(title=f"{merk} vs pesaing: indeks pencarian Google (relatif, skala sama)",
                      yaxis_title="Indeks pencarian (0-100)", legend=dict(orientation="h", y=-0.2),
                      margin=dict(t=50, b=10))
    return fig

def _aman(fn):
    """Panel tidak boleh menampilkan error merah: galat tak terduga -> st.info."""
    @functools.wraps(fn)
    def pembungkus(*a, **k):
        try:
            fn(*a, **k)
        except Exception as e:  # noqa: BLE001
            _log.warning("Panel %s gagal (tipe=%s)", fn.__name__, type(e).__name__)
            st.info("Data pasar sedang tidak dapat ditampilkan. Bagian lain dashboard tetap berfungsi; "
                    "coba lagi beberapa saat lagi.")
    return pembungkus

# ----------------------------------------------------------------------------- panel
def _simpan(kunci: str, nilai: dict) -> None:
    st.session_state.setdefault("_fase6_hasil", {})[kunci] = nilai


def _ambil(kunci: str) -> Optional[dict]:
    return st.session_state.get("_fase6_hasil", {}).get(kunci)


def _tampilkan_penolakan(h: dict) -> None:
    kode = h.get("kode", "")
    (st.warning if kode in ("histori_pasar_pendek", "histori_pendek", "deret_jarang") else st.info)(h.get("pesan", "Tidak dapat dihitung."))
    if "histori_hari" in h:
        st.caption(f"Histori data pasar: {h['histori_hari']} hari (ambang {h.get('ambang_histori_hari', '-')} hari).")
    if "n_minggu" in h:
        st.caption(f"Histori omset: {h['n_minggu']} minggu (minimum {h.get('minimum_minggu', '-')} minggu).")

# ----------------------------------------------------------------------------- riset pesaing AI (FASE 6D)
RISET_AWAL_OTOMATIS = False   # True: Merk awal juga diriset saat dashboard dibuka (lebih lambat)
MAKS_RISET_SESI = 15          # pagar biaya: pencarian BARU (bukan dari cache) per sesi login
LABEL_RISET = ("Daftar pesaing dari pengetahuan AI (tanpa pencarian web), bukan data internal dan bisa tidak lengkap. "
               "Periksa dan edit sebelum dipakai untuk keputusan.")


def _aktifkan_riset() -> None:
    """on_change pemilih Merk: mulai sekarang Merk yang dipilih otomatis diriset (hasil di-cache beberapa jam)."""
    st.session_state["f6_riset_aktif"] = True


def _paksa_riset() -> None:
    st.session_state["f6_riset_aktif"] = True
    st.session_state["f6_riset_paksa"] = True


def _klien_gemini() -> tuple:
    kunci = ai_chat._baca_secret("GEMINI_API_KEY")
    if not kunci:
        return None, ai_pesaing.MODEL_DEFAULT
    return ai_chat._buat_client(kunci), (ai_chat._baca_secret("GEMINI_MODEL", ai_chat.MODEL_DEFAULT) or ai_chat.MODEL_DEFAULT)


def _riset_otomatis(merk: str) -> Optional[ai_pesaing.HasilPesaing]:
    """None = belum dipicu. Selain itu HasilPesaing (ok atau gagal dengan pesan ramah). Tidak pernah melempar error."""
    paksa = bool(st.session_state.pop("f6_riset_paksa", False))
    if not (paksa or st.session_state.get("f6_riset_aktif") or RISET_AWAL_OTOMATIS):
        return None
    klien, model = _klien_gemini()
    if klien is None:
        return ai_pesaing.HasilPesaing.gagal("tanpa_kunci", "Riset pesaing AI belum aktif: GEMINI_API_KEY belum diatur di "
                                                            "secrets. Isi kolom Pesaing secara manual.")
    if not paksa and ai_pesaing.di_cache(merk, model):
        return ai_pesaing.cari_pesaing(merk, klien, model)                       # cache: instan, tanpa biaya
    n = st.session_state.get("f6_riset_n", 0)
    if n >= MAKS_RISET_SESI:
        return ai_pesaing.HasilPesaing.gagal("batas_sesi", f"Batas {MAKS_RISET_SESI} riset baru per sesi tercapai. Login ulang "
                                                           "untuk melanjutkan, atau isi kolom Pesaing secara manual.")
    with st.spinner(f"AI menyusun daftar pesaing {merk} di pasar Indonesia..."):
        hasil = ai_pesaing.cari_pesaing(merk, klien, model, paksa=paksa)
    if not hasil.dari_cache:
        st.session_state["f6_riset_n"] = n + 1
    return hasil


@st.cache_resource(show_spinner=False)
def _buka_sheet_daftar() -> daftar_pesaing.SheetDaftar:
    import gspread
    gc = gspread.service_account_from_dict(dict(st.secrets["gcp_service_account"]))
    sh = gc.open_by_key(st.secrets["TASK_SHEET_ID"])
    try:
        ws = sh.worksheet(daftar_pesaing.NAMA_SHEET)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=daftar_pesaing.NAMA_SHEET, rows=200, cols=len(daftar_pesaing.HEADERS))
        ws.update(range_name="A1:B1", values=[daftar_pesaing.HEADERS])
    return daftar_pesaing.SheetDaftar(ws)


@st.cache_data(ttl=120, show_spinner=False)
def _baca_sheet_daftar(versi: int) -> dict:
    return _buka_sheet_daftar().baca()


def _baca_sheet() -> dict:
    return _baca_sheet_daftar(st.session_state.get("f6_sheet_v", 0))


def _laporan_pesaing(merk: str, d: Optional[daftar_pesaing.DaftarPesaing]) -> None:
    if d is None:
        st.caption("Pilih Merk, atau tekan 'Riset pesaing dengan AI', untuk menyusun daftar pesaing di pasar Indonesia.")
        return
    if d.catatan:
        st.info(d.catatan)          # mis. "Kuota/batas permintaan Gemini sedang penuh... Memakai daftar tetap di Google Sheet"
    st.caption(f"Sumber daftar pesaing: {d.label}. " + (LABEL_RISET if d.asal in ("gemini", "cache_lama") else
                                                       "Daftar ini bisa diedit di kolom di atas."))


def _editor_daftar(role: str, merk: str, pesaing: list[str]) -> None:
    """Hanya manager/direktur (termasuk fauziah) yang boleh menyimpan daftar tetap ke Google Sheet."""
    if not daftar_pesaing.boleh_edit(role):
        return
    with st.expander("Simpan sebagai daftar tetap (cadangan bila Gemini penuh)"):
        st.caption(f"Menyimpan isi kolom Pesaing saat ini untuk {merk} ke sheet '{daftar_pesaing.NAMA_SHEET}'.")
        if st.button("Simpan daftar tetap", key=f"f6_simpan_{merk}", disabled=not pesaing):
            try:
                _buka_sheet_daftar().simpan(merk, pesaing)
                st.session_state["f6_sheet_v"] = st.session_state.get("f6_sheet_v", 0) + 1
                st.success("Daftar tetap tersimpan.")
            except Exception as e:  # noqa: BLE001
                _log.warning("Simpan daftar pesaing gagal (tipe=%s)", type(e).__name__)
                st.error("Gagal menyimpan ke Google Sheet. " + daftar_pesaing.jelaskan_galat(e))


@_aman
def _panel_pasar(df: pd.DataFrame, role: str, name: str, cfg: ai_tools.ToolsConfig, merks: list[str]) -> None:
    st.caption("Menyandingkan indeks minat pencarian pasar dengan omset internal pada minggu yang sama. "
               + LABEL_DUGAAN)
    c1, c2 = st.columns([2, 1])
    merk = c1.selectbox("Merk", merks, key="f6_pasar_merk", on_change=_aktifkan_riset)
    muat = c2.button("Muat tren pasar", key="f6_pasar_btn", use_container_width=True)
    riset = _riset_otomatis(merk)                       # None bila belum dipicu; hasil di-cache beberapa jam
    daftar = daftar_pesaing.tentukan(merk, riset, _baca_sheet)
    kunci_pesaing, kunci_seed = f"f6_pesaing_{merk}", f"f6_seed_{merk}"
    # isi kolom HANYA sebelum widget dibuat; hasil Gemini baru menimpa isian lama, selain itu hanya mengisi yang kosong
    if riset is not None and riset.ok:
        if st.session_state.get(kunci_seed) != riset.dibuat or kunci_pesaing not in st.session_state:
            st.session_state[kunci_pesaing] = ", ".join(daftar.pesaing)
            st.session_state[kunci_seed] = riset.dibuat
    elif kunci_pesaing not in st.session_state:
        st.session_state[kunci_pesaing] = ", ".join(daftar.pesaing)
    st.button("🔎 Riset pesaing dengan AI", key="f6_riset_btn", on_click=_paksa_riset,
              help="Meminta AI menyebut pesaing (tanpa pencarian web). Hasil disimpan beberapa jam; mengulang "
                   "baru bisa bila hasil lama berumur lebih dari 10 menit.")
    teks_pesaing = st.text_input("Pesaing untuk dibandingkan (pisahkan dengan koma, maksimal 4)", key=kunci_pesaing,
                                 help="Terisi otomatis oleh riset AI dan bisa diedit. Kosongkan bila tidak perlu pembanding.")
    pesaing = trends_fallback.bersihkan_pesaing(teks_pesaing, merk)
    _laporan_pesaing(merk, daftar if riset is not None else None)
    _editor_daftar(role, merk, pesaing)
    kunci = f"pasar::{role}::{name}::{merk}::{'|'.join(pesaing)}"
    if muat:
        with st.spinner("Mengambil data pasar (Google Trends bisa memakan waktu beberapa detik)..."):
            with trends_fallback.konteks_merk(ai_market_tools, merk, pesaing):
                hasil = _jalankan("tren_pasar_vs_internal", df, role, name, cfg, merk=merk)
            _simpan(kunci, hasil)
            if hasil.get("ok") or hasil.get("kode") == "tanpa_pemetaan":   # hasil live sudah di-cache, tidak mengambil ulang
                live = trends_fallback.ambil_tren_google(trends_fallback.peta_merk(merk, pesaing))
                _simpan(kunci + "::live", {"df": live.df, "ok": live.ok, "pesan": live.pesan})
    h = _ambil(kunci)
    live = _ambil(kunci + "::live")
    if h is None:
        st.info("Tekan 'Muat tren pasar' untuk mengambil data (tidak dihitung otomatis agar dashboard tetap cepat).")
        return
    if not h.get("ok"):
        if h.get("kode") == "tanpa_pemetaan" and live and live["pesan"]:
            st.info(live["pesan"])                  # Google dibatasi / tidak ada data -> pesan yang benar
        else:
            _tampilkan_penolakan(h)
        return
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Minggu yang sama", h["n_minggu_sama"])
    m2.metric("Perubahan indeks pasar", _pct(h["perubahan_indeks_pasar_persen"]))
    m3.metric("Perubahan omset internal", _pct(h["perubahan_omset_internal_persen"]))
    m4.metric("Arah", h["arah_perubahan"].split(" (")[0])
    st.plotly_chart(grafik_pasar_vs_internal(h), use_container_width=True)
    if pesaing and live and live["ok"] and len(live["df"]):
        st.plotly_chart(grafik_pesaing(live["df"], merk), use_container_width=True)
    elif pesaing:
        st.info("Pembanding pesaing belum tersedia: Google Trends sedang dibatasi atau tidak ada data. Coba lagi nanti.")
    st.warning(f"{h['pernyataan']}  Kekuatan bukti: {h['kekuatan_bukti']}. "
               f"Histori pasar: {h['status_data']['histori_hari']} hari.")
    with st.expander("Batasan & catatan data"):
        for t in list(h["batasan"]) + list(h["catatan"]):
            st.markdown(f"- {t}")
        if h.get("korelasi_perubahan_mingguan") is not None:
            st.markdown(f"- Korelasi peringkat perubahan mingguan: {h['korelasi_perubahan_mingguan']} "
                        f"(n={h['n_pasangan_korelasi']}); sangat tidak stabil bila n kecil.")

@_aman
def _panel_forecast(df: pd.DataFrame, role: str, name: str, cfg: ai_tools.ToolsConfig, merks: list[str]) -> None:
    st.caption("Prakiraan omset mingguan (ETS, statsmodels) dengan backtest rolling-origin dan pembanding baseline naif "
               "musiman. Prakiraan bukan target atau janji.")
    c1, c2, c3 = st.columns([2, 1, 1])
    pilihan = c1.selectbox("Cakupan", ["(Semua yang dapat diakses)"] + merks, key="f6_fc_merk")
    horizon = c2.slider("Horizon (minggu)", 1, 8, 4, key="f6_fc_h")
    hitung = c3.button("Hitung forecast", key="f6_fc_btn", use_container_width=True)
    merk: Optional[str] = None if pilihan.startswith("(") else pilihan
    kunci = f"fc::{role}::{name}::{merk}::{horizon}"
    if hitung:
        with st.spinner("Menghitung backtest dan prakiraan..."):
            _simpan(kunci, _jalankan("prediksi_omset", df, role, name, cfg, merk=merk, horizon_minggu=horizon))
    h = _ambil(kunci)
    if h is None:
        st.info("Tekan 'Hitung forecast' untuk menjalankan (tidak dihitung otomatis agar dashboard tetap cepat).")
        return
    if not h.get("ok"):
        _tampilkan_penolakan(h)
        return
    bt = h["backtest"]
    (st.success if h["mengalahkan_baseline_naif_musiman"] else st.error)(h["kesimpulan"])
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("sMAPE model", f"{bt['smape_model_persen']:.2f}%")
    m2.metric("sMAPE baseline naif musiman", f"{bt['smape_baseline_naif_musiman_persen']:.2f}%")
    m3.metric("Cakupan interval", f"{bt['cakupan_interval_persen']:.1f}%")
    m4.metric("Total prakiraan", h["total_horizon_titik_rp"])
    st.plotly_chart(grafik_forecast(h), use_container_width=True)
    tabel = pd.DataFrame([{"Minggu mulai": str(p["minggu_mulai"])[:10], "Prakiraan": p["titik_rp"], "Bawah": p["bawah_rp"],
                           "Atas": p["atas_rp"]} for p in h["prakiraan"]])
    st.dataframe(tabel, hide_index=True, use_container_width=True)
    with st.expander("Metode, backtest, dan catatan"):
        mt = h["metode"]
        st.markdown(f"- Model: **{mt['model']}** ({mt['pemilihan']}). Interval {mt['interval_tingkat_persen']}% "
                    f"({mt['interval_metode']}).")
        st.markdown(f"- Backtest {bt['jenis']}: {bt['n_lipatan']} lipatan, horizon {bt['horizon_minggu']} minggu, uji "
                    f"{str(bt['periode_uji_awal'])[:10]} s/d {str(bt['periode_uji_akhir'])[:10]}.")
        st.markdown(f"- MAPE model {bt['mape_model_persen']}%, baseline naif {bt['mape_baseline_naif_persen']}%, "
                    f"baseline naif musiman {bt['mape_baseline_naif_musiman_persen']}%. "
                    f"Periode musiman baseline: {mt['periode_musiman_baseline_minggu']} minggu "
                    f"(tahunan teruji: {'ya' if mt['musiman_tahunan_teruji'] else 'belum'}).")
        for t in h["catatan"]:
            st.markdown(f"- {t}")


def render_pasar_forecast(df: pd.DataFrame, role: str, name: str, cfg: ai_tools.ToolsConfig) -> None:
    """Titik masuk dari app.py."""
    st.subheader("🌐 Pasar & Forecast")
    try:
        konteks = ai_tools.konteks_data(df, role, name, cfg)
    except ai_tools.ToolError as e:
        st.info(str(e))
        return
    trends_fallback.pasang_fallback(ai_market_tools)  # Supabase dulu, Google Trends cadangan
    merks = konteks["merk"]
    if not merks:
        st.info("Belum ada Merk pada data yang dapat Anda akses.")
        return
    for c in konteks.get("catatan", []):
        st.caption(c)
    tab_pasar, tab_fc = st.tabs(["Tren pasar vs internal", "Forecast omset"])
    with tab_pasar:
        _panel_pasar(df, role, name, cfg, merks)
    with tab_fc:
        _panel_forecast(df, role, name, cfg, merks)
