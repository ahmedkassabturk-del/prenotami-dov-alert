"""Pure DOV row detection logic, kept separate so it can be unit-tested."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Iterable, Literal


DetectionStatus = Literal["available", "unavailable", "not_found", "unknown"]


@dataclass(frozen=True)
class RowSnapshot:
    """Only the non-sensitive fields needed to classify one services-table row."""

    row_text: str
    booking_text: str
    has_booking_control: bool = False
    has_enabled_booking_control: bool = False
    booking_control_text: str = ""


@dataclass(frozen=True)
class DetectionResult:
    status: DetectionStatus
    booking_text: str = ""
    reason: str = ""


def normalize_text(value: str) -> str:
    """Normalize accents, case and whitespace for English/Italian matching."""

    decomposed = unicodedata.normalize("NFKD", value or "")
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", without_marks).strip().casefold()


def _is_target_row(text: str) -> bool:
    normalized = normalize_text(text)

    exact_markers = (
        "dov only for universities",
        "dov only for university",
        "dov solo per universita",
        "dov esclusivamente per universita",
        "dov esclusivamente per le universita",
    )
    if any(marker in normalized for marker in exact_markers):
        return True

    university_markers = ("universit", "university", "universities")
    return "dov" in normalized and any(marker in normalized for marker in university_markers)


def _is_unavailable(text: str) -> bool:
    normalized = normalize_text(text)
    markers = (
        "booking calendar not yet available",
        "booking calendar unavailable",
        "calendar not yet available",
        "calendar unavailable",
        "not yet available",
        "calendario prenotazioni non ancora disponibile",
        "calendario non ancora disponibile",
        "calendario prenotazioni non disponibile",
        "calendario non disponibile",
        "non ancora disponibile",
    )
    return any(marker in normalized for marker in markers)


def _looks_like_booking_action(text: str) -> bool:
    normalized = normalize_text(text)
    action_words = ("book", "booking", "prenota", "prenotazione")
    return any(word in normalized for word in action_words)


def detect_dov_status(rows: Iterable[RowSnapshot]) -> DetectionResult:
    """Classify the DOV-only row without ever clicking its booking control."""

    for row in rows:
        if not _is_target_row(row.row_text):
            continue

        booking_text = (row.booking_text or row.booking_control_text).strip()

        # The external "Link 1" is in a different table cell. The caller only
        # reports controls found inside the Booking cell, preventing a false alert.
        if row.has_enabled_booking_control and _looks_like_booking_action(
            f"{row.booking_text} {row.booking_control_text}"
        ):
            return DetectionResult(
                status="available",
                booking_text=booking_text,
                reason="enabled booking control found in the Booking cell",
            )

        if _is_unavailable(row.booking_text):
            return DetectionResult(
                status="unavailable",
                booking_text=booking_text,
                reason="the Booking cell says the calendar is unavailable",
            )

        if not row.has_booking_control and _looks_like_booking_action(row.booking_text):
            return DetectionResult(
                status="available",
                booking_text=booking_text,
                reason="the Booking cell contains a booking action rendered as text",
            )

        return DetectionResult(
            status="unknown",
            booking_text=booking_text,
            reason="the target row was found but the Booking cell was unfamiliar",
        )

    return DetectionResult(
        status="not_found",
        reason="the DOV-only-for-universities row was not found",
    )
