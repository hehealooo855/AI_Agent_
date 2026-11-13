"""
ai_tasks_ui.py - FASE 3: Tab "📌 Tugas" (Streamlit).

Pemakaian di app.py:
    import ai_tools, ai_tasks_ui
    ai_tasks_ui.render_tugas(df, user_role, user_name, cfg_ai, catat=log_activity)

Secrets (sama dengan ai_tes_koneksi.py): gcp_service_account, TASK_SHEET_ID.
Sheet harus di-share (Editor) ke email service account. Sheet 'Tugas' dan 'AuditLog' dibuat otomatis bila belum ada.

Tampilan dibungkus @st.fragment sehingga klik di tab ini tidak menjalankan ulang seluruh dashboard.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

import pandas as pd
import streamlit as st

import ai_tasks as K
import ai_tools

KOLOM_TABEL = ["id", "prioritas", "tenggat", "status", "sales", "outlet", "brand", "jenis", "tindakan", "alasan"]
KOLOM_USULAN = ["prioritas", "tenggat", "sales", "outlet", "brand", "tindakan", "alasan"]
_V, _PESAN, _KAND, _KTX = "tugas_versi", "tugas_pesan", "tugas_kandidat", "tugas_konteks"


# =============================================================================
# Koneksi & cache
# =============================================================================
@st.cache_resource(show_spinner=False)
def _buka_store() -> K.SheetStore:
    import gspread
    gc = gspread.service_account_from_dict(dict(st.secrets["gcp_service_account"]))
    sh = gc.open_by_key(st.secrets["TASK_SHEET_ID"])

    def ws(nama: str, headers: list[str]):
        try:
            return sh.worksheet(nama)
        except gspread.WorksheetNotFound:
            return sh.add_worksheet(title=nama, rows=1000, cols=len(headers))

    store = K.SheetStore(ws("Tugas", K.HEADERS), ws("AuditLog", K.AUDIT_HEADERS))
    store.pastikan_header()
    return store


@st.cache_data(ttl=30, show_spinner=False)
def _muat(_store: K.SheetStore, versi: int) -> pd.DataFrame:
    """Seluruh tabel tugas (di-cache 30 dtk). Selalu difilter per user SEBELUM ditampilkan."""
    return _store.baca()


def _segarkan(ok: bool, teks: str) -> None:
    st.session_state[_PESAN] = (ok, teks)
    st.session_state[_V] = st.session_state.get(_V, 0) + 1
    try:
        st.rerun(scope="fragment")
    except Exception:  # noqa: BLE001 - bukan rerun fragment -> rerun penuh
        st.rerun()


# =============================================================================
# Bagian-bagian tampilan
# =============================================================================
def _tab_tugas(terlihat: pd.DataFrame, akses: ai_tools.Akses, store: K.SheetStore, catat: Optional[Callable]) -> None:
    c = st.columns(5)
    for kolom, (judul, hitung) in zip(c, [
        ("Usulan", (terlihat["status"] == "usulan").sum()), ("Disetujui", (terlihat["status"] == "disetujui").sum()),
        ("Dikerjakan", (terlihat["status"] == "dikerjakan").sum()), ("Terlambat", terlihat["terlambat"].sum()),
        ("Selesai", (terlihat["status"] == "selesai").sum())]):
        kolom.metric(judul, int(hitung))

    bawaan = ["usulan", "disetujui", "dikerjakan"] if akses.level != "sales" else ["disetujui", "dikerjakan"]
    pilih = st.multiselect("Status", ["usulan", "disetujui", "dikerjakan", "selesai", "ditolak"], default=bawaan, key="tugas_filter")
    tampil = terlihat[terlihat["status"].isin(pilih)].copy()
    tampil["urut"] = tampil["prioritas"].map(K.URUT_PRIORITAS).fillna(9)
    tampil = tampil.sort_values(["urut", "tenggat"])
    tampil["tenggat"] = tampil["tenggat"] + tampil["terlambat"].map({True: " ⚠️ lewat", False: ""})
    st.dataframe(tampil[KOLOM_TABEL], hide_index=True, use_container_width=True)

    # --- panel aksi untuk tugas yang berjalan ---
    dapat = [r for r in terlihat[terlihat["status"].isin(K.STATUS_BERJALAN)].to_dict("records") if K.boleh_mengerjakan(akses, r)]
    if not dapat:
        st.caption("Tidak ada tugas berjalan yang bisa Anda ubah.")
        return
    st.markdown("**Perbarui tugas**")
    peta = {r["id"]: r for r in dapat}
    id_pilih = st.selectbox("Pilih tugas", list(peta), format_func=lambda i: f"{peta[i]['outlet']} | {peta[i]['tindakan']} ({i})", key="tugas_pilih")
    r = peta[id_pilih]
    st.caption(f"Alasan: {r['alasan']} | Status: **{r['status']}** | Tenggat: {r['tenggat']}")
    pilihan = ["Mulai dikerjakan", "Tandai selesai"] if r["status"] == "disetujui" else ["Tandai selesai"]
    aksi = st.radio("Aksi", pilihan, horizontal=True, key="tugas_aksi")
    if st.button("Terapkan status", key="tugas_btn_status"):
        baru = "dikerjakan" if aksi == "Mulai dikerjakan" else "selesai"
        h = K.ubah_status(store, id_pilih, akses, baru, r["status"])
        if catat and h.ok:
            catat(akses.nama, f"TUGAS {baru.upper()} {id_pilih}")
        _segarkan(h.ok, h.pesan)

    if K.milik_sales(akses, r):
        catatan = st.text_area("Catatan hasil kunjungan", value=r["catatan_sales"], max_chars=K.MAKS_CATATAN, key=f"tugas_cat_{id_pilih}")
        if st.button("Simpan catatan", key="tugas_btn_catatan"):
            h = K.simpan_catatan(store, id_pilih, akses, catatan)
            _segarkan(h.ok, h.pesan)


def _tab_persetujuan(terlihat: pd.DataFrame, akses: ai_tools.Akses, store: K.SheetStore, catat: Optional[Callable]) -> None:
    antrean = [r for r in terlihat[terlihat["status"] == "usulan"].to_dict("records") if K.boleh_menyetujui(akses, r)]
    if not antrean:
        st.info("Tidak ada usulan yang menunggu persetujuan Anda.")
        return
    tabel = pd.DataFrame(antrean)[["id", "prioritas", "tenggat", "sales", "outlet", "brand", "tindakan", "alasan"]]
    tabel["urut"] = tabel["prioritas"].map(K.URUT_PRIORITAS).fillna(9)
    tabel = tabel.sort_values(["urut", "tenggat"]).drop(columns="urut").reset_index(drop=True)
    tabel["Keputusan"] = "-"
    hasil = st.data_editor(
        tabel, hide_index=True, use_container_width=True, key=f"tugas_edit_setuju_{st.session_state.get(_V, 0)}",
        disabled=[k for k in tabel.columns if k != "Keputusan"],
        column_config={"Keputusan": st.column_config.SelectboxColumn(options=["-", "Setujui", "Tolak"], required=True)})
    putusan = hasil[hasil["Keputusan"] != "-"]
    st.caption(f"{len(putusan)} keputusan dipilih dari {len(hasil)} usulan.")
    if st.button("Terapkan keputusan", disabled=putusan.empty, key="tugas_btn_putus"):
        ok_n, gagal = 0, []
        for r in putusan.itertuples():
            h = K.ubah_status(store, r.id, akses, "disetujui" if r.Keputusan == "Setujui" else "ditolak", "usulan")
            if h.ok:
                ok_n += 1
                if catat:
                    catat(akses.nama, f"TUGAS {r.Keputusan.upper()} {r.id}")
            else:
                gagal.append(f"{r.outlet}: {h.pesan}")
        _segarkan(not gagal, f"{ok_n} keputusan diterapkan." + ("".join(f"\n- {g}" for g in gagal) if gagal else ""))


def _konteks(df: pd.DataFrame, role: str, sales_name: str, cfg: ai_tools.ToolsConfig) -> dict:
    if st.session_state.get(_KTX, {}).get("pemilik") != sales_name:
        st.session_state[_KTX] = {"pemilik": sales_name, **ai_tools.konteks_data(df, role, sales_name, cfg)}
    return st.session_state[_KTX]


def _tab_usulkan(df: pd.DataFrame, role: str, sales_name: str, ai_cfg: ai_tools.ToolsConfig, cfg: K.TaskConfig,
                 akses: ai_tools.Akses, store: K.SheetStore, semua: pd.DataFrame, catat: Optional[Callable]) -> None:
    ktx = _konteks(df, role, sales_name, ai_cfg)
    jenis = st.selectbox("Jenis tugas", list(K.JENIS_TUGAS), format_func=K.JENIS_TUGAS.get, key="tugas_jenis")
    k1, k2 = st.columns(2)
    sales_f = k1.selectbox("Sales", ["SEMUA"] + ktx["sales"], key="tugas_sales_f")
    args: dict[str, Any] = {"top_n": 50}
    if sales_f != "SEMUA":
        args["sales"] = sales_f

    if jenis == "cross_sell_brand":
        acuan = k2.selectbox("Merk acuan (sudah dibeli)", ktx["merk"], key="tugas_acuan")
        target = st.selectbox("Merk target (belum dibeli)", [m for m in ktx["merk"] if m != acuan], key="tugas_target")
        args.update(merk_acuan=acuan, merk_target=target)
    else:
        merk_f = k2.selectbox("Merk", ["SEMUA"] + ktx["merk"], key="tugas_merk_f")
        if merk_f != "SEMUA":
            args["merk"] = merk_f
        if jenis == "toko_tidur":
            args["ambang_hari"] = int(st.number_input("Tidak order minimal (hari)", 1, 365, ai_cfg.ambang_toko_tidur_hari, key="tugas_hari"))
        elif jenis == "outlet_turun_order":
            args["ambang_persen"] = float(st.number_input("Omset turun minimal (%)", 1, 100, int(ai_cfg.ambang_turun_persen), key="tugas_persen"))
            rentang = st.date_input("Periode (kosongkan = bulan data terakhir)", value=(), key="tugas_periode")
            if isinstance(rentang, tuple) and len(rentang) == 2:
                args.update(mulai=str(rentang[0]), selesai=str(rentang[1]))
        elif jenis == "cross_sell_sku":
            args["min_confidence"] = float(st.slider("Confidence minimal", 0.1, 1.0, 0.5, 0.05, key="tugas_conf"))
            args["min_faktur"] = int(st.number_input("Minimal faktur bersama", 1, 100, 5, key="tugas_minfak"))

    if st.button("🔎 Cari kandidat", key="tugas_btn_cari"):
        with st.spinner("Menghitung..."):
            hasil = ai_tools.jalankan_tool(jenis, args, df=df, role=role, sales_name=sales_name, config=ai_cfg)
            usul, lewat1 = K.bangun_usulan(hasil)
            baris, lewat2 = K.siapkan_baris(usul, sales_name, semua, cfg)
        st.session_state[_KAND] = {"baris": baris, "lewat": lewat1 + lewat2, "catatan": hasil.get("catatan", []), "jenis": jenis}

    kand = st.session_state.get(_KAND)
    if not kand:
        return
    for c in kand["catatan"]:
        st.caption(f"ℹ️ {c}")
    if kand["lewat"]:
        with st.expander(f"{len(kand['lewat'])} kandidat dilewati"):
            st.dataframe(pd.DataFrame(kand["lewat"]), hide_index=True, use_container_width=True)
    if not kand["baris"]:
        st.info("Tidak ada kandidat baru untuk dijadikan tugas.")
        return

    tabel = pd.DataFrame(kand["baris"])[KOLOM_USULAN]
    tabel.insert(0, "Kirim", True)
    hasil = st.data_editor(tabel, hide_index=True, use_container_width=True, key="tugas_edit_kandidat",
                           disabled=KOLOM_USULAN, column_config={"Kirim": st.column_config.CheckboxColumn(default=True)})
    terpilih = [kand["baris"][i] for i in hasil.index[hasil["Kirim"]]]
    st.caption("Usulan masuk berstatus **usulan** dan baru berjalan setelah disetujui.")
    if st.button(f"📤 Kirim {len(terpilih)} usulan ke persetujuan", disabled=not terpilih, type="primary", key="tugas_btn_kirim"):
        h = K.kirim_usulan(store, terpilih, akses, cfg)
        if catat and h.ok:
            catat(akses.nama, f"TUGAS USULAN x{h.jumlah} ({kand['jenis']})")
        st.session_state[_KAND] = None
        _segarkan(h.ok, h.pesan)


@st.fragment
def _isi(df: pd.DataFrame, role: str, sales_name: str, ai_cfg: ai_tools.ToolsConfig, akses: ai_tools.Akses,
         store: K.SheetStore, cfg: K.TaskConfig, catat: Optional[Callable]) -> None:
    pesan = st.session_state.pop(_PESAN, None)
    if pesan:
        (st.success if pesan[0] else st.error)(pesan[1])

    try:
        semua = _muat(store, st.session_state.get(_V, 0))
    except Exception as e:  # noqa: BLE001
        st.error(f"Gagal membaca sheet Tugas ({type(e).__name__}). Coba lagi sebentar.")
        return
    terlihat = K.tandai_terlambat(K.tugas_terlihat(semua, akses))

    judul = ["📥 Tugas"]
    if akses.level in ("penuh", "supervisor"):
        judul.append("✅ Persetujuan")
    if K.boleh_mengusulkan(akses):
        judul.append("➕ Usulkan")
    tabs = st.tabs(judul)
    with tabs[0]:
        _tab_tugas(terlihat, akses, store, catat)
    if "✅ Persetujuan" in judul:
        with tabs[judul.index("✅ Persetujuan")]:
            _tab_persetujuan(terlihat, akses, store, catat)
    if "➕ Usulkan" in judul:
        with tabs[judul.index("➕ Usulkan")]:
            _tab_usulkan(df, role, sales_name, ai_cfg, cfg, akses, store, semua, catat)


def render_tugas(df: pd.DataFrame, role: str, sales_name: str, ai_config: ai_tools.ToolsConfig,
                 catat: Optional[Callable[[str, str], Any]] = None, task_config: Optional[K.TaskConfig] = None,
                 store: Optional[K.SheetStore] = None) -> None:
    """Titik masuk dari app.py. `store` hanya untuk pengujian (default: Google Sheets dari st.secrets)."""
    st.subheader("📌 Tugas Kunjungan")
    try:
        akses = ai_tools.tentukan_akses(role, sales_name, ai_config)
    except ai_tools.ToolError as e:
        st.error(str(e))
        return
    if store is None:
        try:
            store = _buka_store()
        except Exception as e:  # noqa: BLE001
            st.error("Sheet Tugas belum bisa dibuka: " + (str(e) if isinstance(e, ValueError) else type(e).__name__))
            st.caption("Periksa: (1) Secrets `gcp_service_account` dan `TASK_SHEET_ID` terisi; "
                       "(2) spreadsheet di-share sebagai Editor ke email service account; (3) baris 1 sheet 'Tugas' kosong atau sesuai rancangan.")
            return
    _isi(df, role, sales_name, ai_config, akses, store, task_config or K.TaskConfig(), catat)
