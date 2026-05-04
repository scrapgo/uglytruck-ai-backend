import json
import re
import os
from dotenv import load_dotenv
load_dotenv()

from typing import List, Dict
from bs4 import BeautifulSoup
from mailchimp_marketing.api_client import ApiClientError as MarketingAPIClientError
import mailchimp_transactional as MailchimpTransactional
import mailchimp_marketing as MailchimpMarketing
from app.backend.config import settings

import yaml
from pathlib import Path

def load_config(config_path: str = os.getenv("USER_CONFIG_PATH")) -> dict:
    """Load YAML config file."""
    with open(config_path, "r") as f:
        return yaml.safe_load(f)

user_config = load_config()

def test_connection(client: MailchimpMarketing.Client()):
    try:
        response = client.root.get_root()
        return {"status": "success", "response": response}
    except MarketingAPIClientError as e:
        return {"status": "error", "message": e.text}


def update_template(client: MailchimpMarketing.Client(), template_id: str, html_content: str):
    try:
        response = client.templates.update_template(
            template_id,
            {"name": "UglyTruck Email Template", "html": html_content}
        )
        return response
    except MarketingAPIClientError as e:
        raise Exception(f"Template Update Error: {e.text}")


def create_campaign(client: MailchimpMarketing.Client(), reply_to_email: str, template_id: str):
    try:
        campaign = client.campaigns.create({
            "type": "regular",
            "recipients": {"list_id": settings.MAILCHIMP_AUDIENCE_ID},
            "settings": {
                "subject_line": "Your Truck Listing Update",
                "title": "Unsold Truck Outreach",
                "from_name": "Jayakrishnan - Ugly Truck Team",
                "reply_to": reply_to_email,
                "template_id": int(template_id)
            }
        })
        return campaign["id"]
    except MarketingAPIClientError as e:
        raise Exception(f"Create Campaign Error: {e.text}")


def send_test_email_via_mailchimp(client: MailchimpMarketing.Client(), campaign_id: str, email: str):
    try:
        response = client.campaigns.send_test_email(
            campaign_id,
            {"test_emails": [email], "send_type": "html"}
        )
        return response
    except MarketingAPIClientError as e:
        raise Exception(f"Send Test Email Error: {e.text}")

def extract_text_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    # Remove style & script tags
    for tag in soup(["style", "script", "head", "title", "meta"]):
        tag.decompose()

    # Get clean text
    text = soup.get_text(separator="\n")

    # Cleanup whitespace
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)

def dynamic_html_template(data, comm_subject):
    missing_truck_html_template = user_config["templates"][comm_subject]
    template_rel_path = user_config["templates"][comm_subject]
    # print("🔍 CWD:", os.getcwd())
    # print("🔍 REL PATH:", template_rel_path)
    # print("🔍 ABS PATH:", os.path.abspath(template_rel_path))
    # print("🔍 EXISTS?:", os.path.exists(template_rel_path))
    # print("🔍 DIR LIST:", os.listdir(os.getcwd()))
    # print("🔍 DIR LIST /app:", os.listdir("/app"))
    # print("🔍 DIR LIST /app/app:", os.listdir("/app/app"))
    with open(missing_truck_html_template, "r", encoding="utf-8") as f:
        html_template = f.read()

    # html_data = """<html><body>...</body></html>"""
    data_keys = ["TRUCK_ID", "FNAME", "EMAIL", "YEAR", "MAKE", "MODEL", "CITY", "STATUS", "NOTES"]
    personalized_emails = generate_personalized_emails(html_template, data)

    return personalized_emails

import jinja2

def generate_personalized_emails(template_html: str, records: Dict, upload_base_url: str = "https://uglytruck.ai/upload/") -> List[str]:
    """
    Renders the HTML template using Jinja2 with the provided record data.
    
    Args:
        template_html (str): The base HTML email template (supporting Jinja2 and *|TAG|*).
        records (Dict): recipient data dictionary.
        upload_base_url (str): Optional base link for upload/confirm actions.

    Returns:
        str: Personalized HTML string.
    """
    # 1. PRE-PROCESS: Convert old *|TAG|* format to Jinja2 {{ TAG }} format for compatibility
    # This ensures existing templates still work without being fully rewritten as Jinja2.
    tag_pattern = re.compile(r"\*\|([A-Z0-9_]+)\|\*", re.IGNORECASE)
    jinja_template_str = tag_pattern.sub(r"{{ \1 }}", template_html)

    # 2. RENDER with Jinja2
    env = jinja2.Environment()
    template = env.from_string(jinja_template_str)
    
    # We pass the full records dict. It contains FNAME, MAKE, MISSING_VIEWS, etc.
    rendered_html = template.render(**records)

    # 3. POST-PROCESS: Clean up with BeautifulSoup if needed (matching original behavior)
    soup = BeautifulSoup(rendered_html, "html.parser")
    
    return str(soup)

def html_to_dynamic_json(html_content: str, json_keys: list) -> str:
    """
    Converts HTML into a single-line JSON-safe string with {{ $json["KEY"] }} placeholders.
    - Removes newlines and tabs
    - Escapes quotes
    - Replaces placeholders for given json_keys
    """
    # Clean HTML (remove line breaks and tabs)
    html_content = html_content.replace("\n", "").replace("\t", "").strip()

    # Replace known placeholders with dynamic n8n expressions
    for key in json_keys:
        html_content = html_content.replace(f"*|{key}|*", f"{{{{ $json[\"{key}\"] }}}}")

    # Escape quotes for JSON
    html_content = html_content.replace('"', '\\"')

    # Wrap in quotes
    json_ready = f"\"{html_content}\""

    return json_ready


def html_to_json_ready_string(html_content: str) -> str:
    """
    Converts raw HTML to a single-line JSON-safe string:
    - Removes newlines and tabs
    - Keeps escaped double quotes (\")
    - Preserves HTML structure
    """
    # Remove tabs and newlines
    cleaned_html = html_content.replace("\n", "").replace("\t", "").strip()

    # Escape double quotes to make it JSON-safe
    cleaned_html = cleaned_html.replace('"', '\\"')

    # Wrap in quotes for direct JSON use
    json_ready = f"\"{cleaned_html}\""

    return json_ready
