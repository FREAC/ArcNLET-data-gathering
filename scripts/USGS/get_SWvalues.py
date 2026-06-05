"""
Script Name: get_SWvalues.py
Author: Nicholas Chang
Created: 2026-06-05
Last Updated: 2026-06-05
Purpose:
    Download USGS surface-water values for filtered surface-water inventory CSVs.

    This script reads the filtered surface-water inventory CSVs created by
    filter_SWinventory.py, then downloads values from the appropriate USGS
    endpoint based on data_type_cd:

        dv = USGS NWIS Daily Values Service
        uv = USGS NWIS Instantaneous Values Service
        iv = USGS NWIS Instantaneous Values Service

    The script writes:
        1. One values CSV for each filtered input CSV.
        2. One combined values CSV containing all downloaded surface-water values.
        3. One request log CSV documenting every request.
        4. Optional raw-response files when SAVE_RAW_RESPONSES = True.

Project:
    ArcNLET Charlotte Calibration Data - USGS Surface-Water Values

Notes:
    - dv records are daily statistics and should include stat_cd when available.
    - uv/iv records are continuous/instantaneous values.
    - The script preserves USGS site numbers as text identifiers.
    - Gage height is not the same as water-surface elevation unless the parameter
      definition and datum support that interpretation.
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
# USER SETTINGS
# ---------------------------------------------------------------------------

INPUT_FOLDER = Path(
    r"C:\ArcNLET-CharlotteCalibrationData\USGS_Data\USGS_GWandSW_Locations\Data\SecondTry\03_filtered\SW"
)

OUTPUT_PARENT_FOLDER = Path(
    r"C:\ArcNLET-CharlotteCalibrationData\USGS_Data\USGS_GWandSW_Locations\Data\SecondTry\04_values"
)

OUTPUT_FOLDER = OUTPUT_PARENT_FOLDER / "SW"

# Legacy NWIS Water Services endpoints used for daily and instantaneous values.
NWIS_DAILY_VALUES_SERVICE_URL = "https://waterservices.usgs.gov/nwis/dv/"
NWIS_INSTANTANEOUS_VALUES_SERVICE_URL = "https://waterservices.usgs.gov/nwis/iv/"

BATCH_SIZE = 30

DV_BATCH_SIZE = 30

IV_BATCH_SIZE = 5

REQUEST_TIMEOUT_SECONDS = 180

REQUEST_DELAY_SECONDS = 0.50

MAX_RETRIES = 3

SLEEP_SECONDS_BETWEEN_RETRIES = 5

OUTPUT_ENCODING = "utf-8-sig"

WRITE_EMPTY_VALUE_OUTPUTS = False

DROP_EMPTY_VALUE_ROWS = True

SAVE_RAW_RESPONSES = False

USE_INVENTORY_DATE_RANGE = True

START_DATE_OVERRIDE = None

END_DATE_OVERRIDE = None

DEFAULT_START_DATE = "1900-01-01"

DEFAULT_END_DATE = None


# ---------------------------------------------------------------------------
# OUTPUT PATHS
# ---------------------------------------------------------------------------

RUN_TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)

RAW_RESPONSE_FOLDER = OUTPUT_FOLDER / f"raw_responses_{RUN_TIMESTAMP}"

REQUEST_LOG_CSV = OUTPUT_FOLDER / f"SW_VALUES_request_log_{RUN_TIMESTAMP}.csv"

COMBINED_VALUES_CSV = OUTPUT_FOLDER / f"SW_VALUES_COMBINED_{RUN_TIMESTAMP}.csv"


# ---------------------------------------------------------------------------
# COLUMN SETTINGS
# ---------------------------------------------------------------------------

REQUIRED_INPUT_COLUMNS = [
    "site_no",
    "data_type_cd",
    "parm_cd",
    "stat_cd",
]

SITE_METADATA_COLUMNS = [
    "site_no",
    "agency_cd",
    "station_nm",
    "site_tp_cd",
    "dec_lat_va",
    "dec_long_va",
    "coord_acy_cd",
    "dec_coord_datum_cd",
    "alt_va",
    "alt_acy_va",
    "alt_datum_cd",
    "huc_cd",
    "begin_date",
    "end_date",
    "count_nu",
]

DATE_BEGIN_COLUMN_CANDIDATES = [
    "begin_date",
    "begin",
    "start_date",
]

DATE_END_COLUMN_CANDIDATES = [
    "end_date",
    "end",
    "stop_date",
]

PREFERRED_OUTPUT_COLUMNS = [
    "request_source_csv",
    "service",
    "data_type_cd",
    "parm_cd",
    "stat_cd",
    "agency_cd",
    "site_no",
    "station_nm",
    "datetime",
    "time_zone",
    "value",
    "qualifier",
    "approval_status",
    "unit_code",
    "parameter_name",
    "time_series_id",
    "source_value_column",
    "inventory_station_nm",
    "inventory_site_tp_cd",
    "inventory_dec_lat_va",
    "inventory_dec_long_va",
    "inventory_coord_acy_cd",
    "inventory_dec_coord_datum_cd",
    "inventory_alt_va",
    "inventory_alt_acy_va",
    "inventory_alt_datum_cd",
    "inventory_huc_cd",
    "inventory_begin_date",
    "inventory_end_date",
    "inventory_count_nu",
    "request_startDT",
    "request_endDT",
    "batch_number",
    "page_number",
    "request_offset",
    "request_url",
]


# ---------------------------------------------------------------------------
# GENERAL HELPER FUNCTIONS
# ---------------------------------------------------------------------------

def clean_code(value: object, width: int | None = None) -> str:
    """
    Convert a USGS code value into a clean string.

    Examples:
        00065   -> 00065
        00065.0 -> 00065
        63160   -> 63160
        3.0     -> 00003 when width=5
    """
    code = str(value).strip()

    if code.lower() in {"", "nan", "none", "<na>"}:
        return ""

    if code.endswith(".0") and code[:-2].isdigit():
        code = code[:-2]

    if width is not None and code.isdigit():
        code = code.zfill(width)

    return code


def normalize_site_number(value: object) -> str:
    """
    Convert a USGS site-number value into a clean text identifier.
    """
    site_no = str(value).strip()

    if site_no.lower() in {"", "nan", "none", "<na>"}:
        return ""

    if site_no.endswith(".0") and site_no[:-2].isdigit():
        site_no = site_no[:-2]

    return site_no


def make_batches(items: list[str], batch_size: int) -> list[list[str]]:
    """
    Split a list into smaller batches.
    """
    batches = []

    for start_index in range(0, len(items), batch_size):
        batch = items[start_index:start_index + batch_size]
        batches.append(batch)

    return batches


def get_filtered_csv_files() -> list[Path]:
    """
    Return the filtered surface-water CSVs that should be processed.

    Summary CSVs are skipped because they describe filter results and do not
    contain the station rows needed for value downloads.
    """
    csv_files = [
        path
        for path in INPUT_FOLDER.glob("*.csv")
        if "summary" not in path.name.lower()
    ]

    return sorted(csv_files)


def validate_input_table(filtered_df: pd.DataFrame, input_csv: Path) -> None:
    """
    Confirm that a filtered inventory CSV contains the required fields.
    """
    missing_columns = [
        column
        for column in REQUIRED_INPUT_COLUMNS
        if column not in filtered_df.columns
    ]

    if missing_columns:
        available_columns = ", ".join(filtered_df.columns)

        raise KeyError(
            f"The input CSV is missing required columns: {input_csv}\n"
            + "Missing columns: "
            + ", ".join(missing_columns)
            + f"\nAvailable columns: {available_columns}"
        )

    if filtered_df.empty:
        raise ValueError(f"The input CSV is empty: {input_csv}")


def add_normalized_filter_fields(filtered_df: pd.DataFrame) -> pd.DataFrame:
    """
    Add normalized internal fields for site_no, data_type_cd, parm_cd, and stat_cd.
    """
    filtered_df = filtered_df.copy()

    filtered_df["_filter_site_no"] = filtered_df["site_no"].apply(
        normalize_site_number
    )

    filtered_df["_filter_data_type_cd"] = (
        filtered_df["data_type_cd"]
        .astype("string")
        .str.strip()
        .str.lower()
    )

    filtered_df["_filter_parm_cd"] = filtered_df["parm_cd"].apply(
        lambda value: clean_code(value, width=5)
    )

    filtered_df["_filter_stat_cd"] = filtered_df["stat_cd"].apply(
        lambda value: clean_code(value, width=5)
    )

    return filtered_df


def get_site_numbers(filtered_df: pd.DataFrame) -> list[str]:
    """
    Return unique USGS site numbers in their original order.
    """
    site_numbers = []
    seen_site_numbers = set()

    for value in filtered_df["_filter_site_no"]:
        site_no = normalize_site_number(value)

        if not site_no:
            continue

        if site_no not in seen_site_numbers:
            site_numbers.append(site_no)
            seen_site_numbers.add(site_no)

    return site_numbers


def get_min_date(filtered_df: pd.DataFrame, candidate_columns: list[str]) -> str:
    """
    Return the earliest valid date from the first matching date column.
    """
    for column in candidate_columns:
        if column not in filtered_df.columns:
            continue

        dates = pd.to_datetime(filtered_df[column], errors="coerce")

        if not dates.dropna().empty:
            return dates.min().strftime("%Y-%m-%d")

    return ""


def get_max_date(filtered_df: pd.DataFrame, candidate_columns: list[str]) -> str:
    """
    Return the latest valid date from the first matching date column.
    """
    for column in candidate_columns:
        if column not in filtered_df.columns:
            continue

        dates = pd.to_datetime(filtered_df[column], errors="coerce")

        if not dates.dropna().empty:
            return dates.max().strftime("%Y-%m-%d")

    return ""


def get_request_date_range(filtered_df: pd.DataFrame) -> tuple[str, str]:
    """
    Determine the startDT and endDT values for a request.
    """
    if START_DATE_OVERRIDE:
        start_dt = START_DATE_OVERRIDE
    elif USE_INVENTORY_DATE_RANGE:
        start_dt = get_min_date(filtered_df, DATE_BEGIN_COLUMN_CANDIDATES)
    else:
        start_dt = ""

    if END_DATE_OVERRIDE:
        end_dt = END_DATE_OVERRIDE
    elif USE_INVENTORY_DATE_RANGE:
        end_dt = get_max_date(filtered_df, DATE_END_COLUMN_CANDIDATES)
    else:
        end_dt = ""

    if not start_dt:
        start_dt = DEFAULT_START_DATE

    if not end_dt and DEFAULT_END_DATE:
        end_dt = DEFAULT_END_DATE

    if start_dt and end_dt:
        start_date_check = pd.to_datetime(start_dt, errors="coerce")
        end_date_check = pd.to_datetime(end_dt, errors="coerce")

        if pd.notna(start_date_check) and pd.notna(end_date_check):
            if start_date_check > end_date_check:
                raise ValueError(
                    f"Invalid date range. startDT is after endDT: "
                    f"{start_dt} > {end_dt}"
                )

    return start_dt, end_dt


def build_site_metadata_table(filtered_df: pd.DataFrame) -> pd.DataFrame:
    """
    Build a one-row-per-site metadata table from the filtered inventory CSV.
    """
    available_columns = [
        column
        for column in SITE_METADATA_COLUMNS
        if column in filtered_df.columns
    ]

    metadata_df = filtered_df[available_columns].copy()

    metadata_df["site_no"] = metadata_df["site_no"].apply(normalize_site_number)

    metadata_df = metadata_df.drop_duplicates(subset=["site_no"], keep="first")

    rename_map = {
        column: f"inventory_{column}"
        for column in metadata_df.columns
        if column != "site_no"
    }

    metadata_df = metadata_df.rename(columns=rename_map)

    return metadata_df


def download_text(url: str) -> tuple[str, str, int]:
    """
    Download a response from USGS with retry logic.
    """
    last_error = None

    for attempt_number in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(
                url,
                timeout=REQUEST_TIMEOUT_SECONDS,
                headers={
                    "User-Agent": "FREAC-USGS-SW-Values-Downloader/1.0",
                    "Accept": "text/plain,*/*",
                    "Accept-Encoding": "gzip, deflate",
                },
            )

            response.encoding = response.encoding or "utf-8"

            if response.status_code == 204:
                return "", response.url, response.status_code

            response.raise_for_status()

            text = response.text

            if text.lstrip().lower().startswith("<html"):
                raise RuntimeError("USGS returned HTML instead of data text.")

            return text, response.url, response.status_code

        except Exception as error:
            last_error = error

            print(f"Attempt {attempt_number} failed: {error}")

            if attempt_number < MAX_RETRIES:
                time.sleep(SLEEP_SECONDS_BETWEEN_RETRIES * attempt_number)

    raise RuntimeError(
        f"Download failed after {MAX_RETRIES} attempts. Last error: {last_error}"
    )


# ---------------------------------------------------------------------------
# URL-BUILDING FUNCTIONS
# ---------------------------------------------------------------------------

def build_daily_values_url(
    batch_sites: list[str],
    parm_cd: str,
    stat_cd: str,
    start_dt: str,
    end_dt: str,
) -> str:
    """
    Build one USGS Daily Values Service URL.
    """
    params = {
        "format": "rdb",
        "sites": ",".join(batch_sites),
        "parameterCd": parm_cd,
        "siteStatus": "all",
    }

    if stat_cd:
        params["statCd"] = stat_cd

    if start_dt:
        params["startDT"] = start_dt

    if end_dt:
        params["endDT"] = end_dt

    query_string = urlencode(params, safe=",")

    return f"{NWIS_DAILY_VALUES_SERVICE_URL}?{query_string}"


def build_instantaneous_values_url(
    batch_sites: list[str],
    parm_cd: str,
    start_dt: str,
    end_dt: str,
) -> str:
    """
    Build one USGS Instantaneous Values Service URL for uv/iv records.
    """
    params = {
        "format": "rdb",
        "sites": ",".join(batch_sites),
        "parameterCd": parm_cd,
        "siteStatus": "all",
    }

    if start_dt:
        params["startDT"] = start_dt

    if end_dt:
        params["endDT"] = end_dt

    query_string = urlencode(params, safe=",")

    return f"{NWIS_INSTANTANEOUS_VALUES_SERVICE_URL}?{query_string}"


# ---------------------------------------------------------------------------
# RDB TIME-SERIES PARSING FUNCTIONS
# ---------------------------------------------------------------------------

def row_looks_like_rdb_type_row(row: pd.Series) -> bool:
    """
    Check whether a row is the USGS RDB field-width/type row.
    """
    values = row.fillna("").astype(str).str.strip().tolist()

    non_blank_values = [
        value
        for value in values
        if value
    ]

    if not non_blank_values:
        return False

    return all(re.fullmatch(r"\d+[A-Za-z]", value) for value in non_blank_values)


def read_usgs_rdb_text(rdb_text: str) -> pd.DataFrame:
    """
    Read a USGS RDB response into a clean pandas DataFrame.

    This version is more tolerant of large USGS RDB responses that contain
    occasional malformed rows. Malformed rows are skipped rather than causing
    the entire request batch to fail.
    """
    if not rdb_text.strip():
        return pd.DataFrame()

    try:
        df = pd.read_csv(
            StringIO(rdb_text),
            sep="\t",
            comment="#",
            dtype=str,
            keep_default_na=False,
            engine="python",
            on_bad_lines="skip",
        )

    except pd.errors.EmptyDataError:
        return pd.DataFrame()

    except pd.errors.ParserError:
        return pd.DataFrame()

    df = df.dropna(how="all")

    if df.empty:
        return df

    if row_looks_like_rdb_type_row(df.iloc[0]):
        df = df.iloc[1:].copy()

    df.columns = [
        str(column).strip()
        for column in df.columns
    ]

    for column in df.columns:
        df[column] = df[column].astype("string").str.strip()

    df = df.dropna(how="all")

    return df.reset_index(drop=True)


def parse_time_series_rdb(
    rdb_text: str,
    requested_parm_cd: str,
    requested_stat_cd: str = "",
) -> pd.DataFrame:
    """
    Parse USGS RDB time-series responses into a long table.

    Handles daily-values columns such as:
        31429_00065_00003
        31429_63160_00003

    Handles instantaneous/unit-values columns such as:
        314255_00065
        314255_63160
    """
    rdb_df = read_usgs_rdb_text(rdb_text)

    if rdb_df.empty:
        return pd.DataFrame()

    if "site_no" not in rdb_df.columns:
        return pd.DataFrame()

    if "datetime" not in rdb_df.columns:
        return pd.DataFrame()

    requested_parm_cd = clean_code(requested_parm_cd, width=5)
    requested_stat_cd = clean_code(requested_stat_cd, width=5)

    if requested_stat_cd:
        value_column_pattern = re.compile(
            rf"^(?:(?P<time_series_id>\d+)_)?"
            rf"(?P<parm_cd>{re.escape(requested_parm_cd)})_"
            rf"(?P<stat_cd>{re.escape(requested_stat_cd)})$"
        )
    else:
        value_column_pattern = re.compile(
            rf"^(?:(?P<time_series_id>\d+)_)?"
            rf"(?P<parm_cd>{re.escape(requested_parm_cd)})$"
        )

    value_columns = []

    for column in rdb_df.columns:
        column_name = str(column).strip()

        if column_name.endswith("_cd"):
            continue

        match = value_column_pattern.fullmatch(column_name)

        if match:
            value_columns.append(
                {
                    "value_column": column_name,
                    "time_series_id": match.group("time_series_id") or "",
                    "parm_cd": match.group("parm_cd"),
                    "stat_cd": match.groupdict().get("stat_cd") or "",
                }
            )

    if not value_columns:
        return pd.DataFrame()

    records = []

    for value_column_info in value_columns:
        value_column = value_column_info["value_column"]
        qualifier_column = f"{value_column}_cd"

        for _, row in rdb_df.iterrows():
            measured_value = str(row.get(value_column, "")).strip()

            if DROP_EMPTY_VALUE_ROWS and not measured_value:
                continue

            record = {
                "agency_cd": str(row.get("agency_cd", "")).strip(),
                "site_no": normalize_site_number(row.get("site_no", "")),
                "station_nm": "",
                "datetime": str(row.get("datetime", "")).strip(),
                "time_zone": str(row.get("tz_cd", "")).strip(),
                "value": measured_value,
                "qualifier": str(row.get(qualifier_column, "")).strip()
                if qualifier_column in rdb_df.columns
                else "",
                "approval_status": "",
                "parm_cd": value_column_info["parm_cd"],
                "stat_cd": value_column_info["stat_cd"],
                "time_series_id": value_column_info["time_series_id"],
                "parameter_name": "",
                "unit_code": "",
                "source_value_column": value_column,
            }

            records.append(record)

    return pd.DataFrame(records)


def parse_daily_values_rdb(
    rdb_text: str,
    requested_parm_cd: str,
    requested_stat_cd: str,
) -> pd.DataFrame:
    """
    Parse a USGS Daily Values RDB response into a long table.
    """
    return parse_time_series_rdb(
        rdb_text=rdb_text,
        requested_parm_cd=requested_parm_cd,
        requested_stat_cd=requested_stat_cd,
    )


def parse_instantaneous_values_rdb(
    rdb_text: str,
    requested_parm_cd: str,
) -> pd.DataFrame:
    """
    Parse a USGS Instantaneous Values RDB response into a long table.
    """
    return parse_time_series_rdb(
        rdb_text=rdb_text,
        requested_parm_cd=requested_parm_cd,
        requested_stat_cd="",
    )


# ---------------------------------------------------------------------------
# OUTPUT CLEANUP FUNCTIONS
# ---------------------------------------------------------------------------

def add_request_fields(
    values_df: pd.DataFrame,
    input_csv: Path,
    service: str,
    data_type_cd: str,
    parm_cd: str,
    stat_cd: str,
    start_dt: str,
    end_dt: str,
    batch_number: str,
    page_number: str,
    request_offset: int | str,
    request_url: str,
) -> pd.DataFrame:
    """
    Add request-tracking fields to a values table.
    """
    if values_df.empty:
        return values_df

    values_df = values_df.copy()

    values_df["request_source_csv"] = input_csv.name
    values_df["service"] = service
    values_df["data_type_cd"] = data_type_cd
    values_df["parm_cd"] = values_df["parm_cd"].apply(
        lambda value: clean_code(value, width=5)
    )
    values_df["stat_cd"] = stat_cd
    values_df["request_startDT"] = start_dt
    values_df["request_endDT"] = end_dt
    values_df["batch_number"] = batch_number
    values_df["page_number"] = page_number
    values_df["request_offset"] = request_offset
    values_df["request_url"] = request_url

    return values_df


def enrich_with_inventory_metadata(
    values_df: pd.DataFrame,
    site_metadata_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Join inventory metadata onto downloaded values by site_no.
    """
    if values_df.empty:
        return values_df

    if "site_no" not in values_df.columns:
        return values_df

    values_df = values_df.copy()

    values_df["site_no"] = values_df["site_no"].apply(normalize_site_number)

    enriched_df = values_df.merge(
        site_metadata_df,
        on="site_no",
        how="left",
    )

    return enriched_df


