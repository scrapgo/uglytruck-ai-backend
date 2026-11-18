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


# --- Missing-view check: a side pair labelled the same side twice ------------------------------
# 2026-11-04: the classifier can put both photos of a pair on one side (driver and passenger side
# photos both called "Exterior Passenger Side"), and the seller was told the driver side was
# missing. Two DIFFERENT photos on one side, with the other side empty, mean both sides were sent.
# The same photo uploaded twice (near-identical image hash) never counts as the second side.
SIDE_PAIRS = (
    ("Exterior Driver Side", "Exterior Passenger Side"),
    ("Engine Driver Side", "Engine Passenger Side"),
)
DUPLICATE_MAX_DISTANCE = 10  # of 64 bits: closer than this is the same photo


def image_hash(image) -> int:
    """64-bit difference hash of a PIL image: survives resizing and recompression."""
    small = image.convert("L").resize((9, 8))
    px = list(small.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return bits


def _distinct(hashes: List[Optional[int]]) -> bool:
    """True when at least two of these photos are different pictures."""
    known = [h for h in hashes if h is not None]
    if len(hashes) >= 2 and len(known) < 2:
        return False  # can't tell: assume the same photo
    return any(bin(a ^ b).count("1") > DUPLICATE_MAX_DISTANCE for i, a in enumerate(known) for b in known[i + 1:])


def found_views(view_results: List[Dict]) -> set:
    """Labels of the photos classified as a truck view. Unlabelled (not a truck) and failed photos are skipped."""
    return {r["predicted_label"] for r in view_results if r.get("predicted_label")}


def fill_side_pairs(view_results: List[Dict], found_labels: set) -> List[str]:
    """
    Add the empty side of a pair to found_labels when two different photos were both put on the
    other side. view_results: per-photo dicts with 'predicted_label' and optional 'image_hash'.
    Returns the labels added (for logging).
    """
    added = []
    for a, b in SIDE_PAIRS:
        for have, empty in ((a, b), (b, a)):
            if empty in found_labels or have not in found_labels:
                continue
            hashes = [r.get("image_hash") for r in view_results if r.get("predicted_label") == have and "error" not in r]
            if _distinct(hashes):
                found_labels.add(empty)
                added.append(empty)
    return added
