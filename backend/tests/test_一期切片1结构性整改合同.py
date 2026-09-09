from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
ONBOARDING_SERVICE = BACKEND / "app/modules/institution_onboarding/service.py"
ONBOARDING_REPOSITORY = BACKEND / "app/modules/institution_onboarding/repository.py"
ONBOARDING_API = BACKEND / "app/modules/institution_onboarding/api.py"
PRIVATE_SERVICE = BACKEND / "app/modules/private_file/service.py"
PRIVATE_PORTS = BACKEND / "app/modules/private_file/ports.py"
PRIVATE_API = BACKEND / "app/modules/private_file/api.py"
PRIVATE_MODELS = BACKEND / "app/modules/private_file/models.py"
TASKS = BACKEND / "app/tasks/institution_onboarding_tasks.py"
MIGRATION = (
    BACKEND
    / "app/migrations/versions/20260816_0020_phase1_slice1_controlled_institution_onboarding.py"
)
DATABASE_TEST = BACKEND / "tests/integration/test_一期切片1机构受控入驻数据库闭环.py"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_C1_批准只从审核中状态进入且重验revision与材料():
    source = _source(ONBOARDING_SERVICE)
    assert "domain.approve(" in source
    assert "require_current_revision" in source
    assert "require_clean_bound_files" in source
    assert "domain._expect(expected, domain.status)" not in source


def test_C2_扫描器只能通过Port显式注入且生产代码无测试判定():
    ports = _source(PRIVATE_PORTS)
    service = _source(PRIVATE_SERVICE)
    assert "class PrivateFileScanner(Protocol)" in ports
    assert "scanner: PrivateFileScanner" in service
    assert "EICAR-STANDARD-ANTIVIRUS-TEST-FILE" not in service
    assert "PRIVATE_FILE_SCANNER_UNAVAILABLE" in service


def test_C3_PII读取和审批均执行审核员currentness校验():
    repository = _source(ONBOARDING_REPOSITORY)
    api = _source(ONBOARDING_API)
    service = _source(ONBOARDING_SERVICE)
    assert "reviewer_currentness" in repository
    assert api.count("require_current_reviewer") >= 3
    assert "require_current_reviewer" in service


def test_C4_downgrade撤销0020对既有对象增加的全部权限():
    source = _source(MIGRATION)
    downgrade = source.split("def downgrade() -> None:", 1)[1]
    for fragment in (
        'REVOKE INSERT ("phone", "password_hash", "role", "status", "tenant_id")',
        'REVOKE UPDATE ("tenant_id")',
        'REVOKE INSERT ("org_id", "tenant_code"',
        'REVOKE USAGE, SELECT ON SEQUENCE public.user_id_seq',
        'REVOKE USAGE, SELECT ON SEQUENCE public.tenant_id_seq',
    ):
        assert fragment in downgrade


def test_I1_补正重提是单事务且幂等摘要覆盖原请求():
    source = _source(ONBOARDING_SERVICE)
    body = source.split("async def resubmit_application", 1)[1]
    assert "await save_draft(" not in body
    assert "_canonical_payload(payload)" in body
    assert body.count("await session.commit()") == 0
    assert "_commit_operation(" in body


def test_I2_文件按确定顺序锁定并在锁后重验全部绑定条件():
    repository = _source(ONBOARDING_REPOSITORY)
    service = _source(ONBOARDING_SERVICE)
    assert "lock_files_for_binding" in repository
    assert ".order_by(PrivateFileModel.file_id).with_for_update()" in repository
    assert "PRIVATE_FILE_BIND_CONFLICT" in service
    assert "async def clean_file_purposes" not in repository


def test_I3_短时访问token存在验签和受控内容端点():
    api = _source(PRIVATE_API)
    service = _source(PRIVATE_SERVICE)
    assert '"/{file_id}/content"' in api
    content = api.split("async def get_file_content", 1)[1].split("@router", 1)[0]
    assert 'alias="X-Private-File-Access"' in content
    assert "Query(" not in content
    assert "_consume_access" in service
    assert "read_authorized_content" in service
    assert "hmac.compare_digest" in service


def test_I4_扫描恢复重试与孤儿清理均有可执行入口():
    tasks = _source(TASKS)
    repository = _source(ONBOARDING_REPOSITORY) + _source(
        BACKEND / "app/modules/private_file/repository.py"
    )
    assert "self.retry(" in tasks
    assert "recover_pending_scan_tasks" in tasks
    assert "cleanup_orphan_private_files" in tasks
    assert "pending_scan_ids" in repository
    assert "expired_orphan_ids" in repository


