"""
Pure decision logic for photos a seller emails back in reply to a "Missing Truck Photos" reminder.

No I/O here so it can be unit-tested without a database, Quickbase or OpenAI.
"""
import re
from typing import Dict, List, Optional

from app.validators.image_validation import ALL_REQUIRED_VIEWS, PHOTO_FIELD_MAP, PHOTO_VIEW_LABELS

# "Engine Driver Side" -> "photo_engine_driver_side" / 39
LABEL_TO_COLUMN: Dict[str, str] = {label: col for col, label in PHOTO_VIEW_LABELS.items()}
LABEL_TO_FIELD_ID: Dict[str, int] = {label: PHOTO_FIELD_MAP[col] for col, label in PHOTO_VIEW_LABELS.items()}

# Driver/passenger orientation is the least reliable part of the classifier (the OpenCLIP hint is
# close to a coin flip on engine bays). When the reminder asked for one side and the seller sent a
# photo the classifier put on the other side, trust the reminder.
SIBLING_VIEWS = {
    "Engine Driver Side": "Engine Passenger Side",
    "Engine Passenger Side": "Engine Driver Side",
    "Exterior Driver Side": "Exterior Passenger Side",
    "Exterior Passenger Side": "Exterior Driver Side",
}

# Phrases from app/utils/templates/missing_truck_template.html that bracket the list of views.
_LIST_START_MARKERS = ("following views", "photos are missing")
_LIST_END_MARKERS = ("adding these photos", "best regards")


def _to_plain_text(text: str) -> str:
    """Lower-case, strip HTML tags and quote markers, collapse whitespace."""
    text = re.sub(r"<[^>]+>", "\n", text or "")
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = re.sub(r"[>*_|]+", " ", text)          # Gmail/Outlook quote prefixes, markdown bullets
    return re.sub(r"\s+", " ", text).strip().lower()


def parse_requested_views(text: Optional[str]) -> List[str]:
    """
    Return the truck views listed in a "Missing Truck Photos" reminder, in canonical order.
    Works on the stored plain-text copy of the email we sent, on the HTML, and on the reminder
    quoted inside the seller's reply. Returns [] when the text is not a reminder.
    """
    plain = _to_plain_text(text)
    if not plain:
        return []

    start = -1
    for marker in _LIST_START_MARKERS:
        start = plain.find(marker)
        if start != -1:
            break
    if start == -1:
        return []

    end = len(plain)
    for marker in _LIST_END_MARKERS:
        pos = plain.find(marker, start)
        if pos != -1:
            end = min(end, pos)
    segment = plain[start:end]

    return [label for label in ALL_REQUIRED_VIEWS if label.lower() in segment]


def select_uploads(
    classification_results: List[Dict],
    requested_views: Optional[List[str]],
    existing_files: Optional[set] = None,
) -> List[Dict]:
    """
    Decide which replied photos to upload.

    classification_results: output of SmartImageClassifier.classify_and_map_images
                            (dicts with 'attachment', 'label', ...), in attachment order.
    requested_views:        views listed in the reminder the seller replied to, or None when no
                            reminder could be found.
    existing_files:         DB columns that already hold a backend/Dale-uploaded file (used only
                            in the no-reminder fallback, so we never overwrite one blindly).

    Returns one decision per attachment: the input dict plus
        should_upload (bool), field_id, basis (why it was matched) and reason (why not).
    When a reminder is known, ONLY photos matching a requested view are uploaded, one per view,
    and the view's old photo is replaced. Anything else the seller attached is left alone.
    """
    existing_files = existing_files or set()
    decisions = [dict(r, should_upload=False, field_id=None, basis=None, reason=None) for r in classification_results]

    if requested_views is None:
        # Fallback: no reminder found. Upload any classified view whose file slot is still empty.
        taken = set()
        for d in decisions:
            label = d.get("label")
            if not label:
                d["reason"] = "Classification failed"
            elif label in taken:
                d["reason"] = "Duplicate view in this email"
            elif LABEL_TO_COLUMN.get(label) in existing_files:
                d["reason"] = "A file already exists for this view"
            else:
                d.update(should_upload=True, field_id=LABEL_TO_FIELD_ID[label], basis="content classification", reason="Ready to upload")
                taken.add(label)
        return decisions

    requested = list(requested_views)
    pending = set(requested)

    # Pass 1: exact label matches, first photo per view wins.
    for d in decisions:
        label = d.get("label")
        if label in pending:
            d.update(should_upload=True, field_id=LABEL_TO_FIELD_ID[label], basis="matches the reminder", reason="Requested in reminder")
            pending.discard(label)

    # Pass 2: driver/passenger sibling of a still-pending view.
    for d in decisions:
        label = d.get("label")
        if d["should_upload"] or not label or label in requested:
            continue
        sibling = SIBLING_VIEWS.get(label)
        if sibling in pending:
            d.update(label=sibling, should_upload=True, field_id=LABEL_TO_FIELD_ID[sibling],
                     basis=f"classifier said '{label}', reminder asked for '{sibling}' (side assumed from reminder)",
                     reason="Requested in reminder")
            pending.discard(sibling)

    # Pass 3: exactly one photo answering a reminder that asked for exactly one view.
    if len(pending) == 1 and len(decisions) == 1 and not decisions[0]["should_upload"]:
        only = next(iter(pending))
        decisions[0].update(label=only, should_upload=True, field_id=LABEL_TO_FIELD_ID[only],
                            basis="only photo in the reply to a single-view reminder", reason="Requested in reminder")
        pending.discard(only)

    for d in decisions:
        if d["should_upload"]:
            continue
        label = d.get("label")
        if not label:
            d["reason"] = "Classification failed"
        elif label in requested:
            d["reason"] = f"Another photo in this email already covered '{label}'"
        else:
            d["reason"] = f"'{label}' was not requested in the reminder email"
    return decisions


def remaining_requested_views(requested_views: List[str], uploaded_views: List[str]) -> List[str]:
    """Views the reminder asked for that this reply did not cover."""
    uploaded = set(uploaded_views)
    return [v for v in requested_views if v not in uploaded]
