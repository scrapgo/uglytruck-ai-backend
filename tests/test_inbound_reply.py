"""
Replay a seller's reply (SendGrid Inbound Parse payload) through POST /webhooks/sendgrid/inbound
with Postgres, Quickbase, OpenAI and SendGrid stubbed out. Exercises the real handler code.
Run from the backend directory: venv/bin/python -m unittest tests.test_inbound_reply -v
"""
import copy
import io
import os
import re
import shutil
import unittest
from email.message import EmailMessage
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import app.routers.webhooks as wh
import app.communication.conversation_repository as cr
import app.quickbase.webhook_operations as wo
import app.validators.image_validation as iv
import app.routers.agent as ag
from app.utils.photo_decision import LABEL_TO_FIELD_ID, LABEL_TO_COLUMN
from app.validators.image_validation import PHOTO_FIELD_MAP

RID = 999
WEB = "https://www.uglytruck.net/wp-content/uploads/gravity_forms/6-abc/2026/10/"

REMINDER_ONE = ("Missing Truck Photos Reminder\nHi Smartee,\nWe noticed that some essential photos are missing from your listing. "
                "Please upload the following views to help your truck attract more buyers:\nEngine Driver Side\n"
                "Adding these photos makes your listing stand out\nBest regards,\nUglyTruck.ai Team")
REMINDER_TWO = REMINDER_ONE.replace("Engine Driver Side\n", "Engine Driver Side\nExterior Rear\n")
QUOTED_REPLY = ("Here you go\n\nOn Sun, Oct 5, 2026 at 12:52 PM <diane@scrapgo.com> wrote:\n"
                "> We noticed that some essential photos are missing from your listing. Please upload the\n"
                "> following views to help your truck attract more buyers:\n>\n>    - Engine Driver Side\n>\n"
                "> Adding these photos makes your listing stand out\n")


def jpeg_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buf, format="JPEG")
    return buf.getvalue()


def build_mime(filenames, body="Here is the photo"):
    msg = EmailMessage()
    msg["Subject"] = f"Re: Truck Inquiry - REF ID:{RID}"
    msg["From"] = "seller@example.com"
    msg["To"] = "reply@parse.scrapgo.com"
    msg.set_content(body)
    for name in filenames:
        msg.add_attachment(jpeg_bytes(), maintype="image", subtype="jpeg", filename=name)
    return msg.as_string()


class FakeConn:
    def __init__(self, row):
        self.row = row
        self.updates = []

    async def fetchrow(self, sql, *args):
        s = " ".join(sql.split())
        if s.lower().startswith("update"):
            self.updates.append((s, args))
            m = re.search(r'SET\s+"?(\w+)"?\s*=\s*\$1', s, re.IGNORECASE)
            if m:
                self.row[m.group(1)] = args[0]
            return None
        return dict(self.row)

    async def fetch(self, *a, **k):
        return []

    async def execute(self, *a, **k):
        return None

    async def close(self):
        pass


class FakeQuickbase:
    """Website URLs in 163-169 (164 duplicates 163, like production), no files until uploaded."""
    def __init__(self):
        self.state = {}
        for col in PHOTO_FIELD_MAP:
            web = WEB + ("photo_exterior_driver_side.jpg" if col == "photo_engine_driver_side" else f"{col}.jpg")
            self.state[col] = {"file": None, "file_version": None, "file_url": None, "web": web}
        self.uploads = []

    async def get_values(self, record_id):
        return copy.deepcopy(self.state)

    async def upload(self, record_id, field_id, filename, content, content_type):
        col = next(c for c, f in PHOTO_FIELD_MAP.items() if f == field_id)
        ver = (self.state[col]["file_version"] or 0) + 1
        self.state[col].update(file=filename, file_version=ver, file_url=f"files/br5nbqyfg/{record_id}/{field_id}/{ver}")
        self.uploads.append((record_id, field_id, filename))


