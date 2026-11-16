"""A deal created from the board (title, amount and pipeline only) must stay visible to the person who made it."""


def _board_titles(api, who):
    response = api.call(who, "GET", "/api/deals/board")
    assert response.status_code == 200, response.text
    return {deal["title"] for stage in response.json()["stages"] for deal in stage["deals"]}


def _from_the_board(api, who, title):
    pipeline_id = api.call(who, "GET", "/api/deals/board").json()["pipeline_id"]
    response = api.call(who, "POST", "/api/deals", json={"title": title, "amount": 37500, "pipeline_id": pipeline_id})
    assert response.status_code == 201, response.text
    return response.json()


def test_rep_deal_is_theirs_and_on_their_team(api, db, org):
    from app.models.sales.deal import Deal

    deal = _from_the_board(api, "owner", "Joshi Mart switches")
    row = db.get(Deal, deal["id"])
    assert (row.assigned_to_id, row.team_id) == (org.owner.id, org.alpha.id)

    assert "Joshi Mart switches" in _board_titles(api, "owner")
    assert "Joshi Mart switches" in _board_titles(api, "mgr_same_team")
    assert "Joshi Mart switches" in _board_titles(api, "md")
    assert api.call("owner", "GET", f"/api/deals/{deal['id']}").status_code == 200
    assert {d["title"] for d in api.call("owner", "GET", "/api/deals").json()["items"]} == {"Joshi Mart switches"}


def test_rep_deal_stays_hidden_from_others(api, org):
    _from_the_board(api, "owner", "Private deal")
    assert "Private deal" not in _board_titles(api, "rep_same_team")
    assert "Private deal" not in _board_titles(api, "rep_other_team")
    assert "Private deal" not in _board_titles(api, "mgr_other_team")


def test_rep_cannot_give_a_deal_to_someone_else_or_another_team(api, org):
    assert api.call("owner", "POST", "/api/deals", json={"title": "x", "assigned_to_id": org.rep_same_team.id}).status_code == 403
    assert api.call("owner", "POST", "/api/deals", json={"title": "x", "team_id": org.beta.id}).status_code == 403
    assert api.call("owner", "POST", "/api/deals", json={"title": "mine", "assigned_to_id": org.owner.id}).status_code == 201


def test_manager_deal_goes_to_their_team(api, db, org):
    from app.models.sales.deal import Deal

    open_deal = _from_the_board(api, "mgr_same_team", "Team open deal")
    row = db.get(Deal, open_deal["id"])
    assert (row.assigned_to_id, row.team_id) == (None, org.alpha.id)
    assert "Team open deal" in _board_titles(api, "mgr_same_team")
    assert "Team open deal" in _board_titles(api, "owner")
    assert "Team open deal" not in _board_titles(api, "rep_other_team")

    given = api.call("mgr_same_team", "POST", "/api/deals", json={"title": "For a rep", "assigned_to_id": org.owner.id})
    assert given.status_code == 201, given.text
    assert "For a rep" in _board_titles(api, "owner")
    outside = api.call("mgr_same_team", "POST", "/api/deals", json={"title": "x", "assigned_to_id": org.rep_other_team.id})
    assert outside.status_code == 403
    assert api.call("mgr_same_team", "POST", "/api/deals", json={"title": "x", "team_id": org.beta.id}).status_code == 403


def test_md_deal_is_unchanged(api, db, org):
    from app.models.sales.deal import Deal

    deal = api.call("md", "POST", "/api/deals", json={"title": "Company deal"})
    assert deal.status_code == 201, deal.text
    row = db.get(Deal, deal.json()["id"])
    assert (row.assigned_to_id, row.team_id) == (None, None)
    picked = api.call("md", "POST", "/api/deals", json={"title": "Beta deal", "team_id": org.beta.id, "assigned_to_id": org.rep_other_team.id})
    assert picked.status_code == 201, picked.text
    assert "Beta deal" in _board_titles(api, "rep_other_team")
