import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Sequence


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import Settings, get_settings
from app.core.database import build_database_url, create_async_engine_from_settings, create_session_factory
from app.modules.auth.service import hash_password
from sqlalchemy import bindparam, text


SEED_PASSWORD_ENV = "KG_P1_SEED_PASSWORD"

P1_PLATFORM_ORGS = (
    {
        "id": 9101,
        "org_name": "康邻测试机构 A",
        "org_code": "ORG-P1-A",
        "org_type": "tenant_org",
        "status": "active",
    },
    {
        "id": 9102,
        "org_name": "康邻测试机构 B",
        "org_code": "ORG-P1-B",
        "org_type": "tenant_org",
        "status": "active",
    },
)

P1_USERS = (
    {"id": 9001, "phone": "13900009001", "role": "super_admin", "status": "active", "tenant_id": None},
    {"id": 9002, "phone": "13900009002", "role": "province_admin", "status": "active", "tenant_id": None},
    {"id": 9003, "phone": "13900009003", "role": "city_admin", "status": "active", "tenant_id": None},
    {"id": 9101, "phone": "13900009101", "role": "org_admin", "status": "active", "tenant_id": None},
    {"id": 9102, "phone": "13900009102", "role": "org_admin", "status": "active", "tenant_id": None},
    {"id": 9201, "phone": "13900009201", "role": "member", "status": "active", "tenant_id": None},
    {"id": 9202, "phone": "13900009202", "role": "member", "status": "active", "tenant_id": None},
)

REVIEWED_AT = datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc)
APPROVED_AT = datetime(2026, 7, 28, 10, 5, tzinfo=timezone.utc)
REJECTED_AT = datetime(2026, 7, 28, 11, 0, tzinfo=timezone.utc)

P1_TENANTS = (
    {
        "id": 8101,
        "org_id": 9101,
        "tenant_code": "P1-TENANT-8101",
        "name": "P1 Pending Store 8101",
        "short_name": "P1 Pending 8101",
        "type": "store",
        "credit_code": "913301008101000001",
        "license_no": "P1-LICENSE-8101",
        "license_image": "https://mock.kanglin.local/tenant/8101/license.png",
        "legal_person_name": "Legal 8101",
        "province": "Zhejiang",
        "city": "Hangzhou",
        "district": "Xihu",
        "address": "No. 8101 Wenyi Road",
        "contact_name": "Contact 8101",
        "contact_phone": "13800008101",
        "contact_email": "tenant8101@example.com",
        "logo_url": None,
        "grade": "standard",
        "status": "pending",
        "reviewed_by": None,
        "reviewed_at": None,
        "approved_at": None,
        "reject_reason": None,
    },
    {
        "id": 8102,
        "org_id": 9101,
        "tenant_code": "P1-TENANT-8102",
        "name": "P1 Active Store 8102",
        "short_name": "P1 Active 8102",
        "type": "store",
        "credit_code": "913301008102000001",
        "license_no": "P1-LICENSE-8102",
        "license_image": "https://mock.kanglin.local/tenant/8102/license.png",
        "legal_person_name": "Legal 8102",
        "province": "Zhejiang",
        "city": "Hangzhou",
        "district": "Xihu",
        "address": "No. 8102 Wenyi Road",
        "contact_name": "Contact 8102",
        "contact_phone": "13800008102",
        "contact_email": "tenant8102@example.com",
        "logo_url": None,
        "grade": "standard",
        "status": "active",
        "reviewed_by": 9001,
        "reviewed_at": REVIEWED_AT,
        "approved_at": APPROVED_AT,
        "reject_reason": None,
    },
    {
        "id": 8103,
        "org_id": 9101,
        "tenant_code": "P1-TENANT-8103",
        "name": "P1 Rejected Store 8103",
        "short_name": "P1 Rejected 8103",
        "type": "store",
        "credit_code": "913301008103000001",
        "license_no": "P1-LICENSE-8103",
        "license_image": "https://mock.kanglin.local/tenant/8103/license.png",
        "legal_person_name": "Legal 8103",
        "province": "Zhejiang",
        "city": "Hangzhou",
        "district": "Xihu",
        "address": "No. 8103 Wenyi Road",
        "contact_name": "Contact 8103",
        "contact_phone": "13800008103",
        "contact_email": "tenant8103@example.com",
        "logo_url": None,
        "grade": "standard",
        "status": "rejected",
        "reviewed_by": 9001,
        "reviewed_at": REJECTED_AT,
        "approved_at": None,
        "reject_reason": "P1 seed rejected tenant for review testing.",
    },
    {
        "id": 8201,
        "org_id": 9102,
        "tenant_code": "P1-TENANT-8201",
        "name": "P1 Pending Store 8201",
        "short_name": "P1 Pending 8201",
        "type": "store",
        "credit_code": "913201008201000001",
        "license_no": "P1-LICENSE-8201",
        "license_image": "https://mock.kanglin.local/tenant/8201/license.png",
        "legal_person_name": "Legal 8201",
        "province": "Jiangsu",
        "city": "Nanjing",
        "district": "Gulou",
        "address": "No. 8201 Zhongshan Road",
        "contact_name": "Contact 8201",
        "contact_phone": "13800008201",
        "contact_email": "tenant8201@example.com",
        "logo_url": None,
        "grade": "standard",
        "status": "pending",
        "reviewed_by": None,
        "reviewed_at": None,
        "approved_at": None,
        "reject_reason": None,
    },
    {
        "id": 8202,
        "org_id": 9102,
        "tenant_code": "P1-TENANT-8202",
        "name": "P1 Active Store 8202",
        "short_name": "P1 Active 8202",
        "type": "store",
        "credit_code": "913201008202000001",
        "license_no": "P1-LICENSE-8202",
        "license_image": "https://mock.kanglin.local/tenant/8202/license.png",
        "legal_person_name": "Legal 8202",
        "province": "Jiangsu",
        "city": "Nanjing",
        "district": "Gulou",
        "address": "No. 8202 Zhongshan Road",
        "contact_name": "Contact 8202",
        "contact_phone": "13800008202",
        "contact_email": "tenant8202@example.com",
        "logo_url": None,
        "grade": "standard",
        "status": "active",
        "reviewed_by": 9001,
        "reviewed_at": REVIEWED_AT,
        "approved_at": APPROVED_AT,
        "reject_reason": None,
    },
)