def reorder_output_columns(values_df: pd.DataFrame) -> pd.DataFrame:
    """
    Put the most useful fields at the front of the output table.
    """
    if values_df.empty:
        return values_df

    first_columns = [
        column
        for column in PREFERRED_OUTPUT_COLUMNS
        if column in values_df.columns
    ]

    remaining_columns = [
        column
        for column in values_df.columns
        if column not in first_columns
    ]

    return values_df[first_columns + remaining_columns]


def save_raw_response(
    response_text: str,
    input_csv: Path,
    service: str,
    parm_cd: str,
    stat_cd: str,
    batch_number: str,
    page_number: str,
) -> str:
    """
    Save a raw USGS response if SAVE_RAW_RESPONSES is enabled.
    """
    if not SAVE_RAW_RESPONSES:
        return ""

    RAW_RESPONSE_FOLDER.mkdir(parents=True, exist_ok=True)

    safe_stat_cd = stat_cd if stat_cd else "none"

    if service in {"dv", "iv"}:
        file_extension = "rdb"
    else:
        file_extension = "txt"

    raw_response_path = (
        RAW_RESPONSE_FOLDER
        / (
            f"{input_csv.stem}_{service}_parm{parm_cd}_stat{safe_stat_cd}_"
            f"batch{batch_number}_page{page_number}.{file_extension}"
        )
    )

    raw_response_path.write_text(response_text, encoding="utf-8")

    return str(raw_response_path)