def test_I5_idempotent_mutation在业务锁内重放且未知提交只读确认():
    source = _source(ONBOARDING_SERVICE)
    assert "pg_advisory_xact_lock" in _source(ONBOARDING_REPOSITORY)
    assert "async def _commit_operation" in source
    assert "ONBOARDING_COMMIT_OUTCOME_UNKNOWN" in source
    assert "ONBOARDING_COMMIT_ROLLED_BACK" in source
    assert "async def _commit_draft" in source
    assert "_draft_postimage(persisted) == expected_postimage" in source


def test_I6_0020与ORM包含核心命名外键():
    migration = _source(MIGRATION)
    models = _source(BACKEND / "app/modules/institution_onboarding/models.py")
    private_models = _source(PRIVATE_MODELS)
    for name in (
        "fk_institution_account_invitation",
        "fk_institution_application_invitation",
        "fk_institution_revision_application",
        "fk_institution_license_application",
        "fk_institution_license_private_file",
        "fk_private_file_bound_application",
    ):
        assert name in migration
    assert "ForeignKey(" in models
    assert "ForeignKey(" in private_models


def test_I6_ORM核心外键在动态Core_Metadata中可解析():
    from app.core.sqlalchemy_mapping import map_core_model_classes
    from app.modules.institution_onboarding.models import (
        InstitutionApplicationModel,
        InstitutionOnboardingAccountModel,
    )
    from app.modules.private_file.models import PrivateFileModel

    map_core_model_classes()
    columns = (
        InstitutionOnboardingAccountModel.__table__.c.user_id,
        InstitutionApplicationModel.__table__.c.applicant_user_id,
        InstitutionApplicationModel.__table__.c.tenant_internal_id,
        PrivateFileModel.__table__.c.owner_user_id,
    )
    assert [next(iter(column.foreign_keys)).column.name for column in columns] == [
        "id", "id", "id", "id",
    ]


def test_I7_异常翻译固定脱敏且Cancellation优先():
    onboarding = _source(ONBOARDING_SERVICE)
    private = _source(PRIVATE_SERVICE)
    tasks = _source(TASKS)
    assert "async def _safe_rollback" in onboarding
    assert "except asyncio.CancelledError:" in onboarding
    assert "ONBOARDING_PERSISTENCE_UNAVAILABLE" in onboarding
    assert "PRIVATE_FILE_PERSISTENCE_UNAVAILABLE" in private
    assert "PRIVATE_FILE_WORKER_UNAVAILABLE" in tasks
    assert "ONBOARDING_OUTBOX_DISPATCH_UNAVAILABLE" in tasks
    assert "ONBOARDING_OUTBOX_CONSUMER_UNAVAILABLE" in tasks
    assert "ONBOARDING_OUTBOX_FAILURE_RECORD_UNAVAILABLE" in tasks


def test_I8_文件读取使用只读身份且所有文件状态提交均可fresh确认():
    api = _source(PRIVATE_API)
    service = _source(PRIVATE_SERVICE)
    access = api.split("async def post_file_access", 1)[1].split("@router", 1)[0]
    content = api.split("async def get_file_content", 1)[1].split("@router", 1)[0]
    assert "get_institution_onboarding_reader_session" in access
    assert "get_private_file_writer_session" not in access
    assert "get_institution_onboarding_reader_session" in content
    assert "get_private_file_writer_session" not in content
    assert "async def _commit_private_file" in service
    assert "PRIVATE_FILE_COMMIT_OUTCOME_UNKNOWN" in service
    assert "PRIVATE_FILE_COMMIT_ROLLED_BACK" in service


def test_I9_Outbox具备claim投递重试死信与恢复入口():
    repository = _source(ONBOARDING_REPOSITORY)
    tasks = _source(TASKS)
    ports = _source(BACKEND / "app/modules/institution_onboarding/ports.py")
    models = _source(BACKEND / "app/modules/institution_onboarding/models.py")
    assert "claim_dispatchable_outbox" in repository
    assert 'row.status = "FAILED" if row.attempts >= 3 else "PENDING"' in tasks
    assert "dispatch_institution_outbox_task" in tasks
    assert "ONBOARDING_OUTBOX_DELIVERY_UNAVAILABLE" in tasks
    assert "class InstitutionApprovalDeliveryPort(Protocol)" in ports
    assert "InstitutionOnboardingDeliveryModel" in models
    assert "confirm_outbox_delivery" in tasks
    assert "delivery = await delivery_port.deliver" in tasks
    assert "_delivery_matches(" in tasks
    assert "row.attempts = 3" in tasks
    assert 'status == "PROCESSING"' in repository
    assert "processing_at <= stale_before" in repository
    assert "timedelta(minutes=5)" in tasks


