from sqlalchemy import inspect

from app.models.core.enums import DashboardWidgetViz, SavedReportType
from app.models.sales.dashboard import Dashboard, DashboardWidget
from app.models.sales.saved_report import SavedReport
from tests.helpers.factories import create_company


def test_saved_report_and_dashboard_tables_exist_and_have_company_id(db_engine):
    tables = set(inspect(db_engine).get_table_names())
    assert {"saved_reports", "dashboards", "dashboard_widgets"} <= tables
    for t in ("saved_reports", "dashboards", "dashboard_widgets"):
        cols = {c["name"] for c in inspect(db_engine).get_columns(t)}
        assert "company_id" in cols


def test_saved_report_type_and_widget_viz_values():
    assert SavedReportType.LEADS_INVOICES.value == "leads_invoices"
    assert DashboardWidgetViz.KPI.value == "kpi"
    assert DashboardWidgetViz.CHART.value == "chart"
    assert DashboardWidgetViz.TABLE.value == "table"


def test_can_persist_a_saved_report_dashboard_and_widget(db):
    company = create_company(db, name="Schema Co 1", company_code="S01")
    report = SavedReport(
        company_id=company.id,
        name="Q3 leads",
        report_type=SavedReportType.LEADS_INVOICES,
        filters={"group_by": "source", "start_date": "2026-07-01"},
    )
    dashboard = Dashboard(company_id=company.id, name="Company dashboard")
    db.add_all([report, dashboard])
    db.commit()
    db.refresh(report)
    db.refresh(dashboard)
    widget = DashboardWidget(
        company_id=company.id,
        dashboard_id=dashboard.id,
        saved_report_id=report.id,
        visualization=DashboardWidgetViz.KPI,
        position=0,
    )
    db.add(widget)
    db.commit()
    db.refresh(widget)
    assert report.id is not None
    assert dashboard.id is not None
    assert widget.id is not None
    assert report.filters["group_by"] == "source"
    assert widget.visualization == DashboardWidgetViz.KPI
