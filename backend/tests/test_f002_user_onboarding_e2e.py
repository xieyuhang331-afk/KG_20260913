from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


def _client(monkeypatch):
    from app.core.database import get_db_session
    from app.main import create_app

    app = create_app()

    class FakeSession:
        pass

    async def fake_session():
        yield FakeSession()

    app.dependency_overrides[get_db_session] = fake_session
    return TestClient(app)


def _jwt_headers(role: str = "member", *, user_id: int = 1001) -> dict:
    from app.core.security import create_access_token

    token = create_access_token({"sub": str(user_id), "role": role})
    return {"Authorization": f"Bearer {token}"}


def _profile_payload() -> dict:
    return {
        "gender": "F",
        "birth_date": "1990-01-01",
        "height": "165.5",
        "weight": "55.0",
        "blood_type": "A",
        "medical_history": ["hypertension"],
        "allergy_history": {"drug": ["penicillin"]},
        "family_history": [],
        "smoking": "never",
        "drinking": "none",
        "symptoms": [{"name": "fatigue"}],
        "sleep_quality": "normal",
        "bowel_urination": "normal",
    }


@pytest.fixture
def e2e_state(monkeypatch):
    from app.modules.auth.schemas import UserRegisterResponse
    from app.modules.tenant.schemas import (
        ActiveTenantListItem,
        ActiveTenantListResponse,
    )
    from app.modules.user_health.schemas import HealthProfileResponse

    state = {
        "users": {},
        "phones": {},
        "profiles": {},
        "tenants": {
            501: {"tenant_code": "TACTIVE001", "name": "Kanglin Active Store", "status": "active"},
            502: {"tenant_code": "TPENDING001", "name": "Kanglin Pending Store", "status": "pending"},
            503: {"tenant_code": "TREJECT001", "name": "Kanglin Rejected Store", "status": "rejected"},
            504: {"tenant_code": "TACTIVE002", "name": "Kanglin Active Store 2", "status": "active"},
        },
        "next_user_id": 1001,
        "next_profile_id": 2001,
    }

    async def register_user(session, payload):
        if payload.phone in state["phones"]:
            raise HTTPException(status_code=409, detail="用户已存在")
        user_id = state["next_user_id"]
        state["next_user_id"] += 1
        user = {
            "id": user_id,
            "phone": payload.phone,
            "role": "member",
            "status": "active",
            "verify_status": None,
            "tenant_id": None,
            "password_hash": "hashed-secret",
            "real_name": None,
            "id_card": None,
        }
        state["users"][user_id] = user
        state["phones"][payload.phone] = user_id
        return UserRegisterResponse(
            id=user_id,
            phone=payload.phone,
            role="member",
            status="active",
            verify_status=None,
            tenant_id=None,
            created_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
        )

    async def create_profile(session, *, user_id, payload):
        if user_id not in state["users"]:
            raise HTTPException(status_code=404, detail="User not found")
        if user_id in state["profiles"]:
            raise HTTPException(status_code=409, detail="Health profile already exists")
        profile = HealthProfileResponse(
            id=state["next_profile_id"],
            user_id=user_id,
            gender=payload.gender,
            birth_date=payload.birth_date,
            height=payload.height,
            weight=payload.weight,
            blood_type=payload.blood_type,
            medical_history=payload.medical_history,
            allergy_history=payload.allergy_history,
            family_history=payload.family_history,
            smoking=payload.smoking,
            drinking=payload.drinking,
            symptoms=payload.symptoms,
            sleep_quality=payload.sleep_quality,
            bowel_urination=payload.bowel_urination,
            created_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
            updated_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
        )
        state["next_profile_id"] += 1
        state["profiles"][user_id] = profile
        return profile

    async def get_profile(session, *, user_id):
        if user_id not in state["users"]:
            raise HTTPException(status_code=404, detail="User not found")
        profile = state["profiles"].get(user_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Health profile not found")
        return profile

    async def list_active_tenants(session, current_user, query):
        items = [
            ActiveTenantListItem(
                tenant_id=tenant_id,
                tenant_code=tenant["tenant_code"],
                name=tenant["name"],
                type="health_store",
                grade=None,
                province="ZJ",
                city="HZ",
                district="XH",
                address="No.100 Wensan Road",
                logo_url=None,
                contact_phone=None,
                approved_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
            )
            for tenant_id, tenant in state["tenants"].items()
            if tenant["status"] == "active"
        ]
        return ActiveTenantListResponse(items=items, total=len(items), page=query.page, page_size=query.page_size)

    monkeypatch.setattr("app.modules.auth.api.register_user", register_user)
    monkeypatch.setattr("app.modules.user_health.api.create_health_profile", create_profile)
    monkeypatch.setattr("app.modules.user_health.api.get_health_profile", get_profile)
    monkeypatch.setattr("app.modules.tenant.api.list_active_tenants", list_active_tenants)
    return state


def test_f002_user_onboarding_happy_path(monkeypatch, e2e_state):
    client = _client(monkeypatch)

    register_response = client.post(
        "/api/v1/users/register",
        json={"phone": "13800139901", "password": "Secret12345"},
    )
    assert register_response.status_code == 200
    user = register_response.json()["data"]
    assert user["role"] == "member"
    assert user["status"] == "active"
    assert "password_hash" not in user

    identity_response = client.post(
        f"/api/v1/users/{user['id']}/identity",
        json={"real_name": "Zhang San", "id_card": "110101199001011234"},
        headers=_jwt_headers(user_id=user["id"]),
    )
    assert identity_response.status_code == 410
    assert identity_response.json()["code"] == "LEGACY_IDENTITY_ENDPOINT_RETIRED"
    assert e2e_state["users"][user["id"]]["id_card"] is None

    create_profile_response = client.post(
        f"/api/v1/users/{user['id']}/health-profile",
        json=_profile_payload(),
        headers=_jwt_headers(user_id=user["id"]),
    )
    assert create_profile_response.status_code == 201
    created_profile = create_profile_response.json()["data"]
    assert created_profile["user_id"] == user["id"]
    assert created_profile["medical_history"] == ["hypertension"]

    query_profile_response = client.get(
        f"/api/v1/users/{user['id']}/health-profile",
        headers=_jwt_headers(user_id=user["id"]),
    )
    assert query_profile_response.status_code == 200
    queried_profile = query_profile_response.json()["data"]
    assert queried_profile["birth_date"] == "1990-01-01"
    assert "password_hash" not in queried_profile
    assert "id_card" not in queried_profile
    assert "real_name" not in queried_profile

    active_tenants_response = client.get("/api/v1/tenants/active", headers=_jwt_headers(user_id=user["id"]))
    assert active_tenants_response.status_code == 200
    active_items = active_tenants_response.json()["data"]["items"]
    assert {item["tenant_id"] for item in active_items} == {501, 504}
    assert "credit_code" not in active_items[0]
    assert "license_no" not in active_items[0]
    assert "org_id" not in active_items[0]

    bind_response = client.post(
        f"/api/v1/users/{user['id']}/tenant-binding",
        json={"tenant_id": 501},
        headers=_jwt_headers(user_id=user["id"]),
    )
    assert bind_response.status_code == 410
    assert bind_response.json()["code"] == "LEGACY_MEMBER_TENANT_BINDING_RETIRED"
    assert e2e_state["users"][user["id"]]["tenant_id"] is None


def test_f002_duplicate_registration_returns_409(monkeypatch, e2e_state):
    client = _client(monkeypatch)

    first = client.post("/api/v1/users/register", json={"phone": "13800139902", "password": "Secret12345"})
    second = client.post("/api/v1/users/register", json={"phone": "13800139902", "password": "Secret12345"})

    assert first.status_code == 200
    assert second.status_code == 409
    assert list(e2e_state["phones"]).count("13800139902") == 1


def test_f002_idor_guards_do_not_mutate_target_user(monkeypatch, e2e_state):
    client = _client(monkeypatch)
    user_a = client.post("/api/v1/users/register", json={"phone": "13800139903", "password": "Secret12345"}).json()[
        "data"
    ]
    user_b = client.post("/api/v1/users/register", json={"phone": "13800139904", "password": "Secret12345"}).json()[
        "data"
    ]

    identity = client.post(
        f"/api/v1/users/{user_b['id']}/identity",
        json={"real_name": "Li Si", "id_card": "110101199001011235"},
        headers=_jwt_headers(user_id=user_a["id"]),
    )
    create_profile = client.post(
        f"/api/v1/users/{user_b['id']}/health-profile",
        json=_profile_payload(),
        headers=_jwt_headers(user_id=user_a["id"]),
    )
    query_profile = client.get(
        f"/api/v1/users/{user_b['id']}/health-profile",
        headers=_jwt_headers(user_id=user_a["id"]),
    )
    bind_tenant = client.post(
        f"/api/v1/users/{user_b['id']}/tenant-binding",
        json={"tenant_id": 501},
        headers=_jwt_headers(user_id=user_a["id"]),
    )

    assert identity.status_code == 410
    assert create_profile.status_code == 403
    assert query_profile.status_code == 403
    assert bind_tenant.status_code == 410
    assert e2e_state["users"][user_b["id"]]["real_name"] is None
    assert user_b["id"] not in e2e_state["profiles"]
    assert e2e_state["users"][user_b["id"]]["tenant_id"] is None


def test_f002_duplicate_health_profile_returns_409(monkeypatch, e2e_state):
    client = _client(monkeypatch)
    user = client.post("/api/v1/users/register", json={"phone": "13800139905", "password": "Secret12345"}).json()[
        "data"
    ]

    first = client.post(
        f"/api/v1/users/{user['id']}/health-profile",
        json=_profile_payload(),
        headers=_jwt_headers(user_id=user["id"]),
    )
    second = client.post(
        f"/api/v1/users/{user['id']}/health-profile",
        json=_profile_payload(),
        headers=_jwt_headers(user_id=user["id"]),
    )

    assert first.status_code == 201
    assert second.status_code == 409
    assert list(e2e_state["profiles"]).count(user["id"]) == 1


@pytest.mark.parametrize("tenant_id", [502, 503, 999999])
def test_f002_tenant_binding_rejects_invalid_tenants(monkeypatch, e2e_state, tenant_id):
    client = _client(monkeypatch)
    user = client.post("/api/v1/users/register", json={"phone": f"13800139{tenant_id % 1000:03d}", "password": "Secret12345"}).json()[
        "data"
    ]

    response = client.post(
        f"/api/v1/users/{user['id']}/tenant-binding",
        json={"tenant_id": tenant_id},
        headers=_jwt_headers(user_id=user["id"]),
    )

    assert response.status_code == 410
    assert e2e_state["users"][user["id"]]["tenant_id"] is None


def test_f002_duplicate_tenant_binding_does_not_overwrite(monkeypatch, e2e_state):
    client = _client(monkeypatch)
    user = client.post("/api/v1/users/register", json={"phone": "13800139906", "password": "Secret12345"}).json()[
        "data"
    ]

    first = client.post(
        f"/api/v1/users/{user['id']}/tenant-binding",
        json={"tenant_id": 501},
        headers=_jwt_headers(user_id=user["id"]),
    )
    second = client.post(
        f"/api/v1/users/{user['id']}/tenant-binding",
        json={"tenant_id": 504},
        headers=_jwt_headers(user_id=user["id"]),
    )

    assert first.status_code == 410
    assert second.status_code == 410
    assert e2e_state["users"][user["id"]]["tenant_id"] is None


@pytest.mark.parametrize("role", ["org_admin", "super_admin", "province_admin", "city_admin"])
def test_f002_non_member_roles_are_forbidden(monkeypatch, e2e_state, role):
    client = _client(monkeypatch)
    user = client.post("/api/v1/users/register", json={"phone": "13800139907", "password": "Secret12345"}).json()[
        "data"
    ]
    jwt_headers = _jwt_headers(role, user_id=user["id"])

    identity = client.post(
        f"/api/v1/users/{user['id']}/identity",
        json={"real_name": "Zhang San", "id_card": "110101199001011234"},
        headers=jwt_headers,
    )
    create_profile = client.post(
        f"/api/v1/users/{user['id']}/health-profile",
        json=_profile_payload(),
        headers=jwt_headers,
    )
    query_profile = client.get(
        f"/api/v1/users/{user['id']}/health-profile",
        headers=_jwt_headers(role, user_id=user["id"]),
    )
    active_tenants = client.get("/api/v1/tenants/active", headers=jwt_headers)
    bind_tenant = client.post(
        f"/api/v1/users/{user['id']}/tenant-binding",
        json={"tenant_id": 501},
        headers=jwt_headers,
    )

    assert identity.status_code == 410
    assert create_profile.status_code == 403
    assert query_profile.status_code == 403
    assert active_tenants.status_code == 403
    assert bind_tenant.status_code == 410
