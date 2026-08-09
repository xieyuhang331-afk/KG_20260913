from __future__ import annotations

import inspect


def test_会员本人检测报告列表与详情API尚未实现() -> None:
    try:
        from app.modules.user_health.api import (
            get_member_self_detection_report,
            list_member_self_detection_reports,
        )
    except ImportError:
        raise AssertionError(
            "Member self detection report list and detail API is not implemented"
        ) from None

    assert callable(list_member_self_detection_reports)
    assert callable(get_member_self_detection_report)


def test_检测报告API只开放静态本人路由() -> None:
    from app.modules.user_health.api import (
        get_member_self_detection_report,
        list_member_self_detection_reports,
        router,
    )

    paths = {route.path for route in router.routes}
    assert "/api/v1/users/me/detection-reports" in paths
    assert "/api/v1/users/me/detection-reports/{report_id}" in paths
    assert "user_id" not in inspect.signature(list_member_self_detection_reports).parameters
    assert "user_id" not in inspect.signature(get_member_self_detection_report).parameters
