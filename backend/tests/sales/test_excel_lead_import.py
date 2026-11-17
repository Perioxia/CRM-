"""Leads can be imported from an Excel (.xlsx) file the same way as from a CSV."""
import io

from openpyxl import Workbook

from app.models import Lead

HEADERS = ["Full Name", "Email Address", "Mobile", "Company Name", "Source", "Comments"]
ROWS = [
    ["Gaurav Bhatia", "gaurav@bhatia.example", 9000060001, "Bhatia Power", "Trade Fair", "Wants 50 MCBs"],
    ["Meenakshi Sundaram", "meenakshi@sundaram.example", 9000060002, "Sundaram Homes", "Website", None],
    ["Already Here", "taken@acme.co", 9000060003, "Dup Co", "Website", "Same email as an existing lead"],
    [None, "noname@example.com", 9000060004, "No Name", "Website", "No name"],
]


def _xlsx(rows=ROWS, headers=HEADERS, extra_sheet=False) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    if extra_sheet:
        book.create_sheet("Ignored").append(["Name"])
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


def _upload(api, who, path, data, name="leads.xlsx"):
    return api.call(who, "POST", path, files={"file": (name, data, "application/octet-stream")})


def _existing_lead(db, org):
    db.add(Lead(company_id=org.company.id, name="Existing", email="taken@acme.co", status="Active", team_id=org.alpha.id))
    db.commit()


def test_excel_preview_matches_columns_and_finds_new_duplicate_and_invalid_rows(api, db, org):
    _existing_lead(db, org)
    preview = _upload(api, "mgr_same_team", "/api/import/leads/preview", _xlsx())
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["counts"] == {"new": 2, "duplicate": 1, "invalid": 1, "total": 4}
    assert body["mapping"]["name"] == "Full Name" and body["mapping"]["phone"] == "Mobile"
    phones = [row["values"]["phone"] for row in body["rows"]]
    assert phones[0] == "9000060001"  # a phone typed as a number is not read as 9000060001.0


def test_excel_import_by_a_manager_lands_in_the_pool(api, db, org):
    done = _upload(api, "mgr_same_team", "/api/import/leads/commit", _xlsx())
    assert done.status_code == 200, done.text
    assert done.json()["created"] == 3
    lead = db.query(Lead).filter(Lead.name == "Gaurav Bhatia").one()
    assert (lead.phone, lead.team_id, lead.assigned_to_id) == ("9000060001", org.alpha.id, None)
    assert lead.notes == "Wants 50 MCBs"


def test_excel_import_by_a_rep_is_theirs_and_can_be_undone(api, db, org):
    done = _upload(api, "owner", "/api/import/leads/commit", _xlsx())
    assert done.json()["created"] == 3
    assert {lead.assigned_to_id for lead in db.query(Lead).filter(Lead.name.in_(["Gaurav Bhatia", "Already Here"]))} == {org.owner.id}
    undone = api.call("owner", "POST", "/api/import/undo")
    assert undone.status_code == 200, undone.text
    assert undone.json()["removed"] == 3


def test_only_the_first_sheet_and_at_most_500_rows_are_read(api, db, org):
    many = [[f"Person {i}", None, None, None, None, None] for i in range(600)]
    body = _upload(api, "mgr_same_team", "/api/import/leads/preview", _xlsx(rows=many, extra_sheet=True)).json()
    assert body["counts"]["total"] == 500


def test_a_broken_or_wrong_file_gets_a_clear_message(api, org):
    broken = _upload(api, "mgr_same_team", "/api/import/leads/preview", b"not really excel")
    assert broken.status_code == 400
    assert "Could not read that Excel file" in broken.json()["detail"]
    old_excel = _upload(api, "mgr_same_team", "/api/import/leads/preview", b"\xd0\xcf\x11\xe0", name="leads.xls")
    assert old_excel.status_code == 400
    assert "CSV or Excel (.xlsx)" in old_excel.json()["detail"]


def test_purchase_still_cannot_import_and_clients_stay_csv_only(api, org):
    assert _upload(api, "purchase", "/api/import/leads/preview", _xlsx()).status_code == 403
    clients = _upload(api, "md", "/api/import/clients/preview", _xlsx())
    assert clients.status_code == 400
