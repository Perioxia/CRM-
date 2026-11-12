"""Twilio softphone API (no live Twilio)."""
from unittest.mock import patch

import pytest

from app.models.core.company_settings import CompanySettings
from app.models.sales.call_log import CallLog
from app.utils.rate_limit import auth_limiter
from app.utils.totp_crypto import encrypt_secret
from tests.helpers.auth import create_active_user, login_user
from tests.helpers.factories import create_company


@pytest.fixture(autouse=True)
def _reset_auth():
    auth_limiter._buckets.clear()
    yield


def _admin(client, db, code="TW1", phone="919876543210"):
    company = create_company(db, name=f"Tw {code}", company_code=code)
    admin = create_active_user(
        db, email=f"admin@{code.lower()}.com", role="admin", company_id=company.id, phone=phone,
    )
    login_user(client, admin.email)
    return company, admin


def _configure_twilio(db, company, *, phone="918000000001", default_user_id=None):
    row = db.query(CompanySettings).filter(CompanySettings.company_id == company.id).first()
    if row is None:
        row = CompanySettings(company_id=company.id, company_name="Co")
        db.add(row)
        db.flush()
    row.twilio_account_sid = "ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    row.twilio_auth_token_encrypted = encrypt_secret("auth-token-secret")
    row.twilio_api_key_sid = "SKbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    row.twilio_api_key_secret_encrypted = encrypt_secret("api-key-secret")
    row.twilio_twiml_app_sid = "APcccccccccccccccccccccccccccccccc"
    row.twilio_phone_number = phone
    row.twilio_default_user_id = default_user_id
    row.twilio_ring_mobile_default = 1
    db.commit()
    return row


def test_twilio_token_requires_config(client, db):
    _admin(client, db, "TWA")
    res = client.post("/api/telephony/twilio/token")
    assert res.status_code == 400


@patch("app.services.sales.twilio_voice.mint_access_token", return_value="fake.jwt.token")
def test_twilio_token_ok(mock_mint, client, db):
    company, admin = _admin(client, db, "TWB")
    _configure_twilio(db, company)
    res = client.post("/api/telephony/twilio/token")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["token"] == "fake.jwt.token"
    assert body["identity"] == f"user:{admin.id}"
    mock_mint.assert_called_once()


def test_twilio_create_call_writes_log(client, db):
    company, admin = _admin(client, db, "TWC")
    _configure_twilio(db, company)
    lead_id = client.post("/api/leads", json={"name": "Ravi", "phone": "9876543210"}).json()["id"]
    res = client.post("/api/telephony/twilio/calls", json={"lead_id": lead_id})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["provider"] == "twilio"
    assert body["direction"] == "outbound"
    assert body["outcome"] == "initiated"
    assert body["to_e164"] == "+919876543210"
    row = db.query(CallLog).filter(CallLog.id == body["id"]).one()
    assert row.lead_id == lead_id
    assert row.created_by_id == admin.id
    assert row.company_id == company.id


def test_twilio_create_call_requires_phone(client, db):
    company, _ = _admin(client, db, "TWD")
    _configure_twilio(db, company)
    lead_id = client.post("/api/leads", json={"name": "No Phone"}).json()["id"]
    res = client.post("/api/telephony/twilio/calls", json={"lead_id": lead_id})
    assert res.status_code == 400


