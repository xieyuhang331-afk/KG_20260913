from __future__ import annotations

import base64
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
from itertools import combinations
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.core import 认证配置校验 as validation
from app.modules.health_assessment import service as slice5
from app.modules.private_file import service as private_file
from app.modules.service_fulfillment import service as slice7

PURPOSE_NAMES = (
    "KG_SLICE5_CURSOR_SIGNING_KEY",
    "KG_SLICE7_CURSOR_SIGNING_KEY",
    "KG_SLICE5_PUBLIC_REFERENCE_HMAC_KEY",
    "KG_PRIVATE_FILE_ACCESS_SIGNING_KEY",
)
CURSOR = UUID("0198f1c0-0000-7000-8000-0000000000d1")
CEILING = UUID("0198f1c0-0000-7000-8000-0000000000f1")
SCOPE5 = {"actor_user_id": 71, "role": "super_admin", "kind": "rule-set"}
SCOPE7 = {
    "resource": "EXPORT", "scope_id": None, "actor_user_id": 71,
    "actor_role": "super_admin", "actor_tenant_id": None,
    "filters": {"status": "READY"},
}


def _check(value: bool, code: str) -> None:
    if not value:
        pytest.fail(code, pytrace=False)


@pytest.fixture
def materials(monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("KG_"):
            monkeypatch.delenv(name)
    values = {name: secrets.token_urlsafe(48) for name in (
        *PURPOSE_NAMES, "KG_JWT_SECRET_KEY", "KG_AUTH_RATE_LIMIT_HMAC_KEY",
    )}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    settings = SimpleNamespace(
        environment="test", jwt_algorithm="HS256",
        jwt_access_token_expire_minutes=120,
        jwt_secret_key=values["KG_JWT_SECRET_KEY"],
        auth_rate_limit_hmac_key=values["KG_AUTH_RATE_LIMIT_HMAC_KEY"],
        database_password=secrets.token_urlsafe(48),
        database_driver="postgresql+asyncpg", database_port=5432,
        database_host="127.0.0.1", identity_review_step_up_secret_key=None,
    )
    monkeypatch.setattr(validation, "get_settings", lambda: settings)
    return values, settings


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _signed_like(token: str, key: str, domain: bytes) -> str:
    payload = token.split(".")[0]
    raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    return payload + "." + _b64(hmac.digest(key.encode(), domain + raw, "sha256"))


def test_C1B_R01_Slice5独立材料真实签验且JWT轮换不影响(materials, monkeypatch):
    values, _ = materials
    token = slice5.encode_slice5_cursor(CURSOR, SCOPE5)
    expected = _signed_like(token, values[PURPOSE_NAMES[0]], b"slice5-health-assessment-cursor:v1:\x00")
    _check(hmac.compare_digest(token, expected), "C1B_SLICE5_WRONG_SIGNING_PURPOSE")
    monkeypatch.setenv("KG_JWT_SECRET_KEY", secrets.token_urlsafe(48))
    _check(slice5.decode_slice5_cursor(token, SCOPE5) == CURSOR, "C1B_SLICE5_JWT_COUPLING")


def test_C1B_R02_Slice7独立材料真实签验且JWT轮换不影响(materials, monkeypatch):
    values, _ = materials
    token = slice7.encode_page_cursor(CURSOR, snapshot_ceiling=CEILING, **SCOPE7)
    expected = _signed_like(token, values[PURPOSE_NAMES[1]], b"slice7-service-fulfillment-page-cursor:v1:\x00")
    _check(hmac.compare_digest(token, expected), "C1B_SLICE7_WRONG_SIGNING_PURPOSE")
    monkeypatch.setenv("KG_JWT_SECRET_KEY", secrets.token_urlsafe(48))
    _check(slice7.decode_page_cursor(token, **SCOPE7) == (CURSOR, CEILING), "C1B_SLICE7_JWT_COUPLING")


PAIRS = tuple(combinations(range(6), 2))


@pytest.mark.parametrize("pair", PAIRS, ids=[f"purpose-pair-{n}" for n in range(len(PAIRS))])
def test_C1B_R03用途材料相同必须启动拒绝(materials, monkeypatch, pair):
    values, settings = materials
    names = (*PURPOSE_NAMES, "KG_JWT_SECRET_KEY", "KG_AUTH_RATE_LIMIT_HMAC_KEY")
    first, second = (names[index] for index in pair)
    monkeypatch.setenv(second, values[first])
    if second == "KG_JWT_SECRET_KEY":
        settings.jwt_secret_key = values[first]
    elif second == "KG_AUTH_RATE_LIMIT_HMAC_KEY":
        settings.auth_rate_limit_hmac_key = values[first]
    with pytest.raises(RuntimeError, match="^AUTH_CONFIGURATION_INVALID$"):
        validation.validated_auth_settings()


@pytest.mark.parametrize("purpose", range(4), ids=["slice5", "slice7", "reference", "file"])
@pytest.mark.parametrize("invalid", ["missing", "empty", "short", "placeholder", "whitespace"])
def test_C1B_R04每用途缺失弱值启动稳定拒绝(materials, monkeypatch, purpose, invalid):
    name = PURPOSE_NAMES[purpose]
    bad = {"empty": "", "short": "x", "placeholder": "replace-me-" * 8, "whitespace": " " + secrets.token_urlsafe(48)}
    if invalid == "missing":
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, bad[invalid])
    with pytest.raises(RuntimeError, match="^AUTH_CONFIGURATION_INVALID$"):
        validation.validated_auth_settings()


