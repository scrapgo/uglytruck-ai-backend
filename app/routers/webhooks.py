import os
import time
import json
import html
import asyncpg
import logging
import re
import httpx
from datetime import datetime
from email.parser import BytesParser
from email import message_from_string
from email.policy import default
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from dotenv import load_dotenv

from app.backend.config import settings

from app.models.llm import (
    analyse_conversation,
    generate_email_reply,
    extract_lead_info_from_email,
    call_llm,
    classify_intent
)
from app.utils.sendgrid_utils import (
    extract_record_id,
    normalize_body,
    strip_quoted_text,
    extract_latest_reply,
    strip_signature,
    extract_text_from_mime,
    extract_attachments,      # ← add this
    send_sendgrid_email,
)
from app.utils.smart_image_classifier import SmartImageClassifier
from app.routers.agent import webhook_data_validator, webhook_call_agent
from app.database.database import fetch_record_by_id
from app.quickbase.webhook_operations import (
    upsert_record,
    update_record,
    delete_record,
    preprocess_values,
    upload_file_to_quickbase,
    FIELD_ID_SEQUENCE,  # kept for compatibility elsewhere
    update_lead_info_in_quickbase,
)
from app.communication.conversation_repository import (
    fetch_current_status,
    store_conversation_message,
    build_conversation,
    update_lead_status_pg,
    update_quickbase_status,
    _rule_based_intent
)
from app.communication.sendgrid import SendGridService

from app.database.queries import Queries
from app.validators.image_validation import PHOTO_FIELD_MAP

load_dotenv()
DB_TABLE_NAME = os.environ.get("DB_TABLE_NAME")

router = APIRouter()

import yaml
def load_config(config_path: str = os.getenv("USER_CONFIG_PATH")) -> dict:
    """Load YAML config file."""
    with open(config_path, "r") as f:
        return yaml.safe_load(f)

user_config = load_config()
DALE_EMAIL = user_config['hardcodings']['emails']['to'] # Or DALE_EMAIL
internal_cc = user_config['hardcodings']['emails']['cc']  # Includes others like kaitlin or me for testing usecases

async def send_outbound_reply(to_email: str, subject: str, body: str):
    """
    Send an outbound email via SendGrid.
    """
    if not to_email or not body:
        return

    sendgrid_service = SendGridService()
    send_sendgrid_email(
        service=sendgrid_service,
        to_email=to_email,
        cc_email=None,
        subject=subject,
        html_content=body,
    )

async def get_connection(database=None):
    return await asyncpg.connect(
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        host=settings.DB_HOST,
        database=database or settings.DB_NAME
    )

