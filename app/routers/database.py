from fastapi import APIRouter, Query, Depends, HTTPException
import asyncpg
import json
from typing import Optional, List, Dict, Tuple
from app.backend.config import settings
from app.database.queries import Queries
from app.routers.quickbase import get_last_sync_date
from app.quickbase.quickbase_dump import normalize_keys
from app.utils.database_utils import format_record

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


@router.post("/create_database/{db_name}")
async def create_database(db_name: str):
    conn = await get_connection("postgres")
    try:
        await conn.execute(Queries.CREATE_DATABASE.format(db_name=db_name))
        return {"message": f"Database '{db_name}' created successfully."}
    except Exception as e:
        return {"error": str(e)}
    finally:
        await conn.close()


@router.put("/update_database/{old_name}/{new_name}")
async def update_database(old_name: str, new_name: str):
    conn = await get_connection("postgres")
    try:
        await conn.execute(Queries.UPDATE_DATABASE.format(old_name=old_name, new_name=new_name))
        return {"message": f"Database renamed from '{old_name}' to '{new_name}'"}
    except Exception as e:
        return {"error": str(e)}
    finally:
        await conn.close()


@router.delete("/flush_database/{table_name}")
async def flush_database(table_name: str):
    conn = await get_connection()
    try:
        # tables = await conn.fetch(Queries.LIST_TABLES)
        # for t in tables:
        await conn.execute(Queries.FLUSH_TABLE.format(table_name=table_name))
        return {"message": "All tables dropped successfully."}
    except Exception as e:
        return {"error": str(e)}
    finally:
        await conn.close()


@router.post("/create_table/{table_name}")
async def create_table(table_name: str):
    conn = await get_connection()
    try:
        await conn.execute(Queries.CREATE_TABLE.format(table_name=table_name))
        return {"message": f"Table '{table_name}' created successfully."}
    except Exception as e:
        return {"error": str(e)}
    finally:
        await conn.close()

