"""
ai_tasks_eval_ui.py - FASE 4: Panel "📈 Evaluasi Tugas" (Streamlit).

Pemakaian di app.py (lihat patch):
    import ai_tasks_eval_ui
    ai_tasks_eval_ui.daftarkan_penyedia()                      # SEBELUM tab Asisten AI -> alat evaluasi bisa membaca Sheet
    ai_tasks_eval_ui.render_evaluasi(df, user_role, user_name, cfg_ai)

Semua angka dihitung ai_tasks_eval (fungsi murni). File ini hanya menampilkan. Hak akses sudah diterapkan di dalam
fungsi hitung; tampilan per sales hanya digambar bila hasil menyertakannya (approver).
Koneksi Sheet memakai ai_tasks_ui._buka_store (secrets: gcp_service_account, TASK_SHEET_ID) -> tidak ada secret baru.
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd
import streamlit as st

import ai_tasks as K
import ai_tasks_eval as E
import ai_tools


# =============================================================================
# Sumber data tugas (dipakai panel DAN alat Asisten AI)
# =============================================================================
@st.cache_data(ttl=60, show_spinner=False)
def _baca_tugas(_store: Any) -> pd.DataFrame:
    return _store.baca()


def _ambil_tugas() -> pd.DataFrame:
    import ai_tasks_ui                      # koneksi Sheet yang sama dengan Fase 3
    return _baca_tugas(ai_tasks_ui._buka_store())


def daftarkan_penyedia() -> None:
    """Pasang pembaca Sheet untuk alat AI. Aman dipanggil berulang. Sheet bermasalah -> alat membalas galat ramah."""
    E.pasang_penyedia(_ambil_tugas)


# =============================================================================
# Hitung (di-cache; kunci memuat role + nama sehingga hasil tiap akses terpisah)
# =============================================================================
def _sidik(tugas: pd.DataFrame, df: pd.DataFrame) -> tuple:
    h = int(pd.util.hash_pandas_object(tugas.astype(str), index=False).sum()) if len(tugas) else 0
    return (h, len(df), str(df["Tanggal"].max()), round(float(df["Jumlah"].sum()), 2))


@st.cache_data(ttl=300, show_spinner=False, max_entries=30)
def _hitung(_tugas: pd.DataFrame, _df: pd.DataFrame, sidik: tuple, role: str, sales_name: str,
            _cfg: ai_tools.ToolsConfig, n_hari: int, jenis: Optional[str]) -> dict:
    return {
        "efektivitas": E.efektivitas_tugas(_tugas, _df, role, sales_name, _cfg, n_hari=n_hari, jenis=jenis),
        "kinerja": E.kinerja_penyelesaian_tugas(_tugas, role, sales_name, _cfg, jenis=jenis),
        "terbuka": E.ringkasan_tugas_terbuka(_tugas, role, sales_name, _cfg, jenis=jenis),
    }


# =============================================================================
# Potongan tampilan
# =============================================================================
def _p(x: Optional[float]) -> str:
    return E.BELUM_CUKUP if x is None else f"{x:g}%"


def _tabel_efek(baris: list[dict], kolom_nama: str, kunci: str) -> pd.DataFrame:
    out = []
    for x in baris:
        ok = x["kesimpulan"] != E.BELUM_CUKUP
        out.append({kolom_nama: x[kunci], "Tugas dinilai": x["n_tugas_dievaluasi"],
                    "Order lagi (bertugas)": _p(x.get("win_back_tugas_persen")) if ok else E.BELUM_CUKUP,
                    "Order lagi (pembanding)": _p(x.get("win_back_kontrol_persen")) if ok else E.BELUM_CUKUP,
                    "Selisih (poin)": x.get("selisih_poin") if ok else None,
                    "Outlet pembanding": x["n_kontrol_outlet_unik"], "Kesimpulan": x["kesimpulan"]})
    return pd.DataFrame(out)


def _tabel_kinerja(baris: list[dict], kolom_nama: str, kunci: str) -> pd.DataFrame:
    return pd.DataFrame([{
        kolom_nama: x[kunci], "Ditugaskan": x["n_ditugaskan"], "Selesai": x["n_selesai"],
        "Rasio selesai": _p(x["rasio_selesai_persen"]), "Selesai dari jatuh tempo": _p(x["rasio_selesai_jatuh_tempo_persen"]),
        "Tepat waktu": _p(x["tepat_waktu_persen"]),
        "Rata-rata hari setuju → selesai": x["rata_hari_persetujuan_ke_selesai"] if x["rata_hari_persetujuan_ke_selesai"] is not None else E.BELUM_CUKUP,
        "Lewat tenggat (berjalan)": x["n_berjalan_lewat_tenggat"]} for x in baris])


def _grafik_tren(per_bulan: list[dict]) -> None:
    titik = [x for x in per_bulan if x.get("kesimpulan") != E.BELUM_CUKUP]
    if not titik:
        st.caption("Belum ada bulan dengan sampel cukup untuk digambar (lihat tabel di bawah).")
        return
    import plotly.graph_objects as go
    x_ = [t["bulan"] for t in titik]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x_, y=[t["win_back_tugas_persen"] for t in titik], mode="lines+markers", name="Toko bertugas",
                             text=[f"n={t['n_sebanding']} tugas" for t in titik], hovertemplate="%{x}: %{y}% (%{text})<extra></extra>"))
    fig.add_trace(go.Scatter(x=x_, y=[t["win_back_kontrol_persen"] for t in titik], mode="lines+markers", name="Toko serupa tanpa tugas",
                             text=[f"n={t['n_kontrol_outlet_unik']} outlet" for t in titik], hovertemplate="%{x}: %{y}% (%{text})<extra></extra>"))
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10), yaxis_title="% order lagi", yaxis_range=[0, 100],
                      legend=dict(orientation="h", y=1.12))
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Bulan tanpa titik = belum cukup data. Bulan = bulan tugas ditandai selesai.")


def _bagian_efektivitas(h: dict, akses: ai_tools.Akses) -> None:
    if not h.get("ok"):
        st.warning(h.get("pesan", "Efektivitas belum bisa dihitung."))
        return
    r = h["ringkasan"]
    st.caption(h["definisi"])
    c = st.columns(4)
    c[0].metric("Tugas dinilai", r["n_tugas_dievaluasi"], help=f"Tugas selesai dengan jendela {h['jendela_hari']} hari lengkap. "
                f"Belum matang: {h['belum_matang']}. Selesai tanpa tanggal: {h['selesai_tanpa_tanggal']}.")
    if r["kesimpulan"] == E.BELUM_CUKUP:
        c[1].metric("Toko pembanding", r["n_kontrol_outlet_unik"])
        st.info(r["penafsiran"])
    else:
        c[1].metric("Order lagi: bertugas", f"{r['win_back_tugas_persen']:g}%", help=f"n = {r['n_sebanding']} tugas")
        c[2].metric("Order lagi: serupa tanpa tugas", f"{r['win_back_kontrol_persen']:g}%",
                    help=f"n = {r['n_kontrol_outlet_unik']} outlet unik")
        c[3].metric("Selisih (poin)", f"{r['selisih_poin']:+g}", help=f"Kisaran ~95%: {r['ci95_selisih_poin'][0]:g} s/d {r['ci95_selisih_poin'][1]:g}")
        st.markdown(f"**{r['penafsiran']}**")
        a, b = st.columns(2)
        a.metric("Omset pasca-kunjungan (rata-rata/tugas)", r["omset_pasca_rata_tugas_rp"])
        b.metric("Omset periode sama, toko pembanding (rata-rata/outlet)", r["omset_pasca_rata_kontrol_rp"])
        if r.get("sebelum_sesudah"):
            s = r["sebelum_sesudah"]
            st.caption(f"Pelengkap (tanpa pembanding, n={s['n']}): omset rata-rata {s['omset_sebelum_rata_rp']} sebelum → "
                       f"{s['omset_sesudah_rata_rp']} sesudah. {s['catatan']}")
    st.caption("⚠️ " + h["peringatan"])

    st.markdown("**Tren win-back per bulan**")
    _grafik_tren(h["per_bulan"])
    if h["per_bulan"]:
        st.dataframe(_tabel_efek(h["per_bulan"], "Bulan", "bulan"), hide_index=True, use_container_width=True)
    st.markdown("**Per jenis tugas**")
    if h["per_jenis"]:
        st.dataframe(_tabel_efek(h["per_jenis"], "Jenis", "label"), hide_index=True, use_container_width=True)
    else:
        st.caption("Belum ada tugas selesai yang bisa dinilai.")
    if h["tidak_dievaluasi"]:
        st.caption("Tidak dievaluasi (belum ada definisi pembanding): " + ", ".join(
            f"{K.JENIS_TUGAS.get(j, j)} ({n})" for j, n in h["tidak_dievaluasi"].items()))
    if h.get("per_sales_tersedia") and akses.level in ("penuh", "supervisor"):      # hanya approver
        st.markdown("**Per sales** (hanya approver)")
        if h["per_sales"]:
            st.dataframe(_tabel_efek(h["per_sales"], "Sales", "sales"), hide_index=True, use_container_width=True)
        else:
            st.caption("Belum ada data per sales.")
    else:
        st.caption("Rincian per sales hanya tersedia untuk approver.")


def _bagian_kinerja(k: dict, o: dict, akses: ai_tools.Akses) -> None:
    st.markdown("### ✅ Kinerja penyelesaian tugas")
    if not k.get("ok"):
        st.warning(k.get("pesan", "Kinerja belum bisa dihitung."))
    else:
        r = k["ringkasan"]
        c = st.columns(4)
        c[0].metric("Rasio selesai", _p(r["rasio_selesai_persen"]), help=f"{r['n_selesai']} dari {r['n_ditugaskan']} tugas disetujui")
        c[1].metric("Selesai dari yang jatuh tempo", _p(r["rasio_selesai_jatuh_tempo_persen"]),
                    help=f"{r['n_jatuh_tempo_selesai']} dari {r['n_jatuh_tempo']} tugas jatuh tempo")
        c[2].metric("Tepat waktu", _p(r["tepat_waktu_persen"]), help=f"n = {r['n_ketepatan_waktu_dinilai']} tugas selesai ber-tanggal")
        c[3].metric("Hari setuju → selesai", r["rata_hari_persetujuan_ke_selesai"] if r["rata_hari_persetujuan_ke_selesai"] is not None else E.BELUM_CUKUP,
                    help=f"rata-rata; n = {r['n_durasi_dinilai']}")
        if r["status_sampel"] == E.BELUM_CUKUP:
            st.caption(f"Sampel {r['n_ditugaskan']} tugas: rasio belum ditampilkan sampai cukup data.")
        if k["per_jenis"]:
            st.dataframe(_tabel_kinerja(k["per_jenis"], "Jenis", "label"), hide_index=True, use_container_width=True)
        if k.get("per_sales_tersedia") and akses.level in ("penuh", "supervisor") and k["per_sales"]:
            st.markdown("**Per sales** (hanya approver)")
            st.dataframe(_tabel_kinerja(k["per_sales"], "Sales", "sales"), hide_index=True, use_container_width=True)
        for cat in k["catatan"][:1]:
            st.caption("ℹ️ " + cat)

    st.markdown("### 📋 Tugas terbuka")
    if not o.get("ok"):
        st.warning(o.get("pesan", "Ringkasan tugas terbuka belum bisa dihitung."))
        return
    c = st.columns(4)
    c[0].metric("Terbuka", o["jumlah_terbuka"])
    c[1].metric("Lewat tenggat", o["terlambat"])
    c[2].metric("Segera jatuh tempo", o["segera_jatuh_tempo"])
    c[3].metric("Usulan lama menunggu", o["usulan_menunggu_lama"])
    if o["daftar_terlambat"]:
        st.dataframe(pd.DataFrame(o["daftar_terlambat"]).rename(columns={
            "id": "ID", "outlet": "Outlet", "sales": "Sales", "jenis": "Jenis", "status": "Status", "tenggat": "Tenggat",
            "hari_terlambat": "Hari terlambat"}), hide_index=True, use_container_width=True)


@st.fragment
def _isi(df: pd.DataFrame, role: str, sales_name: str, ai_cfg: ai_tools.ToolsConfig, akses: ai_tools.Akses,
         tugas: pd.DataFrame, cfg: E.EvalConfig) -> None:
    pilihan_n = list(cfg.jendela_pilihan)
    k1, k2, _ = st.columns([1, 1, 2])
    n_hari = k1.selectbox("Order lagi dalam (hari)", pilihan_n,
                          index=pilihan_n.index(cfg.hari_jendela) if cfg.hari_jendela in pilihan_n else 0, key="evt_n")
    label = k2.selectbox("Jenis tugas", ["Semua jenis"] + [K.JENIS_TUGAS[j] for j in E.JENIS_EVAL], key="evt_jenis")
    jenis = None if label == "Semua jenis" else next(k for k, v in K.JENIS_TUGAS.items() if v == label)

    with st.spinner("Menghitung..."):
        hasil = _hitung(tugas, df, _sidik(tugas, df), role, sales_name, ai_cfg, int(n_hari), jenis)
    _bagian_efektivitas(hasil["efektivitas"], akses)
    st.markdown("---")
    _bagian_kinerja(hasil["kinerja"], hasil["terbuka"], akses)

    with st.expander("Kualitas data tugas & catatan metode"):
        kual = (hasil["efektivitas"].get("kualitas_data") if hasil["efektivitas"].get("ok") else None) or {}
        if len(kual) > 1:
            st.write("Masalah pada data Sheet (dibersihkan otomatis, tidak diubah di Sheet):")
            for nama, n in kual.items():
                if nama != "jumlah_baris":
                    st.write(f"- {nama}: {n}")
        else:
            st.write("Tidak ada masalah data yang terdeteksi.")
        for cat in hasil["efektivitas"].get("catatan", []):
            st.caption("• " + cat)


def render_evaluasi(df: pd.DataFrame, role: str, sales_name: str, ai_config: ai_tools.ToolsConfig,
                    eval_config: Optional[E.EvalConfig] = None, tugas_df: Optional[pd.DataFrame] = None) -> None:
    """Titik masuk dari app.py. `tugas_df` hanya untuk pengujian (default: baca Google Sheet)."""
    st.subheader("📈 Evaluasi Tugas Kunjungan")
    try:
        akses = ai_tools.tentukan_akses(role, sales_name, ai_config)
    except ai_tools.ToolError as e:
        st.error(str(e))
        return
    if tugas_df is None:
        try:
            tugas_df = _ambil_tugas()
        except Exception:  # noqa: BLE001 - Sheet bermasalah tidak boleh menjatuhkan dashboard
            st.warning(E.PESAN_SHEET)
            st.caption("Periksa: secrets `gcp_service_account` & `TASK_SHEET_ID`, dan spreadsheet di-share (Editor) ke "
                       "service account.")
            return
    _isi(df, role, sales_name, ai_config, akses, tugas_df, eval_config or E.EvalConfig())
