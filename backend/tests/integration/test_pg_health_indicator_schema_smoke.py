import pytest


pytestmark = pytest.mark.integration


def test_health_indicator_table_exists_and_is_hypertable(pg_database):
    table_name = pg_database.fetch_value(
        """
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
          AND tablename = 'health_indicator'
        """
    )
    hypertable_name = pg_database.fetch_value(
        """
        SELECT hypertable_name
        FROM timescaledb_information.hypertables
        WHERE hypertable_schema = 'public'
          AND hypertable_name = 'health_indicator'
        """
    )

    assert table_name == "health_indicator"
    assert hypertable_name == "health_indicator"


def test_health_indicator_columns_have_expected_types_and_nullability(pg_database):
    rows = pg_database.fetch_rows(
        """
        SELECT column_name, is_nullable, data_type, udt_name, numeric_precision, numeric_scale
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'health_indicator'
        """
    )
    columns = {row["column_name"]: row for row in rows}

    assert set(columns) == {
        "id",
        "user_id",
        "plan_id",
        "batch_id",
        "indicator_type",
        "value",
        "unit",
        "source",
        "recorded_at",
        "created_at",
    }
    assert columns["id"]["is_nullable"] == "NO"
    assert columns["user_id"]["is_nullable"] == "NO"
    assert columns["indicator_type"]["is_nullable"] == "NO"
    assert columns["value"]["is_nullable"] == "NO"
    assert columns["unit"]["is_nullable"] == "NO"
    assert columns["source"]["is_nullable"] == "NO"
    assert columns["recorded_at"]["is_nullable"] == "NO"
    assert columns["created_at"]["is_nullable"] == "NO"

    assert columns["id"]["data_type"] == "bigint"
    assert columns["user_id"]["data_type"] == "bigint"
    assert columns["plan_id"]["data_type"] == "bigint"
    assert columns["batch_id"]["data_type"] == "character varying"
    assert columns["indicator_type"]["data_type"] == "character varying"
    assert columns["value"]["data_type"] == "numeric"
    assert columns["value"]["numeric_precision"] == 10
    assert columns["value"]["numeric_scale"] == 2
    assert columns["unit"]["data_type"] == "character varying"
    assert columns["source"]["data_type"] == "character varying"
    assert columns["recorded_at"]["data_type"] == "timestamp with time zone"
    assert columns["created_at"]["data_type"] == "timestamp with time zone"


def test_health_indicator_primary_key_and_foreign_key_exist(pg_database):
    primary_key_rows = pg_database.fetch_rows(
        """
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        WHERE tc.table_schema = 'public'
          AND tc.table_name = 'health_indicator'
          AND tc.constraint_type = 'PRIMARY KEY'
        ORDER BY kcu.ordinal_position
        """
    )
    foreign_key_rows = pg_database.fetch_rows(
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
          AND tc.table_name = 'health_indicator'
          AND tc.constraint_type = 'FOREIGN KEY'
        """
    )

    assert [row["column_name"] for row in primary_key_rows] == ["id", "recorded_at"]
    assert {
        (row["source_column"], row["target_table"], row["target_column"])
        for row in foreign_key_rows
    } == {("user_id", "user", "id")}


def test_health_indicator_source_check_constraint_exists(pg_database):
    rows = pg_database.fetch_rows(
        """
        SELECT cc.check_clause
        FROM information_schema.table_constraints tc
        JOIN information_schema.check_constraints cc
          ON cc.constraint_name = tc.constraint_name
         AND cc.constraint_schema = tc.constraint_schema
        WHERE tc.table_schema = 'public'
          AND tc.table_name = 'health_indicator'
          AND tc.constraint_type = 'CHECK'
          AND tc.constraint_name = 'ck_health_indicator_source'
        """
    )

    assert len(rows) == 1
    check_clause = rows[0]["check_clause"]
    for source in ("APP", "STORE", "DEVICE", "REPORT"):
        assert source in check_clause


def test_health_indicator_indexes_exist(pg_database):
    rows = pg_database.fetch_rows(
        """
        SELECT indexname
        FROM pg_indexes
        WHERE schemaname = 'public'
          AND tablename = 'health_indicator'
        """
    )

    assert {"idx_hi_user_time", "idx_hi_type_time"} <= {row["indexname"] for row in rows}
