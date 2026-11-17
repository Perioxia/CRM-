from app.models.sales.custom_module import CustomModule, CustomModuleField, CustomModuleRecord
from tests.helpers.factories import create_company


def test_module_columns():
    cols = {c.name for c in CustomModule.__table__.columns}
    assert cols == {"id", "company_id", "name", "slug", "is_active"}


def test_field_columns():
    cols = {c.name for c in CustomModuleField.__table__.columns}
    assert cols == {
        "id", "company_id", "module_id", "name", "field_key",
        "field_type", "options_json", "is_active",
    }


def test_record_columns():
    cols = {c.name for c in CustomModuleRecord.__table__.columns}
    assert cols == {
        "id", "company_id", "module_id", "title", "values_json",
        "created_by_id", "created_at", "updated_at",
    }


def test_module_and_record_persist(db):
    company = create_company(db, name="Schema Co 1", company_code="S01")
    mod = CustomModule(company_id=company.id, name="Sites", slug="sites")
    db.add(mod)
    db.commit()
    db.refresh(mod)
    rec = CustomModuleRecord(
        company_id=company.id, module_id=mod.id, title="Roof A", values_json="{}",
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    assert rec.id is not None
    assert rec.module_id == mod.id
