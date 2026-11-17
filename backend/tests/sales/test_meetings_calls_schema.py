from datetime import datetime

from sqlalchemy import inspect

from app.models.core.enums import CallDirection, MeetingStatus
from app.models.sales.call_log import CallLog
from app.models.sales.lead import Lead
from app.models.sales.meeting import Meeting
from tests.helpers.factories import create_company


def test_meeting_and_call_tables_exist_and_have_company_id(db_engine):
    tables = set(inspect(db_engine).get_table_names())
    assert {"meetings", "call_logs"} <= tables
    for t in ("meetings", "call_logs"):
        cols = {c["name"] for c in inspect(db_engine).get_columns(t)}
        assert "company_id" in cols


def test_meeting_status_and_call_direction_values():
    assert MeetingStatus.SCHEDULED.value == "scheduled"
    assert MeetingStatus.COMPLETED.value == "completed"
    assert MeetingStatus.CANCELLED.value == "cancelled"
    assert CallDirection.INBOUND.value == "inbound"
    assert CallDirection.OUTBOUND.value == "outbound"


def test_can_persist_a_meeting_and_a_call(db):
    company = create_company(db, name="Schema Co 1", company_code="S01")
    lead = Lead(company_id=company.id, name="Schema Lead", status="Active")
    db.add(lead)
    db.commit()
    meeting = Meeting(
        company_id=company.id,
        subject="Site visit",
        starts_at=datetime(2026, 9, 1, 10, 0, 0),
        lead_id=lead.id,
    )
    call = CallLog(
        company_id=company.id,
        direction=CallDirection.OUTBOUND,
        logged_at=datetime(2026, 9, 1, 9, 0, 0),
        lead_id=lead.id,
    )
    db.add_all([meeting, call])
    db.commit()
    db.refresh(meeting)
    db.refresh(call)
    assert meeting.id is not None
    assert meeting.status == MeetingStatus.SCHEDULED
    assert call.id is not None
    assert call.direction == CallDirection.OUTBOUND
