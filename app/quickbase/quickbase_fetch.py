from datetime import datetime, timedelta
import requests
import json
from app.backend.config import settings
from typing import Optional




# --- Function 1: Fetch all tables in the app ---
def fetch_tables(app_id: str = settings.QB_APP_ID):
    url = f"{settings.BASE_URL}/tables"
    params = {"appId": app_id}

    print("🔍 Fetching tables...")
    response = requests.get(url, headers=settings.HEADERS, params=params)

    if response.status_code != 200:
        print("❌ Error:", response.text)
        return None

    data = response.json()
    print("✅ Tables fetched successfully!\n")

    # ✅ Handle both list and dict responses
    if isinstance(data, list):
        for table in data:
            print(f"📋 {table.get('name')} — Alias: {table.get('alias')} — ID: {table.get('id')}")
    elif isinstance(data, dict) and "tables" in data:
        for table in data["tables"]:
            print(f"📋 {table.get('name')} — Alias: {table.get('alias')} — ID: {table.get('id')}")
    else:
        print("⚠️ Unexpected response format:")
        print(json.dumps(data, indent=2))

    return data


def fetch_table_records_with_labels(table_id: str, top: int = 0, last_sync_date: Optional[str]=None):
    """Fetch records from Quickbase with field labels, optionally after a given modified date."""

    url = f"{settings.BASE_URL}/records/query"

    # Build Quickbase query payload
    payload = {
        "from": table_id,
        "select": [3, 1, 2, 5, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 41, 25, 26, 27, 28, 29, 30, 31, 32, 33, 24, 38, 39, 40, 46, 47, 48, 49, 53, 52, 51, 45, 64, 67, 77, 50, 106, 130, 137, 138, 139, 140, 146, 147, 148, 149, 158]
,  # empty = all fields
        "options": {"skip": 0},
        "sortBy": [{"fieldId": 3, "order": "DESC"}]
    }
    if top == 0:
        payload["options"] = {"skip":0}

    # Filter: only fetch records modified after last_sync_date
    if last_sync_date:
        #payload["where"] = f"{{'Date Modified'.AF.'{last_sync_date}'}}"
        payload["where"] = f"{{2.AF.'{last_sync_date}'}}"
    print("Payload:", json.dumps(payload, indent=2))
    print(f"\n🔍 Fetching records from Quickbase (modified after {last_sync_date})...")

    response = requests.post(url, headers=settings.HEADERS, json=payload)
    if response.status_code != 200:
        print("❌ Error fetching records:", response.text)
        return None

    table_data = response.json()

    # --- Fetch field metadata ---
    meta_url = f"{settings.BASE_URL}/fields?tableId={table_id}"
    meta_response = requests.get(meta_url, headers=settings.HEADERS)

    field_map = {}
    if meta_response.status_code == 200:
        for field in meta_response.json():
            field_map[str(field.get("id"))] = field.get("label")
    else:
        print("⚠️ Warning: Couldn't fetch field metadata, using raw field IDs")

    # --- Map field IDs → labels ---
    output = []
    for record in table_data.get("data", []):
        labeled_record = {}
        for key, value in record.items():
            label = field_map.get(str(key), key)
            labeled_record[label] = value.get("value") if isinstance(value, dict) else value
        output.append(labeled_record)

    print(f"✅ Retrieved {len(output)} records (mapped with labels)\n")

    # Pretty print first few records
    for i, rec in enumerate(output[:5], start=1):
        print(f"Record {i}: {json.dumps(rec, indent=2)}")
        print("-" * 40)

    return output


