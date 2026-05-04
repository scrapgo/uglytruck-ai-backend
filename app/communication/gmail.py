from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from app.backend.config import settings
from email.mime.text import MIMEText
import base64
import os
from app.backend.config import settings
from app.communication.base import EmailService
from app.database.database import get_connection

class GmailService(EmailService):
    def __init__(self, database=None):
        if settings.ENABLE_GMAIL_SERVICE:
            self.creds = Credentials.from_authorized_user_file(settings.GOOGLE_TOKEN_PATH,
                                                          ["https://www.googleapis.com/auth/gmail.send"])
            self.service = build('gmail', 'v1', credentials=self.creds)
        else:
            self.service = build('gmail', 'v1', credentials=None)
