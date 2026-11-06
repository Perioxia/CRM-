from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def _scripts() -> ScriptDirectory:
    backend = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    return ScriptDirectory.from_config(cfg)


def test_alembic_has_exactly_one_head():
    heads = _scripts().get_heads()
    assert len(heads) == 1, heads
    assert heads == ["044_invite_hashed_password"]


def test_catchup_revision_follows_015():
    rev = _scripts().get_revision("016_schema_catchup")
    assert rev is not None
    assert rev.down_revision == "015_ai_reasoning"


def test_enrichment_revision_follows_016():
    rev = _scripts().get_revision("017_enrichment")
    assert rev is not None
    assert rev.down_revision == "016_schema_catchup"


def test_token_version_revision_follows_017():
    rev = _scripts().get_revision("018_token_version")
    assert rev is not None
    assert rev.down_revision == "017_enrichment"


def test_scoring_revision_follows_018():
    rev = _scripts().get_revision("019_scoring")
    assert rev is not None
    assert rev.down_revision == "018_token_version"


def test_predictions_revision_follows_019():
    rev = _scripts().get_revision("020_predictions")
    assert rev is not None
    assert rev.down_revision == "019_scoring"


def test_accounting_revision_follows_020():
    rev = _scripts().get_revision("021_accounting")
    assert rev is not None
    assert rev.down_revision == "020_predictions"


def test_custom_modules_revision_follows_021():
    rev = _scripts().get_revision("022_custom_modules")
    assert rev is not None
    assert rev.down_revision == "021_accounting"


def test_marketplace_revision_follows_022():
    rev = _scripts().get_revision("023_marketplace")
    assert rev is not None
    assert rev.down_revision == "022_custom_modules"


def test_campaigns_revision_follows_023():
    rev = _scripts().get_revision("024_campaigns")
    assert rev is not None
    assert rev.down_revision == "023_marketplace"


def test_cases_revision_follows_024():
    rev = _scripts().get_revision("025_cases")
    assert rev is not None
    assert rev.down_revision == "024_campaigns"


def test_mass_email_revision_follows_025():
    rev = _scripts().get_revision("026_mass_email")
    assert rev is not None
    assert rev.down_revision == "025_cases"


def test_email_tracking_revision_follows_026():
    rev = _scripts().get_revision("027_email_tracking")
    assert rev is not None
    assert rev.down_revision == "026_mass_email"


def test_booking_calendar_revision_follows_027():
    rev = _scripts().get_revision("028_booking_calendar")
    assert rev is not None
    assert rev.down_revision == "027_email_tracking"


def test_tally_live_revision_follows_028():
    rev = _scripts().get_revision("029_tally_live")
    assert rev is not None
    assert rev.down_revision == "028_booking_calendar"


def test_einvoice_live_revision_follows_029():
    rev = _scripts().get_revision("030_einvoice_live")
    assert rev is not None
    assert rev.down_revision == "029_tally_live"


def test_price_books_revision_follows_030():
    rev = _scripts().get_revision("031_price_books")
    assert rev is not None
    assert rev.down_revision == "030_einvoice_live"


def test_next_activity_revision_follows_031():
    rev = _scripts().get_revision("032_next_activity_nag")
    assert rev is not None
    assert rev.down_revision == "031_price_books"


def test_sales_orders_revision_follows_032():
    rev = _scripts().get_revision("033_sales_orders")
    assert rev is not None
    assert rev.down_revision == "032_next_activity_nag"


def test_deal_discount_approvals_revision_follows_033():
    rev = _scripts().get_revision("034_deal_discount_approvals")
    assert rev is not None
    assert rev.down_revision == "033_sales_orders"


def test_import_batches_revision_follows_034():
    rev = _scripts().get_revision("035_import_batches")
    assert rev is not None
    assert rev.down_revision == "034_deal_discount_approvals"


def test_report_schedule_revision_follows_035():
    rev = _scripts().get_revision("036_report_schedule")
    assert rev is not None
    assert rev.down_revision == "035_import_batches"


def test_email_templates_revision_follows_036():
    rev = _scripts().get_revision("037_email_templates")
    assert rev is not None
    assert rev.down_revision == "036_report_schedule"


def test_quote_plans_revision_follows_037():
    rev = _scripts().get_revision("038_quote_plans")
    assert rev is not None
    assert rev.down_revision == "037_email_templates"


def test_lead_sheet_revision_follows_038():
    rev = _scripts().get_revision("039_lead_sheet")
    assert rev is not None
    assert rev.down_revision == "038_quote_plans"


def test_team_chat_revision_follows_039():
    rev = _scripts().get_revision("040_team_chat")
    assert rev is not None
    assert rev.down_revision == "039_lead_sheet"


def test_company_id_sequence_revision_follows_042():
    rev = _scripts().get_revision("043_company_id_sequence")
    assert rev is not None
    assert rev.down_revision == "042_chat_calls"
