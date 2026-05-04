import os
import asyncpg
from fastapi import APIRouter, HTTPException
import httpx
import base64
import mimetypes
from typing import List
from pydantic import BaseModel
from app.backend.config import settings
from app.database.queries import Queries
from app.communication.base import TruckEmailData, SMSRequest
from app.utils.mailchimp_utils import dynamic_html_template, generate_personalized_emails
from app.communication.conversation_repository import store_conversation_message, send_alert_to_dale
from app.validators.image_validation import validate_images

router = APIRouter()
from dotenv import load_dotenv
load_dotenv()

HOST = os.getenv("HOST")
PORT = os.getenv("PORT")
ENABLE_HARDCODINGS = os.getenv("ENABLE_HARDCODINGS", "False").lower() == "true"
SAMPLE_TEST_RECORDS = os.getenv("SAMPLE_TEST_RECORDS")
DB_TABLE_NAME = os.environ.get("DB_TABLE_NAME")
DB_CONVERSATION_TABLE = os.environ.get("DB_CONVERSATION_TABLE")

FETCH_TABLE_URL = f"http://{HOST}:{PORT}/database/fetch_table"
OPEN_CLIP_URL = f"http://{HOST}:{PORT}/ai/open-clip"
GPT_VISION_URL = f"http://{HOST}:{PORT}/ai/gpt-vision"
SENDGRID_ENDPOINT = f"http://{HOST}:{PORT}/communication/send-email"
SEND_GMAIL_URL = f"http://{HOST}:{PORT}/communication/send-gmail"
SEND_SMS_URL = f"http://{HOST}:{PORT}/communication/send-sms"

# Status → communication_subject mapping
STATUS_SUBJECT_MAP = {
    "completed": "complete_status",
    "closed": "closed_status",
    "dnc": "dnc_status",
    "docs received": "docs_received_status",
    "funded": "funded_status",
    "sold": "sold_truck",
    "unsold": "unsold_truck",
    "followup": "followup_truck",
    "unresponsive": "unresponsive_follow_up_status",
    "offer made": "offer_made",                    # ← ADD THIS
    "offer accepted": "offer_accepted",            # ← ADD THIS
    "offer declined": "offer_declined",            # ← ADD THIS (if needed)
}

async def get_connection(database=None):
    return await asyncpg.connect(
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        host=settings.DB_HOST,
        database=database or settings.DB_NAME
    )

# ----- Core Agent -----
@router.post("/data-validator")
async def data_validator():

    conn = await get_connection()

    async with httpx.AsyncClient(timeout=60) as client:

        # 1️⃣ Fetch DB records where communication_status = 0
        params = {
            "table_name": DB_TABLE_NAME,
            "where_key": "communication_status",
            "where_value": "0",
            "formatted_output": False
        }

        resp = await client.get(FETCH_TABLE_URL, params=params)
        if resp.status_code != 200:
            raise HTTPException(status_code=500, detail=f"Fetch failed: {resp.text}")

        records = resp.json().get("data", [])
        print("Records fetched:", len(records))

        if not records:
            return {"message": "No records found needing pictures."}

        # Limit if hard-coded testing is enabled
        sample_limit = int(SAMPLE_TEST_RECORDS) if ENABLE_HARDCODINGS else len(records)

        # 2️⃣ Loop through records
        for rec in records[:sample_limit]:
            comm_subject = []
            # ---- Delegate image validation to separate module ----
            image_urls, missing_views = await validate_images(rec, GPT_VISION_URL)

            # ---- Compute new communication_status
            if not image_urls:
                new_status = 1   # Email MUST be sent
                comm_subject.append('missing_truck')
                print(f"Record {rec['id']}: No images → status=1")

            elif missing_views:
                new_status = 1   # Email MUST be sent
                comm_subject.append('missing_truck')
                print(f"Record {rec['id']}: Missing views {missing_views} → status=1")

            else:
                new_status = 2   # No email needed
                print(f"Record {rec['id']}: All views OK → status=2")

            # Additional business rule: If record has manual status override
            record_status = (rec.get("status") or "").strip().lower()

            if record_status in STATUS_SUBJECT_MAP:
                new_status = 1  # Force send email
                comm_subject.append(STATUS_SUBJECT_MAP[record_status])

            print(f"[{rec['id']}] Final Communication Subject → {comm_subject}")

            # ---- Update DB ----
            update_communication_status = Queries.UPDATE_RECORD.format(
                table_name=DB_TABLE_NAME,
                set_clause="communication_status",
                where_key="id"
            )
            update_communication_subject = Queries.UPDATE_RECORD.format(
                table_name=DB_TABLE_NAME,
                set_clause="communication_subject",
                where_key="id"
            )
            await conn.fetchrow(update_communication_status, new_status, rec["id"])
            if comm_subject:
                await conn.fetchrow(update_communication_subject, ','.join(comm_subject), rec["id"])
            print(f"✔ Updated communication_status={new_status} for ID={rec['id']}")

    return {"message": "Image validation completed"}

