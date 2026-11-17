from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.models.sales.client import Client
from app.models.sales.email_log import EmailLog
from app.models.sales.lead import Lead
from app.services.sales.campaigns import (
    MAX_RECIPIENTS,
    create_campaign,
    send_campaign,
)
from tests.helpers.factories import create_company


def _email_log(db, company_id):
    """A real email log row: on Postgres a campaign recipient must point at one that exists."""
    row = EmailLog(company_id=company_id, to_email="x@x.com", subject="s", body="b", status="sent")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _lead(db, company_id, name, email=None):
    row = Lead(company_id=company_id, name=name, email=email, status="Active")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@patch("app.services.sales.campaigns.deliver_and_log")
def test_send_to_leads_with_email(mock_deliver, db):
    company = create_company(db, name="Co", company_code="CP1")
    _lead(db, company.id, "A", "a@x.com")
    _lead(db, company.id, "B")
    camp = create_campaign(
        db, company.id, name="Spring", subject="Hi", body="Hello",
        audience="leads", created_by_id=None,
    )
    mock_deliver.return_value.status = "sent"
    mock_deliver.return_value.id = _email_log(db, company.id).id
    result = send_campaign(db, company.id, camp, sent_by_id=None)
    assert result["sent"] == 1
    assert result["skipped"] == 1
    assert camp.status == "sent"
    mock_deliver.assert_called_once()
    assert mock_deliver.call_args.kwargs["to_email"] == "a@x.com"


def test_unknown_audience_rejected(db):
    company = create_company(db, name="Co", company_code="CP2")
    with pytest.raises(HTTPException) as ei:
        create_campaign(
            db, company.id, name="X", subject="Hi", body="Hello",
            audience="deals", created_by_id=None,
        )
    assert ei.value.status_code == 400


def test_resend_rejected(db):
    company = create_company(db, name="Co", company_code="CP3")
    _lead(db, company.id, "A", "a@x.com")
    camp = create_campaign(
        db, company.id, name="X", subject="Hi", body="Hello",
        audience="leads", created_by_id=None,
    )
    with patch("app.services.sales.campaigns.deliver_and_log") as mock_deliver:
        mock_deliver.return_value.status = "sent"
        mock_deliver.return_value.id = _email_log(db, company.id).id
        send_campaign(db, company.id, camp, sent_by_id=None)
    with pytest.raises(HTTPException) as ei:
        send_campaign(db, company.id, camp, sent_by_id=None)
    assert ei.value.status_code == 400


def test_over_cap_rejected(db):
    company = create_company(db, name="Co", company_code="CP4")
    for i in range(MAX_RECIPIENTS + 1):
        _lead(db, company.id, f"L{i}", f"l{i}@x.com")
    camp = create_campaign(
        db, company.id, name="X", subject="Hi", body="Hello",
        audience="leads", created_by_id=None,
    )
    with pytest.raises(HTTPException) as ei:
        send_campaign(db, company.id, camp, sent_by_id=None)
    assert ei.value.status_code == 400
    assert camp.status == "draft"


def test_send_to_clients(db):
    company = create_company(db, name="Co", company_code="CP5")
    db.add(Client(company_id=company.id, name="Acme", email="c@x.com"))
    db.commit()
    camp = create_campaign(
        db, company.id, name="X", subject="Hi", body="Hello",
        audience="clients", created_by_id=None,
    )
    with patch("app.services.sales.campaigns.deliver_and_log") as mock_deliver:
        mock_deliver.return_value.status = "sent"
        mock_deliver.return_value.id = _email_log(db, company.id).id
        result = send_campaign(db, company.id, camp, sent_by_id=None)
    assert result["sent"] == 1
    assert mock_deliver.call_args.kwargs["client_id"] is not None
