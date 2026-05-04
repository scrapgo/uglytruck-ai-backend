import httpx
import os
import logging
from app.utils.sendgrid_utils import send_sendgrid_email
from app.communication.sendgrid import SendGridService
from app.database.database import get_connection
from dotenv import load_dotenv
load_dotenv()

DB_TABLE_NAME = os.environ.get("DB_TABLE_NAME")
DB_CONVERSATION_TABLE = os.environ.get("DB_CONVERSATION_TABLE")
QB_REALM = os.getenv("QB_REALM")   # e.g. demo.quickbase.com
QB_USER_TOKEN = os.getenv("QB_USER_TOKEN")
QB_TABLE_ID = os.getenv("QB_TABLE_ID")      # e.g. bxyz123abc
QB_STATUS_FIELD_ID = 50                     # 🔴 Replace with real field ID
QB_KEY_FIELD_ID = 3                         # 🔴 record_id field ID

import yaml
from pathlib import Path


def load_config(config_path: str = os.getenv("USER_CONFIG_PATH")) -> dict:
    """Load YAML config file."""
    with open(config_path, "r") as f:
        return yaml.safe_load(f)

user_config = load_config()

DALE_EMAIL = user_config["hardcodings"]["emails"]["to"]
CC_EMAIL = user_config["hardcodings"]["emails"]["cc"]

async def fetch_record_details(record_id: int) -> dict:
    """Fetch complete record details from database."""
    query = f"""
        SELECT 
            last_name,
            seller_phone,
            seller_email,
            location_city,
            state,
            year,
            make,
            model,
            engine,
            transmission
        FROM public.{DB_TABLE_NAME}
        WHERE record_id = $1
    """
    conn = await get_connection()
    row = await conn.fetchrow(query, record_id)
    return dict(row) if row else {}