@router.post("/quickbase")
async def webhook(request: Request):
    try:
        body = await request.body()
        headers = request.headers

        print("===== HEADERS =====")
        print(headers)

        print("===== RAW BODY =====")
        print(body)
        payload = await request.json()
    except Exception as e:
        print(str(e))
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = payload.get("event")
    record_id = payload.get("record_id")
    values = payload.get("values", {})

    # Connect to DB
    conn = await get_connection()

    if event == "Add":
        logging.info(f"[EMAIL-FLOW][Add] ▶ Received Add event for record_id={record_id}")
        # Pre-processing
        clean_values = preprocess_values(values)

        # Check if photos are missing from webhook payload; if so, fetch directly from Quickbase (including website photo URLs)
        from app.validators.image_validation import PHOTO_FIELD_MAP
        has_any_photos = any(clean_values.get(k) for k in PHOTO_FIELD_MAP)
        if not has_any_photos:
            from app.quickbase.webhook_operations import get_record_photo_status
            logging.info(f"[EMAIL-FLOW][Add] 🔍 No photos in webhook payload. Checking Quickbase photo and website fields for record_id={record_id}...")
            try:
                qb_photos = await get_record_photo_status(int(record_id))
                if qb_photos:
                    for k, v in qb_photos.items():
                        if v and not clean_values.get(k):
                            clean_values[k] = v
                    logging.info(f"[EMAIL-FLOW][Add] 📸 Retrieved photo values from Quickbase for record_id={record_id}: {[k for k, v in qb_photos.items() if v]}")
            except Exception as e:
                logging.warning(f"[EMAIL-FLOW][Add] ⚠️ Could not fetch photos from Quickbase for record_id={record_id}: {e}")

        logging.info(f"[EMAIL-FLOW][Add] Preprocessed values for record_id={record_id}: status={clean_values.get('status')}, email={clean_values.get('seller_email')}, phone={clean_values.get('seller_phone')}")
        await upsert_record(conn, record_id, clean_values)
        logging.info(f"[EMAIL-FLOW][Add] DB upsert complete for record_id={record_id}")

        # Always reset communication state so previously-processed records
        # (status=3) are treated as fresh and the full email flow runs again.
        await conn.execute(
            f'UPDATE "{DB_TABLE_NAME}" SET communication_status = 0, communication_subject = NULL WHERE record_id = $1',
            int(record_id)
        )
        logging.info(f"[EMAIL-FLOW][Add] ♻️  Reset communication_status=0 and communication_subject=NULL for record_id={record_id}")

        incoming_status = clean_values.get('status')
        if incoming_status in ["New Lead", "Need Pics"]:
            logging.info(f"[EMAIL-FLOW][Add] Status='{incoming_status}' → calling webhook_data_validator for record_id={record_id}")
            validator_result = await webhook_data_validator(record_id)
            missing_views = validator_result.get("missing_views", []) if validator_result else []
            logging.info(f"[EMAIL-FLOW][Add] Validator returned missing_views={missing_views} for record_id={record_id}")
            logging.info(f"[EMAIL-FLOW][Add] 📧 Calling webhook_call_agent for record_id={record_id} with missing_views={missing_views}")
            await webhook_call_agent(record_id, missing_views=missing_views)
            logging.info(f"[EMAIL-FLOW][Add] ✅ webhook_call_agent completed for record_id={record_id}")
        else:
            logging.info(f"[EMAIL-FLOW][Add] Status='{incoming_status}' → calling webhook_call_agent (docs path) for record_id={record_id}")
            await webhook_call_agent(record_id, set_missing_truck_none=True, set_received_documents=True)
            logging.info(f"[EMAIL-FLOW][Add] ✅ webhook_call_agent (docs path) completed for record_id={record_id}")

    elif event == "Replace":
        logging.info(f"[EMAIL-FLOW][Replace] ▶ Received Replace event for record_id={record_id}")
        
        # Fetch old status FIRST to check if this is just a file upload webhook
        old_record = await conn.fetchrow(
            f'SELECT * FROM "{DB_TABLE_NAME}" WHERE record_id = $1', int(record_id)
        )
        old_status = old_record["status"] if old_record else None
        old_email = old_record["seller_email"] if old_record else None
        old_phone = old_record["seller_phone"] if old_record else None
        old_transportation_status = old_record["transportation"] if old_record else None
        logging.info(f"[EMAIL-FLOW][Replace] DB snapshot → status='{old_status}', email='{old_email}', phone='{old_phone}', transportation='{old_transportation_status}' for record_id={record_id}")
        
        clean_values = preprocess_values(values)
        new_status = clean_values.get("status", old_status)
        new_email = clean_values.get("seller_email", old_email)
        new_phone = clean_values.get("seller_phone", old_phone)
        new_transportation_status = clean_values.get("transportation", old_transportation_status)
        # Photo field names that trigger webhooks when files are uploaded via API
        photo_fields_in_webhook = {
            "photo_exterior_driver_side", "photo_engine_driver_side", "photo_cab",
            "photo_engine_passenger_side", "photo_exterior_front",
            "photo_exterior_passenger_side", "photo_exterior_rear",
            "copy_photo_exterior_driver_side", "photos"
        }
        
        # Check if this webhook contains photo field updates
        has_photo_fields = any(
            key in photo_fields_in_webhook and values.get(key) not in (None, "", "null")
            for key in values.keys()
        )
        
        # If status didn't change AND webhook contains photo fields, this is likely
        # a file upload webhook from our inbound email handler (we handle validation manually)
        # Skip processing to avoid duplicate validation runs
        if old_status == new_status and has_photo_fields:
            logging.info(
                f"⏭️  Quickbase webhook triggered by file upload for record_id={record_id} "
                f"(status unchanged: '{old_status}'). Skipping validation - "
                f"handled manually after all uploads complete in inbound webhook."
            )
            # Still update the record with new photo values
            await update_record(
                conn,
                table_name=DB_TABLE_NAME,
                where_key="record_id",
                record_id=record_id,
                values=clean_values
            )
            await conn.close()
            return {"status": "skipped", "reason": "File upload webhook, validation handled manually"}
        
        # Normal processing for status changes or non-photo updates
        await update_record(
            conn,
            table_name=DB_TABLE_NAME,
            where_key="record_id",
            record_id=record_id,
            values=clean_values
        )
        
        logging.info(f"[EMAIL-FLOW][Replace] Incoming new values → status='{new_status}', email='{new_email}', phone='{new_phone}', transportation='{new_transportation_status}' for record_id={record_id}")
        
        if old_status != new_status or old_email != new_email or old_phone != new_phone or old_transportation_status != new_transportation_status:
            if old_status != new_status:
                logging.info(f"[EMAIL-FLOW][Replace] 🔄 Status changed: '{old_status}' → '{new_status}' for record_id={record_id}")
            elif old_email != new_email:
                logging.info(f"[EMAIL-FLOW][Replace] 🔄 Email changed: '{old_email}' → '{new_email}' for record_id={record_id}")
            elif old_phone != new_phone:
                logging.info(f"[EMAIL-FLOW][Replace] 🔄 Phone changed: '{old_phone}' → '{new_phone}' for record_id={record_id}")
            elif old_transportation_status != new_transportation_status:
                logging.info(f"[EMAIL-FLOW][Replace] 🔄 Transportation changed: '{old_transportation_status}' → '{new_transportation_status}' for record_id={record_id}")
            logging.info(f"[EMAIL-FLOW][Replace] ▶ Change detected — routing to email handler for record_id={record_id}, new_status='{new_status}'")
            if new_status in ['New Lead', 'Need Pics']:
                logging.info(f"[EMAIL-FLOW][Replace] 📋 Status='{new_status}' → calling webhook_data_validator for record_id={record_id}")
                validator_result = await webhook_data_validator(record_id)
                missing_views = validator_result.get("missing_views", []) if validator_result else []
                logging.info(f"[EMAIL-FLOW][Replace] Validator returned missing_views={missing_views} for record_id={record_id}")
                logging.info(f"[EMAIL-FLOW][Replace] 📧 Calling webhook_call_agent for record_id={record_id} with missing_views={missing_views}")
                await webhook_call_agent(record_id, missing_views=missing_views)
                logging.info(f"[EMAIL-FLOW][Replace] ✅ webhook_call_agent completed for record_id={record_id}")

            elif new_status == 'Complete':
                logging.info(f"[EMAIL-FLOW][Replace] 🏁 Status='Complete' → running validator (bypass GPT) then sending seller email for record_id={record_id}")
                await webhook_data_validator(record_id, set_missing_truck_none=True)
                logging.info(f"[EMAIL-FLOW][Replace] Validator done (Complete path) for record_id={record_id}. Calling webhook_call_agent.")
                await webhook_call_agent(record_id, set_missing_truck_none=True)
                logging.info(f"[EMAIL-FLOW][Replace] ✅ Complete email flow finished for record_id={record_id}")
            
            elif new_status == 'Offer Made':
                logging.info(f"[EMAIL-FLOW][Replace] 💰 Status='Offer Made' → sending offer email to seller for record_id={record_id}")
                await webhook_call_agent(record_id, set_missing_truck_none=True)
                logging.info(f"[EMAIL-FLOW][Replace] ✅ Offer Made email dispatched for record_id={record_id}")

            elif new_status == 'Offer Accepted':
                logging.info(f"[EMAIL-FLOW][Replace] 🤝 Status='Offer Accepted' → alerting Dale (no seller email until BOS received) for record_id={record_id}")
                # ── No email sent to seller on manual status change ─────────────────────
                # The post_acceptance_documents email is ONLY triggered when Dale sends
                # the "Seller Bill of Sale NEW" attachment via /sendgrid/inbound (already
                # handled there). Here we simply alert Dale to do exactly that.
                try:
                    from app.communication.conversation_repository import send_alert_to_dale
                    seller_row_qa = await conn.fetchrow(
                        f'SELECT * FROM "{DB_TABLE_NAME}" WHERE record_id = $1',
                        int(record_id),
                    )
                    if seller_row_qa:
                        seller_name_qa = " ".join(
                            part for part in [seller_row_qa["first_name"], seller_row_qa["last_name"]]
                            if part
                        )
                        offer_price_qa = seller_row_qa.get("offer_price", "N/A")
                        vin_qa = seller_row_qa.get("vin", "N/A")
                        alert_message_qa = f"Offer of ${offer_price_qa} is Accepted by seller {seller_name_qa} (VIN# {vin_qa}). I have requested the documents from the seller. I will forward those documents once received."
                        logging.info(f"[EMAIL-FLOW][Replace] Sending 'Offer Accepted' Dale alert for record_id={record_id}: seller='{seller_name_qa}', offer=${offer_price_qa}, vin={vin_qa}")
                        await send_alert_to_dale(
                            record_id=int(record_id),
                            alert_type="offer_accepted",
                            message=alert_message_qa
                        )
                        logging.info(f"[EMAIL-FLOW][Replace] 📨 Dale alerted (Offer Accepted) for record_id={record_id}. Awaiting BOS attachment.")
                    else:
                        logging.warning(f"[EMAIL-FLOW][Replace] ⚠️ No DB row found for record_id={record_id} when sending Offer Accepted alert")
                except Exception as e:
                    logging.exception(f"[EMAIL-FLOW][Replace] ⚠️ Failed to send Offer Accepted alert to Dale for record_id={record_id}: {e}")

            elif new_status == 'Offer Declined':
                logging.info(f"[EMAIL-FLOW][Replace] ❌ Status='Offer Declined' → alerting Dale for record_id={record_id}")
                # ── Mirror exactly what handle_inbound_email_task does ──────────────────
                try:
                    from app.communication.conversation_repository import send_alert_to_dale
                    seller_row_od = await conn.fetchrow(
                        f'SELECT * FROM "{DB_TABLE_NAME}" WHERE record_id = $1',
                        int(record_id),
                    )
                    if seller_row_od:
                        seller_name_od = " ".join(
                            part for part in [seller_row_od["first_name"], seller_row_od["last_name"]]
                            if part
                        )
                        offer_price_od = seller_row_od["offer_price"]
                        vin_od = seller_row_od.get("vin", "")
                        message_od = f"The offer of ${offer_price_od} has been declined by the seller, {seller_name_od} (VIN# {vin_od}). Further negotiation may be required."
                        logging.info(f"[EMAIL-FLOW][Replace] Sending 'Offer Declined' Dale alert for record_id={record_id}: seller='{seller_name_od}', offer=${offer_price_od}, vin={vin_od}")
                        await send_alert_to_dale(
                            record_id=int(record_id),
                            alert_type="offer_rejected",
                            message=message_od
                        )
                        logging.info(f"[EMAIL-FLOW][Replace] 📨 Dale alerted (Offer Declined) for record_id={record_id}")
                    else:
                        logging.warning(f"[EMAIL-FLOW][Replace] ⚠️ No DB row found for record_id={record_id} when sending Offer Declined alert")
                except Exception as e:
                    logging.exception(f"[EMAIL-FLOW][Replace] ⚠️ Failed to process manual 'Offer Declined' for record_id={record_id}: {e}")
            else:
                logging.info(f"[EMAIL-FLOW][Replace] 📄 Status='{new_status}' → docs path for record_id={record_id}")
                await webhook_call_agent(record_id, set_missing_truck_none=True, set_received_documents=True)
                logging.info(f"[EMAIL-FLOW][Replace] ✅ Docs path agent completed for record_id={record_id}")
            # else:
            #     # Since communication subject is already present, means, its more than first time communication happending hence, disabling validation
            #     print("Since communication subject is already present, means, its more than first time communication happending hence, disabling validation")
            #     print(f"RECORD ID: {record_id} has already been validated")

        else:
            logging.info(
                f"[EMAIL-FLOW][Replace] ⏭️  No actionable change detected for record_id={record_id} — "
                f"status='{new_status}', email='{new_email}', phone='{new_phone}'. Skipping email flow."
            )


    elif event == "Delete":
        await delete_record(conn, record_id, where_key="record_id")

    await conn.close()

    return {"status": f"Successfully !!! {event} records"}


