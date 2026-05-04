"""
Smart Image Classification using GPT Vision
Maps truck view images to correct QuickBase fields
"""

import base64
import json
import logging
from io import BytesIO
from PIL import Image
from typing import Dict, Optional, List
from openai import AsyncOpenAI
from app.backend.config import settings
from app.models.open_clip import TruckViewClassifier as OpenClipTruckOrientationClassifier
from app.validators.image_validation import PHOTO_VIEW_LABELS, PHOTO_FIELD_MAP


class SmartImageClassifier:
    """
    Classifies truck images using GPT Vision and maps them to QuickBase fields.
    """

    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = settings.OPENAI_MODEL
        self.orientation_classifier = OpenClipTruckOrientationClassifier()

        self.label_to_field_id = {
            "Exterior Driver Side": 38,
            "Engine Driver Side": 39,
            "Interior View of the truck": 40,
            "Engine Passenger Side": 46,
            "Exterior Front": 47,
            "Exterior Passenger Side": 48,
            "Exterior Rear": 49,
        }
        self.valid_labels = list(self.label_to_field_id.keys())

    def _build_prompt(self, orientation_hint: Optional[Dict]) -> str:
        orientation_text = json.dumps(orientation_hint or {}, indent=2)
        return f"""You are classifying a truck image into exactly one allowed label.

Allowed labels:
{chr(10).join(f'- {label}' for label in self.valid_labels)}

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
  cab_on_left -> Engine Passenger Side
  cab_on_right -> Engine Driver Side
- Respond with ONLY one exact label from the allowed labels.
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

    async def classify_image(self, image_bytes: bytes) -> Optional[str]:
        """
        Classify a truck image using GPT Vision plus OpenCLIP orientation guidance.

        Args:
            image_bytes: Raw image bytes

        Returns:
            Predicted label (e.g., "Exterior Front") or None if classification fails
        """
        try:
            image = Image.open(BytesIO(image_bytes)).convert("RGB")
            base64_image = base64.b64encode(image_bytes).decode("utf-8")
            orientation_hint = self.orientation_classifier.classify_image(image)
            prompt = self._build_prompt(orientation_hint)

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
                                }
                            }
                        ]
                    }
                ],
                max_tokens=20,
                temperature=0.0,
            )

            raw_response = response.choices[0].message.content.strip()
            gpt_predicted_label = raw_response
            if gpt_predicted_label not in self.valid_labels:
                gpt_predicted_label = self._fuzzy_match_label(raw_response)

            if not gpt_predicted_label:
                logging.warning("⚠️ Could not map GPT response '%s' to a valid label", raw_response)
                return None

            predicted_label, orientation_override_applied = self._apply_orientation_hint(
                gpt_predicted_label,
                orientation_hint,
            )

            if orientation_override_applied:
                logging.info(
                    "↔️ OpenCLIP orientation override applied: %s -> %s",
                    gpt_predicted_label,
                    predicted_label,
                )

            logging.info(
                "✅ GPT Vision classified image as: %s (raw=%s, orientation=%s)",
                predicted_label,
                gpt_predicted_label,
                orientation_hint.get("predicted_label") if orientation_hint else None,
            )
            return predicted_label

        except Exception as e:
            logging.error(f"❌ GPT Vision classification failed: {str(e)}")
            return None

    def _fuzzy_match_label(self, response: str) -> Optional[str]:
        """
        Attempt to match GPT response to a valid label using fuzzy matching.
        """
        response_lower = response.lower()

        for label in self.valid_labels:
            if label.lower() in response_lower:
                return label

        if "front" in response_lower:
            return "Exterior Front"
        elif "rear" in response_lower or "back" in response_lower:
            return "Exterior Rear"
        elif "driver" in response_lower and "engine" in response_lower:
            return "Engine Driver Side"
        elif "passenger" in response_lower and "engine" in response_lower:
            return "Engine Passenger Side"
        elif "driver" in response_lower:
            return "Exterior Driver Side"
        elif "passenger" in response_lower:
            return "Exterior Passenger Side"
        elif "interior" in response_lower or "cab" in response_lower:
            return "Interior View of the truck"

        logging.warning(f"⚠️ Could not match GPT response '{response}' to any label")
        return None

    def get_field_id_for_label(self, label: str) -> Optional[int]:
        """
        Get QuickBase field ID for a given label.
        """
        return self.label_to_field_id.get(label)

    async def classify_and_map_images(
            self,
            attachments: List[Dict],
            empty_field_ids: List[int]
    ) -> List[Dict]:
        """
        Classify multiple images and map them to QuickBase fields.

        Args:
            attachments: List of attachment dicts with 'content' and 'filename'
            empty_field_ids: List of currently empty QuickBase field IDs

        Returns:
            List of dicts with 'attachment', 'label', 'field_id', 'should_upload'
        """
        results = []

        for attachment in attachments:
            label = await self.classify_image(attachment["content"])

            if not label:
                logging.warning(f"⚠️ Skipping {attachment.get('filename')} - classification failed")
                results.append({
                    "attachment": attachment,
                    "label": None,
                    "field_id": None,
                    "should_upload": False,
                    "reason": "Classification failed"
                })
                continue

            field_id = self.get_field_id_for_label(label)

            if not field_id:
                logging.warning(f"⚠️ Skipping {attachment.get('filename')} - no field mapping for label '{label}'")
                results.append({
                    "attachment": attachment,
                    "label": label,
                    "field_id": None,
                    "should_upload": False,
                    "reason": "No field mapping"
                })
                continue

            if field_id not in empty_field_ids:
                logging.info(f"⏭️  Skipping {attachment.get('filename')} - field {field_id} already filled")
                results.append({
                    "attachment": attachment,
                    "label": label,
                    "field_id": field_id,
                    "should_upload": False,
                    "reason": "Field already filled"
                })
                continue

            results.append({
                "attachment": attachment,
                "label": label,
                "field_id": field_id,
                "should_upload": True,
                "reason": "Ready to upload"
            })

        return results
