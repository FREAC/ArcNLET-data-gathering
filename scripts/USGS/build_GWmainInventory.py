"""
Script Name: build_GWmainInventory.py
Author: Nicholas Chang
Created: 2026-06-03
Purpose:
    Build batched USGS NWIS inventory request URLs for groundwater wells
    and download the combined groundwater inventory table.

Project:
    ArcNLET Charlotte Calibration Data - USGS Groundwater Inventory

Notes:
    - Reads cleaned groundwater station metadata.
    - Extracts unique USGS site numbers.
    - Builds NWIS Site Service inventory URLs in batches of 30 sites.
    - Downloads RDB inventory responses from USGS.
    - Removes the USGS RDB type row so it does not bleed into the CSV output.
    - Saves one URL/status CSV and one combined inventory CSV.
"""

from __future__ import annotations

from datetime import datetime
from io import StringIO
from pathlib import Path
from urllib.parse import urlencode
import re
import time

import pandas as pd
import requests


# ---------------------------------------------------------------------------
# User settings
# ---------------------------------------------------------------------------

INPUT_FILE = Path(
    r"C:\ArcNLET-CharlotteCalibrationData\USGS_Data\USGS_GWandSW_Locations\Data\SecondTry\01_metadata\GW\GW_StationMetadata_clean_20260603_152416.csv"
)

BASE_INVENTORY_FOLDER = Path(
    r"C:\ArcNLET-CharlotteCalibrationData\USGS_Data\USGS_GWandSW_Locations\Data\SecondTry\02_inventory"
)

OUTPUT_FOLDER = BASE_INVENTORY_FOLDER / "GW"

BATCH_SIZE = 30

NWIS_SITE_SERVICE_URL = "https://waterservices.usgs.gov/nwis/site/"

REQUEST_TIMEOUT_SECONDS = 60

REQUEST_DELAY_SECONDS = 0.25

SITE_NUMBER_COLUMN_CANDIDATES = [
    "site_no",
    "monitoring_location_number",
    "site_number",
    "siteNumber",
]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def normalize_site_number(value: object) -> str:
    """Convert a site-number value into a clean string."""

    site_no = str(value).strip()

    if site_no.lower() in {"", "nan", "none", "<na>"}:
        return ""

    if site_no.endswith(".0") and site_no[:-2].isdigit():
        site_no = site_no[:-2]

    return site_no


def get_site_numbers(metadata_df: pd.DataFrame) -> tuple[list[str], str]:
    """Find the site-number column and return unique site numbers in order."""

    site_number_column = None

    for candidate_column in SITE_NUMBER_COLUMN_CANDIDATES:
        if candidate_column in metadata_df.columns:
            site_number_column = candidate_column
            break

    if site_number_column is None:
        available_columns = ", ".join(metadata_df.columns)
        raise KeyError(
            "Could not find a site-number column. "
            f"Checked: {SITE_NUMBER_COLUMN_CANDIDATES}. "
            f"Available columns: {available_columns}"
        )

    site_numbers = []
    seen_site_numbers = set()

    for value in metadata_df[site_number_column]:
        site_no = normalize_site_number(value)

        if not site_no:
            continue

        if site_no not in seen_site_numbers:
            site_numbers.append(site_no)
            seen_site_numbers.add(site_no)

    if not site_numbers:
        raise ValueError(
            f"The column '{site_number_column}' did not contain any usable site numbers."
        )

    return site_numbers, site_number_column


def make_batches(items: list[str], batch_size: int) -> list[list[str]]:
    """Split a list into smaller batches."""

    batches = []

    for start_index in range(0, len(items), batch_size):
        batch = items[start_index:start_index + batch_size]
        batches.append(batch)

    return batches


def build_inventory_url(batch_sites: list[str]) -> str:
    """Build one USGS NWIS Site Service inventory URL for a batch of sites."""

    params = {
        "format": "rdb",
        "sites": ",".join(batch_sites),
        "seriesCatalogOutput": "true",
        "siteStatus": "all",
    }

    query_string = urlencode(params, safe=",")

    return f"{NWIS_SITE_SERVICE_URL}?{query_string}"


def row_looks_like_rdb_type_row(row: pd.Series) -> bool:
    """Check whether a row is the USGS RDB field-width/type row."""

    values = row.fillna("").astype(str).str.strip().tolist()

    non_blank_values = [value for value in values if value]

    if not non_blank_values:
        return False

    return all(re.fullmatch(r"\d+[A-Za-z]", value) for value in non_blank_values)


