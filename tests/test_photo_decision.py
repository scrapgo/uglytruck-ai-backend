"""Pure-logic tests for the reply-with-photos flow. No network, no database."""
import asyncio
import unittest
from unittest.mock import patch

from app.utils.photo_decision import (
    parse_requested_views,
    select_uploads,
    remaining_requested_views,
    LABEL_TO_FIELD_ID,
)
from app.quickbase import webhook_operations as wo


REMINDER_TEXT = """Missing Truck Photos Reminder
Hi Smartee,
Thank you for listing your 2020 Freightliner Argosy on UglyTruck.ai.
We noticed that some essential photos are missing from your listing. Please upload the following views to help your truck attract more buyers:
Engine Driver Side
Adding these photos makes your listing stand out and helps our system match your truck with the most relevant and verified buyers.
Best regards,
UglyTruck.ai Team"""

REMINDER_TWO = REMINDER_TEXT.replace("Engine Driver Side\n", "Engine Driver Side\nExterior Rear\n")

GMAIL_QUOTED_REPLY = """Here you go

On Sun, Oct 5, 2026 at 12:52 PM <diane@scrapgo.com> wrote:
> Missing Truck Photos Reminder
> Hi Smartee,
> We noticed that some essential photos are missing from your listing. Please upload the
> following views to help your truck attract more buyers:
>
>    - Engine Driver Side
>    - Interior View of the truck
>
> Adding these photos makes your listing stand out
"""

HTML_REMINDER = """<html><body><p>We noticed that some essential photos are missing from your listing.
Please upload the following views to help your truck attract more buyers:</p>
<ul><li>Exterior Passenger Side</li></ul><p>Adding these photos makes your listing stand out</p></body></html>"""

COMPLETE_TEXT = """Hi Smartee,
Thanks for sending over all the details about your 2020 Freightliner Argosy.
We're now tapping into our nationwide buyer network to secure the best possible offer for you."""


def att(name):
    return {"filename": name, "content": b"x", "content_type": "image/jpeg"}


def result(name, label):
    return {"attachment": att(name), "label": label, "field_id": LABEL_TO_FIELD_ID.get(label), "should_upload": True, "reason": ""}


class ParseRequestedViews(unittest.TestCase):
    def test_stored_outbound_single(self):
        self.assertEqual(parse_requested_views(REMINDER_TEXT), ["Engine Driver Side"])

    def test_stored_outbound_two_in_canonical_order(self):
        self.assertEqual(parse_requested_views(REMINDER_TWO), ["Engine Driver Side", "Exterior Rear"])

    def test_gmail_quoted_reply(self):
        self.assertEqual(parse_requested_views(GMAIL_QUOTED_REPLY), ["Engine Driver Side", "Interior View of the truck"])

    def test_html(self):
        self.assertEqual(parse_requested_views(HTML_REMINDER), ["Exterior Passenger Side"])

    def test_not_a_reminder(self):
        self.assertEqual(parse_requested_views(COMPLETE_TEXT), [])
        self.assertEqual(parse_requested_views(""), [])
        self.assertEqual(parse_requested_views(None), [])

    def test_does_not_confuse_sibling_labels(self):
        # "Exterior Driver Side" in the list must not also yield "Engine Driver Side"
        text = REMINDER_TEXT.replace("Engine Driver Side", "Exterior Driver Side")
        self.assertEqual(parse_requested_views(text), ["Exterior Driver Side"])


