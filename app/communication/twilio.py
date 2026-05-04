from twilio.rest import Client
from app.backend.config import settings
from app.database.queries import Queries
from app.database.database import get_connection
from app.communication.base import EmailService, SMSRequest

import os
from dotenv import load_dotenv
load_dotenv()
DB_TABLE_NAME = os.environ.get("DB_TABLE_NAME")

class TwilioService(EmailService):
    def __init__(self):
        if settings.ENABLE_TWILIO:
            self.client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        else:
            self.client = Client(settings.TWILIO_ACCOUNT_SID, None)
            print("Twilio not enabled")

    async def load_truck_details(self, num_rows: int = 2, table_name: str = DB_TABLE_NAME):
        """
        Fetch truck details from the database and convert them to TruckEmailData instances.
        """
        truck_phone_data = []
        conn = await get_connection()

        try:
            query = Queries.SELECT_LIMIT_BY_TRUCKS if num_rows else Queries.SELECT_ALL_TRUCKS
            print("Query:", query.format(table_name=table_name, limit=num_rows))
            rows = await conn.fetch(query.format(table_name=table_name, limit=num_rows))
            if not rows:
                return truck_phone_data
            print("Rows:", rows)
            for record in rows:
                print("Record:", record)
                truck_phone_item = SMSRequest(
                    RECORD_ID=record["RECORD_ID"],
                    phone_number=record["seller_phone"],
                    FNAME=record["last_name"],
                    MAKE=record["make"],
                    MODEL=record["model"],
                    CITY=record["location_city"],
                    STATUS=record["status"],
                )
                truck_phone_data.append(truck_phone_item)

            return truck_phone_data
        except Exception as e:
            return {"error": str(e)}
