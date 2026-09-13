from __future__ import annotations

import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

from app.modules.auth.service import hash_password

pytestmark = pytest.mark.integration

_REVIEWER_ID = 9966101
_HEADQUARTER_ID = 9966102
_PROVINCE_ID = 9966103
_CITY_ID = 9966104
_COUNTY_ID = 9966105
_REVIEWER_PHONE = "136" + ("6" * 8)


def _subprocess_environment(password: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment["KG_ORM_FK_SYNTHETIC_PASSWORD"] = password
    environment["KG_ORM_FK_SYNTHETIC_PHONE"] = _REVIEWER_PHONE
    return environment


@pytest.fixture
def invitation_http_facts(pg_database):
    password = secrets.token_urlsafe(24)
    password_hash = hash_password(password).replace("'", "''")
    pg_database.execute(
        'INSERT INTO public."user"(id,phone,password_hash,role,status,tenant_id) VALUES '
        f"({_REVIEWER_ID},'{_REVIEWER_PHONE}','{password_hash}','super_admin','active',NULL);"
        "INSERT INTO public.platform_org(id,parent_id,org_name,org_code,org_type,status,version) VALUES "
        f"({_HEADQUARTER_ID},NULL,'Synthetic headquarter','ORM-FK-HQ','headquarter','active',1),"
        f"({_PROVINCE_ID},{_HEADQUARTER_ID},'Synthetic province','ORM-FK-PROVINCE','province','active',1),"
        f"({_CITY_ID},{_PROVINCE_ID},'Synthetic city','ORM-FK-CITY','city','active',1),"
        f"({_COUNTY_ID},{_CITY_ID},'Synthetic county','ORM-FK-COUNTY','county','active',1)"
    )
    try:
        yield password
    finally:
        pg_database.execute(
            "DELETE FROM public.institution_onboarding_audit "
            f"WHERE actor_user_id={_REVIEWER_ID};"
            "DELETE FROM public.institution_invitation "
            f"WHERE issued_by={_REVIEWER_ID};"
            "DELETE FROM public.platform_org "
            f"WHERE id IN ({_COUNTY_ID},{_CITY_ID},{_PROVINCE_ID},{_HEADQUARTER_ID});"
            f'DELETE FROM public."user" WHERE id={_REVIEWER_ID}'
        )


def test_独立正式App真实HTTP邀请成功且幂等回放(pg_database, invitation_http_facts) -> None:
    password = invitation_http_facts
    script = f"""
import os
from fastapi.testclient import TestClient
from app.main import create_app

with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
    login = client.post("/api/v1/auth/login", json={{"phone": os.environ["KG_ORM_FK_SYNTHETIC_PHONE"], "password": os.environ["KG_ORM_FK_SYNTHETIC_PASSWORD"]}})
    if login.status_code != 200:
        raise SystemExit(21)
    headers = {{"Authorization": "Bearer " + login.json()["data"]["access_token"], "Idempotency-Key": "orm-fk-http-success"}}
    payload = {{"institution_name": "Synthetic ORM FK institution", "institution_type": "HEALTH_STORE", "applicant_phone": "138" + ("6" * 8), "pilot_batch_code": "ORM-FK", "administrative_region_id": {_COUNTY_ID}, "expires_in_minutes": 60}}
    first = client.post("/api/v1/platform/institution-invitations", headers=headers, json=payload)
    replay = client.post("/api/v1/platform/institution-invitations", headers=headers, json=payload)
    if first.status_code != 200 or replay.status_code != 200:
        raise SystemExit(22)
    if first.json() != replay.json():
        raise SystemExit(23)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        env=_subprocess_environment(password),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, "INSTITUTION_INVITATION_REAL_HTTP_FAILED"
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.institution_invitation "
        f"WHERE issued_by={_REVIEWER_ID}"
    ) == 1
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.institution_onboarding_audit "
        f"WHERE actor_user_id={_REVIEWER_ID} AND action='INVITATION_CREATE'"
    ) == 1


def test_独立正式App非法行政区稳定拒绝且零副作用(pg_database, invitation_http_facts) -> None:
    password = invitation_http_facts
    before = pg_database.fetch_value("SELECT count(*) FROM public.institution_invitation")
    script = f"""
import os
from fastapi.testclient import TestClient
from app.main import create_app

with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
    login = client.post("/api/v1/auth/login", json={{"phone": os.environ["KG_ORM_FK_SYNTHETIC_PHONE"], "password": os.environ["KG_ORM_FK_SYNTHETIC_PASSWORD"]}})
    if login.status_code != 200:
        raise SystemExit(31)
    response = client.post(
        "/api/v1/platform/institution-invitations",
        headers={{"Authorization": "Bearer " + login.json()["data"]["access_token"], "Idempotency-Key": "orm-fk-http-invalid"}},
        json={{"institution_name": "Synthetic invalid region institution", "institution_type": "HEALTH_STORE", "applicant_phone": "138" + ("6" * 7) + "7", "pilot_batch_code": "ORM-FK", "administrative_region_id": {_CITY_ID}, "expires_in_minutes": 60}},
    )
    if response.status_code != 409 or response.json().get("code") != "ONBOARDING_ADMINISTRATIVE_REGION_INVALID":
        raise SystemExit(32)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        env=_subprocess_environment(password),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, "INSTITUTION_INVITATION_INVALID_REGION_CONTRACT_FAILED"
    assert pg_database.fetch_value("SELECT count(*) FROM public.institution_invitation") == before
