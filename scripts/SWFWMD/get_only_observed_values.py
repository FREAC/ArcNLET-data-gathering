from pathlib import Path
import pandas as pd

in_csv = Path(
    r"C:\Users\nc18e\OneDrive - Florida State University\FREAC\ArcNLET\GIS_Data\SWFWMD_SWandGW_Locations\Data\Groundwater\WholeRange\ALL_GW_wells_NearCharCO_timeseries_inventory_Daily_Maximum_NAVD88_values.csv"
    )

out_csv = in_csv.with_name(
    "ALL_GW_stations_NearCharCO_Daily_Mean_NAVD88_observed_only.csv"
)

bad_quality_codes = {"-1", "255"}

first_write = True
total_in = 0
total_out = 0

for chunk in pd.read_csv(in_csv, dtype=str, chunksize=250_000):
    total_in += len(chunk)

    # Clean column names
    chunk.columns = chunk.columns.str.strip()

    # Convert Value to numeric.
    # Missing/non-numeric values become NaN.
    chunk["Value_numeric"] = pd.to_numeric(chunk["Value"], errors="coerce")

    # Keep only rows with:
    # 1. an actual numeric water-level value
    # 2. a quality code that is not -1 or 255
    clean = chunk[
        chunk["Value_numeric"].notna()
        & ~chunk["Quality Code"].astype(str).str.strip().isin(bad_quality_codes)
    ].copy()

    # Replace original Value with numeric-cleaned value
    clean["Value"] = clean["Value_numeric"]

    # Drop helper column
    clean = clean.drop(columns=["Value_numeric"])

    clean.to_csv(
        out_csv,
        mode="w" if first_write else "a",
        header=first_write,
        index=False
    )

    first_write = False
    total_out += len(clean)

print(f"Input rows:  {total_in:,}")
print(f"Output rows: {total_out:,}")
print(f"Dropped rows: {total_in - total_out:,}")
print(f"Wrote: {out_csv}")