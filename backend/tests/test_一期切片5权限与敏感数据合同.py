from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest

from app.modules.health_assessment import service


def _key(value: int) -> str:
    return base64.b64encode(bytes([value]) * 32).decode("ascii")


def test_slice5两个密钥域独立且错误配置fail_closed(monkeypatch) -> None:
    settings = SimpleNamespace(
        slice5_digest_current_key_id="digest-v1",
        slice5_digest_keyring_json=json.dumps({"digest-v1": _key(11)}),
        slice5_phi_current_key_id="phi-v1",
        slice5_phi_keyring_json=json.dumps({"phi-v1": _key(12)}),
    )
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    box = service.Slice5Secrets()
    assert box.digest("REQUEST", {"value": "synthetic"})[0] != box.digest("AUDIT", {"value": "synthetic"})[0]

    settings.slice5_phi_keyring_json = settings.slice5_digest_keyring_json
    settings.slice5_phi_current_key_id = settings.slice5_digest_current_key_id
    with pytest.raises(RuntimeError, match="DEPENDENCY_UNAVAILABLE"):
        service.Slice5Secrets()


def test_slice5公开Schema和事件目录不含敏感材料() -> None:
    from app.modules.health_assessment import api, schemas

    forbidden = {"ciphertext", "key_id", "digest", "password", "phone", "id_card", "database_url"}
    for model in (
        schemas.AssessmentSummaryDTO,
        schemas.AssessmentDetailDTO,
        schemas.HighRiskTaskDTO,
        schemas.DisputeDTO,
        schemas.RuleSetVersionDTO,
    ):
        assert not forbidden.intersection(model.model_fields)
    source = api.__loader__.get_source(api.__name__).lower()
    assert "print(" not in source
    assert "logger." not in source
