from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from typing import Optional, List
from datetime import datetime, timedelta, timezone
import calendar

from app.database import get_db
from app.services.sales.recycle import hide_deleted_lead_rows
from app.utils.dependencies import get_current_user, apply_company_scope, ensure_company_access, is_platform_admin, get_active_team_id
from app.models.core.user import User
from app.models.sales.lead import Lead
from app.models.sales.client import Client
from app.models.finance.invoice import Invoice, InvoiceItem
from app.models.sales.task import Task
from app.models.core.team import Team
from app.models.core.team_membership import TeamMembership
from app.models.core.company import Company
from app.schemas.management import TransferRequestCreate, TransferRequestResponse
from app.models.hr.transfer_request import TeamTransferRequest
from app.utils.notify import notify_role_users

router = APIRouter()

MD_ROLES = {"md", "admin"}
POINT_ELIGIBLE_ROLES = {"sales", "manager"}

def _role_str(user: User) -> str:
    r = getattr(user, "role", None)
    return str(getattr(r, "value", r) or "").strip().lower()


def require_md(current_user: User = Depends(get_current_user)) -> User:
    if is_platform_admin(current_user):
        return current_user
    if _role_str(current_user) not in MD_ROLES:
        raise HTTPException(status_code=403, detail="MD access required")
    return current_user


def _month_range_utc(year: int, month: int) -> tuple[datetime, datetime]:
    """
    Inclusive start, exclusive end for the given month in UTC.
    """
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    last_day = calendar.monthrange(year, month)[1]
    end = datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc) + timedelta(seconds=1)
    return start, end


