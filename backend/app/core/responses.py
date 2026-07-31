from typing import Any


def ok_response(data: Any) -> dict[str, Any]:
    return {"code": 0, "message": "ok", "data": data}

