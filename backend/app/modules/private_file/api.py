from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response

from app.core.database import get_db_session, get_institution_onboarding_reader_session, get_private_file_writer_session
from app.core.responses import ok_response
from app.core.security import CurrentUser, get_current_user_from_jwt
from app.modules.auth.service import verify_password
from app.modules.private_file.schemas import FileAccessRequest, UploadCompleteRequest, UploadInitiateRequest
from app.modules.private_file.service import (
    complete_upload,
    authorize_file_access,
    delete_temporary,
    initiate_upload,
    metadata,
    read_authorized_content,
    upload_content,
)


router = APIRouter(prefix="/api/v1/private-files", tags=["private_file"])


async def _safe_call(awaitable):
    try:
        return await awaitable
    except asyncio.CancelledError:
        raise
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "PRIVATE_FILE_PERSISTENCE_UNAVAILABLE") from None


@router.post("/uploads")
async def post_upload(payload: UploadInitiateRequest, current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_private_file_writer_session)):
    return ok_response(await _safe_call(initiate_upload(session, current_user.id, payload)))


@router.put("/uploads/{file_id}/content")
async def put_upload_content(file_id: str, data: bytes = Body(media_type="application/octet-stream"), current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_private_file_writer_session)):
    await _safe_call(upload_content(session, current_user.id, file_id, data))
    return ok_response({"file_id": file_id, "uploaded": True})


@router.post("/uploads/{file_id}/complete")
async def post_upload_complete(file_id: str, payload: UploadCompleteRequest, current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_private_file_writer_session)):
    result = await _safe_call(complete_upload(session, current_user.id, file_id, payload))
    from app.tasks.institution_onboarding_tasks import scan_private_file_task
    try:
        scan_private_file_task.delay(file_id)
    except Exception:
        result["dispatch_pending"] = True
    return ok_response(result)


@router.get("/{file_id}")
async def get_file(file_id: str, current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_institution_onboarding_reader_session)):
    return ok_response(await _safe_call(metadata(session, current_user.id, file_id)))


@router.post("/{file_id}/access")
async def post_file_access(
    file_id: str,
    payload: FileAccessRequest,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_institution_onboarding_reader_session),
    identity_session=Depends(get_db_session),
):
    reviewer = current_user.role == "super_admin"
    if reviewer:
        from app.modules.institution_onboarding.service import require_current_reviewer
        current = await _safe_call(require_current_reviewer(identity_session, current_user))
        if payload.reauth_password is None or not verify_password(
            payload.reauth_password.get_secret_value(), current["password_hash"]
        ):
            raise HTTPException(403, "PRIVATE_FILE_REAUTH_REQUIRED")
    expires = int(time.time()) + 300
    token = await _safe_call(authorize_file_access(
        session, current_user.id, file_id, payload.reason_code, expires,
        reviewer=reviewer,
    ))
    return ok_response({"file_id": file_id, "access_path": f"/api/v1/private-files/{file_id}/content?token={token}", "expires_at_epoch": expires})


@router.get("/{file_id}/content")
async def get_file_content(
    file_id: str,
    token: str = Query(min_length=32, max_length=2048),
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_institution_onboarding_reader_session),
    identity_session=Depends(get_db_session),
):
    reviewer = current_user.role == "super_admin"
    if reviewer:
        from app.modules.institution_onboarding.service import require_current_reviewer
        await _safe_call(require_current_reviewer(identity_session, current_user))
    data, mime_type = await _safe_call(read_authorized_content(
        session, current_user.id, file_id, token, reviewer=reviewer
    ))
    return Response(content=data, media_type=mime_type)


@router.delete("/{file_id}")
async def delete_file(file_id: str, current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_private_file_writer_session)):
    await _safe_call(delete_temporary(session, current_user.id, file_id))
    return ok_response({"file_id": file_id, "deleted": True})
