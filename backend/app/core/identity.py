"""Normalization of identity fields shared by schemas and services."""

import re

_PHONE_SEPARATORS = re.compile(r"[\s\-().]")
_PHONE_PATTERN = re.compile(r"^\+?[0-9]{7,15}$")


def normalize_email(value: str) -> str:
    return value.strip().lower()


def normalize_phone(value: str) -> str:
    """Strip separators; accept an optional leading + and 7-15 digits."""
    cleaned = _PHONE_SEPARATORS.sub("", value.strip())
    if not _PHONE_PATTERN.match(cleaned):
        raise ValueError("Enter a valid phone number (7-15 digits).")
    return cleaned
