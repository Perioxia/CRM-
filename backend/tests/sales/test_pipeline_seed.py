from app.models.core.enums import DealStageType
from app.models.sales.pipeline import Pipeline, PipelineStage
from app.services.sales.pipeline_seed import ensure_default_pipeline
from tests.helpers.factories import create_company


def test_seed_creates_one_pipeline_and_five_stages(db):
    company = create_company(db, name="Schema Co 1", company_code="S01")
    pipe = ensure_default_pipeline(db, company_id=company.id)
    assert pipe.is_default is True
    stages = db.query(PipelineStage).filter(PipelineStage.pipeline_id == pipe.id).order_by(PipelineStage.position).all()
    assert [s.name for s in stages] == ["Qualification", "Proposal", "Negotiation", "Won", "Lost"]
    assert [s.default_probability for s in stages] == [10, 40, 70, 100, 0]
    assert stages[3].stage_type == DealStageType.WON
    assert stages[4].stage_type == DealStageType.LOST
    assert all(s.company_id == company.id for s in stages)


def test_seed_is_idempotent(db):
    company = create_company(db, name="Schema Co 1", company_code="S01")
    ensure_default_pipeline(db, company_id=company.id)
    ensure_default_pipeline(db, company_id=company.id)
    assert db.query(Pipeline).filter(Pipeline.company_id == company.id).count() == 1
    assert db.query(PipelineStage).filter(PipelineStage.company_id == company.id).count() == 5


def test_seed_is_per_company(db):
    company = create_company(db, name="Schema Co 1", company_code="S01")
    company_2 = create_company(db, name="Schema Co 2", company_code="S02")
    a = ensure_default_pipeline(db, company_id=company.id)
    b = ensure_default_pipeline(db, company_id=company_2.id)
    assert a.id != b.id
    assert db.query(Pipeline).count() == 2


def test_signup_seeds_default_pipeline(client, db):
    from app.models.sales.pipeline import Pipeline
    resp = client.post("/api/auth/signup", json={
        "email": "founder@new.com", "password": "pw123456",
        "full_name": "Founder", "company_name": "NewCo",
    })
    assert resp.status_code in (200, 201), resp.text
    # the new company should have its default pipeline
    assert db.query(Pipeline).filter(Pipeline.is_default == True).count() >= 1  # noqa: E712
