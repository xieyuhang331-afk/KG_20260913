from __future__ import annotations

import base64
import os
from urllib.parse import urlsplit

from app.core.config import get_settings


def _secret(value: str | None) -> bytes:
    if (not isinstance(value, str) or value != value.strip() or len(value.encode()) < 32
            or value.lower() in {"changeme", "default", "test", "secret", "password"}
            or value.lower().startswith(("changeme", "replace-me", "your-secret", "example-secret"))):
        raise ValueError("Invalid configured material")
    return value.encode("utf-8")


def validated_auth_settings(*, worker: bool = False, broker_url: str | None = None):
    """No connections or filesystem side effects; never include configuration values in errors."""
    try:
        settings = get_settings()
        if settings.environment not in {"local", "test", "ci_ephemeral"}:
            raise ValueError("Profile not ready")
        if settings.jwt_algorithm != "HS256":
            raise ValueError("Invalid algorithm")
        ttl = settings.jwt_access_token_expire_minutes
        if type(ttl) is not int or not 15 <= ttl <= 120:
            raise ValueError("Invalid lifetime")
        materials = [_secret(settings.jwt_secret_key), _secret(settings.auth_rate_limit_hmac_key)]
        _secret(settings.database_password)
        if settings.database_driver != "postgresql+asyncpg" or not 1 <= settings.database_port <= 65535:
            raise ValueError("Invalid database configuration")
        if settings.environment == "local" and settings.database_host not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Local database scope invalid")
        broker = broker_url if broker_url is not None else os.getenv("KG_CELERY_BROKER_URL")
        if broker is None:
            if worker:
                raise ValueError("Broker required")
        else:
            parsed = urlsplit(broker)
            port = parsed.port
            if (parsed.scheme not in {"amqp", "amqps"} or not parsed.hostname
                    or not parsed.username or not parsed.password
                    or port is not None and not 1 <= port <= 65535
                    or parsed.netloc.rsplit("@", 1)[-1].endswith(":")):
                raise ValueError("Invalid broker configuration")
        if settings.identity_review_step_up_secret_key is not None:
            materials.append(_secret(settings.identity_review_step_up_secret_key))
        identity_names = ("KG_IDENTITY_PII_KEK_B64", "KG_IDENTITY_PII_HMAC_KEY_B64", "KG_IDENTITY_PII_KEY_ID")
        if any(name in os.environ for name in identity_names):
            from app.modules.auth.identity_submission_crypto import (
                IdentitySubmissionCrypto,
            )

            IdentitySubmissionCrypto.from_environment()
            materials.extend(base64.b64decode(os.environ[name], validate=True) for name in identity_names[:2])
        onboarding_names = ("KG_ONBOARDING_PII_KEK_B64", "KG_ONBOARDING_PII_HMAC_KEY_B64")
        if any(name in os.environ for name in onboarding_names):
            from app.modules.institution_onboarding.service import OnboardingSecrets

            OnboardingSecrets()
            materials.extend(base64.b64decode(os.environ[name], validate=True) for name in onboarding_names)
        if any(name.startswith("KG_THERAPIST_") and (name.endswith("KEY_ID") or name.endswith("KEYRING_JSON"))
               for name in os.environ):
            from app.modules.therapist_qualification.service import TherapistSecrets

            keys = TherapistSecrets()
            for domain in ("pii", "digest", "totp", "code", "replay", "readiness", "delivery"):
                # Key rotation may reuse material within one domain, but not across purposes.
                materials.extend(set(getattr(keys, f"{domain}_keys").values()))
        if len(set(materials)) != len(materials):
            raise ValueError("Key purposes overlap")
        return settings
    except Exception:
        raise RuntimeError("AUTH_CONFIGURATION_INVALID") from None
