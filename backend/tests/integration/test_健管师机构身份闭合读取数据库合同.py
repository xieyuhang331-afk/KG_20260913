import secrets

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from tests.integration.test_一期切片3会员CurrentnessAuthority真实HTTP合同 import (
    _activate_org_admin_for_test,
    _login,
)

pytestmark = pytest.mark.integration

def require(value, code):
    if not value:
        pytest.fail(code, pytrace=False)


@pytest.mark.parametrize("role_kind", ["writer", "authority"])
@pytest.mark.parametrize("release_kind", ["service", "dependency", "dependency_cancel"])
@pytest.mark.parametrize("fault", ["none", "disconnect", "cancel"])
def test_I1_真实会话连接池与数据库锁释放(pg_database, monkeypatch, request, role_kind, release_kind, fault):
    import asyncio
    import json
    import os
    from contextlib import asynccontextmanager
    from importlib.metadata import version

    from sqlalchemy import text
    from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_connection
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core import database
    from app.modules.therapist_qualification import service

    async def exercise():
        variable = "KG_TEST_THERAPIST_ONBOARDING_WRITER_DATABASE_URL" if role_kind == "writer" else "KG_TEST_DATABASE_URL"
        url = os.environ[variable]
        engine = create_async_engine(url, pool_size=1, max_overflow=0, pool_timeout=2)
        probe_engine = create_async_engine(url, pool_size=1, max_overflow=0, pool_timeout=2)
        session = async_sessionmaker(engine, expire_on_commit=False)()
        connection = None
        key = secrets.randbelow(1000000000) + 1800000000
        facts = {"sqlalchemy": version("sqlalchemy"), "asyncpg": version("asyncpg"), "fault_reached": False}
        observed = None
        primary = asyncio.CancelledError() if release_kind == "dependency_cancel" else None
        try:
            connection = await session.connection()
            pid = (await connection.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            await connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
            raw = await connection.get_raw_connection()
            target = raw.dbapi_connection
            async with probe_engine.connect() as probe:
                held = not (await probe.execute(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key})).scalar_one()
                await probe.rollback()
            require(held and engine.pool.checkedout() == 1, "I1_LOCK_SETUP_INVALID")
            original = AsyncAdapt_asyncpg_connection._rollback_and_discard

            async def cancel_at_driver_rollback(adapter):
                if adapter is target and not facts["fault_reached"]:
                    facts["fault_reached"] = True
                    asyncio.current_task().cancel()
                return await original(adapter)

            if fault == "cancel":
                # Test-only cancellation at the real dialect await; no fake close.
                monkeypatch.setattr(AsyncAdapt_asyncpg_connection, "_rollback_and_discard", cancel_at_driver_rollback)
            elif fault == "disconnect":
                async with probe_engine.connect() as probe:
                    facts["fault_reached"] = bool((await probe.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})).scalar_one())
                    await probe.rollback()
                for _ in range(100):
                    if raw.driver_connection.is_closed():
                        break
                    await asyncio.sleep(0.01)
                require(raw.driver_connection.is_closed(), "I1_DISCONNECT_NOT_OBSERVED")

            async def release():
                if release_kind == "service":
                    await service._rollback(session)
                else:
                    if role_kind == "writer":
                        monkeypatch.setattr(database, "get_slice2_session_factory", lambda _: lambda: session)
                        context = database._slice2_session("onboarding_writer")
                    else:
                        monkeypatch.setattr(database, "get_session_factory", lambda: lambda: session)
                        context = asynccontextmanager(database.get_db_session)()
                    async with context:
                        if primary is not None:
                            raise primary

            # A task-local cancellation must not cancel the diagnostic observer.
            task = asyncio.create_task(release())
            try:
                await task
            except (Exception, asyncio.CancelledError) as error:
                observed = error
            facts["release_task_done"] = task.done()
            facts["session_transaction_present"] = session.in_transaction()
            facts["original_connection_closed"] = connection.closed
            facts["original_connection_invalidated"] = connection.invalidated
            facts["pool_checkedout_before_test_cleanup"] = engine.pool.checkedout()
            async with probe_engine.connect() as probe:
                facts["database_lock_available_before_test_cleanup"] = bool((await probe.execute(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key})).scalar_one())
                facts["original_backend_present"] = bool((await probe.execute(text("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid=:pid)"), {"pid": pid})).scalar_one())
                await probe.rollback()
            facts["original_cancellation_preserved"] = primary is None or observed is primary
            facts["observed_cancellation"] = isinstance(observed, asyncio.CancelledError)
            facts["observed_ordinary_error"] = isinstance(observed, Exception)
            request.node.user_properties.append(("I1_BEFORE_TEST_CLEANUP", json.dumps(facts, sort_keys=True)))
            require(fault == "none" or facts["fault_reached"], "I1_FAULT_NOT_REACHED")
            require(facts["original_cancellation_preserved"], "I1_ORIGINAL_CANCEL_REPLACED")
            require(facts["database_lock_available_before_test_cleanup"] and facts["pool_checkedout_before_test_cleanup"] == 0, "I1_RELEASE_NOT_CONFIRMED")
        finally:
            # Public API rescue uses the test-held original connection, never the production verdict.
            try:
                if connection is not None and not connection.closed:
                    await connection.invalidate()
                    await connection.close()
                await session.close()
            finally:
                checked_out = engine.pool.checkedout()
                await engine.dispose()
                await probe_engine.dispose()
                request.node.user_properties.append(("I1_AFTER_TEST_RESCUE_CHECKEDOUT", str(checked_out)))

    try:
        asyncio.run(exercise())
    except (Exception, asyncio.CancelledError):
        pytest.fail("I1_BOUNDED_DIAGNOSTIC_FAILED_SEE_SAFE_FACTS", pytrace=False)