P1_TENANT_ATTACHMENTS = (
    {
        "tenant_id": 8101,
        "file_type": "business_license",
        "file_url": "https://mock.kanglin.local/tenant/8101/business-license.png",
    },
    {
        "tenant_id": 8101,
        "file_type": "store_photo",
        "file_url": "https://mock.kanglin.local/tenant/8101/store-photo.png",
    },
    {
        "tenant_id": 8201,
        "file_type": "business_license",
        "file_url": "https://mock.kanglin.local/tenant/8201/business-license.png",
    },
    {
        "tenant_id": 8201,
        "file_type": "store_photo",
        "file_url": "https://mock.kanglin.local/tenant/8201/store-photo.png",
    },
)

P1_HEALTH_BATCH_ID = "P1-SEED-9201"

P1_HEALTH_PROFILES = (
    {
        "user_id": 9201,
        "gender": "M",
        "birth_date": date(1990, 1, 1),
        "height": Decimal("175.0"),
        "weight": Decimal("72.0"),
        "blood_type": "O",
        "medical_history": '["hypertension_family_attention"]',
        "allergy_history": "[]",
        "family_history": '["hypertension"]',
        "smoking": "never",
        "drinking": "rare",
        "symptoms": '["fatigue"]',
        "sleep_quality": "normal",
        "bowel_urination": "normal",
    },
)

