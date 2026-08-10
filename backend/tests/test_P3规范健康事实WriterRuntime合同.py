import inspect

import pytest

from app.core.config import Settings
from app.core import database


def test_规范健康事实Writer使用独立数据库身份和独立HMAC配置():
    fields = Settings.model_fields
    assert "health_fact_writer_database_url" in fields
    assert "health_fact_digest_current_key_id" in fields
    assert "health_fact_digest_keyring_json" in fields


def _settings(*, writer_url=None, verification_url=None):
    return Settings(
        database_host="db.example.invalid",
        database_port=5432,
        database_name="disposable_only",
        database_user="app_role",
        database_password="hidden",
        jwt_secret_key="hidden",
        verification_writer_database_url=verification_url,
        health_fact_writer_database_url=writer_url,
    )


def test_Writer_URL缺失目标错误或复用身份全部fail_closed():
    valid = "postgresql+asyncpg://fact_role:hidden@db.example.invalid:5432/disposable_only"
    assert database._get_health_fact_writer_database_url(_settings(writer_url=valid)) == valid
    invalid = (
        None,
        "postgresql+asyncpg://app_role:hidden@db.example.invalid:5432/disposable_only",
        "postgresql+asyncpg://postgres:hidden@db.example.invalid:5432/disposable_only",
        "postgresql+asyncpg://fact_role:hidden@other.invalid:5432/disposable_only",
        "postgresql+asyncpg://fact_role:hidden@db.example.invalid:5432/other",
    )
    for value in invalid:
        with pytest.raises(RuntimeError, match="Health fact writer database runtime is unavailable"):
            database._get_health_fact_writer_database_url(_settings(writer_url=value))


def test_Writer不得复用VerificationWriter身份():
    same = "postgresql+asyncpg://shared_role:hidden@db.example.invalid:5432/disposable_only"
    with pytest.raises(RuntimeError):
        database._get_health_fact_writer_database_url(
            _settings(writer_url=same, verification_url=same)
        )


def test_Writer_Runtime没有URL回显且纳入统一dispose():
    source = inspect.getsource(database._get_health_fact_writer_database_url)
    assert "raw_url" not in database._HEALTH_FACT_WRITER_RUNTIME_ERROR
    assert "KG_DATABASE_URL" not in source
    dispose = inspect.getsource(database.dispose_database_runtimes)
    assert "dispose_health_fact_writer_runtime" in dispose
