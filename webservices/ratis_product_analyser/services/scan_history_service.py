"""User-facing paginated scan history — unified receipt + label-group entries.

ARCH : `ratis_client/ARCH_scan_history.md` (section "Endpoints backend").

Shape returned by ``list_scan_history`` :
```
{
  "entries": [
    {"type": "receipt", "receipt_id": ..., "scanned_at": ..., "store_name": ...,
     "store_status": ..., "total_amount_cents": ..., "matched_count": ...,
     "unmatched_count": ..., "pending_count": ...},
    {"type": "label_group", "group_key": "<store_id>|<YYYY-MM-DD>",
     "store_id": ..., "date": "YYYY-MM-DD", "store_name": ...,
     "latest_scanned_at": ..., "accepted_count": ...},
  ],
  "next_cursor": "opaque-base64-string-or-null"
}
```
Rejected scans are always excluded. Entries ordered most-recent-activity DESC.
Cursor pagination is keyset on ``(latest_activity_at, disambiguator)``.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from datetime import datetime

from fastapi import HTTPException
from repositories.scan_repository import list_user_history_entries
from sqlalchemy.orm import Session


def _encode_cursor(activity_at: datetime, disambiguator: str) -> str:
    payload = json.dumps(
        {"a": activity_at.isoformat(), "d": disambiguator},
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        data = json.loads(raw)
        return datetime.fromisoformat(data["a"]), str(data["d"])
    except (ValueError, binascii.Error, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="invalid_cursor") from exc


def list_scan_history(
    db: Session,
    *,
    user_id: uuid.UUID,
    limit: int,
    cursor: str | None,
) -> dict:
    """Return ``{entries: [...], next_cursor: <str|None>}`` — see module docstring."""
    cursor_activity_at: datetime | None = None
    cursor_disambiguator: str | None = None
    if cursor is not None:
        cursor_activity_at, cursor_disambiguator = _decode_cursor(cursor)

    rows = list_user_history_entries(
        db,
        user_id=user_id,
        limit=limit,
        cursor_activity_at=cursor_activity_at,
        cursor_disambiguator=cursor_disambiguator,
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    entries = [_row_to_entry(r) for r in page]

    next_cursor: str | None = None
    if has_more and page:
        last = page[-1]
        next_cursor = _encode_cursor(last["latest_activity_at"], last["disambiguator"])

    return {"entries": entries, "next_cursor": next_cursor}


def _row_to_entry(row: dict) -> dict:
    """Map a merged SQL row to the public entry shape."""
    activity_at = row["latest_activity_at"]
    activity_iso = activity_at.isoformat() if activity_at else None
    if row["type"] == "receipt":
        return {
            "type": "receipt",
            "receipt_id": str(row["receipt_id"]),
            "scanned_at": activity_iso,
            "store_name": row["store_name"],
            "store_status": row["store_status"],
            "total_amount_cents": row["total_amount_cents"],
            "matched_count": int(row["matched_count"] or 0),
            "unmatched_count": int(row["unmatched_count"] or 0),
            "pending_count": int(row["pending_count"] or 0),
        }
    # label_group — disambiguator is "<store_id>|YYYY-MM-DD"
    disambiguator: str = row["disambiguator"]
    store_id_str, date_str = disambiguator.split("|", 1)
    return {
        "type": "label_group",
        "group_key": disambiguator,
        "store_id": store_id_str,
        "date": date_str,
        "store_name": row["store_name"],
        "latest_scanned_at": activity_iso,
        "accepted_count": int(row["matched_count"] or 0),
    }