def _utc_now_naive() -> datetime:
    """Return current UTC as naive datetime for Task.due_date comparisons."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@router.get("/dashboard")
def get_md_dashboard(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_md),
    active_team_id: Optional[int] = Depends(get_active_team_id)
):
    """Integrated MD Executive Dashboard"""
    lead_q = apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.deleted_at.is_(None))
    inv_q = apply_company_scope(db.query(Invoice), Invoice, current_user)
    client_q = apply_company_scope(db.query(Client), Client, current_user)

    active = lead_q.filter(Lead.status.notin_(["Converted", "Lost", "Lost Client"])).count()
    converted = lead_q.filter(Lead.status == "Converted").count()
    lost = lead_q.filter(Lead.status.in_(["Lost", "Lost Client"])).count()
    win_rate = int((converted / (converted + lost) * 100)) if (converted + lost) > 0 else 0
    total_clients = client_q.count()
    paid = inv_q.filter(Invoice.status == "Paid").count()
    pending = inv_q.filter(Invoice.status.in_(["Pending", "Sent"])).count()
    overdue = inv_q.filter(Invoice.status == "Overdue").count()
    total_revenue = inv_q.filter(Invoice.status == "Paid").with_entities(func.sum(Invoice.total)).scalar() or 0

    # Daily sales momentum (last 7 days)
    now = datetime.now(timezone.utc)
    start_of_today = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    sales_trend = []
    for i in range(7):
        day = start_of_today - timedelta(days=6 - i)
        day_end = day + timedelta(days=1)
        day_rev = inv_q.filter(Invoice.status == "Paid", Invoice.created_at >= day, Invoice.created_at < day_end).with_entities(func.sum(Invoice.total)).scalar() or 0
        day_leads = lead_q.filter(Lead.created_at >= day, Lead.created_at < day_end).count()
        sales_trend.append({"date": day.strftime("%a"), "revenue": float(day_rev), "sales": day_leads})
    
    # Sales outcomes
    qualified = lead_q.filter(Lead.status == "Qualified").count()
    
    # Client growth (last 6 months)
    client_growth = []
    for m in range(6):
        month_start = (now.replace(day=1) - timedelta(days=30 * (5 - m))).replace(day=1)
        cumulative = client_q.filter(Client.created_at <= month_start + timedelta(days=31)).count()
        client_growth.append({"date": month_start.strftime("%b"), "count": cumulative})
    
    # Trend watchlist
    d7_ago = now - timedelta(days=7)
    d14_ago = now - timedelta(days=14)
    recent_leads = lead_q.filter(Lead.created_at >= d7_ago).count()
    prev_leads = lead_q.filter(Lead.created_at >= d14_ago, Lead.created_at < d7_ago).count()
    lead_velocity = int(((recent_leads - prev_leads) / max(prev_leads, 1)) * 100)
    
    task_q = hide_deleted_lead_rows(apply_company_scope(db.query(Task), Task, current_user), Task.lead_id)
    if active_team_id is not None:
        # Task has no team_id. Scope via the linked Lead/Client team.
        task_q = task_q.outerjoin(Lead, Task.lead_id == Lead.id).outerjoin(Client, Task.client_id == Client.id)
        task_q = task_q.filter(or_(Lead.team_id == active_team_id, Client.team_id == active_team_id))
        
    now_naive_utc = now.replace(tzinfo=None)
    d7_ago_naive_utc = d7_ago.replace(tzinfo=None)
    overdue_tasks = task_q.filter(Task.due_date < now_naive_utc, Task.status != "Completed").count()
    total_tasks = task_q.filter(Task.due_date >= d7_ago_naive_utc).count()
    sla_adherence = int(((total_tasks - overdue_tasks) / max(total_tasks, 1)) * 100)

    # AI Brief
    overdue_inv_count = inv_q.filter(Invoice.status == "Overdue").count()
    stalled_leads = lead_q.filter(Lead.status.in_(["New", "Contacted"]), Lead.created_at < d14_ago).count()
    ai_brief = []
    if overdue_inv_count > 0:
        ai_brief.append({"id": 1, "title": "Revenue Risk", "summary": f"{overdue_inv_count} invoice(s) are overdue. Review immediately.", "link": "/md/invoices"})
    if stalled_leads > 0:
        ai_brief.append({"id": 2, "title": "Pipeline Stagnation", "summary": f"{stalled_leads} lead(s) have been stalled for 14+ days.", "link": "/md/leads"})
    if not ai_brief:
        ai_brief.append({"id": 1, "title": "All Clear", "summary": "No critical alerts. Pipeline and finances are healthy.", "link": "/md/dashboard"})
    
    return {
        "kpis": [
            {"id": 1, "label": "Total Revenue", "value": f"₹{float(total_revenue):,.0f}", "route": "/md/revenue"},
            {"id": 2, "label": "Pipeline Leads", "value": str(active), "route": "/md/leads"},
            {"id": 3, "label": "Win Rate", "value": f"{win_rate}%", "route": "/md/sales"},
            {"id": 4, "label": "Active Clients", "value": str(total_clients), "route": "/md/clients"},
            {"id": 5, "label": "Invoices Paid", "value": str(paid), "route": "/md/revenue"},
            {"id": 6, "label": "Invoices Pending", "value": str(pending), "route": "/md/revenue"}
        ],
        "pipelineSummary": {
            "stageDistribution": [
                {"stage": "New", "count": lead_q.filter(Lead.status == "New").count()},
                {"stage": "Contacted", "count": lead_q.filter(Lead.status == "Contacted").count()},
                {"stage": "Qualified", "count": lead_q.filter(Lead.status == "Qualified").count()},
                {"stage": "Proposal", "count": lead_q.filter(Lead.status == "Proposal").count()},
                {"stage": "Converted", "count": converted}
            ]
        },
        "financeSnapshot": {
            "invoiceHealth": [
                {"name": "Paid", "value": paid, "color": "#10b981"},
                {"name": "Pending", "value": pending, "color": "#f59e0b"},
                {"name": "Overdue", "value": overdue, "color": "#ef4444"}
            ],
            "counts": {"paid": paid, "outstanding": pending, "overdue": overdue}
        },
        "salesMomentum": {
            "trend": sales_trend,
            "outcomes": [
                {"stage": "Converted", "count": converted, "color": "#10b981"},
                {"stage": "Qualified", "count": qualified, "color": "#6366f1"}
            ]
        },
        "clientSnapshot": {
            "growth": client_growth,
            "status": {"active": total_clients, "risk": stalled_leads}
        },
        "trendWatchlist": [
            {"name": "Lead Velocity", "delta": f"{lead_velocity:+d}%", "trend": "up" if lead_velocity > 0 else "down"},
            {"name": "SLA Adherence", "delta": f"{sla_adherence:+d}%", "trend": "up" if sla_adherence >= 0 else "down"}
        ],
        "aiBrief": ai_brief
    }


@router.get("/revenue")
def get_md_revenue(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_md)
):
    """MD Revenue Performance Dashboard"""
    inv_q = apply_company_scope(db.query(Invoice), Invoice, current_user)
    
    now = datetime.now()  # Use naive datetimes for SQLite compatibility
    start_of_today = datetime(now.year, now.month, now.day)
    d30_ago = start_of_today - timedelta(days=30)
    d60_ago = start_of_today - timedelta(days=60)
    
    total_revenue = inv_q.filter(Invoice.status == "Paid").with_entities(func.sum(Invoice.total)).scalar() or 0
    outstanding = inv_q.filter(Invoice.status.in_(["Pending", "Sent"])).with_entities(func.sum(Invoice.total)).scalar() or 0
    
    recent_rev = inv_q.filter(Invoice.status == "Paid", Invoice.created_at >= d30_ago).with_entities(func.sum(Invoice.total)).scalar() or 0
    prev_rev = inv_q.filter(Invoice.status == "Paid", Invoice.created_at >= d60_ago, Invoice.created_at < d30_ago).with_entities(func.sum(Invoice.total)).scalar() or 0
    growth_pct = int(((recent_rev - prev_rev) / prev_rev * 100)) if prev_rev > 0 else 0
    growth_trend = "up" if growth_pct > 0 else ("down" if growth_pct < 0 else "flat")
    
    # Daily revenue trend (last 30 days)
    revenue_trend = []
    for i in range(30):
        day = d30_ago + timedelta(days=i)
        day_end = day + timedelta(days=1)
        day_total = inv_q.filter(
            Invoice.status == "Paid",
            Invoice.created_at >= day,
            Invoice.created_at < day_end
        ).with_entities(func.sum(Invoice.total)).scalar() or 0
        revenue_trend.append({
            "date": day.strftime("%b %d"),
            "value": float(day_total)
        })
    
    # Weekly breakdown (last 4 weeks)
    breakdown_by_period = []
    colors = ["#6366f1", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6"]
    for w in range(4):
        week_start = start_of_today - timedelta(weeks=4 - w)
        week_end = week_start + timedelta(weeks=1)
        week_rev = inv_q.filter(
            Invoice.status == "Paid",
            Invoice.created_at >= week_start,
            Invoice.created_at < week_end
        ).with_entities(func.sum(Invoice.total)).scalar() or 0
        breakdown_by_period.append({
            "name": f"Week {w + 1}",
            "value": float(week_rev),
            "fill": colors[w % len(colors)]
        })
    
    # Summary table (weekly variance)
    summary_table = []
    for w in range(min(8, 4)):
        week_start = start_of_today - timedelta(weeks=4 - w)
        week_end = week_start + timedelta(weeks=1)
        week_rev = inv_q.filter(
            Invoice.status == "Paid",
            Invoice.created_at >= week_start,
            Invoice.created_at < week_end
        ).with_entities(func.sum(Invoice.total)).scalar() or 0
        # Compare to previous week
        prev_week_start = week_start - timedelta(weeks=1)
        prev_week_rev = inv_q.filter(
            Invoice.status == "Paid",
            Invoice.created_at >= prev_week_start,
            Invoice.created_at < week_start
        ).with_entities(func.sum(Invoice.total)).scalar() or 0
        
        # Ensure Decimals are handled for division and formatted as float for client
        f_week_rev = float(week_rev)
        f_prev_rev = float(prev_week_rev)
        
        delta_pct = int(((f_week_rev - f_prev_rev) / max(f_prev_rev, 1)) * 100)
        delta_str = f"+{delta_pct}%" if delta_pct > 0 else f"{delta_pct}%"
        summary_table.append({
            "id": w + 1,
            "period": f"{week_start.strftime('%b %d')} - {week_end.strftime('%b %d')}",
            "revenue": f"₹{f_week_rev:,.0f}",
            "delta": delta_str
        })
    
    # Overdue invoices as risk signals
    overdue_invoices = inv_q.filter(Invoice.status == "Overdue").all()
    risks = []
    for idx, inv in enumerate(overdue_invoices[:5]):
        client_q = apply_company_scope(db.query(Client), Client, current_user)
        client = client_q.filter(Client.id == inv.client_id).first() if inv.client_id else None
        days_overdue = (datetime.now().date() - inv.due_date).days if inv.due_date else 0
        risks.append({
            "id": idx + 1,
            "signal": f"Overdue: {client.name if client else 'Unknown'} {inv.invoice_number}",
            "severity": "High" if days_overdue > 14 else ("Medium" if days_overdue > 7 else "Low"),
            "metric": f"₹{inv.total:,.0f}" if inv.total else "₹0",
            "delta": f"-{days_overdue}d",
            "detected": inv.due_date.strftime("%b %d") if inv.due_date else "N/A"
        })
    
    # Dynamic Revenue Trend Insight
    if recent_rev > prev_rev:
        trend_insight = f"Revenue grew by {growth_pct}% over the last 30 days. Steady cash flow."
    elif recent_rev < prev_rev:
        trend_insight = f"Revenue contracted by {growth_pct}% compared to the previous 30 days. Monitor retention."
    else:
        trend_insight = "Revenue is perfectly flat compared to the previous 30 days."

    # Dynamic AI growth insights
    ai_insights = []
    if float(total_revenue) > 0 and float(outstanding) > float(total_revenue) * 0.3:
        ai_insights.append({"tag": "Liquidity", "title": "High AR vs Collected", "evidence": [f"{int((float(outstanding)/float(total_revenue))*100)}% of revenue is pending."]})
    elif growth_pct < 0:
        ai_insights.append({"tag": "Contraction", "title": "Negative Growth", "evidence": [f"Down {abs(growth_pct)}% from last 30d."]})
    else:
        ai_insights.append({"tag": "Healthy", "title": "Stable Cashflow", "evidence": ["Collections nominal."]})

    return {
        "kpis": [
            {"id": 1, "code": "total", "label": "Total Revenue", "value": f"₹{float(total_revenue):,.0f}", "change": f"{growth_pct:+d}%", "trend": growth_trend},
            {"id": 2, "code": "growth", "label": "30D Growth", "value": f"₹{float(recent_rev):,.0f}", "change": f"{growth_pct:+d}%", "trend": growth_trend},
            {"id": 3, "code": "outstanding", "label": "Outstanding", "value": f"₹{float(outstanding):,.0f}", "change": None, "trend": "flat"}
        ],
        "revenueTrend": revenue_trend,
        "trendInsight": trend_insight,
        "risks": risks,
        "breakdown": {
            "byPeriod": breakdown_by_period
        },
        "summaryTable": summary_table,
        "total_revenue": total_revenue,
        "outstanding": outstanding,
        "aiInsights": ai_insights
    }


@router.get("/sales")
def get_company_sales(
    period: str = Query("30d"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_md)
):
    """Get company-wide sales analytics"""
    lead_q = apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.deleted_at.is_(None))
    total = lead_q.count()
    won_leads = lead_q.filter(Lead.status == "Converted").count()
    lost_leads = lead_q.filter(Lead.status == "Lost").count()
    active_leads = total - won_leads - lost_leads
    win_rate = int((won_leads / (won_leads + lost_leads) * 100)) if (won_leads + lost_leads) > 0 else 0
    
    # Team performance (company-scoped)
    teams = apply_company_scope(db.query(Team), Team, current_user).all()
    team_performance = []
    for team in teams:
        team_leads = lead_q.filter(Lead.team_id == team.id, Lead.company_id == current_user.company_id).count()
        team_won = lead_q.filter(Lead.team_id == team.id, Lead.status == "Converted", Lead.company_id == current_user.company_id).count()
        team_performance.append({
            "team": team.name,
            "leads": team_leads,
            "won": team_won,
            "win_rate": int((team_won / team_leads * 100)) if team_leads > 0 else 0
        })
    
    # Daily sales trend (last 7 days)
    now = datetime.now(timezone.utc)
    start_of_today = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    inv_q = apply_company_scope(db.query(Invoice), Invoice, current_user)
    sales_trend = []
    for i in range(7):
        day = start_of_today - timedelta(days=6 - i)
        day_end = day + timedelta(days=1)
        day_rev = inv_q.filter(Invoice.status == "Paid", Invoice.created_at >= day, Invoice.created_at < day_end).with_entities(func.sum(Invoice.total)).scalar() or 0
        day_deals = lead_q.filter(Lead.created_at >= day, Lead.created_at < day_end).count()
        sales_trend.append({"date": day.strftime("%a"), "revenue": float(day_rev), "count": day_deals})
    
    # Trend Observation
    if len(sales_trend) >= 2 and sales_trend[-1]["count"] > sales_trend[-2]["count"]:
        obs = f"Lead volume is accelerating. Today saw {sales_trend[-1]['count']} vs {sales_trend[-2]['count']} yesterday."
    else:
        obs = "Transactional flow is stable. Pipeline requires top-funnel injection."

    # Funnel and Signals
    funnel = {
        "stages": [
            {"name": "Total Deals", "value": total, "color": "var(--accent)"},
            {"name": "Active", "value": active_leads, "color": "var(--secondary)"},
            {"name": "Won", "value": won_leads, "color": "var(--primary)"}
        ],
        "signals": [
            {"label": "Conversion", "value": f"{win_rate}%", "metric": "Target 25%", "status": "positive" if win_rate >= 25 else "warning"}
        ]
    }

    # AI Insights
    ai_insights = []
    active_lead_rows = lead_q.filter(
        Lead.status.notin_(["Converted", "Lost"]),
        Lead.created_at.isnot(None),
    ).with_entities(Lead.created_at).all()
    avg_velocity = 0
    if active_lead_rows:
        total_age_days = 0
        for (created_at,) in active_lead_rows:
            if created_at is None:
                continue
            created_at_utc = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
            total_age_days += max(0, int((now - created_at_utc).total_seconds() // 86400))
        avg_velocity = round(total_age_days / max(1, len(active_lead_rows)))
    if win_rate < 20:
        ai_insights.append({"tag": "Critical", "title": "Conversion Drop", "evidence": [f"Win rate at {win_rate}%"], "link": "/md/monitoring"})
    elif active_leads > (won_leads + lost_leads) * 2:
        ai_insights.append({"tag": "Bottleneck", "title": "Pipeline Congestion", "evidence": [f"{active_leads} active deals aging"], "link": "/md/monitoring"})
    else:
        velocity_text = f"Avg {avg_velocity} days" if avg_velocity > 0 else "No active leads in pipeline"
        ai_insights.append({"tag": "Performance", "title": "Pipeline Velocity", "evidence": [velocity_text], "link": "/md/monitoring"})

    return {
        "summary": {
            "total_deals": total,
            "won": won_leads,
            "lost": lost_leads,
            "active": active_leads,
            "win_rate": win_rate
        },
        "team_performance": team_performance,
        "salesTrend": sales_trend,
        "trendObservation": obs,
        "funnel": funnel,
        "aiInsights": ai_insights
    }


@router.get("/leads")
def get_company_leads(
    status: Optional[str] = Query(None),
    team: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_md)
):
    """Get all company leads with filters (paginated)."""
    query = apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.deleted_at.is_(None))
    
    if status:
        query = query.filter(Lead.status == status)
    
    total = query.count()
    leads = query.order_by(Lead.created_at.desc()).offset(skip).limit(limit).all()
    
    result = []
    team_q = apply_company_scope(db.query(Team), Team, current_user)
    user_q = apply_company_scope(db.query(User), User, current_user)
    for lead in leads:
        team_obj = team_q.filter(Team.id == lead.team_id).first() if lead.team_id else None
        owner = user_q.filter(User.id == lead.assigned_to_id).first() if lead.assigned_to_id else None
        result.append({
            "id": lead.id,
            "name": lead.name,
            "company": lead.company,
            "status": lead.status,
            "team": team_obj.name if team_obj else "Unassigned",
            "owner": owner.full_name if owner else "Unassigned"
        })
    
    return {
        "leads": result,
        "total": total,
        "skip": skip,
        "limit": limit,
        "funnel": [
            {"name": "New", "value": apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.deleted_at.is_(None)).filter(Lead.status == "New").count()},
            {"name": "Contacted", "value": apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.deleted_at.is_(None)).filter(Lead.status == "Contacted").count()},
            {"name": "Qualified", "value": apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.deleted_at.is_(None)).filter(Lead.status == "Qualified").count()},
            {"name": "Proposal", "value": apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.deleted_at.is_(None)).filter(Lead.status == "Proposal").count()},
            {"name": "Converted", "value": apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.deleted_at.is_(None)).filter(Lead.status == "Converted").count()},
        ],
        "sourceBreakdown": [
            {"name": src or "Unknown", "value": cnt, "color": color}
            for (src, cnt), color in zip(
                db.query(Lead.source, func.count(Lead.id)).filter(Lead.company_id == current_user.company_id, Lead.deleted_at.is_(None)).group_by(Lead.source).all() if current_user.company_id
                else db.query(Lead.source, func.count(Lead.id)).filter(Lead.deleted_at.is_(None)).group_by(Lead.source).all(),
                ["var(--accent)", "var(--success)", "var(--warning)", "var(--error)", "var(--secondary)"]
            )
        ],
        "conversionRate": int((apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.deleted_at.is_(None)).filter(Lead.status == "Converted").count() / max(total, 1)) * 100)
    }


@router.get("/clients")
def get_company_clients(
    status: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_md)
):
    """Get all company clients (paginated list + summary)."""
    client_q = apply_company_scope(db.query(Client), Client, current_user)
    total = client_q.count()
    
    clients = client_q.order_by(Client.created_at.desc()).offset(skip).limit(limit).all()
    
    # Growth trend (last 6 months)
    now = datetime.now(timezone.utc)
    growth_trend = []
    for m in range(6):
        month_start = (now.replace(day=1) - timedelta(days=30 * (5 - m))).replace(day=1)
        cumulative = client_q.filter(Client.created_at <= month_start + timedelta(days=31)).count()
        growth_trend.append({"date": month_start.strftime("%b"), "value": cumulative})
    
    return {
        "summary": {
            "total": total,
            "active": total
        },
        "clients": [
            {"id": c.id, "name": c.name, "company": c.company}
            for c in clients
        ],
        "skip": skip,
        "limit": limit,
        "growthTrend": growth_trend,
        "healthDistribution": [
            {"name": "Healthy", "value": total, "color": "var(--success)"},
            {"name": "At Risk", "value": 0, "color": "var(--warning)"},
            {"name": "Churned", "value": 0, "color": "var(--error)"}
        ]
    }


@router.get("/employee-lookup")
def employee_lookup(
    search: Optional[str] = Query(None),
    team: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_md)
):
    """Search and lookup employees"""
    query = apply_company_scope(db.query(User), User, current_user)
    
    if search:
        search_pattern = f"%{search}%"
        query = query.filter(
            (User.full_name.ilike(search_pattern)) |
            (User.email.ilike(search_pattern))
        )
    if role:
        query = query.filter(User.role == role.lower())
    
    total = query.count()
    users = query.order_by(User.created_at.desc()).offset(skip).limit(limit).all()
    
    result = []
    team_q = apply_company_scope(db.query(Team), Team, current_user)
    company_ids = {u.company_id for u in users if u.company_id}
    companies = db.query(Company).filter(Company.id.in_(company_ids)).all() if company_ids else []
    company_map = {c.id: c.company_code for c in companies}
    
    for user in users:
        company_rank = db.query(User).filter(
            User.company_id == user.company_id,
            User.id <= user.id
        ).count()
        prefix = company_map.get(user.company_id) or "EMP"
        
        # Get teams via membership
        user_teams = (
            db.query(Team)
            .join(TeamMembership, TeamMembership.team_id == Team.id)
            .filter(TeamMembership.user_id == user.id, TeamMembership.company_id == current_user.company_id)
            .all()
        )
        result.append({
            "id": f"{prefix}{company_rank:03d}",
            "user_id": user.id,
            "name": user.full_name,
            "email": user.email,
            "role": _role_str(user).title(),
            "team": ", ".join(t.name for t in user_teams) if user_teams else None,
            "status": user.status
        })
    
    return {"employees": result, "total": total, "skip": skip, "limit": limit}


@router.get("/employee-lookup/{user_id}")
def get_employee_detail(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_md)
):
    """Get detailed employee information"""
    user = apply_company_scope(db.query(User), User, current_user).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Employee not found")
    
    # Get teams via membership
    user_teams = (
        db.query(Team)
        .join(TeamMembership, TeamMembership.team_id == Team.id)
        .filter(TeamMembership.user_id == user.id, TeamMembership.company_id == current_user.company_id)
        .all()
    )
    team = user_teams[0] if user_teams else None
    
    # Calculate company rank and prefix
    company_rank = db.query(User).filter(
        User.company_id == user.company_id,
        User.id <= user.id
    ).count()
    company = db.query(Company).filter(Company.id == user.company_id).first()
    prefix = company.company_code if company and company.company_code else "EMP"
    formatted_id = f"{prefix}{company_rank:03d}"
    
    # Performance metrics (company-scoped)
    lead_q = apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.deleted_at.is_(None))
    leads = lead_q.filter(Lead.assigned_to_id == user_id).count()
    converted = lead_q.filter(Lead.assigned_to_id == user_id, Lead.status == "Converted").count()
    
    # Calculate 7-day trends
    now = datetime.now(timezone.utc)
    sales_trend = []
    conversion_trend = []
    for i in range(7):
        day = now - timedelta(days=6 - i)
        day_end = day + timedelta(days=1)
        day_leads = lead_q.filter(Lead.assigned_to_id == user_id, Lead.created_at >= day, Lead.created_at < day_end).count()
        day_conv = lead_q.filter(Lead.assigned_to_id == user_id, Lead.status == "Converted", Lead.updated_at >= day, Lead.updated_at < day_end).count()
        sales_trend.append(day_leads)
        conversion_trend.append(day_conv)

    # Team performance context
    team_metrics = {"leads": 0, "converted": 0, "avg_leads_per_member": 0}
    if team:
        team_members_count = (
            db.query(TeamMembership)
            .filter(TeamMembership.team_id == team.id, TeamMembership.company_id == current_user.company_id)
            .count()
        )
        team_leads = lead_q.filter(Lead.team_id == team.id, Lead.company_id == current_user.company_id).count()
        team_converted = lead_q.filter(Lead.team_id == team.id, Lead.status == "Converted", Lead.company_id == current_user.company_id).count()
        team_metrics = {
            "leads": team_leads,
            "converted": team_converted,
            "avg_leads_per_member": round(team_leads / max(team_members_count, 1), 1)
        }

    return {
        "employee": {
            "id": user.id,
            "formatted_id": formatted_id,
            "name": user.full_name,
            "email": user.email,
            "phone": user.phone,
            "role": user.role,
            "team": team.name if team else None,
            "status": user.status
        },
        "performance": {
            "leads": leads,
            "converted": converted
        },
        "trends": {
            "sales": sales_trend,
            "conversion": conversion_trend,
            "activity": sales_trend  # Fallback for activity
        },
        "team_performance": team_metrics
    }


@router.get("/monitoring")
def get_company_monitoring(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_md)
):
    """Get company-wide monitoring and alerts"""
    now_utc_naive = _utc_now_naive()

    # Get team status (company-scoped)
    teams = apply_company_scope(db.query(Team), Team, current_user).all()
    team_status = []
    lead_q = apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.deleted_at.is_(None))
    for team in teams:
        team_leads = lead_q.filter(Lead.team_id == team.id, Lead.company_id == current_user.company_id).count()
        team_status.append({
            "team": team.name,
            "status": "healthy",
            "leads": team_leads
        })
    
    alerts = []
    ai_interpretation = []
    
    # 1. Overdue tasks
    overdue_tasks = hide_deleted_lead_rows(apply_company_scope(db.query(Task), Task, current_user), Task.lead_id).filter(
        Task.due_date.isnot(None),
        Task.due_date < now_utc_naive,
        Task.status != "Completed"
    ).count()
    if overdue_tasks > 0:
        alerts.append({
            "id": 1, "type": "Operations", "title": "Task SLA Breach", 
            "message": f"{overdue_tasks} overdue tasks", "severity": "High"
        })
        ai_interpretation.append({"type": "RISK", "title": "SLA Degradation", "evidence": [f"{overdue_tasks} missed deadlines"]})

    # 2. Overdue Invoices
    overdue_invoices = apply_company_scope(db.query(Invoice), Invoice, current_user).filter(
        Invoice.status == "Overdue"
    ).count()
    if overdue_invoices > 0:
        alerts.append({
            "id": 2, "type": "Finance", "title": "Liquidity Gap Projected", 
            "message": f"{overdue_invoices} overdue invoices detected", "severity": "High"
        })
        ai_interpretation.append({"type": "FINANCE", "title": "Cashflow Risk", "evidence": [f"{overdue_invoices} unsettled"]})

    # 3. Stalled Leads
    two_weeks_ago = now_utc_naive - timedelta(days=14)
    stalled_leads = lead_q.filter(Lead.status.in_(["New", "Contacted"]), Lead.created_at < two_weeks_ago).count()
    if stalled_leads > 0:
        alerts.append({
            "id": 3, "type": "Sales", "title": "Stalled Deals in Negotiation", 
            "message": f"{stalled_leads} leads stalled for 14+ days", "severity": "Medium"
        })
        ai_interpretation.append({"type": "RISK", "title": "Pipeline Stagnation", "evidence": [f"{stalled_leads} aging leads"]})

    if not ai_interpretation:
        ai_interpretation.append({"type": "INFO", "title": "System Nominal", "evidence": ["All clear"]})

    # Risk trend from real daily snapshots (last 7 days).
    risk_trend = []
    for i in range(6, -1, -1):
        checkpoint_dt = now_utc_naive - timedelta(days=i)
        checkpoint_date = checkpoint_dt.date()
        stall_cutoff = checkpoint_dt - timedelta(days=14)

        overdue_tasks_day = hide_deleted_lead_rows(apply_company_scope(db.query(Task), Task, current_user), Task.lead_id).filter(
            Task.due_date.isnot(None),
            Task.due_date < checkpoint_dt,
            Task.status != "Completed",
            Task.created_at <= checkpoint_dt,
        ).count()
        overdue_invoices_day = apply_company_scope(db.query(Invoice), Invoice, current_user).filter(
            Invoice.due_date.isnot(None),
            Invoice.due_date < checkpoint_date,
            Invoice.status.in_(["Pending", "Overdue"]),
        ).count()
        stalled_leads_day = lead_q.filter(
            Lead.status.in_(["New", "Contacted"]),
            Lead.created_at < stall_cutoff,
            Lead.created_at <= checkpoint_dt,
        ).count()

        risk_value = overdue_tasks_day + overdue_invoices_day + stalled_leads_day
        risk_trend.append({"date": checkpoint_dt.strftime("%a"), "value": risk_value})

    return {
        "alerts": alerts,
        "team_status": team_status,
        "risk_trend": risk_trend,
        "ai_interpretation": ai_interpretation
    }


@router.get("/invoices")
def get_company_invoices(
    status: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_md)
):
    """Get all company invoices for MD view"""
    inv_q = apply_company_scope(db.query(Invoice), Invoice, current_user)
    if status and status != "All":
        inv_q = inv_q.filter(Invoice.status == status)

    total = inv_q.count()
    invoices = inv_q.order_by(Invoice.created_at.desc()).offset(skip).limit(limit).all()

    client_q = apply_company_scope(db.query(Client), Client, current_user)
    user_q = apply_company_scope(db.query(User), User, current_user)
    result = []
    for inv in invoices:
        client = client_q.filter(Client.id == inv.client_id).first() if inv.client_id else None
        creator = user_q.filter(User.id == inv.created_by_id).first() if getattr(inv, "created_by_id", None) else None
        
        result.append({
            "id": inv.invoice_number,
            "db_id": inv.id,
            "client": client.name if client else "Unknown",
            "sales_rep_name": creator.full_name if creator else "System",
            "amount": f"₹{float(inv.total or 0):,.2f}",
            "status": inv.status or "Draft",
            "dueDate": inv.due_date.strftime("%Y-%m-%d") if inv.due_date else None,
            "linkedSale": None,
            "paymentStatus": "Settled" if inv.status == "Paid" else "Awaiting"
        })

    return {"invoices": result, "total": total, "skip": skip, "limit": limit}


def _tier_for_points(points: int) -> str:
    if points >= 2000:
        return "Titanium"
    if points >= 1500:
        return "Platinum"
    if points >= 800:
        return "Gold"
    return "Silver"


def _base_points_for_user(lead_q, user_id: int, period: str) -> tuple[int, int, int]:
    now = datetime.now(timezone.utc)
    cutoff = None
    if period == "30d":
        cutoff = datetime(now.year, now.month, now.day, tzinfo=timezone.utc) - timedelta(days=30)
    elif period == "year":
        cutoff = datetime(now.year, now.month, now.day, tzinfo=timezone.utc) - timedelta(days=365)
    
    q_total = lead_q.filter(Lead.assigned_to_id == user_id)
    q_conv = lead_q.filter(Lead.assigned_to_id == user_id, Lead.status == "Converted", Lead.converted_at.isnot(None))
    q_legacy = lead_q.filter(Lead.assigned_to_id == user_id, Lead.status == "Converted", Lead.converted_at.is_(None))
    
    if cutoff is not None:
        q_total = q_total.filter(Lead.created_at >= cutoff)
        q_conv = q_conv.filter(Lead.converted_at >= cutoff)
        q_legacy = q_legacy.filter(Lead.updated_at >= cutoff)
        
    total_leads = q_total.count()
    converted_leads = q_conv.count()
    legacy_converted = q_legacy.count()
    
    converted = converted_leads + legacy_converted
    points = converted * 500 + (total_leads - converted) * 50
    return total_leads, converted, points


@router.get("/points")
def get_performance_points(
    period: str = Query("30d"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_md)
):
    """Get employee performance points based on lead performance."""
    lead_q = apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.deleted_at.is_(None))
    user_q = apply_company_scope(db.query(User), User, current_user)

    sales_users = user_q.filter(User.role.in_(list(POINT_ELIGIBLE_ROLES))).all()

    company_ids = {u.company_id for u in sales_users if u.company_id}
    companies = db.query(Company).filter(Company.id.in_(company_ids)).all() if company_ids else []
    company_map = {c.id: c.company_code for c in companies}

    performance = []
    for user in sales_users:
        company_rank = db.query(User).filter(
            User.company_id == user.company_id,
            User.id <= user.id
        ).count()
        prefix = company_map.get(user.company_id) or "EMP"

        total_leads, converted, base_points = _base_points_for_user(lead_q, user.id, period)
        points = base_points
        target = 2000

        tier = _tier_for_points(points)

        bonus_amount = points * 5
        trend = "up" if converted > 0 else ("flat" if total_leads > 0 else "down")

        performance.append({
            "id": f"{prefix}{company_rank:03d}",
            "user_id": user.id,
            "name": user.full_name,
            "role": _role_str(user).title(),
            "points": points,
            "tier": tier,
            "target": target,
            "trend": trend,
                "bonus": f"₹{bonus_amount:,}",
            "bonus_amount": bonus_amount,
        })

    performance.sort(key=lambda x: x["points"], reverse=True)
    total = len(performance)
    paginated = performance[skip: skip + limit]
    
    # Summary KPIs
    total_points = sum(p["points"] for p in performance)
    total_bonus = sum(p["points"] * 5 for p in performance)
    top_performer = performance[0] if performance else None
    tier_set = set(p["tier"] for p in performance)
    
    return {
        "performance": paginated,
        "total": total,
        "skip": skip,
        "limit": limit,
        "summary": {
            "totalPoints": total_points,
            "totalBonus": total_bonus,
            "topPerformer": top_performer["name"] if top_performer else "N/A",
            "topTier": top_performer["tier"] if top_performer else "N/A",
            "topPoints": top_performer["points"] if top_performer else 0,
            "tierCount": len(tier_set)
        }
    }


@router.get("/performance/monthly")
def get_monthly_performance(
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_md),
):
    """
    Monthly performance leaderboard (1st to end of month).
    """
    if is_platform_admin(current_user):
        raise HTTPException(status_code=403, detail="Company context required")

    start, end = _month_range_utc(year, month)

    # Sales users in the company
    sales_users = apply_company_scope(db.query(User), User, current_user).filter(User.role == "sales").all()
    sales_ids = [u.id for u in sales_users]
    if not sales_ids:
        return {"year": year, "month": month, "start": start.isoformat(), "end": end.isoformat(), "leaderboard": []}

    inv_q = apply_company_scope(db.query(Invoice.created_by_id, func.sum(Invoice.total)), Invoice, current_user)
    inv_q = inv_q.filter(
        Invoice.created_by_id.in_(sales_ids),
        Invoice.status == "Paid",
        Invoice.paid_date.isnot(None),
        Invoice.paid_date >= start.date(),
        Invoice.paid_date < end.date(),
    ).group_by(Invoice.created_by_id)
    revenue_rows = inv_q.all()
    revenue_map = {uid: float(total or 0) for uid, total in revenue_rows}

    # Conversions: leads assigned to the user, converted, converted_at in month.
    lead_q = apply_company_scope(db.query(Lead.assigned_to_id, func.count(Lead.id)), Lead, current_user).filter(Lead.deleted_at.is_(None))
    lead_q = lead_q.filter(
        Lead.assigned_to_id.in_(sales_ids),
        Lead.status == "Converted",
        Lead.converted_at.isnot(None),
        Lead.converted_at >= start,
        Lead.converted_at < end,
    ).group_by(Lead.assigned_to_id)
    conv_rows = lead_q.all()
    conv_map = {uid: int(cnt or 0) for uid, cnt in conv_rows}

    # Total leads created in month
    total_leads_q = apply_company_scope(db.query(Lead.assigned_to_id, func.count(Lead.id)), Lead, current_user).filter(Lead.deleted_at.is_(None))
    total_leads_q = total_leads_q.filter(
        Lead.assigned_to_id.in_(sales_ids),
        Lead.created_at >= start,
        Lead.created_at < end,
    ).group_by(Lead.assigned_to_id)
    total_rows = total_leads_q.all()
    total_map = {uid: int(cnt or 0) for uid, cnt in total_rows}

    leaderboard = []
    for u in sales_users:
        total = total_map.get(u.id, 0)
        converted = conv_map.get(u.id, 0)
        revenue = revenue_map.get(u.id, 0.0)
        leaderboard.append(
            {
                "user_id": u.id,
                "name": u.full_name,
                "email": u.email,
                "converted_leads": converted,
                "total_leads": total,
                "conversion_rate": round((converted / total * 100), 1) if total else 0.0,
                "revenue": revenue,
            }
        )

    # Sort by revenue first, then conversions
    leaderboard.sort(key=lambda x: (x["revenue"], x["converted_leads"]), reverse=True)

    return {
        "year": year,
        "month": month,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "leaderboard": leaderboard,
        "top_sales_exec": leaderboard[0] if leaderboard else None,
    }


@router.get("/teams")
def get_md_teams(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_md)
):
    """Get all teams with member counts and performance KPIs for MD overview."""
    teams = apply_company_scope(db.query(Team), Team, current_user).all()

    result = []
    for team in teams:
        members = (
            db.query(User)
            .join(TeamMembership, TeamMembership.user_id == User.id)
            .filter(
                TeamMembership.team_id == team.id,
                TeamMembership.company_id == current_user.company_id,
            )
            .all()
        )
        member_ids = [m.id for m in members]

        lead_count = apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.deleted_at.is_(None)).filter(Lead.assigned_to_id.in_(member_ids)).count() if member_ids else 0
        converted = apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.deleted_at.is_(None)).filter(Lead.assigned_to_id.in_(member_ids), Lead.status == "Converted").count() if member_ids else 0
        revenue = apply_company_scope(db.query(func.sum(Invoice.total)), Invoice, current_user).filter(Invoice.created_by_id.in_(member_ids), Invoice.status == "Paid").scalar() or 0 if member_ids else 0
        order_count = apply_company_scope(db.query(Invoice), Invoice, current_user).filter(Invoice.created_by_id.in_(member_ids)).count() if member_ids else 0

        manager = next((m for m in members if m.role == "manager"), None)

        result.append({
            "id": team.id,
            "name": team.name,
            "manager": manager.full_name if manager else "Unassigned",
            "member_count": len([m for m in members if m.role == "sales"]),
            "total_leads": lead_count,
            "converted_leads": converted,
            "conversion_rate": round((converted / lead_count * 100), 1) if lead_count > 0 else 0,
            "revenue": float(revenue),
            "order_count": order_count,
            "members": [
                {
                    "id": m.id,
                    "full_name": m.full_name,
                    "email": m.email,
                    "role": m.role,
                    "status": m.status
                }
                for m in members
            ]
        })

    return {"teams": result, "total": len(result)}


@router.post("/transfer-request", response_model=TransferRequestResponse)
def create_md_transfer_request(
    request_data: TransferRequestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_md)
):
    """Request a team transfer for an employee (MD can request for any manager or sales)"""
    target_user = db.query(User).filter(User.id == request_data.user_id, User.company_id == current_user.company_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="Target user not found")
    
    if target_user.role not in ["manager", "sales"]:
        raise HTTPException(status_code=403, detail="Can only request transfers for Managers or Sales executives")
    
    target_team = db.query(Team).filter(Team.id == request_data.target_team_id, Team.company_id == current_user.company_id).first()
    if not target_team:
        raise HTTPException(status_code=404, detail="Target team not found")
    
    new_request = TeamTransferRequest(
        company_id=current_user.company_id,
        user_id=target_user.id,
        requested_by_id=current_user.id,
        current_team_id=getattr(target_user, "team_id", None),
        target_team_id=request_data.target_team_id,
        reason=request_data.reason,
        status="pending"
    )
    
    db.add(new_request)
    db.commit()
    db.refresh(new_request)
    
    notify_role_users(db, current_user.company_id, role="admin",
        title="New Transfer Request (MD)",
        message=f"MD {current_user.full_name} requested to transfer {target_user.full_name} to {target_team.name}.",
        type="info",
        link="/admin/approvals",
        category="approvals")
    
    return new_request


@router.get("/reports/custom")
def get_custom_report(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    service_type: Optional[str] = Query(None),
    group_by: Optional[str] = Query("date"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_md)
):
    """Generate dynamic custom reports combining Leads and Invoices"""
    from app.services.sales.report_runner import run_leads_invoices_report

    return run_leads_invoices_report(db, current_user, {
        "start_date": start_date,
        "end_date": end_date,
        "source": source,
        "service_type": service_type,
        "group_by": group_by or "date",
    })
