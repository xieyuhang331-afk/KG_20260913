import pytest


pytestmark = pytest.mark.integration


HEALTH_PROFILE_COLUMNS = {
    "id",
    "user_id",
    "gender",
    "birth_date",
    "height",
    "weight",
    "blood_type",
    "medical_history",
    "allergy_history",
    "family_history",
    "smoking",
    "drinking",
    "symptoms",
    "sleep_quality",
    "bowel_urination",
    "created_at",
    "updated_at",
}

EXPECTED_NOT_NULL_COLUMNS = {
    "id",
    "user_id",
    "gender",
    "birth_date",
    "created_at",
    "updated_at",
}

EXPECTED_NULLABLE_COLUMNS = HEALTH_PROFILE_COLUMNS - EXPECTED_NOT_NULL_COLUMNS

EXPECTED_UDT_TYPES = {
    "id": "int8",
    "user_id": "int8",
    "gender": "varchar",
    "birth_date": "date",
    "height": "numeric",
    "weight": "numeric",
    "blood_type": "varchar",
    "medical_history": "jsonb",
    "allergy_history": "jsonb",
    "family_history": "jsonb",
    "smoking": "varchar",
    "drinking": "varchar",
    "symptoms": "jsonb",
    "sleep_quality": "varchar",
    "bowel_urination": "varchar",
    "created_at": "timestamptz",
    "updated_at": "timestamptz",
}


def test_health_profile_table_exists_after_upgrade_head(pg_database):
    table_name = pg_database.fetch_value(
        """
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
          AND tablename = 'health_profile'
        """
    )

    assert table_name == "health_profile"


def test_health_profile_has_expected_columns(pg_database):
    rows = pg_database.fetch_rows(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'health_profile'
        """
    )

    columns = {row["column_name"] for row in rows}

    assert columns == HEALTH_PROFILE_COLUMNS


def test_health_profile_columns_have_expected_nullability_and_types(pg_database):
    rows = pg_database.fetch_rows(
        """
        SELECT column_name, is_nullable, udt_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'health_profile'
        """
    )

    nullable_by_column = {row["column_name"]: row["is_nullable"] for row in rows}
    types_by_column = {row["column_name"]: row["udt_name"] for row in rows}

    assert {
        column_name
        for column_name, is_nullable in nullable_by_column.items()
        if is_nullable == "NO"
    } == EXPECTED_NOT_NULL_COLUMNS
    assert {
        column_name
        for column_name, is_nullable in nullable_by_column.items()
        if is_nullable == "YES"
    } == EXPECTED_NULLABLE_COLUMNS
    assert types_by_column == EXPECTED_UDT_TYPES


def test_health_profile_primary_key_exists(pg_database):
    primary_keys = pg_database.fetch_rows(
        """
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        WHERE tc.table_schema = 'public'
          AND tc.table_name = 'health_profile'
          AND tc.constraint_type = 'PRIMARY KEY'
        """
    )

    assert {row["column_name"] for row in primary_keys} == {"id"}


def test_health_profile_user_id_foreign_key_exists(pg_database):
    foreign_keys = pg_database.fetch_rows(
        """
        SELECT
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
          AND tc.table_name = 'health_profile'
          AND tc.constraint_type = 'FOREIGN KEY'
        """
    )

    assert {
        (row["source_column"], row["target_table"], row["target_column"])
        for row in foreign_keys
    } == {("user_id", "user", "id")}


def test_health_profile_user_id_unique_constraint_exists(pg_database):
    unique_constraints = pg_database.fetch_rows(
        """
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        WHERE tc.table_schema = 'public'
          AND tc.table_name = 'health_profile'
          AND tc.constraint_type = 'UNIQUE'
        """
    )

    assert {row["column_name"] for row in unique_constraints} == {"user_id"}
