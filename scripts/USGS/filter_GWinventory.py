"""
Script Name: filter_GWinventory.py
Author: Nicholas Chang
Created: 2026-06-03
Last Updated: 2026-06-03
Purpose:
    Filter the completed USGS groundwater main inventory CSV to keep only
    inventory rows for groundwater parameters selected for ArcNLET-Py
    calibration data review.

    Target groundwater parameter codes:
        62611 = Groundwater level above NAVD88, feet
        72019 = Depth to water level, feet below land surface

Output:
    1. One filtered CSV for each matching filter rule.
    2. One summary CSV showing row counts and unique site counts for each rule.

Notes:
    - This script does not download data from USGS.
    - This script does not modify the input main inventory CSV.
    - The output CSVs preserve the original inventory columns.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd


# ---------------------------------------------------------------------------
# USER SETTINGS
# ---------------------------------------------------------------------------

INPUT_FILE = Path(
    r"C:\ArcNLET-CharlotteCalibrationData\USGS_Data\USGS_GWandSW_Locations\Data\SecondTry\02_inventory\GW\GW_MainInventory_20260603_155451.csv"
)

BASE_FILTERED_FOLDER = Path(
    r"C:\ArcNLET-CharlotteCalibrationData\USGS_Data\USGS_GWandSW_Locations\Data\SecondTry\03_filtered"
)

OUTPUT_FOLDER = BASE_FILTERED_FOLDER / "GW"

WRITE_EMPTY_FILTER_OUTPUTS = False

OUTPUT_ENCODING = "utf-8-sig"


# ---------------------------------------------------------------------------
# FILTER SETTINGS
# ---------------------------------------------------------------------------

FILTER_RULES = [
    {
        "filter_name": "GW_gw_parm62611_groundwater_level_NAVD88",
        "data_type_cd": "gw",
        "parm_cd": "62611",
        "stat_cd": None,
        "meaning": "Irregular groundwater-level measurements, groundwater level above NAVD88, feet",
    },
    {
        "filter_name": "GW_gw_parm72019_depth_to_water_below_land_surface",
        "data_type_cd": "gw",
        "parm_cd": "72019",
        "stat_cd": None,
        "meaning": "Irregular groundwater-level measurements, depth to water level, feet below land surface",
    },
    {
        "filter_name": "GW_uv_parm62611_instantaneous_groundwater_level_NAVD88",
        "data_type_cd": "uv",
        "parm_cd": "62611",
        "stat_cd": None,
        "meaning": "Instantaneous/unit-value groundwater level above NAVD88, feet",
    },
    {
        "filter_name": "GW_dv_parm62611_stat00001_daily_max_groundwater_level_NAVD88",
        "data_type_cd": "dv",
        "parm_cd": "62611",
        "stat_cd": "00001",
        "meaning": "Daily maximum groundwater level above NAVD88, feet",
    },
]

REQUIRED_COLUMNS = [
    "site_no",
    "data_type_cd",
    "parm_cd",
    "stat_cd",
]


# ---------------------------------------------------------------------------
# HELPER FUNCTIONS
# ---------------------------------------------------------------------------

def clean_code(value: object, width: int | None = None) -> str:
    """
    Convert a USGS code value into a clean string.

    This handles values that may appear as:
        62611
        62611.0
        00003
        3.0

    The optional width argument is used when a code should preserve leading
    zeroes, such as stat_cd = 00003.
    """
    code = str(value).strip()

    if code.lower() in {"", "nan", "none", "<na>"}:
        return ""

    if code.endswith(".0") and code[:-2].isdigit():
        code = code[:-2]

    if width is not None and code.isdigit():
        code = code.zfill(width)

    return code


def validate_input_table(inventory_df: pd.DataFrame) -> None:
    """
    Confirm that the input inventory table contains the columns needed
    for filtering.
    """
    missing_columns = [
        column
        for column in REQUIRED_COLUMNS
        if column not in inventory_df.columns
    ]

    if missing_columns:
        available_columns = ", ".join(inventory_df.columns)

        raise KeyError(
            "The input inventory table is missing required columns: "
            + ", ".join(missing_columns)
            + f"\nAvailable columns: {available_columns}"
        )

    if inventory_df.empty:
        raise ValueError("The input groundwater inventory table is empty.")


def add_filter_fields(inventory_df: pd.DataFrame) -> pd.DataFrame:
    """
    Add internal normalized fields used only for filtering.

    These fields prevent filtering errors caused by values such as 62611.0
    instead of 62611.
    """
    inventory_df = inventory_df.copy()

    inventory_df["_filter_data_type_cd"] = (
        inventory_df["data_type_cd"]
        .astype("string")
        .str.strip()
        .str.lower()
    )

    inventory_df["_filter_parm_cd"] = inventory_df["parm_cd"].apply(
        lambda value: clean_code(value, width=5)
    )

    inventory_df["_filter_stat_cd"] = inventory_df["stat_cd"].apply(
        lambda value: clean_code(value, width=5)
    )

    return inventory_df


def filter_inventory(
    inventory_df: pd.DataFrame,
    rule: dict[str, object],
    original_columns: list[str],
) -> pd.DataFrame:
    """
    Apply one filter rule to the groundwater inventory table.
    """
    data_type_cd = str(rule["data_type_cd"]).strip().lower()

    parm_cd = clean_code(rule["parm_cd"], width=5)

    stat_cd = rule["stat_cd"]

    mask = (
        inventory_df["_filter_data_type_cd"].eq(data_type_cd)
        & inventory_df["_filter_parm_cd"].eq(parm_cd)
    )

    if stat_cd is not None:
        normalized_stat_cd = clean_code(stat_cd, width=5)
        mask = mask & inventory_df["_filter_stat_cd"].eq(normalized_stat_cd)

    filtered_df = inventory_df.loc[mask, original_columns].copy()

    return filtered_df


def get_min_date(filtered_df: pd.DataFrame, column_name: str) -> str:
    """
    Return the earliest valid date from a date column.
    """
    if column_name not in filtered_df.columns or filtered_df.empty:
        return ""

    dates = pd.to_datetime(filtered_df[column_name], errors="coerce")

    if dates.dropna().empty:
        return ""

    return dates.min().strftime("%Y-%m-%d")


def get_max_date(filtered_df: pd.DataFrame, column_name: str) -> str:
    """
    Return the latest valid date from a date column.
    """
    if column_name not in filtered_df.columns or filtered_df.empty:
        return ""

    dates = pd.to_datetime(filtered_df[column_name], errors="coerce")

    if dates.dropna().empty:
        return ""

    return dates.max().strftime("%Y-%m-%d")


def build_summary_record(
    rule: dict[str, object],
    filtered_df: pd.DataFrame,
    output_csv: Path | None,
) -> dict[str, object]:
    """
    Build one summary record for the filter summary CSV.
    """
    if "site_no" in filtered_df.columns and not filtered_df.empty:
        unique_site_count = filtered_df["site_no"].nunique(dropna=True)
    else:
        unique_site_count = 0

    if output_csv is None:
        output_csv_text = ""
        status = "no_rows_found_no_csv_written"
    else:
        output_csv_text = str(output_csv)
        status = "csv_written"

    summary_record = {
        "filter_name": rule["filter_name"],
        "data_type_cd": rule["data_type_cd"],
        "parm_cd": rule["parm_cd"],
        "stat_cd": rule["stat_cd"] if rule["stat_cd"] is not None else "",
        "meaning": rule["meaning"],
        "row_count": len(filtered_df),
        "unique_site_count": unique_site_count,
        "earliest_begin_date": get_min_date(filtered_df, "begin_date"),
        "latest_end_date": get_max_date(filtered_df, "end_date"),
        "output_csv": output_csv_text,
        "status": status,
    }

    return summary_record


# ---------------------------------------------------------------------------
# MAIN SCRIPT
# ---------------------------------------------------------------------------

def main() -> None:
    """
    Run the groundwater inventory filtering workflow.
    """
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)

    summary_csv = OUTPUT_FOLDER / f"GW_filter_summary_{run_timestamp}.csv"

    print("Starting groundwater inventory filtering workflow.")
    print(f"Input inventory CSV: {INPUT_FILE}")
    print(f"Output folder: {OUTPUT_FOLDER}")

    inventory_df = pd.read_csv(
        INPUT_FILE,
        dtype=str,
        keep_default_na=False,
    )

    validate_input_table(inventory_df)

    original_columns = inventory_df.columns.tolist()

    inventory_df = add_filter_fields(inventory_df)

    summary_records = []

    for rule in FILTER_RULES:
        filtered_df = filter_inventory(
            inventory_df=inventory_df,
            rule=rule,
            original_columns=original_columns,
        )

        if filtered_df.empty and not WRITE_EMPTY_FILTER_OUTPUTS:
            output_csv = None
        else:
            output_csv = OUTPUT_FOLDER / f"{rule['filter_name']}_{run_timestamp}.csv"

            filtered_df.to_csv(
                output_csv,
                index=False,
                encoding=OUTPUT_ENCODING,
            )

        summary_record = build_summary_record(
            rule=rule,
            filtered_df=filtered_df,
            output_csv=output_csv,
        )

        summary_records.append(summary_record)

        print(
            f"{rule['filter_name']}: "
            f"{len(filtered_df):,} rows, "
            f"{summary_record['unique_site_count']:,} unique sites"
        )

    summary_df = pd.DataFrame(summary_records)

    summary_df.to_csv(
        summary_csv,
        index=False,
        encoding=OUTPUT_ENCODING,
    )

    print("Groundwater inventory filtering complete.")
    print(f"Filter summary CSV: {summary_csv}")


if __name__ == "__main__":
    main()