def read_usgs_rdb_text(rdb_text: str) -> pd.DataFrame:
    """Read a USGS RDB response into a clean pandas DataFrame."""

    if not rdb_text.strip():
        return pd.DataFrame()

    try:
        df = pd.read_csv(
            StringIO(rdb_text),
            sep="\t",
            comment="#",
            dtype=str,
        )

    except pd.errors.EmptyDataError:
        return pd.DataFrame()

    df = df.dropna(how="all")

    if df.empty:
        return df

    if row_looks_like_rdb_type_row(df.iloc[0]):
        df = df.iloc[1:].copy()

    df.columns = [str(column).strip() for column in df.columns]

    for column in df.columns:
        df[column] = df[column].astype("string").str.strip()

    df = df.dropna(how="all")

    return df.reset_index(drop=True)


def build_batch_records(batches: list[list[str]]) -> list[dict[str, object]]:
    """Create the batch URL table before downloading inventory data."""

    batch_records = []

    for batch_index, batch_sites in enumerate(batches, start=1):
        batch_record = {
            "batch_number": f"{batch_index:03d}",
            "site_count": len(batch_sites),
            "first_site_no": batch_sites[0],
            "last_site_no": batch_sites[-1],
            "site_numbers": ",".join(batch_sites),
            "url": build_inventory_url(batch_sites),
            "request_status": "not_requested",
            "http_status_code": "",
            "inventory_row_count": "",
            "error_message": "",
        }

        batch_records.append(batch_record)

    return batch_records


def download_inventory_batches(
    batch_records: list[dict[str, object]]
) -> tuple[list[pd.DataFrame], list[dict[str, object]]]:
    """Download each batch URL and return inventory DataFrames plus status records."""

    inventory_frames = []

    for batch_record in batch_records:
        url = str(batch_record["url"])

        try:
            response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)

            batch_record["http_status_code"] = response.status_code

            response.raise_for_status()

            batch_df = read_usgs_rdb_text(response.text)

            batch_record["inventory_row_count"] = len(batch_df)

            if batch_df.empty:
                batch_record["request_status"] = "success_no_rows"
            else:
                batch_record["request_status"] = "success"
                inventory_frames.append(batch_df)

        except requests.exceptions.RequestException as error:
            batch_record["request_status"] = "request_failed"
            batch_record["error_message"] = str(error)

        except pd.errors.ParserError as error:
            batch_record["request_status"] = "rdb_parse_failed"
            batch_record["error_message"] = str(error)

        time.sleep(REQUEST_DELAY_SECONDS)

    return inventory_frames, batch_records


# ---------------------------------------------------------------------------
# Main script
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the groundwater inventory URL-building and download workflow."""

    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)

    url_csv = OUTPUT_FOLDER / f"GW_MainInventory_URLs_{run_timestamp}.csv"

    inventory_csv = OUTPUT_FOLDER / f"GW_MainInventory_{run_timestamp}.csv"

    print("Starting groundwater main inventory build.")
    print(f"Input metadata file: {INPUT_FILE}")
    print(f"Output folder: {OUTPUT_FOLDER}")

    metadata_df = pd.read_csv(INPUT_FILE, dtype=str)

    site_numbers, site_number_column = get_site_numbers(metadata_df)

    batches = make_batches(site_numbers, BATCH_SIZE)

    batch_records = build_batch_records(batches)

    inventory_frames, batch_records = download_inventory_batches(batch_records)

    url_df = pd.DataFrame(batch_records)

    url_df.to_csv(url_csv, index=False)

    if not inventory_frames:
        raise RuntimeError(
            "No inventory rows were downloaded. "
            f"Check the URL/status CSV here: {url_csv}"
        )

    inventory_df = pd.concat(inventory_frames, ignore_index=True)

    inventory_df.to_csv(inventory_csv, index=False)

    print("Groundwater main inventory build complete.")
    print(f"Site-number column used: {site_number_column}")
    print(f"Unique groundwater wells: {len(site_numbers)}")
    print(f"Batch size: {BATCH_SIZE}")
    print(f"Total batches: {len(batches)}")
    print(f"URL/status CSV: {url_csv}")
    print(f"Combined inventory CSV: {inventory_csv}")


if __name__ == "__main__":
    main()