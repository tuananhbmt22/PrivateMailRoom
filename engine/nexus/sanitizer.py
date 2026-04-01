"""PII sanitizer for Nexus Forge schema JSON.

Walks a JSON structure and replaces values in known PII fields
with placeholder tags. Deterministic — no LLM needed.
"""

from __future__ import annotations

import re
from typing import Any

# Field names that contain PII — mapped to their replacement tag
PII_FIELD_MAP: dict[str, str] = {
    # Names
    "firstName": "[Name]",
    "familyName": "[Name]",
    "otherName": "[Name]",
    "companyName": "[Company]",
    "tradingName": "[Company]",
    "payerCompany": "[Company]",
    # Addresses
    "address": "[Address]",
    "billingAddress": "[Address]",
    "streetName": "[Street]",
    "streetNumber1": "[No.]",
    "streetNumber2": "[No.]",
    "suburb": "[Suburb]",
    "postCode": "[Postcode]",
    "complexUnitIdentifier": "[Unit]",
    # Contact
    "email": "[Email]",
    "emailAddress": "[Email]",
    "contactNumber": "[Phone]",
    # IDs and references
    "ABN": "[ABN]",
    "ACN": "[ACN]",
    "councilDANumber": "[Ref]",
    "councilReferenceNumber": "[Ref]",
    "basixCertificateNumber": "[Ref]",
    "gurasID": "[ID]",
    "cadastralID": "[ID]",
    "documentCaseID": "[ID]",
    # Coordinates
    "latitude": 0.0,
    "longitude": 0.0,
    # URLs
    "documentURL": "[URL]",
}

# Patterns for values that look like PII regardless of field name
EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
PHONE_PATTERN = re.compile(r"\b(?:\+?61|0)\d[\d\s-]{7,12}\b")
ABN_PATTERN = re.compile(r"\b\d{2}\s?\d{3}\s?\d{3}\s?\d{3}\b")


def sanitize_value(key: str, value: Any) -> Any:
    """Sanitize a single value based on its field name."""
    if key in PII_FIELD_MAP:
        replacement = PII_FIELD_MAP[key]
        if isinstance(replacement, str) and isinstance(value, str) and value:
            return replacement
        if isinstance(replacement, (int, float)) and isinstance(value, (int, float)):
            return replacement
    # Pattern-based detection for string values not caught by field name
    if isinstance(value, str) and value:
        if EMAIL_PATTERN.fullmatch(value.strip()):
            return "[Email]"
        if PHONE_PATTERN.fullmatch(value.strip()):
            return "[Phone]"
        if ABN_PATTERN.fullmatch(value.strip()):
            return "[ABN]"
    return value


def sanitize_json(data: Any, parent_key: str = "") -> Any:
    """Recursively sanitize PII from a JSON structure.

    Args:
        data: The JSON data (dict, list, or primitive).
        parent_key: The key of the parent field (for context).

    Returns:
        Sanitized copy of the data.
    """
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            result[key] = sanitize_json(value, parent_key=key)
        return result
    elif isinstance(data, list):
        return [sanitize_json(item, parent_key=parent_key) for item in data]
    else:
        return sanitize_value(parent_key, data)
