import io
import re
import requests
import pandas as pd
import streamlit as st

EXCEL_URL = "https://www.philadelphiafed.org/-/media/FRBP/Assets/Surveys-And-Data/real-time-data/data-files/xlsx/ROUTPUTQvQd.xlsx?sc_lang=en&hash=34FA1C6BF0007996E1885C8C32E3BEF9"

st.set_page_config(page_title="Macro Dashboard", layout="wide")


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def download_excel_bytes(url: str) -> bytes:
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content


def _excel_serial_to_datetime(x):
    # Excel serial date -> datetime (Excel epoch)
    # (Falls wirklich Excel-Seriennummern im Header stehen.)
    try:
        return pd.to_datetime(float(x), unit="D", origin="1899-12-30", errors="coerce")
    except Exception:
        return pd.NaT


def make_unique(names):
    seen = {}
    out = []
    for n in names:
        key = str(n)
        if key not in seen:
            seen[key] = 0
            out.append(key)
        else:
            seen[key] += 1
            out.append(f"{key}_{seen[key]}")
    return out


def normalize_vintage_colname(name):
    """
    Versucht, Vintage-Spaltennamen in lesbare Datumsstrings zu verwandeln.
    - datetime/Timestamp -> YYYY-MM-DD
    - Excel-Serial (Zahl) -> YYYY-MM-DD
    - Strings, die wie Excel-Serial + Duplicate-Suffix aussehen (z.B. '45234.0.1') -> Datum + _1
    - sonst: als String belassen
    """
    if name is None or (isinstance(name, float) and pd.isna(name)):
        return None

    # pandas kann manchmal Duplikate als ".1" ".2" an Strings anhängen
    s = str(name).strip()

    # Falls bereits Timestamp-ähnlich
    dt = pd.to_datetime(s, errors="coerce")
    if pd.notna(dt):
        return dt.strftime("%Y-%m-%d")

    # Muster: "12345" oder "12345.0" oder "12345.0.1"
    m = re.match(r"^(?P<num>\d+(?:\.\d+)?)(?:\.(?P<dup>\d+))?$", s)
    if m:
        num = m.group("num")
        dup = m.group("dup")
        dt2 = _excel_serial_to_datetime(num)
        if pd.notna(dt2):
            base = dt2.strftime("%Y-%m-%d")
            return f"{base}_{dup}" if dup else base

    return s


