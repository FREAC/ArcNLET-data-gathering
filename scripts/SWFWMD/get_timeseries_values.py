from pathlib import Path
from io import StringIO
import time
import csv
import requests
import pandas as pd

BASE_URL = "https://edp.swfwmd.state.fl.us/KiWIS/KiWIS"

# -------------------------------------------------------------------
# CHANGE THIS PATH IF NEEDED
# -------------------------------------------------------------------

inventory_csv = Path(
    r"C:\Users\nc18e\OneDrive - Florida State University\FREAC\ArcNLET\GIS_Data\SWFWMD_SWandGW_Locations\Data\Groundwater\WholeRange\ALL_GW_wells_NearCharCO_timeseries_inventory.csv"
)


output_values_csv = inventory_csv.with_name(
    "ALL_GW_wells_NearCharCO_timeseries_inventory_Daily_Maximum_NAVD88_values.csv"
)

#failed_csv = inventory_csv.with_name(
#    "ALL_SW_stations_NearCharCO_timeseries_inventory_Daily_Mean_NAVD88_failed.csv"
#)

#output_values_csv = Path(
#    r"C:\Users\nc18e\OneDrive - Florida State University\FREAC\ArcNLET\GIS_Data\SWFWMD_SWandGW_Locations\Data\Surfacewater\WholeRange\ALL_SW_stations_NearCharCO_timeseries_inventory_Daily_Mean_NAVD88_values.csv"
#)

failed_csv = output_values_csv.with_name(
    "ALL_GW_wells_NearCharCO_timeseries_inventory_Daily_Maximum_NAVD88_values_failed.csv"
)

# Use a date range first to keep the first run manageable.
# Later, set both to None for full period of record.
DATE_FROM = None
DATE_TO = None


SLEEP_SECONDS = 0.35
MAX_RETRIES = 3

def parse_kiwis_csv_response(text, sep=","):
    """
    KiWIS returns metadata lines beginning with #, and the actual data
    header may be '#Timestamp,Value,...'. This function finds that header
    and parses only the data table.
    """
    lines = text.strip().splitlines()

    if not lines:
        return pd.DataFrame()

    header_index = None

    for i, line in enumerate(lines):
        try:
            cells = next(csv.reader([line], delimiter=sep))
        except Exception:
            continue

        normalized = [
            c.strip()
             .strip('"')
             .lstrip("#")
             .strip()
             .lower()
            for c in cells
        ]

        if "timestamp" in normalized and "value" in normalized:
            header_index = i
            break

    if header_index is None:
        raise RuntimeError(
            "Could not find Timestamp/Value header in API response. "
            "First 10 lines were:\n" + "\n".join(lines[:10])
        )

    table_text = "\n".join(lines[header_index:])

    df = pd.read_csv(
        StringIO(table_text),
        dtype=str,
        sep=sep,
        engine="python",
        on_bad_lines="skip"
    )

    # Rename '#Timestamp' to 'Timestamp'
    df.columns = [
        col.lstrip("#").strip() for col in df.columns
    ]

    # Remove repeated header rows, if any
    if "Timestamp" in df.columns:
        df = df[df["Timestamp"].astype(str).str.lower() != "timestamp"]

    return df


def fetch_timeseries_values(ts_path):
    params = {
        "datasource": "1",
        "service": "kisters",
        "type": "queryServices",
        "request": "getTimeseriesValues",
        "ts_path": ts_path,
        "returnfields": "Timestamp,Value,Quality Code,Quality Code Description",
        "timezone": "GMT-5",
        "format": "csv",
        "csvdiv": ",",
    }

    # If both dates are None, request the complete period of record.
    # This is required by the KiWIS API.
    if DATE_FROM is None and DATE_TO is None:
        params["period"] = "complete"
    else:
        if DATE_FROM is not None:
            params["from"] = DATE_FROM

        if DATE_TO is not None:
            params["to"] = DATE_TO

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(BASE_URL, params=params, timeout=180)
            response.raise_for_status()

            text = response.text.strip()

            if not text:
                return pd.DataFrame()

            if text.lower().startswith("<html"):
                raise RuntimeError(f"Server returned HTML instead of CSV:\n{text[:1000]}")

            return parse_kiwis_csv_response(text, sep=",")

        except Exception as e:
            print(f"    Attempt {attempt} failed: {e}")

            if attempt == MAX_RETRIES:
                raise

            time.sleep(5 * attempt)

# -------------------------------------------------------------------
# READ INVENTORY
# -------------------------------------------------------------------

inventory = pd.read_csv(inventory_csv, dtype=str)

# Clean column names
inventory.columns = inventory.columns.str.strip().str.replace(" ", "_")

