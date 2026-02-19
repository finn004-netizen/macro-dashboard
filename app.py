
try:
    import imghdr
except ImportError:
    import PIL.Image as imghdr

import os
import requests
import pandas as pd
import numpy as np
import streamlit as st

# Download der Excel-Datei von der Philly Fed
EXCEL_URL = "https://www.philadelphiafed.org/-/media/FRBP/Assets/Surveys-And-Data/real-time-data/data-files/xlsx/ROUTPUTQvQd.xlsx?sc_lang=en&hash=34FA1C6BF0007996E1885C8C32E3BEF9"

# Excel herunterladen
def download_excel(url):
    response = requests.get(url)
    response.raise_for_status()  # sicherstellen, dass der Download erfolgreich war
    # Streamlit erlaubt es uns, die Datei temporär zu speichern, ohne in '/mnt/data' zu speichern.
    file_path = "ROUTPUTQvQd.xlsx"
    with open(file_path, "wb") as file:
        file.write(response.content)
    return file_path

# Daten aus Excel-Datei lesen
@st.cache_data(ttl=6 * 60 * 60)  # Cache für 6 Stunden
def load_and_process_data():
    # Lade Excel-Datei
    excel_path = download_excel(EXCEL_URL)
    xls = pd.ExcelFile(excel_path)

    # Schätze die Blattnamen, normalerweise `ROUTPUT`
    sheet_name = xls.sheet_names[0]

    # Lade die Daten
    df = pd.read_excel(xls, sheet_name=sheet_name, header=3)  # Übliche Header-Position bei Philly Fed
    
    # Bereinige und formatiere die Daten
    df = df.dropna(how='all')  # Leere Zeilen entfernen
    df = df.rename(columns={df.columns[0]: 'Date'})  # Der erste Spaltenname ist 'Date'
    
    # Konvertiere alle Vintages in numerische Werte, Fehler werden als NaN behandelt
    df.iloc[:, 1:] = df.iloc[:, 1:].apply(pd.to_numeric, errors='coerce')

    # Konvertiere die 'Date'-Spalte in datetime
    df['Date'] = pd.to_datetime(df['Date'], errors='coerce')

    # Entferne Zeilen mit ungültigen oder leeren Daten
    df = df.dropna(subset=['Date'])
    
    return df

# ---------------------------------
# Funktion zum Berechnen von QoQ
# ---------------------------------
def calc_qoq_saar(df):
    df = df.sort_index()  # Sicherstellen, dass wir nach Datum sortieren

    # Berechne QoQ für jedes Quartal
    df['qoq_saar'] = ((df['value_first'] / df['value_first'].shift(1)) ** 4 - 1) * 100

    return df

# ---------------------------------
# Daten anzeigen und verarbeiten
# ---------------------------------
df = load_and_process_data()

# Speichern der jeweils **aktuellsten Werte** für jedes Quartal
df['value_first'] = df.iloc[:, 1:].max(axis=1)  # Max aus allen Vintages, um den letzten (aktuellsten) Wert zu nehmen

# Berechne QoQ Veränderung mit den richtigen aktuellsten Werten
df = calc_qoq_saar(df)

# Zeige das Dashboard
st.title("Macro Dashboard – First Release / Vintage-sicher")

# Zeige die Daten
st.subheader("Daten und QoQ Berechnungen")
st.write(df)

# Visualisierung der QoQ Veränderung
st.subheader("QoQ Veränderung")
st.line_chart(df.set_index('Date')['qoq_saar'])try:
