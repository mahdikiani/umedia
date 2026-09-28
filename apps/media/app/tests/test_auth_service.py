"""Tests for UMedia's authentication wrapper around usso.lite."""

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi_mongo_base.core.exceptions import BaseHTTPException
from usso.exceptions import USSOException
from usso.lite import LiteConfig

from apps.auth.services import AlreadyConfiguredError, AuthService


@pytest_asyncio.fixture
async def auth_service(tmp_path: Path) -> AsyncGenerator[AuthService]:
    config = LiteConfig(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'auth-test.sqlite3'}",
        issuer="umedia-test",
        audience="umedia-test",
        access_token_minutes=60 * 24,
        refresh_token_days=30,
        allow_registration=False,
    )
    service = AuthService(config)
    await service.ensure_initialized()
    yield service
    await service.dispose()


@pytest.mark.asyncio
async def test_is_configured_starts_false(auth_service: AuthService) -> None:
    assert await auth_service.is_configured() is False


@pytest.mark.asyncio
async def test_auth_database_uses_wal_and_extended_busy_timeout(
    auth_service: AuthService,
) -> None:
    async with auth_service.database.engine.connect() as connection:
        journal_mode = await connection.exec_driver_sql("PRAGMA journal_mode")
        journal_mode_value = journal_mode.scalar_one().lower()
        busy_timeout = await connection.exec_driver_sql("PRAGMA busy_timeout")
        busy_timeout_value = busy_timeout.scalar_one()

    assert journal_mode_value == "wal"
    assert busy_timeout_value == 30_000


@pytest.mark.asyncio
async def test_setup_creates_the_one_administrator_and_logs_in(
    auth_service: AuthService,
) -> None:
    pair, user = await auth_service.setup(
        "Admin@Example.com",
        "a secure first password",
        user_agent="pytest",
        ip="127.0.0.1",
    )

    assert pair.access_token
    assert pair.refresh_token
    assert user.is_active is True
    assert user.roles == ["admin"]
    assert await auth_service.is_configured() is True


@pytest.mark.asyncio
async def test_setup_is_single_use(auth_service: AuthService) -> None:
    await auth_service.setup(
        "admin@example.com",
        "a secure first password",
        user_agent=None,
        ip=None,
    )

    with pytest.raises(AlreadyConfiguredError):
        await auth_service.setup(
            "other@example.com",
            "another secure password",
            user_agent=None,
            ip=None,
        )


@pytest.mark.asyncio
async def test_login_requires_the_correct_password(
    auth_service: AuthService,
) -> None:
    await auth_service.setup(
        "admin@example.com",
        "a secure first password",
        user_agent=None,
        ip=None,
    )

    pair, user = await auth_service.login(
        "admin@example.com",
        "a secure first password",
        user_agent=None,
        ip=None,
    )
    assert pair.access_token
    assert user.is_active is True

    with pytest.raises(USSOException):
        await auth_service.login(
            "admin@example.com",
            "wrong password",
            user_agent=None,
            ip=None,
        )


@pytest.mark.asyncio
async def test_change_password_then_old_password_stops_working(
    auth_service: AuthService,
) -> None:
    _pair, user = await auth_service.setup(
        "admin@example.com",
        "a secure first password",
        user_agent=None,
        ip=None,
    )

    await auth_service.change_password(
        user.uid,
        current_password="a secure first password",
        new_password="a different secure password",
    )

    with pytest.raises(USSOException):
        await auth_service.login(
            "admin@example.com",
            "a secure first password",
            user_agent=None,
            ip=None,
        )

    pair, _user = await auth_service.login(
        "admin@example.com",
        "a different secure password",
        user_agent=None,
        ip=None,
    )
    assert pair.access_token


@pytest.mark.asyncio
async def test_change_password_rejects_wrong_current_password(
    auth_service: AuthService,
) -> None:
    _pair, user = await auth_service.setup(
        "admin@example.com",
        "a secure first password",
        user_agent=None,
        ip=None,
    )

    with pytest.raises(USSOException):
        await auth_service.change_password(
            user.uid,
            current_password="not the right password",
            new_password="a different secure password",
        )


@pytest.mark.asyncio
async def test_refresh_rotates_the_token_pair_and_rejects_reuse(
    auth_service: AuthService,
) -> None:
    pair, user = await auth_service.setup(
        "admin@example.com",
        "a secure first password",
        user_agent=None,
        ip=None,
    )

    rotated, refreshed = await auth_service.refresh(pair.refresh_token)

    assert refreshed.uid == user.uid
    assert rotated.access_token != pair.access_token
    assert rotated.refresh_token != pair.refresh_token

    with pytest.raises(USSOException):
        await auth_service.refresh(pair.refresh_token)


@pytest.mark.asyncio
async def test_logout_is_a_no_op_without_a_refresh_token(
    auth_service: AuthService,
) -> None:
    await auth_service.logout(None)


@pytest.mark.asyncio
async def test_create_and_list_users_with_primary_email(
    auth_service: AuthService,
) -> None:
    _pair, admin = await auth_service.setup(
        "Admin@Example.com",
        "a secure first password",
        user_agent=None,
        ip=None,
    )

    created = await auth_service.create_user(
        email="Member@Example.com",
        password="a secure user password",
        role="user",
        name="Media Member",
    )

    assert created.email == "member@example.com"
    assert created.name == "Media Member"
    assert created.roles == ["user"]
    assert created.is_active is True
    assert await auth_service.get_user_summary(created.uid) == created
    assert await auth_service.get_user_summary(admin.uid) is not None
    assert {user.email for user in await auth_service.list_users()} == {
        "admin@example.com",
        "member@example.com",
    }
    assert await auth_service.count_admins() == 1


