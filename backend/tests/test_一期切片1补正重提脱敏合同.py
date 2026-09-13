from __future__ import annotations

from datetime import date
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.modules.institution_onboarding.schemas import (
    ApplicationDraftRequest,
    ApplicationResubmitRequest,
    LicenseBinding,
)


def _license() -> LicenseBinding:
    return LicenseBinding(
        license_type="BUSINESS_LICENSE",
        private_file_id=UUID("01990000-0000-7000-8000-000000000111"),
        valid_from=date(2026, 1, 1),
        valid_until=date(2027, 1, 1),
    )


def test_补正重提请求不要求回传未点名的脱敏字段():
    payload = ApplicationResubmitRequest(
        service_address="合成已补正服务地址",
        expected_version=5,
        licenses=(_license(),),
    )

    assert payload.model_fields_set == {
        "service_address",
        "expected_version",
        "licenses",
    }


def test_初次草稿仍要求完整字段且重提字段保留原合法性限制():
    with pytest.raises(ValidationError):
        ApplicationDraftRequest(
            service_address="合成服务地址",
            expected_version=1,
        )

    with pytest.raises(ValidationError):
        ApplicationResubmitRequest(
            contact_phone="invalid",
            expected_version=5,
            licenses=(_license(),),
        )


def test_补正重提请求继续拒绝未知字段():
    with pytest.raises(ValidationError):
        ApplicationResubmitRequest(
            service_address="合成已补正服务地址",
            expected_version=5,
            licenses=(_license(),),
            unreviewed_field="forbidden",
        )
