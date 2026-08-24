import asyncio
from datetime import datetime, timezone
import inspect
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest


THERAPIST_ID = UUID("0198b963-38f0-7d7d-8000-000000000081")
ASSIGNMENT_ID = UUID("0198b963-38f0-7d7d-8000-000000000082")
ENROLLMENT_ID = UUID("0198b963-38f0-7d7d-8000-000000000083")
TENANT_PUBLIC_ID = UUID("0198b963-38f0-7d7d-8000-000000000084")
MEMBER_ID = UUID("0198b963-38f0-7d7d-8000-000000000085")


class _Result:
    def __init__(self, rows):
        self._rows = tuple(rows)

    def mappings(self):
        return self

    def one_or_none(self):
        return self._rows[0] if len(self._rows) == 1 else None

    def __iter__(self):
        return iter(self._rows)


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _PreimageSession:
    def __init__(self, preimage, *, with_plan=True):
        self.preimage = preimage
        self.info = {"slice3-mutation-plan": {}} if with_plan else {}
        self.calls = []

    async def execute(self, statement, parameters=None):
        self.calls.append((str(statement), parameters))
        if len(self.calls) == 1:
            return _ScalarResult(self.preimage)
        return _ScalarResult(None)


class _UpdateResult:
    rowcount = 1


class _TherapistPlanSession:
    def __init__(self):
        self.info = {"slice3-mutation-plan": {}}
        self.calls = []
        self.profile = {
            "therapist_id": THERAPIST_ID,
            "tenant_id": 81,
            "status": "APPROVED_ACTIVE",
            "service_tags": ["GLUCOSE_METABOLISM"],
            "capacity_limit": 30,
            "active_case_count": 0,
            "current_qualification_version_id": ASSIGNMENT_ID,
            "qualification_valid_until": datetime(2026, 9, 24).date(),
            "version": 1,
        }

    async def execute(self, statement, parameters=None):
        self.calls.append((str(statement), parameters))
        if str(statement).startswith("UPDATE public.therapist_profile"):
            return _UpdateResult()
        return _Result((self.profile,))


class _TherapistAuthority:
    def __init__(
        self,
        *,
        status="APPROVED_ACTIVE",
        tenant_id=81,
        qualification_valid_until=None,
    ):
        self.status = status
        self.tenant_id = tenant_id
        self.qualification_valid_until = (
            qualification_valid_until
            if qualification_valid_until is not None
            else datetime(2026, 9, 24).date()
        )

    async def execute(self, statement, parameters=None):
        del statement, parameters
        return _Result(({
            "therapist_id": THERAPIST_ID,
            "tenant_id": self.tenant_id,
            "status": self.status,
            "current_qualification_version_id": ASSIGNMENT_ID,
            "qualification_valid_until": self.qualification_valid_until,
            "role": "therapist",
            "user_status": "active",
            "user_tenant_id": self.tenant_id,
        },))


class _AssignmentReader:
    def __init__(self, rows=None):
        self.rows = rows
        self.statement = None

    async def execute(self, statement, parameters=None):
        self.statement = str(statement)
        del parameters
        default = ({
            "assignment_id": ASSIGNMENT_ID,
            "enrollment_id": ENROLLMENT_ID,
            "tenant_public_id": TENANT_PUBLIC_ID,
            "subject_member_id": MEMBER_ID,
            "therapist_id": THERAPIST_ID,
            "status": "PENDING_ACCEPTANCE",
            "service_scope_tags": ["GLUCOSE_METABOLISM"],
            "reason_code": None,
            "service_case_id": None,
            "created_at": datetime(2026, 8, 24, tzinfo=timezone.utc),
            "decided_at": None,
            "version": 1,
            "subject_masked_label": "会员****0085",
            "therapist_display_name": "合同测试健管师",
        },)
        return _Result(default if self.rows is None else self.rows)


class _UnavailableAssignmentReader:
    async def execute(self, statement, parameters=None):
        del statement, parameters
        raise RuntimeError("synthetic repository outage")


class _RecordingTherapistAuthority(_TherapistAuthority):
    def __init__(self):
        super().__init__()
        self.sql = None

    async def execute(self, statement, parameters=None):
        self.sql = str(statement)
        return await super().execute(statement, parameters)


