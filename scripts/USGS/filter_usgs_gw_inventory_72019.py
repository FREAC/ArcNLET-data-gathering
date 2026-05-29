# ---------------------------------------------------------------------------
# Script Name: filter_usgs_gw_inventory_72019.py
# Author: Nicholas Chang
# Created: 2026-05-29
# Purpose:
#   Read all USGS groundwater inventory batch CSVs in a folder and create one
#   combined CSV containing only rows where parm_cd is 72019.
#
# Notes:
#   parm_cd 72019 = depth to water level, feet below land surface.
#   The script uses the parm_cd column name when available.
#   If parm_cd is not found by name, it falls back to column 14, which is
#   index 13 in Python because Python uses zero-based indexing.
# ---------------------------------------------------------------------------

from pathlib import Path
from datetime import datetime
import re
import pandas as pd
import sys
print(sys.executable)


# ---------------------------------------------------------------------------
# USER SETTINGS
# ---------------------------------------------------------------------------

input_folder = Path(
    r"C:\Users\nc18e\OneDrive - Florida State University\FREAC\ArcNLET\GIS_Data\USGS_Data\USGS_GWandSW_Locations\Data\GW_Data_Inventory_RDB_to_CSV_20260528_102207\csv"
)

target_parm_cd = "72019"

file_pattern = "batch_*_GW_DV_IV_inventory*.csv"

run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

output_folder = input_folder / "_filtered_parm_cd_72019_TEST"
output_folder.mkdir(parents=True, exist_ok=True)

output_csv = output_folder / f"USGS_GW_DV_IV_inventory_parm_cd_{target_parm_cd}_combined_{run_timestamp}.csv"
summary_csv = output_folder / f"USGS_GW_DV_IV_inventory_parm_cd_{target_parm_cd}_summary_{run_timestamp}.csv"
error_csv = output_folder / f"USGS_GW_DV_IV_inventory_parm_cd_{target_parm_cd}_errors_{run_timestamp}.csv"


# ---------------------------------------------------------------------------
# FUNCTIONS
# ---------------------------------------------------------------------------

def clean_column_names(df):
    """
    Removes leading and trailing spaces from column names.
    This helps avoid errors caused by headers such as ' parm_cd '.
    """
    df.columns = df.columns.astype(str).str.strip()
    return df


def clean_parm_cd_value(series):
    """
    Standardizes parm_cd values so that comparisons work consistently.

    Examples:
        '72019'   -> '72019'
        ' 72019 ' -> '72019'
        '72019.0' -> '72019'
    """
    return (
        series.astype("string")
        .str.strip()
        .str.replace(r"\.0+$", "", regex=True)
    )


def find_parm_cd_column(df):
    """
    Finds the parm_cd column.

    First choice:
        Use the column named parm_cd, ignoring case and surrounding spaces.

    Fallback:
        Use column 14 from the CSV, which is index 13 in Python.

    This fallback matches your observation that parm_cd is column 14.
    """
    normalized_columns = {
        col.strip().lower(): col
        for col in df.columns
    }

    if "parm_cd" in normalized_columns:
        return normalized_columns["parm_cd"]

    fallback_index = 13

    if len(df.columns) > fallback_index:
        return df.columns[fallback_index]

    raise ValueError(
        "Could not find a parm_cd column, and the file does not have at least 14 columns."
    )


def read_inventory_csv(csv_path):
    """
    Reads one inventory CSV.

    The script first assumes the file is comma-delimited.
    If the file opens as only one column, it tries tab-delimited reading.
    This makes the script safer in case one file still resembles an RDB/table export.
    """
    try:
        df = pd.read_csv(
            csv_path,
            dtype=str,
            sep=",",
            comment="#",
            engine="python",
            on_bad_lines="skip"
        )

        if len(df.columns) > 1:
            return clean_column_names(df), "comma"

    except Exception:
        pass

    df = pd.read_csv(
        csv_path,
        dtype=str,
        sep="\t",
        comment="#",
        engine="python",
        on_bad_lines="skip"
    )

    return clean_column_names(df), "tab"


# ---------------------------------------------------------------------------
# MAIN SCRIPT
# ---------------------------------------------------------------------------

csv_files = sorted(input_folder.glob(file_pattern))

print(f"Input folder: {input_folder}")
print(f"File pattern: {file_pattern}")
print(f"CSV files found: {len(csv_files):,}")
print(f"Target parm_cd: {target_parm_cd}")
print("")

if not csv_files:
    raise FileNotFoundError(
        f"No CSV files were found in {input_folder} using pattern {file_pattern}"
    )

first_write = True
total_rows_read = 0
total_rows_kept = 0

summary_records = []
error_records = []

for file_number, csv_path in enumerate(csv_files, start=1):
    print(f"{file_number:,}/{len(csv_files):,}: Reading {csv_path.name}")

    try:
        df, delimiter_used = read_inventory_csv(csv_path)

        rows_read = len(df)
        total_rows_read += rows_read

        parm_cd_column = find_parm_cd_column(df)

        df["parm_cd_clean"] = clean_parm_cd_value(df[parm_cd_column])

        filtered = df[df["parm_cd_clean"].eq(target_parm_cd)].copy()

        rows_kept = len(filtered)
        total_rows_kept += rows_kept

        if rows_kept > 0:
            filtered.insert(0, "source_file", csv_path.name)
            filtered.insert(1, "source_file_number", file_number)
            filtered.insert(2, "parm_cd_column_used", parm_cd_column)

            filtered = filtered.drop(columns=["parm_cd_clean"])

            filtered.to_csv(
                output_csv,
                mode="w" if first_write else "a",
                header=first_write,
                index=False
            )

            first_write = False

        summary_records.append(
            {
                "source_file": csv_path.name,
                "source_file_number": file_number,
                "delimiter_used": delimiter_used,
                "parm_cd_column_used": parm_cd_column,
                "rows_read": rows_read,
                "rows_kept_parm_cd_72019": rows_kept,
                "status": "success"
            }
        )

        print(f"    Rows read: {rows_read:,}")
        print(f"    Rows kept: {rows_kept:,}")

    except Exception as e:
        error_records.append(
            {
                "source_file": csv_path.name,
                "source_file_number": file_number,
                "error": str(e)
            }
        )

        summary_records.append(
            {
                "source_file": csv_path.name,
                "source_file_number": file_number,
                "delimiter_used": None,
                "parm_cd_column_used": None,
                "rows_read": None,
                "rows_kept_parm_cd_72019": None,
                "status": "failed"
            }
        )

        print(f"    FAILED: {e}")

    print("")


summary_df = pd.DataFrame(summary_records)
summary_df.to_csv(summary_csv, index=False)

if error_records:
    error_df = pd.DataFrame(error_records)
    error_df.to_csv(error_csv, index=False)

print("Done.")
print(f"Total files checked: {len(csv_files):,}")
print(f"Total rows read: {total_rows_read:,}")
print(f"Total rows kept where parm_cd == {target_parm_cd}: {total_rows_kept:,}")
print(f"Output CSV: {output_csv}")
print(f"Summary CSV: {summary_csv}")

if error_records:
    print(f"Error CSV: {error_csv}")
else:
    print("No errors were recorded.")