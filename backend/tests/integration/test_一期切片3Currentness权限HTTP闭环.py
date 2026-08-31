import secrets

import pytest

from tests.integration.test_一期切片3会员CurrentnessAuthority真实HTTP合同 import (
    _activate_org_admin_for_test,
    _login,
)


pytestmark = pytest.mark.integration


def test_机构登录与邀请列表使用分离CurrentnessAuthority(
    pg_database,
    application_database,
    real_db_client,
) -> None:
    from app.core.uuid_generator import Uuid7Generator

    tenant_id = 97101
    org_id = 97103
    phone = "13" + "8" + ("0" * 8)
    password = secrets.token_urlsafe(24)
    tenant_public_id = Uuid7Generator().generate()
    pg_database.execute(
        "INSERT INTO public.platform_org(id,parent_id,org_name,org_code,org_type,status,version) "
        f"VALUES ({org_id},NULL,'Hotfix county','HOTFIX-COUNTY','county','active',1);"
        "INSERT INTO public.tenant(id,org_id,tenant_code,name,type,province,city,status,created_at,updated_at) "
        f"VALUES ({tenant_id},{org_id},'HOTFIX-TENANT','Hotfix institution','store','test','test','active',now(),now())"
    )
    activated = _activate_org_admin_for_test(
        pg_database,
        real_db_client,
        phone=phone,
        password=password,
        org_id=org_id,
        tenant_id=tenant_id,
        tenant_public_id=tenant_public_id,
        institution_name="Hotfix institution",
        pilot_batch_code="HOTFIX",
        service_tags=(),
    )

    assert application_database.fetch_value(
        "SELECT has_table_privilege(current_user,'public.institution_application','SELECT')"
    ) is False

    authorization = _login(
        real_db_client,
        phone,
        password,
        totp_secret=str(activated["totp_secret"]),
    )

    response = real_db_client.get(
        "/api/v1/institution/member-invitations",
        headers=authorization,
    )

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}
