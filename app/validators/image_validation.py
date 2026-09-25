import logging
import httpx
from app.backend.config import settings

# Mapping of DB column names to Quickbase Field IDs
# 38 (driver), 39 (engine driver), 40 (cab), 46 (engine pass), 47 (front), 48 (pass), 49 (rear)
PHOTO_FIELD_MAP = {
    "photo_exterior_driver_side": 38,
    "photo_engine_driver_side": 39,
    "photo_cab": 40,
    "photo_engine_passenger_side": 46,
    "photo_exterior_front": 47,
    "photo_exterior_passenger_side": 48,
    "photo_exterior_rear": 49,
}

# Quickbase website URL field IDs (from Gravity Forms on uglytruck.net)
WEBSITE_PHOTO_FIELD_MAP = {
    "photo_exterior_driver_side": 163,
    "photo_engine_driver_side": 164,
    "photo_cab": 165,
    "photo_engine_passenger_side": 166,
    "photo_exterior_front": 167,
    "photo_exterior_passenger_side": 168,
    "photo_exterior_rear": 169,
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

ALL_REQUIRED_VIEWS = [
    "Exterior Front",
    "Exterior Driver Side",
    "Exterior Passenger Side",
    "Engine Driver Side",
    "Engine Passenger Side",
    "Exterior Rear",
    "Interior View of the truck"
]

async def validate_images(rec: dict, gpt_vision_url: str):
    """
    Extract image URLs (supporting both Quickbase file attachments and direct website URLs),
    call Vision classifier, and return missing views.
    Returns:
        image_urls: list
        missing_views: list
    """
    record_id = rec.get("record_id")
    table_id = settings.QB_TABLE_ID or "br5nbqyfg"

    # 1️⃣ If rec has missing photos, try fetching from Quickbase (including 'from website' fields)
    has_any_photo = any(rec.get(key) not in (None, "", "null") for key in PHOTO_FIELD_MAP)
    if not has_any_photo and record_id:
        try:
            from app.quickbase.webhook_operations import get_record_photo_status
            logging.info(f"[validate_images] 🔍 No photos in rec for record_id={record_id}. Querying Quickbase photo fields...")
            qb_photos = await get_record_photo_status(int(record_id))
            if qb_photos:
                for k, v in qb_photos.items():
                    if v and not rec.get(k):
                        rec[k] = v
                logging.info(f"[validate_images] 📸 Fetched photos from Quickbase for record_id={record_id}: {[k for k, v in qb_photos.items() if v]}")
        except Exception as e:
            logging.warning(f"[validate_images] ⚠️ Could not fetch photos from Quickbase for record {record_id}: {e}")

    # 2️⃣ Construct image URLs (support direct HTTP URLs from website!)
    image_urls = []
    views_with_urls = {}
    for key, field_id in PHOTO_FIELD_MAP.items():
        val = rec.get(key)
        # Check fallback to website-specific key names if column wasn't populated
        if not val or val in ("None", "null"):
            val = (
                rec.get(f"{key}_from_website") or 
                rec.get(f"Photo {PHOTO_VIEW_LABELS.get(key)} from website")
            )

        if val not in (None, "", "null"):
            val_str = str(val).strip()
            if val_str.startswith("http://") or val_str.startswith("https://"):
                url = val_str
            else:
                url = f"files/{table_id}/{record_id}/{field_id}/1"
            image_urls.append(url)
            views_with_urls[PHOTO_VIEW_LABELS[key]] = url

    logging.info(f"[validate_images] 📸 Extracted {len(image_urls)} image URL(s) for record_id={record_id}: {image_urls}")

    # Case 1: No images at all
    if not image_urls:
        return [], ALL_REQUIRED_VIEWS

    # 3️⃣ Send to GPT VISION for classification
    missing_views = []
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            clip_response = await client.post(gpt_vision_url, json=image_urls)
            if clip_response.status_code == 200:
                clip_data = clip_response.json()
                missing_views = clip_data.get("missing", [])
                logging.info(f"[validate_images] 🎯 GPT Vision returned missing_views={missing_views} for record_id={record_id}")
            else:
                logging.error(f"[validate_images] ❌ GPT Vision call failed ({clip_response.status_code}): {clip_response.text}")
                # Fallback: report only views that had no photo uploaded at all
                all_views = set(ALL_REQUIRED_VIEWS)
                uploaded_views = set(views_with_urls.keys())
                missing_views = list(all_views - uploaded_views)
    except Exception as e:
        logging.error(f"[validate_images] ❌ Exception calling GPT Vision: {e}")
        all_views = set(ALL_REQUIRED_VIEWS)
        uploaded_views = set(views_with_urls.keys())
        missing_views = list(all_views - uploaded_views)

    return image_urls, missing_views
