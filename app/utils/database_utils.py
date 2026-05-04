import json
import re

def format_record(record: dict):
    formatted = {}
    for key, value in record.items():
        # Handle None
        if value in (None, "null", "None"):
            formatted[key] = "—"
            continue

        # Handle JSON strings
        if isinstance(value, str) and value.strip().startswith("{") and value.strip().endswith("}"):
            try:
                obj = json.loads(value)
                formatted[key] = ", ".join(f"{k}: {v}" for k, v in obj.items())
                continue
            except Exception:
                pass

        # Handle HTML <img> tags
        if isinstance(value, str) and "<img" in value:
            match = re.search(r"src=['\"]?([^'\">]+)['\"]?", value)
            if match:
                formatted[key] = match.group(1)  # Just send image URL
                continue

        # Handle long URLs
        if isinstance(value, str) and value.startswith("http"):
            formatted[key] = value #{"url": value}
            continue

        # Default fallback
        formatted[key] = value
    return formatted