P1_HEALTH_INDICATORS = (
    {
        "user_id": 9201,
        "plan_id": None,
        "batch_id": P1_HEALTH_BATCH_ID,
        "indicator_type": "systolic_bp",
        "value": Decimal("128.00"),
        "unit": "mmHg",
        "source": "APP",
        "recorded_at": datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc),
    },
    {
        "user_id": 9201,
        "plan_id": None,
        "batch_id": P1_HEALTH_BATCH_ID,
        "indicator_type": "systolic_bp",
        "value": Decimal("125.00"),
        "unit": "mmHg",
        "source": "APP",
        "recorded_at": datetime(2026, 7, 27, 8, 0, tzinfo=timezone.utc),
    },
    {
        "user_id": 9201,
        "plan_id": None,
        "batch_id": P1_HEALTH_BATCH_ID,
        "indicator_type": "systolic_bp",
        "value": Decimal("122.00"),
        "unit": "mmHg",
        "source": "APP",
        "recorded_at": datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc),
    },
    {
        "user_id": 9201,
        "plan_id": None,
        "batch_id": P1_HEALTH_BATCH_ID,
        "indicator_type": "diastolic_bp",
        "value": Decimal("84.00"),
        "unit": "mmHg",
        "source": "APP",
        "recorded_at": datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc),
    },
    {
        "user_id": 9201,
        "plan_id": None,
        "batch_id": P1_HEALTH_BATCH_ID,
        "indicator_type": "diastolic_bp",
        "value": Decimal("82.00"),
        "unit": "mmHg",
        "source": "APP",
        "recorded_at": datetime(2026, 7, 27, 8, 0, tzinfo=timezone.utc),
    },
    {
        "user_id": 9201,
        "plan_id": None,
        "batch_id": P1_HEALTH_BATCH_ID,
        "indicator_type": "diastolic_bp",
        "value": Decimal("80.00"),
        "unit": "mmHg",
        "source": "APP",
        "recorded_at": datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc),
    },
    {
        "user_id": 9201,
        "plan_id": None,
        "batch_id": P1_HEALTH_BATCH_ID,
        "indicator_type": "heart_rate",
        "value": Decimal("76.00"),
        "unit": "bpm",
        "source": "APP",
        "recorded_at": datetime(2026, 7, 26, 8, 5, tzinfo=timezone.utc),
    },
    {
        "user_id": 9201,
        "plan_id": None,
        "batch_id": P1_HEALTH_BATCH_ID,
        "indicator_type": "heart_rate",
        "value": Decimal("74.00"),
        "unit": "bpm",
        "source": "APP",
        "recorded_at": datetime(2026, 7, 27, 8, 5, tzinfo=timezone.utc),
    },
    {
        "user_id": 9201,
        "plan_id": None,
        "batch_id": P1_HEALTH_BATCH_ID,
        "indicator_type": "heart_rate",
        "value": Decimal("72.00"),
        "unit": "bpm",
        "source": "APP",
        "recorded_at": datetime(2026, 7, 28, 8, 5, tzinfo=timezone.utc),
    },
    {
        "user_id": 9201,
        "plan_id": None,
        "batch_id": P1_HEALTH_BATCH_ID,
        "indicator_type": "weight",
        "value": Decimal("72.60"),
        "unit": "kg",
        "source": "APP",
        "recorded_at": datetime(2026, 7, 26, 8, 10, tzinfo=timezone.utc),
    },
    {
        "user_id": 9201,
        "plan_id": None,
        "batch_id": P1_HEALTH_BATCH_ID,
        "indicator_type": "weight",
        "value": Decimal("72.30"),
        "unit": "kg",
        "source": "APP",
        "recorded_at": datetime(2026, 7, 27, 8, 10, tzinfo=timezone.utc),
    },
    {
        "user_id": 9201,
        "plan_id": None,
        "batch_id": P1_HEALTH_BATCH_ID,
        "indicator_type": "weight",
        "value": Decimal("72.00"),
        "unit": "kg",
        "source": "APP",
        "recorded_at": datetime(2026, 7, 28, 8, 10, tzinfo=timezone.utc),
    },
)

P1_TENANT_BINDINGS = (
    {"user_id": 9201, "tenant_id": 8102},
    {"user_id": 9202, "tenant_id": 8202},
)


@dataclass(frozen=True)
class SeedContext:
    settings: Settings
    database_url: str
    engine: object
    session_factory: object
    password_hash: str


@dataclass(frozen=True)
class SeedResult:
    dry_run: bool
    reset: bool
    platform_org_count: int
    user_count: int
    tenant_count: int
    tenant_attachment_count: int
    health_profile_count: int
    health_indicator_count: int
    tenant_binding_count: int


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed P1 platform_org and user test data.")
    parser.add_argument("--dry-run", action="store_true", help="Print the seed plan without writing data.")
    parser.add_argument("--reset", action="store_true", help="Delete P1 platform_org/user seed rows before upserting them.")
    return parser.parse_args(argv)


def ensure_not_production(settings: Settings) -> None:
    if settings.environment.lower() == "production":
        print("Refusing to run P1 seed in production.")
        raise SystemExit(2)


def get_seed_password() -> str:
    password = os.getenv(SEED_PASSWORD_ENV)
    if password is None or not password.strip():
        raise RuntimeError(
            f"Required seed credential environment variable is missing: {SEED_PASSWORD_ENV}"
        )
    return password


