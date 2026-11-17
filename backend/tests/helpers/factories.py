"""Small test factories for common business entities."""

from datetime import datetime, timedelta, timezone

from app.models import Company, Client


def schedule_next_activity(client, deal_id, *, title="Follow up"):
    """Satisfy the Phase 7.8 next-activity gate before a forward stage move.

    Posts a future-dated task linked to the deal via the API using the currently
    logged-in client. Returns the response.
    """
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
    return client.post("/api/tasks", json={
        "title": title,
        "due_date": tomorrow,
        "deal_id": deal_id,
    })


_SHORT_CODES: dict[str, str] = {}


def _fit_company_code(code: str | None) -> str | None:
    """companies.company_code holds 3 characters; SQLite ignores that and Postgres refuses a longer one.
    Older tests pass longer labels, so each label gets its own 3-character code for the whole test run,
    which keeps two companies made in one test distinct."""
    if code is None or len(code) <= 3:
        return code
    if code not in _SHORT_CODES:
        _SHORT_CODES[code] = f"z{len(_SHORT_CODES):02d}"
    return _SHORT_CODES[code]


def create_company(db, *, name: str, company_code: str, status: str = "active") -> Company:
    company = Company(name=name, company_code=_fit_company_code(company_code), status=status)
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


def create_client(db, *, company_id: int, name: str, assigned_to_id: int | None = None, **extra) -> Client:
    client = Client(company_id=company_id, name=name, assigned_to_id=assigned_to_id, **extra)
    db.add(client)
    db.commit()
    db.refresh(client)
    return client
