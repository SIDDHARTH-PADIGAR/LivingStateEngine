## timestamps must include a timezone. All event timestamps are normalised to UTC,
## making chronological sorting deterministic. The content hash lets us
## distinguish a true duplicate from an even ID being reused for different evidenc.


from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


SUPPORTED_EVENT_TYPES = frozenset(
    {
        "project_update",
        "status_update",
        "commitment_created",
        "commitment_completed",
        "risk_raised",
        "risk_resolved",
        "issue_raised",
        "issue_resolved",
        "customer_signal",
        "decision",
        "outcome",
    }
)


class EventValidationError(ValueError):
    """Raised when an event does not satisfy the event contract."""


def normalize_timestamp(value: str | datetime) -> str:
    """Convert a timezone-aware timestamp into a sortable UTC string."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise EventValidationError(
                f"Invalid ISO 8601 timestamp: {value!r}"
            ) from exc

    if not isinstance(value, datetime):
        raise EventValidationError(
            "occurred_at must be a datetime or ISO 8601 string"
        )

    if value.tzinfo is None or value.utcoffset() is None:
        raise EventValidationError(
            "occurred_at must include a timezone"
        )

    return value.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    )


@dataclass(frozen=True, slots=True)
class Event:
    """An immutable, validated project event."""

    event_id: str
    project_id: str
    occurred_at: str | datetime
    event_type: str
    summary: str
    source: str = "manual"
    actor: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "event_id",
            "project_id",
            "event_type",
            "summary",
            "source",
        ):
            value = getattr(self, field_name)

            if not isinstance(value, str) or not value.strip():
                raise EventValidationError(
                    f"{field_name} must be a non-empty string"
                )

        if self.event_type not in SUPPORTED_EVENT_TYPES:
            raise EventValidationError(
                f"Unsupported event_type: {self.event_type!r}"
            )

        if self.actor is not None and not self.actor.strip():
            raise EventValidationError(
                "actor must be non-empty when provided"
            )

        if not isinstance(self.payload, Mapping):
            raise EventValidationError(
                "payload must be a mapping"
            )

        try:
            normalized_payload = json.loads(
                json.dumps(
                    dict(self.payload),
                    sort_keys=True,
                    allow_nan=False,
                )
            )
        except (TypeError, ValueError) as exc:
            raise EventValidationError(
                "payload must contain valid JSON-compatible values"
            ) from exc

        object.__setattr__(
            self,
            "occurred_at",
            normalize_timestamp(self.occurred_at),
        )
        object.__setattr__(self, "payload", normalized_payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "project_id": self.project_id,
            "occurred_at": self.occurred_at,
            "event_type": self.event_type,
            "summary": self.summary,
            "source": self.source,
            "actor": self.actor,
            "payload": dict(self.payload),
        }

    def to_canonical_json(self) -> str:
        """Serialize the event consistently, independent of key order."""
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )

    @property
    def content_hash(self) -> str:
        """Fingerprint the full event for duplicate detection."""
        return hashlib.sha256(
            self.to_canonical_json().encode("utf-8")
        ).hexdigest()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Event:
        return cls(**dict(data))
