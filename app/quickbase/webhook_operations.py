from app.database.database import get_connection
from app.database.queries import Queries
import asyncpg
import logging

import os
from dotenv import load_dotenv
load_dotenv()
DB_TABLE_NAME = os.environ.get("DB_TABLE_NAME")

from datetime import datetime

from datetime import datetime, date

def preprocess_values(values: dict):
    processed = {}

    # === FIELD TYPE GROUPS (Based on your table schema) ===
    int_fields = {
        "record_id", "year", "mileage", "num_of_notes"
    }

    numeric_fields = {
        "seller_target", "buyer_offer", "tow_price",
        "offer_price", "price_spread"
    }

    boolean_fields = {
        "runs", "title", "fedex_label_sent", "updated_form"
    }

    # These two must remain TEXT even if "yes"/"no"
    # (because schema defines TEXT)
    boolean_like_but_text_fields = {
        "buyer_bill_of_sale_sheet_cb"
    }

    timestamp_fields = {
        "date_created", "date_modified", "maximum_date_modified"
    }

    date_fields = {
        "close_date"
    }

    # Normal text fields (no conversion)
    text_fields = {
        "last_modified_by", "source", "make", "model", "vin",
        "engine", "engine_serial_no", "transmission",
        "location_city", "state", "truck_condition_notes",
        "last_name", "seller_phone", "seller_email", "seller_make",
        "status_old", "deal_notes", "buyer_notes", "photos",
        "photo_exterior_driver_side", "photo_engine_driver_side",
        "photo_cab", "photo_engine_passenger_side",
        "photo_exterior_front", "photo_exterior_passenger_side",
        "photo_exterior_rear", "copy_photo_exterior_driver_side",
        "scrapgo_logo", "print_pdf", "add_note",
        "user_name", "status", "pick_up_address", "pickup_contact",
        "pickup_phone", "alternative_contact", "alternative_phone",
        "buyer_bill_of_sale_google_sheet",
        "alternate_contact_pipeline", "alternate_phone_pipeline"
    }

    # === STANDARD CLEANERS ===
    def to_int(v):
        try: return int(v)
        except: return None

    def to_float(v):
        try: return float(v)
        except: return None

    def to_bool(v):
        if v is None: return None
        if isinstance(v, bool): return v

        v = str(v).strip().lower()
        if v in ["yes", "true", "1"]: return True
        if v in ["no", "false", "0"]: return False
        return None

    def to_timestamp(v):
        if not v: return None
        for fmt in ("%m-%d-%Y %I:%M %p", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(v, fmt)
            except:
                pass
        return None

    def to_date(v):
        if not v: return None
        try:
            return datetime.strptime(v, "%m-%d-%Y").date()
        except:
            try:
                return datetime.strptime(v, "%Y-%m-%d").date()
            except:
                return None

    # ======================================================
    #                     MAIN LOGIC
    # ======================================================
    for key, value in values.items():

        # Normalize blank / "None"
        if value in ("", "None", None):
            processed[key] = None
            continue

        # INT
        if key in int_fields:
            processed[key] = to_int(value)
            continue

        # NUMERIC
        if key in numeric_fields:
            processed[key] = to_float(value)
            continue

        # BOOLEAN
        if key in boolean_fields:
            processed[key] = to_bool(value)
            continue

        # BOOLEAN-LIKE but TEXT fields
        if key in boolean_like_but_text_fields:
            processed[key] = str(value)
            continue

        # TIMESTAMP
        if key in timestamp_fields:
            processed[key] = to_timestamp(value)
            continue

        # DATE
        if key in date_fields:
            processed[key] = to_date(value)
            continue

        # TEXT (leave as string)
        if key in text_fields:
            processed[key] = str(value)
            continue

        # DEFAULT fallback
        processed[key] = value

    return processed

async def upsert_record(conn, record_id, values):
    insert_query = Queries.INSERT_RECORD.format(table_name=DB_TABLE_NAME)
    # await insert_record(conn, record_id, values)
    args = [
        int(record_id),
        values.get("date_created"),
        values.get("date_modified"),
        values.get("last_modified_by"),
        values.get("source"),
        values.get("year"),
        values.get("make"),
        values.get("model"),
        values.get("vin"),
        values.get("mileage"),
        values.get("engine"),
        values.get("engine_serial_no"),
        values.get("transmission"),
        values.get("location_city"),
        values.get("state"),
        values.get("runs"),
        values.get("truck_condition_notes"),
        values.get("title"),
        values.get("first_name"),
        values.get("last_name"),
        values.get("seller_phone"),
        values.get("seller_email"),
        values.get("seller_make"),
        values.get("seller_target"),
        values.get("buyer_offer"),
        values.get("tow_price"),
        values.get("offer_price"),
        values.get("price_spread"),
        values.get("status_old"),
        values.get("deal_notes"),
        values.get("buyer_notes"),
        values.get("close_date"),
        values.get("photos"),
        values.get("photo_exterior_driver_side"),
        values.get("photo_engine_driver_side"),
        values.get("photo_cab"),
        values.get("photo_engine_passenger_side"),
        values.get("photo_exterior_front"),
        values.get("photo_exterior_passenger_side"),
        values.get("photo_exterior_rear"),
        values.get("copy_photo_exterior_driver_side"),
        values.get("scrapgo_logo"),
        values.get("print_pdf"),
        values.get("add_note"),
        values.get("num_of_notes"),
        values.get("maximum_date_modified"),
        values.get("user_name"),
        values.get("status"),
        values.get("fedex_label_sent"),
        values.get("pick_up_address"),
        values.get("pickup_contact"),
        values.get("pickup_phone"),
        values.get("alternative_contact"),
        values.get("alternative_phone"),
        values.get("buyer_bill_of_sale_google_sheet"),
        values.get("buyer_bill_of_sale_sheet_cb"),
        values.get("alternate_contact_pipeline"),
        values.get("alternate_phone_pipeline"),
        values.get("updated_form"),
    ]

    try:
        await conn.execute(insert_query, *args)
        print(f"[upsert_record] ✅ New entry created for record_id={record_id}")
    except asyncpg.exceptions.UniqueViolationError:
        # Repeated seller: archive the old row by nullifying its record_id,
        # then insert a fresh new entry so the full email flow runs clean.
        logging.warning(
            f"[upsert_record] ⚠️ record_id={record_id} already exists in DB. "
            f"Archiving old entry (setting record_id=NULL) and inserting new entry."
        )
        await conn.execute(
            f'UPDATE "{DB_TABLE_NAME}" SET record_id = NULL WHERE record_id = $1',
            int(record_id)
        )
        logging.info(f"[upsert_record] 🗃️ Old entry for record_id={record_id} archived (record_id set to NULL).")
        # Now insert the fresh row
        await conn.execute(insert_query, *args)
        logging.info(f"[upsert_record] ✅ New fresh entry created for repeated record_id={record_id}")

async def update_record(conn, table_name=DB_TABLE_NAME, where_key=None, record_id=None, values=None):
    # values = dict of columns to update
    # where_key = "record_id"
    record_id = int(record_id)

    if not values:
        return None  # nothing to update

    # Build SET clause dynamically
    set_clauses = []
    args = []
    idx = 1

    for column, val in values.items():
        set_clauses.append(f'"{column}" = ${idx}')
        args.append(val)
        idx += 1

    set_clause_sql = ", ".join(set_clauses)

    # WHERE condition
    where_param = f"${idx}"
    args.append(record_id)

    print("Checking the query")
    print("Table Name:", table_name)
    print("SET:", set_clause_sql)
    print("WHERE key:", where_param)
    print("WHERE param:", where_param)

    sql = f'''
        UPDATE "{table_name}"
        SET {set_clause_sql}
        WHERE "{where_key}" = {where_param}
        RETURNING *;
    '''

    return await conn.fetchrow(sql, *args)

async def delete_record(conn, record_id, where_key=None):
    print("Deleting record:", record_id)
    delete_query = f"DELETE FROM {DB_TABLE_NAME} WHERE {where_key} = $1"
    await conn.execute(delete_query, int(record_id))

"""
Below are the helper functions and endpoints for sendgrid
"""
import base64
import httpx
import os

QB_REALM = os.getenv("QB_REALM")
QB_USER_TOKEN = os.getenv("QB_USER_TOKEN")
QB_TABLE_ID = os.getenv("QB_TABLE_ID")  # "br5nbqyfg"
FIELD_ID_SEQUENCE = [38, 39, 46, 47, 48, 49]
QB_FILE_FIELD_ID = 38                  # Truck Photos
QB_KEY_FIELD_ID = 3                    # record_id

# Mapping for lead info fields (Quickbase field IDs)
LEAD_INFO_FIELD_IDS = {
    "first_name": 97,
    "last_name": 21,
    "email": 23,
    "phone": 22,
    "state": 17,
    "city": 16,
    "year": 8,
    "make": 9,
    "model": 10,
    "engine": 13,
    "transmission": 15,
}


async def upload_file_to_quickbase(
    record_id: int,
    field_id: int,
    filename: str,
    content: bytes,
    content_type: str,
):
    # 1) Base64 encode file bytes
    encoded = base64.b64encode(content).decode("ascii")

    # 2) Build /v1/records payload
    encoded = base64.b64encode(content).decode("ascii")
    url = "https://api.quickbase.com/v1/records"

    payload = {
        "to": QB_TABLE_ID,
        "data": [
            {
                str(QB_KEY_FIELD_ID): {"value": record_id},
                str(field_id): {
                    "value": {
                        "fileName": filename,
                        "data": encoded,
                    }
                },
            }
        ],
    }

    headers = {
        "QB-Realm-Hostname": QB_REALM,
        "Authorization": f"QB-USER-TOKEN {QB_USER_TOKEN}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload, headers=headers)

    if resp.status_code != 200:
        raise Exception(
            f"Quickbase file upload failed: {resp.status_code} | {resp.text}"
        )


async def update_lead_info_in_quickbase(
    record_id: int,
    lead_info: dict,
) -> None:
    """
    Update Quickbase lead information fields for a given record_id
    using the LEAD_INFO_FIELD_IDS mapping.
    """
    # Build field payload only for non-empty values, with basic type casting
    fields_payload = {}
    for key, field_id in LEAD_INFO_FIELD_IDS.items():
        raw_value = lead_info.get(key)
        if raw_value in (None, "", "null"):
            continue

        value = raw_value

        # Normalize and cast by field type
        if key == "year":
            # Quickbase Year is numeric; attempt int, skip if invalid
            try:
                value = int(str(raw_value).strip())
            except Exception:
                continue  # do not send invalid year
        else:
            # For text fields, ensure it's a clean string
            if not isinstance(raw_value, str):
                value = str(raw_value)
            value = value.strip()
            if not value:
                continue

        fields_payload[str(field_id)] = {"value": value}

    if not fields_payload:
        # Nothing to update
        return

    url = "https://api.quickbase.com/v1/records"

    payload = {
        "to": QB_TABLE_ID,
        "data": [
            {
                str(QB_KEY_FIELD_ID): {"value": record_id},
                **fields_payload,
            }
        ],
    }

    headers = {
        "QB-Realm-Hostname": QB_REALM,
        "Authorization": f"QB-USER-TOKEN {QB_USER_TOKEN}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload, headers=headers)

    if resp.status_code != 200:
        raise Exception(
            f"Quickbase lead info update failed: {resp.status_code} | {resp.text}"
        )

async def get_record_photo_status(record_id: int) -> dict:
    """
    Fetch the current values of photo fields from Quickbase for a specific record.
    Returns a dict mapping column names to their Quickbase status (empty or not).
    """
    from app.validators.image_validation import PHOTO_FIELD_MAP
    
    url = f"https://api.quickbase.com/v1/records/query"
    
    # Select record_id and all photo field IDs
    select_ids = [str(QB_KEY_FIELD_ID)] + [str(v) for v in PHOTO_FIELD_MAP.values()]
    
    payload = {
        "from": QB_TABLE_ID,
        "select": select_ids,
        "where": f"{{{QB_KEY_FIELD_ID}.EX.{record_id}}}"
    }

    headers = {
        "QB-Realm-Hostname": QB_REALM,
        "Authorization": f"QB-USER-TOKEN {QB_USER_TOKEN}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload, headers=headers)

    if resp.status_code != 200:
        logging.error(f"Failed to fetch photo status from Quickbase: {resp.text}")
        return {}

    data = resp.json().get("data", [])
    if not data:
        return {}

    record_data = data[0]
    result = {}
    
    # Reverse map: Field ID (as string) -> column name
    id_to_col = {str(v): k for k, v in PHOTO_FIELD_MAP.items()}
    
    for field_id_str, field_val in record_data.items():
        if field_id_str in id_to_col:
            col_name = id_to_col[field_id_str]
            # In Quickbase API v1, file fields return a dict with fileName or null
            val = field_val.get("value")
            if val and isinstance(val, dict) and val.get("fileName"):
                result[col_name] = val.get("fileName")
            else:
                result[col_name] = None
            
    return result