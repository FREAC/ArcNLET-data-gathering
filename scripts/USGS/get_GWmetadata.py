"""
Script Name: get_GWmetadata.py
Author: Nicholas Chang
Created: 2026-06-03
Last Updated: 2026-06-03
Purpose:
    Download USGS groundwater well/station metadata for selected Florida counties
    from the USGS NWIS Site Service and save the cleaned metadata as a CSV.

    This script retrieves station/well metadata only. It does not retrieve
    time-series values and does not filter by groundwater parameter codes.

Output:
    1. A raw RDB text file exactly as returned by USGS.
    2. A cleaned CSV file suitable for review and GIS point creation.
"""

from pathlib import Path
from io import StringIO
from datetime import datetime
import re
import time

import requests
import pandas as pd


# ---------------------------------------------------------------------------
# USER SETTINGS
# ---------------------------------------------------------------------------

BASE_METADATA_FOLDER = Path(
    r"C:\ArcNLET-CharlotteCalibrationData\USGS_Data\USGS_GWandSW_Locations\Data\SecondTry\01_metadata"
)

OUTPUT_FOLDER = BASE_METADATA_FOLDER / "GW"

USGS_SITE_SERVICE = "https://waterservices.usgs.gov/nwis/site/"

COUNTY_CODES = [
    "12015",  # Charlotte County, FL
    "12027",  # DeSoto County, FL
    "12043",  # Glades County, FL
    "12071",  # Lee County, FL
    "12115",  # Sarasota County, FL
]

SITE_TYPE = "GW"
SITE_STATUS = "all"
OUTPUT_FORMAT = "rdb"

TIMEOUT_SECONDS = 120
MAX_RETRIES = 3
SLEEP_SECONDS_BETWEEN_RETRIES = 5


# ---------------------------------------------------------------------------
# OUTPUT PATHS
# ---------------------------------------------------------------------------

RUN_TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)

RAW_RDB_PATH = OUTPUT_FOLDER / f"GW_StationMetadata_raw_{RUN_TIMESTAMP}.rdb"

CLEAN_CSV_PATH = OUTPUT_FOLDER / f"GW_StationMetadata_clean_{RUN_TIMESTAMP}.csv"


# ---------------------------------------------------------------------------
# HELPER FUNCTIONS
# ---------------------------------------------------------------------------

def build_usgs_site_url() -> str:
    """
    Build the USGS NWIS Site Service URL for groundwater metadata.
    """
    county_code_text = ",".join(COUNTY_CODES)

    url = (
        f"{USGS_SITE_SERVICE}"
        f"?format={OUTPUT_FORMAT}"
        f"&countyCd={county_code_text}"
        f"&siteType={SITE_TYPE}"
        f"&siteStatus={SITE_STATUS}"
    )

    return url


def download_text(url: str) -> str:
    """
    Download a text response from USGS with basic retry logic.
    """
    last_error = None

    for attempt_number in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(
                url,
                timeout=TIMEOUT_SECONDS,
                headers={"User-Agent": "FREAC-USGS-GW-Metadata-Downloader/1.0"},
            )

            response.encoding = response.encoding or "utf-8"
            response.raise_for_status()

            text = response.text

            if text.lstrip().lower().startswith("<html"):
                raise RuntimeError("USGS returned HTML instead of an RDB text table.")

            return text

        except Exception as error:
            last_error = error
            print(f"Attempt {attempt_number} failed: {error}")

            if attempt_number < MAX_RETRIES:
                time.sleep(SLEEP_SECONDS_BETWEEN_RETRIES * attempt_number)

    raise RuntimeError(
        f"Download failed after {MAX_RETRIES} attempts. Last error: {last_error}"
    )


def is_usgs_rdb_definition_row(row: pd.Series) -> bool:
    """
    Identify the USGS RDB field-definition row.

    In USGS RDB files, the line immediately below the column headers often
    contains values such as:
        5s, 15s, 50s, 6s, 10s

    That row describes field widths/types. It is not station metadata.
    """
    nonblank_values = [
        str(value).strip()
        for value in row.tolist()
        if str(value).strip()
    ]

    if not nonblank_values:
        return False

    definition_pattern = re.compile(r"^\d+[A-Za-z]?$")

    matched_count = sum(
        bool(definition_pattern.match(value))
        for value in nonblank_values
    )

    return matched_count == len(nonblank_values)


def convert_rdb_text_to_dataframe(rdb_text: str) -> pd.DataFrame:
    """
    Convert a USGS RDB text response into a cleaned pandas DataFrame.
    """
    non_comment_lines = [
        line
        for line in rdb_text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    if not non_comment_lines:
        return pd.DataFrame()

    table_text = "\n".join(non_comment_lines)

    metadata = pd.read_csv(
        StringIO(table_text),
        sep="\t",
        dtype=str,
        keep_default_na=False,
        engine="python",
    )

    metadata.columns = [
        str(column).strip()
        for column in metadata.columns
    ]

    if not metadata.empty:
        definition_row_mask = metadata.apply(is_usgs_rdb_definition_row, axis=1)
        metadata = metadata.loc[~definition_row_mask].copy()

    metadata = metadata.apply(lambda column: column.str.strip())

    return metadata


def validate_metadata(metadata: pd.DataFrame) -> None:
    """
    Confirm that the cleaned metadata table contains fields needed for GIS use.
    """
    required_columns = [
        "agency_cd",
        "site_no",
        "station_nm",
        "site_tp_cd",
        "dec_lat_va",
        "dec_long_va",
    ]

    missing_columns = [
        column
        for column in required_columns
        if column not in metadata.columns
    ]

    if missing_columns:
        raise ValueError(
            "The cleaned USGS metadata table is missing required columns: "
            + ", ".join(missing_columns)
        )

    if metadata.empty:
        raise ValueError("The cleaned USGS metadata table is empty.")

    if metadata["site_no"].dropna().empty:
        raise ValueError("The cleaned metadata does not contain valid site_no values.")


def save_outputs(raw_rdb_text: str, metadata: pd.DataFrame) -> None:
    """
    Save the raw USGS RDB response and the cleaned CSV output.
    """
    RAW_RDB_PATH.write_text(raw_rdb_text, encoding="utf-8")

    metadata.to_csv(
        CLEAN_CSV_PATH,
        index=False,
        encoding="utf-8-sig",
    )


# ---------------------------------------------------------------------------
# MAIN SCRIPT
# ---------------------------------------------------------------------------

def main() -> None:
    """
    Run the full groundwater metadata download workflow.
    """
    print("Starting USGS groundwater metadata download.")
    print(f"Output folder: {OUTPUT_FOLDER}")

    url = build_usgs_site_url()

    print("Request URL:")
    print(url)

    raw_rdb_text = download_text(url)

    metadata = convert_rdb_text_to_dataframe(raw_rdb_text)

    validate_metadata(metadata)

    save_outputs(raw_rdb_text, metadata)

    print("Download complete.")
    print(f"Groundwater metadata records saved: {len(metadata):,}")
    print(f"Raw RDB saved to: {RAW_RDB_PATH}")
    print(f"Clean CSV saved to: {CLEAN_CSV_PATH}")

    if "site_tp_cd" in metadata.columns:
        print("Site type counts:")
        print(metadata["site_tp_cd"].value_counts(dropna=False).to_string())

    if "county_cd" in metadata.columns:
        print("County code counts:")
        print(metadata["county_cd"].value_counts(dropna=False).to_string())


if __name__ == "__main__":
    main()