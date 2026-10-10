"""Helpers for values stored in or returned as strict JSON."""
from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any


def json_safe(value: Any) -> Any:
    """Recursively convert non-finite and common scalar values to JSON-safe values.

    PostgreSQL JSONB rejects NaN and infinities even though Python's default JSON
    encoder emits them. Non-finite numbers become ``None`` (JSON ``null``).
    """
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, (float, Decimal)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]

    # Support NumPy scalar/array types without importing NumPy in core utilities.
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        return json_safe(tolist())
    item = getattr(value, "item", None)
    if callable(item):
        return json_safe(item())
    return value
