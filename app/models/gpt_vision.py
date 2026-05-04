import base64
import json
from io import BytesIO
from PIL import Image
from typing import Dict, Optional, Union
from openai import AsyncOpenAI
from app.backend.config import settings


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

    def _build_prompt(self, orientation_hint: Optional[Dict]) -> str:
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
        prompt = self._build_prompt(orientation_hint)

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

            predicted_label, orientation_override_applied = self._apply_orientation_hint(
                gpt_predicted_label,
                orientation_hint,
            )

            probabilities = {label: 0.01 for label in self.labels}
            probabilities[predicted_label] = 0.95

            return {
                "predicted_label": predicted_label,
                "gpt_predicted_label": gpt_predicted_label,
                "orientation_override_applied": orientation_override_applied,
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

    def _find_closest_label(self, response: str) -> str:
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

        print(f"Warning: Could not match GPT response '{response}' to any label. Using first label.")
        return self.labels[0]
