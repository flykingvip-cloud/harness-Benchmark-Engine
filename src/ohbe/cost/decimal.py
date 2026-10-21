"""Decimal strings for prices. Floats are not accepted."""

from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_EVEN

_AMOUNT_RE = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.\d+)?$")


def parse_decimal(value: str, *, field: str) -> Decimal:
    """Parse a base-10 decimal string. Rejects floats and scientific notation."""

    if not isinstance(value, str) or _AMOUNT_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a base-10 decimal string")
    number = Decimal(value)
    if number == 0 and value.startswith("-"):
        raise ValueError(f"{field} must not be negative zero")
    return number


def format_amount(value: Decimal) -> str:
    """Canonical money string. A real zero is `0`, never a padded float."""

    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("amount must be a finite Decimal")
    quantized = value.quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN)
    if quantized == 0:
        return "0"
    text = format(quantized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text.startswith("."):
        text = "0" + text
    if text.startswith("-."):
        text = "-0" + text[1:]
    return text


def format_rate(value: Decimal) -> str:
    """Six decimal places, half-even, for rates and interval endpoints."""

    quantized = value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)
    return format(quantized, "f")
