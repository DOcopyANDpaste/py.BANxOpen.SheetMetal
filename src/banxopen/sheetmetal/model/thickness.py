"""The one thickness comparison and the one way a thickness is written. SPEC validation, allowed grades, the
preferences check, the row filter and the requirements all use these, so they cannot disagree."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Final

TOLERANCE_INCHES: Final = 0.0005
"""NX thickness values come from a model measurement, so exact equality is too brittle."""


def thickness_matches(a: float, b: float) -> bool:
    return abs(a - b) <= TOLERANCE_INCHES


def format_thickness(thickness: float) -> str:
    """Up to four decimals, trailing zeros dropped ("0.04", "0.0625"), invariant: requirement text is persisted. The
    C# `0.####` format, which rounds half away from zero."""
    text = format(_rounded(thickness, "0.0001"), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def format_key_thickness(thickness: float) -> str:
    """Fixed four decimals ("0.0400"), as the NX formula the pair requirement is checked against writes it."""
    return format(_rounded(thickness, "0.0000"), "f")


def format_short(thickness: float) -> str:
    """Up to three decimals, for status lines (the C# `0.###`)."""
    text = format(_rounded(thickness, "0.001"), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _rounded(value: float, places: str) -> Decimal:
    return Decimal(repr(value)).quantize(Decimal(places), rounding=ROUND_HALF_UP)