@router.post("/webhook-data-validator")
async def webhook_data_validator(record_id: int, set_missing_truck_none: bool=False, set_received_documents: bool=False):

    conn = await get_connection()

    async with httpx.AsyncClient(timeout=60) as client:

        # 1️⃣ Fetch DB records where communication_status = 0
        params = {
            "table_name": DB_TABLE_NAME,
            "where_key": "record_id",
            "where_value": record_id,
            "formatted_output": False
        }

        resp = await client.get(FETCH_TABLE_URL, params=params)
        if resp.status_code != 200:
            raise HTTPException(status_code=500, detail=f"Fetch failed: {resp.text}")

        records = resp.json().get("data", [])
        print("Records fetched:", len(records))

        if not records:
            return {"message": "No records found needing pictures."}

        # Limit if hard-coded testing is enabled
        records = records[:int(SAMPLE_TEST_RECORDS)] if ENABLE_HARDCODINGS else records

        # 2️⃣ Loop through records
        missing_views_result = None  # Store missing_views to return
        for rec in records:
            comm_subject = []
            # 0️⃣ Pre-check: verify required lead details are present before image validation
            lead_info_flag = int(rec.get("lead_information_received") or 0)
            skip_image_validation = False

            required_fields = {
                "first_name": "First Name",
                "last_name": "Last Name",
                "seller_email": "Email",
                "seller_phone": "Phone",
                "state": "State",
                "location_city": "City",
                "year": "Truck Year",
                "make": "Truck Make",
                "model": "Truck Model",
                "engine": "Engine Type",
                "transmission": "Transmission Type",
            }

            # Only run the lead-info completeness check if flag is not yet set
            if not lead_info_flag:
                print(f"Record {rec['id']}: running lead info validation")
                missing_info_labels = [
                    label
                    for field, label in required_fields.items()
                    if not rec.get(field)
                ]

                if missing_info_labels:
                    # If key contact/vehicle info is missing, request those details from seller
                    new_status = 1  # Email MUST be sent
                    comm_subject.append("missing_lead_info")
                    skip_image_validation = True
                    missing_views_result = []  # No missing views when lead info is missing
                    print(
                        f"Record {rec['id']}: Missing required lead fields {missing_info_labels} "
                        f"→ status=1, subject=missing_lead_info (skipping image validation)"
                    )
                else:
                    # All required lead info present → mark flag = 1 and update status to "Need Pics"
                    rec["lead_information_received"] = 1
                    update_lead_information_received_status = Queries.UPDATE_RECORD.format(
                        table_name=DB_TABLE_NAME,
                        set_clause="lead_information_received",
                        where_key="id",
                    )
                    await conn.fetchrow(
                        update_lead_information_received_status, 1, rec["id"]
                    )
                    # Update Quickbase status to "Need Pics" when lead info is complete
                    from app.communication.conversation_repository import update_quickbase_status
                    await update_quickbase_status(rec["record_id"], "Need Pics")
                    print(
                        f"Record {rec['id']}: All required lead info present → "
                        f"lead_information_received=1, status updated to 'Need Pics'"
                    )

            # If lead info is incomplete, skip image validation and go straight to DB updates
            if not skip_image_validation:
                # ---- Delegate image validation to separate module ----
                if not set_missing_truck_none:
                    image_urls, missing_views = await validate_images(rec, GPT_VISION_URL)
                    missing_views_result = missing_views  # Store for return
                else:
                    image_urls = True  # If we need to skip validation forcefully
                    missing_views = None
                    missing_views_result = []  # Empty list when set_missing_truck_none is True
                # ---- Compute new communication_status
                if not image_urls:
                    new_status = 1   # Email MUST be sent
                    comm_subject.append('missing_truck')
                    print(f"Record {rec['id']}: No images → status=1")

                elif missing_views:
                    new_status = 1   # Email MUST be sent
                    comm_subject.append('missing_truck')
                    print(f"Record {rec['id']}: Missing views {missing_views} → status=1")

                else:
                    new_status = 1   # email needed for requesting documents
                    print(f"Record {rec['id']}: All views OK → status=2")
                    comm_subject.append('complete_status')

            if rec['truck_view_pics_received_confirmation']:
                if 'missing_truck' in comm_subject:
                    comm_subject.remove('missing_truck')


            if rec['documents_received_confirmation']:
                if 'missing_truck' in comm_subject:
                    comm_subject.remove('missing_truck')
                comm_subject = ['complete_status']
            # Additional business rule: If record has manual status override
            # record_status = (rec.get("status") or "").strip().lower()
            #
            # if record_status in STATUS_SUBJECT_MAP:
            #     new_status = 1  # Force send email
            #     comm_subject.append(STATUS_SUBJECT_MAP[record_status])

            print(f"[{rec['id']}] Final Communication Subject → {comm_subject}")

            # If requesting documents, also update status to "Need Pics" if not already set
            if 'complete_status' in comm_subject:
                if 'missing_truck' in comm_subject:
                    comm_subject.remove('missing_truck')
                from app.communication.conversation_repository import update_lead_status_pg, update_quickbase_status
                # Update both PostgreSQL and Quickbase - update_lead_status_pg will send alert and update Quickbase
                await update_lead_status_pg(rec["record_id"], "Complete")

            # ---- Update DB ----
            # Avoid resetting status to 1 (Required) if we already sent the exact same subjects (status 3)
            current_comm_status = str(rec.get("communication_status") or "0")
            current_comm_subject = rec.get("communication_subject") or ""
            new_comm_subject_str = ','.join(comm_subject)

            if current_comm_status == "3" and new_comm_subject_str == current_comm_subject:
                print(f"ℹ️ Subject '{new_comm_subject_str}' already sent (status 3) for record_id={rec['id']}. Maintaining status 3.")
                new_status = 3

            update_communication_status = Queries.UPDATE_RECORD.format(
                table_name=DB_TABLE_NAME,
                set_clause="communication_status",
                where_key="id"
            )
            update_communication_subject = Queries.UPDATE_RECORD.format(
                table_name=DB_TABLE_NAME,
                set_clause="communication_subject",
                where_key="id"
            )
            await conn.fetchrow(update_communication_status, new_status, rec["id"])
            if comm_subject:
                await conn.fetchrow(update_communication_subject, new_comm_subject_str, rec["id"])
            print(f"✔ Updated communication_status={new_status} for ID={rec['id']}")

    return {
        "message": "Image validation completed",
        "missing_views": missing_views_result if missing_views_result is not None else []
    }

