"""Reps cannot change company accounts, hide deals by moving them, or read the raw audit log."""
from app.models.sales.account import Account
from app.models.sales.deal import Deal
from app.models.sales.task import Task
from app.services.sales.pipeline_seed import ensure_default_pipeline


def _account(db, org, name="Shared Account"):
    row = Account(company_id=org.company.id, name=name)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_only_manager_md_and_admin_change_or_delete_accounts(api, db, org):
    account = _account(db, org)
    for who in ("owner", "purchase"):
        assert api.call(who, "PATCH", f"/api/accounts/{account.id}", json={"name": "Renamed"}).status_code == 403
        assert api.call(who, "POST", f"/api/accounts/{account.id}/enrich").status_code == 403
        assert api.call(who, "DELETE", f"/api/accounts/{account.id}").status_code == 403
        assert api.call(who, "GET", f"/api/accounts/{account.id}").status_code == 200
    db.refresh(account)
    assert account.name == "Shared Account"

    assert api.call("mgr_same_team", "PATCH", f"/api/accounts/{account.id}", json={"name": "By Manager"}).status_code == 200
    assert api.call("md", "PATCH", f"/api/accounts/{account.id}", json={"name": "By MD"}).status_code == 200
    assert api.call("md", "DELETE", f"/api/accounts/{account.id}").status_code == 204


def test_reps_can_still_create_accounts(api, org):
    assert api.call("owner", "POST", "/api/accounts", json={"name": "Rep Made"}).status_code == 201


def _deal(db, org, owner, team):
    pipeline = ensure_default_pipeline(db, org.company.id)
    from app.models.sales.pipeline import PipelineStage
    stage = db.query(PipelineStage.id).filter(PipelineStage.pipeline_id == pipeline.id).order_by(PipelineStage.position).first()[0]
    row = Deal(company_id=org.company.id, title="Mine", pipeline_id=pipeline.id, stage_id=stage,
               assigned_to_id=owner.id if owner else None, team_id=team.id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_a_rep_cannot_hand_off_or_move_their_deal(api, db, org):
    deal = _deal(db, org, org.owner, org.alpha)
    assert api.call("owner", "PATCH", f"/api/deals/{deal.id}", json={"assigned_to_id": org.rep_same_team.id}).status_code == 403
    assert api.call("owner", "PATCH", f"/api/deals/{deal.id}", json={"team_id": org.beta.id}).status_code == 403
    assert api.call("owner", "PATCH", f"/api/deals/{deal.id}", json={"assigned_to_id": None}).status_code == 403
    db.refresh(deal)
    assert (deal.assigned_to_id, deal.team_id) == (org.owner.id, org.alpha.id)
    assert api.call("owner", "PATCH", f"/api/deals/{deal.id}", json={"amount": "5000"}).status_code == 200


def test_a_manager_keeps_deals_on_their_team(api, db, org):
    deal = _deal(db, org, org.owner, org.alpha)
    assert api.call("mgr_same_team", "PATCH", f"/api/deals/{deal.id}", json={"assigned_to_id": org.rep_other_team.id}).status_code == 403
    assert api.call("mgr_same_team", "PATCH", f"/api/deals/{deal.id}", json={"team_id": org.beta.id}).status_code == 403
    assert api.call("mgr_same_team", "PATCH", f"/api/deals/{deal.id}", json={"assigned_to_id": org.rep_same_team.id}).status_code == 200
    db.refresh(deal)
    assert deal.assigned_to_id == org.rep_same_team.id


def test_md_can_move_a_deal_anywhere(api, db, org):
    deal = _deal(db, org, org.owner, org.alpha)
    moved = api.call("md", "PATCH", f"/api/deals/{deal.id}", json={"team_id": org.beta.id, "assigned_to_id": org.rep_other_team.id})
    assert moved.status_code == 200, moved.text


def test_task_and_user_history_is_for_md_and_admin_only(api, db, org):
    task = Task(company_id=org.company.id, title="Call back", assigned_to_id=org.owner.id, assigned_by_id=org.owner.id)
    db.add(task)
    db.commit()
    for who in ("owner", "mgr_same_team", "purchase"):
        assert api.call(who, "GET", f"/api/timeline/task/{task.id}").status_code == 403, who
        assert api.call(who, "GET", f"/api/timeline/user/{org.md.id}").status_code == 403, who
    assert api.call("md", "GET", f"/api/timeline/task/{task.id}").status_code == 200
    assert api.call("md", "GET", f"/api/timeline/user/{org.owner.id}").status_code == 200


def test_lead_client_and_deal_history_still_open_for_the_owner(api, db, org):
    deal = _deal(db, org, org.owner, org.alpha)
    assert api.call("owner", "GET", f"/api/timeline/deal/{deal.id}").status_code == 200
