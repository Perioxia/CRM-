from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.core.enums import DealStageType, InvoiceStatus
from app.models.finance.invoice import Invoice
from app.models.sales.client import Client
from app.models.sales.deal import Deal
from app.models.sales.lead import Lead
from app.models.sales.pipeline import PipelineStage
from app.models.core.user import User
from app.services.sales.pipeline_seed import ensure_default_pipeline
from app.utils.dependencies import apply_company_scope

ALLOWED_GROUP_BY = {"date", "source", "service_type"}
PIPELINE_GROUP_BY = {"stage", "owner"}
GST_GROUP_BY = {"date", "status"}
ALLOWED_REPORT_TYPES = {"leads_invoices", "deals_pipeline", "gst_invoices"}
ALLOWED_INVOICE_STATUSES = {s.value for s in InvoiceStatus}


def _parse_date(value, field):
    if value is None or value == "" or value == "All":
        return None
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"Invalid {field}")
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid {field}; use YYYY-MM-DD")


def normalize_filters(raw, report_type: str = "leads_invoices") -> dict:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="filters must be an object")
    report_type = normalize_report_type(report_type)
    if report_type == "deals_pipeline":
        group_by = raw.get("group_by") or "stage"
        if group_by not in PIPELINE_GROUP_BY:
            raise HTTPException(
                status_code=400,
                detail="Invalid group_by. Allowed: stage, owner.",
            )
        start = raw.get("start_date")
        end = raw.get("end_date")
        _parse_date(start, "start_date")
        _parse_date(end, "end_date")
        pipeline_id = raw.get("pipeline_id")
        if pipeline_id in ("", None, "All"):
            pipeline_id = None
        elif pipeline_id is not None:
            try:
                pipeline_id = int(pipeline_id)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Invalid pipeline_id")
        return {
            "start_date": start or None,
            "end_date": end or None,
            "pipeline_id": pipeline_id,
            "group_by": group_by,
        }
    if report_type == "gst_invoices":
        group_by = raw.get("group_by") or "date"
        if group_by not in GST_GROUP_BY:
            raise HTTPException(
                status_code=400,
                detail="Invalid group_by. Allowed: date, status.",
            )
        start = raw.get("start_date")
        end = raw.get("end_date")
        _parse_date(start, "start_date")
        _parse_date(end, "end_date")
        status = raw.get("status")
        if status in ("", "All", None):
            status = None
        elif status not in ALLOWED_INVOICE_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid invoice status filter")
        return {
            "start_date": start or None,
            "end_date": end or None,
            "status": status,
            "group_by": group_by,
        }
    group_by = raw.get("group_by") or "date"
    if group_by not in ALLOWED_GROUP_BY:
        raise HTTPException(
            status_code=400,
            detail="Invalid group_by. Allowed: date, source, service_type.",
        )
    start = raw.get("start_date")
    end = raw.get("end_date")
    _parse_date(start, "start_date")
    _parse_date(end, "end_date")
    source = raw.get("source")
    if source in ("", "All"):
        source = None
    service_type = raw.get("service_type")
    if service_type in ("", "All"):
        service_type = None
    return {
        "start_date": start or None,
        "end_date": end or None,
        "source": source,
        "service_type": service_type,
        "group_by": group_by,
    }


def normalize_report_type(raw: str) -> str:
    value = (raw or "").strip()
    if value not in ALLOWED_REPORT_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Invalid report_type. Allowed: leads_invoices, deals_pipeline, gst_invoices.",
        )
    return value


def _day_key(column, db: Session):
    bind = db.get_bind()
    dialect = bind.dialect.name if bind is not None else "sqlite"
    if dialect == "postgresql":
        return func.to_char(column, "YYYY-MM-DD")
    return func.strftime("%Y-%m-%d", column)


