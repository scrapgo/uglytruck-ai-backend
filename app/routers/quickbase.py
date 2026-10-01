import os
import json
import asyncpg
from fastapi import APIRouter, Request, HTTPException
from datetime import datetime, timedelta
from dateutil import parser
from app.backend.config import settings
from app.quickbase.quickbase_fetch import fetch_tables, fetch_table_records_with_labels
from app.quickbase.quickbase_dump import insert_truck_sales_record, normalize_keys, normalize_value, normalize_quickbase_record
from app.database.database import get_connection
from app.database.queries import Queries
from app.quickbase.webhook_operations import upsert_record, update_record, delete_record, preprocess_values, fetch_and_sync_record_photos
from app.validators.image_validation import PHOTO_FIELD_MAP
from app.routers.agent import webhook_data_validator, webhook_call_agent
router = APIRouter()

import os
from dotenv import load_dotenv
load_dotenv()
DB_TABLE_NAME = os.environ.get("DB_TABLE_NAME")

import yaml
from pathlib import Path

def load_config(config_path: str = os.getenv("USER_CONFIG_PATH")) -> dict:
    """Load YAML config file."""
    with open(config_path, "r") as f:
        return yaml.safe_load(f)

user_config = load_config()

async def get_connection(database=None):
    return await asyncpg.connect(
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        host=settings.DB_HOST,
        database=database or settings.DB_NAME
    )

async def get_last_sync_date(conn):
    """
    Get the most recent 'date_modified' from local DB.
    Used to fetch only newer records from Quickbase.
    """
    query = f"""SELECT MAX(date_modified) AS last_sync FROM {DB_TABLE_NAME};"""
    result = await conn.fetchrow(query)
    if result and result["last_sync"]:
        return result["last_sync"].isoformat()
    return None



@router.get("/fetch_table")
async def get_tables():
    """Fetch Quickbase tables and optionally retrieve sample records."""

    # 1️⃣ List all available tables
    tables = fetch_tables()
    conn = await get_connection(settings.DB_NAME)
    last_sync_date = await get_last_sync_date(conn)
    # Convert string (e.g., '2025-10-30T17:14:08Z') → datetime object
    if last_sync_date:
        dt = parser.isoparse(last_sync_date)

        # Add 1 second to avoid fetching the same record again
        safe_sync_date = (dt + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        #safe_sync_date = (last_sync_date + timedelta(seconds=1)).isoformat() + "Z"
    else:
        safe_sync_date = None
    print(f"last_sync_date: {last_sync_date}")
    print(f"safe_sync_date: {safe_sync_date}")
    # 2️⃣ Fetch labeled records from a chosen table (if specified)
    records = None
    if settings.QB_TABLE_ID:
        records = fetch_table_records_with_labels(settings.QB_TABLE_ID, last_sync_date=safe_sync_date)
        # Post Processing for photos
        photos_fetched_records = []
        for rec in records:
            photos_fetched_records.append(normalize_quickbase_record(rec))
        #print("NEW RECORDS:", records)
        # 3️⃣ (Optional) Save the fetched records for later processing
        #if records:
        truck_sales_record_path = user_config["paths"]["truck_sales_records"]
        with open(truck_sales_record_path, "w") as f:
            json.dump(photos_fetched_records, f, indent=2)
        if records:
            print("💾 Records saved to truck_sales_records.json")
        else:
            print("💾 No New Records saved to truck_sales_records.json")

    return {"status":200,
        "message": "Fetched table list and sample records successfully." if records else "No New Records saved to truck_sales_records.json",
    }

@router.put("/dump_table")
async def dump_tables():
    mapper_path = user_config["paths"]["mapper"]
    truck_sales_record_path = user_config["paths"]["truck_sales_records"]
    with open(mapper_path, "r") as f:
        mapper = json.load(f)
    with open(truck_sales_record_path, "r") as f:
        records = json.load(f)
        print("RECORDS:", records)
        if records:
            for rec in records:
                #normalized = normalize_quickbase_record(rec)
                db_ready = normalize_keys(rec, mapper)
                insert_truck_sales_record(db_ready)
                #insert_truck_sales_record(normalize_keys(rec, mapper))
        else:
            return {"status":"success","message":"No new records found to dump."}

    return {"status": "success","message": "Records dumped successfully."}

@router.post("/sync_photos/{record_id}")
async def sync_photos_for_record(record_id: int):
    """
    Copy the photo URLs / filenames for one record from Quickbase into the local leads table
    and return what the row holds afterwards. Use it to backfill a lead or to diagnose why its
    photo_* columns are NULL.
    """
    photo_cols = list(PHOTO_FIELD_MAP.keys())
    conn = await get_connection()
    try:
        row = await conn.fetchrow(f'SELECT id FROM "{DB_TABLE_NAME}" WHERE record_id = $1', record_id)
        if not row:
            raise HTTPException(status_code=404, detail=f"record_id {record_id} not found in {DB_TABLE_NAME}")
        photos = await fetch_and_sync_record_photos(conn, record_id)
        found_in_quickbase = {k: v for k, v in (photos or {}).items() if v}
        after = await conn.fetchrow(
            f'SELECT {", ".join(photo_cols)} FROM "{DB_TABLE_NAME}" WHERE record_id = $1', record_id
        )
        db_values = {c: after[c] for c in photo_cols} if after else {}
        return {
            "record_id": record_id,
            "found_in_quickbase": found_in_quickbase,
            "db_values_after_sync": db_values,
            "message": "Photo columns updated" if found_in_quickbase else "No photo values found in Quickbase for this record",
        }
    finally:
        await conn.close()


@router.post("/sync_photos")
async def sync_photos_for_missing(limit: int = 50):
    """
    Backfill photo columns from Quickbase for the newest rows whose photo_* columns are all empty.
    """
    photo_cols = list(PHOTO_FIELD_MAP.keys())
    all_empty = " AND ".join(f'("{c}" IS NULL OR "{c}" = \'\')' for c in photo_cols)
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            f'SELECT record_id FROM "{DB_TABLE_NAME}" WHERE record_id IS NOT NULL AND {all_empty} '
            f'ORDER BY id DESC LIMIT $1',
            limit,
        )
        results = {}
        for r in rows:
            rid = int(r["record_id"])
            photos = await fetch_and_sync_record_photos(conn, rid)
            results[rid] = sorted(k for k, v in (photos or {}).items() if v)
        return {"checked": len(results), "updated": sum(1 for v in results.values() if v), "results": results}
    finally:
        await conn.close()


@router.post("/fetch_and_dump")
async def fetch_and_dump():
    # Step 1: Fetch data
    await get_tables()
    # Step 2: Dump into DB
    await dump_tables()
    return {"message": "Fetched from Quickbase and dumped into DB successfully"}

