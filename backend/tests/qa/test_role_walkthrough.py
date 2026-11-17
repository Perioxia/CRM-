"""API stand-in for testing.md (web only). Flutter is out of scope.

Each test maps to a section of the manual guide: the page loads as a 200,
the next role sees the write, and the wrong role is refused.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.models.core.team import Team
from app.models.core.team_membership import TeamMembership
from app.models.sales.lead import Lead
from app.services.billing.seed import seed_plans
from app.utils.rate_limit import auth_limiter
from tests.helpers.auth import create_active_user, login_user
from tests.helpers.factories import create_company, schedule_next_activity


@pytest.fixture(autouse=True)
def _reset_auth_rate_limit():
    auth_limiter._buckets.clear()
    yield


def _platform_login(client, email, password="pw"):
    client.headers.pop("X-Team-Id", None)
    resp = client.post(
        "/api/platform/auth/login",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200, resp.text
    client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
    return resp


def _as(client, email, team_id=None):
    client.headers.pop("X-Team-Id", None)
    login_user(client, email)
    if team_id is not None:
        client.headers["X-Team-Id"] = str(team_id)


def _ok(resp, path):
    assert resp.status_code == 200, f"{path} -> {resp.status_code} {resp.text}"
    return resp


@pytest.fixture
def org(db):
    seed_plans(db)
    company = create_company(db, name="Manual QA Co", company_code="QAW")
    team = Team(company_id=company.id, name="QA Alpha")
    db.add(team)
    db.commit()
    db.refresh(team)

    def user(email, role, on_team=False):
        row = create_active_user(
            db,
            email=email,
            role=role,
            company_id=company.id,
            full_name=email.split("@")[0],
            team_id=team.id if on_team else None,
        )
        if on_team:
            db.add(TeamMembership(company_id=company.id, team_id=team.id, user_id=row.id))
            db.commit()
        return row

    platform = create_active_user(
        db, email="qa-platform@qacrm.com", role="admin", company_id=None, full_name="Platform"
    )
    return SimpleNamespace(
        company=company,
        team=team,
        platform=platform,
        admin=user("qa-admin@qacrm.com", "admin"),
        manager=user("qa-manager@qacrm.com", "manager", on_team=True),
        sales=user("qa-sales@qacrm.com", "sales", on_team=True),
        purchase=user("qa-purchase@qacrm.com", "purchase"),
        md=user("qa-md@qacrm.com", "md"),
    )


def test_qa_platform_console_and_signup_approval(client, db):
    """testing.md §1 — operator console, pending signup, approve, reject."""
    seed_plans(db)
    platform = create_active_user(
        db, email="qa-platform@qacrm.com", role="admin", company_id=None, full_name="Platform"
    )
    trial = create_company(db, name="Trial Co", company_code="TRL", status="trial")
    _platform_login(client, platform.email)

    dash = _ok(client.get("/api/platform/metrics/dashboard"), "dashboard")
    assert "business_metrics" in dash.json()
    _ok(client.get("/api/platform/companies/pending"), "pending")
    companies = _ok(client.get("/api/platform/companies"), "companies").json()["companies"]
    assert any(row["name"] == "Trial Co" and row["status"] == "trial" for row in companies)
    assert all(row["status"] != "pending" for row in companies)
    for status_name in ("active", "pending", "suspended", "rejected"):
        _ok(client.get("/api/platform/companies", params={"status": status_name}), status_name)
    detail = _ok(client.get(f"/api/platform/companies/{trial.id}"), "company detail").json()
    assert detail["status"] == "trial"
    assert detail["statistics"]["users"] >= 0
    _ok(client.get("/api/platform/logs"), "logs")
    plans = _ok(client.get("/api/platform/plans"), "plans").json()["plans"]
    assert {p["name"] for p in plans} >= {"Starter", "Growth", "Enterprise"}
    me = _ok(client.get("/api/platform/auth/me"), "session").json()
    assert me["email"] == platform.email or me.get("user", {}).get("email") == platform.email

    company_admin = create_active_user(
        db, email="qa-admin@qacrm.com", role="admin", company_id=trial.id, full_name="Admin"
    )
    _as(client, company_admin.email)
    assert client.post(
        "/api/platform/auth/login",
        data={"username": company_admin.email, "password": "pw"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    ).status_code == 403

    signup = client.post("/api/auth/signup", json={
        "email": "founder@newco-qa.com",
        "password": "s3cret-pw",
        "full_name": "Founder",
        "company_name": "QA Newco",
        "phone": "9990001111",
    })
    assert signup.status_code in (200, 201), signup.text
    blocked = client.post(
        "/api/auth/login",
        data={"username": "founder@newco-qa.com", "password": "s3cret-pw"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert blocked.status_code == 403
    assert "pending" in blocked.json()["detail"].lower()

    reject_signup = client.post("/api/auth/signup", json={
        "email": "founder@reject-qa.com",
        "password": "s3cret-pw",
        "full_name": "Reject Me",
        "company_name": "QA Reject",
        "phone": "9990002222",
    })
    assert reject_signup.status_code in (200, 201), reject_signup.text

    _platform_login(client, platform.email)
    pending = client.get("/api/platform/companies/pending").json()["companies"]
    names = {row["name"]: row["id"] for row in pending}
    assert "QA Newco" in names
    assert "QA Reject" in names
    listed = client.get("/api/platform/companies").json()["companies"]
    assert all(row["name"] not in ("QA Newco", "QA Reject") for row in listed)

    approved = client.post(f"/api/platform/companies/{names['QA Newco']}/approve")
    assert approved.status_code == 200, approved.text
    pending_after = client.get("/api/platform/companies/pending").json()["companies"]
    assert all(row["name"] != "QA Newco" for row in pending_after)
    active = client.get("/api/platform/companies", params={"status": "active"}).json()["companies"]
    assert any(row["name"] == "QA Newco" and row["status"] == "active" for row in active)

    rejected = client.post(
        f"/api/platform/companies/{names['QA Reject']}/reject",
        params={"reason": "QA reject"},
    )
    assert rejected.status_code == 200, rejected.text
    logs = client.get("/api/platform/logs").json()["logs"]
    actions = {row["action"] for row in logs}
    assert "company_approved" in actions

    client.headers.clear()
    founder = client.post(
        "/api/auth/login",
        data={"username": "founder@newco-qa.com", "password": "s3cret-pw"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert founder.status_code == 200, founder.text
    client.headers["Authorization"] = f"Bearer {founder.json()['access_token']}"
    _ok(client.get("/api/admin/dashboard/stats"), "founder dashboard")

    _platform_login(client, platform.email)
    assert client.get("/api/admin/users").status_code in (401, 403)


def test_qa_company_admin_team_invite_and_product(client, org):
    """testing.md §2 — team, invites, users, approvals, audit, catalog item."""
    _as(client, org.admin.email)
    _ok(client.get("/api/admin/dashboard/stats"), "admin dashboard")
    for path in (
        "/api/admin/users",
        "/api/admin/teams",
        "/api/admin/approvals",
        "/api/admin/transfer-requests",
        "/api/admin/audit-log",
        "/api/products",
        "/api/reports",
        "/api/forecasting/report?year=2026&month=9",
    ):
        _ok(client.get(path), path)

    created = client.post("/api/admin/teams", json={"name": "QA Beta"})
    assert created.status_code == 200, created.text
    team_id = created.json()["id"]
    detail = _ok(client.get(f"/api/admin/teams/{team_id}"), "team detail").json()
    assert detail["members"] == []
    assert detail["manager"] is None

    invite = client.post("/api/admin/invites", json={
        "email": "beta-manager@qacrm.com",
        "full_name": "Beta Manager",
        "phone": "9990003333",
        "role": "manager",
        "team_id": team_id,
    })
    assert invite.status_code == 200, invite.text
    sales_invite = client.post("/api/admin/invites", json={
        "email": "beta-sales@qacrm.com",
        "full_name": "Beta Sales",
        "phone": "9990004444",
        "role": "sales",
        "team_id": team_id,
    })
    assert sales_invite.status_code == 200, sales_invite.text
    second_manager = client.post("/api/admin/invites", json={
        "email": "beta-manager-2@qacrm.com",
        "full_name": "Second Manager",
        "phone": "9990005555",
        "role": "manager",
        "team_id": org.team.id,
    })
    assert second_manager.status_code == 400

    users = _ok(
        client.get("/api/admin/users", params={"search": "qa-sales", "role": "sales"}),
        "user search",
    ).json()
    assert any(row["email"] == org.sales.email for row in users["users"])
    _ok(client.get(f"/api/admin/users/{org.sales.id}"), "user detail")
    _ok(client.get("/api/admin/approvals"), "approvals")

    audit = _ok(client.get("/api/admin/audit-log"), "audit").json()
    blob = str(audit)
    assert "invite" in blob.lower() or "team" in blob.lower()

    product = client.post("/api/products", json={
        "name": "QA Catalog Widget",
        "unit_price": 100,
        "tax_rate": 18,
    })
    assert product.status_code == 201, product.text
    listed = _ok(client.get("/api/products", params={"q": "QA Catalog"}), "products").json()
    assert any(item["name"] == "QA Catalog Widget" for item in listed["items"])


def test_qa_manager_task_and_unassigned_lead(client, org, db):
    """testing.md §3 — manager assigns a task and a lead; sales cannot assign."""
    today = datetime.now(timezone.utc).date().isoformat()
    _as(client, org.manager.email, org.team.id)
    _ok(client.get("/api/manager/dashboard"), "manager dashboard")
    roster = _ok(client.get("/api/manager/team"), "team").json()
    assert str(roster).find(org.sales.email) != -1 or str(roster).find(str(org.sales.id)) != -1

    created = client.post("/api/manager/tasks", params={
        "title": "QA call client",
        "assignee_id": org.sales.id,
        "due_date": today,
        "priority": "medium",
    })
    assert created.status_code == 200, created.text
    task_id = created.json()["task"]["id"]
    assert task_id

    listed = _ok(client.get("/api/manager/tasks"), "manager tasks").json()["tasks"]
    assert any(row["id"] == task_id and row["title"] == "QA call client" for row in listed)

    _as(client, org.sales.email, org.team.id)
    sales_tasks = _ok(client.get("/api/tasks/list"), "sales tasks").json()
    rows = sales_tasks.get("tasks") or sales_tasks.get("items") or []
    assert any(row["id"] == task_id for row in rows)
    done = client.post(f"/api/tasks/{task_id}/complete")
    assert done.status_code == 200, done.text

    _as(client, org.manager.email, org.team.id)
    completed = _ok(
        client.get("/api/manager/tasks", params={"status": "Completed"}),
        "completed",
    ).json()["tasks"]
    assert any(row["id"] == task_id for row in completed)

    # Create stays unassigned only if no territory/workflow claims it. Seed an open row
    # so the pool case is the one the manual guide describes.
    open_lead = Lead(
        company_id=org.company.id,
        name="Pool Lead",
        status="Active",
        team_id=org.team.id,
        assigned_to_id=None,
    )
    db.add(open_lead)
    db.commit()
    db.refresh(open_lead)
    lead_id = open_lead.id

    _as(client, org.sales.email, org.team.id)
    denied = client.put(f"/api/leads/{lead_id}", json={"assigned_to_id": org.sales.id})
    assert denied.status_code == 403, denied.text

    _as(client, org.manager.email, org.team.id)
    pool = _ok(client.get("/api/leads", params={"unassigned": "true"}), "pool").json()
    assert any(row["id"] == lead_id for row in pool["items"])
    assigned = client.put(f"/api/leads/{lead_id}", json={"assigned_to_id": org.sales.id})
    assert assigned.status_code == 200, assigned.text

    _as(client, org.sales.email, org.team.id)
    mine = _ok(client.get("/api/leads"), "sales leads").json()
    assert any(row["id"] == lead_id for row in mine["items"])
    still_open = client.get("/api/leads", params={"unassigned": "true"}).json()
    assert all(row["id"] != lead_id for row in still_open["items"])


def test_qa_sales_purchase_and_md_see_the_same_work(client, org):
    """testing.md §4–§6 — lead, deal, quote, order, purchase, MD."""
    _as(client, org.admin.email)
    product = client.post("/api/products", json={
        "name": "QA Catalog Widget",
        "unit_price": 100,
        "tax_rate": 18,
    })
    assert product.status_code == 201, product.text

    _as(client, org.sales.email, org.team.id)
    for path in (
        "/api/tasks/list",
        "/api/follow-ups",
        "/api/whatsapp/threads",
        "/api/meetings/summary",
        "/api/leads",
        "/api/leads/trash",
        "/api/clients",
        "/api/accounts",
        "/api/deals",
        "/api/quotes",
        "/api/sales-orders",
        "/api/inventory",
        "/api/products",
        "/api/forecasting/report?year=2026&month=9",
    ):
        _ok(client.get(path), path)
    catalog = client.get("/api/products", params={"q": "QA Catalog"}).json()
    assert any(item["name"] == "QA Catalog Widget" for item in catalog["items"])

    own_task = client.post("/api/tasks", json={
        "title": "QA own task",
        "priority": "Medium",
        "due_date": datetime.now(timezone.utc).date().isoformat(),
    })
    assert own_task.status_code == 201, own_task.text

    lead = client.post("/api/leads", json={
        "name": "QA Buyer",
        "email": "buyer@qacrm.com",
        "team_id": org.team.id,
    })
    assert lead.status_code == 201, lead.text
    lead_id = lead.json()["id"]
    note = client.post(f"/api/leads/{lead_id}/notes", params={"content": "Called the buyer"})
    assert note.status_code == 201, note.text
    status = client.patch(f"/api/leads/{lead_id}/status", json={"status": "Contacted"})
    assert status.status_code == 200, status.text
    converted = client.post(f"/api/leads/{lead_id}/convert")
    assert converted.status_code == 200, converted.text
    client_id = converted.json()["client_id"]
    clients = _ok(client.get("/api/clients"), "clients").json()
    client_rows = clients.get("clients") or clients.get("items") or []
    assert any(row["id"] == client_id for row in client_rows)

    deal = client.post("/api/deals", json={
        "title": "QA job",
        "amount": "1000.00",
        "client_id": client_id,
        "team_id": org.team.id,
    })
    assert deal.status_code == 201, deal.text
    deal_body = deal.json()
    stages = _ok(
        client.get("/api/deals/stages", params={"pipeline_id": deal_body["pipeline_id"]}),
        "stages",
    ).json()
    stage_rows = stages if isinstance(stages, list) else stages.get("stages") or stages.get("items")
    current = next(row for row in stage_rows if row["id"] == deal_body["stage_id"])
    nxt = next(
        (row for row in stage_rows if row["position"] == current["position"] + 1 and row.get("stage_type", "open") == "open"),
        None,
    )
    if nxt is not None:
        scheduled = schedule_next_activity(client, deal_body["id"])
        assert scheduled.status_code == 201, scheduled.text
        moved = client.patch(f"/api/deals/{deal_body['id']}/stage", json={"stage_id": nxt["id"]})
        assert moved.status_code == 200, moved.text

    invoice_ids = []
    for title, email in (("QA job A", "a@qacrm.com"), ("QA job B", "b@qacrm.com")):
        extra_lead = client.post("/api/leads", json={
            "name": title,
            "email": email,
            "team_id": org.team.id,
        })
        assert extra_lead.status_code == 201, extra_lead.text
        extra_client = client.post(f"/api/leads/{extra_lead.json()['id']}/convert").json()["client_id"]
        extra_deal = client.post("/api/deals", json={
            "title": title,
            "amount": "50.00",
            "client_id": extra_client,
            "team_id": org.team.id,
        })
        assert extra_deal.status_code == 201, extra_deal.text
        quote = client.post("/api/quotes", json={
            "deal_id": extra_deal.json()["id"],
            "client_id": extra_client,
            "items": [{"description": title, "quantity": 1, "unit_price": "50.00"}],
        })
        assert quote.status_code == 201, quote.text
        accepted = client.post(f"/api/quotes/{quote.json()['id']}/accept")
        assert accepted.status_code == 200, accepted.text
        order = client.post(f"/api/sales-orders/{accepted.json()['sales_order_id']}/invoice")
        assert order.status_code == 200, order.text
        invoice_ids.append(order.json()["invoice_id"])

    quotes = _ok(client.get("/api/quotes"), "quotes").json()
    assert quotes["total"] >= 2
    orders = _ok(client.get("/api/sales-orders"), "orders").json()
    assert orders["total"] >= 2

    assert client.get("/api/admin/users").status_code == 403
    assert client.get("/api/platform/companies").status_code in (401, 403)

    _as(client, org.purchase.email)
    for path in (
        "/api/purchase/dashboard",
        "/api/purchase/sales",
        "/api/purchase/invoices",
        "/api/inventory",
        "/api/products",
    ):
        _ok(client.get(path), path)
    assert any(
        item["name"] == "QA Catalog Widget"
        for item in client.get("/api/products", params={"q": "QA Catalog"}).json()["items"]
    )
    # Invoices made from sales orders wait for purchase like any other order.
    approved = client.post(f"/api/purchase/sales/{invoice_ids[0]}/approve")
    assert approved.status_code == 200, approved.text
    rejected = client.post(
        f"/api/purchase/sales/{invoice_ids[1]}/reject",
        params={"reason": "QA reject"},
    )
    assert rejected.status_code == 200, rejected.text

    draft = client.post("/api/invoices", json={
        "client_id": client_id,
        "items": [{"description": "QA draft", "quantity": 1, "unit_price": 10}],
    })
    assert draft.status_code == 201, draft.text
    draft_id = draft.json()["id"]
    opened = _ok(client.get(f"/api/purchase/invoices/{draft_id}"), "invoice detail")
    assert opened.json()["id"] == draft_id or opened.json().get("db_id") == draft_id
    sent = client.post(f"/api/purchase/invoices/{draft_id}/send")
    assert sent.status_code == 200, sent.text
    paid = client.post(
        f"/api/purchase/invoices/{draft_id}/mark-paid",
        params={"payment_date": datetime.now(timezone.utc).date().isoformat(), "payment_method": "upi"},
    )
    assert paid.status_code == 200, paid.text

    pool = client.get("/api/leads", params={"unassigned": "true"})
    assert pool.status_code in (200, 403)
    if pool.status_code == 200:
        assert pool.json()["items"] == [] or pool.json()["total"] == 0
    assert client.get("/api/whatsapp/threads").status_code in (401, 403)

    _as(client, org.md.email)
    for path in (
        "/api/md/dashboard",
        "/api/md/revenue",
        "/api/md/teams",
        "/api/md/monitoring",
        "/api/md/sales",
        "/api/md/points",
        "/api/md/employee-lookup",
        "/api/leads",
        "/api/clients",
        "/api/accounts",
        "/api/deals",
        "/api/inventory",
        "/api/products",
        "/api/md/invoices",
        "/api/meetings/summary",
        "/api/whatsapp/threads",
        "/api/reports",
        "/api/forecasting/report?year=2026&month=9",
        "/api/md/reports/custom",
    ):
        _ok(client.get(path), path)
    found = _ok(
        client.get("/api/md/employee-lookup", params={"search": "qa-sales"}),
        "lookup",
    ).json()
    assert "qa-sales@qacrm.com" in str(found)
    md_invoices = client.get("/api/md/invoices").json()["invoices"]
    assert any(row["db_id"] == draft_id for row in md_invoices)


def test_qa_settings_shared_modules_and_public(client, org):
    """testing.md §7 — settings and shared modules load; public endpoints do not crash.

    Skipped: live Tally, IRN, Gmail, calendar OAuth, telephony, and bug-report
    submit (that endpoint emails a real inbox).
    """
    _as(client, org.admin.email)
    for path in (
        "/api/mailbox",
        "/api/calendar",
        "/api/webhooks/endpoints",
        "/api/saml/config",
        "/api/auth/oauth/providers",
        "/api/privacy/me",
        "/api/privacy/retention",
        "/api/telephony/connection",
        "/api/territories",
        "/api/sandbox",
        "/api/modules",
        "/api/scoring/rules",
        "/api/leaves",
        "/api/einvoice/connection",
        "/api/settings/approvals",
        "/api/price-books",
        "/api/quote-plans",
        "/api/marketplace/apps",
        "/api/api-keys",
        "/api/whatsapp/templates",
        "/api/accounting/connection",
        "/api/predictions/models",
        "/api/email-templates",
        "/api/cases",
        "/api/campaigns",
        "/api/mass-email",
        "/api/ledgers/payments_received",
    ):
        _ok(client.get(path), path)

    health = client.get("/health")
    assert health.status_code == 200, health.text
    assert health.json()["database"] == "up"
    forgot = client.post("/api/auth/forgot-password", json={"email": "nobody@qacrm.com"})
    assert forgot.status_code == 200, forgot.text