def build_seed_plan(*, reset: bool) -> list[str]:
    plan = [
        "validate environment",
        "load database settings",
        "create async engine",
        "create async session factory",
        "generate test password hash",
    ]
    if reset:
        plan.append("plan reset health seed data")
        plan.append("plan reset tenant seed data")
        plan.append("plan reset platform_org/user seed data")
    plan.extend(
        [
            "plan platform_org seed",
            "plan user seed",
            "plan tenant seed",
            "plan tenant_attachment seed",
            "plan health_profile seed",
            "plan health_indicator seed",
            "plan tenant_binding seed",
        ]
    )
    return plan


def prepare_seed_context() -> SeedContext:
    settings = get_settings()
    ensure_not_production(settings)
    engine = create_async_engine_from_settings(settings)
    session_factory = create_session_factory(engine)
    return SeedContext(
        settings=settings,
        database_url=build_database_url(settings),
        engine=engine,
        session_factory=session_factory,
        password_hash=hash_password(get_seed_password()),
    )


def print_seed_plan(context: SeedContext, plan: list[str], *, dry_run: bool) -> None:
    mode = "dry-run" if dry_run else "plan-only"
    print(f"P1 seed {mode}")
    print(f"Environment: {context.settings.environment}")
    print(f"Database URL: {context.database_url}")
    print(f"Test password hash prefix: {context.password_hash.split('$', 1)[0]}")
    print("Execution plan:")
    for index, step in enumerate(plan, start=1):
        print(f"{index}. {step}")
    if dry_run:
        print("No data was inserted, updated, or deleted.")
    else:
        print("Data will be inserted or updated after the plan is confirmed by execution.")


def expanding_text(sql: str, name: str):
    return text(sql).bindparams(bindparam(name, expanding=True))


async def reset_health_seed(session) -> None:
    user_ids = [profile["user_id"] for profile in P1_HEALTH_PROFILES]
    await session.execute(
        text(
            """
            -- P1 health seed reset
            DELETE FROM health_indicator
            WHERE batch_id = :batch_id
            """
        ),
        {"batch_id": P1_HEALTH_BATCH_ID},
    )
    await session.execute(
        expanding_text(
            """
            -- P1 health seed reset
            DELETE FROM health_profile
            WHERE user_id IN :user_ids
            """,
            "user_ids",
        ),
        {"user_ids": user_ids},
    )


async def reset_tenant_binding_seed(session) -> None:
    user_ids = [binding["user_id"] for binding in P1_TENANT_BINDINGS]
    await session.execute(
        expanding_text(
            """
            -- P1 tenant_binding seed reset
            UPDATE "user"
            SET tenant_id = NULL, updated_at = NOW()
            WHERE id IN :user_ids
            """,
            "user_ids",
        ),
        {"user_ids": user_ids},
    )


async def reset_tenant_seed(session) -> None:
    tenant_ids = [tenant["id"] for tenant in P1_TENANTS]
    tenant_codes = [tenant["tenant_code"] for tenant in P1_TENANTS]
    await session.execute(
        expanding_text(
            """
            -- P1 tenant audit seed reset
            DELETE FROM operation_log
            WHERE module = 'tenant'
              AND object_type = 'tenant'
              AND object_id IN :tenant_ids
            """,
            "tenant_ids",
        ),
        {"tenant_ids": tenant_ids},
    )
    await session.execute(
        expanding_text(
            """
            -- P1 tenant audit seed reset
            DELETE FROM tenant_review_log
            WHERE tenant_id IN :tenant_ids
            """,
            "tenant_ids",
        ),
        {"tenant_ids": tenant_ids},
    )
    await session.execute(
        expanding_text(
            """
            -- P1 tenant seed reset
            DELETE FROM tenant_attachment
            WHERE tenant_id IN :tenant_ids
            """,
            "tenant_ids",
        ),
        {"tenant_ids": tenant_ids},
    )
    await session.execute(
        expanding_text(
            """
            -- P1 tenant seed reset
            DELETE FROM tenant
            WHERE tenant_code IN :tenant_codes
            """,
            "tenant_codes",
        ),
        {"tenant_codes": tenant_codes},
    )


async def reset_identity_seed(session) -> None:
    phones = [user["phone"] for user in P1_USERS]
    org_codes = [org["org_code"] for org in P1_PLATFORM_ORGS]
    await session.execute(
        expanding_text(
            """
            -- P1 platform_org/user seed reset
            DELETE FROM "user"
            WHERE phone IN :phones
            """,
            "phones",
        ),
        {"phones": phones},
    )
    await session.execute(
        expanding_text(
            """
            -- P1 platform_org/user seed reset
            DELETE FROM platform_org
            WHERE org_code IN :org_codes
            """,
            "org_codes",
        ),
        {"org_codes": org_codes},
    )


