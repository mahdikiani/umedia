from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .factory import build_notification_service

router = APIRouter(prefix="/notifications", tags=["Notifications"])


class NotificationOut(BaseModel):
    uid: str
    operation: str
    item_name: str
    error: str
    read_at: datetime | None
    created_at: datetime


def _actor(request: Request) -> str:
    return request.state.user.uid


@router.get("", response_model=list[NotificationOut])
async def list_notifications(request: Request) -> list[NotificationOut]:
    records = await build_notification_service(request).list_for_user(_actor(request))
    return [
        NotificationOut(
            uid=record.uid,
            operation=record.operation,
            item_name=record.item_name,
            error=record.error,
            read_at=record.read_at,
            created_at=record.created_at,
        )
        for record in records
    ]


@router.patch("/{uid}/read", response_model=NotificationOut)
async def mark_notification_read(uid: str, request: Request) -> NotificationOut:
    record = await build_notification_service(request).mark_read(uid, _actor(request))
    if record is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    return NotificationOut(
        uid=record.uid,
        operation=record.operation,
        item_name=record.item_name,
        error=record.error,
        read_at=record.read_at,
        created_at=record.created_at,
    )
