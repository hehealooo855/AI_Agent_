"""
forecasting.py — Sales & Volume Trend Forecaster
================================================
Forecast volume (Qty) mingguan pada level Merk x Provinsi.

Pipeline:
    df transaksi -> build_weekly_panel -> build_series (flag Lebaran + spike)
                 -> backtest (rolling origin) -> forecast (MA / Seasonal Naive / Prophet)

Bagian inti (tanpa UI) hanya butuh pandas + numpy sehingga mudah diuji.
Prophet bersifat OPSIONAL: jika belum terpasang, model otomatis dilewati.
Streamlit & Plotly hanya di-import untuk fungsi render_forecast_tab().
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Callable

import numpy as np
import pandas as pd

try:  # cache hanya aktif saat dijalankan di dalam Streamlit
    import streamlit as st
    _cache = st.cache_data(show_spinner=False)
except ImportError:  # pragma: no cover
    st = None

    def _cache(fn):
        return fn

# --------------------------------------------------------------------------- #
# KONSTANTA & KONFIGURASI
# --------------------------------------------------------------------------- #
DATE_COL = "Tanggal"
TARGET_COL = "Qty"
LEVEL_COLS = ["Merk", "Provinsi"]
MIN_VALID_YEAR = 2001  # harapan.py memakai 2000-01-01 sebagai placeholder tanggal gagal-parse

# Tanggal 1 Syawal (Idul Fitri). Nilai ini perkiraan kalender — mohon diverifikasi
# dan ditambah untuk tahun berikutnya. Jendela +/- hari diatur di ForecastConfig.
IDUL_FITRI = {
    "2024": "2024-04-10",
    "2025": "2025-03-31",
    "2026": "2026-03-20",
    "2027": "2027-03-10",
}


@dataclass(frozen=True)
class ForecastConfig:
    horizon: int = 4              # minggu ke depan
    n_folds: int = 3              # jumlah lipatan backtest (rolling origin)
    ma_window: int = 8            # jendela Moving Average (minggu)
    lebaran_before: int = 14      # hari sebelum Idul Fitri yang di-flag
    lebaran_after: int = 14       # hari sesudah Idul Fitri yang di-flag
    spike_z: float = 3.5          # ambang robust z-score untuk lonjakan
    spike_window: int = 13        # jendela median bergulir (minggu)
    min_weeks: int = 26           # minimal histori agar series dipakai
    anomaly_mode: str = "remove"  # "remove" = minggu ter-flag dibuang | "keep" = hanya ditandai


# --------------------------------------------------------------------------- #
# 1. PREPROCESSING: transaksi harian -> panel mingguan
# --------------------------------------------------------------------------- #
def build_weekly_panel(
    df: pd.DataFrame,
    level_cols: list[str] = LEVEL_COLS,
    target: str = TARGET_COL,
) -> pd.DataFrame:
    """
    Agregasi Qty ke level mingguan (Senin-Minggu) per Merk x Provinsi.

    - Tanggal placeholder (<= 2000) dibuang.
    - Minggu pertama/terakhir yang belum penuh dibuang (menghindari bias turun palsu).
    - Minggu tanpa transaksi diisi 0, tetapi hanya SETELAH minggu penjualan pertama
      series tersebut (agar brand yang baru masuk tidak punya 'nol' palsu).
    - Qty mingguan negatif (retur > penjualan) di-clip ke 0.

    Return: DataFrame [Minggu, *level_cols, y].
    """
    need = [DATE_COL, *level_cols, target]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise KeyError(f"Kolom tidak ditemukan: {missing}")

    d = df.loc[df[DATE_COL].dt.year >= MIN_VALID_YEAR, need].copy()
    if d.empty:
        return pd.DataFrame(columns=["Minggu", *level_cols, "y"])

    d[target] = pd.to_numeric(d[target], errors="coerce").fillna(0.0)
    for c in level_cols:
        d[c] = d[c].astype(str).str.strip()

    day = d[DATE_COL].dt.normalize()
    d["Minggu"] = day - pd.to_timedelta(day.dt.weekday, unit="D")  # Senin

    first_day, last_day = day.min(), day.max()
    weeks = pd.date_range(d["Minggu"].min(), d["Minggu"].max(), freq="7D")
    if len(weeks) and first_day > weeks[0]:            # minggu awal belum penuh
        weeks = weeks[1:]
    if len(weeks) and last_day < weeks[-1] + pd.Timedelta(days=6):  # minggu akhir belum penuh
        weeks = weeks[:-1]
    if len(weeks) == 0:
        return pd.DataFrame(columns=["Minggu", *level_cols, "y"])

    wk = d.groupby(["Minggu", *level_cols], observed=True)[target].sum().reset_index()
    wk["_sid"] = wk.groupby(level_cols, sort=True, observed=True).ngroup()
    keys = wk.drop_duplicates("_sid")[["_sid", *level_cols]]

    wide = (
        wk.pivot(index="Minggu", columns="_sid", values=target)
        .reindex(weeks).fillna(0.0).clip(lower=0.0)
    )
    wide.index.name = "Minggu"
    long = wide.reset_index().melt(id_vars="Minggu", var_name="_sid", value_name="y")
    long = long.merge(keys, on="_sid").sort_values(["_sid", "Minggu"], kind="stable")

    # buang nol di awal tiap series (sebelum penjualan pertama)
    started = long.groupby("_sid", sort=False)["y"].transform(lambda s: (s > 0).cummax())
    long = long.loc[started.astype(bool), ["Minggu", *level_cols, "y"]]
    return long.reset_index(drop=True)


# --------------------------------------------------------------------------- #
# 2. FLAG LONJAKAN & LEBARAN
# --------------------------------------------------------------------------- #
def lebaran_flags(index: pd.DatetimeIndex, cfg: ForecastConfig) -> pd.Series:
    """True untuk minggu (Senin) yang beririsan dengan jendela Idul Fitri."""
    wk_start = index
    wk_end = index + pd.Timedelta(days=6)
    flag = np.zeros(len(index), dtype=bool)
    for raw in IDUL_FITRI.values():
        ied = pd.Timestamp(raw)
        lo = ied - pd.Timedelta(days=cfg.lebaran_before)
        hi = ied + pd.Timedelta(days=cfg.lebaran_after)
        flag |= (wk_end >= lo) & (wk_start <= hi)
    return pd.Series(flag, index=index)


def spike_flags(y: pd.Series, cfg: ForecastConfig) -> pd.Series:
    """
    Lonjakan = deviasi POSITIF yang ekstrem terhadap median bergulir (robust z-score,
    dihitung pada log1p). Penurunan tidak di-flag karena bisa jadi sinyal riil.
    """
    ly = np.log1p(y.astype(float))
    med = ly.rolling(cfg.spike_window, center=True, min_periods=5).median()
    resid = ly - med
    mad = float(np.nanmedian(np.abs(resid - np.nanmedian(resid)))) * 1.4826
    mad = max(mad, 0.15)  # lantai agar series yang sangat stabil tidak over-flag
    z = resid / mad
    return (z > cfg.spike_z).fillna(False)


def build_series(
    panel: pd.DataFrame,
    merk: str | None,
    provinsi: str | None,
    cfg: ForecastConfig,
) -> pd.DataFrame:
    """
    Ambil satu series (None = semua) lalu tambahkan kolom:
    y_raw, is_lebaran, is_spike, is_anomaly, y (y_raw dengan NaN di minggu ter-flag
    bila anomaly_mode == 'remove').
    """
    p = panel
    if merk:
        p = p[p["Merk"] == merk]
    if provinsi:
        p = p[p["Provinsi"] == provinsi]
    if p.empty:
        return pd.DataFrame()

    s = p.groupby("Minggu")["y"].sum().sort_index()
    s = s.reindex(pd.date_range(s.index.min(), s.index.max(), freq="7D"), fill_value=0.0)
    s.index.name = "Minggu"

    out = pd.DataFrame({"y_raw": s})
    out["is_lebaran"] = lebaran_flags(out.index, cfg).values
    out["is_spike"] = spike_flags(out["y_raw"], cfg).values & ~out["is_lebaran"].values
    out["is_anomaly"] = out["is_lebaran"] | out["is_spike"]
    out["y"] = out["y_raw"].where(~out["is_anomaly"]) if cfg.anomaly_mode == "remove" else out["y_raw"]
    return out


def series_quality(s: pd.DataFrame) -> dict:
    """Ringkasan kualitas series untuk ditampilkan/diberi peringatan."""
    n = len(s)
    zero_share = float((s["y_raw"] == 0).mean()) if n else 1.0
    return {
        "n_weeks": n,
        "zero_share": zero_share,
        "n_lebaran": int(s["is_lebaran"].sum()) if n else 0,
        "n_spike": int(s["is_spike"].sum()) if n else 0,
        "intermittent": zero_share > 0.4,
    }


# --------------------------------------------------------------------------- #
# 3. MODEL
# --------------------------------------------------------------------------- #
def _band(base: np.ndarray, hist: pd.Series, window: int) -> tuple[np.ndarray, np.ndarray]:
    sd = float(hist.tail(window * 2).std()) if len(hist) > 2 else 0.0
    sd = 0.0 if np.isnan(sd) else sd
    return np.maximum(base - 1.28 * sd, 0.0), base + 1.28 * sd  # ~80% interval kasar


def forecast_ma(y: pd.Series, horizon: int, window: int = 8):
    """Moving Average datar dari N minggu valid terakhir (NaN diabaikan)."""
    hist = y.dropna()
    level = float(hist.tail(window).mean()) if len(hist) else 0.0
    yhat = np.full(horizon, max(level, 0.0))
    lo, hi = _band(yhat, hist, window)
    return yhat, lo, hi


def forecast_seasonal_naive(y: pd.Series, horizon: int, window: int = 8):
    """Nilai pada minggu yang sama tahun lalu (52 minggu); fallback ke MA bila kosong/NaN."""
    ma, lo, hi = forecast_ma(y, horizon, window)
    if len(y) < 52 + 1:
        return ma, lo, hi
    vals = y.to_numpy(dtype=float)
    n = len(vals)
    yhat = ma.copy()
    for h in range(1, horizon + 1):
        ref = n + h - 1 - 52
        if 0 <= ref < n and not np.isnan(vals[ref]):
            yhat[h - 1] = vals[ref]
    # skala ke level terkini agar tidak ketinggalan tren: rasio MA(8) sekarang vs MA(8) tahun lalu
    ly = vals[max(n - 52 - window, 0): n - 52]
    ly = ly[~np.isnan(ly)]
    ratio = (ma[0] / ly.mean()) if len(ly) and ly.mean() > 0 else 1.0
    yhat = np.where(np.isnan(yhat), ma, yhat) * float(np.clip(ratio, 0.5, 2.0))
    return np.maximum(yhat, 0.0), lo, hi


def prophet_available() -> bool:
    try:
        import prophet  # noqa: F401
        return True
    except Exception:
        return False


def forecast_prophet(y: pd.Series, horizon: int, window: int = 8):
    """Prophet (opsional). Minggu NaN (anomali) otomatis diabaikan saat fit."""
    from prophet import Prophet  # import lokal: tidak wajib terpasang

    logging.getLogger("cmdstanpy").setLevel(logging.WARNING)
    logging.getLogger("prophet").setLevel(logging.WARNING)

    train = pd.DataFrame({"ds": y.index, "y": y.to_numpy(dtype=float)})
    n_valid = int(train["y"].notna().sum())
    m = Prophet(
        yearly_seasonality=n_valid >= 104,   # musiman tahunan hanya bila >= 2 tahun data
        weekly_seasonality=False,            # data sudah mingguan
        daily_seasonality=False,
        changepoint_prior_scale=0.05,        # konservatif: kurangi overfit pada data berisik
        interval_width=0.8,
    )
    m.fit(train)
    fut = m.make_future_dataframe(periods=horizon, freq="7D")
    fc = m.predict(fut).tail(horizon)
    return (
        np.maximum(fc["yhat"].to_numpy(), 0.0),
        np.maximum(fc["yhat_lower"].to_numpy(), 0.0),
        np.maximum(fc["yhat_upper"].to_numpy(), 0.0),
    )


ModelFn = Callable[[pd.Series, int, int], tuple]


def available_models(include_prophet: bool = True) -> dict[str, ModelFn]:
    models: dict[str, ModelFn] = {
        "Moving Average": forecast_ma,
        "Seasonal Naive": forecast_seasonal_naive,
    }
    if include_prophet and prophet_available():
        models["Prophet"] = forecast_prophet
    return models


# --------------------------------------------------------------------------- #
# 4. BACKTEST (rolling origin)
# --------------------------------------------------------------------------- #
def backtest(
    s: pd.DataFrame,
    models: dict[str, ModelFn],
    cfg: ForecastConfig,
) -> pd.DataFrame:
    """
    Latih pada data sebelum cutoff, prediksi `horizon` minggu, bandingkan dengan aktual.
    Minggu ter-flag (Lebaran/lonjakan) TIDAK ikut dinilai.
    Metrik: WAPE = sum|error| / sum(aktual)  dan Bias = sum(error) / sum(aktual).
    (MAPE tidak dipakai karena minggu berqty 0 membuatnya tak terdefinisi.)
    """
    n, H = len(s), cfg.horizon
    folds = [n - H * k for k in range(cfg.n_folds, 0, -1)]
    folds = [c for c in folds if c >= cfg.min_weeks]
    cols = ["Model", "WAPE", "Bias", "Fold"]
    if not folds:
        return pd.DataFrame(columns=cols)

    rows = []
    for name, fn in models.items():
        abs_err = err = actual = 0.0
        used = 0
        for cut in folds:
            train = s["y"].iloc[:cut]
            test = s.iloc[cut: cut + H]
            keep = ~test["is_anomaly"].to_numpy()
            if keep.sum() == 0:
                continue
            try:
                yhat = fn(train, H, cfg.ma_window)[0][: len(test)]
            except Exception:
                continue
            e = yhat[keep] - test["y_raw"].to_numpy()[keep]
            abs_err += float(np.abs(e).sum())
            err += float(e.sum())
            actual += float(test["y_raw"].to_numpy()[keep].sum())
            used += 1
        if used and actual > 0:
            rows.append((name, abs_err / actual, err / actual, used))
    return pd.DataFrame(rows, columns=cols).sort_values("WAPE").reset_index(drop=True)


def forecast_series(
    s: pd.DataFrame,
    cfg: ForecastConfig,
    model_choice: str = "Auto",
    include_prophet: bool = True,
) -> dict:
    """Backtest -> pilih model (Auto = WAPE terkecil) -> forecast akhir."""
    models = available_models(include_prophet)
    bt = backtest(s, models, cfg)

    if model_choice == "Auto":
        chosen = bt.iloc[0]["Model"] if len(bt) else "Moving Average"
    else:
        chosen = model_choice if model_choice in models else "Moving Average"

    yhat, lo, hi = models[chosen](s["y"], cfg.horizon, cfg.ma_window)
    idx = pd.date_range(s.index[-1] + pd.Timedelta(days=7), periods=cfg.horizon, freq="7D")
    fc = pd.DataFrame({"yhat": yhat, "lo": lo, "hi": hi}, index=idx)
    fc.index.name = "Minggu"
    return {"model": chosen, "backtest": bt, "forecast": fc}


def horizon_hits_lebaran(last_week: pd.Timestamp, cfg: ForecastConfig) -> bool:
    """True bila minggu-minggu forecast beririsan dengan jendela Lebaran (hasil kurang andal)."""
    idx = pd.date_range(last_week + pd.Timedelta(days=7), periods=cfg.horizon, freq="7D")
    return bool(lebaran_flags(idx, cfg).any())


# --------------------------------------------------------------------------- #
# 5. BATCH: ringkasan cepat seluruh Merk x Provinsi (tanpa Prophet)
# --------------------------------------------------------------------------- #
def forecast_all_series(panel: pd.DataFrame, cfg: ForecastConfig) -> pd.DataFrame:
    models = available_models(include_prophet=False)  # Prophet terlalu lambat untuk ratusan series
    rows = []
    for (merk, prov), g in panel.groupby(LEVEL_COLS, sort=True):
        if len(g) < cfg.min_weeks:
            continue
        s = build_series(g, None, None, cfg)
        q = series_quality(s)
        res = forecast_series(s, cfg, "Auto", include_prophet=False)
        bt = res["backtest"]
        wape = float(bt.iloc[0]["WAPE"]) if len(bt) else np.nan
        recent = float(s["y"].dropna().tail(cfg.horizon).sum())
        pred = float(res["forecast"]["yhat"].sum())
        rows.append({
            "Merk": merk, "Provinsi": prov,
            "Minggu Data": q["n_weeks"],
            f"Qty {cfg.horizon} Mgg Terakhir": recent,
            f"Forecast {cfg.horizon} Mgg": pred,
            "Perubahan %": (pred / recent - 1) * 100 if recent > 0 else np.nan,
            "Model": res["model"],
            "WAPE Backtest": wape,
            "Keandalan": (
                "Rendah (intermittent)" if q["intermittent"] or np.isnan(wape) or wape >= 0.5
                else "Cukup" if wape >= 0.3 else "Baik"
            ),
        })
    return pd.DataFrame(rows).sort_values(f"Forecast {cfg.horizon} Mgg", ascending=False).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# 6. UI STREAMLIT
# --------------------------------------------------------------------------- #
@_cache
def _cached_panel(_df: pd.DataFrame, signature: tuple) -> pd.DataFrame:
    """`_df` tidak di-hash (500rb baris mahal di-hash); cache dikunci oleh `signature`."""
    return build_weekly_panel(_df)


@_cache
def _cached_batch(panel: pd.DataFrame, cfg: ForecastConfig) -> pd.DataFrame:
    return forecast_all_series(panel, cfg)


@_cache
def _cached_forecast(s: pd.DataFrame, cfg: ForecastConfig, model_choice: str) -> dict:
    return forecast_series(s, cfg, model_choice)


def _signature(df: pd.DataFrame) -> tuple:
    return (len(df), str(df[DATE_COL].max()), round(float(pd.to_numeric(df[TARGET_COL], errors="coerce").sum()), 2))


def render_forecast_tab(df_scope: pd.DataFrame) -> None:
    import plotly.graph_objects as go  # lazy import

    st.subheader("🔮 Forecast Volume Mingguan (Merk × Provinsi)")
    st.caption(
        "Target: Qty (bebas distorsi harga) · Resolusi: mingguan · Minggu Lebaran & lonjakan "
        "ekstrem di-flag lalu dikeluarkan dari pelatihan · Model dipilih via backtest."
    )

    missing = [c for c in (DATE_COL, TARGET_COL, *LEVEL_COLS) if c not in df_scope.columns]
    if missing:
        st.error(f"Kolom wajib tidak ada: {missing}")
        return

    panel = _cached_panel(df_scope, _signature(df_scope))
    if panel.empty:
        st.warning("Belum ada data mingguan yang valid (cek kolom Tanggal & Qty).")
        return

    merk_opts = ["(Semua Merk)"] + sorted(panel["Merk"].unique())
    prov_opts = ["(Semua Provinsi)"] + sorted(panel["Provinsi"].unique())
    model_opts = ["Auto"] + list(available_models().keys())

    with st.form("form_forecast_volume"):
        c1, c2, c3, c4 = st.columns(4)
        sel_merk = c1.selectbox("Merk", merk_opts)
        sel_prov = c2.selectbox("Provinsi", prov_opts)
        horizon = c3.select_slider("Horizon (minggu)", options=[4, 8, 12], value=4)
        model_choice = c4.selectbox("Model", model_opts, help="Auto = pilih WAPE backtest terkecil")
        c5, c6 = st.columns(2)
        remove_anom = c5.checkbox("Buang minggu Lebaran & lonjakan dari pelatihan", value=True)
        ma_window = c6.slider("Jendela Moving Average (minggu)", 4, 16, 8)
        submitted = st.form_submit_button("🚀 Jalankan Forecast", type="primary")

    if "Prophet" not in model_opts:
        st.caption("ℹ️ Prophet belum terpasang (`pip install prophet`) — hanya baseline yang dipakai.")

    if submitted:
        st.session_state["fc_params"] = (sel_merk, sel_prov, horizon, model_choice, remove_anom, ma_window)
    params = st.session_state.get("fc_params")
    if params is None:
        st.info("Pilih parameter lalu klik **Jalankan Forecast**.")
        return

    sel_merk, sel_prov, horizon, model_choice, remove_anom, ma_window = params
    cfg = ForecastConfig(
        horizon=horizon, ma_window=ma_window,
        anomaly_mode="remove" if remove_anom else "keep",
    )
    merk = None if sel_merk.startswith("(") else sel_merk
    prov = None if sel_prov.startswith("(") else sel_prov

    s = build_series(panel, merk, prov, cfg)
    if s.empty or len(s) < cfg.min_weeks:
        st.warning(f"Histori kurang dari {cfg.min_weeks} minggu untuk kombinasi ini.")
        return

    q = series_quality(s)
    if q["intermittent"]:
        st.warning(f"⚠️ {q['zero_share']:.0%} minggu bernilai 0 (intermittent) — hasil kurang andal. "
                   "Pertimbangkan memilih '(Semua Provinsi)' atau '(Semua Merk)'.")
    if horizon_hits_lebaran(s.index[-1], cfg):
        st.warning("⚠️ Periode forecast beririsan dengan jendela Lebaran — pola musiman tidak dimodelkan.")

    with st.spinner("Menjalankan backtest & forecast..."):
        res = _cached_forecast(s, cfg, model_choice)
    fc, bt = res["forecast"], res["backtest"]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Model terpilih", res["model"])
    m2.metric("Histori", f"{q['n_weeks']} minggu")
    m3.metric("Minggu di-flag", f"{q['n_lebaran']} Lebaran · {q['n_spike']} lonjakan")
    last = float(s["y"].dropna().tail(horizon).sum())
    tot = float(fc["yhat"].sum())
    m4.metric(f"Forecast {horizon} mgg", f"{tot:,.0f} pcs",
              f"{(tot / last - 1) * 100:+.1f}% vs {horizon} mgg valid terakhir" if last > 0 else None)

    # ---- grafik ----
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=s.index, y=s["y_raw"], mode="lines", name="Aktual",
                             line=dict(color="#2980b9", width=2)))
    flagged = s[s["is_anomaly"]]
    if len(flagged):
        fig.add_trace(go.Scatter(
            x=flagged.index, y=flagged["y_raw"], mode="markers", name="Di-flag (Lebaran/lonjakan)",
            marker=dict(color="#e67e22", size=9, symbol="x")))
    fig.add_trace(go.Scatter(x=fc.index, y=fc["hi"], mode="lines", line=dict(width=0),
                             showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=fc.index, y=fc["lo"], mode="lines", line=dict(width=0), fill="tonexty",
                             fillcolor="rgba(231,76,60,0.18)", name="Rentang ~80%", hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=fc.index, y=fc["yhat"], mode="lines+markers", name="Forecast",
                             line=dict(color="#e74c3c", dash="dash", width=2)))
    fig.update_layout(title=f"Qty Mingguan — {sel_merk} × {sel_prov}", xaxis_title="Minggu (Senin)",
                      yaxis_title="Qty", hovermode="x unified", margin=dict(t=50, b=10),
                      legend=dict(orientation="h", y=-0.2))
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    # ---- backtest & tabel ----
    cA, cB = st.columns([1, 1])
    with cA:
        st.markdown("**Hasil Backtest** (lebih kecil = lebih baik)")
        if bt.empty:
            st.caption("Histori belum cukup untuk backtest — dipakai Moving Average.")
        else:
            st.dataframe(bt.style.format({"WAPE": "{:.1%}", "Bias": "{:+.1%}"}),
                         use_container_width=True, hide_index=True)
            st.caption("Bias positif = cenderung over-forecast.")
    with cB:
        st.markdown("**Angka Forecast**")
        show = fc.copy().round(0)
        show.index = show.index.strftime("%d %b %Y")
        st.dataframe(show.rename(columns={"yhat": "Forecast", "lo": "Bawah", "hi": "Atas"}),
                     use_container_width=True)
        st.download_button("⬇️ Unduh CSV", fc.round(1).to_csv().encode("utf-8"),
                           file_name="forecast_qty_mingguan.csv", mime="text/csv")

    # ---- ringkasan seluruh series ----
    with st.expander("📋 Ringkasan semua Merk × Provinsi (baseline cepat)"):
        if st.button("Hitung semua series", key="fc_batch_btn"):
            st.session_state["fc_batch_ready"] = True
        if st.session_state.get("fc_batch_ready"):
            with st.spinner("Menghitung ratusan series..."):
                tbl = _cached_batch(panel, replace(cfg, anomaly_mode="remove"))
            st.dataframe(
                tbl.style.format({f"Qty {horizon} Mgg Terakhir": "{:,.0f}", f"Forecast {horizon} Mgg": "{:,.0f}",
                                  "Perubahan %": "{:+.1f}", "WAPE Backtest": "{:.1%}"}),
                use_container_width=True, hide_index=True)
