from app.models.sales.prediction import PredictionModel
from tests.helpers.factories import create_company


def test_prediction_model_columns():
    cols = {c.name for c in PredictionModel.__table__.columns}
    assert cols == {
        "id", "company_id", "kind", "trained_at",
        "sample_count", "base_rate", "params", "version",
    }


def test_prediction_model_persists(db):
    company = create_company(db, name="Schema Co 1", company_code="S01")
    m = PredictionModel(company_id=company.id, kind="deal_convert", sample_count=12,
                        base_rate=0.5, params="{}", version=1)
    db.add(m)
    db.commit()
    db.refresh(m)
    assert m.id is not None