def build_request_record(
    input_csv: Path,
    service: str,
    data_type_cd: str,
    parm_cd: str,
    stat_cd: str,
    batch_number: str,
    page_number: str,
    request_offset: int | str,
    batch_sites: list[str],
    start_dt: str,
    end_dt: str,
    request_url: str,
    output_csv: Path,
) -> dict[str, object]:
    """
    Build one request-log record.
    """
    return {
        "source_csv": input_csv.name,
        "service": service,
        "data_type_cd": data_type_cd,
        "parm_cd": parm_cd,
        "stat_cd": stat_cd,
        "batch_number": batch_number,
        "page_number": page_number,
        "request_offset": request_offset,
        "site_count": len(batch_sites),
        "first_site_no": batch_sites[0] if batch_sites else "",
        "last_site_no": batch_sites[-1] if batch_sites else "",
        "startDT": start_dt,
        "endDT": end_dt,
        "url": request_url,
        "final_url": "",
        "request_status": "not_requested",
        "http_status_code": "",
        "value_row_count": 0,
        "output_csv": str(output_csv),
        "raw_response_path": "",
        "error_message": "",
    }


# ---------------------------------------------------------------------------
# DOWNLOAD WORKFLOW FUNCTIONS
# ---------------------------------------------------------------------------

def download_rdb_values_for_batch(
    input_csv: Path,
    batch_sites: list[str],
    site_metadata_df: pd.DataFrame,
    data_type_cd: str,
    parm_cd: str,
    stat_cd: str,
    start_dt: str,
    end_dt: str,
    batch_number: str,
    output_csv: Path,
) -> tuple[list[pd.DataFrame], list[dict[str, object]]]:
    """
    Download one RDB time-series request for dv or uv/iv records.
    """
    if data_type_cd == "dv":
        service = "dv"
        request_url = build_daily_values_url(
            batch_sites=batch_sites,
            parm_cd=parm_cd,
            stat_cd=stat_cd,
            start_dt=start_dt,
            end_dt=end_dt,
        )

    elif data_type_cd in {"uv", "iv"}:
        service = "iv"
        request_url = build_instantaneous_values_url(
            batch_sites=batch_sites,
            parm_cd=parm_cd,
            start_dt=start_dt,
            end_dt=end_dt,
        )

    else:
        raise ValueError(f"Unsupported RDB data_type_cd: {data_type_cd}")

    page_number = "001"

    request_record = build_request_record(
        input_csv=input_csv,
        service=service,
        data_type_cd=data_type_cd,
        parm_cd=parm_cd,
        stat_cd=stat_cd,
        batch_number=batch_number,
        page_number=page_number,
        request_offset="",
        batch_sites=batch_sites,
        start_dt=start_dt,
        end_dt=end_dt,
        request_url=request_url,
        output_csv=output_csv,
    )

    value_frames = []

    try:
        response_text, final_url, http_status_code = download_text(request_url)

        request_record["final_url"] = final_url
        request_record["http_status_code"] = http_status_code

        raw_response_path = save_raw_response(
            response_text=response_text,
            input_csv=input_csv,
            service=service,
            parm_cd=parm_cd,
            stat_cd=stat_cd,
            batch_number=batch_number,
            page_number=page_number,
        )

        request_record["raw_response_path"] = raw_response_path

        if service == "dv":
            values_df = parse_daily_values_rdb(
                rdb_text=response_text,
                requested_parm_cd=parm_cd,
                requested_stat_cd=stat_cd,
            )

        elif service == "iv":
            values_df = parse_instantaneous_values_rdb(
                rdb_text=response_text,
                requested_parm_cd=parm_cd,
            )

        else:
            values_df = pd.DataFrame()

        values_df = add_request_fields(
            values_df=values_df,
            input_csv=input_csv,
            service=service,
            data_type_cd=data_type_cd,
            parm_cd=parm_cd,
            stat_cd=stat_cd,
            start_dt=start_dt,
            end_dt=end_dt,
            batch_number=batch_number,
            page_number=page_number,
            request_offset="",
            request_url=final_url,
        )

        values_df = enrich_with_inventory_metadata(
            values_df=values_df,
            site_metadata_df=site_metadata_df,
        )

        values_df = reorder_output_columns(values_df)

        request_record["value_row_count"] = len(values_df)

        if values_df.empty:
            request_record["request_status"] = "success_no_values"
        else:
            request_record["request_status"] = "success"
            value_frames.append(values_df)

    except Exception as error:
        request_record["request_status"] = "failed"
        request_record["error_message"] = str(error)

    return value_frames, [request_record]


