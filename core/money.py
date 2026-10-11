"""Deterministic money rounding and display formatting (Decimal only, never binary floats)."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_UNITS: tuple[tuple[Decimal, str, str, Decimal], ...] = (
    # (size, suffix, decimal places, smallest amount shown in this unit). The thresholds sit half
    # a display step below each unit so rounding can never produce "$1,000K" or "$1.00M" as a
    # step down: 999,600 is $1.00M, 999,400 is $999K.
    (Decimal(10) ** 9, "B", "0.01", Decimal(999_500_000)),
    (Decimal(10) ** 6, "M", "0.01", Decimal(999_500)),
    (Decimal(10) ** 3, "K", "1", Decimal(1000)),
)
_WHOLE = Decimal(1)


def to_decimal(value: object) -> Decimal:
    """Convert a number or numeric string to Decimal; floats go through their repr (4.7 -> 4.7)."""
    if isinstance(value, bool):
        raise ValueError("expected a number, not a boolean")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        try:
            result = Decimal(value.strip())
        except InvalidOperation:
            raise ValueError(f"not a number: {value!r}") from None
    elif isinstance(value, float):
        result = Decimal(repr(value))
    else:
        raise ValueError(f"expected a number, got {type(value).__name__}")
    if not result.is_finite():
        raise ValueError("expected a finite number")
    return result


def plain(value: Decimal) -> str:
    """Decimal without exponent or trailing zeros: 7.00 -> '7', 4.70 -> '4.7'."""
    text = format(value.normalize(), "f")
    return text if text not in ("-0", "") else "0"


def _group(value: Decimal, places: str) -> str:
    return f"{value.quantize(Decimal(places), rounding=ROUND_HALF_UP):,}"


def format_money_compact(amount: Decimal, *, trim: bool = False) -> str:
    """Compact display used for large figures: $638K, $1.06M, $2.13B (half-up rounding).

    The unit is chosen after rounding, so 999,600 becomes $1.00M rather than $1,000K.
    `trim` drops trailing zeros ($10.00M -> $10M).
    """
    if amount < 0:
        raise ValueError("negative amounts are not supported")
    for size, suffix, places, threshold in _UNITS:
        if amount < threshold:
            continue
        scaled = (amount / size).quantize(Decimal(places), rounding=ROUND_HALF_UP)
        text = f"{scaled:,}"
        if trim and "." in text:
            text = text.rstrip("0").rstrip(".")
        return f"${text}{suffix}"
    return f"${_group(amount, '1')}"


def format_money_label(amount: Decimal) -> str:
    """Exact label for an input amount: $30K for 30,000 but $32,500 (never a rounded $33K)."""
    if amount < 0:
        raise ValueError("negative amounts are not supported")
    if amount >= 1000 and amount % 1000 == 0:
        return format_money_compact(amount, trim=True)
    places = "1" if amount == amount.to_integral_value() else "0.01"
    return f"${_group(amount, places)}"


def format_money_whole(amount: Decimal, rounding: str = ROUND_HALF_UP) -> str:
    """Whole dollars with thousands separators: $1,234."""
    try:
        return f"${amount.quantize(_WHOLE, rounding=rounding):,}"
    except InvalidOperation as exc:  # amount too large for the context precision
        raise ValueError(f"cannot format {amount}") from exc