def test_G01_正式Application邀请201与审计(pg_database, application_database, real_db_client, request, monkeypatch):
    from app.core.uuid_generator import Uuid7Generator
    tenant_id, org_id = 9887101, 9887103
    phone, recipient = "199" + "6" * 8, "188" + "7" * 8
    password = secrets.token_urlsafe(24)
    public_id = Uuid7Generator().generate()
    pg_database.execute(
        "INSERT INTO public.platform_org(id,parent_id,org_name,org_code,org_type,status,version) "
        f"VALUES ({org_id},NULL,'Gate synthetic county','GATE-{org_id}','county','active',1);"
        "INSERT INTO public.tenant(id,org_id,tenant_code,name,type,province,city,status,created_at,updated_at) "
        f"VALUES ({tenant_id},{org_id},'GATE-{tenant_id}','Gate synthetic institution','store','test','test','active',now(),now())"
    )
    activated = _activate_org_admin_for_test(
        pg_database, real_db_client, phone=phone,password=password,org_id=org_id,
        tenant_id=tenant_id,tenant_public_id=public_id,
        institution_name="Gate synthetic institution",pilot_batch_code="GATE",
        service_tags=("GLUCOSE_METABOLISM",),
    )
    headers = _login(real_db_client,phone,password,totp_secret=str(activated["totp_secret"]))
    def counts():
        return tuple(pg_database.fetch_value("SELECT count(*) FROM public." + table)
                     for table in ("therapist_invitation", "therapist_workflow_audit"))
    before = counts()
    states = []
    audited = (
        "SELECT a.tenant_public_id FROM public.institution_application a "
        "JOIN public.tenant t ON t.id=a.tenant_internal_id "
        "WHERE a.tenant_internal_id=:tenant_id AND a.status='APPROVED' "
        "AND a.tenant_public_id IS NOT NULL AND t.status='active' "
        "FOR SHARE OF a,t"
    ).replace(":tenant_id", "$1")
    def observe(context):
        state = getattr(context.original_exception,"sqlstate",None)
        # Exact known compiled query only; never retain SQL or parameters.
        query = context.statement or ""
        operation = "TENANT_PUBLIC_ID_LOCK" if query == audited else "OTHER"
        states.append((operation, "42501" if state == "42501" else "OTHER" if state else "UNAVAILABLE"))
    event.listen(Engine,"handle_error",observe)
    try:
        result = real_db_client.post("/api/v1/institution/therapist-invitations",
            json={"phone":recipient,"expires_in_minutes":30},
            headers={**headers,"Idempotency-Key":"gate-baseline",
                     "X-Request-ID":"01990000-0000-7000-8000-000000000abc"})
    finally:
        event.remove(Engine,"handle_error",observe)
    after = counts()
    code = result.json().get("code")
    request.node.user_properties.extend([
        ("STAGE","AUTHORIZED_RESPONSE"),("HTTP_STATUS",str(result.status_code)),
        ("SAFE_CODE",code if code in {"DEPENDENCY_UNAVAILABLE","ACTOR_CURRENTNESS_FORBIDDEN"} else "UNKNOWN"),
        ("ERROR_OPERATION",states[0][0] if states else "NONE"),
        ("ERROR_SQLSTATE",states[0][1] if states else "NONE"),
        ("ERROR_COUNT",str(len(states))),
        ("BUSINESS_BEFORE",str(before[0])),("BUSINESS_AFTER",str(after[0])),
        ("AUDIT_BEFORE",str(before[1])),("AUDIT_AFTER",str(after[1])),
        ("APPLICATION_TABLE_SELECT",str(application_database.fetch_value(
            "SELECT has_table_privilege(current_user,'public.institution_application','SELECT')"))),
    ])
    require(result.status_code == 201,"GATE_BASELINE_INVITATION_NOT_CREATED")
    invitation = result.json()["data"]["invitation_id"]
    audit = pg_database.fetch_value("SELECT count(*) FROM public.therapist_workflow_audit "
        f"WHERE object_id='{invitation}' AND request_id='01990000-0000-7000-8000-000000000abc'")
    require(audit == 1 and after == tuple(x+1 for x in before),"GATE_AUDIT_OR_MUTATION_MISMATCH")
    import asyncio
    import base64

    from app.modules.institution_onboarding.domain import generate_totp
    from app.modules.therapist_qualification.service import utcnow

    asyncio.run(_verify_authority_locks(tenant_id, public_id))
    therapist_secret = base64.b32encode(secrets.token_bytes(20)).decode("ascii")
    therapist_password = secrets.token_urlsafe(24)
    activation = real_db_client.post(
        "/api/v1/therapist-onboarding/activate",
        json={"invitation_id": invitation, "phone": recipient,
              "short_code": result.json()["data"]["short_code"],
              "password": therapist_password, "totp_secret": therapist_secret,
              "totp_code": generate_totp(therapist_secret, at=utcnow())},
        headers={"Idempotency-Key": "gate-activate", "X-Request-ID": str(Uuid7Generator().generate())},
    )
    require(activation.status_code == 201, "GATE_ACTIVATE_NOT_CREATED")
    therapist_headers = _login(real_db_client, recipient, therapist_password, totp_secret=therapist_secret)
    detail = real_db_client.get("/api/v1/therapist-onboarding/profile", headers=therapist_headers)
    require(detail.status_code == 200, "GATE_SELF_PROFILE_NOT_READABLE")
    draft = real_db_client.put(
        "/api/v1/therapist-onboarding/profile",
        json={"real_name": "Gate synthetic therapist", "display_name": "Gate synthetic display",
              "practice_summary": "Synthetic practice", "service_tags": ["GLUCOSE_METABOLISM"],
              "expected_version": activation.json()["data"]["version"]},
        headers={**therapist_headers, "Idempotency-Key": "gate-profile",
                 "X-Request-ID": str(Uuid7Generator().generate())},
    )
    require(draft.status_code == 200, "GATE_PROFILE_NOT_SAVED")
    detail = real_db_client.get("/api/v1/therapist-onboarding/profile", headers=therapist_headers)
    require(detail.status_code == 200, "GATE_ENCRYPTED_PROFILE_NOT_READABLE")
    _verify_qualification_http_journey(
        pg_database, real_db_client, therapist_headers,
        activation.json()["data"]["therapist_id"], draft.json()["data"]["version"],
        monkeypatch,
    )
    request.node.user_properties.append(("REAL_HTTP_ENTRANCES_PASSED", "8"))


