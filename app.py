import os
import requests
import pandas as pd
import numpy as np
import streamlit as st
from datetime import date

FRED_BASE = "https://api.stlouisfed.org/fred"

st.set_page_config(page_title="Macro Vintage Dashboard (First Release)", layout="wide")

# -----------------------------
# Helpers
# -----------------------------
def fred_get(endpoint: str, params: dict) -> dict:
    url = f"{FRED_BASE}/{endpoint}"
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    return r.json()

@st.cache_data(ttl=6 * 60 * 60)  # 6h Cache
def fetch_first_release_series(series_id: str, api_key: str) -> pd.DataFrame:
    """
    Returns a DataFrame indexed by observation date with:
    - value_first: first release value (earliest vintage)
    - vintage_first: realtime_start of that first release
    """
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        # Pull full real-time history so we can select earliest vintage per obs date
        "realtime_start": "1776-07-04",
        "realtime_end": "9999-12-31",
    }

    data = fred_get("series/observations", params)
    obs = pd.DataFrame(data["observations"])

    # Clean / types
    obs["date"] = pd.to_datetime(obs["date"])
    obs["realtime_start"] = pd.to_datetime(obs["realtime_start"])
    obs["value"] = pd.to_numeric(obs["value"], errors="coerce")

    obs = obs.dropna(subset=["value"])

    # Pick earliest realtime_start per observation date
    idx = obs.groupby("date")["realtime_start"].idxmin()
    first = obs.loc[idx, ["date", "value", "realtime_start"]].sort_values("date")

    first = first.rename(columns={"value": "value_first", "realtime_start": "vintage_first"})
    first = first.set_index("date")
    return first

def add_transforms_q(series: pd.Series, z_window_quarters: int = 40) -> pd.DataFrame:
    """
    Quarterly transforms:
    - YoY%: 4-quarter pct change
    - QoQ SAAR%: ((x/x(-1))^4 - 1)*100
    - Z-score: rolling z on YoY%
    """
    df = pd.DataFrame({"level": series})

    df["yoy_pct"] = 100 * (df["level"] / df["level"].shift(4) - 1.0)
    df["qoq_saar_pct"] = 100 * ((df["level"] / df["level"].shift(1)) ** 4 - 1.0)

    # z-score on YoY% (common macro choice); you can change to level if desired
    mu = df["yoy_pct"].rolling(z_window_quarters).mean()
    sd = df["yoy_pct"].rolling(z_window_quarters).std(ddof=0)
    df["z_yoy"] = (df["yoy_pct"] - mu) / sd

    return df

# -----------------------------
# UI
# -----------------------------
st.title("Macro Dashboard – First Release / Vintage-sicher")

api_key = st.secrets.get("FRED_API_KEY") or os.getenv("FRED_API_KEY")
if not api_key:
    st.error("Bitte FRED_API_KEY als Streamlit Secret oder Environment Variable setzen.")
    st.stop()

series_id = st.sidebar.text_input("Series ID", value="GDPC1")
z_years = st.sidebar.slider("Z-Score Window (Jahre)", min_value=5, max_value=25, value=10, step=1)
z_window_quarters = z_years * 4

st.sidebar.caption("First Release = je Datum frühester realtime_start (ältestes Vintage).")

first = fetch_first_release_series(series_id, api_key)
trans = add_transforms_q(first["value_first"], z_window_quarters=z_window_quarters)

# Merge for display
out = first.join(trans, how="left")

# -----------------------------
# Layout
# -----------------------------
c1, c2, c3 = st.columns(3)
c1.metric("Serie", series_id)
c2.metric("Beobachtungen", f"{out.shape[0]:,}")
c3.metric("Letztes Datum", out.index.max().date().isoformat() if len(out) else "-")

st.subheader("Level (First Release)")
st.line_chart(out["value_first"])

st.subheader("Wachstum & Z-Scores (auf First Release gerechnet)")
colA, colB = st.columns(2)
with colA:
    st.caption("YoY %")
    st.line_chart(out["yoy_pct"])
with colB:
    st.caption("QoQ SAAR %")
    st.line_chart(out["qoq_saar_pct"])

st.caption(f"Z-Score auf YoY% (Rolling {z_years} Jahre)")
st.line_chart(out["z_yoy"])

with st.expander("Daten-Tabelle (First Release + Vintage-Datum)", expanded=False):
    st.dataframe(
        out[["value_first", "vintage_first", "yoy_pct", "qoq_saar_pct", "z_yoy"]].tail(200),
        use_container_width=True
    )
