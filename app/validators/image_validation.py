import httpx
from app.backend.config import settings

# Mapping of DB column names to Quickbase Field IDs
# Based on user note: 38 is field ID similarly 39, 46, 47, 48, 49 are also fieldIDs.
PHOTO_FIELD_MAP = {
    "photo_exterior_driver_side": 38,
    "photo_engine_driver_side": 39,
    "photo_cab": 40,
    "photo_engine_passenger_side": 46,
    "photo_exterior_front": 47,
    "photo_exterior_passenger_side": 48,
    "photo_exterior_rear": 49,
}

PHOTO_VIEW_LABELS = {
    "photo_exterior_driver_side": "Exterior Driver Side",
    "photo_engine_driver_side": "Engine Driver Side",
    "photo_cab": "Interior View of the truck",
    "photo_engine_passenger_side": "Engine Passenger Side",
    "photo_exterior_front": "Exterior Front",
    "photo_exterior_passenger_side": "Exterior Passenger Side",
    "photo_exterior_rear": "Exterior Rear",
}

async def validate_images(rec: dict, gpt_vision_url: str):
    """
    Extract image URLs, call OpenCLIP, and return missing views.
    Returns:
    image_urls: list
    missing_views: list
    """

    record_id = rec.get("record_id")
    table_id = settings.QB_TABLE_ID

    # 1️⃣ Construct relative Quickbase API URLs for each valid image field
    image_urls = []
    for key, field_id in PHOTO_FIELD_MAP.items():
        if rec.get(key) not in (None, "", "null"):
            # Format: files/{table_id}/{record_id}/{field_id}/1
            url = f"files/{table_id}/{record_id}/{field_id}/1"
            image_urls.append(url)

    # Case 1: No images at all (or if we couldn't map them)
    if not image_urls:
        return [], [
            "Exterior Front",
            "Exterior Driver Side",
            "Exterior Passenger Side",
            "Engine Driver Side",
            "Engine Passenger Side",
            "Exterior Rear",
            "Interior View of the truck"
        ]

    # 2️⃣ Send to GPT VISION for classification
    async with httpx.AsyncClient(timeout=60) as client:
        # We pass the full structure to the model endpoint
        clip_response = await client.post(gpt_vision_url, json=image_urls)
        clip_data = clip_response.json()

    missing_views = clip_data.get("missing", [])

    return image_urls, missing_views