def _verify_qualification_http_journey(database, client, therapist_headers, therapist_id, version, monkeypatch):
    from datetime import date, timedelta

    from app.core.uuid_generator import Uuid7Generator
    from app.modules.auth.service import hash_password

    reviewer_id = 9887110
    reviewer_phone = "177" + "4" * 8
    password = secrets.token_urlsafe(24)
    encoded_password = hash_password(password).replace("'", "''")
    database.execute(
        'INSERT INTO public."user"(id,phone,password_hash,role,status,tenant_id) '
        f"VALUES ({reviewer_id},'{reviewer_phone}','{encoded_password}','super_admin','active',NULL)"
    )
    reviewer_headers = _login(client, reviewer_phone, password)
    owner = database.fetch_value(
        f"SELECT user_id FROM public.therapist_profile WHERE therapist_id='{therapist_id}'"
    )

    def qualification(sequence):
        file_id = str(Uuid7Generator().generate())
        database.execute(
            "INSERT INTO public.private_file(file_id,purpose,owner_user_id,declared_size,declared_mime_type,"
            "declared_sha256,actual_size,actual_mime_type,actual_sha256,object_key,status,created_at,expires_at,scanned_at) "
            f"VALUES ('{file_id}','THERAPIST_QUALIFICATION',{int(owner)},1,'application/pdf',repeat('a',64),"
            f"1,'application/pdf',repeat('a',64),'gate-synthetic/{file_id}','CLEAN',now(),now()+interval '1 day',now())"
        )
        return {"qualification_type": "METABOLIC_HEALTH_PRACTICE", "certificate_no": f"GATE{sequence}SYNTHETIC",
                "issuer_name": "Gate synthetic issuer", "valid_from": str(date.today()-timedelta(days=1)),
                "valid_until": str(date.today()+timedelta(days=365)), "attachment_file_ids": [file_id]}

    def business_snapshot():
        tables = database.fetch_column(
            "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public' "
            "AND starts_with(tablename,'therapist_') ORDER BY tablename"
        )
        require(bool(tables), "GATE_THERAPIST_SNAPSHOT_EMPTY")
        return tuple(database.fetch_column(
            'SELECT row_to_json(t)::text FROM public."' + table.replace('"', '""')
            + '" t ORDER BY row_to_json(t)::text'
        ) for table in tables)

    def rejected(path, body, step, expected=409, authenticated=True, idempotency_key=None):
        before = business_snapshot()
        response = client.post(path, json=body, headers={
            **(therapist_headers if authenticated else {}),
            "Idempotency-Key": idempotency_key or "gate-negative-" + step,
            "X-Request-ID": str(Uuid7Generator().generate()),
        })
        require(response.status_code == expected, "GATE_REJECTION_STATUS_INVALID")
        require(before == business_snapshot(), "GATE_REJECTED_REQUEST_BUSINESS_MUTATION")

    def post(path, body, headers, step, status=201):
        safe_errors = []

        def observe(context):
            code = getattr(context.original_exception, "sqlstate", None)
            operation = "DECISION_LOCK" if "therapist_review_decision" in (context.statement or "") else "OTHER"
            safe_errors.append((operation, "42501" if code == "42501" else "OTHER"))

        event.listen(Engine, "handle_error", observe)
        try:
            response = client.post(path, json=body, headers={**headers, "Idempotency-Key": f"gate-{step}",
                                  "X-Request-ID": str(Uuid7Generator().generate())})
        finally:
            event.remove(Engine, "handle_error", observe)
        if response.status_code != status:
            code = response.json().get("code", response.json().get("detail"))
            safe_code = code if code in {"DEPENDENCY_UNAVAILABLE", "THERAPIST_CORRECTION_SCOPE_CONFLICT"} else "OTHER"
            operation, sqlstate = safe_errors[0] if safe_errors else ("NONE", "NONE")
            pytest.fail(f"GATE_HTTP_{step.upper()}_{response.status_code}_{safe_code}_{operation}_{sqlstate}", pytrace=False)
        return response.json()["data"]

    initial_qualification = qualification(1)
    submitted = post("/api/v1/therapist-onboarding/qualifications/submit",
                     {"expected_version": version, "qualification": initial_qualification},
                     therapist_headers, "submit")
    review_path = f"/api/v1/platform/therapist-reviews/{therapist_id}/decision"
    claimed = post(review_path, {"decision": "START_REVIEW", "expected_version": submitted["version"]},
                   reviewer_headers, "claim_initial", 200)
    correction = post(review_path,
                      {"decision": "NEEDS_CORRECTION", "reason_code": "PROFILE_INCOMPLETE",
                       "profile_fields": ["practice_summary"], "expected_version": claimed["profile"]["version"]},
                      reviewer_headers, "correct_initial", 200)
    initial_path = "/api/v1/therapist-onboarding/resubmit"
    valid_initial = {
        "expected_version": correction["profile"]["version"],
        "decision_id": correction["decision_id"],
        "profile_changes": {"practice_summary": "Synthetic revised practice"},
        "qualification": initial_qualification,
    }
    rejected(initial_path, valid_initial, "auth_priority", 401, False)
    rejected(initial_path, {**valid_initial, "expected_version": valid_initial["expected_version"]-1}, "stale")
    rejected(initial_path, {**valid_initial, "decision_id": str(Uuid7Generator().generate())}, "decision")
    rejected(initial_path, {**valid_initial, "profile_changes": {"display_name": "Synthetic unapproved"}}, "field")
    import asyncio

    from app.modules.therapist_qualification import service

    original_commit_receipt = service._commit_receipt
    for fault in ("precommit", "after_flush", "commit_not_committed"):
        before_fault = business_snapshot()
        entered = []

        async def fail_receipt(session, *, _fault=fault, _entered=entered, **kwargs):
            require(kwargs["operation"] == "RESUBMIT", "GATE_FAULT_WRONG_OPERATION")
            if _fault == "after_flush":
                await session.flush()
                _entered.append("after_flush")
                raise RuntimeError("SAFE_INJECTED_FLUSH_BOUNDARY")
            if _fault == "commit_not_committed":
                async def fail_commit():
                    _entered.append("commit")
                    raise RuntimeError("SAFE_COMMIT_NOT_SENT")

                session.commit = fail_commit
                await original_commit_receipt(session, **kwargs)
                return

            async def fail_precommit():
                _entered.append("precommit")
                raise RuntimeError("SAFE_INJECTED_PRECOMMIT")

            kwargs["precommit_check"] = fail_precommit
            await original_commit_receipt(session, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(service, "_commit_receipt", fail_receipt)
            failure_response = client.post(initial_path, json=valid_initial, headers={
                **therapist_headers, "Idempotency-Key": "gate-fault-" + fault,
                "X-Request-ID": str(Uuid7Generator().generate()),
            })
            require(failure_response.status_code == 503, "GATE_FAULT_TRANSLATION_INVALID")
        require(bool(entered), "GATE_FAULT_NOT_EXECUTED")
        require(before_fault == business_snapshot(), "GATE_FAULT_BUSINESS_ROLLBACK_INCOMPLETE")
    asyncio.run(_verify_real_service_cancellation(therapist_id, valid_initial, monkeypatch))
    committed_calls = []

    async def commit_receipt_uncertain_after_commit(session, **kwargs):
        actual_commit = session.commit

        async def commit_then_raise():
            await actual_commit()
            committed_calls.append(True)
            raise RuntimeError("SAFE_COMMIT_RESULT_LOST")

        session.commit = commit_then_raise
        await original_commit_receipt(session, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(service, "_commit_receipt", commit_receipt_uncertain_after_commit)
        resubmitted = post(initial_path, valid_initial, therapist_headers, "resubmit")
    require(len(committed_calls) == 1, "GATE_COMMIT_CONFIRMATION_RETRIED_WRITE")
    before_unknown = business_snapshot()
    asyncio.run(_verify_unknown_postimage(therapist_id, valid_initial["decision_id"], resubmitted))
    require(before_unknown == business_snapshot(), "GATE_UNKNOWN_PROBE_BUSINESS_MUTATION")
    claimed = post(review_path, {"decision": "START_REVIEW", "expected_version": resubmitted["version"]},
                   reviewer_headers, "claim_corrected", 200)
    qualification_id = str(database.fetch_value(
        f"SELECT qualification_version_id FROM public.therapist_qualification_version WHERE therapist_id='{therapist_id}'"
    ))
    qualification_correction = post(review_path,
        {"decision": "NEEDS_CORRECTION", "reason_code": "QUALIFICATION_INCOMPLETE",
         "qualification_targets": [qualification_id], "expected_version": claimed["profile"]["version"]},
        reviewer_headers, "correct_initial_qualification", 200)
    import asyncio

    asyncio.run(_verify_correction_closed_fields(therapist_id, qualification_correction["decision_id"]))
    corrected_qualification = qualification(4)
    corrected_payload = {
        "expected_version": qualification_correction["profile"]["version"],
        "decision_id": qualification_correction["decision_id"], "profile_changes": {},
        "qualification": corrected_qualification,
    }
    corrected = post(initial_path, corrected_payload, therapist_headers, "resubmit_initial_qualification")
    before_replay = business_snapshot()
    replay = post(initial_path, corrected_payload, therapist_headers, "resubmit_initial_qualification")
    require(replay == corrected and before_replay == business_snapshot(), "GATE_RESUBMIT_REPLAY_MUTATION")
    rejected(initial_path, {**corrected_payload, "profile_changes": {"practice_summary": "Synthetic changed replay"}},
             "changed_replay", idempotency_key="gate-resubmit_initial_qualification")
    claimed = post(review_path, {"decision": "START_REVIEW", "expected_version": corrected["version"]},
                   reviewer_headers, "claim_corrected_qualification", 200)
    qualification_id = str(database.fetch_value(
        "SELECT qualification_version_id FROM public.therapist_profile_revision_qualification "
        f"WHERE therapist_id='{therapist_id}' AND revision_id='{corrected['revision_id']}' AND position=1"
    ))
    approved = post(review_path,
                    {"decision": "APPROVED", "qualification_outcomes": {qualification_id: "APPROVED"},
                     "expected_version": claimed["profile"]["version"]}, reviewer_headers, "approve", 200)
    renewed = post("/api/v1/therapist-onboarding/qualifications/renew",
                   {"expected_version": approved["profile"]["version"], "predecessor_version_id": qualification_id,
                    "qualification": qualification(2)}, therapist_headers, "renew")
    renewal_id = renewed["review_item_id"]
    renewal_path = f"/api/v1/platform/therapist-renewal-reviews/{renewal_id}/decision"
    claimed = post(renewal_path, {"decision": "START_REVIEW", "expected_version": 1},
                   reviewer_headers, "claim_renewal", 200)
    renewal_qualification = str(database.fetch_value(
        f"SELECT qualification_version_id FROM public.therapist_review_item WHERE review_item_id='{renewal_id}'"
    ))
    correction = post(renewal_path,
                       {"decision": "NEEDS_CORRECTION", "reason_code": "QUALIFICATION_INCOMPLETE",
                        "qualification_targets": [renewal_qualification],
                        "expected_version": claimed["review_item"]["version"]},
                       reviewer_headers, "correct_renewal", 200)
    import asyncio

    async def legacy_lock_still_denied():
        from uuid import UUID

        import asyncpg

        writer = await _connect_role("KG_TEST_THERAPIST_ONBOARDING_WRITER_DATABASE_URL")
        try:
            try:
                await writer.fetch(
                    "SELECT * FROM public.therapist_review_item WHERE review_item_id=$1 FOR UPDATE",
                    UUID(renewal_id),
                )
            except asyncpg.InsufficientPrivilegeError:
                return
            pytest.fail("GATE_LEGACY_RENEWAL_LOCK_UNEXPECTEDLY_GRANTED", pytrace=False)
        finally:
            await writer.close()

    asyncio.run(legacy_lock_still_denied())
    asyncio.run(_verify_correction_closed_fields(therapist_id, correction["decision_id"], renewal_id))
    post(f"/api/v1/therapist-onboarding/qualification-renewals/{renewal_id}/resubmit",
         {"expected_review_version": correction["review_item"]["version"],
          "decision_id": correction["decision_id"], "qualification": qualification(3)},
         therapist_headers, "renewal_resubmit")


async def _verify_unknown_postimage(therapist_id, decision_id, response):
    from uuid import UUID

    from fastapi import HTTPException
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core import database
    from app.modules.therapist_qualification import service

    fixture = await _connect_role("KG_TEST_MIGRATION_DATABASE_URL")
    engine = None
    original = None
    try:
        original = await fixture.fetchrow(
            "SELECT user_id,practice_summary FROM public.therapist_profile WHERE therapist_id=$1", UUID(therapist_id)
        )
        engine = create_async_engine(database._slice2_url(database.get_settings(), "onboarding_writer"),
                                     pool_size=1, max_overflow=0, pool_timeout=2)
        factory = database.create_session_factory(engine)
        arguments = {"scope": f"therapist-user:{original['user_id']}:decision:{decision_id}",
                     "operation": "RESUBMIT", "key": "gate-resubmit", "response": response}
        async with factory() as reader:
            expected = await service._mutation_postimage_snapshot(reader, **arguments)
            await reader.rollback()
        # Controlled fixture drift after a frozen real postimage; no runtime base-table grant.
        await fixture.execute(
            "UPDATE public.therapist_profile SET practice_summary=$1 WHERE therapist_id=$2",
            "Synthetic concurrent postimage drift", UUID(therapist_id),
        )
        confirmations = []
        attempts = []
        async with factory() as writer:
            await writer.execute(text("SELECT 1"))

            async def uncertain_commit():
                attempts.append(True)
                raise RuntimeError("SAFE_COMMIT_UNCERTAIN")

            async def confirm():
                require(engine.pool.checkedout() == 0, "GATE_CONFIRM_BEFORE_WRITER_RELEASE")
                async with factory() as independent:
                    outcome = await service._confirm_mutation_outcome(independent, expected=expected, **arguments)
                    confirmations.append(outcome)
                    await independent.rollback()
                    return outcome

            writer.commit = uncertain_commit
            try:
                await service._commit(writer, confirm=confirm)
            except HTTPException as exc:
                require(exc.status_code == 503 and exc.detail == "COMMIT_OUTCOME_UNKNOWN", "GATE_UNKNOWN_NOT_SAFE_503")
            else:
                pytest.fail("GATE_UNKNOWN_REPORTED_SUCCESS", pytrace=False)
        require(confirmations == [service.UNKNOWN] and len(attempts) == 1, "GATE_UNKNOWN_RETRY_OR_CLASSIFICATION_INVALID")
        require(engine.pool.checkedout() == 0, "GATE_UNKNOWN_POOL_NOT_RETURNED")
    finally:
        try:
            if original is not None:
                await fixture.execute(
                    "UPDATE public.therapist_profile SET practice_summary=$1 WHERE therapist_id=$2",
                    original["practice_summary"], UUID(therapist_id),
                )
        finally:
            try:
                if engine is not None:
                    await engine.dispose()
            finally:
                await fixture.close()


async def _verify_real_service_cancellation(therapist_id, payload, monkeypatch):
    import asyncio
    from uuid import UUID, uuid4

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core import database
    from app.core.security import CurrentUser
    from app.modules.therapist_qualification import service
    from app.modules.therapist_qualification.schemas import TherapistResubmit

    observer = await _connect_role("KG_TEST_MIGRATION_DATABASE_URL")
    engine = None
    held_session = None
    try:
        profile = await observer.fetchrow(
            "SELECT user_id,tenant_id,version FROM public.therapist_profile WHERE therapist_id=$1", UUID(therapist_id)
        )
        public_id = await observer.fetchval(
            "SELECT tenant_public_id FROM public.institution_application WHERE tenant_internal_id=$1 AND status='APPROVED'",
            profile["tenant_id"],
        )
        tables = await observer.fetch(
            "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public' "
            "AND starts_with(tablename,'therapist_') ORDER BY tablename"
        )

        async def snapshot():
            values = []
            for row in tables:
                table = row["tablename"].replace('"', '""')
                values.append(await observer.fetch(
                    'SELECT row_to_json(t)::text FROM public."' + table + '" t ORDER BY row_to_json(t)::text'
                ))
            return values

        before = await snapshot()
        settings = database.get_settings()
        engine = create_async_engine(database._slice2_url(settings, "onboarding_writer"),
                                     pool_size=1, max_overflow=0, pool_timeout=2)
        factory = database.create_session_factory(engine)
        primary = asyncio.CancelledError()
        entered = []
        lock_key = secrets.randbelow(1000000000) + 1900000000

        async def operation():
            nonlocal held_session
            async with database._slice2_session("onboarding_writer") as session:
                held_session = session
                role = (await session.execute(text("SELECT current_user"))).scalar_one()
                require(role == settings.therapist_onboarding_writer_role, "GATE_CANCEL_WRITER_ROLE_INVALID")
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})

                async def cancel_precommit():
                    entered.append(True)
                    pending = (await session.execute(text(
                        "SELECT version FROM public.therapist_profile WHERE therapist_id=:subject"
                    ), {"subject": UUID(therapist_id)})).scalar_one()
                    require(pending == profile["version"]+1, "GATE_CANCEL_PENDING_MUTATION_NOT_REACHED")
                    require(not await observer.fetchval("SELECT pg_try_advisory_xact_lock($1)", lock_key),
                            "GATE_CANCEL_LOCK_NOT_HELD")
                    raise primary

                await service.resubmit(
                    session, CurrentUser(profile["user_id"], "therapist", profile["tenant_id"]),
                    TherapistResubmit.model_validate(payload), str(uuid4()), "gate-real-service-cancel",
                    tenant_public_id=str(public_id), precommit_check=cancel_precommit,
                )
                pytest.fail("GATE_CANCEL_SERVICE_RETURNED_SUCCESS", pytrace=False)

        with monkeypatch.context() as patch:
            patch.setattr(database, "get_slice2_session_factory", lambda kind: factory)
            task = asyncio.create_task(operation())
            observed = None
            try:
                await task
            except asyncio.CancelledError as exc:
                observed = exc
            require(observed is primary and task.done() and len(entered) == 1, "GATE_SERVICE_CANCEL_NOT_PRIMARY")
        # All verdicts precede test-only rescue/disposal and use an independent connection.
        require(not held_session.in_transaction() and engine.pool.checkedout() == 0, "GATE_CANCEL_POOL_NOT_RETURNED")
        require(await observer.fetchval("SELECT pg_try_advisory_xact_lock($1)", lock_key), "GATE_CANCEL_LOCK_NOT_RELEASED")
        require(before == await snapshot(), "GATE_CANCEL_BUSINESS_ROLLBACK_INCOMPLETE")
    finally:
        try:
            if held_session is not None:
                if held_session.in_transaction():
                    await held_session.invalidate()
                await held_session.close()
        finally:
            try:
                if engine is not None:
                    await engine.dispose()
            finally:
                await observer.close()


