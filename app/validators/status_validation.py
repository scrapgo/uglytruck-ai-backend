import httpx
from app.communication.base import TruckEmailData


COMPLETED_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<title>We’re Reviewing Your Info</title>
<style>
body{font-family:Arial,sans-serif;background:#f9fafb;color:#333;margin:0;padding:0;}
.container{max-width:600px;margin:30px auto;background:#fff;border-radius:10px;padding:30px;box-shadow:0 4px 12px rgba(0,0,0,0.05);}
h3{color:#0d9488;margin-top:0;}p{line-height:1.6;}
.footer{text-align:center;font-size:12px;color:#777;margin-top:30px;}
</style>
</head>
<body>
<div class="container">
<h3>Hi *|FNAME|*,</h3>

<p>
Thanks for sending over all the details about your <strong>*|YEAR|* *|MAKE|* *|MODEL|*</strong> (Truck ID: *|TRUCK_ID|*).
</p>

<p>
We’re now tapping into our nationwide buyer network to secure the best possible offer for you. If we need anything else, we’ll reach out.
</p>

<p>Best regards,<br>The UglyTruck.ai Team</p>

<div class="footer">© 2025 UglyTruck.ai — Helping you sell your truck smarter, not harder.</div>
</div>
</body>
</html>
"""


def render_completed_template(data: TruckEmailData) -> str:
    """Replace Mailchimp-style placeholders."""
    html = COMPLETED_TEMPLATE
    html = html.replace("*|FNAME|*", data.FNAME or "")
    html = html.replace("*|YEAR|*", data.YEAR or "")
    html = html.replace("*|MAKE|*", data.MAKE or "")
    html = html.replace("*|MODEL|*", data.MODEL or "")
    html = html.replace("*|TRUCK_ID|*", data.TRUCK_ID or "")
    return html


async def send_completed_status_email(rec: dict, send_gmail_url: str):
    """
    Sends email when status = 'completed'
    """

    # Build email object
    email_payload = TruckEmailData(
        TRUCK_ID=rec.get("vin"),
        FNAME=rec.get("last_name"),
        EMAIL=rec.get("seller_email"),
        YEAR=str(rec.get("year") or "").strip(),
        MAKE=rec.get("make"),
        MODEL=rec.get("model"),
        CITY=rec.get("location_city"),
        STATUS="completed",
        NOTES=rec.get("truck_condition_notes"),
    )

    html_body = render_completed_template(email_payload)

    request_payload = {
        "to_email": email_payload.EMAIL,
        "subject": f"Your Truck Details Are Submitted — Truck ID {email_payload.TRUCK_ID}",
        "html_content": html_body
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(send_gmail_url, json=request_payload)

    return resp