@router.post("/webhook-agent-init")
async def webhook_call_agent(
    record_id: int, 
    set_missing_truck_none: bool = False, 
    set_received_documents: bool=False,
    missing_views: list = None,
    attachments: list = None
):
    """
    communication_status
                0 : New Record
                1 : Email required to send
                2 : Email not required to send
                3 : Email Sent
    
    Args:
        record_id: Record ID to process
        set_missing_truck_none: If True, use empty list for missing_views
        set_received_documents: Flag for document handling
        missing_views: List of missing truck views (from webhook_data_validator)
    """
    conn = await get_connection()
    async with httpx.AsyncClient(timeout=60) as client:
        # 1️⃣ Fetch records from DB
        if set_received_documents:
            update_communication_status = Queries.UPDATE_RECORD.format(
                table_name=DB_TABLE_NAME,
                set_clause="communication_status",
                where_key="record_id"
            )
            update_communication_subject = Queries.UPDATE_RECORD.format(
                table_name=DB_TABLE_NAME,
                set_clause="communication_subject",
                where_key="record_id"
            )
            update_docs_received = Queries.UPDATE_RECORD.format(
                table_name=DB_TABLE_NAME,
                set_clause="docs_received",
                where_key="record_id"
            )
            await conn.fetchrow(update_communication_status, 1, int(record_id))
            await conn.fetchrow(update_communication_subject, 'docs_received_status', int(record_id))
            await conn.fetchrow(update_docs_received, 1, int(record_id))
        params = {
            "table_name": DB_TABLE_NAME,
            "where_key": "record_id",
            "where_value": record_id,
            "formatted_output": False,
        }
        resp = await client.get(FETCH_TABLE_URL, params=params)
        if resp.status_code != 200:
            raise HTTPException(status_code=500, detail=f"Fetch failed: {resp.text}")

        records = resp.json().get("data", [])
        print("Records fetched:", len(records))
        if not records:
            return {"message": "No records found needing pictures."}

        processed = []
        if ENABLE_HARDCODINGS:
            sample_records = int(SAMPLE_TEST_RECORDS)
        else:
            sample_records = len(records)

        for rec in records[:sample_records]:
            # Use missing_views from webhook_data_validator
            # If set_missing_truck_none is True, use empty list, otherwise use the passed missing_views
            if set_missing_truck_none:
                missing_views_for_email = []
            else:
                missing_views_for_email = missing_views if missing_views is not None else []

            # If this communication is about missing lead info, compute exactly which fields are missing
            comm_subject_str = (rec.get("communication_subject") or "") or ""
            missing_lead_fields_text = None
            if "missing_lead_info" in comm_subject_str:
                required_fields = {
                    "first_name": "First Name",
                    "last_name": "Last Name",
                    "seller_email": "Email",
                    "seller_phone": "Phone",
                    "state": "State",
                    "location_city": "City",
                    "year": "Truck Year",
                    "make": "Truck Make",
                    "model": "Truck Model",
                    "engine": "Engine Type",
                    "transmission": "Transmission Type",
                }
                missing_labels = [
                    label for field, label in required_fields.items() if not rec.get(field)
                ]
                if missing_labels:
                    missing_lead_fields_text = ", ".join(missing_labels)

            # Instantiating TruckEmailData (core payload for email service)
            try:
                truck_mail_item = TruckEmailData(
                    RECORD_ID=rec["record_id"],
                    TRUCK_ID=rec["vin"],
                    FNAME=(rec.get("first_name") or rec.get("last_name") or "").strip(),
                    EMAIL=rec["seller_email"],
                    YEAR=str(rec.get("year") or "").strip(),
                    MAKE=rec["make"],
                    MODEL=rec["model"],
                    CITY=rec["location_city"],
                    STATUS=rec["status"],
                    NOTES=rec["truck_condition_notes"],
                    MISSING_VIEWS=missing_views_for_email,
                    MISSING_LEAD_FIELDS_TEXT=missing_lead_fields_text,
                    ATTACHMENTS=attachments,
                    TRANSPORTATION=rec.get("transportation"),
                )
            except Exception as e:
                print(f"⚠️ Skipping record due to error: {e}")
                continue

            if rec.get("status") == "Offer Made":
                # Force communication_subject to "offer_made"
                print("Forced communication_subject to offer_made")
                update_comm_subject = Queries.UPDATE_RECORD.format(
                    table_name=DB_TABLE_NAME,
                    set_clause="communication_subject",
                    where_key="record_id"
                )
                await conn.fetchrow(update_comm_subject, "offer_made", int(record_id))

                # Set communication_status to 1 (email required)
                update_comm_status = Queries.UPDATE_RECORD.format(
                    table_name=DB_TABLE_NAME,
                    set_clause="communication_status",
                    where_key="record_id"
                )
                await conn.fetchrow(update_comm_status, 1, int(record_id))
                rec['communication_status'] = 1
                rec['communication_subject'] = "offer_made"
            
            elif rec.get("status") == "Offer Made" and str(rec.get("communication_status")) == "3" and rec.get("communication_subject") == "offer_made":
                print(f"✅ 'Offer Made' email already sent for record_id={record_id}. Skipping duplicate.")
                continue

            elif rec.get("status") == "Complete":
                # Check if we already sent the completion email in this or a previous pass
                if rec.get("communication_subject") == "complete_status" and str(rec.get("communication_status")) == "3":
                    print(f"✅ 'Complete' email already sent for record_id={record_id}. Skipping duplicate.")
                    continue
                    
                # Force communication_subject to "complete_status"
                print("Forced communication_subject to complete_status")
                update_comm_subject = Queries.UPDATE_RECORD.format(
                    table_name=DB_TABLE_NAME,
                    set_clause="communication_subject",
                    where_key="record_id"
                )
                await conn.fetchrow(update_comm_subject, "complete_status", int(record_id))

                # Set communication_status to 1 (email required)
                update_comm_status = Queries.UPDATE_RECORD.format(
                    table_name=DB_TABLE_NAME,
                    set_clause="communication_status",
                    where_key="record_id"
                )
                await conn.fetchrow(update_comm_status, 1, int(record_id))
                rec['communication_status'] = 1
                rec['communication_subject'] = "complete_status"
            
            elif rec.get("status") == "Offer Accepted":
                comm_subject_oa = (rec.get("communication_subject") or "")
                if comm_subject_oa == "post_acceptance_documents":
                    # ✅ BOS pipeline triggered this — Dale has attached "Seller Bill of Sale NEW".
                    # communication_subject and communication_status=1 were already set by
                    # handle_inbound_email_task. Let the email flow through normally.
                    print(f"✅ 'Offer Accepted' + 'post_acceptance_documents' for record_id={record_id}: BOS pipeline active, allowing document request email.")
                    rec['communication_status'] = 1
                else:
                    # ⏭️ Status is "Offer Accepted" but no BOS document yet.
                    # Block any accidental email send — we are only waiting for Dale's BOS attachment.
                    update_comm_status = Queries.UPDATE_RECORD.format(
                        table_name=DB_TABLE_NAME,
                        set_clause="communication_status",
                        where_key="record_id"
                    )
                    await conn.fetchrow(update_comm_status, 2, int(record_id))
                    rec['communication_status'] = 2
                    print(f"⏭️  'Offer Accepted' for record_id={record_id}: no email sent. Awaiting BOS attachment from Dale.")

            if rec["communication_status"] == "1" or rec["communication_status"] == 1:
                # 🛑 Check missing_truck_email_count to break potential endless loops
                comm_subject_str = (rec.get("communication_subject") or "")
                missing_truck_count = int(rec.get("missing_truck_email_count") or 0)

                if "missing_truck" in comm_subject_str and missing_truck_count >= 2:
                    print(f"⚠️ Record {rec['record_id']}: missing_truck_email_count={missing_truck_count} >= 2. Forwarding to Dale instead of seller.")
                    
                    # 1. Accumulate all images from local storage
                    accumulated_attachments = []
                    try:
                        storage_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "storage", "images", str(rec["record_id"]))
                        if os.path.exists(storage_path):
                            for filename in os.listdir(storage_path):
                                file_path = os.path.join(storage_path, filename)
                                if os.path.isfile(file_path):
                                    with open(file_path, "rb") as f:
                                        content = f.read()
                                    content_type, _ = mimetypes.guess_type(file_path)
                                    accumulated_attachments.append({
                                        "filename": filename,
                                        "content": content,
                                        "content_type": content_type or "application/octet-stream"
                                    })
                        print(f"📦 Accumulated {len(accumulated_attachments)} photos from local storage for REF ID: {rec['record_id']}")
                    except Exception as e:
                        print(f"⚠️ Failed to read accumulated photos: {e}")

                    # 1. Forward to Dale
                    record_url = f"https://{os.getenv('QB_REALM')}/db/{os.getenv('QB_TABLE_ID')}?a=dr&rid={rec['record_id']}"
                    alert_message = (
                        f"The seller has been emailed 2 times regarding missing truck photos for REF ID: {rec['record_id']}, "
                        f"but our system still identifies missing views. This may be due to misclassification by the AI.\n\n"
                        f"Please manually review all accumulated images attached to this email and upload them to the correct fields in Quickbase.\n\n"
                        f"**Action Required:** Please manually turn the status to 'Complete' after reviewing/uploading these photos to break the automated loop.\n\n"
                        f"Quickbase Record: {record_url}"
                    )
                    await send_alert_to_dale(
                        record_id=int(rec["record_id"]),
                        alert_type="manual_intervention",
                        message=alert_message,
                        subject=f"Manual Intervention Required: Missing Truck Loop - REF ID: {rec['record_id']}",
                        attachments=accumulated_attachments if accumulated_attachments else attachments
                    )
                    
                    # 2. Update status to 2 (Email not required) to stop the loop
                    update_comm_status = Queries.UPDATE_RECORD.format(
                        table_name=DB_TABLE_NAME,
                        set_clause="communication_status",
                        where_key="id"
                    )
                    await conn.fetchrow(update_comm_status, 2, rec["id"])
                    
                    # 3. Clear communication_subject to prevent redundant triggers
                    update_comm_subject = Queries.UPDATE_RECORD.format(
                        table_name=DB_TABLE_NAME,
                        set_clause="communication_subject",
                        where_key="id"
                    )
                    await conn.fetchrow(update_comm_subject, "", rec["id"])
                    
                    continue

                # if rec["documents_received_confirmation"]:
                #     print("SINCE WE ALREADY RECEIVED DOCUMENTS NO NEED TO TRIGGER MAILS ANYMORE")
                #     return {"message": "Emails already sent Successfully !!!"}

                # 1️⃣ Prepare enriched payload for email template rendering
                #    We extend the core TruckEmailData with additional fields that
                #    the Jinja2 templates (e.g. offer_made_template.html) expect.
                truck_mail_payload = truck_mail_item.dict()
                truck_mail_payload.update(
                    {
                        # Explicit seller name fields
                        "first_name": rec.get("first_name"),
                        "last_name": rec.get("last_name"),
                        # Core truck details
                        "year": rec.get("year"),
                        "make": rec.get("make"),
                        "model": rec.get("model"),
                        "vin": rec.get("vin"),
                        # Pricing / offer data
                        "offer_price": rec.get("offer_price"),
                        "transportation": rec.get("transportation"),
                    }
                )

                # Base64 encode any attachments for JSON serialization
                if truck_mail_payload.get("ATTACHMENTS"):
                    for att in truck_mail_payload["ATTACHMENTS"]:
                        if isinstance(att.get("content"), bytes):
                            att["content"] = base64.b64encode(att["content"]).decode("ascii")

                # 2️⃣ Send email via SendGrid
                print("MAIL TRIGGERED BY **Webhook Call Agent**")
                endpoint_output = await client.post(SENDGRID_ENDPOINT, json=truck_mail_payload)
                print(endpoint_output)

                if endpoint_output.status_code == 200:
                    # 2️⃣ Mark communication_status = 3 (Email Sent)
                    query = Queries.UPDATE_RECORD.format(
                        table_name=DB_TABLE_NAME,
                        set_clause="communication_status",
                        where_key="id"
                    )
                    result = await conn.fetchrow(query, 3, rec["id"])

                    # 4️⃣ If this was a missing_truck email, increment the counter
                    if "missing_truck" in comm_subject_str:
                        update_count_query = Queries.UPDATE_RECORD.format(
                            table_name=DB_TABLE_NAME,
                            set_clause="missing_truck_email_count",
                            where_key="id"
                        )
                        await conn.fetchrow(update_count_query, missing_truck_count + 1, rec["id"])
                        print(f"📈 Incremented missing_truck_email_count to {missing_truck_count + 1} for ID={rec['id']}")

                    # 5️⃣ Store outbound email in conversation history
                    response_json = endpoint_output.json()
                    data = {
                        "record_id": rec["record_id"],
                        "sender": settings.SENDGRID_FROM_EMAIL,
                        "subject": response_json["subject"],
                        "body": response_json["body"]
                    }
                    await store_conversation_message(data, direction='outbound')
                    #
                    # # 4️⃣ Trigger SMS to notify seller about the email
                    # try:
                    #     sms_payload = {
                    #         "phone_number": rec["seller_phone"],
                    #         "FNAME": rec["last_name"],
                    #         "MAKE": rec["seller_make"],
                    #         "MODEL": rec["model"],
                    #         "CITY": rec["location_city"],
                    #         "STATUS": rec["status"],
                    #     }
                    #     truck_sms_item = SMSRequest(
                    #         RECORD_ID=rec["record_id"],
                    #         PHONE_NUMBER=rec["seller_phone"],
                    #         FNAME=rec["last_name"],
                    #         MAKE=rec["seller_make"],
                    #         MODEL=rec["model"],
                    #         CITY=rec["location_city"],
                    #         STATUS=rec["status"],
                    #     )
                    #     sms_resp = await client.post(SEND_SMS_URL, json=truck_sms_item.dict())
                    #     print(f"SMS response: {sms_resp.status_code} - {sms_resp.text}")
                    # except Exception as sms_err:
                    #     # Don't fail the whole flow if SMS fails; just log it.
                    #     print(f"⚠️ Failed to send SMS notification for record {rec['record_id']}: {sms_err}")
                else:
                    raise HTTPException(status_code=500, detail=f"Sending Mail Failed...")


            elif rec["communication_status"] == "2" or rec["communication_status"] == 2:
                continue

            else:
                if rec["communication_status"] == "0" or rec["communication_status"] == 0:
                    # YET TO IMPLEMENT
                    pass

        return {"message":"Successfully sent emails !!!"}