required = ["ts_path", "parametertype_name", "stationparameter_longname", "ts_clientvalue1"]

for col in required:
    if col not in inventory.columns:
        raise ValueError(f"Missing required column: {col}")

for col in required:
    inventory[col] = inventory[col].fillna("").astype(str).str.strip()

# -------------------------------------------------------------------
# FILTER TO NEEDED PARAMETER FROM available_parameter_summary.py
#   Change all Caps in this: "& inventory["ts_clientvalue1_norm"].eq("DISTRICTDAILYMEANNAVD88")" to what you need
#       District Daily Maximum NAVD 88 for GW
# Handles both "NAVD 88" and "NAVD88"
# -------------------------------------------------------------------

def normalize_text(series):
    return (
        series.fillna("")
        .astype(str)
        .str.upper()
        .str.replace(r"[^A-Z0-9]", "", regex=True)
    )

inventory["ts_clientvalue1_norm"] = normalize_text(inventory["ts_clientvalue1"])
inventory["parametertype_name_norm"] = normalize_text(inventory["parametertype_name"])
inventory["stationparameter_longname_norm"] = normalize_text(inventory["stationparameter_longname"])

filtered = inventory[
    inventory["ts_path"].notna()
    & inventory["parametertype_name_norm"].eq("WL")
    & inventory["stationparameter_longname_norm"].str.contains("WATERELEVATION", na=False)
    & inventory["ts_clientvalue1_norm"].eq("DISTRICTDAILYMAXIMUMNAVD88")
].copy()

filtered = filtered.drop_duplicates("ts_path")

print(f"Total inventory rows: {len(inventory):,}")
print(f"Filtered time series to download: {len(filtered):,}")

print("\nFirst 10 selected:")
print(filtered[["station_no", "station_name", "ts_clientvalue1", "ts_path"]].head(10))

# Safety test: uncomment this for a small first test.
# filtered = filtered.head(3)

# -------------------------------------------------------------------
# DOWNLOAD VALUES
# -------------------------------------------------------------------

if output_values_csv.exists():
    output_values_csv.unlink()

if failed_csv.exists():
    failed_csv.unlink()

first_write = True
failures = []

for n, (_, row) in enumerate(filtered.iterrows(), start=1):
    ts_path = row["ts_path"]

    print(
        f"{n:,}/{len(filtered):,}: "
        f"{row.get('station_no')} | "
        f"{row.get('station_name')} | "
        f"{row.get('ts_clientvalue1')}"
    )

    try:
        values = fetch_timeseries_values(ts_path)

        if values.empty:
            print("    No values returned.")
            continue

        # Add useful inventory metadata to every downloaded value row
        values["source_station_no"] = row.get("station_no")
        values["source_station_name"] = row.get("station_name")
        values["source_ts_name"] = row.get("ts_name")
        values["source_ts_path"] = row.get("ts_path")
        values["source_parametertype_name"] = row.get("parametertype_name")
        values["source_stationparameter_longname"] = row.get("stationparameter_longname")
        values["source_ts_clientvalue1"] = row.get("ts_clientvalue1")
        values["source_from"] = row.get("from")
        values["source_to"] = row.get("to")
        values["site_name"] = row.get("site_name")
        values["station_status"] = row.get("station_status")
        values["Station_Type"] = row.get("Station_Type")
        values["GW_Primary_Hydrogeology"] = row.get("GW_Primary_Hydrogeology")
        values["GW_Total_Depth"] = row.get("GW_Total_Depth")
        values["GW_Total_Cased_Depth"] = row.get("GW_Total_Cased_Depth")
        values["GW_Primary_Casing_Diameter"] = row.get("GW_Primary_Casing_Diameter")

        values.to_csv(
            output_values_csv,
            mode="w" if first_write else "a",
            header=first_write,
            index=False
        )

        first_write = False
        print(f"    Rows downloaded: {len(values):,}")

    except Exception as e:
        print(f"    FAILED: {ts_path}")
        print(f"    Error: {e}")

        failures.append({
            "station_no": row.get("station_no"),
            "station_name": row.get("station_name"),
            "ts_name": row.get("ts_name"),
            "ts_clientvalue1": row.get("ts_clientvalue1"),
            "ts_path": ts_path,
            "error": str(e),
        })

    time.sleep(SLEEP_SECONDS)

if failures:
    pd.DataFrame(failures).to_csv(failed_csv, index=False)
    print(f"Failures written to: {failed_csv}")

print("Done.")

if output_values_csv.exists():
    print(f"Values written to: {output_values_csv}")
else:
    print("No values CSV was created. No time series successfully returned data.")