def run_leads_invoices_report(db: Session, current_user, filters: dict) -> dict:
    filters = normalize_filters(filters)
    start_date = filters.get("start_date")
    end_date = filters.get("end_date")
    source = filters.get("source")
    service_type = filters.get("service_type")
    group_by = filters.get("group_by") or "date"

    lead_q = apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.deleted_at.is_(None))
    inv_q = apply_company_scope(db.query(Invoice, Lead.source, Lead.service_type), Invoice, current_user)
    inv_q = inv_q.outerjoin(Client, Invoice.client_id == Client.id).outerjoin(
        Lead, Client.converted_from_lead_id == Lead.id
    )

    inv_sum_q = apply_company_scope(db.query(func.sum(Invoice.total)), Invoice, current_user)
    inv_sum_q = inv_sum_q.outerjoin(Client, Invoice.client_id == Client.id).outerjoin(
        Lead, Client.converted_from_lead_id == Lead.id
    )

    inv_count_q = apply_company_scope(db.query(func.count(Invoice.id)), Invoice, current_user)
    inv_count_q = inv_count_q.outerjoin(Client, Invoice.client_id == Client.id).outerjoin(
        Lead, Client.converted_from_lead_id == Lead.id
    )

    dt_start = _parse_date(start_date, "start_date")
    if dt_start:
        lead_q = lead_q.filter(Lead.created_at >= dt_start)
        inv_q = inv_q.filter(Invoice.created_at >= dt_start)
        inv_sum_q = inv_sum_q.filter(Invoice.created_at >= dt_start)
        inv_count_q = inv_count_q.filter(Invoice.created_at >= dt_start)

    dt_end = _parse_date(end_date, "end_date")
    if dt_end:
        dt_end_excl = dt_end + timedelta(days=1)
        lead_q = lead_q.filter(Lead.created_at < dt_end_excl)
        inv_q = inv_q.filter(Invoice.created_at < dt_end_excl)
        inv_sum_q = inv_sum_q.filter(Invoice.created_at < dt_end_excl)
        inv_count_q = inv_count_q.filter(Invoice.created_at < dt_end_excl)

    if source:
        lead_q = lead_q.filter(Lead.source == source)
        inv_q = inv_q.filter(Lead.source == source)
        inv_sum_q = inv_sum_q.filter(Lead.source == source)
        inv_count_q = inv_count_q.filter(Lead.source == source)

    if service_type:
        lead_q = lead_q.filter(Lead.service_type == service_type)
        inv_q = inv_q.filter(Lead.service_type == service_type)
        inv_sum_q = inv_sum_q.filter(Lead.service_type == service_type)
        inv_count_q = inv_count_q.filter(Lead.service_type == service_type)

    total_leads = lead_q.count()
    converted_leads = lead_q.filter(Lead.status == "Converted").count()
    total_revenue = inv_sum_q.filter(Invoice.status == "Paid").scalar() or 0
    total_invoices = inv_count_q.scalar() or 0

    chart_data = {}
    if group_by == "source":
        lead_groups = lead_q.with_entities(Lead.source, func.count(Lead.id)).group_by(Lead.source).all()
        for src, cnt in lead_groups:
            name = src or "Unknown"
            chart_data[name] = {"name": name, "leads": cnt, "revenue": 0}
        inv_groups = (
            inv_sum_q.with_entities(Lead.source, func.sum(Invoice.total))
            .filter(Invoice.status == "Paid")
            .group_by(Lead.source)
            .all()
        )
        for src, rev in inv_groups:
            name = src or "Unknown"
            if name not in chart_data:
                chart_data[name] = {"name": name, "leads": 0, "revenue": 0}
            chart_data[name]["revenue"] = float(rev or 0)
    elif group_by == "service_type":
        lead_groups = lead_q.with_entities(Lead.service_type, func.count(Lead.id)).group_by(Lead.service_type).all()
        for srv, cnt in lead_groups:
            name = srv or "Unknown"
            chart_data[name] = {"name": name, "leads": cnt, "revenue": 0}
        inv_groups = (
            inv_sum_q.with_entities(Lead.service_type, func.sum(Invoice.total))
            .filter(Invoice.status == "Paid")
            .group_by(Lead.service_type)
            .all()
        )
        for srv, rev in inv_groups:
            name = srv or "Unknown"
            if name not in chart_data:
                chart_data[name] = {"name": name, "leads": 0, "revenue": 0}
            chart_data[name]["revenue"] = float(rev or 0)
    else:
        day_key = _day_key(Lead.created_at, db)
        lead_groups = (
            lead_q.with_entities(day_key, func.count(Lead.id))
            .group_by(day_key)
            .all()
        )
        for dt, cnt in lead_groups:
            name = dt if dt else "Unknown"
            chart_data[name] = {"name": name, "leads": cnt, "revenue": 0}
        inv_day = _day_key(Invoice.created_at, db)
        inv_groups = (
            inv_sum_q.with_entities(inv_day, func.sum(Invoice.total))
            .filter(Invoice.status == "Paid")
            .group_by(inv_day)
            .all()
        )
        for dt, rev in inv_groups:
            name = dt if dt else "Unknown"
            if name not in chart_data:
                chart_data[name] = {"name": name, "leads": 0, "revenue": 0}
            chart_data[name]["revenue"] = float(rev or 0)

    grid_results = inv_q.order_by(Invoice.created_at.desc()).limit(50).all()
    client_ids = [inv.client_id for inv, _, _ in grid_results]
    clients = db.query(Client).filter(Client.id.in_(client_ids)).all() if client_ids else []
    client_map = {c.id: c.name for c in clients}
    grid_data = []
    for inv, l_src, l_stype in grid_results:
        status_val = inv.status
        if hasattr(status_val, "value"):
            status_val = status_val.value
        grid_data.append({
            "id": inv.invoice_number,
            "client": client_map.get(inv.client_id, "Unknown"),
            "amount": float(inv.total or 0),
            "status": status_val,
            "source": l_src or "Unknown",
            "service_type": l_stype or "Unknown",
            "date": inv.created_at.strftime("%Y-%m-%d") if inv.created_at else "",
        })

    chart_list = list(chart_data.values())
    if group_by == "date":
        chart_list.sort(key=lambda x: x["name"])
    else:
        chart_list.sort(key=lambda x: x["revenue"], reverse=True)

    return {
        "kpis": {
            "totalRevenue": float(total_revenue),
            "totalInvoices": total_invoices,
            "totalLeads": total_leads,
            "convertedLeads": converted_leads,
        },
        "chartData": chart_list,
        "gridData": grid_data,
    }


