from __future__ import annotations

import hashlib
import secrets

import pytest

from app.core.security import create_access_token

pytestmark = pytest.mark.integration


def _headers(*, user_id: int, role: str, tenant_id: int | None = None) -> dict[str, str]:
    claims: dict[str, object] = {"sub": str(user_id), "role": role}
    if tenant_id is not None:
        claims["tenant_id"] = tenant_id
    return {"Authorization": f"Bearer {create_access_token(claims)}"}


def _request(*, purpose: str) -> dict[str, object]:
    content = f"synthetic-{purpose}".encode()
    return {
        "purpose": purpose,
        "size": len(content),
        "mime_type": "application/pdf",
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def test_家庭与健管师使用正式Runtime发起私密文件上传并真实持久化(
    real_db_client,
    pg_database,
) -> None:
    suffix = secrets.randbelow(10_000_000)
    org_id = 9_610_000_000 + suffix
    tenant_id = 9_620_000_000 + suffix
    member_user_id = 9_630_000_000 + suffix
    therapist_user_id = 9_640_000_000 + suffix
    disabled_user_id = 9_650_000_000 + suffix
    member_phone = f"13{suffix:09d}"[-11:]
    therapist_phone = f"15{suffix:09d}"[-11:]
    disabled_phone = f"17{suffix:09d}"[-11:]

    pg_database.execute(
        "INSERT INTO public.platform_org("
        "id,parent_id,org_name,org_code,org_type,status,version) VALUES ("
        f"{org_id},NULL,'Synthetic upload org','PF-{suffix}','county','active',1);"
        "INSERT INTO public.tenant("
        "id,org_id,tenant_code,name,type,province,city,status,created_at,updated_at) VALUES ("
        f"{tenant_id},{org_id},'PF-{suffix}','Synthetic upload tenant','health_store',"
        "'Synthetic Province','Synthetic City','active',now(),now());"
        'INSERT INTO public."user"(id,phone,password_hash,role,status,tenant_id) VALUES '
        f"({member_user_id},'{member_phone}','synthetic-hash','member','active',NULL),"
        f"({therapist_user_id},'{therapist_phone}','synthetic-hash','therapist','active',{tenant_id}),"
        f"({disabled_user_id},'{disabled_phone}','synthetic-hash','member','disabled',NULL);"
    )

    before = pg_database.fetch_value(
        "SELECT count(*) FROM public.private_file WHERE owner_user_id IN "
        f"({member_user_id},{therapist_user_id},{disabled_user_id})"
    )

    member_response = real_db_client.post(
        "/api/v1/private-files/uploads",
        headers=_headers(user_id=member_user_id, role="member"),
        json=_request(purpose="DETECTION_REPORT"),
    )
    assert member_response.status_code == 200

    therapist_response = real_db_client.post(
        "/api/v1/private-files/uploads",
        headers=_headers(
            user_id=therapist_user_id,
            role="therapist",
            tenant_id=tenant_id,
        ),
        json=_request(purpose="THERAPIST_QUALIFICATION"),
    )
    assert therapist_response.status_code == 200

    member_file_id = member_response.json()["data"]["file_id"]
    therapist_file_id = therapist_response.json()["data"]["file_id"]
    rows = pg_database.fetch_rows(
        "SELECT file_id,purpose,owner_user_id,status,bound_application_id "
        "FROM public.private_file WHERE file_id=$1 OR file_id=$2 ORDER BY owner_user_id",
        member_file_id,
        therapist_file_id,
    )
    assert len(rows) == 2
    assert {
        (row["purpose"], row["owner_user_id"], row["status"], row["bound_application_id"])
        for row in rows
    } == {
        ("DETECTION_REPORT", member_user_id, "UPLOAD_INITIATED", None),
        ("THERAPIST_QUALIFICATION", therapist_user_id, "UPLOAD_INITIATED", None),
    }

    denied = real_db_client.post(
        "/api/v1/private-files/uploads",
        json=_request(purpose="DETECTION_REPORT"),
    )
    assert denied.status_code == 401

    disabled = real_db_client.post(
        "/api/v1/private-files/uploads",
        headers=_headers(user_id=disabled_user_id, role="member"),
        json=_request(purpose="DETECTION_REPORT"),
    )
    assert disabled.status_code == 401

    invalid_purpose = real_db_client.post(
        "/api/v1/private-files/uploads",
        headers=_headers(user_id=member_user_id, role="member"),
        json={**_request(purpose="DETECTION_REPORT"), "purpose": "UNAPPROVED"},
    )
    assert invalid_purpose.status_code == 422

    after = pg_database.fetch_value(
        "SELECT count(*) FROM public.private_file WHERE owner_user_id IN "
        f"({member_user_id},{therapist_user_id},{disabled_user_id})"
    )
    assert after - before == 2
