from sqlalchemy.orm import Session

from app.models.core.user import User
from app.models.sales.lead import Lead
from app.services.sales.lead_ownership import notify_lead_assigned
from app.services.sales.territory import round_robin_sales_on_team, _user_id_with_lowest_load
from app.models.sales.quote import Quote
from app.models.sales.task import Task
from app.models.sales.workflow_rule import WorkflowRule
from app.utils.notify import send_notification, notify_role_users

DEFAULT_RULES = (
    ("lead_created", "assign_round_robin"),
    ("lead_created", "create_task"),
    ("stage_changed", "notify"),
    ("quote_accepted", "notify"),
)


def ensure_default_workflow_rules(db: Session, company_id: int) -> None:
    existing = db.query(WorkflowRule).filter(WorkflowRule.company_id == company_id).count()
    if existing:
        return
    for trigger, action in DEFAULT_RULES:
        db.add(WorkflowRule(company_id=company_id, trigger=trigger, action=action, is_active=True))
    db.flush()


def run_workflows(
    db: Session,
    trigger: str,
    *,
    lead: Lead | None = None,
    quote: Quote | None = None,
    auto_assign: bool = True,
) -> None:
    company_id = None
    if lead is not None:
        company_id = lead.company_id
    elif quote is not None:
        company_id = quote.company_id
    if company_id is None:
        return
    ensure_default_workflow_rules(db, company_id)
    rules = (
        db.query(WorkflowRule)
        .filter(
            WorkflowRule.company_id == company_id,
            WorkflowRule.trigger == trigger,
            WorkflowRule.is_active == True,
        )
        .order_by(WorkflowRule.id.asc())
        .all()
    )
    for rule in rules:
        if rule.action == "assign_round_robin" and lead is not None:
            if auto_assign:
                _assign_round_robin(db, lead)
        elif rule.action == "create_task" and lead is not None:
            _create_follow_up_task(db, lead)
        elif rule.action == "notify":
            _notify(db, lead=lead, quote=quote)


def _assign_round_robin(db: Session, lead: Lead) -> None:
    if lead.assigned_to_id:
        return
    if lead.team_id is not None:
        user_id = round_robin_sales_on_team(db, company_id=lead.company_id, team_id=lead.team_id)
    else:
        candidates = (
            db.query(User)
            .filter(
                User.company_id == lead.company_id,
                User.role == "sales",
                User.is_active == True,
            )
            .all()
        )
        user_id = _user_id_with_lowest_load(db, lead.company_id, candidates)
    if user_id is not None:
        lead.assigned_to_id = user_id
        notify_lead_assigned(db, lead, user_id, "A new lead was assigned to you.")


def _create_follow_up_task(db: Session, lead: Lead) -> None:
    db.add(
        Task(
            company_id=lead.company_id,
            title=f"Follow up: {lead.name}",
            lead_id=lead.id,
            assigned_to_id=lead.assigned_to_id,
        )
    )


def _notify(db: Session, *, lead: Lead | None, quote: Quote | None) -> None:
    if quote is not None:
        notify_role_users(
            db,
            quote.company_id,
            "admin",
            "Quote accepted",
            f"Quote {quote.quote_number} was accepted.",
            category="finance",
        )
        return
    if lead is not None and lead.assigned_to_id:
        send_notification(
            db,
            lead.assigned_to_id,
            f"Lead updated: {lead.name}",
            f"{lead.name} moved to {getattr(lead.status, 'value', lead.status)}.",
            category="leads",
        )
