from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from tests.conftest import ALLOWED_USER, DENIED_USER, FILE_ID, auth_cookies


def _login_super(client):
    response = client.post("/api/v1/auth/login", json={"username": "super", "password": "123"})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": client.cookies.get("qka_next_csrf") or ""}


def _csrf_for(client, user_id) -> tuple[dict[str, str], dict[str, str]]:
    """Issue session + matching CSRF cookie for a seeded user."""
    cookies = auth_cookies(user_id)
    csrf = "test-csrf-token-ai-chat"
    cookies["qka_next_csrf"] = csrf
    return cookies, {"X-CSRF-Token": csrf}


def test_ai_status_requires_auth(client) -> None:
    assert client.get("/api/v1/ai/status").status_code == 401


def test_ai_status_not_configured(client) -> None:
    cookies = auth_cookies(ALLOWED_USER)
    response = client.get("/api/v1/ai/status", cookies=cookies)
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False
    assert body["available"] is False
    assert body["message"] == "AI not configured"
    assert body["status"] == "not_configured"


def test_chat_stream_reports_not_configured(client) -> None:
    cookies, headers = _csrf_for(client, ALLOWED_USER)
    response = client.post(
        "/api/v1/ai/chat/stream",
        cookies=cookies,
        headers=headers,
        json={"message": "Hello"},
    )
    assert response.status_code == 200
    assert "AI not configured" in response.text
    assert "event: error" in response.text


def test_summary_openai_flag_false_without_key(client) -> None:
    cookies = auth_cookies(ALLOWED_USER)
    response = client.get("/api/v1/diagnostics/summary", cookies=cookies)
    assert response.status_code == 200
    body = response.json()
    assert body["openaiConfigured"] is False
    assert body["openaiAvailable"] is False


def test_unauthorized_document_acl_denied_in_chat(client, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("QKA_OPENAI_DATA_SHARING_PERMITTED", "true")
    from qka_shared.config import get_settings

    get_settings.cache_clear()
    cookies, headers = _csrf_for(client, DENIED_USER)
    response = client.post(
        "/api/v1/ai/chat/stream",
        cookies=cookies,
        headers=headers,
        json={"message": "Summarize this", "documentIds": [FILE_ID.hex]},
    )
    assert response.status_code == 200
    assert "event: error" in response.text
    assert "acl_denied" in response.text or "Access denied" in response.text
    get_settings.cache_clear()


class _FakeStream:
    def __init__(self, events):
        self._events = events

    def __iter__(self):
        return iter(self._events)


def test_streamed_text_only_chat_with_mock_openai(client, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("QKA_OPENAI_DATA_SHARING_PERMITTED", "false")
    from qka_shared.config import get_settings

    get_settings.cache_clear()

    delta1 = MagicMock(type="response.output_text.delta", delta="Hello ")
    delta2 = MagicMock(type="response.output_text.delta", delta="from GPT")
    completed = MagicMock(type="response.completed")
    completed.response = MagicMock(usage=MagicMock(input_tokens=12, output_tokens=4))
    fake_client = MagicMock()
    fake_client.responses.create.return_value = _FakeStream([delta1, delta2, completed])

    cookies, headers = _csrf_for(client, ALLOWED_USER)
    with patch("openai.OpenAI", return_value=fake_client):
        response = client.post(
            "/api/v1/ai/chat/stream",
            cookies=cookies,
            headers=headers,
            json={"message": "Say hello"},
        )
    assert response.status_code == 200, response.text
    text = response.text
    assert "event: meta" in text
    assert "event: delta" in text
    assert "Hello " in text
    assert "from GPT" in text
    assert "event: done" in text
    assert "event: error" not in text

    # Conversation history persisted
    meta_line = next(line for line in text.splitlines() if line.startswith("data: ") and "conversationId" in line)
    meta = json.loads(meta_line[len("data: ") :])
    conversation_id = meta["conversationId"]
    detail = client.get(f"/api/v1/ai/conversations/{conversation_id}", cookies=cookies)
    assert detail.status_code == 200
    messages = detail.json()["messages"]
    assert any(m["role"] == "user" and m["content"] == "Say hello" for m in messages)
    assert any(m["role"] == "assistant" and "Hello from GPT" in m["content"] for m in messages)

    # Follow-up reuses conversation
    delta3 = MagicMock(type="response.output_text.delta", delta="Follow-up ok")
    completed2 = MagicMock(type="response.completed")
    completed2.response = MagicMock(usage=MagicMock(input_tokens=20, output_tokens=3))
    fake_client.responses.create.return_value = _FakeStream([delta3, completed2])
    with patch("openai.OpenAI", return_value=fake_client):
        follow = client.post(
            "/api/v1/ai/chat/stream",
            cookies=cookies,
            headers=headers,
            json={"message": "And again", "conversationId": conversation_id},
        )
    assert follow.status_code == 200
    assert "Follow-up ok" in follow.text

    status = client.get("/api/v1/ai/status", cookies=cookies)
    assert status.json()["configured"] is True
    assert status.json()["successCount"] >= 1
    assert status.json()["lastSuccessfulRequestAt"] is not None
    get_settings.cache_clear()


def test_sample_upload_admin_and_analysis(client, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("QKA_OPENAI_DATA_SHARING_PERMITTED", "true")
    from qka_shared.config import get_settings

    get_settings.cache_clear()
    headers = _login_super(client)
    files = {"file": ("sample.txt", b"Quarterly revenue was 42 million SAR.", "text/plain")}
    upload = client.post("/api/v1/ai/sample-upload", headers=headers, files=files)
    assert upload.status_code == 200, upload.text
    body = upload.json()
    assert body["ok"] is True
    sample_id = body["id"]

    delta = MagicMock(type="response.output_text.delta", delta="Revenue is 42 million SAR.")
    completed = MagicMock(type="response.completed")
    completed.response = MagicMock(usage=MagicMock(input_tokens=30, output_tokens=8))
    fake_client = MagicMock()
    fake_client.responses.create.return_value = _FakeStream([delta, completed])
    with patch("openai.OpenAI", return_value=fake_client):
        stream = client.post(
            "/api/v1/ai/chat/stream",
            headers=headers,
            json={"message": "What is the revenue?", "sampleIds": [sample_id]},
        )
    assert stream.status_code == 200
    assert "42 million" in stream.text
    # Ensure untrusted sample content was passed into the OpenAI input
    call_kwargs = fake_client.responses.create.call_args.kwargs
    serialized = json.dumps(call_kwargs["input"])
    assert "Quarterly revenue" in serialized
    assert "UNTRUSTED" in serialized
    get_settings.cache_clear()


def test_document_chat_blocked_without_data_sharing(client, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("QKA_OPENAI_DATA_SHARING_PERMITTED", "false")
    from qka_shared.config import get_settings

    get_settings.cache_clear()
    cookies, headers = _csrf_for(client, ALLOWED_USER)
    response = client.post(
        "/api/v1/ai/chat/stream",
        cookies=cookies,
        headers=headers,
        json={"message": "Summarize", "documentIds": [FILE_ID.hex]},
    )
    assert response.status_code == 200
    assert "data_sharing_denied" in response.text
    get_settings.cache_clear()