@patch("app.services.sales.twilio_voice.validate_request_signature", return_value=True)
def test_twilio_inbound_routes_to_owner(mock_sig, client, db):
    company, admin = _admin(client, db, "TWE")
    sales = create_active_user(
        db, email="sales@twe.com", role="sales", company_id=company.id, phone="919111111111",
    )
    _configure_twilio(db, company, phone="918000000099", default_user_id=admin.id)
    login_user(client, admin.email)
    lead = client.post(
        "/api/leads",
        json={"name": "Inbound", "phone": "912222222222", "assigned_to_id": sales.id},
    ).json()
    # Some create endpoints may ignore assigned_to; set directly.
    from app.models.sales.lead import Lead
    db_lead = db.query(Lead).filter(Lead.id == lead["id"]).one()
    db_lead.assigned_to_id = sales.id
    db.commit()

    res = client.post(
        "/api/telephony/twilio/inbound",
        data={
            "AccountSid": "ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "To": "+918000000099",
            "From": "+912222222222",
            "CallSid": "CAinbound1",
        },
        headers={"X-Twilio-Signature": "ok"},
    )
    assert res.status_code == 200, res.text
    assert "application/xml" in res.headers.get("content-type", "")
    assert f"user:{sales.id}" in res.text
    assert "+919111111111" in res.text  # simultaneous mobile ring
    row = db.query(CallLog).filter(CallLog.provider_call_id == "CAinbound1").one()
    assert row.direction == "inbound" or (
        hasattr(row.direction, "value") and row.direction.value == "inbound"
    )
    assert row.lead_id == lead["id"]
    assert row.created_by_id == sales.id


@patch("app.services.sales.twilio_voice.validate_request_signature", return_value=False)
def test_twilio_status_bad_signature_is_ignored(mock_sig, client, db):
    """Invalid signatures must not 403 — Twilio Client surfaces that as gateway 31005."""
    company, _ = _admin(client, db, "TWF")
    _configure_twilio(db, company)
    res = client.post(
        "/api/telephony/twilio/status",
        data={"AccountSid": "ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "CallSid": "CAx", "CallStatus": "completed"},
        headers={"X-Twilio-Signature": "bad"},
    )
    assert res.status_code == 204


@patch("app.services.sales.twilio_voice.validate_request_signature", return_value=True)
def test_twilio_status_updates_duration(mock_sig, client, db):
    company, admin = _admin(client, db, "TWG")
    _configure_twilio(db, company)
    lead_id = client.post("/api/leads", json={"name": "Ravi", "phone": "9876543210"}).json()["id"]
    created = client.post("/api/telephony/twilio/calls", json={"lead_id": lead_id}).json()
    call_id = created["id"]
    row = db.query(CallLog).filter(CallLog.id == call_id).one()
    row.provider_call_id = "CAstatus1"
    db.commit()

    res = client.post(
        f"/api/telephony/twilio/status?call_log_id={call_id}",
        data={
            "AccountSid": "ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "CallSid": "CAstatus1",
            "CallStatus": "completed",
            "CallDuration": "42",
        },
        headers={"X-Twilio-Signature": "ok"},
    )
    assert res.status_code == 204
    db.refresh(row)
    assert row.duration_seconds == 42
    assert row.outcome == "completed"
    assert row.ended_at is not None


def test_put_connection_saves_twilio(client, db):
    company, _ = _admin(client, db, "TWH")
    res = client.put("/api/telephony/connection", json={
        "twilio_account_sid": "ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "twilio_auth_token": "auth-secret",
        "twilio_api_key_sid": "SKbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "twilio_api_key_secret": "key-secret",
        "twilio_twiml_app_sid": "APcccccccccccccccccccccccccccccccc",
        "twilio_phone_number": "+918000000001",
    })
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["twilio"]["configured"] is True
    assert "auth-secret" not in res.text
    assert "key-secret" not in res.text
    row = db.query(CompanySettings).filter(CompanySettings.company_id == company.id).one()
    assert row.twilio_auth_token_encrypted != "auth-secret"
    assert row.twilio_phone_number == "918000000001"


def test_me_telephony_ring_mobile(client, db):
    company, admin = _admin(client, db, "TWI")
    _configure_twilio(db, company)
    res = client.get("/api/telephony/me")
    assert res.status_code == 200
    assert res.json()["twilio_configured"] is True
    put = client.put("/api/telephony/me", json={"twilio_ring_mobile": False, "twilio_phone_number": "918111111111"})
    assert put.status_code == 200, put.text
    assert put.json()["twilio_ring_mobile"] is False
    db.refresh(admin)
    assert admin.twilio_ring_mobile == 0
    assert admin.twilio_phone_number == "918111111111"
