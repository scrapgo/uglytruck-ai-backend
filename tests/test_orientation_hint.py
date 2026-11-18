"""2026-11-04: a weak OpenCLIP left/right hint must not overrule GPT's side choice."""
import os
import unittest

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from app.models.gpt_vision import ORIENTATION_MIN_CONFIDENCE, TruckViewClassifier

classify = TruckViewClassifier.__new__(TruckViewClassifier)
classify.labels = [
    "Exterior Front", "Exterior Driver Side", "Exterior Passenger Side", "Engine Driver Side",
    "Engine Passenger Side", "Exterior Rear", "Interior View of the truck",
]


class OrientationHintTests(unittest.TestCase):
    def test_a_weak_hint_is_ignored(self):
        # what OpenCLIP said for the client's driver side photo: cab_on_right, confidence -0.006
        hint = {"predicted_label": "cab_on_right", "confidence": -0.0061}
        assert TruckViewClassifier._strong_hint(hint) is None
        assert classify._apply_orientation_hint("Exterior Driver Side", TruckViewClassifier._strong_hint(hint)) == ("Exterior Driver Side", False)

    def test_a_strong_hint_still_decides_the_side(self):
        hint = {"predicted_label": "cab_on_right", "confidence": ORIENTATION_MIN_CONFIDENCE + 0.01}
        assert TruckViewClassifier._strong_hint(hint) is hint
        assert classify._apply_orientation_hint("Exterior Driver Side", hint) == ("Exterior Passenger Side", True)

    def test_bad_hints_are_ignored(self):
        for hint in (None, {}, {"predicted_label": "unknown", "confidence": 1}, {"predicted_label": "cab_on_left", "confidence": "x"}):
            assert TruckViewClassifier._strong_hint(hint) is None

    def test_without_a_hint_gpt_is_told_how_to_judge_the_side(self):
        prompt = classify._build_prompt(None)
        assert "OpenCLIP" not in prompt
        assert "front points to the LEFT of the photo -> Driver Side" in prompt
        assert "front points to the RIGHT of the photo -> Passenger Side" in prompt
        for label in classify.labels:
            assert f"- {label}" in prompt

    def test_front_rear_and_interior_are_never_changed_by_a_hint(self):
        hint = {"predicted_label": "cab_on_left", "confidence": 1.0}
        for label in ("Exterior Front", "Exterior Rear", "Interior View of the truck"):
            assert classify._apply_orientation_hint(label, hint) == (label, False)


if __name__ == "__main__":
    unittest.main()
