from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "app"
    / "migrations"
    / "versions"
    / "20260914_0047_R4旧健康接口受限兼容边界.py"
)

FUNCTIONS = (
    "r4_member_health_currentness_v1",
    "r4_member_legacy_health_profile_read_v1",
    "r4_member_legacy_health_profile_create_v1",
    "r4_member_legacy_health_profile_update_v1",
    "r4_member_self_health_indicator_history_v1",
    "r4_member_legacy_health_indicator_create_v1",
    "r4_member_legacy_health_indicator_history_v1",
    "r4_member_legacy_health_indicator_latest_v1",
    "r4_member_self_detection_report_history_v1",
    "r4_member_self_detection_report_read_v1",
)
SIGNATURES = (
    "public.r4_member_health_currentness_v1(BIGINT)",
    "public.r4_member_legacy_health_profile_read_v1(BIGINT,BIGINT)",
    "public.r4_member_legacy_health_profile_create_v1(BIGINT,BIGINT,VARCHAR,DATE,NUMERIC,NUMERIC,VARCHAR,JSONB,JSONB,JSONB,VARCHAR,VARCHAR,JSONB,VARCHAR,VARCHAR,TIMESTAMPTZ)",
    "public.r4_member_legacy_health_profile_update_v1(BIGINT,BIGINT,TIMESTAMPTZ,VARCHAR,DATE,NUMERIC,NUMERIC,VARCHAR,TIMESTAMPTZ)",
    "public.r4_member_self_health_indicator_history_v1(BIGINT,VARCHAR,TIMESTAMPTZ,TIMESTAMPTZ,TIMESTAMPTZ,BIGINT,INTEGER)",
    "public.r4_member_legacy_health_indicator_create_v1(BIGINT,BIGINT,VARCHAR,VARCHAR,NUMERIC,VARCHAR,VARCHAR,TIMESTAMPTZ)",
    "public.r4_member_legacy_health_indicator_history_v1(BIGINT,BIGINT,VARCHAR,TIMESTAMPTZ,TIMESTAMPTZ,INTEGER)",
    "public.r4_member_legacy_health_indicator_latest_v1(BIGINT,BIGINT)",
    "public.r4_member_self_detection_report_history_v1(BIGINT,VARCHAR,TIMESTAMPTZ,TIMESTAMPTZ,TIMESTAMPTZ,BIGINT,INTEGER)",
    "public.r4_member_self_detection_report_read_v1(BIGINT,BIGINT)",
)


def test_R4使用0047线性Migration并冻结10个最小受限函数() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assignments = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"revision", "down_revision"}
    }
    assert assignments == {
        "revision": "20260914_0047",
        "down_revision": "20260914_0046",
    }
    assert all(f"CREATE FUNCTION public.{name}(" in source for name in FUNCTIONS)
    assert source.count("SECURITY DEFINER") == len(FUNCTIONS)
    assert source.count("SET search_path = pg_catalog") == len(FUNCTIONS)


def test_R4函数只授予Application执行且不扩大旧表或Sequence权限() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "GRANT EXECUTE ON FUNCTION" in source
    assert "REVOKE ALL ON FUNCTION" in source
    assert " FROM PUBLIC" in source
    forbidden = (
        "GRANT SELECT ON TABLE public.health_profile",
        "GRANT SELECT ON TABLE public.health_indicator",
        "GRANT SELECT ON TABLE public.detection_report",
        "GRANT INSERT ON TABLE public.health_profile",
        "GRANT INSERT ON TABLE public.health_indicator",
        "GRANT USAGE ON SEQUENCE public.health_profile_id_seq",
        "GRANT USAGE ON SEQUENCE public.health_indicator_id_seq",
        "EXECUTE FORMAT(",
        "EXECUTE IMMEDIATE",
    )
    upper = source.upper()
    assert all(item.upper() not in upper for item in forbidden)


def test_R4兼容函数固定在旧表且不得写入正式健康真相对象() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "public.health_profile" in source
    assert "public.health_indicator" in source
    assert "public.detection_report" in source
    forbidden_targets = (
        "INSERT INTO public.health_profile_revision",
        "INSERT INTO public.canonical_health_fact",
        "INSERT INTO public.detection_report_attachment",
        "INSERT INTO public.assessment_input_assembly",
        "INSERT INTO public.assessment_readiness",
    )
    assert all(target not in source for target in forbidden_targets)


def test_R4函数签名输出和页面上限均为固定合同() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    tree = ast.parse(source)
    signature_assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "_SIGNATURES"
    )
    assert ast.literal_eval(signature_assignment.value) == SIGNATURES
    assert "id BIGINT,\n  role public.user_role" in source
    assert "id BIGINT,user_id BIGINT,gender VARCHAR(5),birth_date DATE" in source
    assert "is_initial_baseline BOOLEAN,report_data JSONB" in source
    assert "p_limit > 201" in source
    assert "p_limit > 200" in source
    assert "p_limit > 101" in source


def test_R4档案写入共享固定事务锁域且不升级User行锁() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert source.count("'r4-legacy-health-profile:' || p_actor_user_id::TEXT,47") == 2
    assert source.count("pg_catalog.pg_advisory_xact_lock(") == 2
    assert "FOR UPDATE OF actor" not in source


def test_R4降级必须在撤权和删函数前完成旧数据非空预检() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    downgrade = source[source.index("def downgrade() -> None:") :]
    assert "_assert_empty_for_downgrade()" in downgrade
    assert downgrade.index("_assert_empty_for_downgrade()") < downgrade.index(
        "REVOKE EXECUTE ON FUNCTION"
    )
    assert all(
        f"public.{table_name}" in source
        for table_name in ("health_profile", "health_indicator", "detection_report")
    )
