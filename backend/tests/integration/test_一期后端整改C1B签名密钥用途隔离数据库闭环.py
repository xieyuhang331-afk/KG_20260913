from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import os
from uuid import UUID, uuid4

import pytest

from app.core.security import create_access_token
from app.modules.health_assessment.service import encode_slice5_cursor
from app.modules.service_fulfillment.service import encode_page_cursor

pytestmark = pytest.mark.integration


def _check(value: bool, code: str) -> None:
    if not value:
        pytest.fail(code, pytrace=False)


def _create_user(database, role):
    return int(asyncio.run(database._fetch_value(
        'INSERT INTO public."user"(phone,password_hash,role,status) '
        "VALUES($1,$2,$3::user_role,'active') RETURNING id",
        "000" + str(uuid4().int)[-8:], "c1b-synthetic-only", role,
    )))


def _resign(token, key, domain):
    payload = token.split(".")[0]
    raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    digest = hmac.digest(key.encode(), domain + raw, "sha256")
    return payload + "." + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def test_C1B_R09_Fresh正式HTTP分页拒绝旧JWT用途且首页可重取(pg_database, real_db_client):
    subject = _create_user(pg_database, "super_admin")
    try:
        access = create_access_token({"sub": str(subject), "role": "super_admin"})
        headers = {"Authorization": "Bearer " + access}
        cursor_id = UUID("0198f1c0-0000-7000-8000-0000000000d1")
        cursor5 = encode_slice5_cursor(cursor_id, {
            "actor_user_id": subject, "role": "super_admin", "kind": "rule-set",
        })
        cursor7 = encode_page_cursor(
            cursor_id, snapshot_ceiling=cursor_id, resource="EXPORT", scope_id=None,
            actor_user_id=subject, actor_role="super_admin", actor_tenant_id=None,
            filters={"risk": None, "status": None},
        )
        before = pg_database.fetch_value('SELECT count(*) FROM public."user"')
        for path, cursor, domain in (
            ("/api/v1/platform/assessment-rule-sets", cursor5, b"slice5-health-assessment-cursor:v1:\x00"),
            ("/api/v1/platform/data-exports", cursor7, b"slice7-service-fulfillment-page-cursor:v1:\x00"),
        ):
            valid = real_db_client.get(path, headers=headers, params={"cursor": cursor, "limit": 1})
            _check(valid.status_code == 200, "C1B_FRESH_VALID_CURSOR_REJECTED")
            old = _resign(cursor, os.environ["KG_JWT_SECRET_KEY"], domain)
            rejected = real_db_client.get(path, headers=headers, params={"cursor": old, "limit": 1})
            _check(rejected.status_code == 422, "C1B_FRESH_OLD_CURSOR_NOT_REJECTED")
            _check(rejected.json() == {"code": "INVALID_REQUEST", "message": "request rejected"},
                   "C1B_FRESH_ERROR_CONTRACT_CHANGED")
            _check(not any(value in rejected.text for value in (str(subject), access, old)),
                   "C1B_FRESH_ERROR_OUTPUT_UNSAFE")
            first = real_db_client.get(path, headers=headers, params={"limit": 1})
            _check(first.status_code == 200, "C1B_FRESH_FIRST_PAGE_RECOVERY_FAILED")
            ordinary = real_db_client.get(path, headers=headers, params={"limit": 0})
            _check(ordinary.status_code == 422 and ordinary.json() == rejected.json(),
                   "C1B_FRESH_INVALID_REQUEST_NOT_SHARED")
        _check(pg_database.fetch_value('SELECT count(*) FROM public."user"') == before,
               "C1B_FRESH_GET_MUTATED_USERS")
    finally:
        asyncio.run(pg_database._fetch_value(
            'DELETE FROM public."user" WHERE id=$1 RETURNING true', subject,
        ))


def test_C1B_R07_Fresh正式HTTP文件凭证独立一次性消费(pg_database, real_db_client, tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.modules.private_file.schemas import UploadCompleteRequest
    from app.modules.private_file.service import complete_upload, record_scan
    from app.modules.private_file.storage import LocalFilesystemAdapter

    subject = _create_user(pg_database, "member")
    file_id = None
    store = LocalFilesystemAdapter((tmp_path / "private").resolve())
    monkeypatch.setattr(real_db_client.app.state, "private_object_store", store)
    content = b"%PDF-1.7\nC1B SYNTHETIC\n%%EOF"
    digest = hashlib.sha256(content).hexdigest()
    request = {"purpose": "BUSINESS_LICENSE", "size": len(content),
               "mime_type": "application/pdf", "sha256": digest}

    class CleanScanner:
        async def scan(self, path, *, mime_type):
            _check(path.is_file() and mime_type == "application/pdf", "C1B_SYNTHETIC_SCAN_INVALID")
            return "CLEAN"

    async def complete_and_scan():
        engine = create_async_engine(os.environ["KG_TEST_PRIVATE_FILE_WRITER_DATABASE_URL"], poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                await complete_upload(session, subject, file_id, UploadCompleteRequest(
                    size=len(content), mime_type="application/pdf", sha256=digest,
                ), object_store=store)
            async with AsyncSession(engine, expire_on_commit=False) as session:
                await record_scan(session, file_id, scanner=CleanScanner(), object_store=store)
        finally:
            await engine.dispose()

    try:
        access = create_access_token({"sub": str(subject), "role": "member"})
        headers = {"Authorization": "Bearer " + access}
        initiated = real_db_client.post("/api/v1/private-files/uploads", headers=headers, json=request)
        _check(initiated.status_code == 200, "C1B_FILE_INIT_FAILED")
        file_id = initiated.json()["data"]["file_id"]
        uploaded = real_db_client.put(
            f"/api/v1/private-files/uploads/{file_id}/content",
            headers={**headers, "Content-Type": "application/octet-stream"}, content=content,
        )
        _check(uploaded.status_code == 200, "C1B_FILE_UPLOAD_FAILED")
        asyncio.run(complete_and_scan())
        issued = real_db_client.post(
            f"/api/v1/private-files/{file_id}/access", headers=headers,
            json={"reason_code": "OWNER_DOWNLOAD"},
        )
        _check(issued.status_code == 200, "C1B_FILE_ACCESS_FAILED")
        grant = issued.json()["data"]
        downloaded = real_db_client.get(
            grant["content_path"], headers={**headers, "X-Private-File-Access": grant["access_credential"]},
        )
        _check(downloaded.status_code == 200 and downloaded.content == content, "C1B_FILE_DOWNLOAD_FAILED")
        _check(downloaded.headers.get("Cache-Control") == "no-store, private, max-age=0",
               "C1B_FILE_NO_STORE_MISSING")
        replay = real_db_client.get(
            grant["content_path"], headers={**headers, "X-Private-File-Access": grant["access_credential"]},
        )
        _check(replay.status_code == 403, "C1B_FILE_REPLAY_ACCEPTED")
        _check(all(value not in replay.text for value in (str(subject), access, grant["access_credential"])),
               "C1B_FILE_REPLAY_OUTPUT_UNSAFE")
    finally:
        if file_id is not None:
            asyncio.run(pg_database._fetch_value(
                "DELETE FROM public.private_file_download_access WHERE private_file_id=$1 RETURNING true", file_id,
            ))
            asyncio.run(pg_database._fetch_value("DELETE FROM public.private_file WHERE file_id=$1 RETURNING true", file_id))
        asyncio.run(pg_database._fetch_value('DELETE FROM public."user" WHERE id=$1 RETURNING true', subject))
