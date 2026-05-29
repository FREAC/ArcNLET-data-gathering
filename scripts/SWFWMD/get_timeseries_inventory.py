from pathlib import Path
from io import StringIO
import time
import requests
import pandas as pd

BASE_URL = "https://edp.swfwmd.state.fl.us/KiWIS/KiWIS"

# -------------------------------------------------------------------
# CHANGE THESE PATHS
# -------------------------------------------------------------------

station_csv = Path(r"C:\Users\nc18e\OneDrive - Florida State University\FREAC\ArcNLET\GIS_Data\SWFWMD_SWandGW_Locations\Data\Groundwater\WholeRange\ALL_GW_wells_NearCharCO.csv")

#output_csv = Path(
#    r""
#)

output_csv = station_csv.with_name(
    "ALL_GW_wells_NearCharCO_timeseries_inventory.csv"
)

CHUNK_SIZE = 50  # Lower to 100 or 50 if the server times out.

# -------------------------------------------------------------------
# FUNCTIONS
# -------------------------------------------------------------------

def clean_station_no(series):
    return (
        series.astype("string")
        .str.strip()
        .str.replace(r"\.0+$", "", regex=True)
    )

def chunks(values, size):
    for i in range(0, len(values), size):
        yield values[i:i + size]

def fetch_timeseries_list(station_batch):
    params = {
        "datasource": "1",
        "service": "kisters",
        "type": "queryServices",
        "request": "getTimeseriesList",
        "returnfields": (
            "station_no,station_name,ts_name,ts_path,"
            "parametertype_name,stationparameter_longname,"
            "ts_clientvalue1,coverage"
        ),
        "station_no": ",".join(station_batch),
        "format": "csv",
        "csvdiv": ",",
    }

    response = requests.get(BASE_URL, params=params, timeout=120)
    response.raise_for_status()

    text = response.text.strip()

    if not text:
        return pd.DataFrame()

    if text.lower().startswith("<html"):
        raise RuntimeError(f"Server returned HTML instead of CSV:\n{text[:1000]}")

    return pd.read_csv(StringIO(text), dtype=str)

# -------------------------------------------------------------------
# MAIN
# -------------------------------------------------------------------

stations = pd.read_csv(station_csv, dtype=str)

if "station_no" not in stations.columns:
    raise ValueError("Your CSV must have a column named station_no")

stations["station_no"] = clean_station_no(stations["station_no"])

station_ids = (
    stations["station_no"]
    .dropna()
    .drop_duplicates()
    .tolist()
)

print(f"Station records in CSV: {len(stations):,}")
print(f"Unique station IDs to query: {len(station_ids):,}")

all_results = []

for batch_num, batch in enumerate(chunks(station_ids, CHUNK_SIZE), start=1):
    print(f"Batch {batch_num}: {batch[0]} through {batch[-1]}")

    try:
        result = fetch_timeseries_list(batch)

        if not result.empty:
            all_results.append(result)
            print(f"  Time series found: {len(result):,}")
        else:
            print("  No time series returned.")

    except Exception as e:
        print(f"  FAILED batch starting with station {batch[0]}")
        print(f"  Error: {e}")

    time.sleep(0.25)

if not all_results:
    print("No time series were returned.")
    raise SystemExit

inventory = pd.concat(all_results, ignore_index=True)
inventory["station_no"] = clean_station_no(inventory["station_no"])

# Keep one metadata row per station_no from your original CSV
station_metadata = stations.drop_duplicates("station_no").copy()

# Avoid duplicate station_name confusion:
# SWFWMD API also returns station_name, so rename your original one.
if "station_name" in station_metadata.columns:
    station_metadata = station_metadata.rename(
        columns={"station_name": "station_name_from_station_csv"}
    )

# Join your station metadata onto the time-series inventory
inventory_full = inventory.merge(
    station_metadata,
    on="station_no",
    how="left"
)

inventory_full = inventory_full.drop_duplicates()

inventory_full.to_csv(output_csv, index=False)

print(f"\nDone.")
print(f"Wrote: {output_csv}")
print(f"Total time series records: {len(inventory_full):,}")