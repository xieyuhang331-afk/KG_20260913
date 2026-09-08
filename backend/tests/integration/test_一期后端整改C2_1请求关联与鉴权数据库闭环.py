import json
import secrets
from datetime import datetime
from uuid import UUID

import pytest

from tests.integration.test_一期切片3会员CurrentnessAuthority真实HTTP合同 import (
    _activate_org_admin_for_test,
    _login,
)

pytestmark = pytest.mark.integration


def _require(condition: bool, code: str) -> None:
    if not condition:
        pytest.fail(code, pytrace=False)


_EVENT_FIELDS = {
    "timestamp_utc", "level", "event", "request_id", "method", "route_template",
    "status_code", "duration_ms", "error_code", "retryable",
}


def _safe_success_event(value: dict, *, path: str, request_id: str) -> bool:
    if set(value) != _EVENT_FIELDS:
        return False
    try:
        timestamp = datetime.fromisoformat(value["timestamp_utc"])
        parsed = UUID(value["request_id"])
    except (TypeError, ValueError, AttributeError):
        return False
    return (
        timestamp.tzinfo is not None and parsed.version == 7
        and str(parsed) == value["request_id"] == request_id
        and value["level"] == "INFO" and value["event"] == "request"
        and value["method"] == "POST" and value["route_template"] == path
        and type(value["status_code"]) is int and value["status_code"] == 201
        and type(value["duration_ms"]) is int and value["duration_ms"] >= 0
        and value["error_code"] is None and value["retryable"] is False
    )


@pytest.fixture
def sqlstate_observer():
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    categories = []

    def observe(context):
        # Read only the public SQLSTATE metadata. Do not inspect SQL, parameters,
        # exception messages, connection URLs, or change exception handling.
        state = getattr(context.original_exception, "sqlstate", None)
        categories.append("42501" if state == "42501" else "OTHER" if state else "UNAVAILABLE")

    event.listen(Engine, "handle_error", observe)
    try:
        yield categories
    finally:
        event.remove(Engine, "handle_error", observe)