def parse_quarter_dates(date_series: pd.Series) -> pd.Series:
    # 1) normaler Parse
    dt = pd.to_datetime(date_series, errors="coerce")

    # 2) Quartalsstrings wie "1965:Q1", "1965Q1", "1965-Q1"
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
            dt.loc[qmask] = periods.to_timestamp(how="end").normalize()

    return dt


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def load_and_process_data() -> pd.DataFrame:
    content = download_excel_bytes(EXCEL_URL)
    xls = pd.ExcelFile(io.BytesIO(content), engine="openpyxl")

    sheet = "ROUTPUT" if "ROUTPUT" in xls.sheet_names else xls.sheet_names[0]

    # Komplett ohne Header laden, dann Headerzeile dynamisch finden
    raw = pd.read_excel(xls, sheet_name=sheet, header=None, engine="openpyxl")
    raw = raw.dropna(how="all").reset_index(drop=True)

    # Finde die Zeile, deren erste Spalte "Date" ist
    first_col = raw.iloc[:, 0].astype(str).str.strip().str.lower()
    date_rows = raw.index[first_col.eq("date")].tolist()
    if not date_rows:
        # manchmal steht "DATE" oder "Date " etc. -> contains
        date_rows = raw.index[first_col.str.contains(r"\bdate\b", na=False)].tolist()

    if not date_rows:
        raise RuntimeError("Konnte keine Header-Zeile mit 'Date' finden.")

    header_row = date_rows[0]
    vintage_row = header_row + 1
    data_start = vintage_row + 1

    if vintage_row >= len(raw):
        raise RuntimeError("Vintage-Header-Zeile fehlt (Header+1 existiert nicht).")

    # Column names aus Vintage-Zeile ziehen
    colnames = raw.iloc[vintage_row].tolist()
    colnames[0] = "Date"

    # Wenn Vintage-Zeile leer/NaN ist, fallback auf Headerzeile
    if all(pd.isna(x) for x in colnames[1:]):
        colnames = raw.iloc[header_row].tolist()
        colnames[0] = "Date"

    # Normalisieren + Duplikate eindeutig machen
    norm = []
    for i, c in enumerate(colnames):
        if i == 0:
            norm.append("Date")
        else:
            nc = normalize_vintage_colname(c)
            norm.append(nc if nc not in (None, "nan", "NaN") else f"Vintage_{i}")
    norm = make_unique(norm)

    df = raw.iloc[data_start:].copy()
    df.columns = norm

    # Leere Zeilen/Spalten entfernen
    df = df.dropna(how="all").dropna(axis=1, how="all")

    # Date parsen
    df["Date"] = parse_quarter_dates(df["Date"])
    df = df.dropna(subset=["Date"]).sort_values("Date")

    # Vintage-Spalten numerisch
    value_cols = [c for c in df.columns if c != "Date"]
    df[value_cols] = df[value_cols].apply(pd.to_numeric, errors="coerce")

    # Spalten raus, die komplett NaN sind
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

    # Sortiere Vintage-Spalten (sofern Datumsstrings)
    # Wenn nicht sortierbar: bleibt Reihenfolge wie im File
    try:
        parsed = pd.to_datetime([c.split("_")[0] for c in data.columns], errors="coerce")
        if parsed.notna().any():
            order = list(pd.Series(data.columns).iloc[parsed.argsort()])
            data = data[order]
    except Exception:
        pass

    if mode == "latest":
        return data.ffill(axis=1).iloc[:, -1]
    if mode == "first":
        return data.bfill(axis=1).iloc[:, 0]

    raise ValueError("mode muss 'latest' oder 'first' sein")


def calc_qoq_saar(level_series: pd.Series) -> pd.Series:
    return ((level_series / level_series.shift(1)) ** 4 - 1) * 100


# ---------------- UI ----------------
st.title("Macro Dashboard – Philly Fed RTDSM (Vintage-sicher)")

with st.sidebar:
    st.markdown("### Einstellungen")
    choice = st.radio(
        "Vintage-Auswahl",
        ("Latest (aktuellster Wert je Quartal)", "First release (erste Schätzung je Quartal)"),
    )
    mode = "latest" if choice.startswith("Latest") else "first"


try:
    raw = load_and_process_data()
except Exception as e:
    st.error(f"Fehler beim Laden/Parsen der Excel-Datei: {e}")
    st.stop()

df = raw.copy()
df["value"] = pick_vintage_values(df, mode=mode)
df["qoq_saar"] = calc_qoq_saar(df["value"])

# Rollender Z-Score (20 Jahre bei Quartalsdaten = 80 Quartale)
WINDOW_Q = 20 * 4
roll_mean = df["qoq_saar"].rolling(WINDOW_Q, min_periods=WINDOW_Q).mean()
roll_std = df["qoq_saar"].rolling(WINDOW_Q, min_periods=WINDOW_Q).std(ddof=0)
df["zscore_20y"] = (df["qoq_saar"] - roll_mean) / roll_std

st.subheader("QoQ SAAR (annualisiert)")
st.line_chart(df.set_index("Date")["qoq_saar"])

st.subheader("Z-Score (QoQ SAAR, rollend 20 Jahre)")
st.line_chart(df.set_index("Date")["zscore_20y"])

st.subheader("Auszug")
st.dataframe(df[["Date", "value", "qoq_saar", "zscore_20y"]], use_container_width=True)

with st.expander("Rohdaten inkl. Vintagespalten"):
    st.dataframe(df, use_container_width=True)
