"""
CRM tool implementations for the company AI assistant.
Returns JSON-serializable dicts (use ok/summary/suggestion for soft failures).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.core.enums import LeadStatus, TaskPriority, TaskStatus
from app.models.core.team import Team
from app.models.core.team_membership import TeamMembership
from app.models.core.user import User
from app.models.sales.client import Client
from app.models.sales.follow_up import FollowUp
from app.models.sales.lead import Lead
from app.models.sales.note import Note
from app.models.finance.invoice import Invoice
from app.models.sales.task import Task
from app.utils.dependencies import apply_company_scope, ensure_company_access
from app.utils.helpers import normalize_email, normalize_phone
from app.utils.audit import log_activity

from app.routers.sales import leads as leads_router
from app.routers.sales import tasks as tasks_router


def _ai_role(user: User) -> str:
    r = getattr(user, "role", None)
    return str(getattr(r, "value", r)).strip().lower()


def _ctx_team_id(context: dict | None) -> int | None:
    if not context or not isinstance(context, dict):
        return None
    raw = context.get("active_team_id")
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _get_lead_scoped(db: Session, user: User, lead_id: int) -> Lead | None:
    return apply_company_scope(db.query(Lead), Lead, user).filter(Lead.id == lead_id).first()


def _lead_access_ok_ctx(
    db: Session,
    user: User,
    lead: Lead,
    context: dict | None,
    *,
    manager_needs_team: bool = True,
) -> tuple[bool, str | None]:
    role = _ai_role(user)
    ctx_team = _ctx_team_id(context)
    if role == "purchase":
        return False, "Purchase users have no access to leads."
    if role == "sales" and lead.assigned_to_id != user.id:
        return False, "You can only access leads assigned to you."
    if role == "manager" and manager_needs_team:
        if ctx_team is None or lead.team_id != ctx_team:
            return False, "You can only access leads in your active team."
    return True, None


def ai_list_teams(db: Session, user: User) -> dict[str, Any]:
    rows = apply_company_scope(db.query(Team), Team, user).order_by(Team.name.asc()).all()
    return {"teams": [{"id": t.id, "name": t.name} for t in rows]}


def ai_list_company_users(db: Session, user: User, role_filter: str | None) -> dict[str, Any]:
    q = apply_company_scope(db.query(User), User, user).filter(User.is_active.is_(True))
    rf = (role_filter or "").strip().lower()
    if rf in ("sales", "manager", "admin", "md", "purchase"):
        q = q.filter(User.role == rf)
    elif rf and rf != "all":
        return {"ok": False, "summary": f"Unknown role filter '{role_filter}'.", "suggestion": "Use sales, manager, admin, md, purchase, or all."}
    rows = q.order_by(User.full_name.asc()).limit(100).all()
    return {
        "users": [
            {
                "id": u.id,
                "full_name": u.full_name,
                "email": u.email,
                "role": _ai_role(u),
            }
            for u in rows
        ]
    }


def ai_list_leads(db: Session, user: User, limit: int, status_filter: str | None, context: dict | None) -> dict[str, Any]:
    role = _ai_role(user)
    ctx_team = _ctx_team_id(context)
    if role == "purchase":
        return {"leads": []}
    q = apply_company_scope(db.query(Lead), Lead, user)
    if role == "sales":
        q = q.filter(Lead.assigned_to_id == user.id)
        if ctx_team is not None:
            q = q.filter(Lead.team_id == ctx_team)
    elif role == "manager":
        if ctx_team is None:
            return {
                "ok": False,
                "summary": "Select an active team to list leads.",
                "suggestion": "Pick a team in the header or pass team context from the app.",
            }
        q = q.filter(Lead.team_id == ctx_team)
    if status_filter:
        sf = status_filter.strip()
        try:
            matched = next((e for e in LeadStatus if e.value.lower() == sf.lower()), None)
            if matched:
                q = q.filter(Lead.status == matched)
            else:
                q = q.filter(Lead.status == sf)
        except Exception:
            q = q.filter(Lead.status == sf)
    lim = max(1, min(int(limit or 20), 50))
    rows = q.order_by(Lead.id.desc()).limit(lim).all()
    return {
        "leads": [
            {
                "id": l.id,
                "name": l.name,
                "email": l.email,
                "company": l.company,
                "status": l.status.value if hasattr(l.status, "value") else str(l.status),
                "assigned_to_id": l.assigned_to_id,
                "team_id": l.team_id,
            }
            for l in rows
        ]
    }


def ai_list_tasks(db: Session, user: User, limit: int, context: dict | None) -> dict[str, Any]:
    role = _ai_role(user)
    ctx_team = _ctx_team_id(context)
    q = apply_company_scope(db.query(Task), Task, user)
    if role == "sales":
        q = q.filter(
            (Task.assigned_to_id == user.id)
            | (Task.assigned_by_id == user.id)
        )
    elif role == "manager":
        if ctx_team is None:
            return {"ok": False, "summary": "Select an active team to list tasks."}
        member_ids = [
            m.user_id
            for m in apply_company_scope(db.query(TeamMembership), TeamMembership, user).filter(
                TeamMembership.team_id == ctx_team
            ).all()
        ]
        if not member_ids:
            return {"tasks": []}
        q = q.filter(Task.assigned_to_id.in_(member_ids))
    lim = max(1, min(int(limit or 20), 50))
    rows = q.order_by(Task.id.desc()).limit(lim).all()
    return {
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "status": t.status,
                "priority": t.priority,
                "assigned_to_id": t.assigned_to_id,
                "lead_id": t.lead_id,
                "client_id": t.client_id,
            }
            for t in rows
        ]
    }


def _parse_lead_status_value(raw: str) -> LeadStatus:
    s = (raw or "").strip()
    for e in LeadStatus:
        if e.value.lower() == s.lower():
            return e
    raise ValueError(f"Invalid lead status '{raw}'")


def ai_claim_lead(db: Session, user: User, lead_id: int, context: dict | None) -> dict[str, Any]:
    if _ai_role(user) != "sales":
        return {"ok": False, "summary": "Only sales users can claim leads."}
    ctx_team = _ctx_team_id(context)
    lead = _get_lead_scoped(db, user, lead_id)
    if not lead:
        return {"ok": False, "summary": "Lead not found."}
    if lead.assigned_to_id is not None:
        return {"ok": False, "summary": "This lead is already assigned."}
    if ctx_team is not None and lead.team_id != ctx_team:
        return {"ok": False, "summary": "Lead does not belong to your active team."}
    lead.assigned_to_id = user.id
    log_activity(db, user=user, action="claimed", entity_type="lead", entity_id=lead.id, entity_name=lead.name, after=user.full_name)
    db.commit()
    return {"lead_id": lead.id, "claimed_by": user.id}


def ai_add_lead_note(db: Session, user: User, lead_id: int, content: str, context: dict | None) -> dict[str, Any]:
    lead = _get_lead_scoped(db, user, lead_id)
    if not lead:
        return {"ok": False, "summary": "Lead not found."}
    ok, err = _lead_access_ok_ctx(db, user, lead, context)
    if not ok:
        return {"ok": False, "summary": err or "Access denied"}
    text = (content or "").strip()
    if not text:
        return {"ok": False, "summary": "Note content is required."}
    note = Note(
        company_id=user.company_id,
        content=text[:20000],
        lead_id=lead.id,
        created_by_id=user.id,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return {"note_id": note.id, "lead_id": lead.id}


def ai_update_lead_status(db: Session, user: User, lead_id: int, status: str, context: dict | None) -> dict[str, Any]:
    lead = _get_lead_scoped(db, user, lead_id)
    if not lead:
        return {"ok": False, "summary": "Lead not found."}
    ok, err = _lead_access_ok_ctx(db, user, lead, context)
    if not ok:
        return {"ok": False, "summary": err or "Access denied"}
    try:
        new_st = _parse_lead_status_value(status)
    except ValueError as e:
        return {"ok": False, "summary": str(e)}
    old = lead.status
    if new_st == LeadStatus.CONVERTED and not lead.assigned_to_id:
        lead.assigned_to_id = user.id
    lead.status = new_st
    if new_st == LeadStatus.CONVERTED and lead.converted_at is None:
        lead.converted_at = datetime.now(timezone.utc)
    created_client = leads_router._ensure_client_for_converted_lead(db, lead, user)
    if old != new_st:
        log_activity(
            db,
            user=user,
            action="status_changed",
            entity_type="lead",
            entity_id=lead.id,
            entity_name=lead.name,
            before=old,
            after=new_st,
        )
    db.commit()
    db.refresh(lead)
    st = lead.status
    return {
        "lead_id": lead.id,
        "status": st.value if hasattr(st, "value") else str(st),
    }


def ai_assign_lead(db: Session, user: User, lead_id: int, assigned_to_id: int, context: dict | None) -> dict[str, Any]:
    role = _ai_role(user)
    if role == "sales":
        return {"ok": False, "summary": "Sales users cannot reassign leads to others via the assistant."}
    lead = _get_lead_scoped(db, user, lead_id)
    if not lead:
        return {"ok": False, "summary": "Lead not found."}
    ok, err = _lead_access_ok_ctx(db, user, lead, context)
    if not ok:
        return {"ok": False, "summary": err or "Access denied"}
    assignee = apply_company_scope(db.query(User), User, user).filter(User.id == assigned_to_id).first()
    if not assignee:
        return {"ok": False, "summary": "Assignee not found in your company."}
    if role == "manager":
        tid = _ctx_team_id(context)
        if tid is None:
            return {"ok": False, "summary": "Active team required to assign leads."}
        in_team = apply_company_scope(db.query(TeamMembership), TeamMembership, user).filter(
            TeamMembership.team_id == tid,
            TeamMembership.user_id == assignee.id,
        ).first()
        if not in_team:
            return {"ok": False, "summary": "Assignee must be on your active team."}
    lead.assigned_to_id = assigned_to_id
    db.commit()
    return {"lead_id": lead.id, "assigned_to_id": assigned_to_id}


def ai_update_lead_fields(
    db: Session,
    user: User,
    lead_id: int,
    fields: dict[str, Any],
    context: dict | None,
) -> dict[str, Any]:
    lead = _get_lead_scoped(db, user, lead_id)
    if not lead:
        return {"ok": False, "summary": "Lead not found."}
    ok, err = _lead_access_ok_ctx(db, user, lead, context)
    if not ok:
        return {"ok": False, "summary": err or "Access denied"}
    if "name" in fields and fields["name"] is not None:
        n = str(fields["name"]).strip()
        if n:
            lead.name = n[:255]
    if "email" in fields and fields["email"] is not None:
        e = str(fields["email"]).strip()
        lead.email = normalize_email(e) if e else None
    if "phone" in fields and fields["phone"] is not None:
        p = str(fields["phone"]).strip()
        lead.phone = normalize_phone(p) if p else None
    if "company" in fields and fields["company"] is not None:
        c = str(fields["company"]).strip()
        lead.company = c[:255] if c else None
    if "notes" in fields and fields["notes"] is not None:
        lead.notes = str(fields["notes"])[:10000] if fields["notes"] else None
    if "source" in fields and fields["source"] is not None:
        lead.source = str(fields["source"])[:100] if fields["source"] else None
    if "service_type" in fields and fields["service_type"] is not None:
        lead.service_type = str(fields["service_type"])[:100] if fields["service_type"] else None
    db.commit()
    db.refresh(lead)
    return {"lead_id": lead.id, "updated": True}


def ai_convert_lead(db: Session, user: User, lead_id: int, context: dict | None) -> dict[str, Any]:
    lead = _get_lead_scoped(db, user, lead_id)
    if not lead:
        return {"ok": False, "summary": "Lead not found."}
    ok, err = _lead_access_ok_ctx(db, user, lead, context)
    if not ok:
        return {"ok": False, "summary": err or "Access denied"}
    if not lead.assigned_to_id:
        lead.assigned_to_id = user.id
    existing = apply_company_scope(db.query(Client), Client, user).filter(Client.converted_from_lead_id == lead.id).first()
    if existing:
        return {"client_id": existing.id, "message": "Already converted", "lead_id": lead_id}
    lead.status = LeadStatus.CONVERTED
    if lead.converted_at is None:
        lead.converted_at = datetime.now(timezone.utc)
    leads_router._ensure_client_for_converted_lead(db, lead, user)
    db.commit()
    client = apply_company_scope(db.query(Client), Client, user).filter(Client.converted_from_lead_id == lead.id).first()
    return {"client_id": client.id if client else None, "lead_id": lead_id}


def ai_delete_lead(db: Session, user: User, lead_id: int, context: dict | None) -> dict[str, Any]:
    lead = _get_lead_scoped(db, user, lead_id)
    if not lead:
        return {"ok": False, "summary": "Lead not found."}
    role = _ai_role(user)
    if role == "sales" and lead.assigned_to_id != user.id:
        return {"ok": False, "summary": "You can only delete your own leads."}
    if role == "manager":
        tid = _ctx_team_id(context)
        if tid is None or lead.team_id != tid:
            return {"ok": False, "summary": "You can only delete leads in your active team."}
    client = apply_company_scope(db.query(Client), Client, user).filter(Client.converted_from_lead_id == lead.id).first()
    if client:
        inv_c = apply_company_scope(db.query(Invoice), Invoice, user).filter(Invoice.client_id == client.id).count()
        if inv_c > 0:
            return {"ok": False, "summary": "Cannot delete: client has invoices."}
        client.converted_from_lead_id = None
        db.add(client)
    db.delete(lead)
    db.commit()
    return {"deleted_lead_id": lead_id}


def _parse_ymd(s: str) -> Any:
    from datetime import date as date_cls

    return datetime.strptime(s.strip(), "%Y-%m-%d").date()


def _parse_hm(s: str):
    return datetime.strptime(s.strip(), "%H:%M").time()


def ai_create_follow_up(
    db: Session,
    user: User,
    lead_id: int,
    scheduled_date: str,
    scheduled_time: str | None,
    notes: str | None,
    context: dict | None,
) -> dict[str, Any]:
    lead = _get_lead_scoped(db, user, lead_id)
    if not lead:
        return {"ok": False, "summary": "Lead not found."}
    role = _ai_role(user)
    ctx_team = _ctx_team_id(context)
    if role == "sales":
        if lead.assigned_to_id != user.id:
            return {"ok": False, "summary": "You can only add follow-ups to your leads."}
        if ctx_team is not None and lead.team_id != ctx_team:
            return {"ok": False, "summary": "Follow-up must be on a lead in your active team."}
    elif role == "manager":
        if ctx_team is None or lead.team_id != ctx_team:
            return {"ok": False, "summary": "Follow-up must be on a lead in your active team."}
    try:
        d = _parse_ymd(scheduled_date)
    except Exception:
        return {"ok": False, "summary": "scheduled_date must be YYYY-MM-DD."}
    t = None
    if scheduled_time and str(scheduled_time).strip():
        try:
            t = _parse_hm(str(scheduled_time))
        except Exception:
            return {"ok": False, "summary": "scheduled_time must be HH:MM (24h)."}
    fu = FollowUp(
        company_id=lead.company_id,
        lead_id=lead_id,
        scheduled_date=d,
        scheduled_time=t,
        notes=(notes or "").strip() or None,
        status="Pending",
        created_by_id=user.id,
    )
    db.add(fu)
    db.commit()
    db.refresh(fu)
    return {"follow_up_id": fu.id, "lead_id": lead_id}


def ai_complete_follow_up(db: Session, user: User, follow_up_id: int, outcome: str, context: dict | None) -> dict[str, Any]:
    fu = apply_company_scope(db.query(FollowUp), FollowUp, user).filter(FollowUp.id == follow_up_id).first()
    if not fu:
        return {"ok": False, "summary": "Follow-up not found."}
    ensure_company_access(fu, user)
    lead = _get_lead_scoped(db, user, fu.lead_id)
    if not lead:
        return {"ok": False, "summary": "Lead not found."}
    role = _ai_role(user)
    ctx_team = _ctx_team_id(context)
    if role == "sales" and lead.assigned_to_id != user.id:
        return {"ok": False, "summary": "You cannot complete this follow-up."}
    if role == "manager" and (ctx_team is None or lead.team_id != ctx_team):
        return {"ok": False, "summary": "Outside your team."}
    oc = (outcome or "").strip()
    if not oc:
        return {"ok": False, "summary": "Outcome text is required."}
    fu.status = "Completed"
    fu.outcome = oc[:5000]
    fu.completed_at = datetime.now(timezone.utc)
    db.commit()
    return {"follow_up_id": follow_up_id, "status": "Completed"}


def ai_reschedule_follow_up(
    db: Session,
    user: User,
    follow_up_id: int,
    new_date: str,
    new_time: str | None,
    reason: str | None,
    context: dict | None,
) -> dict[str, Any]:
    fu = apply_company_scope(db.query(FollowUp), FollowUp, user).filter(FollowUp.id == follow_up_id).first()
    if not fu:
        return {"ok": False, "summary": "Follow-up not found."}
    ensure_company_access(fu, user)
    lead = _get_lead_scoped(db, user, fu.lead_id)
    if not lead:
        return {"ok": False, "summary": "Lead not found."}
    role = _ai_role(user)
    ctx_team = _ctx_team_id(context)
    if role == "sales" and lead.assigned_to_id != user.id:
        return {"ok": False, "summary": "You cannot reschedule this follow-up."}
    if role == "manager" and (ctx_team is None or lead.team_id != ctx_team):
        return {"ok": False, "summary": "Outside your team."}
    try:
        fu.scheduled_date = _parse_ymd(new_date)
    except Exception:
        return {"ok": False, "summary": "new_date must be YYYY-MM-DD."}
    if new_time and str(new_time).strip():
        try:
            fu.scheduled_time = _parse_hm(str(new_time))
        except Exception:
            return {"ok": False, "summary": "new_time must be HH:MM."}
    if reason and str(reason).strip():
        fu.notes = f"{fu.notes or ''}\n[Rescheduled: {str(reason).strip()[:500]}]"
    db.commit()
    return {"follow_up_id": follow_up_id, "scheduled_date": str(fu.scheduled_date)}


def ai_delete_follow_up(db: Session, user: User, follow_up_id: int, context: dict | None) -> dict[str, Any]:
    fu = apply_company_scope(db.query(FollowUp), FollowUp, user).filter(FollowUp.id == follow_up_id).first()
    if not fu:
        return {"ok": False, "summary": "Follow-up not found."}
    ensure_company_access(fu, user)
    lead = _get_lead_scoped(db, user, fu.lead_id)
    if not lead:
        return {"ok": False, "summary": "Lead not found."}
    role = _ai_role(user)
    ctx_team = _ctx_team_id(context)
    if role == "sales" and lead.assigned_to_id != user.id:
        return {"ok": False, "summary": "You cannot delete this follow-up."}
    if role == "manager" and (ctx_team is None or lead.team_id != ctx_team):
        return {"ok": False, "summary": "Outside your team."}
    db.delete(fu)
    db.commit()
    return {"deleted_follow_up_id": follow_up_id}


def ai_complete_task(db: Session, user: User, task_id: int, context: dict | None) -> dict[str, Any]:
    task = apply_company_scope(db.query(Task), Task, user).filter(Task.id == task_id).first()
    if not task:
        return {"ok": False, "summary": "Task not found."}
    ensure_company_access(task, user)
    role = _ai_role(user)
    ctx_team = _ctx_team_id(context)
    if role == "sales":
        if not tasks_router._sales_may_access_task(db, user, task):
            return {"ok": False, "summary": "You cannot complete this task."}
    elif role == "manager":
        if ctx_team is None:
            return {"ok": False, "summary": "Active team required."}
        if task.assigned_to_id:
            in_team = apply_company_scope(db.query(TeamMembership), TeamMembership, user).filter(
                TeamMembership.team_id == ctx_team,
                TeamMembership.user_id == task.assigned_to_id,
            ).first()
            if not in_team and task.assigned_by_id != user.id:
                return {"ok": False, "summary": "Task is outside your team."}
    task.status = TaskStatus.COMPLETED
    task.completed_at = tasks_router._utc_now_naive()
    db.commit()
    return {"task_id": task_id, "status": task.status}


def ai_update_task(
    db: Session,
    user: User,
    task_id: int,
    fields: dict[str, Any],
    context: dict | None,
) -> dict[str, Any]:
    task = apply_company_scope(db.query(Task), Task, user).filter(Task.id == task_id).first()
    if not task:
        return {"ok": False, "summary": "Task not found."}
    ensure_company_access(task, user)
    role = _ai_role(user)
    ctx_team = _ctx_team_id(context)
    if role == "sales":
        if not tasks_router._sales_may_access_task(db, user, task):
            return {"ok": False, "summary": "You cannot edit this task."}
    elif role == "manager":
        if ctx_team is None:
            return {"ok": False, "summary": "Active team required."}
        if task.assigned_to_id:
            in_team = apply_company_scope(db.query(TeamMembership), TeamMembership, user).filter(
                TeamMembership.team_id == ctx_team,
                TeamMembership.user_id == task.assigned_to_id,
            ).first()
            if not in_team and task.assigned_by_id != user.id:
                return {"ok": False, "summary": "Task is outside your team."}
    if "title" in fields and fields["title"] is not None:
        task.title = str(fields["title"]).strip()[:500]
    if "status" in fields and fields["status"] is not None:
        raw = str(fields["status"]).strip().lower().replace("_", " ")
        smap = {"pending": TaskStatus.PENDING, "in progress": TaskStatus.IN_PROGRESS, "completed": TaskStatus.COMPLETED}
        if raw not in smap:
            return {"ok": False, "summary": "Invalid task status (pending, in progress, completed)."}
        task.status = smap[raw]
        if task.status == TaskStatus.COMPLETED:
            task.completed_at = tasks_router._utc_now_naive()
    if "priority" in fields and fields["priority"] is not None:
        pr = str(fields["priority"]).strip().lower()
        pmap = {"low": TaskPriority.LOW, "medium": TaskPriority.MEDIUM, "high": TaskPriority.HIGH}
        if pr not in pmap:
            return {"ok": False, "summary": "Invalid priority (low, medium, high)."}
        task.priority = pmap[pr]
    if "due_date" in fields:
        raw = fields["due_date"]
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            task.due_date = None
        else:
            try:
                task.due_date = tasks_router._parse_due_date_input(str(raw))
            except Exception:
                return {"ok": False, "summary": "Invalid due_date (use YYYY-MM-DD or ISO)." }
    db.commit()
    db.refresh(task)
    return {"task_id": task_id, "updated": True}


def ai_delete_task(db: Session, user: User, task_id: int, context: dict | None) -> dict[str, Any]:
    task = apply_company_scope(db.query(Task), Task, user).filter(Task.id == task_id).first()
    if not task:
        return {"ok": False, "summary": "Task not found."}
    ensure_company_access(task, user)
    role = _ai_role(user)
    ctx_team = _ctx_team_id(context)
    if role == "sales":
        if not tasks_router._sales_may_access_task(db, user, task):
            return {"ok": False, "summary": "You cannot delete this task."}
    elif role == "manager":
        if ctx_team is None:
            return {"ok": False, "summary": "Active team required."}
        if task.assigned_to_id:
            in_team = apply_company_scope(db.query(TeamMembership), TeamMembership, user).filter(
                TeamMembership.team_id == ctx_team,
                TeamMembership.user_id == task.assigned_to_id,
            ).first()
            if not in_team and task.assigned_by_id != user.id:
                return {"ok": False, "summary": "Task is outside your team."}
    db.delete(task)
    db.commit()
    return {"deleted_task_id": task_id}