def test_I10_SERVICE_READY在ORM_DDL与列级权限三层均不可由审核员开启():
    migration = _source(MIGRATION)
    models = _source(BACKEND / "app/modules/institution_onboarding/models.py")
    assert "service_ready = false" in migration
    assert "service_ready = false" in models
    reviewer_update = migration.split('_grant_columns(connection, reviewer, "UPDATE", "institution_application"', 1)[1].split("\n", 1)[0]
    assert "service_ready" not in reviewer_update


def test_M1_数据库权限合同不存在恒真断言():
    source = _source(DATABASE_TEST)
    assert "is None or True" not in source
    assert "has_column_privilege" in source


def test_M2_ORM文件大小约束与Migration括号语义一致():
    source = _source(PRIVATE_MODELS)
    assert (
        'declared_size BETWEEN 1 AND 10485760 AND '
        '(actual_size IS NULL OR actual_size BETWEEN 1 AND 10485760)'
        in source
    )


def test_最终_C1_审核员旧JWT不得携带tenant或org_scope():
    source = _source(ONBOARDING_SERVICE)
    body = source.split("async def require_current_reviewer", 1)[1].split("async def", 1)[0]
    assert "actor.tenant_id is not None" in body
    assert "actor.org_id is not None" in body


def test_最终_I1_Outbox_dispatch_commit未知必须fresh确认且PENDING不得死信():
    source = _source(TASKS)
    assert "async def confirm_outbox_dispatch" in source
    assert "ONBOARDING_OUTBOX_DISPATCH_OUTCOME_UNKNOWN" in source
    failed = source.split("async def _mark_outbox_failed", 1)[1].split("@celery_app.task", 1)[0]
    assert 'row.status == "PROCESSING"' in failed
    assert "row.attempts >= 3" in failed


def test_最终_I2_动态入驻org_admin登录从tenant解析org_id():
    source = _source(BACKEND / "app/modules/auth/service.py")
    assert "async def _tenant_org_id" in source
    assert "dynamic_org_id" in source


def test_最终_I3_邀请行政节点必须是active_canonical_county且有FK():
    repository = _source(ONBOARDING_REPOSITORY)
    service = _source(ONBOARDING_SERVICE)
    migration = _source(MIGRATION)
    assert "active_canonical_county" in repository
    assert "ONBOARDING_ADMINISTRATIVE_REGION_INVALID" in service
    assert "fk_institution_invitation_administrative_region" in migration


def test_最终_I4_草稿提交重提的领域冲突固定映射409():
    source = _source(ONBOARDING_SERVICE)
    assert source.count("raise _translate_domain_error(exc) from None") >= 5


def test_最终_I5_补正重提可原子替换材料():
    service = _source(ONBOARDING_SERVICE)
    repository = _source(ONBOARDING_REPOSITORY)
    frontend = _source(
        BACKEND.parent
        / "frontend/src/domains/institution/pages/ControlledOnboardingPage.tsx"
    )
    assert "licenses_for_update" in repository
    assert "old_file.bound_application_id = None" in service
    assert "license_row.private_file_id =" in service
    assert 'status === "NEEDS_CORRECTION"' in frontend
    assert "uploadCleanFile(" in frontend.split('status === "NEEDS_CORRECTION"', 1)[1]


def test_最终_I6_private_file最小列投影与权限对齐():
    repository = _source(ONBOARDING_REPOSITORY)
    migration = _source(MIGRATION)
    assert "load_only(" in repository
    assert '_grant_columns(connection, onboarding, "SELECT", "private_file", file_binding_columns)' in migration
    assert '_grant_columns(connection, reviewer, "SELECT", "private_file", file_review_columns)' in migration
    assert '_grant_columns(connection, reviewer, "SELECT", "institution_invitation", invitation_review_columns)' in migration


def test_最终_I7_DELETED孤儿仍可被周期物理清理():
    repository = _source(BACKEND / "app/modules/private_file/repository.py")
    assert 'PrivateFileModel.status != "DELETED"' not in repository
    assert "case(" in repository


def test_最终_补正只允许替换审核明确点名的证照类型():
    service = _source(ONBOARDING_SERVICE)
    assert "LICENSE_TYPE_TO_CORRECTION_FIELD" in service
    assert "ONBOARDING_CORRECTION_FIELD_FORBIDDEN" in service.split(
        "async def _submit_existing", 1
    )[1]


def test_最终_邀请行政区锁定并验证完整四级canonical链():
    repository = _source(ONBOARDING_REPOSITORY)
    service = _source(ONBOARDING_SERVICE)
    assert "parent_id" in repository.split("active_canonical_county", 1)[1]
    assert 'expected_types = ("county", "city", "province", "headquarter")' in repository
    assert ".with_for_update(read=True)" in repository.split(
        "active_canonical_county", 1
    )[1]
    approval = service.split("async def review_decision", 1)[1]
    assert "currentness_session" in approval
    assert "active_canonical_county(invitation.administrative_region_id)" in approval
    assert "ONBOARDING_ADMINISTRATIVE_REGION_INVALID" in approval


