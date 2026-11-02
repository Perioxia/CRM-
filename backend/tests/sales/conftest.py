"""People and helpers for the lead access and ownership tests."""
from types import SimpleNamespace

import pytest

from app.models.core.company_settings import CompanySettings
from app.models.core.team import Team
from app.models.core.team_membership import TeamMembership
from app.models.core.user import User
from app.utils.security import crm_access_token
from tests.helpers.factories import create_company


def _user(db, name, role, company, team=None):
    user = User(
        email=f"{name}@{company.company_code.lower()}.test",
        full_name=name,
        hashed_password="unused",
        role=role,
        company_id=company.id,
        team_id=team.id if team else None,
        status="active",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    if team:
        db.add(TeamMembership(company_id=company.id, team_id=team.id, user_id=user.id))
        db.commit()
    return user


@pytest.fixture
def org(client, db):
    company = create_company(db, name="Access Co", company_code="ACC")
    other = create_company(db, name="Other Co", company_code="OTH")
    alpha = Team(company_id=company.id, name="Alpha")
    beta = Team(company_id=company.id, name="Beta")
    foreign = Team(company_id=other.id, name="Foreign")
    db.add_all([alpha, beta, foreign])
    db.commit()
    db.add(CompanySettings(
        company_id=company.id,
        company_name="Access Co",
        whatsapp_api_key="k",
        whatsapp_source="918888800000",
        exotel_sid="sid",
        exotel_api_token_encrypted="token",
        exotel_caller_id="1",
    ))
    db.commit()
    people = SimpleNamespace(
        company=company,
        alpha=alpha,
        beta=beta,
        owner=_user(db, "owner", "sales", company, alpha),
        rep_same_team=_user(db, "repsame", "sales", company, alpha),
        rep_other_team=_user(db, "repother", "sales", company, beta),
        mgr_same_team=_user(db, "mgrsame", "manager", company, alpha),
        mgr_other_team=_user(db, "mgrother", "manager", company, beta),
        purchase=_user(db, "buyer", "purchase", company),
        md=_user(db, "md", "md", company),
        other_company=_user(db, "outsider", "sales", other, foreign),
    )
    return people


class Api:
    def __init__(self, client, org):
        self.client = client
        self.org = org
        self.count = 0

    def _headers(self, user):
        headers = {"Authorization": f"Bearer {crm_access_token(user)}"}
        if user.team_id:
            headers["X-Team-Id"] = str(user.team_id)
        return headers

    def call(self, who, method, path, **kwargs):
        user = getattr(self.org, who) if isinstance(who, str) else who
        self.client.cookies.clear()
        return self.client.request(method, path, headers=self._headers(user), **kwargs)

    def new_lead(self, **extra):
        self.count += 1
        body = {
            "name": f"SECRETLEAD{self.count}",
            "team_id": self.org.alpha.id,
            "phone": f"90000{self.count:05d}",
            "email": f"lead{self.count}@acme.co",
        }
        response = self.call("owner", "POST", "/api/leads", json=body | extra)
        assert response.status_code == 201, response.text
        return response.json()["id"]


@pytest.fixture
def api(client, org):
    return Api(client, org)
