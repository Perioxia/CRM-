from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta, timezone

from app.database import get_db
from app.utils.datetime_json import task_due_for_json
from app.services.sales.lead_access import ensure_can_work_lead
from app.services.sales.recycle import hide_deleted_lead_rows
from app.utils.dependencies import get_current_user, apply_company_scope, ensure_company_access, get_active_team_id
from app.models.core.user import User
from app.models.core.team_membership import TeamMembership
from app.models.sales.task import Task
from app.models.sales.lead import Lead
from app.models.sales.client import Client
from app.models.sales.deal import Deal
from app.schemas.sales import TaskListResponse
from app.schemas.admin import MessageResponse
from app.models.core.enums import TaskStatus
from app.utils.notify import send_notification

router = APIRouter()


def _user_role_str(user: User) -> str:
    r = getattr(user, "role", None)
    if r is None:
        return ""
    return str(getattr(r, "value", r))


def _ids_equal(a: Optional[object], b: Optional[object]) -> bool:
    if a is None or b is None:
        return False
    try:
        return int(a) == int(b)
    except (TypeError, ValueError):
        return False


def _sales_may_access_task(db: Session, current_user: User, task: Task) -> bool:
    """Sales: assignee, creator, or owner of the linked lead/client (any task on that record)."""
    if _ids_equal(task.assigned_to_id, current_user.id):
        return True
    if _ids_equal(task.assigned_by_id, current_user.id):
        return True
    if task.lead_id is not None:
        lead = apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.id == task.lead_id).first()
        if lead and _ids_equal(lead.assigned_to_id, current_user.id):
            return True
    if task.client_id is not None:
        client = apply_company_scope(db.query(Client), Client, current_user).filter(Client.id == task.client_id).first()
        if client and _ids_equal(client.assigned_to_id, current_user.id):
            return True
    return False


class TaskCreateBody(BaseModel):
    title: str
    description: Optional[str] = None
    priority: str = "medium"
    due_date: Optional[str] = None
    assigned_to_id: Optional[int] = None
    lead_id: Optional[int] = None
    client_id: Optional[int] = None
    deal_id: Optional[int] = None


