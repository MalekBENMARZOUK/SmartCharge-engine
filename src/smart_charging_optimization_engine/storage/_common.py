from __future__ import annotations

import re

from smart_charging_optimization_engine.exceptions import InvalidIdentifierError

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")


def validate_item_id(item_id: str) -> str:
    if not _IDENTIFIER_PATTERN.fullmatch(item_id):
        msg = (
            "Identifiers must start with an alphanumeric character and contain only "
            "letters, digits, '.', '_' or '-' up to 255 characters"
        )
        raise InvalidIdentifierError(msg)
    return item_id