@pytest.mark.asyncio
async def test_create_user_enforces_umedia_password_minimum(
    auth_service: AuthService,
) -> None:
    with pytest.raises(BaseHTTPException) as exc_info:
        await auth_service.create_user(
            email="member@example.com",
            password="short-pass!",
            role="user",
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.error_code == "weak_password"
    assert await auth_service.list_users() == []


@pytest.mark.asyncio
async def test_update_user_changes_profile_role_and_active_state(
    auth_service: AuthService,
) -> None:
    await auth_service.setup(
        "admin@example.com",
        "a secure first password",
        user_agent=None,
        ip=None,
    )
    created = await auth_service.create_user(
        email="member@example.com",
        password="a secure user password",
        role="user",
        name="Before",
    )

    updated = await auth_service.update_user(
        created.uid,
        name="After",
        role="admin",
    )

    assert updated.name == "After"
    assert updated.roles == ["admin"]
    assert updated.is_active is True
    assert await auth_service.count_admins() == 2

    deactivated = await auth_service.update_user(created.uid, is_active=False)
    assert deactivated.is_active is False
    # Inactive admins do not satisfy the "at least one admin" invariant.
    assert await auth_service.count_admins() == 1


@pytest.mark.asyncio
async def test_cannot_deactivate_the_last_admin(
    auth_service: AuthService,
) -> None:
    _pair, admin = await auth_service.setup(
        "admin@example.com",
        "a secure first password",
        user_agent=None,
        ip=None,
    )

    with pytest.raises(BaseHTTPException) as exc_info:
        await auth_service.update_user(admin.uid, is_active=False)

    assert exc_info.value.status_code == 409
    assert exc_info.value.error_code == "last_admin_required"
    assert (await auth_service.get_user_summary(admin.uid)).is_active is True


@pytest.mark.asyncio
async def test_update_user_can_clear_an_optional_name(
    auth_service: AuthService,
) -> None:
    created = await auth_service.create_user(
        email="member@example.com",
        password="a secure user password",
        role="user",
        name="Before",
    )

    updated = await auth_service.update_user(created.uid, name=None)

    assert updated.name is None


@pytest.mark.asyncio
async def test_cannot_demote_the_last_admin(auth_service: AuthService) -> None:
    _pair, admin = await auth_service.setup(
        "admin@example.com",
        "a secure first password",
        user_agent=None,
        ip=None,
    )

    with pytest.raises(BaseHTTPException) as exc_info:
        await auth_service.update_user(admin.uid, role="user")

    assert exc_info.value.status_code == 409
    assert exc_info.value.error_code == "last_admin_required"
    assert (await auth_service.get_user_summary(admin.uid)).roles == ["admin"]


@pytest.mark.asyncio
async def test_admin_can_be_demoted_when_another_admin_remains(
    auth_service: AuthService,
) -> None:
    _pair, bootstrap_admin = await auth_service.setup(
        "admin@example.com",
        "a secure first password",
        user_agent=None,
        ip=None,
    )
    other_admin = await auth_service.create_user(
        email="other-admin@example.com",
        password="another secure password",
        role="admin",
    )

    demoted = await auth_service.update_user(bootstrap_admin.uid, role="user")

    assert demoted.roles == ["user"]
    assert await auth_service.count_admins() == 1
    assert (await auth_service.get_user_summary(other_admin.uid)).roles == [
        "admin",
    ]


@pytest.mark.asyncio
async def test_delete_user_rejects_self_and_last_admin(
    auth_service: AuthService,
) -> None:
    _pair, admin = await auth_service.setup(
        "admin@example.com",
        "a secure first password",
        user_agent=None,
        ip=None,
    )

    with pytest.raises(BaseHTTPException) as self_delete:
        await auth_service.delete_user(admin.uid, actor_uid=admin.uid)
    assert self_delete.value.status_code == 403
    assert self_delete.value.error_code == "cannot_delete_self"

    with pytest.raises(BaseHTTPException) as last_admin:
        await auth_service.delete_user(admin.uid, actor_uid="different-user")
    assert last_admin.value.status_code == 409
    assert last_admin.value.error_code == "last_admin_required"


@pytest.mark.asyncio
async def test_delete_user_is_soft_delete(auth_service: AuthService) -> None:
    _pair, admin = await auth_service.setup(
        "admin@example.com",
        "a secure first password",
        user_agent=None,
        ip=None,
    )
    member = await auth_service.create_user(
        email="member@example.com",
        password="a secure user password",
        role="user",
    )

    await auth_service.delete_user(member.uid, actor_uid=admin.uid)

    assert await auth_service.get_user_summary(member.uid) is None
    assert [user.uid for user in await auth_service.list_users()] == [admin.uid]


@pytest.mark.asyncio
async def test_update_and_delete_missing_user_return_404(
    auth_service: AuthService,
) -> None:
    with pytest.raises(BaseHTTPException) as update_error:
        await auth_service.update_user("missing", role="user")
    assert update_error.value.status_code == 404
    assert update_error.value.error_code == "user_not_found"

    with pytest.raises(BaseHTTPException) as delete_error:
        await auth_service.delete_user("missing", actor_uid="admin")
    assert delete_error.value.status_code == 404
    assert delete_error.value.error_code == "user_not_found"