@router.get("/fetch_table")#{table_name}")
async def fetch_table(table_name: str=DB_TABLE_NAME,
    columns: Optional[List[str]] = Query(None, description="List of column names to fetch"),
    where_key: Optional[str] = None,
    where_value: Optional[str] = None,
    formatted_output: bool = True):

    conn = await get_connection()
    try:
        # Fetch all rows from given table
        # Build WHERE dictionary only if both key & value are given
        where = {where_key: where_value} if where_key and where_value else None
        if not columns or columns=="None":
            columns = "*"
        query = Queries.FETCH_TABLE.format(
    table_name=table_name,
    column_part=columns,
    where_clause=f" WHERE {where_key}='{where_value}'" if where_key and where_value else ""
)
        rows = await conn.fetch(query)
        denormalized_rows = []
        # Reverse Key Mapping
        mapper_path = user_config["paths"]["mapper"]
        with open(mapper_path, "r") as f:
            mapper = json.load(f)
        reverse_mapper = {value:key for key, value in mapper.items()}
        for rec in rows:
            denormalized_rows.append(normalize_keys(rec, reverse_mapper))
        # Formatting Record
        formatted_denormalized_rows = []
        for record in denormalized_rows:
            formatted_denormalized_rows.append(format_record(record))
        # print("FORMATTED ROWS", formatted_denormalized_rows)
        # Calculate required info
        total_records = len(formatted_denormalized_rows)
        total_fields = len(formatted_denormalized_rows[0]) if rows else 0
        data_size = sum(len(str(r)) for r in formatted_denormalized_rows) / 1024  # Calculate size in KB (rough estimate)

        # Format last sync (for now, just mock this — replace with real logic if possible)
        last_sync = await get_last_sync_date(conn)# Replace with actual timestamp of the last sync, if available

        # Fetch Keys
        fetch_keys = ["Last Name", "Location (City)", "Year", "Make", "Model", "VIN", "Mileage", "Engine", "Transmission", "Seller Phone", "Seller Email"]
        data = [{key: row.get(key) for key in fetch_keys} for row in rows]
        if formatted_output:
            data = [{key: row.get(key) for key in fetch_keys} for row in formatted_denormalized_rows]
        # Convert records to dictionaries for JSON serialization
        return {
            "table": table_name,
            "data": [dict(r) for r in (formatted_denormalized_rows if formatted_output else rows)],
            "total_records": total_records,
            "total_fields": total_fields,
            "data_size": round(data_size, 2),  # Size in KB
            "last_sync": last_sync
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        await conn.close()

@router.get("/paginated_fetch_table")  # {table_name}
async def paginated_fetch_table(
    table_name: str = DB_TABLE_NAME,
    columns: Optional[List[str]] = Query(None, description="List of column names to fetch"),
    where_key: Optional[str] = None,
    where_value: Optional[str] = None,
    formatted_output: bool = True,
    page: int = 1,          # NEW
    limit: int = 20         # NEW
):
    conn = await get_connection()
    try:
        # Pagination math
        offset = (page - 1) * limit

        if not columns or columns == "None":
            columns = "*"

        # WHERE clause
        where_clause = f" WHERE {where_key}='{where_value}'" if where_key and where_value else ""

        # Paginated Query
        query = f"""
            SELECT {columns}
            FROM {table_name}
            {where_clause}
            ORDER BY date_created DESC
            LIMIT {limit} OFFSET {offset}
        """

        rows = await conn.fetch(query)

        # ----------------------------
        # COUNT query (total records)
        # ----------------------------
        count_query = f"""
            SELECT COUNT(*) 
            FROM {table_name}
            {where_clause}
        """
        total_records = await conn.fetchval(count_query)

        # Reverse Key Mapping
        mapper_path = user_config["paths"]["mapper"]
        with open(mapper_path, "r") as f:
            mapper = json.load(f)
        reverse_mapper = {value: key for key, value in mapper.items()}

        denormalized_rows = [
            normalize_keys(rec, reverse_mapper) for rec in rows
        ]

        # Formatting
        formatted = [format_record(rec) for rec in denormalized_rows]

        # Size
        data_size = sum(len(str(r)) for r in formatted) / 1024

        # Last sync
        last_sync = await get_last_sync_date(conn)

        return {
            "table": table_name,
            "page": page,
            "limit": limit,
            "total_records": total_records,                   # NEW
            "total_pages": (total_records + limit - 1) // limit,  # NEW
            "data": formatted if formatted_output else [dict(r) for r in rows],
            "data_size": round(data_size, 2),
            "last_sync": last_sync
        }

    except Exception as e:
        return {"error": str(e)}
    finally:
        await conn.close()

@router.delete("/delete_column")
async def delete_column(table_name: str, column_name: str):
    conn = await get_connection()
    try:
        query = Queries.DROP_COLUMN.format(
            table_name=table_name,
            column_name=column_name
        )

        #async with db.pool.acquire() as conn:
        await conn.execute(query)

        return {
            "status": "success",
            "message": f"Column '{column_name}' deleted from table '{table_name}'"
        }

    except Exception as e:
        return {"error": str(e)}

@router.put("/update_table/{table_name}")
async def update_table(table_name: str, column_name: str, column_type: str = "TEXT"):
    conn = await get_connection()
    try:
        await conn.execute(
            Queries.ADD_COLUMN.format(table_name=table_name, column_name=column_name, column_type=column_type)
        )
        return {"message": f"Column '{column_name}' added to '{table_name}'."}
    except Exception as e:
        return {"error": str(e)}
    finally:
        await conn.close()


@router.delete("/delete_table/{table_name}")
async def delete_table(table_name: str):
    conn = await get_connection()
    try:
        await conn.execute(Queries.DELETE_TABLE.format(table_name=table_name))
        return {"message": f"Table '{table_name}' deleted successfully."}
    except Exception as e:
        return {"error": str(e)}
    finally:
        await conn.close()
