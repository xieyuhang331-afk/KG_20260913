from __future__ import annotations

import asyncio
import os
import secrets

import asyncpg
import pytest

from app.core.uuid_generator import Uuid7Generator
from tests.integration.conftest import _to_asyncpg_dsn

pytestmark = pytest.mark.integration


async def _seed_current_therapist(database_url: str) -> dict[str, object]:
    generator = Uuid7Generator()
    suffix = secrets.randbelow(10_000_000)
    org_id = 9_430_000_000 + suffix
    tenant_id = org_id
    org_user_id = 9_440_000_000 + suffix
    therapist_user_id = 9_450_000_000 + suffix
    tenant_public_id = generator.generate()
    institution_invitation_id = generator.generate()
    application_id = generator.generate()
    therapist_invitation_id = generator.generate()
    therapist_id = generator.generate()
    connection = await asyncpg.connect(_to_asyncpg_dsn(database_url))
    try:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO public.platform_org(id,org_name,org_code,org_type,status) "
                "VALUES($1,'Synthetic R3 Org',$2,'county','active')",
                org_id,
                f"R3{suffix}",
            )
            await connection.execute(
                "INSERT INTO public.tenant(id,org_id,tenant_code,name,type,province,city,status) "
                "VALUES($1,$1,$2,'Synthetic R3 Tenant','health_store',"
                "'Synthetic Province','Synthetic City','active')",
                tenant_id,
                f"R3-{suffix}",
            )
            await connection.execute(
                'INSERT INTO public."user"(id,phone,password_hash,role,status,tenant_id) '
                "VALUES($1,$2,'synthetic-hash','org_admin','active',$3),"
                "($4,$5,'synthetic-hash','therapist','active',$3)",
                org_user_id,
                f"18{suffix:09d}"[-11:],
                tenant_id,
                therapist_user_id,
                f"17{suffix:09d}"[-11:],
            )
            await connection.execute(
                "INSERT INTO public.institution_invitation("
                "invitation_id,institution_name,institution_type,applicant_phone_ciphertext,"
                "applicant_phone_digest,pilot_batch_code,administrative_region_id,code_digest,"
                "status,failed_attempts,expires_at,issued_by,issued_at,activated_at,version) "
                "VALUES($1,'Synthetic R3 Institution','HEALTH_STORE',$2,$3,'R3',$4,$5,"
                "'ACTIVATED',0,now()+interval '1 day',0,now(),now(),1)",
                institution_invitation_id,
                b"synthetic",
                "1" * 64,
                org_id,
                "2" * 64,
            )
            await connection.execute(
                "INSERT INTO public.institution_application("
                "application_id,invitation_id,applicant_user_id,institution_type,status,"
                "draft_payload,correction_fields,current_revision_no,tenant_internal_id,"
                "tenant_public_id,service_ready,created_at,updated_at,submitted_at,reviewed_at,version) "
                "VALUES($1,$2,$3,'HEALTH_STORE','APPROVED','{}'::jsonb,'[]'::jsonb,1,"
                "$4,$5,false,now(),now(),now(),now(),3)",
                application_id,
                institution_invitation_id,
                org_user_id,
                tenant_id,
                tenant_public_id,
            )
            await connection.execute(
                "INSERT INTO public.therapist_invitation("
                "invitation_id,tenant_id,phone_ciphertext,phone_encryption_key_id,phone_digest,"
                "phone_digest_key_id,phone_masked,code_digest,code_digest_key_id,expires_at,"
                "status,failed_attempts,issued_by,issued_at,activated_at,version) VALUES("
                "$1,$2,$3,'synthetic-key',$4,'synthetic-digest-key','170****0000',$5,"
                "'synthetic-code-key',now()+interval '1 day','ACTIVATED',0,$6,now(),now(),1)",
                therapist_invitation_id,
                tenant_id,
                b"synthetic",
                "3" * 64,
                "4" * 64,
                org_user_id,
            )
            await connection.execute(
                "INSERT INTO public.therapist_profile("
                "therapist_id,user_id,tenant_id,invitation_id,status,capacity_limit,"
                "active_case_count,current_revision_no,totp_secret_ciphertext,"
                "totp_encryption_key_id,totp_enabled,activated_at,created_at,updated_at,version) "
                "VALUES($1,$2,$3,$4,'DRAFT',30,0,0,$5,'synthetic-totp-key',true,"
                "now(),now(),now(),1)",
                therapist_id,
                therapist_user_id,
                tenant_id,
                therapist_invitation_id,
                b"synthetic",
            )
    finally:
        await connection.close()
    return {
        "org_id": org_id,
        "tenant_id": tenant_id,
        "therapist_user_id": therapist_user_id,
        "therapist_id": therapist_id,
    }


