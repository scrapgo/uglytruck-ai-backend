from fastapi import APIRouter
from typing import List, Dict, Any
import asyncpg

# 👇 adjust this import to wherever your get_connection is defined
from app.database.database import get_connection  # or from .database import get_connection

import os
from dotenv import load_dotenv
load_dotenv()
DB_TABLE_NAME = os.environ.get("DB_TABLE_NAME")

router = APIRouter()


# ---------- 1. STATUS ANALYTICS ----------
@router.get("/status")
async def get_status_analytics() -> Dict[str, Any]:
  """
  Returns count of records per Status.
  Example response:
  {
    "items": [
      {"status": "New Lead", "count": 120},
      {"status": "Need Pics", "count": 45},
      ...
    ]
  }
  """
  conn: asyncpg.Connection = await get_connection()
  try:
    query = f"""
      SELECT status, COUNT(*) AS count
      FROM {DB_TABLE_NAME}
      GROUP BY status
      ORDER BY count DESC;
    """
    rows = await conn.fetch(query)

    items = [
      {
        "status": row["status"],
        "count": row["count"],
      }
      for row in rows
    ]

    return {"items": items}
  finally:
    await conn.close()


# ---------- 2. COMMUNICATION STATUS ANALYTICS ----------
@router.get("/communication")
async def get_communication_analytics() -> Dict[str, Any]:
  """
  Returns count of records per communication_status.
  Example response:
  {
    "items": [
      {"code": 0, "label": "New Record", "count": 22},
      {"code": 1, "label": "Email required to send", "count": 45},
      ...
    ]
  }
  """

  # Map codes to labels (same mapping you described)
  communication_labels = {
    0: "New Record",
    1: "Email required to send",
    2: "Email not required to send",
    3: "Email Sent",
  }

  conn: asyncpg.Connection = await get_connection()
  try:
    query = f"""
      SELECT communication_status, COUNT(*) AS count
      FROM {DB_TABLE_NAME}
      GROUP BY communication_status
      ORDER BY communication_status;
    """
    rows = await conn.fetch(query)

    items = []
    for row in rows:
      code = row["communication_status"]
      items.append(
        {
          "code": code,
          "label": communication_labels.get(code, "Unknown"),
          "count": row["count"],
        }
      )

    return {"items": items}
  finally:
    await conn.close()


# ---------- 3. SOURCE ANALYTICS ----------
@router.get("/source")
async def get_source_analytics() -> Dict[str, Any]:
  """
  Returns count of records per Source (e.g. Inbound / Outbound / Other).
  Example response:
  {
    "items": [
      {"source": "Outbound", "count": 80},
      {"source": "Inbound", "count": 120}
    ]
  }
  """
  conn: asyncpg.Connection = await get_connection()
  try:
    query = f"""
      SELECT source, COUNT(*) AS count
      FROM {DB_TABLE_NAME}
      GROUP BY source
      ORDER BY count DESC;
    """
    rows = await conn.fetch(query)

    items = [
      {
        "source": row["source"],
        "count": row["count"],
      }
      for row in rows
    ]

    return {"items": items}
  finally:
    await conn.close()


# ---------- 4. MAKE DISTRIBUTION ANALYTICS ----------
@router.get("/make_distribution")
async def get_make_distribution() -> Dict[str, Any]:
    """
    Returns normalized (case-insensitive) count of records per Make.
    """
    conn: asyncpg.Connection = await get_connection()
    try:
        query = f"""
            SELECT 
                LOWER(
                    TRIM(
                        REGEXP_REPLACE(make, '\s+', ' ', 'g')
                    )
                ) AS make,
                COUNT(*) AS count
            FROM public.unbounce_leads
            GROUP BY LOWER(
                       TRIM(
                          REGEXP_REPLACE(make, '\s+', ' ', 'g')
                       )
                     )
            ORDER BY count DESC;
        """
        rows = await conn.fetch(query)

        items = [
            {
                # Convert "freightliner" → "Freightliner"
                "make": row["make"].title() if row["make"] is not None else "Others",
                "count": row["count"],
            }
            for row in rows
        ]

        return {"items": items}
    finally:
        await conn.close()
