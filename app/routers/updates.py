"""Read-only release information for the fork distribution."""

from fastapi import APIRouter, Depends

from app.auth.deps import require
from app.auth.permissions import Permission
from app.updates import check

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

