import io
import requests
import pandas as pd
import streamlit as st

# Philly Fed RTDSM Excel (Real Output)
EXCEL_URL = "https://www.philadelphiafed.org/-/media/FRBP/Assets/Surveys-And-Data/real-time-data/data-files/xlsx/ROUTPUTQvQd.xlsx?sc_lang=en&hash=34FA1C6BF0007996E1885C8C32E3BEF9"


st.set_page_config(page_title="Macro Dashboard", layout="wide")


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)  # Cache 6 Stunden
def download_excel_bytes(url: str) -> bytes:
    # timeout ist wichtig, damit Deploys nicht „hängen“
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content


def parse_quarter_dates(date_series: pd.Series) -> pd.Series:
    """
    Philly-Fed-RTDSM hat oft Quartalsformate wie '1965:Q1' oder '1965Q1'.
    pd.to_datetime() kann das nicht direkt -> wir ergänzen robustes Parsing.
    """
    # Erst normal probieren
    dt = pd.to_datetime(date_series, errors="coerce")

    # Für alles, was noch NaT ist: Quarter-Strings extrahieren (YYYY:Qn / YYYYQn / YYYY-Qn)
    missing = dt.isna()
    if missing.any():
        s = date_series.astype(str).str.strip()
        extracted = s.str.extract(r"^(?P<year>\d{4})\s*[:\-/ ]?\s*Q(?P<q>[1-4])$", expand=True)
        qmask = missing & extracted["year"].notna()

        if qmask.any():
            periods = pd.PeriodIndex(
                extracted.loc[qmask, "year"] + "Q" + extracted.loc[qmask, "q"],
                freq="Q",
            )
            # Quarter-End als Datum (00:00 Uhr) -> gut für Charts
            dt.loc[qmask] = periods.to_timestamp(how="end").normalize()

    return dt


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def load_and_process_data() -> pd.DataFrame:
    content = download_excel_bytes(EXCEL_URL)
    xls = pd.ExcelFile(io.BytesIO(content), engine="openpyxl")

    # Meist ist das erste Sheet richtig; falls 'ROUTPUT' existiert, nehmen wir das gezielt
    sheet = "ROUTPUT" if "ROUTPUT" in xls.sheet_names else xls.sheet_names[0]

    # Philly Fed Dateien haben oft Meta-Zeilen oben. Wir probieren ein paar Header-Offsets.
    df = None
    last_err = None
    for header in (3, 2, 0):
        try:
            tmp = pd.read_excel(xls, sheet_name=sheet, header=header, engine="openpyxl")
            if tmp.shape[1] >= 2:
                df = tmp
                break
        except Exception as e:
            last_err = e

    if df is None:
        raise RuntimeError(f"Konnte Excel nicht einlesen. Letzter Fehler: {last_err}")

    # Leere Zeilen/Spalten raus
    df = df.dropna(how="all").dropna(axis=1, how="all")

    # Erste Spalte als Date
    df = df.rename(columns={df.columns[0]: "Date"})
    df["Date"] = parse_quarter_dates(df["Date"])
    df = df.dropna(subset=["Date"])

    # Alle anderen Spalten numerisch machen (Vintages)
    value_cols = [c for c in df.columns if c != "Date"]
    df[value_cols] = df[value_cols].apply(pd.to_numeric, errors="coerce")

    # Spalten entfernen, die komplett leer sind
    df = df.dropna(axis=1, how="all")

    return df


def pick_vintage_values(df: pd.DataFrame, mode: str) -> pd.Series:
    """
    mode='latest' -> pro Quartal den aktuellsten verfügbaren Wert (letzte nicht-NaN Vintage)
    mode='first'  -> pro Quartal die erste verfügbare Schätzung (erste nicht-NaN Vintage)
    """
    value_cols = [c for c in df.columns if c != "Date"]
    data = df[value_cols].dropna(axis=1, how="all")

    if data.shape[1] == 0:
        return pd.Series([float("nan")] * len(df), index=df.index)

    if mode == "latest":
        return data.ffill(axis=1).iloc[:, -1]

    if mode == "first":
        return data.bfill(axis=1).iloc[:, 0]

    raise ValueError("mode muss 'latest' oder 'first' sein")


def calc_qoq_saar(level_series: pd.Series) -> pd.Series:
    # QoQ SAAR: ((x_t / x_{t-1})^4 - 1) * 100
    return ((level_series / level_series.shift(1)) ** 4 - 1) * 100


# ---------------- UI ----------------
st.title("Macro Dashboard – Philly Fed RTDSM (Vintage-sicher)")

with st.sidebar:
    st.markdown("### Einstellungen")
    choice = st.radio(
        "Welche Vintage-Reihe verwenden?",
        ("Latest (aktuellster Wert je Quartal)", "First release (erste Schätzung je Quartal)"),
    )
    mode = "latest" if choice.startswith("Latest") else "first"

try:
    raw = load_and_process_data()
except Exception as e:
    st.error(f"Fehler beim Laden der Philly-Fed-Excel-Datei: {e}")
    st.stop()

df = raw.copy()
df["value"] = pick_vintage_values(df, mode=mode)
df = df.sort_values("Date")
df["qoq_saar"] = calc_qoq_saar(df["value"])

st.subheader("QoQ SAAR (annualisiert)")
st.line_chart(df.set_index("Date")["qoq_saar"])

st.subheader("Auszug")
st.dataframe(df[["Date", "value", "qoq_saar"]], use_container_width=True)

with st.expander("Rohdaten inkl. Vintagespalten"):
    st.dataframe(df, use_container_width=True)