@router.post("/init")
async def call_agent():
    """
    communication_status
                0 : New Record
                1 : Email required to send
                2 : Email not required to send
                3 : Email Sent
    """
    conn = await get_connection()
    async with httpx.AsyncClient(timeout=60) as client:
        # 1️⃣ Fetch records from DB
        params = {
            "table_name": DB_TABLE_NAME,
            "where_key": "communication_status",
            "where_value": str(1),
            "formatted_output": False,
        }
        resp = await client.get(FETCH_TABLE_URL, params=params)
        if resp.status_code != 200:
            raise HTTPException(status_code=500, detail=f"Fetch failed: {resp.text}")

        records = resp.json().get("data", [])
        print("Records fetched:", len(records))
        if not records:
            return {"message": "No records found needing pictures."}

        processed = []
        if ENABLE_HARDCODINGS:
            sample_records = int(SAMPLE_TEST_RECORDS)
        else:
            sample_records = len(records)

        for rec in records[:sample_records]:
            # Delegate image validation to get missing views for the template
            image_urls, missing_views = await validate_images(rec, GPT_VISION_URL)

            # Instantiating TruckEmailData
            try:
                truck_mail_item = TruckEmailData(
                    RECORD_ID=rec["id"],
                    TRUCK_ID=rec["vin"],
                    FNAME=rec["last_name"],
                    EMAIL=rec["seller_email"],
                    YEAR=str(rec.get("year") or "").strip(),
                    MAKE=rec["make"],
                    MODEL=rec["model"],
                    CITY=rec["location_city"],
                    STATUS=rec["status"],
                    NOTES=rec["truck_condition_notes"],
                    MISSING_VIEWS=missing_views,
                    TRANSPORTATION=rec.get("transportation")
                )
            except Exception as e:
                print(f"⚠️ Skipping record due to error: {e}")
                continue

            # 🛑 Check missing_truck_email_count before sending to break potential endless loops
            comm_subject_str = (rec.get("communication_subject") or "")
            missing_truck_count = int(rec.get("missing_truck_email_count") or 0)

            if "missing_truck" in comm_subject_str and missing_truck_count >= 3:
                print(f"⚠️ Record {rec['id']}: missing_truck_email_count={missing_truck_count} >= 3. Forwarding to Dale.")
                
                # 1. Accumulate all images from local storage
                accumulated_attachments = []
                try:
                    storage_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "storage", "images", str(rec.get("record_id") or rec["id"]))
                    if os.path.exists(storage_path):
                        for filename in os.listdir(storage_path):
                            file_path = os.path.join(storage_path, filename)
                            if os.path.isfile(file_path):
                                with open(file_path, "rb") as f:
                                    content = f.read()
                                content_type, _ = mimetypes.guess_type(file_path)
                                accumulated_attachments.append({
                                    "filename": filename,
                                    "content": content,
                                    "content_type": content_type or "application/octet-stream"
                                })
                    print(f"📦 Accumulated {len(accumulated_attachments)} photos from local storage for REF ID: {rec.get('record_id') or rec['id']}")
                except Exception as e:
                    print(f"⚠️ Failed to read accumulated photos: {e}")

                record_url = f"https://{os.getenv('QB_REALM')}/db/{os.getenv('QB_TABLE_ID')}?a=dr&rid={rec.get('record_id', rec['id'])}"
                alert_message = (
                    f"The seller has been emailed 3 times regarding missing truck photos for REF ID: {rec.get('record_id', rec['id'])}, "
                    f"but our system still identifies missing views.\n\n"
                    f"Please review all accumulated images attached to this email.\n\n"
                    f"Quickbase Record: {record_url}"
                )
                await send_alert_to_dale(
                    record_id=int(rec.get("record_id", rec["id"])),
                    alert_type="manual_intervention",
                    message=alert_message,
                    subject=f"Manual Intervention Required: Missing Truck Loop - REF ID: {rec.get('record_id', rec['id'])}",
                    attachments=accumulated_attachments
                )
                
                update_comm_status = Queries.UPDATE_RECORD.format(
                    table_name=DB_TABLE_NAME,
                    set_clause="communication_status",
                    where_key="id"
                )
                await conn.fetchrow(update_comm_status, 2, rec["id"])
                
                update_comm_subject = Queries.UPDATE_RECORD.format(
                    table_name=DB_TABLE_NAME,
                    set_clause="communication_subject",
                    where_key="id"
                )
                await conn.fetchrow(update_comm_subject, "", rec["id"])
                
                continue

            if rec["communication_status"] == "1" or rec["communication_status"] == 1:
                result = await client.post(SENDGRID_ENDPOINT, json=truck_mail_item.dict())
                print(result)
                # Write to DB
                if result.status_code == 200:
                    # Mark as Email Sent
                    query = Queries.UPDATE_RECORD.format(
                        table_name=DB_TABLE_NAME,
                        set_clause="communication_status",
                        where_key="id"
                    )
                    await conn.fetchrow(query, 3, rec["id"])

                    # Increment missing_truck_email_count if applicable
                    if "missing_truck" in comm_subject_str:
                        update_count_query = Queries.UPDATE_RECORD.format(
                            table_name=DB_TABLE_NAME,
                            set_clause="missing_truck_email_count",
                            where_key="id"
                        )
                        await conn.fetchrow(update_count_query, missing_truck_count + 1, rec["id"])
                else:
                    raise HTTPException(status_code=500, detail=f"Sending Mail Failed: {result.text}")


            elif rec["communication_status"] == "2" or rec["communication_status"] == 2:
                continue

            else:
                if rec["communication_status"] == "0" or rec["communication_status"] == 0:
                    # YET TO IMPLEMENT
                    pass

        return {"message":"Successfully sent emails !!!"}



