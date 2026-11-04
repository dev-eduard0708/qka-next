"""Dual AD + Google identity linking — pending workflow, no email auto-merge."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from qka_api.db import create_session_factory
from qka_shared.allowlist import LINKED, PROVIDER_AD, PROVIDER_GOOGLE
from qka_shared.config import get_settings
from qka_shared.google_oidc import GoogleOidcIdentity
from qka_shared.models import ExternalIdentity, PendingGoogleLink, QkaUser
from tests.conftest import ALLOWED_USER, auth_cookies
from tests.fake_google import FakeGoogleOidcService
from tests.test_google_oidc import _login
from tests.test_source_access import _admin_headers

AD_ONLY_USER = uuid.UUID("aaaaaaaa-ad01-4ad1-8ad1-111111111111")
AD_ONLY_SID = "S-1-5-21-111-222-333-4444"
GOOGLE_SUB_AD_LINK = "google-sub-ad-link-001"
AD_CONTACT = "ad.employee@qehc.edu.sa"


def _seed_ad_only_employee(*, contact_email: str | None = AD_CONTACT) -> None:
    get_settings.cache_clear()
    engine, factory = create_session_factory(get_settings())
    now = datetime.now(timezone.utc)
    try:
        with factory() as session:
            existing = session.get(QkaUser, AD_ONLY_USER)
            if existing is None:
                session.add(
                    QkaUser(
                        id=AD_ONLY_USER,
                        display_name="AD Employee",
                        status=0,
                        login_enabled=False,
                        is_directory_operator=False,
                        is_super_admin=False,
                        contact_email=contact_email,
                        directory_department="IT",
                        created_at=now,
                        updated_at=now,
                    )
                )
                session.flush()
                session.add(
                    ExternalIdentity(
                        id=uuid.uuid4(),
                        qka_user_id=AD_ONLY_USER,
                        provider=PROVIDER_AD,
                        stable_external_id=AD_ONLY_SID,
                        domain_username="ademployee",
                        primary_email=contact_email,
                        account_status=0,
                        link_status=LINKED,
                        directory_presence=0,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                existing.contact_email = contact_email
                existing.login_enabled = False
                # Drop any previously linked Google identity from earlier tests in this DB.
                for identity in list(
                    session.execute(
                        select(ExternalIdentity).where(
                            ExternalIdentity.qka_user_id == AD_ONLY_USER,
                            ExternalIdentity.provider == PROVIDER_GOOGLE,
                        )
                    ).scalars().all()
                ):
                    session.delete(identity)
            session.commit()
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_first_google_sign_in_still_creates_google_user(google_client) -> None:
    """Allowlisted email with no AD candidate still creates a new QkaUser (existing path)."""
    callback = _login(google_client, "code-unknown")
    assert callback.status_code == 302
    assert "authError" not in (callback.headers.get("location") or "")
    me = google_client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["id"] != str(ALLOWED_USER)


def test_google_login_matching_ad_contact_creates_pending_not_duplicate(google_client) -> None:
    _seed_ad_only_employee()
    google_client.app.state.google_oidc = FakeGoogleOidcService(
        {
            "code-ad-link": GoogleOidcIdentity(
                GOOGLE_SUB_AD_LINK, AD_CONTACT, True, "qehc.edu.sa", "AD Employee"
            ),
        }
    )
    # Allowlist the contact so we prove allowlist alone does not auto-link / create a second user.
    headers = _admin_headers(google_client)
    created = google_client.post(
        "/api/v1/access/users",
        headers=headers,
        json={"displayName": "AD Employee", "email": AD_CONTACT, "department": "IT"},
    )
    # May already exist from a prior run in same DB — 200 or 409 is fine for allowlist presence.
    assert created.status_code in {200, 409}, created.text

    callback = _login(google_client, "code-ad-link")
    location = callback.headers.get("location") or ""
    assert "authError" in location
    assert "administrator" in location.lower() or "approval" in location.lower()

    # Clear Super Admin cookies from the directory setup call — Google bind must not log anyone in.
    google_client.cookies.clear()
    me = google_client.get("/api/v1/auth/me")
    assert me.status_code == 401

    get_settings.cache_clear()
    engine, factory = create_session_factory(get_settings())
    try:
        with factory() as session:
            pending = session.execute(
                select(PendingGoogleLink).where(
                    PendingGoogleLink.google_sub == GOOGLE_SUB_AD_LINK,
                    PendingGoogleLink.target_qka_user_id == AD_ONLY_USER,
                )
            ).scalar_one()
            assert pending.status == 0
            # No second QkaUser with login for this Google sub.
            linked = session.execute(
                select(ExternalIdentity).where(
                    ExternalIdentity.provider == PROVIDER_GOOGLE,
                    ExternalIdentity.stable_external_id == GOOGLE_SUB_AD_LINK,
                    ExternalIdentity.link_status == LINKED,
                )
            ).scalar_one_or_none()
            assert linked is None
            users = session.execute(select(QkaUser).where(QkaUser.contact_email == AD_CONTACT)).scalars().all()
            assert any(u.id == AD_ONLY_USER for u in users)
    finally:
        engine.dispose()
        get_settings.cache_clear()

    headers = _admin_headers(google_client)
    listed = google_client.get("/api/v1/access/users", headers=headers, params={"pageSize": 200})
    assert listed.status_code == 200
    row = next(r for r in listed.json()["items"] if r.get("qkaUserId") == str(AD_ONLY_USER))
    assert row["adLinked"] is True
    assert row["googleLinked"] is False
    assert row["googlePending"] is True
    assert row["accountStatus"] == "awaiting_admin_approval"
    assert "ad" in row["identityBadges"]
    assert "google_pending" in row["identityBadges"]
    assert "google" not in row["identityBadges"]
    assert row["linkedIdentities"] != "both"
    assert row["pendingGoogleLink"]["googleSub"] == GOOGLE_SUB_AD_LINK


def test_admin_approve_links_google_to_same_qka_user(google_client) -> None:
    _seed_ad_only_employee()
    google_client.app.state.google_oidc = FakeGoogleOidcService(
        {
            "code-ad-link": GoogleOidcIdentity(
                GOOGLE_SUB_AD_LINK, AD_CONTACT, True, "qehc.edu.sa", "AD Employee"
            ),
        }
    )
    headers = _admin_headers(google_client)
    google_client.post(
        "/api/v1/access/users",
        headers=headers,
        json={"displayName": "AD Employee", "email": AD_CONTACT, "department": "IT"},
    )
    _login(google_client, "code-ad-link")

    listed = google_client.get("/api/v1/access/users", headers=headers, params={"pageSize": 200}).json()
    row = next(r for r in listed["items"] if r.get("qkaUserId") == str(AD_ONLY_USER))
    pending_id = row["pendingGoogleLink"]["id"]

    approved = google_client.post(
        f"/api/v1/access/identities/google-pending/{pending_id}/approve",
        headers=headers,
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["ok"] is True
    assert body["qkaUserId"] == str(AD_ONLY_USER)
    user = body["user"]
    assert user["googleLinked"] is True
    assert user["adLinked"] is True
    assert user["linkedIdentities"] == "both"
    assert set(user["identityBadges"]) == {"ad", "google"}
    assert user["googleSub"] == GOOGLE_SUB_AD_LINK
    assert user["googlePending"] is False

    # Subsequent Google login succeeds on the same QkaUser.
    callback = _login(google_client, "code-ad-link")
    assert "authError" not in (callback.headers.get("location") or "")
    me = google_client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["id"] == str(AD_ONLY_USER)


def test_admin_reject_keeps_identities_separate(google_client) -> None:
    _seed_ad_only_employee(contact_email="ad.reject@qehc.edu.sa")
    sub = "google-sub-reject-001"
    google_client.app.state.google_oidc = FakeGoogleOidcService(
        {
            "code-reject": GoogleOidcIdentity(sub, "ad.reject@qehc.edu.sa", True, "qehc.edu.sa", "Reject Me"),
        }
    )
    headers = _admin_headers(google_client)
    google_client.post(
        "/api/v1/access/users",
        headers=headers,
        json={"displayName": "Reject Me", "email": "ad.reject@qehc.edu.sa", "department": "IT"},
    )
    _login(google_client, "code-reject")

    listed = google_client.get("/api/v1/access/users", headers=headers, params={"pageSize": 200}).json()
    row = next(r for r in listed["items"] if r.get("qkaUserId") == str(AD_ONLY_USER))
    pending_id = row["pendingGoogleLink"]["id"]

    rejected = google_client.post(
        f"/api/v1/access/identities/google-pending/{pending_id}/reject",
        headers=headers,
    )
    assert rejected.status_code == 200, rejected.text

    get_settings.cache_clear()
    engine, factory = create_session_factory(get_settings())
    try:
        with factory() as session:
            user = session.execute(
                select(QkaUser)
                .options(selectinload(QkaUser.identities))
                .where(QkaUser.id == AD_ONLY_USER)
            ).scalar_one()
            assert not any(
                i.provider == PROVIDER_GOOGLE and i.link_status == LINKED for i in user.identities
            )
            pending = session.get(PendingGoogleLink, uuid.UUID(pending_id))
            assert pending is not None
            assert pending.status == 2
    finally:
        engine.dispose()
        get_settings.cache_clear()

    # Login still denied after reject.
    callback = _login(google_client, "code-reject")
    assert "authError" in (callback.headers.get("location") or "")


def test_identity_conflict_blocks_approve_no_silent_merge(google_client) -> None:
    """AD contact matches email already owned by another Google-linked QkaUser → conflict."""
    _seed_ad_only_employee(contact_email="eduard@qehc.edu.sa")
    # ALLOWED_USER already has linked Google for eduard@qehc.edu.sa.
    # New sub signing in with that email while AD-only also has that contact → conflict pending.
    sub = "google-sub-conflict-001"
    google_client.app.state.google_oidc = FakeGoogleOidcService(
        {
            "code-conflict": GoogleOidcIdentity(sub, "eduard@qehc.edu.sa", True, "qehc.edu.sa", "Conflict"),
        }
    )
    headers = _admin_headers(google_client)
    _login(google_client, "code-conflict")

    listed = google_client.get("/api/v1/access/users", headers=headers, params={"pageSize": 200}).json()
    row = next((r for r in listed["items"] if r.get("qkaUserId") == str(AD_ONLY_USER)), None)
    assert row is not None
    assert row["identityConflict"] is True
    pending_id = row["pendingGoogleLink"]["id"]

    approved = google_client.post(
        f"/api/v1/access/identities/google-pending/{pending_id}/approve",
        headers=headers,
    )
    assert approved.status_code == 400
    assert "merge" in approved.text.lower() or "conflict" in approved.text.lower()

    # Existing Google-linked QkaUser remains intact — no silent merge into AD-only user.
    get_settings.cache_clear()
    engine, factory = create_session_factory(get_settings())
    try:
        with factory() as session:
            allowed = session.execute(
                select(QkaUser)
                .options(selectinload(QkaUser.identities))
                .where(QkaUser.id == ALLOWED_USER)
            ).scalar_one()
            assert any(
                i.provider == PROVIDER_GOOGLE and i.link_status == LINKED for i in allowed.identities
            )
            ad_only = session.execute(
                select(QkaUser)
                .options(selectinload(QkaUser.identities))
                .where(QkaUser.id == AD_ONLY_USER)
            ).scalar_one()
            assert not any(
                i.provider == PROVIDER_GOOGLE and i.link_status == LINKED for i in ad_only.identities
            )
    finally:
        engine.dispose()
        get_settings.cache_clear()

    google_client.cookies.clear()
    me = google_client.get("/api/v1/auth/me", cookies=auth_cookies(ALLOWED_USER))
    assert me.status_code == 200
    assert me.json()["id"] == str(ALLOWED_USER)


def test_entering_contact_email_does_not_auto_link_google(client) -> None:
    _seed_ad_only_employee(contact_email=None)
    headers = _admin_headers(client)
    updated = client.post(
        f"/api/v1/access/users/qka/{AD_ONLY_USER}",
        headers=headers,
        json={"contactEmail": "fresh.contact@qehc.edu.sa", "department": "IT", "displayName": "AD Employee"},
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["contactEmail"] == "fresh.contact@qehc.edu.sa"
    assert body["googleLinked"] is False
    assert body["googlePending"] is False
    assert body["linkedIdentities"] == "ad"
    assert body["identityBadges"] == ["ad"]
