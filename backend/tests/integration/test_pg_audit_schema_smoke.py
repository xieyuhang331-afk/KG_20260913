import pytest


pytestmark = pytest.mark.integration

AUDIT_TABLES = {"tenant_review_log", "operation_log"}


def test_audit_tables_have_expected_columns(pg_database):
    rows = pg_database.fetch_rows(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = ANY($1::text[])
        """,
        list(AUDIT_TABLES),
    )

    assert {(row["table_name"], row["column_name"]) for row in rows} == {
        ("tenant_review_log", "id"),
        ("tenant_review_log", "tenant_id"),
        ("tenant_review_log", "reviewer_id"),
        ("tenant_review_log", "action"),
        ("tenant_review_log", "grade"),
        ("tenant_review_log", "comment"),
        ("tenant_review_log", "created_at"),
        ("operation_log", "id"),
        ("operation_log", "operator_id"),
        ("operation_log", "module"),
        ("operation_log", "object_type"),
        ("operation_log", "object_id"),
        ("operation_log", "action"),
        ("operation_log", "payload"),
        ("operation_log", "created_at"),
    }


def test_audit_tables_have_expected_primary_keys(pg_database):
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
        list(AUDIT_TABLES),
    )

    assert {(row["table_name"], row["column_name"]) for row in rows} == {
        ("tenant_review_log", "id"),
        ("operation_log", "id"),
    }


def test_audit_columns_have_expected_nullability_and_types(pg_database):
    rows = pg_database.fetch_rows(
        """
        SELECT table_name, column_name, udt_name, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = ANY($1::text[])
        """,
        list(AUDIT_TABLES),
    )
    columns = {
        (row["table_name"], row["column_name"]): row
        for row in rows
    }

    expected_not_null = {
        ("tenant_review_log", "id"),
        ("tenant_review_log", "tenant_id"),
        ("tenant_review_log", "action"),
        ("tenant_review_log", "created_at"),
        ("operation_log", "id"),
        ("operation_log", "module"),
        ("operation_log", "object_type"),
        ("operation_log", "action"),
        ("operation_log", "created_at"),
    }
    assert {
        column
        for column, row in columns.items()
        if column in expected_not_null and row["is_nullable"] == "NO"
    } == expected_not_null

    expected_nullable = {
        ("tenant_review_log", "reviewer_id"),
        ("tenant_review_log", "grade"),
        ("tenant_review_log", "comment"),
        ("operation_log", "operator_id"),
        ("operation_log", "object_id"),
        ("operation_log", "payload"),
    }
    assert {
        column
        for column, row in columns.items()
        if column in expected_nullable and row["is_nullable"] == "YES"
    } == expected_nullable

    assert columns[("operation_log", "payload")]["udt_name"] == "jsonb"


def test_audit_foreign_keys_exist(pg_database):
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
          AND tc.table_name = ANY($1::text[])
        """,
        list(AUDIT_TABLES),
    )

    assert {
        (row["source_table"], row["source_column"], row["target_table"], row["target_column"])
        for row in rows
    } == {
        ("tenant_review_log", "tenant_id", "tenant", "id"),
        ("tenant_review_log", "reviewer_id", "user", "id"),
        ("operation_log", "operator_id", "user", "id"),
    }


def test_tenant_review_log_action_check_constraint_exists(pg_database):
    rows = pg_database.fetch_rows(
        """
        SELECT conname, pg_get_constraintdef(oid) AS definition
        FROM pg_constraint
        WHERE conrelid = 'tenant_review_log'::regclass
          AND contype = 'c'
        """
    )

    definitions = [row["definition"] for row in rows]

    assert any("approved" in definition and "rejected" in definition for definition in definitions)


def test_audit_indexes_exist(pg_database):
    rows = pg_database.fetch_rows(
        """
        SELECT indexname
        FROM pg_indexes
        WHERE schemaname = 'public'
          AND tablename = ANY($1::text[])
        """,
        list(AUDIT_TABLES),
    )

    assert {row["indexname"] for row in rows}.issuperset(
        {
            "idx_tenant_review_log_tenant",
            "idx_tenant_review_log_reviewer",
            "idx_operation_log_module_object",
            "idx_operation_log_operator",
        }
    )
