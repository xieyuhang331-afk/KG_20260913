import asyncio
import re
from contextvars import ContextVar
from time import monotonic
from uuid import RFC_4122, UUID

from fastapi import FastAPI, Request
from fastapi.routing import iter_route_contexts
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core import logging as request_logging
from app.core.uuid_generator import Uuid7Generator
from app.core.接口合同 import error_response, safe_log_error_code

request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)
_REQUEST_ID = re.compile(rb"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}")


def _request_id(scope: Scope) -> str:
    values = [value for name, value in scope.get("headers", ()) if name.lower() == b"x-request-id"]
    if len(values) == 1 and len(values[0]) == 36 and _REQUEST_ID.fullmatch(values[0]):
        parsed = UUID(values[0].decode("ascii"))
        if parsed.version == 7 and parsed.variant == RFC_4122:
            return str(parsed)
    return str(Uuid7Generator().generate())


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, application: FastAPI | None = None) -> None:
        self.app = app
        self.route_templates: dict[int, set[str]] = {}
        if application is not None:
            for route in iter_route_contexts(application.routes):
                path = route.path_format
                if type(path) is str and len(path) <= 200 and re.fullmatch(r"/[A-Za-z0-9_/{}/.:-]*", path):
                    self.route_templates.setdefault(id(route.original_route), set()).add(path)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = _request_id(scope)
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_context.set(request_id)
        start = monotonic()
        started = False
        status = 500
        primary_cancel = None

        async def send_with_context(message: Message) -> None:
            nonlocal started, status
            if message["type"] == "http.response.start":
                started = True
                status = message["status"]
                headers = [(name, value) for name, value in message.get("headers", ())
                           if name.lower() != b"x-request-id"]
                message = {**message, "headers": [*headers, (b"x-request-id", request_id.encode("ascii"))]}
            await send(message)

        try:
            await self.app(scope, receive, send_with_context)
        except asyncio.CancelledError as error:
            primary_cancel = error
            scope["state"]["error_code"] = "REQUEST_CANCELLED"
            raise
        except Exception:
            if started:
                scope["state"]["error_code"] = "REQUEST_STREAM_FAILED"
                raise RuntimeError("REQUEST_STREAM_FAILED") from None
            response = error_response(Request(scope), 500, "INTERNAL_ERROR")
            await response(scope, receive, send_with_context)
        finally:
            try:
                templates = self.route_templates.get(id(scope.get("route")), set())
                template = next(iter(templates)) if len(templates) == 1 else "unmatched"
                method = scope.get("method")
                if method not in {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "TRACE", "CONNECT"}:
                    method = "OTHER"
                request_logging.emit_request_event(
                    request_id=request_id, method=method, route_template=template,
                    status_code=status, duration_ms=max(0, int((monotonic() - start) * 1000)),
                    error_code=safe_log_error_code(scope["state"].get("error_code")),
                    retryable=scope["state"].get("error_retryable") is True,
                )
            except asyncio.CancelledError:
                if primary_cancel is None:
                    raise
            except Exception:
                try:
                    request_logging.observation_failed()
                except asyncio.CancelledError:
                    if primary_cancel is None:
                        raise
                except Exception:
                    # Both observation sinks failed; do not replace the business
                    # result, the safe stream error, or the original cancellation.
                    pass
            finally:
                request_id_context.reset(token)


def add_request_middleware(app: FastAPI) -> None:
    app.add_middleware(RequestContextMiddleware, application=app)

