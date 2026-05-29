from pathlib import Path
import pandas as pd

inventory_csv = Path(
    r"C:\Users\nc18e\OneDrive - Florida State University\FREAC\ArcNLET\GIS_Data\SWFWMD_SWandGW_Locations\Data\Groundwater\WholeRange\ALL_GW_wells_NearCharCO_timeseries_inventory.csv"
)

summary_csv = inventory_csv.with_name(
    "ALL_GW_wells_NearCharCO_timeseries_inventory_parameter_summary.csv"
)

df = pd.read_csv(inventory_csv, dtype=str)

# Clean column names
df.columns = df.columns.str.strip().str.replace(" ", "_")

print("Columns found:")
for col in df.columns:
    print(f"  {col}")

# Make sure expected columns exist
expected_cols = [
    "station_no",
    "ts_path",
    "ts_name",
    "parametertype_name",
    "stationparameter_longname",
    "ts_clientvalue1",
    "from",
    "to",
]

for col in expected_cols:
    if col not in df.columns:
        df[col] = ""

# Clean date fields as strings first
df["from"] = df["from"].fillna("").astype(str).str.strip()
df["to"] = df["to"].fillna("").astype(str).str.strip()

# Also make grouping fields safe
for col in ["parametertype_name", "stationparameter_longname", "ts_clientvalue1"]:
    df[col] = df[col].fillna("").astype(str).str.strip()

# Convert date fields to real datetimes for min/max
df["from_dt"] = pd.to_datetime(df["from"], errors="coerce", utc=True)
df["to_dt"] = pd.to_datetime(df["to"], errors="coerce", utc=True)

summary = (
    df.groupby(
        ["parametertype_name", "stationparameter_longname", "ts_clientvalue1"],
        dropna=False
    )
    .agg(
        time_series_count=("ts_path", "count"),
        station_count=("station_no", "nunique"),
        earliest_from=("from_dt", "min"),
        latest_to=("to_dt", "max"),
    )
    .reset_index()
    .sort_values(
        ["parametertype_name", "stationparameter_longname", "ts_clientvalue1"]
    )
)

# Optional: remove timezone formatting weirdness for Excel
summary["earliest_from"] = summary["earliest_from"].dt.strftime("%Y-%m-%d")
summary["latest_to"] = summary["latest_to"].dt.strftime("%Y-%m-%d")

summary.to_csv(summary_csv, index=False)

print(f"\nWrote summary to:")
print(summary_csv)
print(f"\nSummary rows: {len(summary):,}")