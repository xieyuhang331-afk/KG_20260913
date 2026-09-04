from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from app.core.database import (
    get_db_session,
    get_institution_onboarding_reader_session,
    get_private_file_access_writer_session,
    get_private_file_writer_session,
    get_slice4_clinical_reader_session,
    get_slice4_institution_reader_session,
    get_slice7_transfer_writer_session,
)
from app.core.responses import ok_response
from app.core.security import CurrentUser, get_current_user_from_jwt
from app.modules.auth.service import verify_password
from app.modules.private_file.schemas import (
    FileAccessRequest,
    PrivateFileAccessResponse,
    PrivateFileDeleteResult,
    PrivateFileEnvelope,
    PrivateFileInitiated,
    PrivateFileMetadata,
    PrivateFileUploadResult,
    UploadCompleteRequest,
    UploadInitiateRequest,
)
from app.modules.private_file.service import (
    authorize_file_access,
    complete_upload,
    delete_temporary,
    initiate_upload,
    metadata,
    read_authorized_content,
    upload_content,
)

router = APIRouter(prefix="/api/v1/private-files", tags=["private_file"])
_ACCESS_WRITER_DEPENDENCY = Depends(get_private_file_access_writer_session)

_PRIVATE_HEADERS = {
    "Cache-Control": "no-store, private, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def _set_private_headers(response: Response) -> None:
    for name, value in _PRIVATE_HEADERS.items():
        response.headers[name] = value


def _object_store(request: Request):
    store = getattr(request.app.state, "private_object_store", None)
    if store is None:
        raise HTTPException(503, "PRIVATE_FILE_STORAGE_UNAVAILABLE")
    return store


def _report_access_context(current_user: CurrentUser) -> str:
    return {
        "member": "FAMILY",
        "therapist": "THERAPIST",
        "super_admin": "PLATFORM",
    }.get(current_user.role, "INSTITUTION")


async def _safe_call(awaitable):
    try:
        return await awaitable
    except asyncio.CancelledError:
        raise
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "PRIVATE_FILE_PERSISTENCE_UNAVAILABLE") from None


@router.post("/uploads", response_model=PrivateFileEnvelope[PrivateFileInitiated])
async def post_upload(payload: UploadInitiateRequest, response: Response, current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_private_file_writer_session)):
    _set_private_headers(response)
    return ok_response(await _safe_call(initiate_upload(session, current_user.id, payload)))


@router.put(
    "/uploads/{file_id}/content",
    response_model=PrivateFileEnvelope[PrivateFileUploadResult],
    responses={413: {"description": "PRIVATE_FILE_SIZE_LIMIT_EXCEEDED"}},
)
async def put_upload_content(file_id: str, request: Request, response: Response, current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_private_file_writer_session)):
    _set_private_headers(response)
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > 10 * 1024 * 1024:
                raise HTTPException(413, "PRIVATE_FILE_SIZE_LIMIT_EXCEEDED")
        except ValueError:
            raise HTTPException(422, "PRIVATE_FILE_CONTENT_LENGTH_INVALID") from None
    await _safe_call(upload_content(
        session,
        current_user.id,
        file_id,
        request.stream(),
        object_store=_object_store(request),
    ))
    return ok_response({"file_id": file_id, "uploaded": True})


@router.post("/uploads/{file_id}/complete", response_model=PrivateFileEnvelope[PrivateFileUploadResult])
async def post_upload_complete(file_id: str, payload: UploadCompleteRequest, request: Request, response: Response, current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_private_file_writer_session)):
    _set_private_headers(response)
    result = await _safe_call(complete_upload(
        session,
        current_user.id,
        file_id,
        payload,
        object_store=_object_store(request),
    ))
    from app.tasks.institution_onboarding_tasks import scan_private_file_task
    dispatch_required = bool(result.pop("dispatch_required", False))
    if dispatch_required:
        try:
            scan_private_file_task.apply_async(args=(file_id,), queue="private-file")
        except Exception:
            result["dispatch_pending"] = True
    return ok_response(result)