async def send_alert_to_dale(
        record_id: int,
        alert_type: str,
        message: str,
        subject: str = None,
        attachments: list = None
):
    """
    Send alert to Dale via email.

    Args:
        record_id: Record ID triggering the alert
        alert_type: "complete", "offer_rejected", etc.
        message: Alert message body
        subject: Optional custom subject
    """
    if not subject:
        subject = f"Alert: {alert_type.replace('_', ' ').title()} - REF ID: {record_id}"

    # For "complete" or "manual_intervention" alerts, fetch and include all record details
    if alert_type in ["complete", "manual_intervention"]:
        try:
            record = await fetch_record_details(record_id)
            
            # Construct Quickbase photo URLs
            from app.validators.image_validation import PHOTO_FIELD_MAP
            from app.backend.config import settings
            
            photo_urls = []
            photo_labels = {
                "photo_exterior_driver_side": "Side View (Driver)",
                "photo_engine_driver_side": "Engine (Driver Side)",
                "photo_cab": "Cab Interior",
                "photo_engine_passenger_side": "Engine (Passenger Side)",
                "photo_exterior_front": "Front View",
                "photo_exterior_passenger_side": "Side View (Passenger)",
            }
            
            # Construct Quickbase record view URL
            record_url = f"https://{settings.QB_REALM}/db/{settings.QB_TABLE_ID}?a=dr&rid={record_id}"
            
            for photo_key, field_id in PHOTO_FIELD_MAP.items():
                # Quickbase web URL format for viewing specific field/file
                photo_url = f"https://{settings.QB_REALM}/db/{settings.QB_TABLE_ID}?a=dr&rid={record_id}&fid={field_id}"
                label = photo_labels.get(photo_key, photo_key.replace("_", " ").title())
                photo_urls.append(f'<li><a href="{photo_url}" target="_blank">{label}</a></li>')
            
            # Format truck details
            truck_info = f"{record.get('year', 'N/A')} {record.get('make', 'N/A')} {record.get('model', 'N/A')}"
            if truck_info.strip() == "N/A N/A N/A":
                truck_info = "Not specified"
            
            # Build comprehensive HTML email
            html_content = f"""
            <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                    .container {{ max-width: 800px; margin: 0 auto; padding: 20px; }}
                    .header {{ background-color: #4CAF50; color: white; padding: 15px; border-radius: 5px 5px 0 0; }}
                    .content {{ background-color: #f9f9f9; padding: 20px; border: 1px solid #ddd; }}
                    .section {{ margin-bottom: 20px; }}
                    .section h3 {{ color: #4CAF50; border-bottom: 2px solid #4CAF50; padding-bottom: 5px; }}
                    .info-row {{ margin: 10px 0; }}
                    .info-label {{ font-weight: bold; color: #555; display: inline-block; width: 150px; }}
                    .info-value {{ color: #333; }}
                    .photos {{ list-style: none; padding: 0; }}
                    .photos li {{ margin: 5px 0; }}
                    .photos a {{ color: #4CAF50; text-decoration: none; }}
                    .photos a:hover {{ text-decoration: underline; }}
                    .footer {{ background-color: #333; color: white; padding: 15px; text-align: center; border-radius: 0 0 5px 5px; margin-top: 20px; }}
                    .action-note {{ background-color: #fff3cd; border-left: 4px solid #ffc107; padding: 15px; margin: 20px 0; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h2>{ '🚛 Lead Complete' if alert_type == 'complete' else '⚠️ Manual Intervention Required' }</h2>
                        <p><strong>REF ID:</strong> {record_id}</p>
                    </div>
                    
                    <div class="content">
                        <div class="action-note">
                            <strong>📋 Action Required:</strong> {message}
                        </div>
                        
                        <div class="section">
                            <h3>👤 Seller Information</h3>
                            <div class="info-row">
                                <span class="info-label">Last Name:</span>
                                <span class="info-value">{record.get('last_name', 'N/A')}</span>
                            </div>
                            <div class="info-row">
                                <span class="info-label">Email:</span>
                                <span class="info-value">{record.get('seller_email', 'N/A')}</span>
                            </div>
                            <div class="info-row">
                                <span class="info-label">Phone:</span>
                                <span class="info-value">{record.get('seller_phone', 'N/A')}</span>
                            </div>
                            <div class="info-row">
                                <span class="info-label">City:</span>
                                <span class="info-value">{record.get('location_city', 'N/A')}</span>
                            </div>
                            <div class="info-row">
                                <span class="info-label">State:</span>
                                <span class="info-value">{record.get('state', 'N/A')}</span>
                            </div>
                        </div>
                        
                        <div class="section">
                            <h3>🚚 Truck Details</h3>
                            <div class="info-row">
                                <span class="info-label">Year, Make & Model:</span>
                                <span class="info-value">{truck_info}</span>
                            </div>
                            <div class="info-row">
                                <span class="info-label">Engine Type:</span>
                                <span class="info-value">{record.get('engine', 'N/A')}</span>
                            </div>
                            <div class="info-row">
                                <span class="info-label">Transmission Type:</span>
                                <span class="info-value">{record.get('transmission', 'N/A')}</span>
                            </div>
                        </div>
                        
                        <div class="section">
                            <h3>📸 Truck View Pictures</h3>
                            <p>The following truck photos have been uploaded and are available for review:</p>
                            <ul class="photos">
                                {''.join(photo_urls) if photo_urls else '<li>No photos available</li>'}
                            </ul>
                            <p><em>Click on any photo link above to view the image in Quickbase.</em></p>
                            <p><strong><a href="{record_url}" target="_blank">View Full Record in Quickbase</a></strong></p>
                        </div>
                        
                        <div class="action-note">
                            <strong>💼 Next Steps:</strong> { 'Please review all the information above and proceed with pricing negotiations.' if alert_type == 'complete' else 'Please manually review the images and update Quickbase fields to break the automated loop.' }
                        </div>
                    </div>
                    
                    <div class="footer">
                        <p>This is an automated alert from the Ugly Truck AI Automation System</p>
                    </div>
                </div>
            </body>
            </html>
            """
        except Exception as e:
            logging.exception(f"Error fetching record details for alert: {e}")
            # Fallback to simple message if details fetch fails
            html_content = f"""
            <h2>Alert: {alert_type.replace('_', ' ').title()}</h2>
            <p><strong>Record ID:</strong> {record_id}</p>
            <p>{message}</p>
            <p><em>Note: Could not fetch complete record details. Please check Quickbase for full information.</em></p>
            """
    elif alert_type == "offer_accepted":
        html_content = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 800px; margin: 0 auto; padding: 20px; }}
                .header {{ background-color: #4CAF50; color: white; padding: 15px; border-radius: 5px 5px 0 0; }}
                .content {{ background-color: #f9f9f9; padding: 20px; border: 1px solid #ddd; }}
                .section {{ margin-bottom: 20px; }}
                .section h3 {{ color: #4CAF50; border-bottom: 2px solid #4CAF50; padding-bottom: 5px; }}
                .info-row {{ margin: 10px 0; }}
                .info-label {{ font-weight: bold; color: #555; display: inline-block; width: 150px; }}
                .info-value {{ color: #333; }}
                .photos {{ list-style: none; padding: 0; }}
                .photos li {{ margin: 5px 0; }}
                .photos a {{ color: #4CAF50; text-decoration: none; }}
                .photos a:hover {{ text-decoration: underline; }}
                .footer {{ background-color: #333; color: white; padding: 15px; text-align: center; border-radius: 0 0 5px 5px; margin-top: 20px; }}
                .action-note {{ background-color: #fff3cd; border-left: 4px solid #ffc107; padding: 15px; margin: 20px 0; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2>Alert: {alert_type.replace('_', ' ').title()}</h2>
                    <p><strong>Record ID:</strong> {record_id}</p>
                </div>
                <div class="content">
                    <div class="section">
                        <p>{message}</p>
                    </div>
                </div>
                <div class="footer">
                    <p>This is an automated alert from the Ugly Truck AI Automation System</p>
                </div>
            </div>
        </body>
        </html>
        """
    else:
        # For other alert types, use simple format
        colour = "red"
        html_content = f"""
        <h2 style="color: {colour};">Alert: {alert_type.replace('_', ' ').title()}</h2>
        <p><strong>Record ID:</strong> {record_id}</p>
        <p>{message}</p>
        """

    sendgrid_service = SendGridService()
    send_sendgrid_email(
        service=sendgrid_service,
        to_email=DALE_EMAIL,
        cc_email=CC_EMAIL,
        subject=subject,
        html_content=html_content,
        attachments=attachments,
    )
    logging.info(f"✅ Alert sent to Dale for record_id={record_id}, alert_type={alert_type}")
    print("Alert sent to Dale !!!")

async def store_conversation_message(data: dict, direction: str):
    query = """
        INSERT INTO email_conversations
        (record_id, sender, subject, body, direction, created_at)
        VALUES ($1, $2, $3, $4, $5, NOW())
    """

    conn = await get_connection()
    await conn.execute(
        query,
        data["record_id"],
        data["sender"],
        data.get("subject"),
        data["body"],
        direction
    )
    # new_body = "\n\n---- MESSAGE REPLY RECEIVED FROM SELLER ----\n\n" + data["body"]
    #
    # query = f"""
    #     UPDATE {DB_TABLE_NAME}
    #     SET conversations = COALESCE(conversations, '') || $2
    #     WHERE record_id = $1;
    #     """
    #
    # conn = await get_connection()
    # await conn.execute(
    #     query,
    #     data["record_id"],  # $1
    #     new_body,  # $2
    # )

async def fetch_messages(record_id: str):
    """
    Fetch all messages (inbound + outbound) for a RECORD_ID
    ordered chronologically.
    """
    query = """
        SELECT
            sender,
            subject,
            body,
            direction,
            created_at
        FROM email_conversations
        WHERE record_id = $1 AND direction = $2
        ORDER BY created_at DESC
        LIMIT 1
    """
    # query = f"""
    # SELECT conversations FROM {DB_TABLE_NAME}
    # WHERE record_id = $1;
    # """
    conn = await get_connection()
    rows = await conn.fetch(query, record_id, "inbound")
    return rows


async def build_conversation(conversation_row: dict) -> str:
    """
    Builds a full email conversation for LLM analysis.
    """
    rows = await fetch_messages(conversation_row["record_id"])
    if not rows:
        return ""

    conversation_blocks = []

    # include historical messages so LLM sees full thread
    for row in rows:
        block = (
            f"From: {row['sender']}\n"
            f"Subject: {row.get('subject') or ''}\n"
            f"Direction: {row['direction']}\n"
            f"At: {row['created_at']}\n\n"
            f"{row['body']}"
        )
        conversation_blocks.append(block)

    # add the newest message if it was not yet persisted in rows
    if not rows or rows[-1]["body"] != conversation_row["body"]:
        block = (
            f"From: {conversation_row['sender']}\n"
            f"Subject: {conversation_row.get('subject') or ''}\n"
            f"Direction: inbound\n"
            f"At: {conversation_row.get('received_at') or ''}\n\n"
            f"{conversation_row['body']}"
        )
        conversation_blocks.append(block)

    return "\n\n---\n\n".join(conversation_blocks)


def decide_status(llm_output: dict, current_status: str | None) -> str:
    suggested = llm_output.get("suggested_status")

    STATUS_ORDER = [
        "New Lead",
        "Need Pics",
        "Complete",
        "Offer Made",  # ← UNCOMMENT
        "Offer Accepted",
        "Offer Declined", # ← ADD THIS
        #"Docs Received",  # ← UNCOMMENT (for post-acceptance)
        "Closed",  # ← UNCOMMENT
    ]

    # Safety: Unknown status
    if suggested not in STATUS_ORDER and suggested != "Unresponsive/Follow Up":
        return current_status or "New Lead"

    # Unresponsive has special rules
    if suggested == "Unresponsive/Follow Up":
        return "Unresponsive/Follow Up"

    # Prevent backward transitions
    if current_status in STATUS_ORDER:
        current_idx = STATUS_ORDER.index(current_status)
        suggested_idx = STATUS_ORDER.index(suggested)

        # Allow forward movement and specific backward movements
        if suggested_idx < current_idx:
            # Allow backward only for re-negotiation: Offer Rejected → Offer Made
            if current_status == "Offer Declined" and suggested == "Offer Made":
                return suggested
            if current_status == "Offer Declined" and suggested == "Offer Accepted":
                return suggested
            return current_status

    return suggested

async def fetch_current_status(record_id: int) -> str | None:
    query = f"SELECT status FROM public.{DB_TABLE_NAME} WHERE record_id = $1"
    conn = await get_connection()
    row = await conn.fetchrow(query, record_id)
    return row["status"] if row else None

async def update_lead_status_pg(record_id: int, new_status: str):
    query = f"""
        UPDATE public.{DB_TABLE_NAME}
        SET
            status = $1::text,
            date_modified = NOW(),
            -- When status becomes 'Complete', mark docs_received flag = 1
            docs_received = CASE
                WHEN $1::text = 'Complete' THEN 1
                ELSE docs_received
            END
        WHERE record_id = $2
    """
    conn = await get_connection()
    await conn.execute(query, new_status, record_id)

    # Send alert to Dale when Complete
    if new_status == "Complete":
        await send_alert_to_dale(
            record_id=record_id,
            alert_type="complete",
            message=f"BOS is required for next steps."
        )
        # Also update Quickbase status, but don't send duplicate alert
        await update_quickbase_status(record_id, new_status, send_alert=False)

async def update_quickbase_status(record_id: int, status: str, send_alert: bool = True):
    """
    Update status in Quickbase.
    
    Args:
        record_id: Record ID to update
        status: New status value
        send_alert: Whether to send alert if status becomes "Complete" (default: True)
                   Set to False if alert is already being sent via update_lead_status_pg
    """
    url = "https://api.quickbase.com/v1/records"

    payload = {
        "to": QB_TABLE_ID,
        "data": [
            {
                str(QB_STATUS_FIELD_ID): {
                    "value": status
                },
                str(QB_KEY_FIELD_ID): {
                    "value": record_id
                }
            }
        ]
    }

    headers = {
        "QB-Realm-Hostname": QB_REALM,
        "Authorization": f"QB-USER-TOKEN {QB_USER_TOKEN}",
        "User-Agent": "UglyTruck-AI/1.0",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload, headers=headers)

    if resp.status_code != 200:
        logging.error(
            f"Quickbase update failed | record_id={record_id} | "
            f"status={status} | response={resp.text}"
        )
        raise Exception("Quickbase status update failed")

    logging.info(
        f"Quickbase status updated | record_id={record_id} | status={status}"
    )
    
    # Send alert to Dale when status becomes "Complete" (only if send_alert is True)
    # This allows callers to control whether alert is sent (e.g., if update_lead_status_pg already sent it)
    if status == "Complete" and send_alert:
        await send_alert_to_dale(
            record_id=record_id,
            alert_type="complete",
            message=f"BOS is required for next steps."
        )


def _rule_based_intent(body: str, attachments_present: bool) -> str:
    """
    Fallback lightweight intent classifier (original heuristic logic).
    """
    text = (body or "").lower()
    has_question = "?" in text or any(
        kw in text
        for kw in ["how", "what", "when", "where", "why", "help", "clarify", "do", "can", "could"]
    )
    photo_keywords = ["photo", "picture", "image", "pic", "upload", "attach", "attachment"]
    has_photo_signal = attachments_present or any(kw in text for kw in photo_keywords)

    if has_photo_signal and has_question:
        return "mixed"
    if has_photo_signal:
        return "photos"
    if has_question:
        return "question"
    return "other"