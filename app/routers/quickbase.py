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
from app.quickbase.webhook_operations import upsert_record, update_record, delete_record, preprocess_values
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

@router.post("/fetch_and_dump")
async def fetch_and_dump():
    # Step 1: Fetch data
    await get_tables()
    # Step 2: Dump into DB
    await dump_tables()
    return {"message": "Fetched from Quickbase and dumped into DB successfully"}