def test_最终_DELETED物理残留优先于普通孤儿重试():
    repository = _source(BACKEND / "app/modules/private_file/repository.py")
    assert 'case((PrivateFileModel.status == "DELETED", 0), else_=1)' in repository


def test_最终_I8_Tenant核心字段使用受控密文恢复原值():
    source = _source(ONBOARDING_SERVICE)
    assert 'values["credit_code_ciphertext"]' in source
    assert 'values["contact_phone_ciphertext"]' in source
    assert 'tenant.credit_code = secrets_box.decrypt(' in source
    assert 'tenant.contact_phone = secrets_box.decrypt(' in source


def test_最终_M1_前端API使用显式Slice1类型而非unknown容器():
    institution = _source(
        BACKEND.parent / "frontend/src/domains/institution/api.ts"
    )
    platform = _source(BACKEND.parent / "frontend/src/domains/platform/api.ts")
    assert "payload: unknown" not in institution + platform
    assert "Record<string, unknown>" not in institution + platform


def test_最终_I9_邀请撤销与重发接入服务API并保持幂等审计():
    service = _source(ONBOARDING_SERVICE)
    api = _source(ONBOARDING_API)
    schemas = _source(BACKEND / "app/modules/institution_onboarding/schemas.py")
    assert "class InvitationResendRequest" in schemas
    assert "class InvitationRevokeRequest" in schemas
    assert "async def resend_invitation" in service
    assert "async def revoke_invitation" in service
    assert 'operation="INVITATION_RESEND"' in service
    assert 'operation="INVITATION_REVOKE"' in service
    assert (
        '@platform_router.post("/institution-invitations/{invitation_id}/resend", '
        "response_model=OnboardingSuccessEnvelope[InvitationIssuedDTO])" in api
    )
    assert (
        '@platform_router.post("/institution-invitations/{invitation_id}/revoke", '
        "response_model=OnboardingSuccessEnvelope[InvitationRevokedDTO])" in api
    )
    resend = service.split("async def resend_invitation", 1)[1].split(
        "async def revoke_invitation", 1
    )[0]
    revoke = service.split("async def revoke_invitation", 1)[1].split(
        "async def activate", 1
    )[0]
    assert 'scope = f"{actor.id}:{invitation_id}"' in resend
    assert 'scope = f"{actor.id}:{invitation_id}"' in revoke
    assert "_secure_invitation_response" in service
    assert "_public_invitation_response" in service
    assert '"short_code_ciphertext"' in service


def test_最终_I10_审核材料访问要求当前密码再认证且前端带JWT取Blob():
    private_api = _source(PRIVATE_API)
    private_schema = _source(BACKEND / "app/modules/private_file/schemas.py")
    repository = _source(ONBOARDING_REPOSITORY)
    platform_api = _source(BACKEND.parent / "frontend/src/domains/platform/api.ts")
    review_page = _source(
        BACKEND.parent / "frontend/src/domains/platform/pages/InstitutionReviewPage.tsx"
    )
    assert "reauth_password" in private_schema
    assert "verify_password" in private_api
    assert "password_hash" in repository.split("reviewer_currentness", 1)[1]
    assert "getAccessToken" in platform_api
    assert "fetchPrivateFileContent" in platform_api
    assert "URL.createObjectURL" in review_page
    assert "window.open(access.access_path" not in review_page


def test_最终_I11_审核详情只返回显式解密业务字段():
    service = _source(ONBOARDING_SERVICE)
    api = _source(ONBOARDING_API)
    review_page = _source(
        BACKEND.parent / "frontend/src/domains/platform/pages/InstitutionReviewPage.tsx"
    )
    assert "def review_draft_projection" in service
    assert '"credit_code"' in service.split("def review_draft_projection", 1)[1]
    assert '"contact_phone"' in service.split("def review_draft_projection", 1)[1]
    detail_body = api.split("async def get_review", 1)[1].split(
        "@platform_router.post", 1
    )[0]
    assert '"draft": row.draft_payload' not in detail_body
    assert "review_draft_projection(dict(row.draft_payload))" in detail_body
    assert "Object.entries(detail.draft" not in review_page


def test_最终_I12_已绑定租户的动态机构claim优先于静态映射():
    source = _source(BACKEND / "app/modules/auth/service.py")
    body = source.split("def _build_login_claims", 1)[1].split("async def", 1)[0]
    assert body.index("user.tenant_id is not None") < body.index(
        'context.get("org_id")'
    )
