from sendgrid.helpers.mail import (
    Mail,
    Email,
    To,
    Cc,
    ReplyTo,
    Header,
    Attachment,
    FileContent,
    FileName,
    FileType,
    Disposition,
)
from app.backend.config import settings
from bs4 import BeautifulSoup
import re
import os
import base64

# Allow images + PDFs and reasonable size
ALLOWED_ATTACHMENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "application/pdf",  # for documents (DL, title, banking details)
}
MAX_ATTACHMENT_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB

import yaml
def load_config(config_path: str = os.getenv("USER_CONFIG_PATH")) -> dict:
    """Load YAML config file."""
    with open(config_path, "r") as f:
        return yaml.safe_load(f)

user_config = load_config()

def extract_text_from_mime(msg):
    """
    Extract best possible human-readable text from MIME email
    """
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = part.get("Content-Disposition", "")

            if ctype == "text/plain" and "attachment" not in disp:
                return part.get_content()

        # fallback to HTML if no text/plain
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                return part.get_content()
    else:
        return msg.get_content()

    return ""

REPLY_SEPARATORS = [
    r"^On .* wrote:",
    r"^From:",
    r"^Sent:",
    r"^To:",
    r"^Subject:",
    r"^-----Original Message-----",
    r"^________________________________",
]

def extract_latest_reply(text: str) -> str:
    """
    Extract only the newest user reply from an email body.
    """
    if not text:
        return ""

    lines = text.splitlines()
    clean_lines = []

    for line in lines:
        # Stop at quoted thread
        if any(re.match(pattern, line.strip(), re.IGNORECASE) for pattern in REPLY_SEPARATORS):
            break

        # Ignore quoted lines
        if line.strip().startswith(">"):
            break

        clean_lines.append(line)

    reply = "\n".join(clean_lines).strip()
    return reply

SIGNATURE_MARKERS = [
    "thanks",
    "regards",
    "best regards",
    "kind regards",
]

def strip_signature(text: str) -> str:
    lines = text.splitlines()
    result = []

    for line in lines:
        if line.strip().lower() in SIGNATURE_MARKERS:
            break
        result.append(line)

    return "\n".join(result).strip()

def extract_record_id(subject: str | None) -> str | None:
    if not subject:
        return None

    match = re.search(r"REF ID:\s*([A-Za-z0-9_-]+)", subject)
    return int(match.group(1)) if match else None


def normalize_body(text: str | None, html: str | None) -> str:
    if text:
        return text.strip()

    if html:
        soup = BeautifulSoup(html, "html.parser")
        return soup.get_text(separator="\n").strip()

    return ""

import re

def strip_quoted_text(body: str) -> str:
    """
    Removes quoted email threads such as:
    - Lines starting after '______'
    - Outlook/Gmail 'From:' reply blocks
    - Legacy 'On <date>, <name> wrote:' blocks
    - Quoted lines starting with '>'
    """

    patterns = [
        r"(?s)\n_{2,}.*$",              # Outlook / Gmail separator
        r"(?s)\nFrom:.*$",              # From: header onward
        r"(?s)\nOn .*? wrote:.*$",      # On <date>, <name> wrote:
        r"(?m)^\s*>.*$",                # Quoted lines starting with >
    ]

    cleaned = body
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned)

    return cleaned.strip()


def send_sendgrid_email(
    service,
    to_email,
    cc_email,
    subject,
    html_content,
    attachments=None,
):
    # Normalize recipients
    to_list = [to_email] if isinstance(to_email, str) else to_email
    cc_list = [cc_email] if isinstance(cc_email, str) else (cc_email or [])

    message = Mail(
        from_email=Email(settings.SENDGRID_FROM_EMAIL),
        subject=subject,
        html_content=html_content,
    )

    # Add TO
    for to in to_list:
        message.add_to(To(to))

    # Add CC
    for cc in cc_list:
        message.add_cc(Cc(cc))

    if settings.SENDGRID_REPLY_TO:
        message.reply_to = settings.SENDGRID_REPLY_TO

    # Optional attachments (used for forwarding seller documents)
    if attachments:
        for att in attachments:
            try:
                file_bytes = att["content"]
                file_name = att.get("filename", "document")
                content_type = att.get("content_type", "application/octet-stream")

                if isinstance(file_bytes, str):
                    # If it's already a string, assume it's base64 encoded
                    encoded = file_bytes
                else:
                    # Otherwise, encode the bytes
                    encoded = base64.b64encode(file_bytes).decode("ascii")
                attachment = Attachment(
                    FileContent(encoded),
                    FileName(file_name),
                    FileType(content_type),
                    Disposition("attachment"),
                )
                message.add_attachment(attachment)
            except Exception:
                # Best-effort: ignore malformed attachment entries
                continue

    response = service.client.send(message)
    return response.status_code

def extract_attachments(msg):
    """
    Extract attachments from a MIME email message as a list of dicts:
    [
      {
        "filename": str,
        "content": bytes,
        "content_type": str,
      },
      ...
    ]
    Applies basic filtering for images + size.
    """
    attachments = []

    for part in msg.walk():
        content_disposition = part.get("Content-Disposition", "") or ""
        if "attachment" not in content_disposition.lower():
            continue

        filename = part.get_filename()
        if not filename:
            continue

        content = part.get_payload(decode=True)
        if not content:
            continue

        content_type = part.get_content_type()

        # Type filter
        if content_type not in ALLOWED_ATTACHMENT_TYPES:
            continue

        # Size filter
        if len(content) > MAX_ATTACHMENT_SIZE_BYTES:
            continue

        attachments.append(
            {
                "filename": filename,
                "content": content,
                "content_type": content_type,
            }
        )

    return attachments