@pytest.mark.parametrize("slice_name", ["slice2", "slice3"])
def test_C21_R08_Fresh正式权限响应日志与业务审计关联一致(
    pg_database, application_database, real_db_client, capfd, slice_name, sqlstate_observer, request,
):
    from app.core.uuid_generator import Uuid7Generator

    offset = 0 if slice_name == "slice2" else 10
    tenant_id, org_id = 9886101 + offset, 9886103 + offset
    phone = "199" + ("3" if offset else "2") * 8
    recipient = "188" + ("5" if offset else "4") * 8
    password = secrets.token_urlsafe(24)
    tenant_public = Uuid7Generator().generate()
    _require(pg_database.fetch_value(
        f"SELECT count(*) FROM public.tenant WHERE id={tenant_id}"
    ) == 0, "C21_SYNTHETIC_FIXTURE_COLLISION")
    pg_database.execute(
        "INSERT INTO public.platform_org(id,parent_id,org_name,org_code,org_type,status,version) "
        f"VALUES ({org_id},NULL,'C21 synthetic county','C21-{org_id}','county','active',1);"
        "INSERT INTO public.tenant(id,org_id,tenant_code,name,type,province,city,status,created_at,updated_at) "
        f"VALUES ({tenant_id},{org_id},'C21-{tenant_id}','C21 synthetic institution','store','test','test','active',now(),now())"
    )
    activated = _activate_org_admin_for_test(
        pg_database, real_db_client, phone=phone, password=password, org_id=org_id,
        tenant_id=tenant_id, tenant_public_id=tenant_public,
        institution_name="C21 synthetic institution", pilot_batch_code="C21",
        service_tags=("GLUCOSE_METABOLISM",),
    )
    pg_database.execute(
        "INSERT INTO public.institution_service_readiness("
        "tenant_id,readiness_status,reason_codes,qualified_therapist_count,computed_at,"
        "evidence_version,input_digest,result_digest,source_versions,next_expiry_at,version) VALUES ("
        f"{tenant_id},'SERVICE_READY',ARRAY[]::text[],1,now(),1,repeat('c',64),repeat('d',64),"
        "'{}'::jsonb,current_date+30,1)"
    )
    application_select = application_database.fetch_value(
        "SELECT has_table_privilege(current_user,'public.institution_application','SELECT')"
    )
    request.node.user_properties.append(("C21_APPLICATION_SELECT", str(application_select)))
    _require(application_select is False, "C21_APPLICATION_AUTHORITY_EXPANDED")
    headers = _login(real_db_client, phone, password, totp_secret=str(activated["totp_secret"]))
    if slice_name == "slice2":
        path = "/api/v1/institution/therapist-invitations"
        payload = {"phone": recipient, "expires_in_minutes": 30}
        audit_table, action = "therapist_workflow_audit", "THERAPIST_INVITED"
        object_table = "therapist_invitation"
        request_id = "01990000-0000-7000-8000-000000000ABC"
    else:
        path = "/api/v1/institution/member-invitations"
        payload = {"phone": recipient, "mode": "SELF"}
        audit_table, action = "member_enrollment_audit", "MEMBER_INVITATION_CREATED"
        object_table = "member_service_invitation"
        request_id = "synthetic-invalid-correlation"

    # Both names come only from the closed test branch above, never user input.
    counts_before = tuple(pg_database.fetch_value(f"SELECT count(*) FROM public.{table}")
                          for table in (object_table, audit_table))
    capfd.readouterr()
    denied = real_db_client.post(path, json=payload, headers={
        "Authorization": "Bearer synthetic-invalid", "Idempotency-Key": "c21-denied",
        "X-Request-ID": request_id,
    })
    _require(denied.status_code == 401, "C21_AUTHENTICATION_PRIORITY_CHANGED")
    _require(tuple(pg_database.fetch_value(f"SELECT count(*) FROM public.{table}")
                   for table in (object_table, audit_table)) == counts_before, "C21_DENIED_REQUEST_MUTATED")
    denied_output = capfd.readouterr()
    _require("synthetic-invalid" not in denied_output.out + denied_output.err + denied.text,
             "C21_DENIED_INPUT_EXPOSED")

    sqlstate_observer.clear()
    result = real_db_client.post(path, json=payload, headers={
        **headers, "Idempotency-Key": "c21-audit-" + slice_name, "X-Request-ID": request_id,
    })
    try:
        response_code = result.json().get("code")
    except (ValueError, AttributeError):
        response_code = None
    safe_code = response_code if response_code in {
        "DEPENDENCY_UNAVAILABLE", "ACTOR_CURRENTNESS_FORBIDDEN", "AUTHENTICATION_REQUIRED",
        "THERAPIST_INVITATION_CONFLICT", "REQUEST_VALIDATION_FAILED",
    } else "UNKNOWN"
    counts_after = tuple(pg_database.fetch_value(f"SELECT count(*) FROM public.{table}")
                         for table in (object_table, audit_table))
    request.node.user_properties.extend([
        ("C21_STAGE", "AUTHORIZED_RESPONSE"), ("C21_HTTP_STATUS", str(result.status_code)),
        ("C21_SAFE_CODE", safe_code),
        ("C21_SQLSTATE_42501_COUNT", str(sqlstate_observer.count("42501"))),
        ("C21_SQLSTATE_OTHER_COUNT", str(sqlstate_observer.count("OTHER"))),
        ("C21_SQLSTATE_UNAVAILABLE_COUNT", str(sqlstate_observer.count("UNAVAILABLE"))),
        ("C21_BUSINESS_BEFORE_COUNT", str(counts_before[0])), ("C21_BUSINESS_AFTER_COUNT", str(counts_after[0])),
        ("C21_AUDIT_BEFORE_COUNT", str(counts_before[1])), ("C21_AUDIT_AFTER_COUNT", str(counts_after[1])),
    ])
    _require(result.status_code == 201, "C21_AUTHORIZED_JOURNEY_FAILED")
    final_id = result.headers.get("x-request-id", "")
    try:
        parsed = UUID(final_id)
    except ValueError:
        pytest.fail("C21_RESPONSE_ID_INVALID", pytrace=False)
    _require(parsed.version == 7 and final_id == str(parsed), "C21_RESPONSE_ID_NOT_CANONICAL_V7")
    _require(final_id == request_id.lower() if slice_name == "slice2" else final_id != request_id,
             "C21_HEADER_CANONICALIZATION_CHANGED")
    data = result.json()["data"] if slice_name == "slice2" else result.json()
    try:
        invitation = UUID(data["invitation_id"])
    except (KeyError, ValueError):
        pytest.fail("C21_INVITATION_DTO_INVALID", pytrace=False)
    audit_id = pg_database.fetch_value(
        f"SELECT request_id FROM public.{audit_table} WHERE object_id='{invitation}' AND action='{action}'"
    )
    _require(str(audit_id) == final_id, "C21_HTTP_AUDIT_CORRELATION_MISMATCH")
    _require(tuple(pg_database.fetch_value(f"SELECT count(*) FROM public.{table}")
                   for table in (object_table, audit_table)) == tuple(value + 1 for value in counts_before),
             "C21_EXTRA_BUSINESS_MUTATION")
    output = capfd.readouterr()
    events = [json.loads(line) for line in output.err.splitlines() if line.startswith("{")]
    matched = [event for event in events if event.get("request_id") == final_id]
    _require(len(matched) == 1, "C21_REQUEST_EVENT_COUNT_INVALID")
    _require(set(matched[0]) == _EVENT_FIELDS, "C21_REQUEST_EVENT_FIELD_SET_INVALID")
    short_actor = str(activated["user_id"])
    old_matches = sorted(key for key, value in matched[0].items() if short_actor in str(value))
    request.node.user_properties.append(("C21_OLD_ACTOR_SUBSTRING_FIELDS", ",".join(old_matches) or "NONE"))
    _require(_safe_success_event(matched[0], path=path, request_id=final_id), "C21_REQUEST_EVENT_SEMANTICS_INVALID")
    # Prove that real actor fields and embedded actor strings remain rejected.
    _require(not _safe_success_event({**matched[0], "actor": short_actor}, path=path, request_id=final_id),
             "C21_ACTOR_FIELD_GUARD_MISSING")
    for key in ("event", "method", "route_template", "error_code", "request_id", "timestamp_utc"):
        _require(not _safe_success_event({**matched[0], key: "actor=" + short_actor}, path=path, request_id=final_id),
                 "C21_EMBEDDED_ACTOR_GUARD_MISSING")
    _require(matched[0]["route_template"] == path and matched[0]["status_code"] == 201,
             "C21_REQUEST_EVENT_ROUTE_OR_STATUS_INVALID")
    _require(not any(value in output.out + output.err for value in (
        phone, recipient, password, str(activated["totp_secret"]), headers["Authorization"],
        str(invitation), "C21 synthetic institution", "C21 synthetic county",
    )), "C21_REQUEST_EVENT_SENSITIVE_VALUE")
    request.node.user_properties.append(("C21_PUBLIC_EVENT_GUARDS", "PASS"))