@router.post("/sendgrid/inbound")
async def sendgrid_inbound(request: Request, background_tasks: BackgroundTasks):
    logging.info("[SENDGRID INBOUND] 🚀 Webhook request received from SendGrid")
    try:
        form = await request.form(max_part_size=50 * 1024 * 1024)  # allow ~25MB
        payload = dict(form)

        logging.info(f"SendGrid keys: {list(payload.keys())}")
        logging.info("Payload keys: %s", list(payload.keys()))

        logging.info("===================================")
        logging.info("FORM KEYS: %s", list(form.keys()))
        logging.info("PAYLOAD KEYS: %s", list(payload.keys()))
        logging.info("CONTENT TYPE: %s", request.headers.get("content-type"))
        logging.info("===================================")
        
        raw_email = payload.get("email")
        if not raw_email:
            logging.error("[SENDGRID INBOUND] ❌ Missing raw email field in payload")
            raise ValueError("Missing raw email field")

        logging.info(f"[SENDGRID INBOUND] 📧 Raw email payload extracted (size: {len(raw_email)} bytes)")
        
        # 🔐 Parse RAW MIME safely
        msg = message_from_string(raw_email, policy=default)
        # Extract attachments (images + PDFs, filtered in utils)
        attachments = extract_attachments(msg)
        logging.info(f"Found {len(attachments)} attachments")

        from_email = payload.get("from")
        to_email = payload.get("to")
        subject = payload.get("subject")

        # Extract clean body
        text_body = extract_text_from_mime(msg)

        logging.info(f"Subject: {subject}")
        logging.info(f"Body preview: {text_body[:300]}")

    except Exception as e:
        logging.exception("Inbound parse failed")
        raise HTTPException(status_code=400, detail=str(e))

    # Extract record ID safely
    record_id = extract_record_id(subject)
    logging.info(f"[SENDGRID INBOUND] 🆔 Extracted Record ID: {record_id} from subject: '{subject}'")
    if not record_id:
        logging.warning("[SENDGRID INBOUND] ⚠️ No record ID found. Returning status: ignored")
        return {"status": "ignored", "reason": "No record ID"}

    logging.info(f"[SENDGRID INBOUND] ⏱️ Adding heavy processing to background tasks for record_id={record_id}")
    # Start heavy processing in background to prevent SendGrid timeouts/retries
    background_tasks.add_task(
        handle_inbound_email_task,
        record_id=record_id,
        from_email=from_email,
        to_email=to_email,
        subject=subject,
        text_body=text_body,
        attachments=attachments,
        payload=payload
    )

    return {"status": "accepted", "record_id": record_id}