async def _verify_correction_closed_fields(therapist_id, decision_id, renewal_id=None):
    import json
    from uuid import UUID, uuid4

    import asyncpg

    # Fixture role only adjusts its synthetic decision; the target function always uses OW.
    fixture = await _connect_role("KG_TEST_MIGRATION_DATABASE_URL")
    writer = None
    decision_uuid = UUID(decision_id)
    original = None
    try:
        writer = await _connect_role("KG_TEST_THERAPIST_ONBOARDING_WRITER_DATABASE_URL")
        profile = await fixture.fetchrow(
            "SELECT user_id,tenant_id FROM public.therapist_profile WHERE therapist_id=$1", UUID(therapist_id)
        )
        original = await fixture.fetchval(
            "SELECT correction_fields::text FROM public.therapist_review_decision WHERE decision_id=$1", decision_uuid
        )
        args = [profile["user_id"], profile["tenant_id"], UUID(therapist_id), decision_uuid,
                UUID(renewal_id) if renewal_id else None]
        query = "SELECT * FROM public.slice2_therapist_correction_authority_v1($1,$2,$3,$4,$5)"
        expected_columns = {
            "decision_therapist_id", "decision_kind", "decision_revision_id", "decision_review_item_id",
            "allow_real_name", "allow_display_name", "allow_practice_summary", "allow_service_tags",
            "qualification_target", "item_therapist_id", "item_review_kind", "item_status", "item_version",
            "item_qualification_version_id",
        }
        valid = await writer.fetch(query, *args)
        require(len(valid) == 1 and set(valid[0].keys()) == expected_columns, "GATE_CORRECTION_RETURN_NOT_CLOSED")
        item_uuid = valid[0]["decision_review_item_id"]
        old_decided_at = await fixture.fetchval(
            "SELECT decided_at FROM public.therapist_review_item WHERE review_item_id=$1", item_uuid
        )
        try:
            try:
                await fixture.execute(
                    "UPDATE public.therapist_review_item SET status='UNDER_REVIEW',decided_at=NULL WHERE review_item_id=$1",
                    item_uuid,
                )
            except asyncpg.CheckViolationError as exc:
                require(exc.message == "therapist review decision state is invalid",
                        "GATE_NONDECIDED_FIXTURE_REJECTION_UNEXPECTED")
            else:
                require(not await writer.fetch(query, *args), "GATE_CORRECTION_NONDECIDED_ACCEPTED")
        finally:
            await fixture.execute(
                "UPDATE public.therapist_review_item SET status='DECIDED',decided_at=$1 WHERE review_item_id=$2",
                old_decided_at, item_uuid,
            )
        original_revision = valid[0]["decision_revision_id"]
        other_revision = await fixture.fetchval(
            "SELECT revision_id FROM public.therapist_profile_revision "
            "WHERE therapist_id=$1 AND revision_id<>$2 ORDER BY revision_no LIMIT 1", UUID(therapist_id), original_revision,
        )
        require(other_revision is not None, "GATE_REVISION_DRIFT_FIXTURE_MISSING")
        try:
            await fixture.execute("UPDATE public.therapist_review_decision SET revision_id=$1 WHERE decision_id=$2",
                                  other_revision, decision_uuid)
            require(not await writer.fetch(query, *args), "GATE_CORRECTION_REVISION_DRIFT_ACCEPTED")
        finally:
            await fixture.execute("UPDATE public.therapist_review_decision SET revision_id=$1 WHERE decision_id=$2",
                                  original_revision, decision_uuid)
        old_kind = valid[0]["item_review_kind"]
        old_qualification = valid[0]["item_qualification_version_id"]
        try:
            await fixture.execute(
                "UPDATE public.therapist_review_item SET review_kind=$1,qualification_version_id=$2 WHERE review_item_id=$3",
                "INITIAL" if old_kind == "RENEWAL" else "RENEWAL",
                None if old_kind == "RENEWAL" else valid[0]["qualification_target"], item_uuid,
            )
            require(not await writer.fetch(query, *args), "GATE_CORRECTION_WRONG_KIND_ACCEPTED")
        finally:
            await fixture.execute(
                "UPDATE public.therapist_review_item SET review_kind=$1,qualification_version_id=$2 WHERE review_item_id=$3",
                old_kind, old_qualification, item_uuid,
            )
        decision_before = await fixture.fetchrow(
            "SELECT decision,reason_code,qualification_outcomes::text AS outcomes "
            "FROM public.therapist_review_decision WHERE decision_id=$1", decision_uuid,
        )
        approved_outcomes = {str(valid[0]["qualification_target"]): "APPROVED"}
        if old_kind == "RENEWAL":
            previous_qualification = await fixture.fetchval(
                "SELECT previous_version_id FROM public.therapist_qualification_version WHERE qualification_version_id=$1",
                valid[0]["qualification_target"],
            )
            require(previous_qualification is not None, "GATE_RENEWAL_PREDECESSOR_FIXTURE_MISSING")
            approved_outcomes[str(previous_qualification)] = "SUPERSEDED"
        try:
            await fixture.execute(
                "UPDATE public.therapist_review_decision SET decision='APPROVED',reason_code=NULL,"
                "correction_fields=NULL,qualification_outcomes=$1::jsonb WHERE decision_id=$2",
                json.dumps(approved_outcomes), decision_uuid,
            )
            require(not await writer.fetch(query, *args), "GATE_CORRECTION_NONCORRECTION_DECISION_ACCEPTED")
        finally:
            await fixture.execute(
                "UPDATE public.therapist_review_decision SET decision=$1,reason_code=$2,"
                "correction_fields=$3::jsonb,qualification_outcomes=$4::jsonb WHERE decision_id=$5",
                decision_before["decision"], decision_before["reason_code"], original, decision_before["outcomes"], decision_uuid,
            )
        for position in range(4):
            wrong = list(args)
            wrong[position] = args[position]+10000 if position < 2 else uuid4()
            require(not await writer.fetch(query, *wrong), "GATE_CORRECTION_ASSOCIATION_ACCEPTED")
        wrong = list(args)
        wrong[4] = uuid4()
        require(not await writer.fetch(query, *wrong), "GATE_CORRECTION_ITEM_ASSOCIATION_ACCEPTED")
        for position in range(4):
            invalid = list(args)
            invalid[position] = None
            try:
                await writer.fetch(query, *invalid)
            except asyncpg.PostgresError as exc:
                require(exc.sqlstate == "22023", "GATE_CORRECTION_NULL_REJECTION_INVALID")
            else:
                pytest.fail("GATE_CORRECTION_NULL_ACCEPTED", pytrace=False)
        malformed = (
            None, {}, [], [None], [1], [["practice_summary"]], [{"field": "practice_summary"}],
            ["practice_summary", "practice_summary"], ["z" * 51], ["not_allowed"],
            ["qualification:" + str(uuid4())],
        )
        for fields in malformed:
            try:
                await fixture.execute(
                    "UPDATE public.therapist_review_decision SET correction_fields=$1::jsonb WHERE decision_id=$2",
                    json.dumps(fields) if fields is not None else None, decision_uuid,
                )
            except asyncpg.PostgresError as exc:
                require(exc.sqlstate in {"23514", "23502"}, "GATE_FIXTURE_FIELD_REJECTION_UNEXPECTED")
            else:
                require(not await writer.fetch(query, *args), "GATE_CORRECTION_MALFORMED_FIELD_ACCEPTED")
            finally:
                await fixture.execute(
                    "UPDATE public.therapist_review_decision SET correction_fields=$1::jsonb WHERE decision_id=$2",
                    original, decision_uuid,
                )
        require(len(await writer.fetch(query, *args)) == 1, "GATE_CORRECTION_FIXTURE_NOT_RESTORED")
        # The service owns the profile lock before entering the closed function.
        # A second real connection must be blocked until that writer transaction ends.
        await writer.execute("BEGIN")
        locked_rows = [
            ("therapist_profile", "therapist_id", UUID(therapist_id)),
            ("therapist_review_decision", "decision_id", decision_uuid),
        ]
        if renewal_id:
            locked_rows.append(("therapist_review_item", "review_item_id", UUID(renewal_id)))
        try:
            await writer.fetchval(
                "SELECT therapist_id FROM public.therapist_profile WHERE therapist_id=$1 FOR UPDATE",
                UUID(therapist_id),
            )
            require(len(await writer.fetch(query, *args)) == 1, "GATE_CORRECTION_LOCKED_READ_FAILED")
            for table, key, value in locked_rows:
                await fixture.execute("BEGIN")
                try:
                    await fixture.execute("SET LOCAL lock_timeout='200ms'")
                    try:
                        await fixture.execute(f"UPDATE public.{table} SET {key}={key} WHERE {key}=$1", value)
                    except asyncpg.PostgresError as exc:
                        require(exc.sqlstate == "55P03", "GATE_CORRECTION_LOCK_CONFLICT_INVALID")
                    else:
                        pytest.fail("GATE_CORRECTION_LOCK_NOT_HELD", pytrace=False)
                finally:
                    await fixture.execute("ROLLBACK")
        finally:
            await writer.execute("ROLLBACK")
        for table, key, value in locked_rows:
            await fixture.execute("BEGIN")
            try:
                await fixture.execute("SET LOCAL lock_timeout='200ms'")
                await fixture.execute(f"UPDATE public.{table} SET {key}={key} WHERE {key}=$1", value)
            finally:
                await fixture.execute("ROLLBACK")
    finally:
        try:
            if original is not None:
                await fixture.execute(
                    "UPDATE public.therapist_review_decision SET correction_fields=$1::jsonb WHERE decision_id=$2",
                    original, decision_uuid,
                )
        finally:
            try:
                if writer is not None:
                    await writer.close()
            finally:
                await fixture.close()