class StubClassifier:
    labels = {}

    async def classify_and_map_images(self, attachments, empty_field_ids):
        out = []
        for a in attachments:
            label = self.labels.get(a["filename"])
            out.append({"attachment": a, "label": label, "field_id": LABEL_TO_FIELD_ID.get(label),
                        "should_upload": bool(label), "reason": "stub"})
        return out


class InboundReplyFlow(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(wh.router, prefix="/webhooks")
        self.qb = FakeQuickbase()
        self.row = {
            "record_id": RID, "id": 1, "status": "Need Pics", "seller_email": "seller@example.com",
            "first_name": "Smart", "last_name": "Data", "transportation": None,
            "documents_received_confirmation": 0, "truck_view_pics_received_confirmation": 0,
            "lead_information_received": 1, "communication_subject": "missing_truck", "communication_status": 3,
            "missing_truck_email_count": 1,
        }
        for col, vals in self.qb.state.items():
            self.row[col] = vals["web"]
        self.conn = FakeConn(self.row)
        self.update_status = AsyncMock()
        self.agent = AsyncMock(return_value={"message": "ok"})
        self.validate = AsyncMock(return_value=([WEB + "x.jpg"] * 7, ["Engine Driver Side"]))
        self.analyse = AsyncMock(return_value=None)
        self.outbound = [REMINDER_ONE]

    def tearDown(self):
        shutil.rmtree(os.path.join(os.path.dirname(os.path.dirname(wh.__file__)), "storage", "images", str(RID)), ignore_errors=True)

    def post_reply(self, filenames, labels, body="Here is the photo"):
        StubClassifier.labels = labels

        async def get_conn(*a, **k):
            return self.conn

        async def outbound_bodies(record_id, limit=10):
            return list(self.outbound)

        patches = [
            patch.object(wh, "get_connection", get_conn),
            patch.object(cr, "fetch_latest_outbound_bodies", outbound_bodies),
            patch.object(wo, "get_record_photo_values", self.qb.get_values),
            patch.object(wh, "SmartImageClassifier", StubClassifier),
            patch.object(wh, "upload_file_to_quickbase", self.qb.upload),
            patch.object(wh, "update_lead_status_pg", self.update_status),
            patch.object(ag, "webhook_call_agent", self.agent),
            patch.object(wh, "webhook_call_agent", self.agent),
            patch.object(iv, "validate_images", self.validate),
            patch.object(wh, "store_conversation_message", AsyncMock()),
            patch.object(wh, "build_conversation", AsyncMock(return_value="conversation")),
            patch.object(wh, "analyse_conversation", self.analyse),
            patch.object(wh, "fetch_current_status", AsyncMock(side_effect=lambda rid: self.row["status"])),
            patch.object(wh, "fetch_record_by_id", AsyncMock(side_effect=lambda rid: dict(self.row))),
            patch.object(wh, "update_quickbase_status", AsyncMock()),
            patch.object(wh, "extract_lead_info_from_email", AsyncMock(return_value={})),
            patch.object(wh, "send_outbound_reply", AsyncMock()),
            patch.object(wh, "SendGridService", MagicMock()),
            patch.object(wh, "send_sendgrid_email", MagicMock()),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        with TestClient(self.app) as client:
            resp = client.post("/webhooks/sendgrid/inbound", data={
                "email": build_mime(filenames, body),
                "from": "seller@example.com",
                "to": "reply@parse.scrapgo.com",
                "subject": f"Re: Truck Inquiry - REF ID:{RID}",
            })
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "accepted")

    def assert_completed(self):
        self.update_status.assert_awaited_once_with(RID, "Complete")
        self.assertEqual(self.agent.await_count, 1)
        self.assertEqual(self.agent.await_args.kwargs, {"record_id": RID, "set_missing_truck_none": True})
        self.assertEqual(self.row["communication_subject"], "complete_status")
        self.assertEqual(self.row["truck_view_pics_received_confirmation"], 1)

    def test_only_requested_view_replaced_then_complete(self):
        """Reminder asked for Engine Driver Side; seller sends Front + Engine Driver Side + Passenger Side."""
        with self.assertNoLogs(level="ERROR"):
            self.post_reply(
                ["Exterior Front.jpg", "Photo Engine Driver Side.jpg", "Exterior Passenger Side.jpg"],
                {"Exterior Front.jpg": "Exterior Front", "Photo Engine Driver Side.jpg": "Engine Driver Side",
                 "Exterior Passenger Side.jpg": "Exterior Passenger Side"},
            )
        self.assertEqual(self.qb.uploads, [(RID, 39, "Photo Engine Driver Side.jpg")])
        self.assertEqual(self.row["photo_engine_driver_side"], f"files/br5nbqyfg/{RID}/39/1")   # replaced
        self.assertTrue(self.row["photo_exterior_front"].startswith("http"))                      # untouched
        self.assertTrue(self.row["photo_exterior_passenger_side"].startswith("http"))             # untouched
        self.validate.assert_not_awaited()
        self.assert_completed()
        self.assertEqual(self.analyse.await_args.kwargs["truck_photos_count"], 3)

    def test_partial_reply_asks_only_for_the_rest(self):
        self.outbound = [REMINDER_TWO]
        self.post_reply(["eng.jpg"], {"eng.jpg": "Engine Driver Side"})
        self.assertEqual(self.qb.uploads, [(RID, 39, "eng.jpg")])
        self.update_status.assert_not_awaited()
        self.assertEqual(self.agent.await_count, 1)
        self.assertEqual(self.agent.await_args.kwargs, {"record_id": RID, "missing_views": ["Exterior Rear"]})
        self.assertEqual(self.row["communication_subject"], "missing_truck")

    def test_wrong_side_label_is_trusted_from_reminder(self):
        self.post_reply(["eng.jpg"], {"eng.jpg": "Engine Passenger Side"})
        self.assertEqual(self.qb.uploads, [(RID, 39, "eng.jpg")])
        self.assert_completed()

    def test_reminder_read_from_quoted_reply_when_not_stored(self):
        self.outbound = []
        self.post_reply(["eng.jpg"], {"eng.jpg": "Engine Driver Side"}, body=QUOTED_REPLY)
        self.assertEqual(self.qb.uploads, [(RID, 39, "eng.jpg")])
        self.validate.assert_not_awaited()
        self.assert_completed()

    def test_no_reminder_anywhere_falls_back_to_content_validation(self):
        self.outbound = []
        self.post_reply(["eng.jpg"], {"eng.jpg": "Engine Driver Side"})
        self.assertEqual(self.qb.uploads, [(RID, 39, "eng.jpg")])
        self.validate.assert_awaited_once()
        self.assert_completed()

    def test_unrequested_photo_only_triggers_follow_up(self):
        self.post_reply(["front.jpg", "rear.jpg"], {"front.jpg": "Exterior Front", "rear.jpg": "Exterior Rear"})
        self.assertEqual(self.qb.uploads, [])
        self.update_status.assert_not_awaited()
        self.assertEqual(self.agent.await_args.kwargs, {"record_id": RID, "missing_views": ["Engine Driver Side"]})

    def test_empty_body_still_completes(self):
        self.post_reply(["eng.jpg"], {"eng.jpg": "Engine Driver Side"}, body="")
        self.assert_completed()
        self.analyse.assert_not_awaited()

    def test_status_gate_blocks_already_complete_lead(self):
        self.row["status"] = "Complete"
        self.post_reply(["eng.jpg"], {"eng.jpg": "Engine Driver Side"})
        self.assertEqual(self.qb.uploads, [(RID, 39, "eng.jpg")])   # photo still stored for Dale
        self.update_status.assert_not_awaited()
        self.agent.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