def _apply_date_range(query, column, start_date, end_date, field_prefix=""):
    dt_start = _parse_date(start_date, f"{field_prefix}start_date" if field_prefix else "start_date")
    if dt_start:
        query = query.filter(column >= dt_start)
    dt_end = _parse_date(end_date, f"{field_prefix}end_date" if field_prefix else "end_date")
    if dt_end:
        query = query.filter(column < dt_end + timedelta(days=1))
    return query


def run_deals_pipeline_report(db: Session, current_user, filters: dict) -> dict:
    filters = normalize_filters(filters, "deals_pipeline")
    pipeline_id = filters.get("pipeline_id")
    if pipeline_id is None:
        pipeline_id = ensure_default_pipeline(db, current_user.company_id).id
    group_by = filters.get("group_by") or "stage"

    stages = (
        apply_company_scope(db.query(PipelineStage), PipelineStage, current_user)
        .filter(PipelineStage.pipeline_id == pipeline_id)
        .order_by(PipelineStage.position)
        .all()
    )
    stage_map = {s.id: s for s in stages}

    deals_q = apply_company_scope(db.query(Deal), Deal, current_user).filter(Deal.pipeline_id == pipeline_id)
    deals_q = _apply_date_range(deals_q, Deal.created_at, filters.get("start_date"), filters.get("end_date"))
    deals = deals_q.all()

    open_deals = []
    won_value = Decimal("0")
    open_value = Decimal("0")
    weighted_forecast = Decimal("0")
    for deal in deals:
        stage = stage_map.get(deal.stage_id)
        if stage is None:
            continue
        if stage.stage_type == DealStageType.WON:
            won_value += deal.amount or Decimal("0")
        elif stage.stage_type == DealStageType.OPEN:
            open_deals.append(deal)
            amt = deal.amount or Decimal("0")
            open_value += amt
            eff = deal.probability if deal.probability is not None else (stage.default_probability or 0)
            weighted_forecast += amt * Decimal(eff) / Decimal(100)

    chart_data = {}
    if group_by == "owner":
        for deal in open_deals:
            owner = deal.assigned_to_id
            name = "Unassigned"
            if owner:
                user = db.query(User).filter(User.id == owner).first()
                name = (user.full_name or user.email or f"User {owner}") if user else f"User {owner}"
            if name not in chart_data:
                chart_data[name] = {"name": name, "deals": 0, "value": 0.0}
            chart_data[name]["deals"] += 1
            chart_data[name]["value"] += float(deal.amount or 0)
    else:
        for deal in open_deals:
            stage = stage_map.get(deal.stage_id)
            name = stage.name if stage else "Unknown"
            if name not in chart_data:
                chart_data[name] = {"name": name, "deals": 0, "value": 0.0}
            chart_data[name]["deals"] += 1
            chart_data[name]["value"] += float(deal.amount or 0)

    client_ids = [d.client_id for d in open_deals if d.client_id]
    clients = db.query(Client).filter(Client.id.in_(client_ids)).all() if client_ids else []
    client_map = {c.id: c.name for c in clients}

    grid_data = []
    for deal in sorted(open_deals, key=lambda d: d.created_at or datetime.min, reverse=True)[:50]:
        stage = stage_map.get(deal.stage_id)
        eff = deal.probability if deal.probability is not None else (stage.default_probability if stage else 0)
        grid_data.append({
            "title": deal.title,
            "client": client_map.get(deal.client_id, "—"),
            "stage": stage.name if stage else "Unknown",
            "amount": float(deal.amount or 0),
            "probability": eff or 0,
            "expected_close": deal.expected_close.isoformat() if deal.expected_close else "",
            "owner_id": deal.assigned_to_id,
        })

    chart_list = list(chart_data.values())
    chart_list.sort(key=lambda x: x["value"], reverse=True)

    return {
        "kpis": {
            "openDeals": len(open_deals),
            "openValue": float(open_value),
            "weightedForecast": float(weighted_forecast.quantize(Decimal("0.01"))),
            "wonValue": float(won_value),
        },
        "chartData": chart_list,
        "gridData": grid_data,
    }


