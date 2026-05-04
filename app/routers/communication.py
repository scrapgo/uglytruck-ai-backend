import os
import json
import asyncpg
from fastapi import APIRouter, HTTPException
from typing import Optional, Dict, List
from app.utils.mailchimp_utils import (
    extract_text_from_html,
    dynamic_html_template,
    update_template,
    create_campaign,
    send_test_email_via_mailchimp,
)
from app.utils.gmail_utils import send_gmail_email
from app.utils.sendgrid_utils import send_sendgrid_email
from app.utils.twilio_utils import design_message_body
from app.backend.config import settings
from app.communication.base import TruckEmailData, SMSRequest
from app.communication.mailchimp import MailchimpService
from app.communication.twilio import TwilioService
from app.communication.gmail import GmailService
from app.communication.sendgrid import SendGridService

from app.database.queries import Queries
from app.database.database import get_connection

from dotenv import load_dotenv
load_dotenv()
ENABLE_HARDCODINGS = os.getenv('ENABLE_HARDCODINGS', 'False').lower() == 'true'
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


router = APIRouter()

@router.post("/send-email")
async def send_email(data: Dict):
    print("<----------- SENDING EMAIL VIA SENDGRID ----------->")
    sendgrid_service = SendGridService()
    seller_obj = data
    where_key = "record_id"
    where_value = data["RECORD_ID"]
    table_name = DB_TABLE_NAME

    # load communication_Subject
    communication_subject_query = Queries.FETCH_TABLE.format(
        table_name=table_name,
        column_part='communication_subject',
        where_clause=f" WHERE {where_key}='{where_value}'" if where_key and where_value else ""
    )

    conn = await get_connection()
    row = await conn.fetchrow(communication_subject_query)  # <- FIXED

    if not row:
        raise HTTPException(status_code=404, detail="No matching record found")

    comm_subject = row.get("communication_subject")
    print("COMM SUBJECT:", comm_subject)
    # Handle None
    if comm_subject is None:
        comm_subject = []
    # Handle CSV format
    elif "," in comm_subject:
        comm_subject = [s.strip() for s in comm_subject.split(",")]
    else:
        comm_subject = [comm_subject]  # normalize to list

    print("COMM_SUBJECT:", comm_subject)
    # Hardcoded for test
    print("ENABLE_HARDCODINGS:", ENABLE_HARDCODINGS)
    cc_email = user_config["hardcodings"]["emails"]["cc"]
    if ENABLE_HARDCODINGS:
        seller_obj['EMAIL'] = user_config['hardcodings']['emails']['to']
    print("WE REACHED HERE")
    print(seller_obj['RECORD_ID'])
    print(seller_obj['EMAIL'])
    #try:
    for subject in comm_subject:
        html_content = dynamic_html_template(seller_obj, subject)
        email_subject = f"Truck Inquiry - REF ID:{seller_obj['RECORD_ID']}"
        send_sendgrid_email(
            service=sendgrid_service,
            to_email=seller_obj["EMAIL"],
            cc_email=None,
            subject=email_subject,
            html_content=html_content,
            attachments=data.get("ATTACHMENTS")
        )
    print(f"✅ Sent email to {seller_obj['EMAIL']}")
    # Converting html content to text
    html_to_str_content = extract_text_from_html(str(html_content))
    # except Exception as e:
    #     print("Error occured:", str(e))
    #     raise HTTPException(status_code=500, detail=str(e))

    return {"status": 200,
            "message": f"Sent test email to {seller_obj['EMAIL']}",
            "subject": email_subject,
            "body": html_to_str_content}

