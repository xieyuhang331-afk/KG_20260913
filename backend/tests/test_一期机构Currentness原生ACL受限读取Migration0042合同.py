from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "app/migrations/versions/20260911_0042_机构当前性受限读取.py"
)
SIGNATURE = (
    "public.slice3_institution_currentness_authority_v1(BIGINT,BIGINT)"
)


def _source() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_0042线性继承已审R1候选且不修改历史Migration() -> None:
    source = _source()
    assert 'revision = "20260911_0042"' in source
    assert 'down_revision = "20260910_0041"' in source
    assert "20260910_0041_注册会员受限写入.py" not in source


def test_0042仅创建一个最小机构Currentness受限函数() -> None:
    source = _source()
    assert (
        "CREATE FUNCTION public.slice3_institution_currentness_authority_v1("
        in source
    )
    assert "actor_user_id BIGINT" in source
    assert "claimed_tenant_id BIGINT" in source
    for field in (
        "actor_current BOOLEAN",
        "institution_current BOOLEAN",
        "tenant_public_id UUID",
    ):
        assert field in source
    assert "SECURITY DEFINER VOLATILE" in source
    assert "SET search_path=pg_catalog,pg_temp" in source


def test_0042显式User后Tenant锁序并锁后校验退出删除() -> None:
    source = _source()
    user_lock = source.index('FROM public."user" u')
    tenant_lock = source.index("FROM public.tenant t")
    institution_read = source.index("FROM public.institution_application a")
    assert user_lock < tenant_lock < institution_read
    assert "FOR SHARE OF u" in source
    assert "FOR SHARE OF t" in source
    assert "FOR SHARE OF a" not in source
    assert "FOR UPDATE OF a" not in source
    assert "value_exited_at IS NOT NULL" in source
    assert "value_deletion_requested_at IS NOT NULL" in source
    assert "value_user_tenant IS DISTINCT FROM claimed_tenant_id" in source


def test_0042机构读取保持MVCC且多行不变量FailClosed() -> None:
    source = _source()
    assert "status='APPROVED'" in source
    assert "tenant_public_id IS NOT NULL" in source
    assert "WHEN NO_DATA_FOUND THEN" in source
    assert "WHEN TOO_MANY_ROWS THEN" in source
    assert "USING ERRCODE='21000'" in source


def test_0042只授权Application执行且不授予基础表权限() -> None:
    source = _source()
    assert "REVOKE ALL ON FUNCTION {_SIGNATURE} FROM PUBLIC" in source
    assert "GRANT EXECUTE ON FUNCTION {_SIGNATURE}" in source
    assert "session_user <>" in source
    assert "USING ERRCODE='42501'" in source
    assert "GRANT SELECT" not in source
    assert "GRANT INSERT" not in source
    assert "GRANT UPDATE" not in source
    assert "GRANT DELETE" not in source
    assert "GRANT USAGE ON SEQUENCE" not in source


def test_0042降级只撤销执行并删除精确函数() -> None:
    source = _source()
    downgrade = source[source.index("def downgrade()") :]
    assert "REVOKE EXECUTE ON FUNCTION {_SIGNATURE}" in downgrade
    assert "REVOKE ALL ON FUNCTION {_SIGNATURE} FROM PUBLIC" in downgrade
    assert "DROP FUNCTION {_SIGNATURE}" in downgrade
    for protected in (
        "slice2_institution_identity_authority_v1",
        "auth_user_currentness_v1",
        "registration_create_member_v1",
    ):
        assert protected not in downgrade
