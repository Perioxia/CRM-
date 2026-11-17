from app.models.sales.scoring import ScoringRule
from app.models.sales.lead import Lead
from app.models.sales.deal import Deal
from tests.helpers.factories import create_company


def test_scoring_rule_table_columns():
    cols = {c.name for c in ScoringRule.__table__.columns}
    assert cols == {
        "id", "company_id", "entity_type", "field",
        "operator", "value", "points", "is_active", "created_at",
    }


def test_score_columns_on_lead_and_deal():
    assert "score" in Lead.__table__.columns
    assert "score_updated_at" in Lead.__table__.columns
    assert "score" in Deal.__table__.columns
    assert "score_updated_at" in Deal.__table__.columns


def test_scoring_rule_persists(db):
    company = create_company(db, name="Schema Co 1", company_code="S01")
    rule = ScoringRule(
        company_id=company.id, entity_type="lead", field="source",
        operator="eq", value="Referral", points=20, is_active=True,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    assert rule.id is not None
