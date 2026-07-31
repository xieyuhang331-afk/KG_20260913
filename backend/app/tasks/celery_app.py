from app.core.config import get_settings


def get_declared_queues() -> tuple[str, ...]:
    return get_settings().celery_queues

