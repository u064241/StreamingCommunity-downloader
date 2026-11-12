"""Security response headers, set once for every response.

The Content-Security-Policy is as tight as the existing frontend allows, and
each relaxation has a reason:

* ``script-src 'self'`` — every script is a file under /static; there is no
  inline ``<script>`` in the templates. ``script-src-attr 'unsafe-inline'`` is
  there because the frontend still emits inline handlers (``onerror=`` on
  thumbnails, one ``onclick=``). Moving those to listeners would let it go.
* ``style-src 'unsafe-inline'`` — templates and rendered cards carry
  ``style="…"`` attributes throughout.
* ``img-src https: http:`` — posters come through /api/image, but AnimeUnity
  artwork is linked directly, from hosts that are not known in advance.
* ``frame-src`` — trailers are YouTube (no-cookie) embeds.

Framing: by default only the panel may frame itself. FRAME_ANCESTORS, when
set, names the foreign origins that may embed it as well. X-Frame-Options
cannot name a foreign origin, so it is only sent when nothing foreign is
allowed.
"""

from urllib.parse import urlsplit

from app import config

_CSP_BASE = (
    "default-src 'self'; "
    "script-src 'self'; "
    "script-src-attr 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: https: http:; "
    "media-src 'self' blob:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-src https://www.youtube-nocookie.com; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


def _origin(url: str | None) -> str | None:
    try:
        parts = urlsplit((url or "").strip())
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return None
        return f"{parts.scheme}://{parts.netloc.rpartition('@')[2]}"
    except ValueError:
        return None


def embed_origins() -> list[str]:
    """Foreign origins allowed to frame the panel: FRAME_ANCESTORS, if any."""
    return [o for o in (_origin(v) for v in config.FRAME_ANCESTORS) if o]


def security_headers() -> dict[str, str]:
    origins = embed_origins()
    ancestors = " ".join(["'self'", *origins])
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "same-origin",
        "Content-Security-Policy": f"{_CSP_BASE}; frame-ancestors {ancestors}",
    }
    if not origins:
        headers["X-Frame-Options"] = "SAMEORIGIN"
    return headers


class SecurityHeadersMiddleware:
    """Pure ASGI, like LocalAccessMiddleware: it also covers the 403s that one
    sends itself, which a route-level hook would never see."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                present = {name.lower() for name, _ in message.get("headers", [])}
                extra = [
                    (name.lower().encode("latin-1"), value.encode("latin-1"))
                    for name, value in security_headers().items()
                    if name.lower().encode("latin-1") not in present
                ]
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        await self.app(scope, receive, send_with_headers)