@router.get("/{file_id}", response_model=PrivateFileEnvelope[PrivateFileMetadata])
async def get_file(file_id: str, response: Response, current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_institution_onboarding_reader_session)):
    _set_private_headers(response)
    return ok_response(await _safe_call(metadata(session, current_user.id, file_id)))


@router.post("/{file_id}/access", response_model=PrivateFileEnvelope[PrivateFileAccessResponse])
async def post_file_access(
    file_id: str,
    payload: FileAccessRequest,
    request: Request,
    response: Response,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_institution_onboarding_reader_session),
    access_writer_session=_ACCESS_WRITER_DEPENDENCY,
    identity_session=Depends(get_db_session),
    report_authority_session=Depends(get_slice4_clinical_reader_session),
    report_institution_session=Depends(get_slice4_institution_reader_session),
):
    _set_private_headers(response)
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
        session, access_writer_session, current_user.id, file_id,
        payload.reason_code, expires,
        reviewer=reviewer,
        report_authority_session=(
            report_institution_session
            if current_user.role in {"org_admin", "org_operator"}
            else report_authority_session
        ),
        report_access_context=_report_access_context(current_user),
        object_store=_object_store(request),
    ))
    return ok_response({
        "file_id": file_id,
        "content_path": f"/api/v1/private-files/{file_id}/content",
        "access_credential": token,
        "expires_at_epoch": expires,
    })


@router.get(
    "/{file_id}/content",
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def get_file_content(
    file_id: str,
    request: Request,
    access_credential: str | None = Header(
        default=None, alias="X-Private-File-Access"
    ),
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_institution_onboarding_reader_session),
    access_writer_session=_ACCESS_WRITER_DEPENDENCY,
    identity_session=Depends(get_db_session),
    report_authority_session=Depends(get_slice4_clinical_reader_session),
    report_institution_session=Depends(get_slice4_institution_reader_session),
    export_access_session=Depends(get_slice7_transfer_writer_session),
):
    if access_credential is None:
        raise HTTPException(403, "PRIVATE_FILE_ACCESS_INVALID")
    reviewer = current_user.role == "super_admin"
    if reviewer:
        from app.modules.institution_onboarding.service import require_current_reviewer
        await _safe_call(require_current_reviewer(identity_session, current_user))
    async def consume_export_access(values: dict) -> bool:
        consumer = getattr(
            request.app.state, "slice7_export_download_consumer", None
        )
        if not callable(consumer):
            raise HTTPException(503, "PRIVATE_FILE_ACCESS_UNAVAILABLE")
        return bool(await consumer(
            export_access_session,
            {
                "access_id": values["token_id"],
                "private_file_id": file_id,
                "actor_user_id": current_user.id,
                "evidence_digest": values["evidence_digest"],
                "consumed_at": datetime.now(timezone.utc),
            },
        ))

    stream, mime_type = await _safe_call(read_authorized_content(
        session, access_writer_session, current_user.id, file_id,
        access_credential, reviewer=reviewer,
        report_authority_session=(
            report_institution_session
            if current_user.role in {"org_admin", "org_operator"}
            else report_authority_session
        ),
        report_access_context=_report_access_context(current_user),
        export_access_consumer=consume_export_access,
        object_store=_object_store(request),
    ))
    return StreamingResponse(
        stream,
        media_type=mime_type,
        headers={
            **_PRIVATE_HEADERS,
            "Content-Disposition": "attachment",
            "Vary": "Authorization, X-Private-File-Access",
        },
    )


@router.delete("/{file_id}", response_model=PrivateFileEnvelope[PrivateFileDeleteResult])
async def delete_file(file_id: str, request: Request, response: Response, current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_private_file_writer_session)):
    _set_private_headers(response)
    await _safe_call(delete_temporary(
        session,
        current_user.id,
        file_id,
        object_store=_object_store(request),
    ))
    return ok_response({"file_id": file_id, "deleted": True})
