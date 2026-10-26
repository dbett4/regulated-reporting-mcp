"""Pure, exact arithmetic shared by reporting checks."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any, Iterable

SCALES = {"units": Decimal(1), "thousands": Decimal(1000), "millions": Decimal(1000000)}
_NUMBER = re.compile(r"-?(?:\d{1,30}|\d{1,3}(?:,\d{3}){1,9})(?:\.\d{1,12})?\Z")


def number(value: Any) -> Decimal:
    """Parse the deliberately narrow numeric representation used by tie-outs."""
    if (
        isinstance(value, bool)
        or isinstance(value, (datetime, date))
        or not isinstance(value, (str, int, float, Decimal))
    ):
        raise ValueError("missing or unsupported numeric value")
    text = str(value).strip()
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    if not _NUMBER.fullmatch(text):
        raise ValueError("missing or unsupported numeric value")
    return Decimal(text.replace(",", ""))


def signed_sum(
    reported_value: str,
    supporting: Iterable[tuple[str, str]],
    decimal_places: int,
) -> tuple[Decimal, Decimal, Decimal]:
    """Round reported and the signed supporting sum, then return their difference."""
    with localcontext() as context:
        context.prec = 80
        quantum = Decimal(1).scaleb(-decimal_places)
        reported = Decimal(reported_value).quantize(quantum, rounding=ROUND_HALF_UP)
        expected = sum(
            (Decimal(value) * (1 if sign == "add" else -1) for value, sign in supporting),
            Decimal(0),
        ).quantize(quantum, rounding=ROUND_HALF_UP)
        return reported, expected, reported - expected
