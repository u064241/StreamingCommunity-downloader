"""Read-only release information for the fork distribution."""

import ipaddress
import secrets
import zipfile
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, StrictBool
import requests

from app.auth.deps import require
from app.auth.permissions import Permission
from app.updates import check
from app import distribution, portable_updates
from app.update_download import UpdateDownloadError

router = APIRouter(prefix="/api/updates", tags=["updates"])
CAN_READ = [
    Depends(require(Permission.REQUEST, Permission.DOWNLOAD, Permission.MANAGE_SETTINGS, mode="or"))
]


@router.get("", dependencies=CAN_READ)
def get_update_status():
    return check()


@router.post("/check", dependencies=CAN_READ)
def check_for_update():
    return check(force=True)


_local_token = secrets.token_urlsafe(32)
MANAGE = Depends(require(Permission.MANAGE_SETTINGS))


def _local(request: Request):
    try:
        host = request.url.hostname
        if host != "localhost" and not ipaddress.ip_address(host).is_loopback:
            raise ValueError()
        if not request.client or not ipaddress.ip_address(request.client.host).is_loopback:
            raise ValueError()
        # Never accept forwarded origins/clients for binary replacement.
        if any(h in request.headers for h in ("forwarded", "x-forwarded-for", "x-forwarded-host")):
            raise ValueError()
        if request.method == "POST":
            origin = urlsplit(request.headers.get("origin", ""))
            if (origin.scheme, origin.netloc) != (request.url.scheme, request.url.netloc):
                raise ValueError()
            if not secrets.compare_digest(request.headers.get("x-portable-update-token", ""), _local_token):
                raise ValueError()
    except (TypeError, ValueError):
        raise HTTPException(403, "Aggiornamento consentito solo dal pannello locale")


class ApplyUpdate(BaseModel):
    token: str = Field(min_length=20, max_length=128)
    confirmed: StrictBool = False


def _run(operation, *args):
    try:
        return operation(*args)
    except (UpdateDownloadError, OSError, ValueError, KeyError, TypeError, AttributeError, zipfile.BadZipFile,
            requests.RequestException) as exc:
        raise HTTPException(409, f"Aggiornamento non disponibile: {exc}") from exc


@router.get("/portable", dependencies=[MANAGE, Depends(_local)])
def portable_status():
    mode = distribution.mode()
    return {"mode": mode, "available": mode == "portable", "token": _local_token}


@router.post("/portable/prepare", dependencies=[Depends(_local)])
def prepare_portable(user=MANAGE):
    return _run(portable_updates.prepare, user.id)


@router.post("/portable/apply", dependencies=[Depends(_local)])
def apply_portable(request: Request, body: ApplyUpdate, user=MANAGE):
    if body.confirmed is not True:
        raise HTTPException(400, "Conferma esplicita richiesta")
    return _run(portable_updates.apply, user.id, body.token, request.app)

