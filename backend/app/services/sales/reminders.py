"""Send in-app + email reminders for due tasks and follow-ups; WhatsApp cadence templates."""
from datetime import datetime, time, date
from html import escape

from sqlalchemy.orm import Session, joinedload

from app.models.core.company_settings import CompanySettings
from app.models.core.enums import TaskStatus
from app.models.core.enums import TaskStatus
from app.models.core.user import User
from app.models.sales.deal import Deal
from app.models.sales.follow_up import FollowUp
from app.models.sales.task import Task
from app.services.sales.daily_digest import run_daily_digests
from app.services.sales.lead_sheet import sync_due_sheets
from app.services.sales.recycle import hide_deleted_lead_rows
from app.services.sales.whatsapp import send_cadence_whatsapp
from app.utils.email_service import send_email
from app.utils.notify import send_notification


def _end_of_day(day: date) -> datetime:
    return datetime.combine(day, time(23, 59, 59))


def _flags(db: Session, company_id: int) -> tuple[bool, bool]:
    row = db.query(CompanySettings).filter(CompanySettings.company_id == company_id).first()
    if row is None:
        return True, True
    return bool(row.task_reminders_enabled), bool(row.followup_alerts_enabled)


def _email(user: User | None, subject: str, body: str) -> None:
    if user is None or not (user.email or "").strip():
        return
    send_email(user.email, subject, f"<p>{escape(body)}</p>")


def run_due_reminders(db: Session, now: datetime | None = None) -> dict:
    now = now or datetime.utcnow()
    cutoff = _end_of_day(now.date())
    task_count = 0
    follow_count = 0
    whatsapp_count = 0
    flags: dict[int, tuple[bool, bool]] = {}

    tasks = (
        hide_deleted_lead_rows(db.query(Task), Task.lead_id)
        .options(joinedload(Task.assigned_to))
        .filter(
            Task.reminded_at.is_(None),
            Task.due_date.isnot(None),
            Task.due_date <= cutoff,
            Task.assigned_to_id.isnot(None),
            Task.status != TaskStatus.COMPLETED,
        )
        .all()
    )
    for task in tasks:
        company_id = task.company_id
        if company_id not in flags:
            flags[company_id] = _flags(db, company_id)
        if not flags[company_id][0]:
            continue
        title = f"Task due: {task.title}"
        message = f'"{task.title}" is due today or overdue.'
        send_notification(
            db,
            task.assigned_to_id,
            title,
            message,
            type="warning",
            link="/sales/follow-ups",
            category="tasks",
        )
        _email(task.assigned_to, title, message)
        task.reminded_at = now
        task_count += 1

    follow_ups = (
        hide_deleted_lead_rows(db.query(FollowUp), FollowUp.lead_id)
        .options(joinedload(FollowUp.created_by), joinedload(FollowUp.lead))
        .filter(
            FollowUp.reminded_at.is_(None),
            FollowUp.scheduled_date <= now.date(),
            FollowUp.status == "Pending",
            FollowUp.created_by_id.isnot(None),
        )
        .all()
    )
    for item in follow_ups:
        company_id = item.company_id
        if company_id not in flags:
            flags[company_id] = _flags(db, company_id)
        if not flags[company_id][1]:
            continue
        lead = item.lead
        if lead is not None and getattr(lead, "deleted_at", None) is not None:
            continue
        lead_name = lead.name if lead is not None else "lead"
        title = f"Follow-up due: {lead_name}"
        message = item.notes or f"Follow-up with {lead_name} is due."
        send_notification(
            db,
            item.created_by_id,
            title,
            message,
            type="warning",
            link="/sales/follow-ups",
            category="tasks",
        )
        _email(item.created_by, title, message)
        if (item.channel or "").lower() == "whatsapp" and send_cadence_whatsapp(db, item):
            whatsapp_count += 1
        item.reminded_at = now
        follow_count += 1

    deals_due = (
        db.query(Deal)
        .options(joinedload(Deal.assigned_to))
        .filter(
            Deal.due_reminded_at.is_(None),
            Deal.expected_close == now.date(),
            Deal.assigned_to_id.isnot(None),
            Deal.closed_at.is_(None),
        )
        .all()
    )
    deal_count = 0
    for deal in deals_due:
        company_id = deal.company_id
        if company_id not in flags:
            flags[company_id] = _flags(db, company_id)
        if not flags[company_id][0]:
            continue
        assignee = deal.assigned_to
        title = f"Deal due today: {deal.title}"
        message = f'"{deal.title}" is expected to close today.'
        send_notification(
            db,
            deal.assigned_to_id,
            title,
            message,
            type="warning",
            link=f"/sales/deals/{deal.id}",
            category="deals",
        )
        _email(assignee, title, message)
        deal.due_reminded_at = now
        deal_count += 1

    wa_follow_ups = (
        hide_deleted_lead_rows(db.query(FollowUp), FollowUp.lead_id)
        .options(joinedload(FollowUp.lead))
        .filter(
            FollowUp.reminded_at.is_(None),
            FollowUp.scheduled_date <= now.date(),
            FollowUp.status == "Pending",
            FollowUp.channel == "whatsapp",
        )
        .all()
    )
    for item in wa_follow_ups:
        lead = item.lead
        if lead is not None and getattr(lead, "deleted_at", None) is not None:
            continue
        if send_cadence_whatsapp(db, item):
            whatsapp_count += 1
        item.reminded_at = now

    digest_count = run_daily_digests(db, now=now)
    sheet_count = sync_due_sheets(db)

    db.commit()
    return {
        "tasks": task_count,
        "follow_ups": follow_count,
        "deals_due": deal_count,
        "whatsapp": whatsapp_count,
        "digests": digest_count,
        "sheets": sheet_count,
    }
