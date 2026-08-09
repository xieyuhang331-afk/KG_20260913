from __future__ import annotations

import inspect


def test_User_currentness查询只使用最小投影() -> None:
    from app.modules.user_health import repository

    source = inspect.getsource(repository.get_member_profile_user_state)
    assert "User.id" in source
    assert "User.role" in source
    assert "User.status" in source
    assert "User.verify_status" in source
    assert "select(User)" not in source
    for forbidden in ("phone", "id_card", "password", "real_name"):
        assert forbidden not in source


def test_历史与latest仓储冻结确定性排序及显式投影() -> None:
    from app.modules.user_health import repository

    history = inspect.getsource(repository.list_member_health_indicator_history)
    latest = inspect.getsource(repository.list_member_latest_health_indicators)
    assert "recorded_at.desc()" in history and "id.desc()" in history
    assert "recorded_at.desc()" in latest and "id.desc()" in latest
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