def test_C1B_R05公开引用独立于JWT与游标(materials, monkeypatch):
    values, _ = materials
    before = slice5.public_user_reference(71)
    expected = "usr_" + _b64(hmac.digest(
        values[PURPOSE_NAMES[2]].encode(), b"slice5-public-user-ref:v1:\x00" + b"71", "sha256",
    ))
    _check(hmac.compare_digest(before, expected), "C1B_REFERENCE_WRONG_PURPOSE")
    for name in ("KG_JWT_SECRET_KEY", PURPOSE_NAMES[0], PURPOSE_NAMES[1]):
        monkeypatch.setenv(name, secrets.token_urlsafe(48))
    _check(hmac.compare_digest(before, slice5.public_user_reference(71)), "C1B_REFERENCE_CURSOR_COUPLING")


@pytest.mark.parametrize("module", ["slice5", "slice7"])
def test_C1B_R06旧JWT签名与跨用途签名稳定拒绝(materials, module):
    values, _ = materials
    if module == "slice5":
        token = slice5.encode_slice5_cursor(CURSOR, SCOPE5)
        own, domain = PURPOSE_NAMES[0], b"slice5-health-assessment-cursor:v1:\x00"
        def decode(value):
            return slice5.decode_slice5_cursor(value, SCOPE5)
        error = slice5.HealthAssessmentError
    else:
        token = slice7.encode_page_cursor(CURSOR, snapshot_ceiling=CEILING, **SCOPE7)
        own, domain = PURPOSE_NAMES[1], b"slice7-service-fulfillment-page-cursor:v1:\x00"
        def decode(value):
            return slice7.decode_page_cursor(value, **SCOPE7)
        error = slice7.ServiceFulfillmentError
    for name, key in values.items():
        if name != own:
            with pytest.raises(error, match="^INVALID_REQUEST$"):
                decode(_signed_like(token, key, domain))
    raw = base64.urlsafe_b64decode(token.split(".")[0] + "==")
    _check(not {"exp", "kid"}.intersection(json.loads(raw)), "C1B_CURSOR_FORMAT_EXPANSION")


@pytest.mark.parametrize("invalid", ["missing", "short", "placeholder"])
def test_C1B_R07文件key弱值在codec边界拒绝(materials, monkeypatch, invalid):
    if invalid == "missing":
        monkeypatch.delenv(PURPOSE_NAMES[3])
    else:
        monkeypatch.setenv(PURPOSE_NAMES[3], "x" if invalid == "short" else "replace-me-" * 8)
    with pytest.raises(RuntimeError, match="^Private file access is unavailable$"):
        private_file._access_secret()


def test_C1B_R07文件既有合规key及HMAC合同保持(materials, monkeypatch):
    before = private_file._access_secret()
    for name in (*PURPOSE_NAMES[:3], "KG_JWT_SECRET_KEY"):
        monkeypatch.setenv(name, secrets.token_urlsafe(48))
    _check(hmac.compare_digest(before, private_file._access_secret()), "C1B_FILE_KEY_CHANGED")


def test_C1B_R08错误不泄漏材料且有效配置不连接外部(materials, monkeypatch, capsys, caplog):
    values, settings = materials
    calls = []
    import sqlalchemy
    import sqlalchemy.ext.asyncio

    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *a, **k: calls.append("db"))
    monkeypatch.setattr(sqlalchemy.ext.asyncio, "create_async_engine", lambda *a, **k: calls.append("async-db"))
    _check(validation.validated_auth_settings() is settings, "C1B_VALID_CONFIGURATION_REJECTED")
    monkeypatch.setenv(PURPOSE_NAMES[0], " " + values[PURPOSE_NAMES[0]])
    with pytest.raises(RuntimeError, match="^AUTH_CONFIGURATION_INVALID$") as caught:
        validation.validated_auth_settings()
    captured = capsys.readouterr()
    public = captured.out + captured.err + caplog.text + str(caught.value)
    _check(all(value not in public for value in values.values()), "C1B_MATERIAL_OUTPUT_LEAK")
    _check(not calls, "C1B_STARTUP_EXTERNAL_CONNECTION")


