import pytest


pytestmark = pytest.mark.integration

CORE_TABLES = {
    "platform_org",
    "tenant",
    "user",
    "health_profile",
    "tenant_attachment",
    "tenant_review_log",
    "operation_log",
    "detection_report",
}


def test_core_tables_exist(pg_database):
    rows = pg_database.fetch_rows(
        """
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
          AND tablename = ANY($1::text[])
        """,
        list(CORE_TABLES),
    )

    assert {row["tablename"] for row in rows} == CORE_TABLES


def test_core_tables_have_expected_primary_keys(pg_database):
    rows = pg_database.fetch_rows(
        """
        SELECT tc.table_name, kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        WHERE tc.table_schema = 'public'
          AND tc.constraint_type = 'PRIMARY KEY'
          AND tc.table_name = ANY($1::text[])
        """,
        list(CORE_TABLES),
    )

    assert {(row["table_name"], row["column_name"]) for row in rows} == {
        ("platform_org", "id"),
        ("tenant", "id"),
        ("user", "id"),
        ("health_profile", "id"),
        ("tenant_attachment", "id"),
        ("tenant_review_log", "id"),
        ("operation_log", "id"),
        ("detection_report", "id"),
    }


def test_core_columns_have_expected_nullability_and_enum_types(pg_database):
    column_rows = pg_database.fetch_rows(
        """
        SELECT table_name, column_name, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = ANY($1::text[])
        """,
        list(CORE_TABLES),
    )
    nullable_by_column = {
        (row["table_name"], row["column_name"]): row["is_nullable"]
        for row in column_rows
    }

    expected_not_null_columns = {
        ("platform_org", "id"),
        ("platform_org", "org_name"),
        ("platform_org", "org_code"),
        ("platform_org", "org_type"),
        ("platform_org", "created_at"),
        ("platform_org", "updated_at"),
        ("tenant", "id"),
        ("tenant", "tenant_code"),
        ("tenant", "name"),
        ("tenant", "type"),
        ("tenant", "province"),
        ("tenant", "city"),
        ("tenant", "status"),
        ("tenant", "created_at"),
        ("tenant", "updated_at"),
        ("user", "id"),
        ("user", "phone"),
        ("user", "password_hash"),
        ("user", "role"),
        ("user", "created_at"),
        ("user", "updated_at"),
        ("health_profile", "id"),
        ("health_profile", "user_id"),
        ("health_profile", "gender"),
        ("health_profile", "birth_date"),
        ("health_profile", "created_at"),
        ("health_profile", "updated_at"),
        ("tenant_attachment", "id"),
        ("tenant_attachment", "file_type"),
        ("tenant_attachment", "file_url"),
        ("tenant_attachment", "created_at"),
        ("tenant_review_log", "id"),
        ("tenant_review_log", "tenant_id"),
        ("tenant_review_log", "action"),
        ("tenant_review_log", "created_at"),
        ("operation_log", "id"),
        ("operation_log", "module"),
        ("operation_log", "object_type"),
        ("operation_log", "action"),
        ("operation_log", "created_at"),
        ("detection_report", "id"),
        ("detection_report", "user_id"),
        ("detection_report", "report_type"),
        ("detection_report", "detection_time"),
        ("detection_report", "view_status"),
        ("detection_report", "report_schema_version"),
        ("detection_report", "report_data"),
        ("detection_report", "created_at"),
    }
    assert {
        column
        for column, is_nullable in nullable_by_column.items()
        if column in expected_not_null_columns and is_nullable == "NO"
    } == expected_not_null_columns

    enum_rows = pg_database.fetch_rows(
        """
        SELECT table_name, column_name, udt_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND (table_name, column_name) IN (
            ('tenant', 'status'),
            ('user', 'role'),
            ('user', 'status')
          )
        """
    )

    assert {
        (row["table_name"], row["column_name"], row["udt_name"])
        for row in enum_rows
    } == {
        ("tenant", "status", "tenant_status"),
        ("user", "role", "user_role"),
        ("user", "status", "user_status"),
    }


def test_core_foreign_keys_exist(pg_database):
    rows = pg_database.fetch_rows(
        """
        SELECT
          tc.table_name AS source_table,
          kcu.column_name AS source_column,
          ccu.table_name AS target_table,
          ccu.column_name AS target_column
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name = tc.constraint_name
         AND ccu.table_schema = tc.table_schema
        WHERE tc.table_schema = 'public'
          AND tc.constraint_type = 'FOREIGN KEY'
        """
    )

    assert {
        (row["source_table"], row["source_column"], row["target_table"], row["target_column"])
        for row in rows
    }.issuperset(
        {
            ("tenant", "org_id", "platform_org", "id"),
            ("tenant", "reviewed_by", "user", "id"),
            ("health_profile", "user_id", "user", "id"),
            ("tenant_attachment", "tenant_id", "tenant", "id"),
            ("tenant_review_log", "tenant_id", "tenant", "id"),
            ("tenant_review_log", "reviewer_id", "user", "id"),
            ("operation_log", "operator_id", "user", "id"),
            ("detection_report", "user_id", "user", "id"),
            ("detection_report", "store_id", "tenant", "id"),
        }
    )
