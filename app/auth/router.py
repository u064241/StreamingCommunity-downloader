"""Identity of the local user, for the frontend.

There is no login: the panel is single-user and local. The frontend still asks
who it is acting as (to show the permission-gated menu entries and the build
version), so ``/api/auth/me`` answers with the local user.
"""

from fastapi import APIRouter, Request

from app import __version__
from app.auth.deps import current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/me")
def me(request: Request):
    user = current_user(request)
    return {
        "user": user.to_public(),
        # No session, so no CSRF token: cross-site writes are refused by the
        # Origin / Sec-Fetch-Site check in app/auth/deps.py instead.
        "csrf_token": None,
        "auth_enabled": False,
        "version": __version__,
    }