async def _delete_seed(database_url: str, values: dict[str, object]) -> None:
    connection = await asyncpg.connect(_to_asyncpg_dsn(database_url))
    try:
        async with connection.transaction():
            await connection.execute(
                "DELETE FROM public.therapist_profile WHERE therapist_id=$1",
                values["therapist_id"],
            )
            await connection.execute(
                "DELETE FROM public.institution_application WHERE tenant_internal_id=$1",
                values["tenant_id"],
            )
            await connection.execute(
                "DELETE FROM public.therapist_invitation WHERE tenant_id=$1",
                values["tenant_id"],
            )
            await connection.execute(
                "DELETE FROM public.institution_invitation WHERE administrative_region_id=$1",
                values["org_id"],
            )
            await connection.execute(
                'DELETE FROM public."user" WHERE id IN ($1,$2)',
                values["therapist_user_id"],
                values["org_id"] + 10_000_000,
            )
            await connection.execute(
                "DELETE FROM public.tenant WHERE id=$1", values["tenant_id"]
            )
            await connection.execute(
                "DELETE FROM public.platform_org WHERE id=$1", values["org_id"]
            )
    finally:
        await connection.close()


def test_旧方案两个正式Writer共享锁后升级存在确定性冲突(pg_database) -> None:
    async def scenario() -> None:
        values = await _seed_current_therapist(pg_database.database_url)
        writer_url = os.environ["KG_TEST_THERAPIST_ONBOARDING_WRITER_DATABASE_URL"]
        first = await asyncpg.connect(_to_asyncpg_dsn(writer_url))
        second = await asyncpg.connect(_to_asyncpg_dsn(writer_url))
        first_tx = first.transaction()
        second_tx = second.transaction()
        try:
            await first_tx.start()
            await second_tx.start()
            await first.execute("SET LOCAL lock_timeout='1500ms'")
            await second.execute("SET LOCAL lock_timeout='1500ms'")
            for connection in (first, second):
                await connection.fetchval(
                    "SELECT therapist_id FROM public.therapist_profile "
                    "WHERE therapist_id=$1 FOR SHARE",
                    values["therapist_id"],
                )

            async def upgrade(connection):
                try:
                    await connection.execute(
                        "UPDATE public.therapist_profile "
                        "SET practice_summary=practice_summary WHERE therapist_id=$1",
                        values["therapist_id"],
                    )
                    return None
                except Exception as error:
                    return getattr(error, "sqlstate", type(error).__name__)

            outcomes = await asyncio.gather(upgrade(first), upgrade(second))
            assert any(value in {"40P01", "55P03"} for value in outcomes)
        finally:
            if not first.is_closed():
                await first_tx.rollback()
                await first.close()
            if not second.is_closed():
                await second_tx.rollback()
                await second.close()
            await _delete_seed(pg_database.database_url, values)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "writer_environment",
    (
        "KG_TEST_THERAPIST_ONBOARDING_WRITER_DATABASE_URL",
        "KG_TEST_THERAPIST_REVIEW_WRITER_DATABASE_URL",
    ),
)
def test_0043Writer首次锁应直接串行而非共享锁升级(
    pg_database, writer_environment
) -> None:
    async def scenario() -> None:
        values = await _seed_current_therapist(pg_database.database_url)
        writer_url = os.environ[writer_environment]
        first = await asyncpg.connect(_to_asyncpg_dsn(writer_url))
        second = await asyncpg.connect(_to_asyncpg_dsn(writer_url))
        first_tx = first.transaction()
        second_tx = second.transaction()
        first_holds = asyncio.Event()
        release_first = asyncio.Event()

        async def owner() -> None:
            await first_tx.start()
            row = await first.fetchrow(
                "SELECT * FROM public.slice2_therapist_onboarding_currentness_v1($1,$2)",
                values["therapist_user_id"],
                values["tenant_id"],
            )
            assert row is not None
            first_holds.set()
            await release_first.wait()
            await first_tx.commit()

        async def waiter() -> None:
            await first_holds.wait()
            await second_tx.start()
            row = await second.fetchrow(
                "SELECT * FROM public.slice2_therapist_onboarding_currentness_v1($1,$2)",
                values["therapist_user_id"],
                values["tenant_id"],
            )
            assert row is not None
            writable_column = (
                "status"
                if writer_environment
                == "KG_TEST_THERAPIST_REVIEW_WRITER_DATABASE_URL"
                else "practice_summary"
            )
            await second.execute(
                f"UPDATE public.therapist_profile SET {writable_column}="
                f"{writable_column} WHERE therapist_id=$1",
                values["therapist_id"],
            )
            await second_tx.commit()

        owner_task = asyncio.create_task(owner())
        waiter_task = asyncio.create_task(waiter())
        try:
            try:
                await asyncio.wait_for(first_holds.wait(), timeout=5)
            except TimeoutError:
                # Surface an authority/setup failure from the owner task instead
                # of leaving the RED contract blocked on an event it cannot set.
                await owner_task
                raise
            await asyncio.sleep(0)
            release_first.set()
            await asyncio.wait_for(asyncio.gather(owner_task, waiter_task), timeout=5)
        finally:
            release_first.set()
            for task in (owner_task, waiter_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(owner_task, waiter_task, return_exceptions=True)
            if not first.is_closed():
                await first.close()
            if not second.is_closed():
                await second.close()
            await _delete_seed(pg_database.database_url, values)

    asyncio.run(scenario())


def test_0043本人退出权威双连接按UserTenantProfile顺序串行(pg_database) -> None:
    async def scenario() -> None:
        values = await _seed_current_therapist(pg_database.database_url)
        writer_url = os.environ["KG_TEST_THERAPIST_REVIEW_WRITER_DATABASE_URL"]
        first = await asyncpg.connect(_to_asyncpg_dsn(writer_url))
        second = await asyncpg.connect(_to_asyncpg_dsn(writer_url))
        first_tx = first.transaction()
        second_tx = second.transaction()
        first_holds = asyncio.Event()
        release_first = asyncio.Event()
        request_digest = "a" * 64

        async def invoke(connection):
            return await connection.fetchrow(
                "SELECT * FROM public.slice2_therapist_self_exit_currentness_v1("
                "$1,$2,$3,$4)",
                values["therapist_user_id"],
                values["tenant_id"],
                "r3-self-exit-lock-order-0001",
                request_digest,
            )

        async def owner() -> None:
            await first_tx.start()
            assert await invoke(first) is None
            first_holds.set()
            await release_first.wait()
            await first_tx.commit()

        async def waiter() -> None:
            await first_holds.wait()
            await second_tx.start()
            assert await invoke(second) is None
            await second_tx.commit()

        owner_task = asyncio.create_task(owner())
        waiter_task = asyncio.create_task(waiter())
        try:
            await asyncio.wait_for(first_holds.wait(), timeout=5)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(waiter_task), timeout=0.2)
            release_first.set()
            await asyncio.wait_for(
                asyncio.gather(owner_task, waiter_task), timeout=5
            )
        finally:
            release_first.set()
            for task in (owner_task, waiter_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(owner_task, waiter_task, return_exceptions=True)
            for connection in (first, second):
                if not connection.is_closed():
                    await connection.close()
            await _delete_seed(pg_database.database_url, values)

    asyncio.run(scenario())
