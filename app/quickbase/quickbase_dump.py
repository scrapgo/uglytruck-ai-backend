import json
import psycopg2
from psycopg2 import sql
from app.backend.config import settings
from app.database.queries import Queries

import os
from dotenv import load_dotenv
load_dotenv()
DB_TABLE_NAME = os.environ['DB_TABLE_NAME']

# Database connection setup
def get_connection():
    return psycopg2.connect(
        host=settings.DB_HOST,
        database=settings.DB_NAME,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD
    )

# Function to insert one record
def _insert_columns() -> list:
    """Column names of Queries.INSERT_RECORD_PSYCOP, in order."""
    import re as _re
    cols = _re.search(r"\((.*?)\)\s*VALUES", Queries.INSERT_RECORD_PSYCOP, _re.S).group(1)
    return [c.strip() for c in cols.split(",") if c.strip()]


def insert_truck_sales_record(record: dict):
    conn = get_connection()
    cursor = conn.cursor()

    insert_query = sql.SQL(Queries.INSERT_RECORD_PSYCOP.format(table_name=DB_TABLE_NAME))
    # Build the values in the INSERT's column order by name, instead of relying on dict order.
    # Extra keys (e.g. a Quickbase label with no mapper entry) are ignored instead of shifting columns.
    values = tuple(record.get(col) for col in _insert_columns())
    unknown = [k for k in record if k not in _insert_columns()]
    if unknown:
        print(f"ℹ️ Ignoring keys with no DB column: {unknown}")
    try:
        cursor.execute(insert_query, values)
        conn.commit()
        print("✅ Record inserted successfully!")
    except Exception as e:
        print("❌ Error inserting record:", e)
        conn.rollback()
    finally:
        cursor.close()
        conn.close()


import json
from datetime import datetime
from dateutil import parser as date_parser


def normalize_value(key: str, value):
    """Convert empty strings, booleans, numbers, and timestamps to PostgreSQL-safe Python values."""
    if value in ("", None, "None"):
        return None

    key_lower = key.lower()

    # --- Booleans ---
    if key_lower in ["runs", "title", "fedex_label_sent", "updated_form"]:
        if isinstance(value, str):
            val = value.strip().lower()
            if val in ["true", "yes", "1", "t", "y", "✓", "☑"]:
                return True
            elif val in ["false", "no", "0", "f", "n", "✗", "☐"]:
                return False
            else:
                return None
        return bool(value)

    # --- Integers ---
    if key_lower in ["year", "mileage", "num_of_notes"]:
        try:
            return int(str(value).replace(",", "").replace("$", "").strip())
        except (ValueError, TypeError):
            return None

    # --- Floats / Currency ---
    if key_lower in [
        "seller_target", "buyer_offer", "tow_price", "offer_price", "price_spread"
    ]:
        try:
            cleaned = str(value).replace(",", "").replace("$", "").strip()
            return float(cleaned)
        except (ValueError, TypeError):
            return None

    # --- JSON or list objects ---
    if isinstance(value, (dict, list)):
        return json.dumps(value)

    # --- Datetime / Timestamps ---
    if key_lower in ["date_created", "date_modified", "maximum_date_modified"]:
        try:
            return date_parser.parse(str(value))
        except Exception:
            return None

    # --- Date only ---
    if key_lower == "close_date":
        try:
            return date_parser.parse(str(value)).date()
        except Exception:
            return None

    # --- Default: plain string ---
    return str(value).strip() if isinstance(value, str) else value


def normalize_keys(record: dict, mapper: dict) -> dict:
    """
    Normalize record keys from Quickbase to DB-safe column names and sanitize values.
    """
    normalized = {}
    for k, v in record.items():
        k_clean = k.strip().replace("’", "'").replace("‘", "'")
        new_key = mapper.get(k_clean, k_clean)  # use mapping if exists
        new_val = normalize_value(new_key, v)
        # Two Quickbase labels map onto the same column (e.g. "Photo Exterior Front" and
        # "Photo Exterior Front from website"). Keep the non-empty one.
        if new_key in normalized and normalized[new_key] not in (None, "") and new_val in (None, ""):
            continue
        normalized[new_key] = new_val
    return normalized

import re

def normalize_quickbase_record(rec: dict) -> dict:
    """
    Clean only photo-related fields.
    - Converts nested photo objects or <img> HTML tags to direct URLs.
    - Leaves all other fields untouched.
    """
    cleaned = {}

    for key, val in rec.items():
        #key_clean = key.lower().replace(" ", "_")

        # Process photo fields only
        if key.lower().startswith("photo"):
            if isinstance(val, dict):
                url = val.get("url")
                if url:
                    # Construct full URL if relative
                    if url.startswith("/files/"):
                        url = url #f"https://scrapgo-9114.quickbase.com{url}"
                    cleaned[key] = url
                else:
                    cleaned[key] = None

            elif isinstance(val, str) and "<img" in val:
                match = re.search(r"src=['\"]?([^'\">]+)['\"]?", val)
                cleaned[key] = match.group(1) if match else None

            else:
                cleaned[key] = val
        else:
            # Leave all other fields as-is
            cleaned[key] = val

    return cleaned