@router.post("/send-gmail")
async def send_gmail(data: Dict):

    gmail_service = GmailService()
    seller_obj = data
    # Use the same key as other parts of the system (e.g. Quickbase + send-email)
    # so we actually find the record by its RECORD_ID.
    where_key = "record_id"
    where_value = data["RECORD_ID"]
    table_name = DB_TABLE_NAME

    # load communication_Subject
    communication_subject_query = Queries.FETCH_TABLE.format(
        table_name=table_name,
        column_part='communication_subject',
        where_clause=f" WHERE {where_key}='{where_value}'" if where_key and where_value else ""
    )

    conn = await get_connection()
    row = await conn.fetchrow(communication_subject_query)  # <- FIXED

    if not row:
        raise HTTPException(status_code=404, detail="No matching record found")

    comm_subject = row.get("communication_subject")
    print("COMM SUBJECT:", comm_subject)
    # Handle None
    if comm_subject is None:
        comm_subject = []
    # Handle CSV format
    elif "," in comm_subject:
        comm_subject = [s.strip() for s in comm_subject.split(",")]
    else:
        comm_subject = [comm_subject]  # normalize to list

    print("COMM_SUBJECT:", comm_subject)
    # Hardcoded for test
    print("ENABLE_HARDCODINGS:", ENABLE_HARDCODINGS)
    cc_email = user_config["hardcodings"]["emails"]["cc"]
    if ENABLE_HARDCODINGS:
        seller_obj['EMAIL'] = user_config['hardcodings']['emails']['to']
    print("WE REACHED HERE")

    try:
        for subject in comm_subject:
            html_content = dynamic_html_template(seller_obj, subject)
            email_subject = f"Truck Inquiry - REF ID:{seller_obj['RECORD_ID']}"
            result = send_gmail_email(
                gmail_service.service,
                to_email=seller_obj['EMAIL'],
                cc_email=None,
                subject=email_subject,
                html_content=html_content,
            )
        print(f"✅ Sent test email to {seller_obj['EMAIL']}")
        # Converting html content to text
        html_to_str_content = extract_text_from_html(str(html_content))
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail=str(e))

    return {"status": 200,
            "message": f"Sent test email to {seller_obj['EMAIL']}",
            "subject":email_subject,
            "body": html_to_str_content}

@router.post("/send-test")
async def send_test_email(): #data: TruckEmailData):
    service = MailchimpService()
    recipient_list = await service.load_truck_details()
    print("Recipient List:", recipient_list)
    # Step 1: Generate Personalized HTML
    # html_content = dynamic_html_template([data.dict()])[0]
    for seller_obj in recipient_list:

        # HARD CODING
        seller_obj.EMAIL = "jayakrishnanr@smartdatainc.net"
        html_content = dynamic_html_template(seller_obj.dict())

        # Step 2: Update Mailchimp Template
        update_template(service.client, settings.MAILCHIMP_TEMPLATE_ID, str(html_content))

        # Step 3: Create/Reuse Campaign
        campaign_id = settings.MAILCHIMP_CAMPAIGN_ID
        if not campaign_id or campaign_id == "None":
            campaign_id = create_campaign(service.client, seller_obj.EMAIL, settings.MAILCHIMP_TEMPLATE_ID)

        # Step 4: Send Test Email
        try:
            result = send_test_email_via_mailchimp(service.client, campaign_id, seller_obj.EMAIL)
            print("Sucessfully sent test email to {}".format(seller_obj.EMAIL))
            # return {"status": "success", "result": result}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@router.post("/send-sms")
async def send_sms(request: Dict):
    """
    Send a single SMS using Twilio for a specific seller.

    This is intended to be called right after an email is sent for a truck
    enquiry, to notify the seller to check their email.
    """
    client = TwilioService()
    body = design_message_body(request)
    print("ENABLE_HARDCODINGS:", ENABLE_HARDCODINGS)
    if ENABLE_HARDCODINGS:
        request['PHONE_NUMBER'] = user_config['hardcodings']['phone_numbers']['to']
    # Intro
    try:
        to_number = request.get("PHONE_NUMBER")
        message = client.client.messages.create(
            body=body,
            from_=settings.TWILIO_PHONE_NUMBER,
            to=to_number,
        )
        print(f"Status : SUCCESS, SID: {message.sid}, to: {to_number}")
        return {
            "status": "success",
            "sid": message.sid,
            "to": to_number,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))