async def handle_inbound_email_task(record_id, from_email, to_email, subject, text_body, attachments, payload):
    logging.info(f"[SENDGRID TASK] 🚀 Starting background task for record_id={record_id}")
    conn = await get_connection()
    try:
        # 0) Detect Bill of Sale from Dale
        is_from_dale = False
        if isinstance(DALE_EMAIL, list):
            is_from_dale = any(email.lower() in from_email.lower() for email in DALE_EMAIL)
        elif isinstance(DALE_EMAIL, str):
            is_from_dale = DALE_EMAIL.lower() in from_email.lower()
        
        bos_attachments = []  # Collects BOS + Direct Deposit Form (and any other docs Dale sends)
        if is_from_dale:
            # Check if current status is 'Offer Accepted'
            row = await conn.fetchrow(f"SELECT status, transportation FROM {DB_TABLE_NAME} WHERE record_id = $1", int(record_id))
            if row and (row.get('status') or "").lower() == "offer accepted":
                bos_found = False
                for att in attachments:
                    fname = att.get("filename", "").lower()
                    if "seller bill of sale new" in fname:
                        bos_attachments.append(att)
                        bos_found = True
                        logging.info(f"📄 Detected BOS '{att.get('filename')}' from Dale for record_id={record_id}.")
                    elif "direct_deposit" in fname or "direct deposit" in fname:
                        bos_attachments.append(att)
                        logging.info(f"📄 Detected Direct Deposit Form '{att.get('filename')}' from Dale for record_id={record_id}.")
                
                # Only proceed if BOS is present (it's the mandatory trigger)
                if not bos_found:
                    bos_attachments = []

        if bos_attachments:
            logging.info(
                f"📎 Forwarding {len(bos_attachments)} attachment(s) from Dale to seller for record_id={record_id}: "
                f"{[a.get('filename') for a in bos_attachments]}"
            )
            
            # Update communication_subject to request_documents_template (post_acceptance_documents in config)
            update_comm_subject = Queries.UPDATE_RECORD.format(
                table_name=DB_TABLE_NAME,
                set_clause="communication_subject",
                where_key="record_id"
            )
            await conn.fetchrow(update_comm_subject, "post_acceptance_documents", int(record_id))
            
            update_comm_status = Queries.UPDATE_RECORD.format(
                table_name=DB_TABLE_NAME,
                set_clause="communication_status",
                where_key="record_id"
            )
            await conn.fetchrow(update_comm_status, 1, int(record_id))
            
            # Trigger the agent to send the email with all collected attachments
            from app.routers.agent import webhook_data_validator, webhook_call_agent
            await webhook_call_agent(
                record_id=int(record_id),
                set_missing_truck_none=True,
                attachments=bos_attachments
            )
            
            await conn.close()
            return {"status": "success", "reason": f"BOS + {len(bos_attachments)} attachment(s) forwarded to seller"}

        # 1) Separate truck photos vs. document attachments
        # Truck photos still go to Quickbase; documents are forwarded via email to internal CC.
        truck_attachments = []
        document_attachments = []

        for att in attachments:
            filename = (att.get("filename") or "").lower()
            ctype = (att.get("content_type") or "").lower()

            # Heuristic: if filename hints at truck views, treat as truck photo
            photo_keywords = [
                "front", "side", "rear", "exterior",
                "driver", "passenger", "cab", "truck",
            ]
            is_truck_photo = any(k in filename for k in photo_keywords)

            # Heuristic: if filename hints at documents (Signed BOS, License, Title, etc.)
            doc_keywords = ["bos", "bill", "sale", "dl", "license", "licence", "title", "bank", "ach", "routing", "id", "passport"]
            is_doc_item = any(k in filename for k in doc_keywords)

            # PDFs are much more likely to be documents
            is_pdf = ctype == "application/pdf" or filename.endswith(".pdf")

            if (is_pdf or is_doc_item) and not is_truck_photo:
                document_attachments.append(att)
            elif is_truck_photo:
                truck_attachments.append(att)
            else:
                # Fallback: images without clear photo hints go to truck photos
                truck_attachments.append(att)

        logging.info(
            f"Attachment split for record_id={record_id}: "
            f"{len(truck_attachments)} truck photo(s), "
            f"{len(document_attachments)} document(s)"
        )
    ## Commenting it for version dev_v2.4 onwards in order to prevent updation of status to Complete pre-maturely.
    # if len(truck_attachments) >= 3:
    #     update_pics_received_status = Queries.UPDATE_RECORD.format(
    #         table_name=DB_TABLE_NAME,
    #         set_clause="truck_view_pics_received_confirmation",
    #         where_key="record_id"
    #     )
    #     await conn.fetchrow(update_pics_received_status, 1, record_id)
        if len(document_attachments) > 0:
            update_docs_received_status = Queries.UPDATE_RECORD.format(
                table_name=DB_TABLE_NAME,
                set_clause="documents_received_confirmation",
                where_key="record_id"
            )
            await conn.fetchrow(update_docs_received_status, 1, record_id)
        # 2) Upload truck photo attachments to Quickbase, filling ONLY empty photo fields
        # We upload all files first, then trigger validation ONCE after all uploads complete

        # 2) Smart image classification and upload
        from app.utils.smart_image_classifier import SmartImageClassifier

        uploaded_any = False
        uploaded_count = 0
        uploaded_attachment_names = []
        classification_results = []

        # Determine which QuickBase photo fields are currently empty
        # SYNC: Fetch latest photo status from Quickbase to be absolutely sure
        from app.quickbase.webhook_operations import get_record_photo_status
        qb_photo_status = {}
        try:
            logging.info(f"🔄 Syncing photo status from Quickbase for record_id={record_id}")
            qb_photo_status = await get_record_photo_status(int(record_id))
            if qb_photo_status:
                logging.info(f"✅ Sync successful. QB Photo Status: {qb_photo_status}")
                # Update local DB with synced info
                for col, val in qb_photo_status.items():
                    if val:
                        await conn.fetchrow(
                            Queries.UPDATE_RECORD.format(table_name=DB_TABLE_NAME, set_clause=f'"{col}"', where_key="record_id"),
                            val, int(record_id)
                        )
        except Exception as sync_err:
            logging.error(f"⚠️ Quickbase photo sync failed: {sync_err}")

        empty_field_ids = []
        try:
            photo_columns = list(PHOTO_FIELD_MAP.keys())
            photo_row = await conn.fetchrow(
                f"SELECT status, {', '.join(photo_columns)} FROM {DB_TABLE_NAME} WHERE record_id = $1",
                record_id,
            )
            current_db_status = photo_row.get("status") if photo_row else None
            
            # Trust Quickbase status if sync worked, otherwise use DB row
            for col, field_id in PHOTO_FIELD_MAP.items():
                if qb_photo_status:
                    # If field is not in qb_photo_status or value is None, it's empty
                    if not qb_photo_status.get(col):
                        empty_field_ids.append(field_id)
                else:
                    # Fallback to local DB if sync failed
                    val = photo_row.get(col) if photo_row else None
                    if val in (None, "", "null"):
                        empty_field_ids.append(field_id)
        except Exception as e:
            logging.exception(f"⚠️ Failed to determine empty photo fields: {e}")
            empty_field_ids = list(PHOTO_FIELD_MAP.values())

        logging.info(f"📋 Empty field IDs for record_id={record_id}: {empty_field_ids}")

        # NEW: Use smart classifier to map images to correct fields
        # Initialize variable to capture missing views for the system note
        current_missing_views = None
        if truck_attachments:
            classifier = SmartImageClassifier()
            classification_results = await classifier.classify_and_map_images(
                truck_attachments,
                empty_field_ids
            )

            # Upload images to their correctly identified fields
            for result in classification_results:
                if not result["should_upload"]:
                    print(f"⏭️  Skipping {result['attachment'].get('filename')}: {result['reason']}")
                    logging.info(
                        f"⏭️  Skipping {result['attachment'].get('filename')}: {result['reason']}"
                    )
                    continue

                attachment = result["attachment"]
                field_id = result["field_id"]
                label = result["label"]
                filename = attachment.get("filename", f"classified_{label}.jpg")

                try:
                    await upload_file_to_quickbase(
                        record_id=int(record_id),
                        field_id=field_id,
                        filename=filename,
                        content=attachment["content"],
                        content_type=attachment["content_type"],
                    )
                    
                    # NEW: Update local Postgres database so subsequent validation sees the file
                    db_col = next((k for k, v in PHOTO_FIELD_MAP.items() if v == field_id), None)
                    if db_col:
                        try:
                            update_db_query = Queries.UPDATE_RECORD.format(
                                table_name=DB_TABLE_NAME,
                                set_clause=f'"{db_col}"',
                                where_key="record_id"
                            )
                            # We just use the filename to indicate it's not empty/null
                            await conn.fetchrow(update_db_query, filename, int(record_id))
                            logging.info(f"💾 Updated local DB column '{db_col}' for record_id={record_id}")
                        except Exception as db_err:
                            logging.warning(f"⚠️ Failed to update local DB for column {db_col}: {db_err}")

                    if current_db_status in ("Need Pics", "New Lead"):
                        uploaded_any = True
                    uploaded_count += 1
                    uploaded_attachment_names.append(filename)
                    print(f"✅ Uploaded {filename} (classified as '{label}') \n",
                        f"to field_id={field_id} for record_id={record_id}")
                    logging.info(
                        f"✅ Uploaded {filename} (classified as '{label}') "
                        f"to field_id={field_id} for record_id={record_id}"
                    )
                except Exception as e:
                    logging.exception(
                        f"❌ Failed to upload {filename} to field_id={field_id}: {e}"
                    )

        # NEW: Save truck photos to local storage for accumulation
        if truck_attachments:
            try:
                storage_base = os.path.join(os.path.dirname(os.path.dirname(__file__)), "storage", "images", str(record_id))
                os.makedirs(storage_base, exist_ok=True)
                for att in truck_attachments:
                    fname = att.get("filename")
                    if not fname:
                        continue
                    # Clean filename to avoid path traversal
                    safe_fname = "".join([c for c in fname if c.isalnum() or c in (".", "_", "-")]).strip()
                    file_path = os.path.join(storage_base, safe_fname)
                    with open(file_path, "wb") as f:
                        f.write(att["content"])
                logging.info(f"💾 Saved {len(truck_attachments)} truck photos to local storage for record_id={record_id}")
            except Exception as e:
                logging.exception(f"⚠️ Failed to save photos to local storage for record_id={record_id}: {e}")

        # 3) Post-upload validation - check if all required views are now present
        # We run this if ANY truck photos were received, even if they weren't uploaded 
        # (e.g. if they already existed, or if the seller is just re-sending confirmation).
        if truck_attachments:
            print(f"🔍 Running post-upload validation for record_id={record_id}")
            logging.info(f"[SENDGRID TASK] 🔍 Running post-upload validation for record_id={record_id} with {len(truck_attachments)} attachments")

            try:
                # Import validation function
                from app.validators.image_validation import validate_images
                from app.routers.agent import GPT_VISION_URL

                # Fetch updated record from DB
                updated_record = await conn.fetchrow(
                    f"SELECT * FROM {DB_TABLE_NAME} WHERE record_id = $1",
                    record_id
                )

                if updated_record:
                    # Convert asyncpg.Record to dict
                    rec_dict = dict(updated_record)

                    # Validate images to identify any remaining missing views
                    image_urls, missing_views = await validate_images(rec_dict, GPT_VISION_URL)
                    # [NEW] Capture for system note
                    current_missing_views = missing_views
                    print(f"📊 Post-upload validation results for record_id={record_id}: ",
                        f"Found {len(image_urls)} images, Missing views: {missing_views}")
                    logging.info(
                        f"📊 Post-upload validation results for record_id={record_id}: "
                        f"Found {len(image_urls)} images, Missing views: {missing_views}"
                    )

                    # If there are still missing views, trigger follow-up email
                    # ONLY if the status is 'New Lead' or 'Need Pics'. 
                    # For 'Offer Made', we skip this to allow intent analysis to handle acceptance/decline.
                    if missing_views and current_db_status in ['New Lead', 'Need Pics']:
                        print(f"📧 Triggering follow-up email for record_id={record_id} ",
                            f"with missing views: {missing_views}")
                        logging.info(
                            f"📧 Triggering follow-up email for record_id={record_id} "
                            f"with missing views: {missing_views}"
                        )

                        # Update communication_subject to request missing views
                        update_comm_subject = Queries.UPDATE_RECORD.format(
                            table_name=DB_TABLE_NAME,
                            set_clause="communication_subject",
                            where_key="record_id"
                        )
                        await conn.fetchrow(update_comm_subject, "missing_truck", int(record_id))

                        # Set communication_status to 1 (email required)
                        update_comm_status = Queries.UPDATE_RECORD.format(
                            table_name=DB_TABLE_NAME,
                            set_clause="communication_status",
                            where_key="record_id"
                        )
                        await conn.fetchrow(update_comm_status, 1, int(record_id))

                        # Trigger the agent to send the email
                        from app.routers.agent import webhook_call_agent
                        await webhook_call_agent(
                            record_id=int(record_id),
                            missing_views=missing_views
                        )
                    elif not missing_views and current_db_status in ['New Lead', 'Need Pics']:
                        print(f"✅ All required views confirmed for record_id={record_id}. "
                            f"Transitioning to 'Complete'.")
                        
                        from app.communication.conversation_repository import update_lead_status_pg
                        await update_lead_status_pg(int(record_id), "Complete")

                        # Sync flags
                        update_status_sub = Queries.UPDATE_RECORD.format(
                            table_name=DB_TABLE_NAME,
                            set_clause="communication_subject",
                            where_key="record_id"
                        )
                        await conn.fetchrow(update_status_sub, "complete_status", int(record_id))
                        
                        update_pics_received_status = Queries.UPDATE_RECORD.format(
                                    table_name=DB_TABLE_NAME,
                                    set_clause="truck_view_pics_received_confirmation",
                                    where_key="record_id"
                                )
                        await conn.fetchrow(update_pics_received_status, 1, record_id)

                        # Trigger completion workflow email
                        from app.routers.agent import webhook_call_agent
                        await webhook_call_agent(
                            record_id=int(record_id),
                            set_missing_truck_none=True
                        )
                    else:
                        logging.info(f"⏭️ Skipping proactive follow-up/completion for record_id={record_id} because status is '{current_db_status}'")
                    
                    # FINAL SYNC: Trust the database above all. 
                    # Ensure current_missing_views doesn't include what we JUST uploaded or what is already in DB
                    if current_missing_views:
                        from app.validators.image_validation import PHOTO_VIEW_LABELS
                        
                        # Re-fetch latest record state from DB
                        latest_row = await conn.fetchrow(f"SELECT * FROM {DB_TABLE_NAME} WHERE record_id = $1", int(record_id))
                        populated_views = []
                        if latest_row:
                            for col, label in PHOTO_VIEW_LABELS.items():
                                if latest_row.get(col) not in (None, "", "null"):
                                    populated_views.append(label)
                        
                        successful_uploads = [res['label'] for res in classification_results if res.get('should_upload')]
                        
                        filtered_missing = [v for v in current_missing_views if v not in populated_views and v not in successful_uploads]
                        current_missing_views = filtered_missing
                        logging.info(f"🎯 Final synced missing views for record_id={record_id}: {current_missing_views}")

            except Exception as e:
                print(f"⚠️ Post-upload validation failed for record_id={record_id}: {e}")
                logging.exception(
                    f"⚠️ Post-upload validation failed for record_id={record_id}: {e}"
                )

    # 4) Forward document attachments (if any) to internal CC email
        # 4) Forward document attachments (if any) to internal CC email
        row = await conn.fetchrow(f"SELECT * FROM {DB_TABLE_NAME} WHERE record_id = $1", record_id)
        if document_attachments and int(row["documents_received_confirmation"]):
            try:
                sendgrid_service = SendGridService()

                doc_names = ", ".join(
                    att.get("filename", "document") for att in document_attachments
                )
                docs_body = (
                    f"Documents received for REF ID: {record_id}\n\n"
                    f"From: {from_email}\n"
                    f"To: {to_email}\n"
                    f"Subject: {subject}\n\n"
                    f"The following document file(s) were attached:\n"
                    f"{doc_names}\n\n"
                    f"Original email body (text version):\n\n"
                    f"{text_body}"
                )
                # Uncommenting it to enable document forwarding to Dale as requested
                send_sendgrid_email(
                    service=sendgrid_service,
                    to_email=DALE_EMAIL,
                    cc_email=internal_cc,
                    subject=f"Documents received - REF ID:{record_id}",
                    html_content=docs_body.replace("\n", "<br>"),
                    attachments=document_attachments,
                )
                print(f"✅ Forwarded {len(document_attachments)} document(s) ",
                    f"to internal CC for record_id={record_id}")
                logging.info(
                    f"✅ Forwarded {len(document_attachments)} document(s) "
                    f"to internal CC for record_id={record_id}"
                )
            except Exception as e:
                logging.exception(
                    f"Failed to forward document attachments for record_id={record_id}: {e}"
                )

        # 5) Normal body / conversation handling
        body = strip_quoted_text(normalize_body(text_body, None))

        if not body:
            # Even if body is empty, we may still have uploaded docs
            logging.info(f"No body for record_id={record_id}, returning early from task.")
            return {"status": "ok", "note": "No body, attachments processed"}

        # Enhance conversation text with attachment information so LLM can "see" attachments in the conversation
        classification_str = ""
        if uploaded_count > 0 and uploaded_attachment_names:

            # 1. Build classification summary from SmartImageClassifier results
            classification_summary_list = []
            for res in classification_results:
                if res.get("should_upload"):
                    fname = res['attachment'].get('filename', 'Unknown')
                    lbl = res.get('label', 'Unclassified')
                    classification_summary_list.append(f"'{fname}' -> {lbl}")

            classification_str = "; ".join(classification_summary_list)

        # 2. Format missing views string
        if current_missing_views is not None:
            missing_str = ", ".join(
                current_missing_views) if current_missing_views else "None - All required photos received"
        else:
            missing_str = "Status unknown (validation skipped)"

        # 3. Construct System Note
        attachment_info = (
            f"\n\n[SYSTEM NOTE - ATTACHMENTS RECEIVED: This email included {uploaded_count} photo/attachment(s) "
            f"that were successfully uploaded to our system.\n"
            f"Classification Results: {classification_str}.\n"
            f"Remaining Missing Views: {missing_str}.\n"
            f"Use this information to inform the user about what was received and what (if anything) is still needed.]\n"
        )

        # Append attachment info to the conversation text so LLM explicitly sees attachments were received
        if body:
            body = body + attachment_info
        else:
            body = attachment_info
        # Also tell LLM about document attachments, if any
        if document_attachments:
            doc_names = ", ".join(
                att.get("filename", "document") for att in document_attachments
            )
            docs_note = (
                f"\n\n[SYSTEM NOTE - DOCUMENTS RECEIVED: "
                f"This latest email included {len(document_attachments)} document file(s). "
                f"Filenames: {doc_names}. "
                f"Use this information to determine whether the seller has provided "
                f"their driver's license, copy of title, and banking details.]\n"
            )
            body = (body or "") + docs_note
            logging.info(
                f"✅ Enhanced conversation text with document info: "
                f"{len(document_attachments)} document file(s) ({doc_names})"
            )

        conversation_row = {
            "record_id": record_id,
            "sender": from_email,
            "subject": subject,
            "body": body,
            "received_at": datetime.utcnow(),
        }

        # Persist inbound message first
        await store_conversation_message(conversation_row, direction="inbound")

        # 5a) If this email is likely a reply to a missing_lead_info request,
        #     use LLM to extract structured lead info and update DB + Quickbase.
        try:
            row_lead = await conn.fetchrow(
                f"SELECT communication_subject, lead_information_received FROM {DB_TABLE_NAME} WHERE record_id = $1",
                record_id,
            )
            comm_subj = (row_lead.get("communication_subject") or "") if row_lead else ""
            lead_info_flag = int(row_lead.get("lead_information_received") or 0) if row_lead else 0

            if ("missing_lead_info" in (comm_subj or "")) and lead_info_flag == 0:
                # Call LLM to extract lead info from the latest email body
                lead_info = await extract_lead_info_from_email(body, provider="openai")

                # Map LLM fields to DB columns
                db_updates = {}
                if lead_info.get("first_name"):
                    db_updates["first_name"] = lead_info["first_name"]
                if lead_info.get("last_name"):
                    db_updates["last_name"] = lead_info["last_name"]
                if lead_info.get("email"):
                    db_updates["seller_email"] = lead_info["email"]
                if lead_info.get("phone"):
                    db_updates["seller_phone"] = lead_info["phone"]
                if lead_info.get("state"):
                    db_updates["state"] = lead_info["state"]
                if lead_info.get("city"):
                    db_updates["location_city"] = lead_info["city"]
                if lead_info.get("year"):
                    # Cast year to int if possible
                    try:
                        db_updates["year"] = int(str(lead_info["year"]).strip())
                    except Exception:
                        db_updates["year"] = lead_info["year"]
                if lead_info.get("make"):
                    db_updates["make"] = lead_info["make"]
                if lead_info.get("model"):
                    db_updates["model"] = lead_info["model"]
                if lead_info.get("engine"):
                    db_updates["engine"] = lead_info["engine"]
                if lead_info.get("transmission"):
                    db_updates["transmission"] = lead_info["transmission"]

                if db_updates:
                    # Update Quickbase
                    await update_lead_info_in_quickbase(int(record_id), lead_info)

                    # If ALL required fields are now present, set lead_information_received = 1
                    update_flag_query = Queries.UPDATE_RECORD.format(
                        table_name=DB_TABLE_NAME,
                        set_clause="lead_information_received",
                        where_key="record_id",
                    )
                    await conn.fetchrow(update_flag_query, 1, record_id)
                    # Update Quickbase status to "Need Pics" when lead info is complete
                    await update_quickbase_status(int(record_id), "Need Pics")
                    logging.info(
                        f"✅ Lead info fully received for record_id={record_id}; "
                        f"lead_information_received set to 1, status updated to 'Need Pics'"
                    )
        except Exception as e:
            print(f"⚠️ Failed to auto-update lead info from email for record_id={record_id}: {e}")
            logging.exception(
                f"⚠️ Failed to auto-update lead info from email for record_id={record_id}: {e}"
            )

        # Build conversation context for LLM and intent routing
        logging.info(f"[SENDGRID TASK] 🧠 Building conversation context and analyzing intent for record_id={record_id}")
        conversation_text = await build_conversation(conversation_row)
        #intent = await classify_intent(body, attachments_present=bool(attachments))
        intent = _rule_based_intent(body, attachments_present=bool(attachments))
        logging.info(f"[SENDGRID TASK] 🎯 Detected intent: '{intent}' for record_id={record_id}")

        # If seller asked a question, generate a reply and send it
        outbound_reply = None
        print(f"🔍 Intent detected as '{intent}' - generating email reply")
        if intent in ("question"):
            logging.info(f"🔍 Intent detected as '{intent}' - generating email reply")
            print("Conversation Text So far:\n\n" + conversation_text)
            conversation_template = user_config["templates"]["conversation"]
            with open(conversation_template, "r", encoding="utf-8") as f:
                html_template = f.read()
            outbound_reply = await generate_email_reply(conversation_text, provider="openai")
            
            if not outbound_reply or not outbound_reply.strip():
                logging.error("❌ generate_email_reply returned empty or None response")
                outbound_reply = "Thank you for your message. We have received your photos and will review them shortly."
            else:
                logging.info(f"✅ Generated reply (length: {len(outbound_reply)} chars): {outbound_reply[:100]}...")
            
            # Format plain text reply as HTML
            # Escape HTML special characters for safety, then convert newlines to HTML
            escaped_reply = html.escape(outbound_reply.strip())
            
            # Split by double newlines to create paragraphs, then handle single newlines within paragraphs
            paragraphs = escaped_reply.split("\n\n")
            formatted_paragraphs = []
            for para in paragraphs:
                if para.strip():  # Skip empty paragraphs
                    # Convert single newlines to <br> within each paragraph
                    para_html = para.replace("\n", "<br>")
                    formatted_paragraphs.append(f"<p>{para_html}</p>")
            
            formatted_reply = "".join(formatted_paragraphs) if formatted_paragraphs else "<p></p>"
            logging.info(f"📝 Formatted reply HTML (length: {len(formatted_reply)}): {formatted_reply[:200]}...")
            
            # Replace the empty container div with the formatted reply
            pattern = r'<div class="container">\s*</div>'
            replacement = f'<div class="container">{formatted_reply}</div>'
            
            html_template = re.sub(pattern, replacement, html_template)
            
            reply_subject = f"Re: {subject}" if subject else "Re: Your truck inquiry"
            logging.info(f"[DEBUG] HTML TEMPLATE preview (first 500 chars): {html_template[:500]}...")
            print("MAIL TRIGGERED BY **LLM**")
            await send_outbound_reply(
                to_email=from_email,
                subject=reply_subject,
                body=html_template,  # html_template is already a string, no need for str()
            )
            await store_conversation_message(
                {
                    "record_id": record_id,
                    "sender": "diane@scrapgo.com",
                    "subject": reply_subject,
                    "body": outbound_reply,
                },
                direction="outbound",
            )
            return
        # Continue existing status analysis so pipeline logic still runs
        # Pass separate counts for truck photos and documents, plus document filenames
        document_filenames = [
            att.get("filename", "document") for att in document_attachments
        ] if document_attachments else []

        logging.info(f"[SENDGRID TASK] 🤖 Sending conversation to LLM for status analysis for record_id={record_id}")
        analysis_result = await analyse_conversation(
            record_id=record_id,
            conversation_text=conversation_text,
            provider="openai",
            truck_photos_count=uploaded_count,  # Count of truck photos uploaded to Quickbase
            documents_count=len(document_attachments),  # Count of document attachments
            document_filenames=document_filenames,  # List of document filenames for LLM analysis
        )
        logging.info(f"[SENDGRID TASK] ✅ LLM analysis completed for record_id={record_id}")

        # ← ADD THIS: Handle offer acceptance/rejection
        print(f"Handing Acceptance/Rejection for Record ID: {record_id}")
        # Extract suggested_status from nested structure: analysis_result["analysis"]["analysis"]["suggested_status"]
        # Or use final_status which is already at the top level
        suggested_status = None
        if analysis_result:
            # Try to get suggested_status from nested LLM analysis
            llm_analysis = analysis_result.get("analysis", {})
            if isinstance(llm_analysis, dict):
                nested_analysis = llm_analysis.get("analysis", {})
                if isinstance(nested_analysis, dict):
                    suggested_status = nested_analysis.get("suggested_status")
            # Fallback to final_status if suggested_status not found
            if not suggested_status:
                suggested_status = analysis_result.get("final_status")
        
        if suggested_status:
            print(f"Suggested Status by Agent for record id {record_id}: {suggested_status}")
            current_status = await fetch_current_status(int(record_id))
            print(f"Current Status for record id {record_id}: {current_status}")

            # Transition to Complete
            if suggested_status == "Complete" and current_status in ["Need Pics", "New Lead", "Manual Intervention"]:
                 print(f"LLM suggests 'Complete' status for record id {record_id}. Updating DB...")
                 from app.communication.conversation_repository import update_lead_status_pg
                 await update_lead_status_pg(int(record_id), "Complete")
                 
                 # Force confirmation flags to 1 as LLM is satisfied
                 update_pics_flag = Queries.UPDATE_RECORD.format(
                     table_name=DB_TABLE_NAME,
                     set_clause="truck_view_pics_received_confirmation",
                     where_key="record_id"
                 )
                 await conn.fetchrow(update_pics_flag, 1, int(record_id))

                 # Trigger completion workflow email to seller
                 from app.routers.agent import webhook_call_agent
                 await webhook_call_agent(
                     record_id=int(record_id),
                     set_missing_truck_none=True
                 )

            # Handle offer acceptance/rejection (current_status == "Offer Made")
            if current_status == "Offer Made":
                if suggested_status in ["Offer Accepted", "Offer Declined"]:
                    # Update status
                    #await update_lead_status_pg(int(record_id), suggested_status)
                    await update_quickbase_status(int(record_id), suggested_status)


                    # If accepted, trigger next workflow
                    if suggested_status == "Offer Accepted":
                        # Set communication_subject to offer_accepted confirmation
                        # This email should say "Offer Accepted, we'll send the BOS soon"
                        update_comm_subject = Queries.UPDATE_RECORD.format(
                            table_name=DB_TABLE_NAME,
                            set_clause="communication_subject",
                            where_key="record_id"
                        )
                        await conn.fetchrow(update_comm_subject, "offer_accepted", int(record_id))
                        
                        update_comm_status = Queries.UPDATE_RECORD.format(
                            table_name=DB_TABLE_NAME,
                            set_clause="communication_status",
                            where_key="record_id"
                        )
                        await conn.fetchrow(update_comm_status, 1, int(record_id))

                        # NOTE: update_quickbase_status("Offer Accepted") above fires a QB Replace
                        #       webhook which hits the 'Offer Accepted' Replace handler and sends
                        #       the Dale alert from there. Do NOT send alert here to avoid duplicates.
            # In sendgrid_inbound(), after document detection:

            if current_status == "Offer Accepted":
                # Check if all required documents received
                if len(document_attachments) >= 2:  # DL + Banking (pickup date is text)
                    # Verify filenames indicate DL and Banking
                    has_dl = any("dl" in f.lower() or "license" in f.lower() or "licence" in f.lower()
                                 for f in document_filenames)
                    has_banking = any("bank" in f.lower() or "ach" in f.lower() or "routing" in f.lower()
                                      for f in document_filenames)

                    if has_dl and has_banking:
                        # Set docs_received flag
                        update_docs = Queries.UPDATE_RECORD.format(
                            table_name=DB_TABLE_NAME,
                            set_clause="docs_received",
                            where_key="record_id"
                        )
                        await conn.fetchrow(update_docs, 1, int(record_id))

            if suggested_status == "Offer Declined":
                await update_quickbase_status(int(record_id), suggested_status)

                        # NOTE: update_quickbase_status("Offer Declined") above fires a QB Replace
                        #       webhook which hits the 'Offer Declined' Replace handler and sends
                        #       the Dale alert from there. Do NOT send alert here to avoid duplicates.


        # Call webhook_data_validator to validate images and update communication_subject/status
        current_status = await fetch_current_status(int(record_id))
        if current_status == 'Need Pics': # IN version dev_v2.4, I am removing New Lead from here
            try:
                rec = await fetch_record_by_id(record_id)
                print(f"Value of truck_view_pics_received_confirmation for record_id {record_id}: ",
                      rec['truck_view_pics_received_confirmation'])
                print(f"Value of lead_information_received for record_id {record_id}: ",rec['lead_information_received'])
                if rec['truck_view_pics_received_confirmation'] and rec['lead_information_received']:
                    set_missing_truck_none = True
                elif not rec['truck_view_pics_received_confirmation'] and rec['lead_information_received']:
                    set_missing_truck_none = False
                else:
                    set_missing_truck_none = True
                # validator_result = await webhook_data_validator(record_id=int(record_id), set_missing_truck_none=set_missing_truck_none)
                # missing_views = validator_result.get("missing_views", []) if validator_result else []
                # await webhook_call_agent(record_id=int(record_id), set_missing_truck_none=set_missing_truck_none, missing_views=missing_views)
            except Exception as e:
                logging.exception(
                    f"⚠️  webhook_data_validator failed for record_id={record_id}: {e}"
                )
                # Don't fail the entire request if validator fails
        # elif current_status in ['Complete']:
        #     try:
        #         from app.routers.agent import webhook_data_validator
        #         validator_result = await webhook_data_validator(record_id=int(record_id), set_missing_truck_none=True)
        #         missing_views = validator_result.get("missing_views", []) if validator_result else []
        #         from app.routers.agent import webhook_call_agent
        #         await webhook_call_agent(record_id=int(record_id), set_missing_truck_none=True, missing_views=missing_views)
        #         logging.info(f"✅ Called webhook_data_validator & web_call_agent for record_id={record_id} with nullifying missing truck")
        #     except Exception as e:
        #         logging.exception(
        #             f"⚠️  webhook_data_validator failed for record_id={record_id}: {e}"
        #         )
        #         # Don't fail the entire request if validator fails
        print(f"Returning from handle_inbound_email_task for record_id={record_id}")
        return {
            "status": "ok",
            "intent": intent,
            "uploaded_attachments": uploaded_any,
            "analysis": analysis_result,
            "replied": bool(outbound_reply),
        }
    except Exception as e:
        logging.exception(f"Error in handle_inbound_email_task: {e}")
    finally:
        await conn.close()