async def seed_platform_orgs(session) -> None:
    await session.execute(
        text(
            """
            INSERT INTO platform_org (
                id, org_name, org_code, org_type, status, created_at, updated_at
            )
            VALUES (
                :id, :org_name, :org_code, :org_type, :status, NOW(), NOW()
            )
            ON CONFLICT (org_code) DO UPDATE
            SET
                org_name = EXCLUDED.org_name,
                org_type = EXCLUDED.org_type,
                status = EXCLUDED.status,
                updated_at = NOW()
            """
        ),
        list(P1_PLATFORM_ORGS),
    )


async def seed_users(session, password_hash: str) -> None:
    rows = [{**user, "password_hash": password_hash} for user in P1_USERS]
    await session.execute(
        text(
            """
            INSERT INTO "user" (
                id, phone, password_hash, role, status, tenant_id, created_at, updated_at
            )
            VALUES (
                :id, :phone, :password_hash, :role, :status, :tenant_id, NOW(), NOW()
            )
            ON CONFLICT (phone) DO UPDATE
            SET
                password_hash = EXCLUDED.password_hash,
                role = EXCLUDED.role,
                status = EXCLUDED.status,
                tenant_id = EXCLUDED.tenant_id,
                updated_at = NOW()
            """
        ),
        rows,
    )


async def seed_tenants(session) -> None:
    await session.execute(
        text(
            """
            INSERT INTO tenant (
                id, org_id, tenant_code, name, short_name, type, credit_code,
                license_no, license_image, legal_person_name, province, city,
                district, address, contact_name, contact_phone, contact_email,
                logo_url, grade, status, reviewed_by, reviewed_at, approved_at,
                reject_reason, created_at, updated_at
            )
            VALUES (
                :id, :org_id, :tenant_code, :name, :short_name, :type, :credit_code,
                :license_no, :license_image, :legal_person_name, :province, :city,
                :district, :address, :contact_name, :contact_phone, :contact_email,
                :logo_url, :grade, :status, :reviewed_by, :reviewed_at, :approved_at,
                :reject_reason, NOW(), NOW()
            )
            ON CONFLICT (tenant_code) DO UPDATE
            SET
                org_id = EXCLUDED.org_id,
                name = EXCLUDED.name,
                short_name = EXCLUDED.short_name,
                type = EXCLUDED.type,
                credit_code = EXCLUDED.credit_code,
                license_no = EXCLUDED.license_no,
                license_image = EXCLUDED.license_image,
                legal_person_name = EXCLUDED.legal_person_name,
                province = EXCLUDED.province,
                city = EXCLUDED.city,
                district = EXCLUDED.district,
                address = EXCLUDED.address,
                contact_name = EXCLUDED.contact_name,
                contact_phone = EXCLUDED.contact_phone,
                contact_email = EXCLUDED.contact_email,
                logo_url = EXCLUDED.logo_url,
                grade = EXCLUDED.grade,
                status = EXCLUDED.status,
                reviewed_by = EXCLUDED.reviewed_by,
                reviewed_at = EXCLUDED.reviewed_at,
                approved_at = EXCLUDED.approved_at,
                reject_reason = EXCLUDED.reject_reason,
                updated_at = NOW()
            """
        ),
        list(P1_TENANTS),
    )


async def seed_tenant_attachments(session) -> None:
    tenant_ids = [tenant["id"] for tenant in P1_TENANTS]
    await session.execute(
        expanding_text(
            """
            -- P1 tenant_attachment idempotent rebuild
            DELETE FROM tenant_attachment
            WHERE tenant_id IN :tenant_ids
            """,
            "tenant_ids",
        ),
        {"tenant_ids": tenant_ids},
    )
    await session.execute(
        text(
            """
            INSERT INTO tenant_attachment (
                tenant_id, file_type, file_url, created_at
            )
            VALUES (
                :tenant_id, :file_type, :file_url, NOW()
            )
            """
        ),
        list(P1_TENANT_ATTACHMENTS),
    )