def _client(*, reader=None, authority=None) -> TestClient:
    from app.core.database import get_db_session, get_member_enrollment_reader_session
    from app.core.security import CurrentUser, get_current_user_from_jwt
    from app.modules.member_enrollment.api import therapist_router

    async def current_user():
        return CurrentUser(id=81, role="therapist", tenant_id=81)

    async def authority_session():
        yield authority or _TherapistAuthority()

    async def reader_session():
        yield reader or _AssignmentReader()

    app = FastAPI()
    app.include_router(therapist_router)
    app.dependency_overrides[get_current_user_from_jwt] = current_user
    app.dependency_overrides[get_db_session] = authority_session
    app.dependency_overrides[get_member_enrollment_reader_session] = reader_session
    return TestClient(app)


def _unauthenticated_client() -> TestClient:
    from app.modules.member_enrollment.api import therapist_router

    app = FastAPI()
    app.include_router(therapist_router)
    return TestClient(app)


def test_合法当前健管师查询本人待接受分配严格满足列表DTO() -> None:
    response = _client().get(
        "/api/v1/therapist/primary-assignments",
        params={"status": "PENDING_ACCEPTANCE"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [{
            "assignment_id": str(ASSIGNMENT_ID),
            "enrollment_id": str(ENROLLMENT_ID),
            "tenant_id": str(TENANT_PUBLIC_ID),
            "subject_member_id": str(MEMBER_ID),
            "therapist_id": str(THERAPIST_ID),
            "status": "PENDING_ACCEPTANCE",
            "service_scope_tags": ["GLUCOSE_METABOLISM"],
            "reason_code": None,
            "service_case_id": None,
            "created_at": "2026-08-24T00:00:00Z",
            "decided_at": None,
            "version": 1,
        }],
        "next_cursor": None,
    }


def test_本人分配详情保留详情专属字段() -> None:
    response = _client().get(
        f"/api/v1/therapist/primary-assignments/{ASSIGNMENT_ID}"
    )

    assert response.status_code == 200
    assert response.json()["subject_masked_label"] == "会员****0085"
    assert response.json()["therapist_display_name"] == "合同测试健管师"


def test_不存在或不可见分配遵守现有404防枚举合同() -> None:
    response = _client(reader=_AssignmentReader(())).get(
        f"/api/v1/therapist/primary-assignments/{ASSIGNMENT_ID}"
    )

    assert response.status_code == 404
    assert response.json()["code"] == "ASSIGNMENT_NOT_FOUND"


@pytest.mark.parametrize("authorization", (None, "Basic invalid-authorization"))
def test_缺少或无效Authorization返回401(
    authorization: str | None,
) -> None:
    headers = {} if authorization is None else {"Authorization": authorization}

    response = _unauthenticated_client().get(
        "/api/v1/therapist/primary-assignments",
        headers=headers,
    )

    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_REQUIRED"


@pytest.mark.parametrize(
    ("authority", "actor_tenant_id"),
    (
        (_TherapistAuthority(status="SUSPENDED"), 81),
        (_TherapistAuthority(tenant_id=82), 81),
        (
            _TherapistAuthority(
                qualification_valid_until=datetime(2026, 8, 23).date()
            ),
            81,
        ),
    ),
)
def test_停用或租户Currentness不一致继续FailClosed(
    authority: _TherapistAuthority,
    actor_tenant_id: int,
) -> None:
    del actor_tenant_id
    response = _client(authority=authority).get(
        "/api/v1/therapist/primary-assignments"
    )

    assert response.status_code == 403
    assert response.json()["code"] == "THERAPIST_CURRENTNESS_FORBIDDEN"


def test_Repository真正不可用仍返回503且不泄漏内部异常() -> None:
    response = _client(reader=_UnavailableAssignmentReader()).get(
        "/api/v1/therapist/primary-assignments"
    )

    assert response.status_code == 503
    assert response.json() == {
        "code": "DEPENDENCY_UNAVAILABLE",
        "message": "request rejected",
    }
    assert "synthetic" not in response.text


def test_接受写事务前仅锁定User当前身份且写事务再校验Profile() -> None:
    from app.core.security import CurrentUser
    from app.modules.member_enrollment import api
    from app.modules.member_enrollment.service import MemberEnrollmentService

    authority = _RecordingTherapistAuthority()
    asyncio.run(
        api._therapist_current(
            authority,
            CurrentUser(id=81, role="therapist", tenant_id=81),
            lock_profile=False,
        )
    )

    assert "FOR SHARE OF u" in authority.sql
    assert "FOR SHARE OF p,u" not in authority.sql
    assert "therapist_for_case_update" in inspect.getsource(
        MemberEnrollmentService.accept_assignment
    )


def _complete_enrollment_preimage() -> dict[str, object]:
    from app.modules.member_enrollment.models import ServiceEnrollmentModel

    return {
        column.name: ENROLLMENT_ID if column.name == "enrollment_id" else None
        for column in ServiceEnrollmentModel.__table__.columns
    }


@pytest.mark.parametrize("invalid_shape", ("missing", "extra"))
def test_Enrollment前像字段缺失或多余均FailClosed(invalid_shape: str) -> None:
    from app.modules.member_enrollment.repository import MemberEnrollmentRepository

    preimage = _complete_enrollment_preimage()
    if invalid_shape == "missing":
        preimage.pop("version")
    else:
        preimage["unexpected"] = "forbidden"
    repository = MemberEnrollmentRepository(_PreimageSession(preimage))

    with pytest.raises(
        RuntimeError, match="Slice 3 case enrollment preimage is invalid"
    ):
        asyncio.run(
            repository.case_enrollment_preimage_for_update(
                assignment_id=ASSIGNMENT_ID,
                enrollment_id=ENROLLMENT_ID,
                therapist_id=THERAPIST_ID,
                actor_user_id=81,
            )
        )


def test_完整Enrollment前像写入MutationPlan且更新时不再全列SELECT() -> None:
    from app.modules.member_enrollment.repository import MemberEnrollmentRepository

    session = _PreimageSession(_complete_enrollment_preimage())
    repository = MemberEnrollmentRepository(session)
    asyncio.run(
        repository.case_enrollment_preimage_for_update(
            assignment_id=str(ASSIGNMENT_ID),
            enrollment_id=str(ENROLLMENT_ID),
            therapist_id=str(THERAPIST_ID),
            actor_user_id=81,
        )
    )
    asyncio.run(
        repository.update_enrollment(
            ENROLLMENT_ID,
            status="CASE_CREATED",
            version=2,
        )
    )

    assert len(session.calls) == 2
    assert "slice3_case_enrollment_preimage_authority_v1" in session.calls[0][0]
    assert all(
        isinstance(session.calls[0][1][name], UUID)
        for name in ("assignment_id", "enrollment_id", "therapist_id")
    )
    assert session.calls[1][0].startswith("UPDATE public.service_enrollment")
    plan = session.info["slice3-mutation-plan"]
    assert len(plan["pre_rows"]) == 1
    assert plan["rows"][0]["value"]["status"] == "CASE_CREATED"


def test_Profile计数更新复用同事务已锁定快照且不二次SELECT() -> None:
    from app.modules.member_enrollment.repository import MemberEnrollmentRepository

    session = _TherapistPlanSession()
    repository = MemberEnrollmentRepository(session)
    profile = asyncio.run(repository.therapist_for_case_update(THERAPIST_ID))
    assert profile is not None
    updated_at = datetime(2026, 8, 24, tzinfo=timezone.utc)
    assert asyncio.run(
        repository.update_therapist_case_count(
            THERAPIST_ID,
            expected_version=1,
            now=updated_at,
        )
    )

    assert len(session.calls) == 2
    assert session.calls[0][0].startswith(
        "SELECT therapist_id,tenant_id,status,service_tags"
    )
    assert session.calls[1][0].startswith("UPDATE public.therapist_profile")
    plan = session.info["slice3-mutation-plan"]
    assert plan["rows"][0]["value"]["updated_at"] == updated_at.isoformat(
        timespec="seconds"
    )


def test_新建ServiceCase返回读取不要求额外Update权限() -> None:
    from app.modules.member_enrollment.repository import MemberEnrollmentRepository

    session = _AssignmentReader(({"case_id": ASSIGNMENT_ID},))
    repository = MemberEnrollmentRepository(session)
    row = asyncio.run(repository.service_case_after_create(ASSIGNMENT_ID))

    assert row == {"case_id": ASSIGNMENT_ID}
    assert "FOR UPDATE" not in session.statement


def test_非被分配健管师接受时返回403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import (
        get_db_session,
        get_institution_onboarding_reader_session,
        get_member_case_writer_session,
    )
    from app.core.security import CurrentUser, get_current_user_from_jwt
    from app.modules.member_enrollment import api

    async def assignment_for_update(repository, assignment_id):
        del repository, assignment_id
        return {
            "assignment_id": str(ASSIGNMENT_ID),
            "therapist_id": "0198b963-38f0-7d7d-8000-000000000099",
        }

    monkeypatch.setattr(
        api.MemberEnrollmentRepository,
        "assignment_for_update",
        assignment_for_update,
    )

    async def current_user():
        return CurrentUser(id=81, role="therapist", tenant_id=81)

    async def authority_session():
        yield _TherapistAuthority()

    async def unused_session():
        yield object()

    app = FastAPI()
    app.include_router(api.therapist_router)
    app.dependency_overrides[get_current_user_from_jwt] = current_user
    app.dependency_overrides[get_db_session] = authority_session
    app.dependency_overrides[get_member_case_writer_session] = unused_session
    app.dependency_overrides[
        get_institution_onboarding_reader_session
    ] = unused_session

    response = TestClient(app).post(
        f"/api/v1/therapist/primary-assignments/{ASSIGNMENT_ID}/accept",
        headers={"Idempotency-Key": "slice3-hotfix-other-therapist"},
        json={"expected_version": 1},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "THERAPIST_SCOPE_FORBIDDEN"


def test_合法当前且被分配健管师接受本人分配保持HTTP201(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import (
        get_db_session,
        get_institution_onboarding_reader_session,
        get_member_case_writer_session,
    )
    from app.core.security import CurrentUser, get_current_user_from_jwt
    from app.modules.member_enrollment import api

    case_id = UUID("0198b963-38f0-7d7d-8000-000000000086")
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    assignment = {
        "assignment_id": str(ASSIGNMENT_ID),
        "enrollment_id": str(ENROLLMENT_ID),
        "tenant_id": 81,
        "therapist_id": str(THERAPIST_ID),
    }
    enrollment = {"mode": "SELF"}
    service_case = {
        "case_id": str(case_id),
        "enrollment_id": str(ENROLLMENT_ID),
        "subject_member_id": str(MEMBER_ID),
        "tenant_id": 81,
        "primary_therapist_id": str(THERAPIST_ID),
        "assignment_id": str(ASSIGNMENT_ID),
        "status": "PREPARING",
        "service_scope_tags": ["GLUCOSE_METABOLISM"],
        "created_at": now,
        "version": 1,
    }

    async def assignment_for_update(repository, assignment_id):
        del repository, assignment_id
        return assignment

    async def case_enrollment_for_update(repository, enrollment_id):
        del repository, enrollment_id
        return enrollment

    async def service_case_after_create(repository, requested_case_id):
        del repository, requested_case_id
        return service_case

    class _Service:
        async def accept_assignment(self, *args, **kwargs):
            del args, kwargs
            return case_id

    async def begin_mutation(*args, **kwargs):
        del args, kwargs
        return ASSIGNMENT_ID, None, None

    async def finish_mutation(*args, result, **kwargs):
        del args, kwargs
        return result

    async def tenant_public_id(authority, tenant_id):
        del authority, tenant_id
        return TENANT_PUBLIC_ID

    monkeypatch.setattr(
        api.MemberEnrollmentRepository,
        "assignment_for_update",
        assignment_for_update,
    )
    monkeypatch.setattr(
        api.MemberEnrollmentRepository,
        "case_enrollment_for_update",
        case_enrollment_for_update,
    )
    monkeypatch.setattr(
        api.MemberEnrollmentRepository,
        "service_case_after_create",
        service_case_after_create,
    )
    monkeypatch.setattr(api, "_service", lambda session: _Service())
    monkeypatch.setattr(api, "_begin_mutation", begin_mutation)
    monkeypatch.setattr(api, "_finish_mutation", finish_mutation)
    monkeypatch.setattr(api, "_tenant_public_id", tenant_public_id)

    async def current_user():
        return CurrentUser(id=81, role="therapist", tenant_id=81)

    async def authority_session():
        yield _TherapistAuthority()

    async def case_writer_session():
        yield object()

    async def institution_session():
        yield object()

    app = FastAPI()
    app.include_router(api.therapist_router)
    app.dependency_overrides[get_current_user_from_jwt] = current_user
    app.dependency_overrides[get_db_session] = authority_session
    app.dependency_overrides[get_member_case_writer_session] = case_writer_session
    app.dependency_overrides[
        get_institution_onboarding_reader_session
    ] = institution_session

    response = TestClient(app).post(
        f"/api/v1/therapist/primary-assignments/{ASSIGNMENT_ID}/accept",
        headers={"Idempotency-Key": "slice3-hotfix-accept"},
        json={"expected_version": 1},
    )

    assert response.status_code == 201
    assert response.json() == {
        "case_id": str(case_id),
        "enrollment_id": str(ENROLLMENT_ID),
        "subject_member_id": str(MEMBER_ID),
        "tenant_id": str(TENANT_PUBLIC_ID),
        "primary_therapist_id": str(THERAPIST_ID),
        "assignment_id": str(ASSIGNMENT_ID),
        "status": "PREPARING",
        "service_scope_tags": ["GLUCOSE_METABOLISM"],
        "created_at": "2026-08-24T00:00:00Z",
        "version": 1,
    }
