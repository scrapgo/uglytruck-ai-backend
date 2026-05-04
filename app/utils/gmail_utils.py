from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from email.mime.text import MIMEText
import base64
import os

def send_gmail_email(service, to_email, cc_email, subject, html_content):
    """Send Gmail email to one or more recipients."""
    # Normalize to list
    if isinstance(to_email, str):
        to_list = [to_email]
    else:
        to_list = to_email

    if isinstance(cc_email, str):
        cc_list = [cc_email]
    else:
        cc_list = cc_email
    message = MIMEText(html_content, "html")
    message["to"] = ", ".join(to_list)
    message["cc"] = ", ".join(cc_list)
    message["subject"] = subject
    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
    return service.users().messages().send(userId="me", body={"raw": raw_message}).execute()