def run_gst_invoices_report(db: Session, current_user, filters: dict) -> dict:
    filters = normalize_filters(filters, "gst_invoices")
    group_by = filters.get("group_by") or "date"
    status_filter = filters.get("status")

    inv_q = apply_company_scope(db.query(Invoice), Invoice, current_user)
    inv_q = _apply_date_range(inv_q, Invoice.created_at, filters.get("start_date"), filters.get("end_date"))
    if status_filter:
        inv_q = inv_q.filter(Invoice.status == status_filter)
    invoices = inv_q.order_by(Invoice.created_at.desc()).all()

    total_cgst = sum(float(i.cgst or 0) for i in invoices)
    total_sgst = sum(float(i.sgst or 0) for i in invoices)
    total_igst = sum(float(i.igst or 0) for i in invoices)
    total_taxable = sum(float(i.subtotal or 0) for i in invoices)
    total_amount = sum(float(i.total or 0) for i in invoices)

    chart_data = {}
    if group_by == "status":
        for inv in invoices:
            status_val = inv.status.value if hasattr(inv.status, "value") else str(inv.status)
            if status_val not in chart_data:
                chart_data[status_val] = {"name": status_val, "invoices": 0, "tax": 0.0}
            chart_data[status_val]["invoices"] += 1
            chart_data[status_val]["tax"] += float((inv.cgst or 0) + (inv.sgst or 0) + (inv.igst or 0))
    else:
        day_key = _day_key(Invoice.created_at, db)
        groups = (
            inv_q.with_entities(day_key, func.count(Invoice.id), func.sum(Invoice.cgst + Invoice.sgst + Invoice.igst))
            .group_by(day_key)
            .all()
        )
        for dt, cnt, tax_sum in groups:
            name = dt if dt else "Unknown"
            chart_data[name] = {"name": name, "invoices": cnt, "tax": float(tax_sum or 0)}

    client_ids = [i.client_id for i in invoices[:50]]
    clients = db.query(Client).filter(Client.id.in_(client_ids)).all() if client_ids else []
    client_map = {c.id: c.name for c in clients}

    grid_data = []
    for inv in invoices[:50]:
        status_val = inv.status.value if hasattr(inv.status, "value") else str(inv.status)
        grid_data.append({
            "invoice": inv.invoice_number,
            "client": client_map.get(inv.client_id, "Unknown"),
            "date": inv.created_at.strftime("%Y-%m-%d") if inv.created_at else "",
            "seller_gstin": inv.seller_gstin or "",
            "buyer_gstin": inv.buyer_gstin or "",
            "cgst": float(inv.cgst or 0),
            "sgst": float(inv.sgst or 0),
            "igst": float(inv.igst or 0),
            "total": float(inv.total or 0),
            "irn": inv.irn or "",
            "status": status_val,
        })

    chart_list = list(chart_data.values())
    if group_by == "date":
        chart_list.sort(key=lambda x: x["name"])
    else:
        chart_list.sort(key=lambda x: x["tax"], reverse=True)

    return {
        "kpis": {
            "totalInvoices": len(invoices),
            "totalTaxable": total_taxable,
            "totalCgst": total_cgst,
            "totalSgst": total_sgst,
            "totalIgst": total_igst,
            "totalAmount": total_amount,
        },
        "chartData": chart_list,
        "gridData": grid_data,
    }