async def _verify_authority_locks(tenant_id, expected_public_id):
    import asyncpg
    application = await _connect_role("KG_TEST_DATABASE_URL")
    contender = None
    try:
        contender = await _connect_role("KG_TEST_MIGRATION_DATABASE_URL")
        await application.execute("BEGIN")
        value = await application.fetchval(
            "SELECT public.slice2_institution_identity_authority_v1($1)", tenant_id
        )
        require(str(value) == str(expected_public_id), "GATE_LOCK_IDENTITY_MISMATCH")
        for table, predicate in (
            ("public.institution_application", "tenant_internal_id=$1"),
            ("public.tenant", "id=$1"),
        ):
            await contender.execute("BEGIN")
            try:
                await contender.execute("SET LOCAL lock_timeout='200ms'")
                try:
                    await contender.execute(
                        f"UPDATE {table} SET status=status WHERE {predicate}", tenant_id
                    )
                except asyncpg.LockNotAvailableError:
                    pass
                else:
                    pytest.fail("GATE_AUTHORITY_LOCK_NOT_HELD", pytrace=False)
            finally:
                await contender.execute("ROLLBACK")
        await application.execute("ROLLBACK")
        await contender.execute("BEGIN")
        try:
            await contender.execute("SET LOCAL lock_timeout='200ms'")
            await contender.execute("UPDATE public.tenant SET status=status WHERE id=$1", tenant_id)
            await contender.execute(
                "UPDATE public.institution_application SET status=status WHERE tenant_internal_id=$1", tenant_id
            )
        finally:
            await contender.execute("ROLLBACK")
    finally:
        try:
            await application.close()
        finally:
            if contender is not None:
                await contender.close()


