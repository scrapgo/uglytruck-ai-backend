"""
Remember Quickbase status changes this backend made itself, so the Replace webhook that Quickbase
fires back for them is not mistaken for a human change and re-run (which used to send the seller
the same "missing photos" email twice). In-memory is enough: the app runs as one uvicorn process.
"""
import time
from typing import Dict, Tuple

_SELF_UPDATES: Dict[Tuple[int, str], float] = {}
_MAX_AGE_SECONDS = 15 * 60


def mark_self_status_update(record_id: int, status: str) -> None:
    _SELF_UPDATES[(int(record_id), status)] = time.monotonic()


def consume_self_status_update(record_id: int, status: str) -> bool:
    """True (once) if this backend recently set `status` on `record_id`."""
    now = time.monotonic()
    for key, ts in list(_SELF_UPDATES.items()):
        if now - ts > _MAX_AGE_SECONDS:
            _SELF_UPDATES.pop(key, None)
    return _SELF_UPDATES.pop((int(record_id), status), None) is not None
