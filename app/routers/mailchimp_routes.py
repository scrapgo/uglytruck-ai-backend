import os
import json
from fastapi import APIRouter, HTTPException
#from .truck_model import TruckEmailData
#from .mailchimp_service import MailchimpService
from app.utils.mailchimp_utils import dynamic_html_template, update_template, create_campaign, send_test_email_via_mailchimp
from app.backend.config import settings
import mailchimp_transactional as MailchimpTransactional
import mailchimp_marketing as MailchimpMarketing

router = APIRouter()

from pydantic import BaseModel, EmailStr
from typing import Optional


class TruckEmailData(BaseModel):
    TRUCK_ID: Optional[str]
    FNAME: str
    EMAIL: EmailStr
    MAKE: str
    MODEL: Optional[str]
    CITY: Optional[str]
    STATUS: str
    NOTES: Optional[str]


class MailchimpService:
    def __init__(self):
        if settings.ENABLE_TRANSACTIONAL:
            self.client = MailchimpTransactional.Client(settings.MAILCHIMP_API_KEY)
        else:
            self.client = MailchimpMarketing.Client()
            self.client.set_config({
                "api_key": settings.MAILCHIMP_API_KEY,
                "server": settings.MAILCHIMP_SERVER_NAME
            })

@router.post("/send-test")
def send_test_email(data: TruckEmailData):
    service = MailchimpService()

    # Step 1: Generate Personalized HTML
    html_content = dynamic_html_template([data.dict()])[0]

    # Step 2: Update Mailchimp Template
    update_template(service.client, settings.MAILCHIMP_TEMPLATE_ID, str(html_content))

    # Step 3: Create/Reuse Campaign
    campaign_id = settings.MAILCHIMP_CAMPAIGN_ID
    if not campaign_id or campaign_id == "None":
        campaign_id = create_campaign(service.client, data.EMAIL, settings.MAILCHIMP_TEMPLATE_ID)

    # Step 4: Send Test Email
    try:
        result = send_test_email_via_mailchimp(service.client, campaign_id, data.EMAIL)
        return {"status": "success", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
