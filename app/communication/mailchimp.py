from app.communication.base import EmailService, TruckEmailData
from app.utils.mailchimp_utils import dynamic_html_template, update_template, create_campaign, send_test_email_via_mailchimp
from app.backend.config import settings
from app.database.queries import Queries
from app.database.database import get_connection
from pydantic import BaseModel, EmailStr
from typing import Optional, List
import mailchimp_transactional as MailchimpTransactional
import mailchimp_marketing as MailchimpMarketing

class MailchimpService(EmailService):
    def __init__(self):
        """Initialize Mailchimp service with either transactional or marketing client."""
        if settings.ENABLE_TRANSACTIONAL:
            self.client = MailchimpTransactional.Client(settings.MAILCHIMP_API_KEY)
        else:
            self.client = MailchimpMarketing.Client()
            self.client.set_config({
                "api_key": settings.MAILCHIMP_API_KEY,
                "server": settings.MAILCHIMP_SERVER_NAME
            })