def download_values_for_group(
    input_csv: Path,
    group_df: pd.DataFrame,
    site_metadata_df: pd.DataFrame,
    data_type_cd: str,
    parm_cd: str,
    stat_cd: str,
    output_csv: Path,
) -> tuple[list[pd.DataFrame], list[dict[str, object]]]:
    """
    Download values for one data_type_cd, parm_cd, and stat_cd group.
    """
    site_numbers = get_site_numbers(group_df)

    if not site_numbers:
        raise ValueError(f"No usable site numbers found in: {input_csv}")

    start_dt, end_dt = get_request_date_range(group_df)

    if data_type_cd == "dv":
        batch_size = DV_BATCH_SIZE

    elif data_type_cd in {"uv", "iv"}:
        batch_size = IV_BATCH_SIZE

    else:
        batch_size = BATCH_SIZE

    batches = make_batches(site_numbers, batch_size)

    all_value_frames = []
    all_request_records = []

    for batch_index, batch_sites in enumerate(batches, start=1):
        batch_number = f"{batch_index:03d}"

        if data_type_cd in {"dv", "uv", "iv"}:
            value_frames, request_records = download_rdb_values_for_batch(
                input_csv=input_csv,
                batch_sites=batch_sites,
                site_metadata_df=site_metadata_df,
                data_type_cd=data_type_cd,
                parm_cd=parm_cd,
                stat_cd=stat_cd,
                start_dt=start_dt,
                end_dt=end_dt,
                batch_number=batch_number,
                output_csv=output_csv,
            )

        else:
            request_record = build_request_record(
                input_csv=input_csv,
                service="",
                data_type_cd=data_type_cd,
                parm_cd=parm_cd,
                stat_cd=stat_cd,
                batch_number=batch_number,
                page_number="",
                request_offset="",
                batch_sites=batch_sites,
                start_dt=start_dt,
                end_dt=end_dt,
                request_url="",
                output_csv=output_csv,
            )

            request_record["request_status"] = "skipped_unsupported_data_type_cd"
            request_record["error_message"] = (
                "Only data_type_cd values of dv, uv, and iv are supported by this script."
            )

            value_frames = []
            request_records = [request_record]

        all_value_frames.extend(value_frames)
        all_request_records.extend(request_records)

        time.sleep(REQUEST_DELAY_SECONDS)

    return all_value_frames, all_request_records


