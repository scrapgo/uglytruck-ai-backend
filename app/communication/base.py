from pydantic import BaseModel, EmailStr, field_validator
from app.database.database import get_connection
from app.database.queries import Queries
from app.utils.status import status
from typing import Optional, List, Dict, Tuple, Union

import os
from dotenv import load_dotenv
load_dotenv()
DB_TABLE_NAME = os.environ.get("DB_TABLE_NAME")

class TruckEmailData(BaseModel):
    RECORD_ID: int
    TRUCK_ID: Optional[str]
    FNAME: Optional[str]
    EMAIL: List[EmailStr]
    YEAR: Optional[str] = None
    MAKE: Optional[str]
    MODEL: Optional[str]
    CITY: Optional[str]
    STATUS: Optional[str]
    NOTES: Optional[str]
    MISSING_VIEWS: Optional[List[str]] = []
    MISSING_LEAD_FIELDS_TEXT: Optional[str] = None
    ATTACHMENTS: Optional[List[Dict]] = []
    TRANSPORTATION: Optional[str] = None

    @field_validator("EMAIL", mode="before")
    def split_emails(cls, v):
        """Allow comma- or semicolon-separated emails."""
        if v is None:
            return []  # <-- return empty list instead of failing

        if isinstance(v, str):
            parts = [e.strip() for e in v.replace(";", ",").split(",") if e.strip()]
            return parts
        return v

# Request body schema
class SMSRequest(BaseModel):
    RECORD_ID: int
    PHONE_NUMBER: Optional[str]
    FNAME: Optional[str]
    MAKE: Optional[str]
    MODEL: Optional[str]
    CITY: Optional[str]
    STATUS: str


class EmailService:
    def __init__(self, database=None):
        pass

    async def load_truck_details(self, num_rows: int = 2, table_name: str = DB_TABLE_NAME) -> List[TruckEmailData]:
        """
        Fetch truck details from the database and convert them to TruckEmailData instances.
        """
        truck_mail_data = []
        conn = await get_connection()

        try:
            # Query to fetch top num_rows records
            # query = Queries.SELECT_LIMIT_BY_TRUCKS if num_rows else Queries.SELECT_ALL_TRUCKS
            # print("Query:",query.format(table_name=table_name, limit=num_rows))
            query = Queries.SELECT_TRUCK_BY_STATUS
            print("Query:",query.format(table_name=table_name, status=status.NEED_PICS))
            rows = await conn.fetch(query.format(table_name=table_name, limit=num_rows))
            if not rows:
                return truck_mail_data
            print("Rows:",rows)
            for record in rows:
                print("Record:", record)
                truck_mail_item = TruckEmailData(
                    TRUCK_ID=record["vin"],
                    FNAME=(record.get("first_name") or record.get("last_name") or "").strip(),
                    EMAIL=record["seller_email"],
                    YEAR=str(record.get("year") or "").strip(),
                    MAKE=record["make"],
                    MODEL=record["model"],
                    CITY=record["location_city"],
                    STATUS=record["status"],
                    NOTES=record["truck_condition_notes"]
                )
                truck_mail_data.append(truck_mail_item)

            return truck_mail_data
        except Exception as e:
            return {"error": str(e)}