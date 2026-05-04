# app/config.py
import os
from dotenv import load_dotenv

load_dotenv()

import yaml
from pathlib import Path

def load_config(config_path: str = os.getenv("USER_CONFIG_PATH")) -> dict:
    """Load YAML config file."""
    with open(config_path, "r") as f:
        return yaml.safe_load(f)

user_config = load_config()

class Settings:
    # OpenAI Credentials
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")  # Default to mini for cost efficiency

    # Sendgrid credentials
    SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
    SENDGRID_FROM_EMAIL = "diane@scrapgo.com"
    SENDGRID_REPLY_TO = "reply@parse.scrapgo.com"
    # Gmail Credentials
    ENABLE_GMAIL_SERVICE = os.getenv("ENABLE_GMAIL_SERVICE")
    GOOGLE_TOKEN_PATH=os.getenv("GOOGLE_TOKEN_PATH")
    GOOGLE_CREDENTIALS_PATH=os.getenv("GOOGLE_CREDENTIALS_PATH")

    # Mailchimp Credentials
    MAILCHIMP_API_KEY = os.getenv("MAILCHIMP_API_KEY")
    MAILCHIMP_SERVER_NAME = os.getenv("MAILCHIMP_SERVER_NAME")
    MAILCHIMP_AUDIENCE_ID = os.getenv("MAILCHIMP_AUDIENCE_ID")
    MAILCHIMP_TEMPLATE_ID = os.getenv("MAILCHIMP_TEMPLATE_ID")
    MAILCHIMP_CAMPAIGN_ID = os.getenv("MAILCHIMP_CAMPAIGN_ID")
    ENABLE_TRANSACTIONAL = os.getenv("ENABLE_MAILCHIMP_TRANSACTIONAL", "False").lower() == "true"

    # Twilio Credentials
    TWILIO_API_KEY = os.getenv("TWILIO_API_KEY")
    TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
    TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
    TWILIO_PHONE_NUMBER = user_config['hardcodings']['phone_numbers']['from']
    ENABLE_TWILIO = os.getenv("ENABLE_TWILIO", "False").lower() == "true"

    # DB Credentials
    DB_HOST = os.getenv("DB_HOST")
    DB_NAME = os.getenv("DB_NAME")
    DB_USER = os.getenv("DB_USER")
    DB_PASSWORD = os.getenv("DB_PASSWORD")
    DB_PORT = os.getenv("DB_PORT")

    # QuickBase Credentials
    QB_REALM = os.getenv("QB_REALM")
    QB_USER_TOKEN = os.getenv("QB_USER_TOKEN")
    QB_APP_ID = os.getenv("QB_APP_ID")
    QB_TABLE_ID = os.getenv("QB_TABLE_ID")

    BASE_URL = "https://api.quickbase.com/v1/"
    HEADERS = {
        "QB-Realm-Hostname": QB_REALM,
        "Authorization": f"QB-USER-TOKEN {QB_USER_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    SECRET_KEY = "2d6615d90da67ccd1a5d68b15c492daf13e01eed0305847616ec9dbfa8ade67b"
    ALGORITHM = "HS256"


settings = Settings()
