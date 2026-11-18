import base64
import json
import os
from io import BytesIO
from PIL import Image
from typing import Dict, Optional, Union
from openai import AsyncOpenAI
from app.backend.config import settings

# The OpenCLIP left/right hint is trusted only at or above this confidence. Its scores are usually
# tiny (0.001-0.013 on real listings, 2026-11-04), i.e. a coin flip, and a wrong hint turned a driver
# side photo into a second passenger side one ("Exterior Driver Side missing"). Below it GPT decides
# the side itself from which way the truck's front points.
ORIENTATION_MIN_CONFIDENCE = float(os.getenv("ORIENTATION_MIN_CONFIDENCE", "0.05"))


class TruckViewClassifier:
    """
    Truck view classifier using GPT Vision plus an OpenCLIP cab-orientation hint.
    """

    def __init__(self, model: str = None):
        self.model = model or settings.OPENAI_MODEL
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self.labels = [
            "Exterior Front",
            "Exterior Driver Side",
            "Exterior Passenger Side",
            "Engine Driver Side",
            "Engine Passenger Side",
            "Exterior Rear",
            "Interior View of the truck",
        ]

    def _encode_image(self, image: Image.Image) -> str:
        buffered = BytesIO()
        image.save(buffered, format="JPEG")
        return base64.b64encode(buffered.getvalue()).decode("utf-8")

    @staticmethod
    def _strong_hint(orientation_hint: Optional[Dict]) -> Optional[Dict]:
        """The hint, only when it is clear enough to decide driver vs passenger side."""
        if not orientation_hint:
            return None
        if orientation_hint.get("predicted_label") not in {"cab_on_left", "cab_on_right"}:
            return None
        try:
            confidence = float(orientation_hint.get("confidence", 0))
        except (TypeError, ValueError):
            return None
        return orientation_hint if confidence >= ORIENTATION_MIN_CONFIDENCE else None

    def _build_prompt(self, orientation_hint: Optional[Dict]) -> str:
        if not orientation_hint:
            return self._build_prompt_without_hint()
        orientation_text = json.dumps(orientation_hint or {}, indent=2)
        return f"""You are classifying a truck image into exactly one allowed label.

Allowed labels:
{chr(10).join(f'- {label}' for label in self.labels)}

You are also given an OpenCLIP cab-orientation hint. Use it to resolve driver-vs-passenger side when the image is a side view or engine-side view.
OpenCLIP orientation output:
{orientation_text}

Reasoning rules:
- First inspect the image yourself and determine whether it is front, rear, interior, exterior side, or engine side.
- If the image is front, rear, or interior, ignore the orientation hint and choose the correct non-side label.
- If the image is an exterior side view, the side label must match the orientation hint:
  cab_on_left -> Driver Side
  cab_on_right -> Passenger Side
- If the image is an engine-side view, the side label must match the orientation hint:
    cab_on_left -> Passenger Side
    cab_on_right -> Driver Side
- If the image does not show a truck at all (a screenshot, document, person, or other object),
  respond exactly: Not a truck photo
- Otherwise respond with ONLY one exact label from the allowed labels.
"""

    def _build_prompt_without_hint(self) -> str:
        return f"""You are classifying a truck image into exactly one allowed label.

Allowed labels:
{chr(10).join(f'- {label}' for label in self.labels)}

Reasoning rules:
- First inspect the image and determine whether it is front, rear, interior, exterior side, or engine side.
- If the image is front, rear, or interior, choose the correct non-side label.
- For an exterior side view or an engine-side view (hood open, seen from the side or a front corner),
  decide the side yourself. These are US trucks: the driver sits on the truck's LEFT.
  Find which way the FRONT of the truck (headlights, bumper, grille, front wheel) points in the photo:
  front points to the LEFT of the photo -> Driver Side
  front points to the RIGHT of the photo -> Passenger Side
  A steering wheel seen through the nearest door window also means Driver Side.
- If the image does not show a truck at all (a screenshot, document, person, or other object),
  respond exactly: Not a truck photo
- Otherwise respond with ONLY one exact label from the allowed labels.
"""

    def _apply_orientation_hint(self, predicted_label: str, orientation_hint: Optional[Dict]) -> tuple[str, bool]:
        if not orientation_hint:
            return predicted_label, False

        orientation_label = orientation_hint.get("predicted_label")
        if orientation_label not in {"cab_on_left", "cab_on_right"}:
            return predicted_label, False

        exterior_map = {
            "cab_on_left": "Exterior Driver Side",
            "cab_on_right": "Exterior Passenger Side",
        }
        engine_map = {
            "cab_on_left": "Engine Passenger Side",
            "cab_on_right": "Engine Driver Side",
        }

        if predicted_label in {"Exterior Driver Side", "Exterior Passenger Side"}:
            corrected_label = exterior_map[orientation_label]
            return corrected_label, corrected_label != predicted_label

        if predicted_label in {"Engine Driver Side", "Engine Passenger Side"}:
            corrected_label = engine_map[orientation_label]
            return corrected_label, corrected_label != predicted_label

        return predicted_label, False

    async def classify_image(
        self,
        image_input: Union[str, Image.Image],
        orientation_hint: Optional[Dict] = None,
    ) -> Dict:
        if isinstance(image_input, Image.Image):
            image = image_input.convert("RGB")
        else:
            image = Image.open(image_input).convert("RGB")

        base64_image = self._encode_image(image)
        used_hint = self._strong_hint(orientation_hint)
        prompt = self._build_prompt(used_hint)

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}",
                                    "detail": "high",
                                },
                            },
                        ],
                    }
                ],
                max_tokens=50,
                temperature=0.0,
            )

            raw_response = response.choices[0].message.content.strip()
            gpt_predicted_label = raw_response
            if gpt_predicted_label not in self.labels:
                gpt_predicted_label = self._find_closest_label(raw_response)

            if gpt_predicted_label is None:
                # Not a truck view (screenshot, document, random photo): it counts for no view.
                return {
                    "predicted_label": None,
                    "gpt_predicted_label": None,
                    "unrelated": True,
                    "orientation_override_applied": False,
                    "orientation_hint_used": False,
                    "probabilities": {label: 0.0 for label in self.labels},
                    "orientation_hint": orientation_hint,
                    "model": self.model,
                    "raw_response": raw_response,
                }

            predicted_label, orientation_override_applied = self._apply_orientation_hint(
                gpt_predicted_label,
                used_hint,
            )

            probabilities = {label: 0.01 for label in self.labels}
            probabilities[predicted_label] = 0.95

            return {
                "predicted_label": predicted_label,
                "gpt_predicted_label": gpt_predicted_label,
                "orientation_override_applied": orientation_override_applied,
                "orientation_hint_used": used_hint is not None,
                "probabilities": probabilities,
                "orientation_hint": orientation_hint,
                "model": self.model,
                "raw_response": raw_response,
            }
        except Exception as e:
            print(f"Error calling GPT Vision API: {str(e)}")
            return {
                "predicted_label": self.labels[0],
                "gpt_predicted_label": self.labels[0],
                "orientation_override_applied": False,
                "probabilities": {label: 1.0 / len(self.labels) for label in self.labels},
                "orientation_hint": orientation_hint,
                "model": self.model,
                "error": str(e),
            }

    def _find_closest_label(self, response: str) -> Optional[str]:
        """Map a free-text GPT answer to a label, or None when it is not a truck view at all."""
        response_lower = response.lower()
        for label in self.labels:
            if label.lower() in response_lower:
                return label

        if "front" in response_lower:
            return "Exterior Front"
        if "rear" in response_lower or "back" in response_lower:
            return "Exterior Rear"
        if "driver" in response_lower and "engine" in response_lower:
            return "Engine Driver Side"
        if "passenger" in response_lower and "engine" in response_lower:
            return "Engine Passenger Side"
        if "driver" in response_lower:
            return "Exterior Driver Side"
        if "passenger" in response_lower:
            return "Exterior Passenger Side"
        if "interior" in response_lower or "cab" in response_lower:
            return "Interior View of the truck"

        # 2026-11-05: answers like "this image does not depict a truck" used to fall back to the first
        # label (Exterior Front), so an unrelated upload could pass as the front photo. No label now.
        print(f"Warning: GPT response '{response}' is not a truck view. Leaving this photo unlabelled.")
        return None