@pytest.mark.parametrize("job", ["backend-unit", "backend-integration"])
def test_C1B_R10_CI双Job独立生成用途材料(materials, job):
    source = (Path(__file__).resolve().parents[2] / ".github/workflows/p2-foundation-ci.yml").read_text("utf-8")
    block = source.split(f"  {job}:", 1)[1].split("\n  frontend-build:" if job == "backend-integration" else "\n  backend-integration:", 1)[0]
    for name in PURPOSE_NAMES:
        _check(f'"{name}": secrets.token_urlsafe(' in block, "C1B_CI_PURPOSE_MATERIAL_MISSING")


def test_C1B_R10_Unit敏感扫描失败不得上传原始材料():
    import yaml

    source = (Path(__file__).resolve().parents[2] / ".github/workflows/p2-foundation-ci.yml").read_text("utf-8")
    steps = yaml.safe_load(source)["jobs"]["backend-unit"]["steps"]
    scan = next(step for step in steps if step["name"] == "Scan unit signing material before artifact upload")
    upload = next(step for step in steps if step["name"] == "Upload backend test report")
    _check(scan.get("id") == "unit_signing_sensitive_scan" and scan.get("if") == "always()",
           "C1B_UNIT_SCAN_NOT_FAIL_CLOSED")
    _check(upload.get("if") == "always() && steps.unit_signing_sensitive_scan.outcome == 'success'",
           "C1B_UNIT_UNSAFE_ARTIFACT_UPLOAD")
    for name in PURPOSE_NAMES:
        _check(f'os.environ.get("{name}", "")' in scan["run"], "C1B_UNIT_SCAN_PURPOSE_MISSING")


@pytest.mark.parametrize(("outcome", "allowed"), [
    ("success", True), ("failure", False), ("skipped", False), ("cancelled", False), ("", False),
], ids=["success", "failure", "skipped", "cancelled", "missing"])
def test_C1B_R10_上传条件仅接受扫描成功(outcome, allowed):
    import yaml

    source = (Path(__file__).resolve().parents[2] / ".github/workflows/p2-foundation-ci.yml").read_text("utf-8")
    steps = yaml.safe_load(source)["jobs"]["backend-unit"]["steps"]
    upload = next(step for step in steps if step["name"] == "Upload backend test report")
    # Interpret only this closed GitHub expression, not an arbitrary expression evaluator.
    match = re.fullmatch(r"always\(\) && steps\.unit_signing_sensitive_scan\.outcome == '([a-z]+)'", upload["if"])
    _check(match is not None, "C1B_UNIT_UPLOAD_CONDITION_NOT_CLOSED")
    _check((outcome == match[1]) is allowed, "C1B_UNIT_UPLOAD_WRONG_OUTCOME")


@pytest.mark.parametrize("leak", [False, True], ids=["clean-report", "rejected-report"])
def test_C1B_R10_真实执行扫描脚本且失败日志不回显材料(materials, leak):
    import yaml

    values, _ = materials
    source = (Path(__file__).resolve().parents[2] / ".github/workflows/p2-foundation-ci.yml").read_text("utf-8")
    steps = yaml.safe_load(source)["jobs"]["backend-unit"]["steps"]
    scan = next(step for step in steps if step["name"] == "Scan unit signing material before artifact upload")
    script = scan["run"].split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    with tempfile.TemporaryDirectory(prefix="kg-c1b-scan-contract-") as directory:
        report = Path(directory) / "pytest-report.xml"
        report.write_text(values[PURPOSE_NAMES[0]] if leak else "<testsuites/>", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=directory, env=os.environ.copy(),
            capture_output=True, text=True, timeout=15, check=False,
        )
    _check(result.returncode == (1 if leak else 0), "C1B_UNIT_SCAN_EXIT_WRONG")
    output = result.stdout + result.stderr
    _check(not any(value in output for value in values.values()), "C1B_UNIT_SCAN_LOG_LEAK")
    _check(output.strip() == ("C1B_UNIT_SIGNING_MATERIAL_LEAK" if leak else ""), "C1B_UNIT_SCAN_LOG_UNSAFE")
