# pyright: reportMissingImports=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownParameterType=false
import json
from typing import Any

import engineio

try:  # pragma: no cover - optional dependency
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # type: ignore

try:
    from typing import TYPE_CHECKING
except Exception:  # pragma: no cover
    TYPE_CHECKING = False  # type: ignore

if TYPE_CHECKING:  # pragma: no cover - typing shim for engineio.ASGIApp
    from collections.abc import Awaitable
    from typing import Callable

    class _BaseASGIApp:  # minimal protocol
        async def __call__(
            self,
            scope: Any,
            receive: Callable[[], Awaitable[Any]],
            send: Callable[[Any], Awaitable[None]],
        ): ...
        def __init__(self, *args: Any, **kwargs: Any) -> None: ...
else:  # runtime alias
    _BaseASGIApp = engineio.ASGIApp  # type: ignore[assignment]


class ASGIApp(_BaseASGIApp):  # pragma: no cover
    """ASGI application middleware for Socket.IO.

    This middleware dispatches traffic to an Socket.IO application. It can
    also serve a list of static files to the client, or forward unrelated
    HTTP traffic to another ASGI application.

    :param socketio_server: The Socket.IO server. Must be an instance of the
                            ``fastsio.AsyncServer`` class.
    :param static_files: A dictionary with static file mapping rules. See the
                         documentation for details on this argument.
    :param other_asgi_app: A separate ASGI app that receives all other traffic.
    :param socketio_path: The endpoint where the Socket.IO application should
                          be installed. The default value is appropriate for
                          most cases. With a value of ``None``, all incoming
                          traffic is directed to the Socket.IO server, with the
                          assumption that routing, if necessary, is handled by
                          a different layer. When this option is set to
                          ``None``, ``static_files`` and ``other_asgi_app`` are
                          ignored.
    :param on_startup: function to be called on application startup; can be
                       coroutine
    :param on_shutdown: function to be called on application shutdown; can be
                        coroutine

    Example usage::

        import fastsio
        import uvicorn

        sio = fastsio.AsyncServer()
        app = fastsio.ASGIApp(
            sio,
            static_files={
                "/": "index.html",
                "/static": "./public",
            },
        )
        uvicorn.run(app, host="127.0.0.1", port=5000)
    """

    def __init__(
        self,
        socketio_server: Any,
        other_asgi_app: Any = None,
        static_files: Any = None,
        socketio_path: str = "socket.io",
        on_startup: Any = None,
        on_shutdown: Any = None,
    ) -> None:
        super().__init__(
            socketio_server,
            other_asgi_app,
            static_files=static_files,
            engineio_path=socketio_path,
            on_startup=on_startup,
            on_shutdown=on_shutdown,
        )
        # keep reference for schema generation
        self._fastsio_server = socketio_server

    async def __call__(self, scope, receive, send):  # type: ignore[override]
        # Intercept AsyncAPI endpoints if enabled
        try:
            cfg = getattr(self._fastsio_server, "asyncapi_config", None)
        except Exception:
            cfg = None
        if cfg and getattr(cfg, "enabled", False) and scope["type"] == "http":
            path = scope.get("path", "")
            # Redirect missing default CSS to CDN to avoid 404s from web-component
            if path == "/assets/default.min.css":
                css_cdn = "https://unpkg.com/@asyncapi/react-component@1.0.0-next.39/styles/default.min.css"
                await send(
                    {
                        "type": "http.response.start",
                        "status": 302,
                        "headers": [[b"location", css_cdn.encode("utf-8")]],
                    }
                )
                await send({"type": "http.response.body", "body": b""})
                return None
            # UI page
            if getattr(cfg, "ui_url", None):
                if path == cfg.ui_url:
                    await self._send_asyncapi_ui(scope, receive, send)
                    return None
            if path == cfg.url:
                await self._send_asyncapi_json(scope, receive, send)
                return None
            if getattr(cfg, "expose_yaml", True) and (
                path == cfg.url.replace(".json", ".yaml")
                or path == cfg.url.replace(".json", ".yml")
            ):
                await self._send_asyncapi_yaml(scope, receive, send)
                return None
        # Fallback to Engine.IO default handling
        return await super().__call__(scope, receive, send)

    async def _send_asyncapi_json(self, scope: Any, receive: Any, send: Any) -> None:
        from .asyncapi import AsyncAPIGenerator  # local import to avoid cycles

        gen = AsyncAPIGenerator(self._fastsio_server.asyncapi_config)
        payload = gen.generate(self._fastsio_server)
        body = json.dumps(payload).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    [b"content-type", b"application/json"],
                    [b"cache-control", b"no-store"],
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def _send_asyncapi_yaml(self, scope: Any, receive: Any, send: Any) -> None:
        if yaml is None:
            body = b"YAML support requires PyYAML to be installed."
            await send(
                {
                    "type": "http.response.start",
                    "status": 501,
                    "headers": [[b"content-type", b"text/plain; charset=utf-8"]],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        from .asyncapi import AsyncAPIGenerator  # local import to avoid cycles

        gen = AsyncAPIGenerator(self._fastsio_server.asyncapi_config)
        payload = gen.generate(self._fastsio_server)
        text = yaml.safe_dump(payload, sort_keys=False)
        body = text.encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    [b"content-type", b"application/yaml"],
                    [b"cache-control", b"no-store"],
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def _send_asyncapi_ui(self, scope: Any, receive: Any, send: Any) -> None:
        # Simple HTML page embedding AsyncAPI React component (CDN)
        # Build schema inline to avoid fetch/CORS/issues and guarantee correctness
        from .asyncapi import AsyncAPIGenerator  # local import

        gen = AsyncAPIGenerator(self._fastsio_server.asyncapi_config)
        schema_payload = gen.generate(self._fastsio_server)
        schema_json = json.dumps(schema_payload)
        js_url = "https://unpkg.com/@asyncapi/web-component@latest/lib/asyncapi-web-component.js"
        css_cdn = "https://unpkg.com/@asyncapi/react-component@1.0.0-next.39/styles/default.min.css"
        css_cdn = "https://unpkg.com/@asyncapi/react-component@1.0.0-next.39/styles/default.min.css"

        html = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>AsyncAPI Docs</title>
  <style>html,body{{height:100%;margin:0}} asyncapi-component{{height:100vh;display:block}}</style>
</head>
<body>
  <script src=\"{js_url}\"></script>
  <asyncapi-component id=\"aapi\" css-url=\"{css_cdn}\"></asyncapi-component>
  <script>
    (function() {{
      const el = document.getElementById('aapi');
      el.schema = {schema_json};
    }})();
  </script>
</body>
</html>
"""
        body = html.encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    [b"content-type", b"text/html; charset=utf-8"],
                    [b"cache-control", b"no-store"],
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
