from functools import lru_cache
import json
import os
from typing import Any

from pydantic import BaseModel, Field


def require_secret(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Required secret environment variable is missing: {name}")
    return value


class Settings(BaseModel):
    service_name: str = "KG_20260727"
    version: str = "0.1.0"
    environment: str = "local"
    database_driver: str = "postgresql+asyncpg"
    database_host: str = "localhost"
    database_port: int = 5432
    database_name: str = "kg_20260727"
    database_user: str = "kg_app"
    database_password: str
    verification_writer_database_url: str | None = None
    health_fact_writer_database_url: str | None = None
    health_fact_digest_current_key_id: str | None = None
    health_fact_digest_keyring_json: str | None = None
    organization_mapping_writer_database_url: str | None = None
    health_mapping_writer_database_url: str | None = None
    organization_projection_builder_database_url: str | None = None
    health_projection_builder_database_url: str | None = None
    projection_confirmation_database_url: str | None = None
    organization_projection_shadow_database_url: str | None = None
    health_projection_shadow_database_url: str | None = None
    projection_ready_gate_database_url: str | None = None
    projection_shadow_confirmation_database_url: str | None = None
    organization_projection_digest_current_key_id: str | None = None
    organization_projection_digest_keyring_json: str | None = None
    health_projection_digest_current_key_id: str | None = None
    health_projection_digest_keyring_json: str | None = None
    organization_mapping_digest_current_key_id: str | None = None
    organization_mapping_digest_keyring_json: str | None = None
    identity_pii_kek_b64: str | None = None
    identity_pii_hmac_key_b64: str | None = None
    identity_pii_key_id: str | None = None
    identity_review_step_up_secret_key: str | None = None
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 120
    auth_context_map: dict[str, dict[str, Any]] = Field(default_factory=dict)
    async_runtime: str = "Celery + RabbitMQ"
    file_storage_backend: str = "MinIO"
    celery_queues: tuple[str, ...] = (
        "ai",
        "judgment",
        "ocr",
        "report",
        "settlement",
        "notification",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        environment=os.getenv("KG_ENV", "local"),
        database_host=os.getenv("KG_DATABASE_HOST", "localhost"),
        database_port=int(os.getenv("KG_DATABASE_PORT", "5432")),
        database_name=os.getenv("KG_DATABASE_NAME", "kg_20260727"),
        database_user=os.getenv("KG_DATABASE_USER", "kg_app"),
        database_password=require_secret("KG_DATABASE_PASSWORD"),
        verification_writer_database_url=os.getenv(
            "KG_VERIFICATION_WRITER_DATABASE_URL"
        ),
        health_fact_writer_database_url=os.getenv(
            "KG_HEALTH_FACT_WRITER_DATABASE_URL"
        ),
        health_fact_digest_current_key_id=os.getenv(
            "KG_HEALTH_FACT_DIGEST_CURRENT_KEY_ID"
        ),
        health_fact_digest_keyring_json=os.getenv(
            "KG_HEALTH_FACT_DIGEST_KEYRING_JSON"
        ),
        organization_mapping_writer_database_url=os.getenv(
            "KG_ORGANIZATION_MAPPING_WRITER_DATABASE_URL"
        ),
        health_mapping_writer_database_url=os.getenv(
            "KG_HEALTH_MAPPING_WRITER_DATABASE_URL"
        ),
        organization_projection_builder_database_url=os.getenv("KG_ORGANIZATION_PROJECTION_BUILDER_DATABASE_URL"),
        health_projection_builder_database_url=os.getenv("KG_HEALTH_PROJECTION_BUILDER_DATABASE_URL"),
        projection_confirmation_database_url=os.getenv("KG_PROJECTION_CONFIRMATION_DATABASE_URL"),
        organization_projection_shadow_database_url=os.getenv("KG_ORGANIZATION_PROJECTION_SHADOW_DATABASE_URL"),
        health_projection_shadow_database_url=os.getenv("KG_HEALTH_PROJECTION_SHADOW_DATABASE_URL"),
        projection_ready_gate_database_url=os.getenv("KG_PROJECTION_READY_GATE_DATABASE_URL"),
        projection_shadow_confirmation_database_url=os.getenv("KG_PROJECTION_SHADOW_CONFIRMATION_DATABASE_URL"),
        organization_projection_digest_current_key_id=os.getenv("KG_ORGANIZATION_PROJECTION_DIGEST_CURRENT_KEY_ID"),
        organization_projection_digest_keyring_json=os.getenv("KG_ORGANIZATION_PROJECTION_DIGEST_KEYRING_JSON"),
        health_projection_digest_current_key_id=os.getenv("KG_HEALTH_PROJECTION_DIGEST_CURRENT_KEY_ID"),
        health_projection_digest_keyring_json=os.getenv("KG_HEALTH_PROJECTION_DIGEST_KEYRING_JSON"),
        organization_mapping_digest_current_key_id=os.getenv(
            "KG_ORGANIZATION_MAPPING_DIGEST_CURRENT_KEY_ID"
        ),
        organization_mapping_digest_keyring_json=os.getenv(
            "KG_ORGANIZATION_MAPPING_DIGEST_KEYRING_JSON"
        ),
        identity_pii_kek_b64=os.getenv("KG_IDENTITY_PII_KEK_B64"),
        identity_pii_hmac_key_b64=os.getenv("KG_IDENTITY_PII_HMAC_KEY_B64"),
        identity_pii_key_id=os.getenv("KG_IDENTITY_PII_KEY_ID"),
        identity_review_step_up_secret_key=os.getenv(
            "KG_IDENTITY_REVIEW_STEP_UP_SECRET_KEY"
        ),
        jwt_secret_key=require_secret("KG_JWT_SECRET_KEY"),
        jwt_algorithm=os.getenv("KG_JWT_ALGORITHM", "HS256"),
        jwt_access_token_expire_minutes=int(
            os.getenv("KG_JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "120")
        ),
        auth_context_map=json.loads(os.getenv("KG_AUTH_CONTEXT_MAP", "{}")),
    )
