import string
import random
import re
from typing import Optional
from sqlalchemy.orm import Session
from app.models.core.company import Company

_NON_DIGIT_RE = re.compile(r"[^0-9]+")

def normalize_email(value: Optional[str]) -> Optional[str]:
    """Normalize email for consistent matching/storage (case-insensitive)."""
    if value is None:
        return None
    value = value.strip()
    return value.lower() if value else None

def normalize_phone(value: Optional[str]) -> Optional[str]:
    """Normalize phone for consistent matching/storage (digits-only)."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    digits = _NON_DIGIT_RE.sub("", value)
    return digits if digits else None

def phone_variants(value: Optional[str]) -> set[str]:
    """Every way the same number may be stored. Only Indian numbers have more than one."""
    digits = normalize_phone(value)
    if not digits:
        return set()
    if len(digits) == 10:
        local = digits
    elif len(digits) == 11 and digits.startswith("0"):
        local = digits[1:]
    elif len(digits) == 12 and digits.startswith("91"):
        local = digits[2:]
    else:
        return {digits}
    return {local, f"0{local}", f"91{local}"}


def generate_company_code(db: Session, length: int = 3) -> str:
    """Generate a unique alphanumeric code for a company."""
    chars = string.ascii_uppercase + string.digits
    
    for _ in range(10):  # Try 10 times to avoid infinite loops
        code = ''.join(random.choices(chars, k=length))
        # Check if the code is unique
        existing = db.query(Company).filter(Company.company_code == code).first()
        if not existing:
            return code
            
    # Fallback if extremely collision-heavy (unlikely with 3 chars but just in case)
    import time
    return (str(int(time.time()))[-2:] + random.choice(chars)).upper()


def next_employee_num(db: Session, company_id: int) -> int:
    """Return the next sequential employee_num for a company.

    Uses MAX(employee_num) + 1 so the value is stable even if users are deleted.
    """
    from sqlalchemy import func as sa_func
    from app.models.core.user import User

    current_max = (
        db.query(sa_func.max(User.employee_num))
        .filter(User.company_id == company_id)
        .scalar()
    )
    return (current_max or 0) + 1
