import os
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (
    Mail, Email, To, Cc, ReplyTo
)
from app.backend.config import settings
from app.communication.base import EmailService


class SendGridService(EmailService):
    def __init__(self):
        self.client = SendGridAPIClient(
            api_key=settings.SENDGRID_API_KEY
        )
