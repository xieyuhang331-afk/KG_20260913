from __future__ import annotations

import base64
import hashlib
import secrets
import time
import traceback
from datetime import UTC, datetime

import pytest

from app.modules.auth.service import hash_password
from app.modules.institution_onboarding.domain import generate_totp

pytestmark = pytest.mark.integration


def _http(client, stage, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    # Only fixed stage names and status are emitted; never response bodies or tokens.
    print(f"HTTP_STAGE={stage};STATUS={response.status_code}")
    if response.status_code != 200:
        pytest.fail(f"HTTP_STAGE={stage};STATUS={response.status_code}", pytrace=False)
    return response.json()["data"]


def test_同一机构从邀请到补正批准后重新登录(real_db_client, pg_database):
    client = real_db_client
    password = secrets.token_urlsafe(24)
    phone = "136" + "8" * 8
    applicant_phone = "138" + "8" * 8
    pg_database.execute(
        'INSERT INTO public."user"(id,phone,password_hash,role,status,tenant_id) '
        f"VALUES(9977101,'{phone}','{hash_password(password)}','super_admin','active',NULL);"
        "INSERT INTO public.platform_org(id,parent_id,org_name,org_code,org_type,status,version) VALUES "
        "(9977102,NULL,'合成总部','SAME-HQ','headquarter','active',1),"
        "(9977103,9977102,'合成省','SAME-P','province','active',1),"
        "(9977104,9977103,'合成市','SAME-C','city','active',1),"
        "(9977105,9977104,'合成区','SAME-D','county','active',1)"
    )
    login = _http(client, "PLATFORM_LOGIN", "POST", "/api/v1/auth/login",
                  json={"phone": phone, "password": password})
    platform = {"Authorization": "Bearer " + login["access_token"]}
    issued = _http(client, "INVITE", "POST", "/api/v1/platform/institution-invitations",
                   headers={**platform, "Idempotency-Key": "same-subject-invite"},
                   json={"institution_name": "合成同主体机构", "institution_type": "HEALTH_STORE",
                         "applicant_phone": applicant_phone, "pilot_batch_code": "SAME-SUBJECT",
                         "administrative_region_id": 9977105, "expires_in_minutes": 60})
    secret = base64.b32encode(secrets.token_bytes(20)).decode("ascii")
    activated = _http(client, "ACTIVATE", "POST", "/api/v1/institution-onboarding/activate",
                      headers={"Idempotency-Key": "same-subject-activate"},
                      json={"invitation_id": issued["invitation_id"], "phone": applicant_phone,
                            "short_code": issued["short_code"], "password": password,
                            "totp_secret": secret, "totp_code": generate_totp(secret, at=datetime.now(UTC))})
    admin_login = _http(client, "ADMIN_LOGIN", "POST", "/api/v1/auth/login",
                        json={"phone": applicant_phone, "password": password,
                              "totp_code": generate_totp(secret, at=datetime.now(UTC))})
    assert str(admin_login["user"]["id"]) == str(activated["user_id"])
    admin = {"Authorization": "Bearer " + admin_login["access_token"]}
    current = _http(client, "READ_DRAFT", "GET", "/api/v1/institution-onboarding/application", headers=admin)
    assert current["application_id"] == activated["application_id"]
    assert "credit_code" not in current["draft"]
    assert "contact_phone" not in current["draft"]
    draft = {"credit_code": "91310000TEST000002", "legal_representative_name": "合成负责人",
             "registered_address": "合成注册地址", "service_address": "合成服务地址",
             "contact_name": "合成联系人", "contact_phone": applicant_phone,
             "contact_email": "synthetic@example.invalid", "service_tags": ["HYPERTENSION"]}
    saved = _http(client, "SAVE_DRAFT", "PUT", "/api/v1/institution-onboarding/application",
                  headers=admin, json={**draft, "expected_version": current["version"]})
    content = b"%PDF-1.7\nsynthetic-same-subject\n%%EOF"
    metadata = {"size": len(content), "mime_type": "application/pdf", "sha256": hashlib.sha256(content).hexdigest()}
    uploaded = _http(client, "UPLOAD_INIT", "POST", "/api/v1/private-files/uploads",
                     headers=admin, json={**metadata, "purpose": "BUSINESS_LICENSE"})
    file_id = uploaded["file_id"]
    _http(client, "UPLOAD_CONTENT", "PUT", f"/api/v1/private-files/uploads/{file_id}/content",
          headers={**admin, "Content-Type": "application/pdf"}, content=content)
    _http(client, "UPLOAD_COMPLETE", "POST", f"/api/v1/private-files/uploads/{file_id}/complete",
          headers=admin, json=metadata)
    for _ in range(30):
        try:
            rows = pg_database.fetch_rows(
                "SELECT status FROM public.private_file WHERE file_id=$1", file_id
            )
            status = rows[0]["status"] if rows else None
        except Exception as exc:
            frames = traceback.extract_tb(exc.__traceback__)
            project_frame = next(
                (
                    frame
                    for frame in reversed(frames)
                    if "backend" in frame.filename and ".venv" not in frame.filename
                ),
                None,
            )
            location = (
                f"{project_frame.name}:{project_frame.lineno}"
                if project_frame is not None
                else "UNAVAILABLE"
            )
            pytest.fail(
                "SCAN_POLL_FAILURE;"
                f"TYPE={type(exc).__name__};LOCATION={location}",
                pytrace=False,
            )
        if status == "CLEAN":
            print("DEPENDENCY=SYNTHETIC_SCANNER;RESULT=CLEAN")
            break
        time.sleep(1)
    else:
        pytest.fail("SCAN_STAGE_FAILURE;TYPE=WorkerTimeout", pytrace=False)
    licenses = [{"license_type": "BUSINESS_LICENSE", "private_file_id": file_id,
                 "valid_from": "2026-01-01", "valid_until": "2027-12-31"}]
    submitted = _http(client, "SUBMIT", "POST", "/api/v1/institution-onboarding/application/submit",
                      headers={**admin, "Idempotency-Key": "same-subject-submit"},
                      json={"expected_version": saved["version"], "licenses": licenses})
    sensitive_before = pg_database.fetch_rows(
        "SELECT draft_payload->>'credit_code_digest' AS credit_digest,"
        "draft_payload->>'credit_code_ciphertext' AS credit_ciphertext,"
        "draft_payload->>'contact_phone_digest' AS phone_digest,"
        "draft_payload->>'contact_phone_ciphertext' AS phone_ciphertext "
        "FROM public.institution_application WHERE application_id=$1",
        activated["application_id"],
    )[0]
    review_path = f"/api/v1/platform/institution-reviews/{activated['application_id']}"
    _http(client, "REVIEW_DETAIL", "GET", review_path, headers=platform)
    corrected = _http(client, "REQUEST_CORRECTION", "POST", review_path + "/decision",
                      headers={**platform, "Idempotency-Key": "same-subject-correction"},
                      json={"decision": "NEEDS_CORRECTION", "expected_version": submitted["version"],
                            "correction_fields": ["service_address"], "reason_code": "ADDRESS_UNCLEAR"})
    correction_view = _http(client, "READ_CORRECTIONS", "GET", "/api/v1/institution-onboarding/application/corrections", headers=admin)
    assert correction_view["fields"] == ["service_address"]
    resubmit_payload = {
        "service_address": "合成已补正服务地址",
        "expected_version": corrected["version"],
        "licenses": licenses,
    }
    effects_before_rejections = pg_database.fetch_rows(
        "SELECT "
        "(SELECT count(*) FROM public.institution_application_revision WHERE application_id=$1) AS revisions,"
        "(SELECT count(*) FROM public.institution_onboarding_audit WHERE object_id=$1) AS audits,"
        "(SELECT count(*) FROM public.institution_onboarding_outbox WHERE aggregate_id=$1) AS outbox,"
        "(SELECT count(*) FROM public.institution_onboarding_idempotency WHERE actor_scope=$2 AND operation='APPLICATION_RESUBMIT') AS receipts",
        activated["application_id"],
        str(activated["user_id"]),
    )[0]
    invalid_resubmits = (
        ({"expected_version": corrected["version"], "licenses": licenses}, "missing-field"),
        ({**resubmit_payload, "service_address": None}, "null-field"),
        ({**resubmit_payload, "registered_address": "合成未点名地址"}, "unreviewed-field"),
    )
    for invalid_payload, key_suffix in invalid_resubmits:
        rejected = client.post(
            "/api/v1/institution-onboarding/application/resubmit",
            headers={**admin, "Idempotency-Key": "same-subject-reject-" + key_suffix},
            json=invalid_payload,
        )
        assert rejected.status_code == 403
    assert pg_database.fetch_rows(
        "SELECT "
        "(SELECT count(*) FROM public.institution_application_revision WHERE application_id=$1) AS revisions,"
        "(SELECT count(*) FROM public.institution_onboarding_audit WHERE object_id=$1) AS audits,"
        "(SELECT count(*) FROM public.institution_onboarding_outbox WHERE aggregate_id=$1) AS outbox,"
        "(SELECT count(*) FROM public.institution_onboarding_idempotency WHERE actor_scope=$2 AND operation='APPLICATION_RESUBMIT') AS receipts",
        activated["application_id"],
        str(activated["user_id"]),
    )[0] == effects_before_rejections
    resubmitted = _http(client, "RESUBMIT", "POST", "/api/v1/institution-onboarding/application/resubmit",
                        headers={**admin, "Idempotency-Key": "same-subject-resubmit"},
                        json=resubmit_payload)
    assert pg_database.fetch_rows(
        "SELECT draft_payload->>'credit_code_digest' AS credit_digest,"
        "draft_payload->>'credit_code_ciphertext' AS credit_ciphertext,"
        "draft_payload->>'contact_phone_digest' AS phone_digest,"
        "draft_payload->>'contact_phone_ciphertext' AS phone_ciphertext "
        "FROM public.institution_application WHERE application_id=$1",
        activated["application_id"],
    )[0] == sensitive_before
    side_effects = pg_database.fetch_rows(
        "SELECT "
        "(SELECT count(*) FROM public.institution_application_revision WHERE application_id=$1) AS revisions,"
        "(SELECT count(*) FROM public.institution_onboarding_audit WHERE object_id=$1) AS audits,"
        "(SELECT count(*) FROM public.institution_onboarding_outbox WHERE aggregate_id=$1) AS outbox,"
        "(SELECT count(*) FROM public.institution_onboarding_idempotency WHERE actor_scope=$2 AND operation='APPLICATION_RESUBMIT') AS receipts",
        activated["application_id"],
        str(activated["user_id"]),
    )[0]
    replayed = _http(client, "RESUBMIT_REPLAY", "POST", "/api/v1/institution-onboarding/application/resubmit",
                     headers={**admin, "Idempotency-Key": "same-subject-resubmit"}, json=resubmit_payload)
    assert replayed == resubmitted
    assert pg_database.fetch_rows(
        "SELECT "
        "(SELECT count(*) FROM public.institution_application_revision WHERE application_id=$1) AS revisions,"
        "(SELECT count(*) FROM public.institution_onboarding_audit WHERE object_id=$1) AS audits,"
        "(SELECT count(*) FROM public.institution_onboarding_outbox WHERE aggregate_id=$1) AS outbox,"
        "(SELECT count(*) FROM public.institution_onboarding_idempotency WHERE actor_scope=$2 AND operation='APPLICATION_RESUBMIT') AS receipts",
        activated["application_id"],
        str(activated["user_id"]),
    )[0] == side_effects
    explicit_null_conflict = client.post(
        "/api/v1/institution-onboarding/application/resubmit",
        headers={**admin, "Idempotency-Key": "same-subject-resubmit"},
        json={**resubmit_payload, "contact_phone": None},
    )
    assert explicit_null_conflict.status_code == 409
    assert pg_database.fetch_rows(
        "SELECT "
        "(SELECT count(*) FROM public.institution_application_revision WHERE application_id=$1) AS revisions,"
        "(SELECT count(*) FROM public.institution_onboarding_audit WHERE object_id=$1) AS audits,"
        "(SELECT count(*) FROM public.institution_onboarding_outbox WHERE aggregate_id=$1) AS outbox,"
        "(SELECT count(*) FROM public.institution_onboarding_idempotency WHERE actor_scope=$2 AND operation='APPLICATION_RESUBMIT') AS receipts",
        activated["application_id"],
        str(activated["user_id"]),
    )[0] == side_effects
    conflict = client.post(
        "/api/v1/institution-onboarding/application/resubmit",
        headers={**admin, "Idempotency-Key": "same-subject-resubmit"},
        json={**resubmit_payload, "service_address": "合成另一服务地址"},
    )
    assert conflict.status_code == 409
    phone_correction = _http(
        client,
        "REQUEST_PHONE_CORRECTION",
        "POST",
        review_path + "/decision",
        headers={**platform, "Idempotency-Key": "same-subject-phone-correction"},
        json={
            "decision": "NEEDS_CORRECTION",
            "expected_version": resubmitted["version"],
            "correction_fields": ["contact_phone"],
            "reason_code": "CONTACT_INVALID",
        },
    )
    phone_resubmitted = _http(
        client,
        "RESUBMIT_PHONE_CORRECTION",
        "POST",
        "/api/v1/institution-onboarding/application/resubmit",
        headers={**admin, "Idempotency-Key": "same-subject-phone-resubmit"},
        json={
            "contact_phone": "13777777777",
            "expected_version": phone_correction["version"],
            "licenses": licenses,
        },
    )
    sensitive_after_phone = pg_database.fetch_rows(
        "SELECT draft_payload->>'credit_code_digest' AS credit_digest,"
        "draft_payload->>'credit_code_ciphertext' AS credit_ciphertext,"
        "draft_payload->>'contact_phone_digest' AS phone_digest,"
        "draft_payload->>'contact_phone_ciphertext' AS phone_ciphertext "
        "FROM public.institution_application WHERE application_id=$1",
        activated["application_id"],
    )[0]
    assert sensitive_after_phone["credit_digest"] == sensitive_before["credit_digest"]
    assert sensitive_after_phone["credit_ciphertext"] == sensitive_before["credit_ciphertext"]
    assert sensitive_after_phone["phone_digest"] != sensitive_before["phone_digest"]
    assert sensitive_after_phone["phone_ciphertext"] != sensitive_before["phone_ciphertext"]
    approved = _http(client, "APPROVE", "POST", review_path + "/decision",
                     headers={**platform, "Idempotency-Key": "same-subject-approve"},
                     json={"decision": "APPROVED", "expected_version": phone_resubmitted["version"]})
    assert approved["status"] == "APPROVED"
    relogin = _http(client, "ADMIN_RELOGIN", "POST", "/api/v1/auth/login",
                    json={"phone": applicant_phone, "password": password,
                          "totp_code": generate_totp(secret, at=datetime.now(UTC))})
    final_public = _http(
        client,
        "READ_FINAL_PUBLIC",
        "GET",
        "/api/v1/institution-onboarding/application",
        headers={"Authorization": "Bearer " + relogin["access_token"]},
    )
    assert "credit_code" not in final_public["draft"]
    assert "contact_phone" not in final_public["draft"]
    rows = pg_database.fetch_rows(
        'SELECT u.tenant_id,t.org_id,a.tenant_public_id,a.applicant_user_id '
        'FROM public."user" u JOIN public.tenant t ON t.id=u.tenant_id '
        'JOIN public.institution_application a ON a.tenant_internal_id=t.id '
        'WHERE u.id=$1', int(activated["user_id"]),
    )
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == relogin["user"]["tenant_id"]
    assert rows[0]["org_id"] == relogin["user"]["org_id"] == 9977105
    assert str(rows[0]["tenant_public_id"]) == approved["tenant_id"]
    assert rows[0]["applicant_user_id"] == int(activated["user_id"])
    print("SAME_SUBJECT_AND_PERSISTED_OWNERSHIP=PASS")