def process_filtered_csv(input_csv: Path) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    """
    Process one filtered surface-water inventory CSV.
    """
    print(f"Processing: {input_csv.name}")

    filtered_df = pd.read_csv(
        input_csv,
        dtype=str,
        keep_default_na=False,
    )

    validate_input_table(filtered_df, input_csv)

    filtered_df = add_normalized_filter_fields(filtered_df)

    site_metadata_df = build_site_metadata_table(filtered_df)

    output_csv = OUTPUT_FOLDER / f"{input_csv.stem}_VALUES_{RUN_TIMESTAMP}.csv"

    all_value_frames = []
    all_request_records = []

    group_columns = [
        "_filter_data_type_cd",
        "_filter_parm_cd",
        "_filter_stat_cd",
    ]

    grouped = filtered_df.groupby(group_columns, dropna=False)

    for (data_type_cd, parm_cd, stat_cd), group_df in grouped:
        data_type_cd = str(data_type_cd).strip().lower()
        parm_cd = clean_code(parm_cd, width=5)
        stat_cd = clean_code(stat_cd, width=5)

        value_frames, request_records = download_values_for_group(
            input_csv=input_csv,
            group_df=group_df,
            site_metadata_df=site_metadata_df,
            data_type_cd=data_type_cd,
            parm_cd=parm_cd,
            stat_cd=stat_cd,
            output_csv=output_csv,
        )

        all_value_frames.extend(value_frames)
        all_request_records.extend(request_records)

    if all_value_frames:
        values_df = pd.concat(all_value_frames, ignore_index=True)
    else:
        values_df = pd.DataFrame()

    if not values_df.empty or WRITE_EMPTY_VALUE_OUTPUTS:
        values_df.to_csv(
            output_csv,
            index=False,
            encoding=OUTPUT_ENCODING,
        )

        print(f"  Values rows saved: {len(values_df):,}")
        print(f"  Output CSV: {output_csv}")

    else:
        print("  No values returned. No values CSV written.")

    return values_df, all_request_records