def run_report(db: Session, current_user, report_type: str, filters: dict) -> dict:
    report_type = normalize_report_type(report_type)
    if report_type == "deals_pipeline":
        return run_deals_pipeline_report(db, current_user, filters)
    if report_type == "gst_invoices":
        return run_gst_invoices_report(db, current_user, filters)
    return run_leads_invoices_report(db, current_user, filters)


def report_csv_headers_and_rows(report_type: str, result: dict) -> tuple[list[str], list[list]]:
    report_type = normalize_report_type(report_type)
    if report_type == "deals_pipeline":
        headers = ["Title", "Client", "Stage", "Amount", "Probability", "Expected close"]
        rows = [
            [r.get("title"), r.get("client"), r.get("stage"), r.get("amount"), r.get("probability"), r.get("expected_close")]
            for r in result.get("gridData") or []
        ]
        return headers, rows
    if report_type == "gst_invoices":
        headers = ["Invoice", "Client", "Date", "Seller GSTIN", "Buyer GSTIN", "CGST", "SGST", "IGST", "Total", "IRN", "Status"]
        rows = [
            [
                r.get("invoice"), r.get("client"), r.get("date"), r.get("seller_gstin"), r.get("buyer_gstin"),
                r.get("cgst"), r.get("sgst"), r.get("igst"), r.get("total"), r.get("irn"), r.get("status"),
            ]
            for r in result.get("gridData") or []
        ]
        return headers, rows
    headers = ["Invoice", "Client", "Date", "Source", "Product", "Status", "Amount"]
    rows = [
        [r.get("id"), r.get("client"), r.get("date"), r.get("source"), r.get("service_type"), r.get("status"), r.get("amount")]
        for r in result.get("gridData") or []
    ]
    return headers, rows