class TaskUpdateBody(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    due_date: Optional[str] = None


def _parse_due_date_input(raw_due_date: Optional[str]) -> Optional[datetime]:
    if raw_due_date is None:
        return None
    due_date_str = raw_due_date.strip()
    if not due_date_str:
        return None
    try:
        parsed = datetime.fromisoformat(due_date_str.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid due_date format. Use ISO datetime or YYYY-MM-DD.",
        )
    if parsed.tzinfo is None:
        # Persist all task datetimes as naive UTC in DB.
        return parsed
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _utc_now_naive() -> datetime:
    """Return current UTC as naive datetime (Task.due_date storage format)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalize_priority(raw_priority: Optional[str]) -> str:
    normalized = (raw_priority or "").strip().lower()
    mapping = {
        "low": "Low",
        "medium": "Medium",
        "high": "High",
    }
    if normalized not in mapping:
        raise HTTPException(status_code=400, detail="Invalid priority. Allowed: low, medium, high.")
    return mapping[normalized]


def _normalize_status(raw_status: Optional[str]) -> str:
    normalized = (raw_status or "").strip().lower().replace("_", " ")
    mapping = {
        "pending": "Pending",
        "in progress": "In Progress",
        "completed": "Completed",
    }
    if normalized not in mapping:
        raise HTTPException(status_code=400, detail="Invalid status. Allowed: Pending, In Progress, Completed.")
    return mapping[normalized]


def _resolve_task_assignee(db: Session, current_user: User, requested_assignee_id: Optional[int]) -> int:
    if requested_assignee_id is None:
        return current_user.id

    target = apply_company_scope(db.query(User), User, current_user).filter(User.id == requested_assignee_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Assignee not found")

    if target.status != "active":
        raise HTTPException(status_code=400, detail="Assignee must be active")

    if _user_role_str(current_user) == "sales" and target.id != current_user.id:
        raise HTTPException(status_code=403, detail="Sales users can only assign tasks to themselves")

    if _user_role_str(current_user) == "manager" and target.id != current_user.id:
        manager_team_ids = [
            team_id
            for (team_id,) in apply_company_scope(
                db.query(TeamMembership.team_id),
                TeamMembership,
                current_user,
            )
            .filter(TeamMembership.user_id == current_user.id)
            .all()
        ]
        if not manager_team_ids:
            raise HTTPException(status_code=403, detail="Manager has no team membership configured")

        shared_membership = (
            apply_company_scope(db.query(TeamMembership), TeamMembership, current_user)
            .filter(
                TeamMembership.user_id == target.id,
                TeamMembership.team_id.in_(manager_team_ids),
            )
            .first()
        )
        if not shared_membership:
            raise HTTPException(status_code=403, detail="Managers can only assign tasks within their team")

    return target.id


def _task_link_for_role(role: Optional[object]) -> Optional[str]:
    role_map = {
        "sales": "/sales/tasks",
        "manager": "/manager/tasks",
    }
    role_val = str(getattr(role, "value", role) or "")
    return role_map.get(role_val.strip().lower())


def _notify_task_assigned(db: Session, current_user: User, task: Task) -> None:
    if not task.assigned_to_id or task.assigned_to_id == current_user.id:
        return
    assignee = (
        apply_company_scope(db.query(User), User, current_user)
        .filter(User.id == task.assigned_to_id)
        .first()
    )
    if not assignee:
        return
    due_text = task.due_date.strftime("%Y-%m-%d") if task.due_date else "No due date"
    send_notification(
        db,
        assignee.id,
        title=f"New Task Assigned: {task.title}",
        message=f"{current_user.full_name} assigned you a task. Due: {due_text}.",
        type="info",
        link=_task_link_for_role(assignee.role),
        category="tasks",
    )


def _notify_task_completed(db: Session, current_user: User, task: Task) -> None:
    if not task.assigned_by_id or task.assigned_by_id == current_user.id:
        return
    assigner = (
        apply_company_scope(db.query(User), User, current_user)
        .filter(User.id == task.assigned_by_id)
        .first()
    )
    if not assigner:
        return
    send_notification(
        db,
        assigner.id,
        title=f"Task Completed: {task.title}",
        message=f"{current_user.full_name} marked this task as completed.",
        type="success",
        link=_task_link_for_role(assigner.role),
        category="tasks",
    )


def _is_task_completed(value: object) -> bool:
    normalized = getattr(value, "value", value)
    return str(normalized) == "Completed"


# ===============================
# Tasks List (for Tasks page)
# ===============================

@router.get("/list", response_model=TaskListResponse)
def get_tasks_list(
    status: Optional[str] = Query(None, description="Filter by status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    """Get full task list with grouping (paginated)."""
    # Base query for tasks
    query = hide_deleted_lead_rows(apply_company_scope(db.query(Task), Task, current_user), Task.lead_id)
    
    # Role-based filtering
    if _user_role_str(current_user) == "sales":
        query = query.outerjoin(Lead, Task.lead_id == Lead.id).outerjoin(Client, Task.client_id == Client.id)
        query = query.filter(
            (Task.assigned_to_id == current_user.id)
            | (Task.assigned_by_id == current_user.id)
            | (Lead.assigned_to_id == current_user.id)
            | (Client.assigned_to_id == current_user.id)
        )
        if active_team_id is not None:
            query = query.filter(
                or_(Lead.team_id == active_team_id, Client.team_id == active_team_id, (Task.lead_id == None) & (Task.client_id == None))
            )
    elif _user_role_str(current_user) == "manager":
        # Managers see tasks assigned to anyone in their team or created by them.
        # MUST scope by company_id to avoid ID collision across companies.
        if active_team_id is None:
            team_members = []
        else:
            team_members = (
                apply_company_scope(db.query(User.id), User, current_user)
                .join(TeamMembership, TeamMembership.user_id == User.id)
                .filter(TeamMembership.team_id == active_team_id)
                .all()
            )
        team_member_ids = [m[0] for m in team_members]
        query = query.filter((Task.assigned_to_id.in_(team_member_ids)) | (Task.assigned_by_id == current_user.id))
    elif _user_role_str(current_user) == "purchase":
        query = query.filter((Task.assigned_to_id == current_user.id) | (Task.assigned_by_id == current_user.id))
    
    if status:
        query = query.filter(Task.status == status)
    total = query.count()
    tasks = query.order_by(Task.due_date.asc()).offset(skip).limit(limit).all()
    
    def get_due_label(task):
        due_dt = _to_utc(task.due_date)
        if not due_dt:
            return "No date"
        now_date = datetime.now(timezone.utc).date()
        due_date = due_dt.date()
        diff_days = (due_date - now_date).days
        
        if diff_days < 0:
            return f"{abs(diff_days)} days ago"
        elif diff_days == 0:
            # If due date has a specific time, show time; otherwise just "Today"
            return due_dt.strftime("%I:%M %p") if due_dt.hour != 0 or due_dt.minute != 0 else "Today"
        elif diff_days == 1:
            return "Tomorrow"
        else:
            return due_dt.strftime("%a, %b %d")
    
    lead_ids = {t.lead_id for t in tasks if t.lead_id}
    client_ids = {t.client_id for t in tasks if t.client_id}
    
    leads = {l.id: l for l in apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.id.in_(lead_ids)).all()} if lead_ids else {}
    clients = {c.id: c for c in apply_company_scope(db.query(Client), Client, current_user).filter(Client.id.in_(client_ids)).all()} if client_ids else {}

    def get_entity_info(task):
        if task.lead_id:
            lead = leads.get(task.lead_id)
            return (lead.name if lead else "Unknown Lead", "Lead")
        elif task.client_id:
            client = clients.get(task.client_id)
            return (client.name if client else "Unknown Client", "Client")
        return ("Internal", "System")
    
    result = []
    for task in tasks:
        entity_name, entity_type = get_entity_info(task)
        result.append({
            "id": task.id,
            "title": task.title,
            "description": task.description,
            "entity": entity_name,
            "entityType": entity_type,
            "assignedBy": "manager" if task.is_manager_assigned else "self",
            "dueDate": get_due_label(task),
            "due_date_iso": task_due_for_json(task.due_date),
            "status": task.status,
            "priority": task.priority
        })
    return {"items": result, "total": total, "skip": skip, "limit": limit}


@router.get("")
def get_priority_tasks(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    """Get priority tasks for dashboard (overdue and due today)"""
    now = _utc_now_naive()
    today_start = datetime(now.year, now.month, now.day)
    today_end = today_start + timedelta(days=1)
    
    task_query = hide_deleted_lead_rows(apply_company_scope(db.query(Task), Task, current_user), Task.lead_id)
    
    # Role-based filtering
    if _user_role_str(current_user) == "sales":
        task_query = task_query.outerjoin(Lead, Task.lead_id == Lead.id).outerjoin(Client, Task.client_id == Client.id)
        task_query = task_query.filter(
            (Task.assigned_to_id == current_user.id)
            | (Task.assigned_by_id == current_user.id)
            | (Lead.assigned_to_id == current_user.id)
            | (Client.assigned_to_id == current_user.id)
        )
        if active_team_id is not None:
            task_query = task_query.filter(
                or_(Lead.team_id == active_team_id, Client.team_id == active_team_id, (Task.lead_id == None) & (Task.client_id == None))
            )
    elif _user_role_str(current_user) == "manager":
        if active_team_id is None:
            team_members = []
        else:
            team_members = (
                apply_company_scope(db.query(User.id), User, current_user)
                .join(TeamMembership, TeamMembership.user_id == User.id)
                .filter(TeamMembership.team_id == active_team_id)
                .all()
            )
        team_member_ids = [m[0] for m in team_members]
        task_query = task_query.filter((Task.assigned_to_id.in_(team_member_ids)) | (Task.assigned_by_id == current_user.id))
        
    # Overdue tasks
    overdue = task_query.filter(
        Task.due_date < today_start,
        Task.status != "Completed"
    ).all()
    
    # Due today
    due_today = task_query.filter(
        Task.due_date >= today_start,
        Task.due_date < today_end,
        Task.status != "Completed"
    ).all()
    
    result = []
    for task in overdue:
        result.append({
            "id": task.id,
            "title": task.title,
            "dueDate": task.due_date.isoformat() if task.due_date else None,
            "status": task.status
        })
    for task in due_today:
        result.append({
            "id": task.id,
            "title": task.title,
            "dueDate": task.due_date.isoformat() if task.due_date else None,
            "status": task.status
        })
    
    return result


@router.get("/{task_id}")
def get_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    """Get task details by ID"""
    task = apply_company_scope(db.query(Task), Task, current_user).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    ensure_company_access(task, current_user)
    
    # Role-based scoping checking
    if _user_role_str(current_user) in ("sales", "purchase"):
        if not _sales_may_access_task(db, current_user, task):
            raise HTTPException(status_code=403, detail="You do not have access to this task")
    elif _user_role_str(current_user) == "manager":
        if active_team_id is None:
            raise HTTPException(status_code=403, detail="Active team required")
        if task.assigned_to_id:
            in_team = apply_company_scope(db.query(TeamMembership), TeamMembership, current_user).filter(
                TeamMembership.team_id == active_team_id,
                TeamMembership.user_id == task.assigned_to_id,
            ).first()
            if not in_team and task.assigned_by_id != current_user.id:
                raise HTTPException(status_code=403, detail="You do not have access to this team's task")
    
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "priority": task.priority,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "lead_id": task.lead_id,
        "client_id": task.client_id,
        "deal_id": task.deal_id,
        "is_manager_assigned": task.is_manager_assigned,
        "created_at": task.created_at.isoformat() if task.created_at else None
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_task(
    body: TaskCreateBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    """Create a new task"""
    if current_user.company_id is None:
        raise HTTPException(status_code=403, detail="User must be assigned to a company")
    due_date_val = _parse_due_date_input(body.due_date)
    assignee_id = _resolve_task_assignee(db, current_user, body.assigned_to_id)

    # Security: Verify Lead/Client belong to this company before linking
    if body.lead_id:
        lead = apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.id == body.lead_id).first()
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")
        ensure_can_work_lead(lead, current_user, active_team_id)
    if body.client_id:
        client = apply_company_scope(db.query(Client), Client, current_user).filter(Client.id == body.client_id).first()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")
    if body.deal_id:
        deal = apply_company_scope(db.query(Deal), Deal, current_user).filter(Deal.id == body.deal_id).first()
        if not deal:
            raise HTTPException(status_code=404, detail="Deal not found")

    new_task = Task(
        company_id=current_user.company_id,
        title=body.title,
        description=body.description,
        priority=_normalize_priority(body.priority),
        due_date=due_date_val,
        lead_id=body.lead_id,
        client_id=body.client_id,
        deal_id=body.deal_id,
        assigned_to_id=assignee_id,
        assigned_by_id=current_user.id,
        status="Pending"
    )
    
    db.add(new_task)
    _notify_task_assigned(db, current_user, new_task)
    db.commit()
    db.refresh(new_task)
    
    return {
        "id": new_task.id,
        "title": new_task.title,
        "status": new_task.status,
        "message": "Task created successfully"
    }


@router.put("/{task_id}")
def update_task(
    task_id: int,
    body: TaskUpdateBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    """Update a task"""
    task = apply_company_scope(db.query(Task), Task, current_user).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    ensure_company_access(task, current_user)
    
    # Role-based editing rules
    if _user_role_str(current_user) in ("sales", "purchase"):
        if not _sales_may_access_task(db, current_user, task):
            raise HTTPException(status_code=403, detail="You cannot edit someone else's task")
    elif _user_role_str(current_user) == "manager":
        if active_team_id is None:
            raise HTTPException(status_code=403, detail="Active team required")
        if task.assigned_to_id:
            in_team = apply_company_scope(db.query(TeamMembership), TeamMembership, current_user).filter(
                TeamMembership.team_id == active_team_id,
                TeamMembership.user_id == task.assigned_to_id,
            ).first()
            if not in_team and task.assigned_by_id != current_user.id:
                raise HTTPException(status_code=403, detail="You cannot edit a task outside your team")
    
    previous_status = task.status
    if body.title is not None:
        task.title = body.title
    if body.status is not None:
        task.status = _normalize_status(body.status)
        if task.status == "Completed":
            task.completed_at = _utc_now_naive()
    if body.priority is not None:
        task.priority = _normalize_priority(body.priority)
    if "due_date" in body.model_fields_set:
        task.due_date = _parse_due_date_input(body.due_date)
    if not _is_task_completed(previous_status) and _is_task_completed(task.status):
        _notify_task_completed(db, current_user, task)
    
    db.commit()
    db.refresh(task)
    
    return {"message": "Task updated successfully", "id": task.id}


@router.post("/{task_id}/complete", response_model=MessageResponse)
def complete_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    """Mark task as completed"""
    task = apply_company_scope(db.query(Task), Task, current_user).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    ensure_company_access(task, current_user)
    
    # Role-based scoping
    if _user_role_str(current_user) in ("sales", "purchase"):
        if not _sales_may_access_task(db, current_user, task):
            raise HTTPException(status_code=403, detail="You do not have permission to complete this task")
    elif _user_role_str(current_user) == "manager":
        if active_team_id is None:
            raise HTTPException(status_code=403, detail="Active team required")
        if task.assigned_to_id:
            in_team = apply_company_scope(db.query(TeamMembership), TeamMembership, current_user).filter(
                TeamMembership.team_id == active_team_id,
                TeamMembership.user_id == task.assigned_to_id,
            ).first()
            if not in_team and task.assigned_by_id != current_user.id:
                raise HTTPException(status_code=403, detail="You do not have permission to complete tasks outside your team")
    
    was_completed = _is_task_completed(task.status)
    task.status = TaskStatus.COMPLETED
    task.completed_at = _utc_now_naive()
    if not was_completed:
        _notify_task_completed(db, current_user, task)
    
    db.commit()
    
    return {"message": f"Task {task_id} completed successfully"}


@router.delete("/{task_id}", response_model=MessageResponse)
def delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    """Delete a task"""
    task = apply_company_scope(db.query(Task), Task, current_user).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    ensure_company_access(task, current_user)

    # Role-based delete permission
    if _user_role_str(current_user) in ("sales", "purchase"):
        if not _sales_may_access_task(db, current_user, task):
            raise HTTPException(status_code=403, detail="You can only delete your own tasks")
    elif _user_role_str(current_user) == "manager":
        if active_team_id is None:
            raise HTTPException(status_code=403, detail="Active team required")
        if task.assigned_to_id:
            in_team = apply_company_scope(db.query(TeamMembership), TeamMembership, current_user).filter(
                TeamMembership.team_id == active_team_id,
                TeamMembership.user_id == task.assigned_to_id,
            ).first()
            if not in_team and task.assigned_by_id != current_user.id:
                raise HTTPException(status_code=403, detail="You can only delete tasks in your team")
    
    db.delete(task)
    db.commit()
    
    return {"message": f"Task {task_id} deleted successfully"}
