"""Checks on lead contact details, shared by every way a lead can be created or edited."""
import re

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE = re.compile(r"^[+()\-.\s\d]+$")
_MAX_LENGTHS = {
    "name": 255,
    "email": 255,
    "phone": 50,
    "company": 255,
    "source": 100,
    "service_type": 100,
    "website": 500,
    "industry": 100,
    "linkedin_url": 500,
}


def clean_lead_fields(values: dict) -> dict:
    """Trim the given fields and return them. Raises ValueError with a message for the user.

    A None value means "not given" and is skipped. A blank optional value stays blank, which clears it.
    """
    cleaned = {}
    for field, limit in _MAX_LENGTHS.items():
        value = values.get(field)
        if value is None:
            continue
        value = str(value).strip()
        if len(value) > limit:
            raise ValueError(f"{field.replace('_', ' ').capitalize()} must be at most {limit} characters")
        cleaned[field] = value
    if "name" in cleaned and not cleaned["name"]:
        raise ValueError("Name is required")
    if cleaned.get("email") and not _EMAIL.match(cleaned["email"]):
        raise ValueError("Enter a valid email address")
    phone = cleaned.get("phone")
    if phone and (not _PHONE.match(phone) or not any(ch.isdigit() for ch in phone)):
        raise ValueError("Enter a valid phone number")
    return cleaned