async def _connect_role(variable):
    import os

    import asyncpg
    from sqlalchemy.engine import make_url
    url = make_url(os.environ[variable])
    expected = make_url(os.environ["KG_TEST_DATABASE_URL"])
    require((url.host, url.port, url.database) == (expected.host, expected.port, expected.database)
            and url.host in {"127.0.0.1", "localhost"}, "GATE_DATABASE_SCOPE_INVALID")
    return await asyncpg.connect(url.render_as_string(hide_password=False).replace("postgresql+asyncpg", "postgresql"))


def test_G09_真实角色函数拒绝与Application底表零扩权(pg_database):
    import asyncio
    import os

    import asyncpg
    signature = "public.slice2_institution_identity_authority_v1(bigint)"
    require(pg_database.fetch_value(
        "SELECT NOT has_function_privilege('public','" + signature + "','EXECUTE')"
    ), "GATE_PUBLIC_EXECUTE_EXPOSED")
    correction_signature = "public.slice2_therapist_correction_authority_v1(bigint,bigint,uuid,uuid,uuid)"
    require(pg_database.fetch_value(
        "SELECT NOT has_function_privilege('public','" + correction_signature + "','EXECUTE')"
    ), "GATE_CORRECTION_PUBLIC_EXECUTE_EXPOSED")

    async def verify():
        application = await _connect_role("KG_TEST_DATABASE_URL")
        try:
            for sql in (
                "SELECT * FROM public.institution_application WHERE false",
                "UPDATE public.institution_application SET version=version WHERE false",
                "DELETE FROM public.institution_application WHERE false",
            ):
                try:
                    await application.execute(sql)
                except asyncpg.InsufficientPrivilegeError:
                    pass
                else:
                    pytest.fail("GATE_APPLICATION_BASE_TABLE_EXPOSED", pytrace=False)
            for value in (None, 0, -1):
                try:
                    await application.fetchval("SELECT public.slice2_institution_identity_authority_v1($1)", value)
                except asyncpg.PostgresError as exc:
                    require(exc.sqlstate == "22023", "GATE_INPUT_REJECTION_INVALID")
                else:
                    pytest.fail("GATE_INPUT_ACCEPTED", pytrace=False)
            require(await application.fetchval(
                "SELECT public.slice2_institution_identity_authority_v1($1)", 9223372036854775806
            ) is None, "GATE_MISSING_AUTHORITY_ACCEPTED")
        finally:
            await application.close()
        for variable in (
            "KG_TEST_READONLY_DATABASE_URL",
            "KG_TEST_VERIFICATION_WRITER_DATABASE_URL",
            "KG_TEST_DELIVERY_WORKER_DATABASE_URL",
            "KG_TEST_INSTITUTION_ONBOARDING_READER_DATABASE_URL",
            "KG_TEST_THERAPIST_ONBOARDING_WRITER_DATABASE_URL",
            "KG_TEST_THERAPIST_REVIEW_WRITER_DATABASE_URL",
            "KG_TEST_THERAPIST_READER_DATABASE_URL",
            "KG_TEST_THERAPIST_READINESS_WORKER_DATABASE_URL",
        ):
            connection = await _connect_role(variable)
            try:
                for sql in (
                    "SELECT public.slice2_institution_identity_authority_v1(1)",
                    'SET ROLE "' + os.environ["KG_TEST_APPLICATION_ROLE"] + '"',
                ):
                    try:
                        await connection.execute(sql)
                    except asyncpg.InsufficientPrivilegeError:
                        pass
                    else:
                        pytest.fail("GATE_UNRELATED_ROLE_ACCEPTED", pytrace=False)
            finally:
                await connection.close()
        for variable in (
            "KG_TEST_DATABASE_URL",
            "KG_TEST_READONLY_DATABASE_URL",
            "KG_TEST_VERIFICATION_WRITER_DATABASE_URL",
            "KG_TEST_DELIVERY_WORKER_DATABASE_URL",
            "KG_TEST_INSTITUTION_ONBOARDING_READER_DATABASE_URL",
            "KG_TEST_THERAPIST_REVIEW_WRITER_DATABASE_URL",
            "KG_TEST_THERAPIST_READER_DATABASE_URL",
            "KG_TEST_THERAPIST_READINESS_WORKER_DATABASE_URL",
        ):
            connection = await _connect_role(variable)
            try:
                try:
                    await connection.execute(
                        "SELECT * FROM public.slice2_therapist_correction_authority_v1(NULL,NULL,NULL,NULL,NULL)"
                    )
                except asyncpg.InsufficientPrivilegeError:
                    pass
                else:
                    pytest.fail("GATE_CORRECTION_UNRELATED_ROLE_ACCEPTED", pytrace=False)
            finally:
                await connection.close()
    asyncio.run(verify())