# ---------------------------------------------------------------------------
# MAIN SCRIPT
# ---------------------------------------------------------------------------

def main() -> None:
    """
    Run the full surface-water values download workflow.
    """
    print("Starting USGS surface-water values download workflow.")
    print(f"Run timestamp: {RUN_TIMESTAMP}")
    print(f"Input folder: {INPUT_FOLDER}")
    print(f"Output folder: {OUTPUT_FOLDER}")

    filtered_csv_files = get_filtered_csv_files()

    if not filtered_csv_files:
        raise FileNotFoundError(
            f"No filtered surface-water CSV files were found in: {INPUT_FOLDER}"
        )

    combined_value_frames = []
    request_records = []

    for input_csv in filtered_csv_files:
        values_df, file_request_records = process_filtered_csv(input_csv)

        if not values_df.empty:
            combined_value_frames.append(values_df)

        request_records.extend(file_request_records)

    request_log_df = pd.DataFrame(request_records)

    request_log_df.to_csv(
        REQUEST_LOG_CSV,
        index=False,
        encoding=OUTPUT_ENCODING,
    )

    if combined_value_frames:
        combined_values_df = pd.concat(combined_value_frames, ignore_index=True)

        combined_values_df = reorder_output_columns(combined_values_df)

        combined_values_df.to_csv(
            COMBINED_VALUES_CSV,
            index=False,
            encoding=OUTPUT_ENCODING,
        )

        print("Combined surface-water values CSV written.")
        print(f"Combined rows: {len(combined_values_df):,}")
        print(f"Combined CSV: {COMBINED_VALUES_CSV}")

    else:
        print("No surface-water values were downloaded. Combined CSV was not written.")

    print("Surface-water values download workflow complete.")
    print(f"Request log CSV: {REQUEST_LOG_CSV}")


if __name__ == "__main__":
    main()