class SelectUploads(unittest.TestCase):
    def uploads(self, decisions):
        return [(d["attachment"]["filename"], d["label"], d["field_id"]) for d in decisions if d["should_upload"]]

    def test_only_requested_view_is_uploaded(self):
        decisions = select_uploads(
            [result("front.jpg", "Exterior Front"), result("eng.jpg", "Engine Driver Side"), result("pass.jpg", "Exterior Passenger Side")],
            ["Engine Driver Side"],
        )
        self.assertEqual(self.uploads(decisions), [("eng.jpg", "Engine Driver Side", 39)])
        skipped = {d["attachment"]["filename"]: d["reason"] for d in decisions if not d["should_upload"]}
        self.assertIn("not requested", skipped["front.jpg"])
        self.assertIn("not requested", skipped["pass.jpg"])

    def test_two_requested_one_sent(self):
        decisions = select_uploads([result("eng.jpg", "Engine Driver Side")], ["Engine Driver Side", "Exterior Rear"])
        self.assertEqual(self.uploads(decisions), [("eng.jpg", "Engine Driver Side", 39)])
        self.assertEqual(remaining_requested_views(["Engine Driver Side", "Exterior Rear"], ["Engine Driver Side"]), ["Exterior Rear"])

    def test_duplicate_photo_for_same_view_uploads_once(self):
        decisions = select_uploads([result("a.jpg", "Engine Driver Side"), result("b.jpg", "Engine Driver Side")], ["Engine Driver Side"])
        self.assertEqual(self.uploads(decisions), [("a.jpg", "Engine Driver Side", 39)])
        self.assertIn("already covered", decisions[1]["reason"])

    def test_sibling_side_is_trusted_from_reminder(self):
        decisions = select_uploads([result("eng.jpg", "Engine Passenger Side")], ["Engine Driver Side"])
        self.assertEqual(self.uploads(decisions), [("eng.jpg", "Engine Driver Side", 39)])
        self.assertIn("side assumed", decisions[0]["basis"])

    def test_sibling_not_used_when_both_sides_requested(self):
        decisions = select_uploads([result("eng.jpg", "Engine Passenger Side")], ["Engine Driver Side", "Engine Passenger Side"])
        self.assertEqual(self.uploads(decisions), [("eng.jpg", "Engine Passenger Side", 46)])

    def test_single_photo_single_request_assumed(self):
        decisions = select_uploads([result("x.jpg", None)], ["Exterior Rear"])
        self.assertEqual(self.uploads(decisions), [("x.jpg", "Exterior Rear", 49)])
        decisions = select_uploads([result("x.jpg", "Exterior Front")], ["Exterior Rear"])
        self.assertEqual(self.uploads(decisions), [("x.jpg", "Exterior Rear", 49)])

    def test_single_photo_not_assumed_when_two_requested(self):
        decisions = select_uploads([result("x.jpg", "Exterior Front")], ["Exterior Rear", "Interior View of the truck"])
        self.assertEqual(self.uploads(decisions), [])

    def test_fallback_without_reminder_uploads_empty_file_slots_only(self):
        decisions = select_uploads(
            [result("front.jpg", "Exterior Front"), result("rear.jpg", "Exterior Rear"), result("bad.jpg", None)],
            None,
            existing_files={"photo_exterior_rear"},
        )
        self.assertEqual(self.uploads(decisions), [("front.jpg", "Exterior Front", 47)])
        self.assertEqual(decisions[1]["reason"], "A file already exists for this view")
        self.assertEqual(decisions[2]["reason"], "Classification failed")


class QuickbasePhotoValues(unittest.TestCase):
    def test_extract_file_info_both_shapes(self):
        self.assertEqual(wo._extract_file_info({"fileName": "a.jpg"}), ("a.jpg", 1))
        self.assertEqual(
            wo._extract_file_info({"url": "https://x/up/t/a/r1/e39/v2", "versions": [
                {"fileName": "old.jpg", "versionNumber": 1}, {"fileName": "new.jpg", "versionNumber": 2}]}),
            ("new.jpg", 2),
        )
        self.assertEqual(wo._extract_file_info({"url": "https://x/up/t/a/r1/e39/v3"}), ("attached", 3))
        self.assertEqual(wo._extract_file_info(None), (None, None))
        self.assertEqual(wo._extract_file_info({}), (None, None))
        self.assertEqual(wo._extract_file_info({"versions": []}), (None, None))

    def test_file_wins_over_website_url(self):
        cells = {
            "3": {"value": 999},
            "39": {"value": {"url": "u", "versions": [{"fileName": "engine.jpg", "versionNumber": 2}]}},
            "164": {"value": "https://uglytruck.net/wp-content/uploads/gravity_forms/x/dup.jpg"},
            "167": {"value": "https://uglytruck.net/wp-content/uploads/gravity_forms/x/front.jpg"},
        }
        async def fake_fetch(record_id):
            return cells, "br5nbqyfg"
        with patch.object(wo, "_fetch_photo_record_data", fake_fetch):
            values = asyncio.run(wo.get_record_photo_values(999))
            status = asyncio.run(wo.get_record_photo_status(999))
        self.assertEqual(values["photo_engine_driver_side"]["file"], "engine.jpg")
        self.assertEqual(values["photo_engine_driver_side"]["file_url"], "files/br5nbqyfg/999/39/2")
        self.assertEqual(values["photo_engine_driver_side"]["web"], cells["164"]["value"])
        self.assertEqual(status["photo_engine_driver_side"], "files/br5nbqyfg/999/39/2")
        self.assertEqual(status["photo_exterior_front"], cells["167"]["value"])
        self.assertIsNone(status["photo_cab"])

    def test_record_not_found(self):
        async def fake_fetch(record_id):
            return None, None
        with patch.object(wo, "_fetch_photo_record_data", fake_fetch):
            self.assertEqual(asyncio.run(wo.get_record_photo_values(1)), {})
            self.assertEqual(asyncio.run(wo.get_record_photo_status(1)), {})


if __name__ == "__main__":
    unittest.main()