def test_G10_0039往返只改变闭合函数(pg_database):
    from alembic import command

    from tests.integration.conftest import _build_alembic_config, _get_test_database_url
    signatures = (
        "public.slice2_institution_identity_authority_v1(bigint)",
        "public.slice2_therapist_correction_authority_v1(bigint,bigint,uuid,uuid,uuid)",
    )
    approved_filter = (
        "NOT EXISTS (SELECT 1 FROM (VALUES "
        "('public','slice2_institution_identity_authority_v1','bigint'),"
        "('public','slice2_therapist_correction_authority_v1','bigint, bigint, uuid, uuid, uuid')"
        ") approved(schema_name,function_name,argument_types) "
        "WHERE approved.schema_name=n.nspname AND approved.function_name=p.proname "
        "AND approved.argument_types=pg_catalog.oidvectortypes(p.proargtypes))"
    )
    # Virtual catalog rows exercise the exact production-snapshot predicate without DDL.
    retained = pg_database.fetch_column(
        "SELECT marker FROM (VALUES "
        "('slice2_institution_identity_authority_v1','20'::oidvector,0),"
        "('slice2_institution_identity_authority_v1','20 20'::oidvector,1),"
        "('slice2_therapist_correction_authority_v1','20 20 2950 2950 2950'::oidvector,0),"
        "('slice2_therapist_correction_authority_v1','20'::oidvector,2)"
        ") p(proname,proargtypes,marker) CROSS JOIN (VALUES ('public')) n(nspname) "
        "WHERE " + approved_filter + " ORDER BY marker"
    )
    require(retained == [1, 2], "GATE_OVERLOAD_FILTER_INCOMPLETE")

    def snapshot():
        # Full synthetic rows stay in memory; only a stable failure code is emitted.
        tables = pg_database.fetch_rows(
            "SELECT n.nspname,c.relname FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname IN ('public','identity') AND c.relkind IN ('r','p') "
            "AND c.relname <> 'alembic_version' ORDER BY 1,2"
        )
        business = []
        require(bool(tables), "GATE_BUSINESS_SNAPSHOT_EMPTY")
        for table_row in tables:
            schema, table = table_row["nspname"], table_row["relname"]
            qualified = '"' + schema.replace('"', '""') + '"."' + table.replace('"', '""') + '"'
            business.append((schema, table, pg_database.fetch_column(
                "SELECT row_to_json(t)::text FROM " + qualified + " t ORDER BY row_to_json(t)::text"
            )))
        catalogs = tuple(pg_database.fetch_rows(sql) for sql in (
            "SELECT n.nspname,c.relname,c.relkind,c.relowner,c.relacl::text,"
            "c.relrowsecurity,c.relforcerowsecurity "
            "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname IN ('public','identity') ORDER BY 1,2",
            "SELECT n.nspname,c.relname,a.attnum,a.attname,a.atttypid,a.attnotnull,a.attacl::text "
            "FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid "
            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname IN ('public','identity') ORDER BY 1,2,3",
            "SELECT nspname,nspowner,nspacl::text FROM pg_catalog.pg_namespace "
            "WHERE nspname IN ('public','identity') ORDER BY 1",
            "SELECT rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,"
            "rolreplication,rolbypassrls,rolconnlimit,rolvaliduntil,rolconfig::text "
            "FROM pg_catalog.pg_roles ORDER BY rolname",
            "SELECT roleid,member,grantor,admin_option,inherit_option,set_option "
            "FROM pg_catalog.pg_auth_members ORDER BY 1,2,3",
            "SELECT n.nspname,c.relname,k.conname,pg_get_constraintdef(k.oid) "
            "FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid=k.conrelid "
            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname IN ('public','identity') ORDER BY 1,2,3",
            "SELECT n.nspname,p.proname,pg_get_function_identity_arguments(p.oid),"
            "p.proowner,p.proacl::text,pg_get_functiondef(p.oid) FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname IN ('public','identity') AND p.prokind IN ('f','p') "
            "AND " + approved_filter + " ORDER BY 1,2,3",
            "SELECT n.nspname,c.relname,c.relkind,pg_get_viewdef(c.oid,false) "
            "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname IN ('public','identity') AND c.relkind IN ('v','m') ORDER BY 1,2",
            "SELECT n.nspname,c.relname,ic.relname AS index_name,pg_get_indexdef(i.indexrelid),"
            "i.indisvalid,i.indisready,i.indisreplident "
            "FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class c ON c.oid=i.indrelid "
            "JOIN pg_catalog.pg_class ic ON ic.oid=i.indexrelid "
            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname IN ('public','identity') ORDER BY 1,2,3",
            "SELECT n.nspname,c.relname,t.tgname,t.tgenabled,pg_get_triggerdef(t.oid,false) "
            "FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid "
            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname IN ('public','identity') ORDER BY 1,2,3",
            "SELECT n.nspname,c.relname,a.attname,a.attgenerated,a.attidentity,"
            "pg_get_expr(d.adbin,d.adrelid,false) FROM pg_catalog.pg_attribute a "
            "JOIN pg_catalog.pg_class c ON c.oid=a.attrelid "
            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum "
            "WHERE n.nspname IN ('public','identity') AND a.attnum>0 AND NOT a.attisdropped ORDER BY 1,2,3",
            "SELECT n.nspname,c.relname,p.polname,p.polcmd,p.polpermissive,p.polroles::text,"
            "pg_get_expr(p.polqual,p.polrelid,false) AS policy_using,"
            "pg_get_expr(p.polwithcheck,p.polrelid,false) AS policy_with_check "
            "FROM pg_catalog.pg_policy p JOIN pg_catalog.pg_class c ON c.oid=p.polrelid "
            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname IN ('public','identity') ORDER BY 1,2,3",
            "SELECT n.nspname,d.defaclrole,d.defaclobjtype,d.defaclacl::text "
            "FROM pg_catalog.pg_default_acl d LEFT JOIN pg_catalog.pg_namespace n ON n.oid=d.defaclnamespace "
            "WHERE n.nspname IN ('public','identity') OR d.defaclnamespace=0 ORDER BY 1,2,3",
        ))
        require(all(catalogs[index] for index in (0, 1, 2, 3, 5, 6, 8, 10)),
                "GATE_REQUIRED_CATALOG_SNAPSHOT_EMPTY")
        total_functions = pg_database.fetch_value(
            "SELECT count(*) FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname IN ('public','identity') AND p.prokind IN ('f','p')"
        )
        approved_count = sum(bool(rows) for rows in approved_functions())
        require(len(catalogs[6]) + approved_count == total_functions, "GATE_FUNCTION_SNAPSHOT_NOT_EXHAUSTIVE")
        return business, catalogs

    def approved_functions():
        return tuple(pg_database.fetch_rows(
            "SELECT pg_get_functiondef(p.oid),p.proowner,p.proacl::text "
            "FROM pg_catalog.pg_proc p WHERE p.oid=to_regprocedure('" + signature + "')"
        ) for signature in signatures)

    config = _build_alembic_config(_get_test_database_url())
    require(pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260911_0042",
            "GATE_CURRENT_HEAD_INVALID")
    try:
        command.downgrade(config, "20260906_0039")
        require(pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260906_0039",
                "GATE_0039_BASELINE_HEAD_INVALID")
        before = snapshot()
        # In-memory reverse probes: every selected field participates in equality.
        # Empty optional categories also remain represented, so additions are detected.
        for category_index, rows in enumerate(before[1]):
            original_length = len(rows)
            changed_categories = list(before[1])
            changed_categories[category_index] = [*rows, {"safe_probe": True}]
            require(before[1] != tuple(changed_categories), "GATE_CATALOG_CATEGORY_COMPARISON_INACTIVE")
            require(len(rows) == original_length, "GATE_PROBE_MUTATED_ORIGINAL")
            for row_index, row in enumerate(rows):
                for key in row:
                    original_value = row[key]
                    changed = list(rows)
                    changed[row_index] = dict(row)
                    changed[row_index][key] = object()
                    require(rows != changed, "GATE_CATALOG_FIELD_COMPARISON_INACTIVE")
                    require(row[key] is original_value, "GATE_PROBE_MUTATED_ORIGINAL")
        # A legal empty policy catalog still needs both expressions independently protected.
        policy_probe = {"policy_using": None, "policy_with_check": None}
        for field in policy_probe:
            changed_policy = dict(policy_probe)
            changed_policy[field] = "SAFE_EXPRESSION_PROBE"
            require(policy_probe != changed_policy and all(value is None for value in policy_probe.values()),
                    "GATE_POLICY_FIELD_PROBE_INVALID")
        function_before = approved_functions()
        command.downgrade(config, "20260904_0038")
        for signature in signatures:
            require(pg_database.fetch_value("SELECT to_regprocedure('" + signature + "')") is None,
                    "GATE_DOWNGRADE_FUNCTION_REMAINS")
        require(before == snapshot(), "GATE_DOWNGRADE_UNAPPROVED_MUTATION")
        require(pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260904_0038",
                "GATE_DOWNGRADE_HEAD_INVALID")
        command.upgrade(config, "20260906_0039")
        require(pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260906_0039",
                "GATE_REUPGRADE_HEAD_INVALID")
        for signature in signatures:
            require(pg_database.fetch_value("SELECT to_regprocedure('" + signature + "') IS NOT NULL"),
                    "GATE_REUPGRADE_FUNCTION_MISSING")
        require(before == snapshot(), "GATE_MIGRATION_BUSINESS_OR_AUTHORITY_MUTATION")
        require(function_before == approved_functions(), "GATE_REUPGRADE_FUNCTION_CONTRACT_DRIFT")
    finally:
        command.upgrade(config, "head")
        require(pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260911_0042",
                "GATE_LATEST_HEAD_RESTORE_FAILED")