async def seed_health_profiles(session) -> None:
    await session.execute(
        text(
            """
            INSERT INTO health_profile (
                user_id, gender, birth_date, height, weight, blood_type,
                medical_history, allergy_history, family_history, smoking,
                drinking, symptoms, sleep_quality, bowel_urination,
                created_at, updated_at
            )
            VALUES (
                :user_id, :gender, :birth_date, :height, :weight, :blood_type,
                CAST(:medical_history AS JSONB), CAST(:allergy_history AS JSONB),
                CAST(:family_history AS JSONB), :smoking, :drinking,
                CAST(:symptoms AS JSONB), :sleep_quality, :bowel_urination,
                NOW(), NOW()
            )
            ON CONFLICT (user_id) DO UPDATE
            SET
                gender = EXCLUDED.gender,
                birth_date = EXCLUDED.birth_date,
                height = EXCLUDED.height,
                weight = EXCLUDED.weight,
                blood_type = EXCLUDED.blood_type,
                medical_history = EXCLUDED.medical_history,
                allergy_history = EXCLUDED.allergy_history,
                family_history = EXCLUDED.family_history,
                smoking = EXCLUDED.smoking,
                drinking = EXCLUDED.drinking,
                symptoms = EXCLUDED.symptoms,
                sleep_quality = EXCLUDED.sleep_quality,
                bowel_urination = EXCLUDED.bowel_urination,
                updated_at = NOW()
            """
        ),
        list(P1_HEALTH_PROFILES),
    )


async def seed_health_indicators(session) -> None:
    await session.execute(
        text(
            """
            -- P1 health_indicator idempotent rebuild
            DELETE FROM health_indicator
            WHERE batch_id = :batch_id
            """
        ),
        {"batch_id": P1_HEALTH_BATCH_ID},
    )
    await session.execute(
        text(
            """
            INSERT INTO health_indicator (
                user_id, plan_id, batch_id, indicator_type, value, unit,
                source, recorded_at, created_at
            )
            VALUES (
                :user_id, :plan_id, :batch_id, :indicator_type, :value, :unit,
                :source, :recorded_at, NOW()
            )
            """
        ),
        list(P1_HEALTH_INDICATORS),
    )


async def seed_tenant_bindings(session) -> None:
    await session.execute(
        text(
            """
            -- P1 tenant_binding seed
            UPDATE "user"
            SET tenant_id = :tenant_id, updated_at = NOW()
            WHERE id = :user_id
            """
        ),
        list(P1_TENANT_BINDINGS),
    )


async def run_identity_seed(context: SeedContext, *, dry_run: bool, reset: bool) -> SeedResult:
    if dry_run:
        return SeedResult(
            dry_run=True,
            reset=reset,
            platform_org_count=len(P1_PLATFORM_ORGS),
            user_count=len(P1_USERS),
            tenant_count=len(P1_TENANTS),
            tenant_attachment_count=len(P1_TENANT_ATTACHMENTS),
            health_profile_count=len(P1_HEALTH_PROFILES),
            health_indicator_count=len(P1_HEALTH_INDICATORS),
            tenant_binding_count=len(P1_TENANT_BINDINGS),
        )

    async with context.session_factory() as session:
        try:
            if reset:
                await reset_health_seed(session)
                await reset_tenant_binding_seed(session)
                await reset_tenant_seed(session)
                await reset_identity_seed(session)
            await seed_platform_orgs(session)
            await seed_users(session, context.password_hash)
            await seed_tenants(session)
            await seed_tenant_attachments(session)
            await seed_health_profiles(session)
            await seed_health_indicators(session)
            await seed_tenant_bindings(session)
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    return SeedResult(
        dry_run=False,
        reset=reset,
        platform_org_count=len(P1_PLATFORM_ORGS),
        user_count=len(P1_USERS),
        tenant_count=len(P1_TENANTS),
        tenant_attachment_count=len(P1_TENANT_ATTACHMENTS),
        health_profile_count=len(P1_HEALTH_PROFILES),
        health_indicator_count=len(P1_HEALTH_INDICATORS),
        tenant_binding_count=len(P1_TENANT_BINDINGS),
    )


def print_seed_result(result: SeedResult) -> None:
    if result.dry_run:
        return
    action = "reset and upserted" if result.reset else "upserted"
    print(
        f"P1 identity seed {action}: "
        f"{result.platform_org_count} platform_org rows, "
        f"{result.user_count} user rows, "
        f"{result.tenant_count} tenant rows, "
        f"{result.tenant_attachment_count} tenant_attachment rows, "
        f"{result.health_profile_count} health_profile rows, "
        f"{result.health_indicator_count} health_indicator rows, "
        f"{result.tenant_binding_count} tenant_binding rows."
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    context = prepare_seed_context()
    plan = build_seed_plan(reset=args.reset)
    print_seed_plan(context, plan, dry_run=args.dry_run)
    result = asyncio.run(run_identity_seed(context, dry_run=args.dry_run, reset=args.reset))
    print_seed_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
