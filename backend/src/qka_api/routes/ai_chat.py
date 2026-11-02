"""Native OpenAI chat routes (Google session required; API key never leaves the server)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from qka_api.auth import require_csrf, require_principal
from qka_api.deps import get_session, get_settings_dep
from qka_api.services import ai_chat as chat
from qka_shared.allowlist import load_active_user
from qka_shared.chat_access import channel_deny_message, require_local_chat
from qka_shared.config import Settings
from qka_shared.models import QkaUser

router = APIRouter(prefix="/api/v1/ai")


class CreateConversationBody(BaseModel):
    title: str | None = None


class StreamMessageBody(BaseModel):
    message: str = Field(min_length=1, max_length=16000)
    conversationId: str | None = None
    documentIds: list[str] = Field(default_factory=list)
    sampleIds: list[str] = Field(default_factory=list)


def _require_user(request: Request, settings: Settings, session: Session) -> QkaUser:
    principal = require_principal(request, settings)
    user = load_active_user(session, principal.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in required.")
    return user


def _require_local_chat_user(request: Request, settings: Settings, session: Session) -> QkaUser:
    user = _require_user(request, settings, session)
    access = require_local_chat(session, user.id)
    if not access.allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=channel_deny_message(access.reason))
    return user


def _require_super_admin(request: Request, settings: Settings, session: Session, *, mutating: bool) -> QkaUser:
    if mutating:
        require_csrf(request)
    user = _require_user(request, settings, session)
    if not user.is_super_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super Admin access is required.")
    return user


@router.get("/status")
def ai_status(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    user = _require_user(request, settings, session)
    status_body = chat.openai_status(session, settings)
    channel = require_local_chat(session, user.id)
    status_body["localChatEnabled"] = bool(channel.allowed and channel.local_chat)
    status_body["localChatReason"] = None if status_body["localChatEnabled"] else channel_deny_message(channel.reason)
    return status_body


@router.get("/conversations")
def list_conversations(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    user = _require_local_chat_user(request, settings, session)
    return {"items": chat.list_conversations(session, user.id)}


@router.post("/conversations")
def create_conversation(
    body: CreateConversationBody,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    require_csrf(request)
    user = _require_local_chat_user(request, settings, session)
    created = chat.create_conversation(session, user.id, body.title)
    session.commit()
    return created


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: str,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    user = _require_local_chat_user(request, settings, session)
    payload = chat.get_conversation(session, user.id, conversation_id)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    return payload


@router.delete("/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: str,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    require_csrf(request)
    user = _require_local_chat_user(request, settings, session)
    ok = chat.delete_conversation(session, user.id, conversation_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    session.commit()
    return {"ok": True}


@router.post("/chat/stream")
def chat_stream(
    body: StreamMessageBody,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> StreamingResponse:
    require_csrf(request)
    user = _require_local_chat_user(request, settings, session)
    user_id = user.id
    factory = request.app.state.session_factory

    def event_gen():
        # Own session for the full SSE lifetime (request-scoped session closes when the endpoint returns).
        with factory() as stream_session:
            try:
                for chunk in chat.stream_chat(
                    stream_session,
                    settings,
                    user_id,
                    conversation_id=body.conversationId,
                    message=body.message,
                    document_ids=body.documentIds,
                    sample_ids=body.sampleIds,
                ):
                    yield chunk
                stream_session.commit()
            except Exception:
                stream_session.rollback()
                raise

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/sample-upload")
async def sample_upload(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    user = _require_super_admin(request, settings, session, mutating=True)
    payload = await file.read()
    result = chat.upload_sample(session, settings, user.id, file.filename or "upload.bin", payload)
    if not result.get("ok") and not result.get("id"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result.get("error") or "Upload failed.")
    session.commit()
    return result
