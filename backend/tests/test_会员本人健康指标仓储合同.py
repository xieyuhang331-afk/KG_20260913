from __future__ import annotations

import inspect


def test_User_currentness查询只使用最小投影() -> None:
    from app.modules.user_health import repository

    source = inspect.getsource(repository.get_member_profile_user_state)
    assert "r4_member_health_currentness_v1" in source
    assert "actor_user_id=user_id" in source
    assert "select(User)" not in source
    assert 'public."user"' not in source
    for forbidden in ("phone", "id_card", "password", "real_name"):
        assert forbidden not in source


def test_历史与latest仓储冻结确定性排序及显式投影() -> None:
    from app.modules.user_health import repository

    history = inspect.getsource(repository.list_member_health_indicator_history)
    latest = inspect.getsource(repository.list_member_latest_health_indicators)
    assert "r4_member_self_health_indicator_history_v1" in history
    assert "r4_member_legacy_health_indicator_latest_v1" in latest
    assert "select(HealthIndicator)" not in history
    assert "select(HealthIndicator)" not in latest
    for source in (history, latest):
        assert "session.add" not in source
        assert "flush" not in source
        assert "commit" not in source


def test_趋势仓储冻结recorded_at与id升序且零写入() -> None:
    from app.modules.health_analysis import repository

    source = inspect.getsource(repository.list_member_indicator_trend_points)
    assert "recorded_at.asc()" in source
    assert "id.asc()" in source
    assert "select(HealthIndicator)" not in source
    for forbidden in ("session.add", "flush", "commit", "update(", "delete("):
        assert forbidden not in source
