"""2026-11-05: a photo GPT cannot see a truck in must not count as the first view (Exterior Front)."""
import asyncio
import os
import unittest
from types import SimpleNamespace

from PIL import Image

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from app.models.gpt_vision import TruckViewClassifier
from app.utils.photo_decision import fill_side_pairs, found_views

LABELS = [
    "Exterior Front", "Exterior Driver Side", "Exterior Passenger Side", "Engine Driver Side",
    "Engine Passenger Side", "Exterior Rear", "Interior View of the truck",
]
NOT_A_TRUCK = "I'm unable to classify this image as it does not depict a truck."


def classifier(answer):
    """A TruckViewClassifier whose OpenAI client always answers `answer`."""
    c = TruckViewClassifier.__new__(TruckViewClassifier)
    c.labels = list(LABELS)
    c.model = "gpt-4o"

    async def create(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=answer))])

    c.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return c


class UnrelatedImageTests(unittest.TestCase):
    def test_not_a_truck_answer_gets_no_label(self):
        self.assertIsNone(classifier("x")._find_closest_label(NOT_A_TRUCK))

    def test_real_answers_still_map_to_labels(self):
        c = classifier("x")
        self.assertEqual(c._find_closest_label("This is the exterior front of the truck."), "Exterior Front")
        self.assertEqual(c._find_closest_label("Engine bay, driver side"), "Engine Driver Side")

    def test_classify_image_returns_unrelated_for_a_screenshot(self):
        result = asyncio.run(classifier(NOT_A_TRUCK).classify_image(Image.new("RGB", (64, 48), "white")))
        self.assertIsNone(result["predicted_label"])
        self.assertTrue(result["unrelated"])
        self.assertEqual(result["raw_response"], NOT_A_TRUCK)

    def test_classify_image_still_labels_a_truck_view(self):
        result = asyncio.run(classifier("Exterior Rear").classify_image(Image.new("RGB", (64, 48), "white")))
        self.assertEqual(result["predicted_label"], "Exterior Rear")
        self.assertFalse(result.get("unrelated", False))

    def test_unlabelled_photo_leaves_the_front_view_missing(self):
        # six real views plus a screenshot uploaded instead of the front photo
        results = [{"predicted_label": label, "image_hash": i} for i, label in enumerate(LABELS[1:])]
        results.append({"predicted_label": None, "unrelated": True, "raw_response": NOT_A_TRUCK})
        found = found_views(results)
        self.assertEqual(fill_side_pairs(results, found), [])
        self.assertEqual(sorted(set(LABELS) - found), ["Exterior Front"])

    def test_found_views_skips_failed_and_unlabelled_photos(self):
        results = [{"predicted_label": None}, {"error": "download failed"}, {"predicted_label": "Exterior Rear"}]
        self.assertEqual(found_views(results), {"Exterior Rear"})


if __name__ == "__main__":
    unittest.main()
