# ---------------------------------------------------------------------------
# Script Name: get_SWmetadata.py
# Author: Nicholas Chang
# Created: 2026-06-03
# Purpose:
#   Download USGS surface-water station metadata from the NWIS Site Service
#   for Charlotte County and nearby counties.
#
#   The script saves a clean CSV of surface-water station metadata into:
#       01_metadata/SW
#
# Notes:
#   This script mirrors the get_GWmetadata.py workflow, but changes the
#   siteType parameter from GW to surface-water station types:
#       ST = Stream
#       LK = Lake
#       ES = Estuary
#       WE = Wetland
#       OC = Ocean/coastal
# ---------------------------------------------------------------------------

from pathlib import Path
from datetime import datetime
from io import StringIO
import sys
import time

import pandas as pd
import requests


print(sys.executable)


# ---------------------------------------------------------------------------
# USER SETTINGS
# ---------------------------------------------------------------------------

BASE_METADATA_FOLDER = Path(
    r"C:\ArcNLET-CharlotteCalibrationData\USGS_Data\USGS_GWandSW_Locations\Data\SecondTry\01_metadata"
)

OUTPUT_FOLDER = BASE_METADATA_FOLDER / "SW"

COUNTY_CODES = [
    "12015",  # Charlotte County, Florida
    "12027",  # DeSoto County, Florida
    "12043",  # Glades County, Florida
    "12071",  # Lee County, Florida
    "12115",  # Sarasota County, Florida
]

SITE_TYPES = [
    "ST",  # Stream
    "LK",  # Lake
    "ES",  # Estuary
    "WE",  # Wetland
    "OC",  # Ocean/coastal
]

SITE_STATUS = "all"

BASE_URL = "https://waterservices.usgs.gov/nwis/site/"

TIMEOUT_SECONDS = 180
MAX_RETRIES = 3
SLEEP_SECONDS = 0.50
SAVE_RAW_RDB = True

RUN_TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

OUTPUT_CSV = OUTPUT_FOLDER / f"SW_StationMetadata_{RUN_TIMESTAMP}.csv"
RAW_RDB_FILE = OUTPUT_FOLDER / f"SW_StationMetadata_raw_{RUN_TIMESTAMP}.rdb"


# ---------------------------------------------------------------------------
# HELPER FUNCTIONS
# ---------------------------------------------------------------------------

def build_usgs_site_metadata_url() -> str:
    """
    Build the USGS NWIS Site Service URL for surface-water station metadata.
    """
    county_cd_text = ",".join(COUNTY_CODES)
    site_type_text = ",".join(SITE_TYPES)

    url = (
        f"{BASE_URL}"
        f"?format=rdb"
        f"&countyCd={county_cd_text}"
        f"&siteType={site_type_text}"
        f"&siteStatus={SITE_STATUS}"
    )

    return url


def download_text(url: str) -> tuple[str, str, int]:
    """
    Download a text response from a URL with retry logic.

    Returns:
        response_text, final_url, status_code
    """
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(
                url,
                timeout=TIMEOUT_SECONDS,
                headers={"User-Agent": "FREAC-SW-Metadata-Downloader/1.0"},
            )

            response.encoding = response.encoding or "utf-8"
            response.raise_for_status()

            return response.text, response.url, response.status_code

        except requests.RequestException as error:
            last_error = error

            print(
                f"Download attempt {attempt} of {MAX_RETRIES} failed. "
                f"Error: {error}"
            )

            if attempt < MAX_RETRIES:
                time.sleep(SLEEP_SECONDS)

    raise RuntimeError(f"All download attempts failed. Last error: {last_error}")


def is_usgs_rdb_definition_row(row: pd.Series) -> bool:
    """
    Identify the USGS RDB field-definition row.

    In USGS RDB files, the row immediately below the header often contains
    values such as 5s, 15s, 10n, and similar field-width/type definitions.
    That row is not real data and should be removed.
    """
    nonblank_values = [
        str(value).strip()
        for value in row.tolist()
        if str(value).strip()
    ]

    if not nonblank_values:
        return False

    matched_count = 0

    for value in nonblank_values:
        if value[:-1].isdigit() and value[-1].isalpha():
            matched_count += 1

    return matched_count == len(nonblank_values)


def convert_rdb_text_to_dataframe(rdb_text: str) -> pd.DataFrame:
    """
    Convert a USGS RDB text response into a clean pandas DataFrame.
    """
    non_comment_lines = [
        line
        for line in rdb_text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    if not non_comment_lines:
        return pd.DataFrame()

    table_text = "\n".join(non_comment_lines)

    data = pd.read_csv(
        StringIO(table_text),
        sep="\t",
        dtype=str,
        keep_default_na=False,
        engine="python",
    )

    data.columns = [str(column).strip() for column in data.columns]

    if not data.empty and is_usgs_rdb_definition_row(data.iloc[0]):
        data = data.iloc[1:].reset_index(drop=True)

    return data


# ---------------------------------------------------------------------------
# MAIN SCRIPT
# ---------------------------------------------------------------------------

def main() -> None:
    """
    Run the USGS surface-water station metadata download workflow.
    """
    print("Starting USGS surface-water station metadata download workflow.")
    print(f"Run timestamp: {RUN_TIMESTAMP}")
    print(f"Output folder:  {OUTPUT_FOLDER}")

    OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)

    request_url = build_usgs_site_metadata_url()

    print(f"Request URL: {request_url}")

    rdb_text, final_url, status_code = download_text(request_url)

    if SAVE_RAW_RDB:
        RAW_RDB_FILE.write_text(rdb_text, encoding="utf-8")

    metadata = convert_rdb_text_to_dataframe(rdb_text)

    if metadata.empty:
        raise RuntimeError("The USGS response did not contain any station metadata rows.")

    metadata.to_csv(OUTPUT_CSV, index=False)

    print(f"Rows saved: {len(metadata):,}")
    print(f"Output CSV: {OUTPUT_CSV}")
    print(f"Raw RDB: {RAW_RDB_FILE}")
    print(f"Final URL: {final_url}")
    print(f"Status code: {status_code}")

if __name__ == "__main__":
    main()