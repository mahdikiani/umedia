"""Multi-user authentication, wrapping usso.lite's LiteAuth.

Business logic that is genuinely ours lives here: the bootstrap
administrator is created exactly once, passwords must meet UMedia's own
minimum, the last administrator can be neither demoted nor deleted, and
nobody deletes their own account. Everything else (password hashing, JWT
issuance/verification, refresh-token rotation, rate limiting) is
usso.lite's, not reimplemented. See docs/02-architecture.md#auth-concrete
for the rationale.
"""

from contextlib import suppress
from typing import Any, NamedTuple, Protocol

from fastapi_mongo_base.core.exceptions import BaseHTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from usso.enums import AuthIdentifier
from usso.exceptions import USSOException
from usso.lite import LiteAuth, LiteConfig
from usso.lite.database import LiteDatabase
from usso.lite.models import LocalUser
from usso.lite.schemas import Identifier, LoginRequest, TokenPair

from .schemas import MIN_PASSWORD_LENGTH, UserSummary

#: Sentinel distinguishing "argument not passed" from an explicit None
#: (update_user must support clearing the optional name with `name=None`).
_UNSET: object = object()


class AlreadyConfiguredError(BaseHTTPException):
    """Raised when initial setup has already completed."""

    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            error_code="already_configured",
            detail="Administrator password is already configured",
            message="Administrator password is already configured",
        )


class WeakPasswordError(BaseHTTPException):
    """Raised when a password does not meet UMedia's minimum length."""

    def __init__(self) -> None:
        super().__init__(
            status_code=422,
            error_code="weak_password",
            detail=(
                f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
            ),
            message=(
                f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
            ),
        )


class UserNotFoundError(BaseHTTPException):
    """Raised when the referenced user does not exist (or is deleted)."""

    def __init__(self) -> None:
        super().__init__(
            status_code=404,
            error_code="user_not_found",
            detail="User not found",
            message="User not found",
        )


class LastAdminRequiredError(BaseHTTPException):
    """Raised when an operation would leave the installation adminless."""

    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            error_code="last_admin_required",
            detail="The installation must keep at least one administrator",
            message="The installation must keep at least one administrator",
        )


class CannotDeleteSelfError(BaseHTTPException):
    """Raised when a user tries to delete their own account."""

    def __init__(self) -> None:
        super().__init__(
            status_code=403,
            error_code="cannot_delete_self",
            detail="You cannot delete your own account",
            message="You cannot delete your own account",
        )


class AuthResult(NamedTuple):
    """A signed token pair plus the user it belongs to."""

    pair: TokenPair
    user: LocalUser


class AccessKeyServiceProtocol(Protocol):
    """The slice of `apps.user_access_keys.services.UserAccessKeyService`
    account creation uses: give every new user their default key pair."""

    async def ensure_default_key(self, user_id: str) -> Any: ...  # noqa: ANN401


class AuthService:
    """Configure the installation and manage its user accounts."""

    def __init__(
        self,
        config: LiteConfig,
        *,
        access_keys: AccessKeyServiceProtocol | None = None,
    ) -> None:
        self._auth = LiteAuth(config)
        self._database = LiteDatabase(config.database_url)
        #: Optional so auth-only tests need not wire the key service;
        #: production always passes one (server/server.py's lifespan) --
        #: users created without it get their key lazily, on first mint.
        self._access_keys = access_keys

    @property
    def lite_auth(self) -> LiteAuth:
        """Expose the underlying LiteAuth (needed by the auth middleware)."""
        return self._auth

    @property
    def database(self) -> LiteDatabase:
        """Expose the underlying LiteDatabase (needed by the auth middleware)."""
        return self._database

    async def ensure_initialized(self) -> None:
        """Create usso.lite's tables if they don't exist yet."""
        await self._database.init_db()

    async def dispose(self) -> None:
        """Release the database engine on shutdown."""
        await self._database.dispose()

    async def is_configured(self) -> bool:
        """Return whether the bootstrap administrator exists yet."""
        async with self._database.session_maker() as session:
            _, total = await self._auth.list_users(session, offset=0, limit=1)
            return total > 0

    async def setup(
        self,
        email: str,
        password: str,
        *,
        user_agent: str | None,
        ip: str | None,
    ) -> AuthResult:
        """Create the bootstrap administrator account and log them in.

        Single-use: raises AlreadyConfiguredError once any account
        already exists, regardless of the email given. Further accounts
        are created by that administrator through `create_user`.
        """
        async with self._database.session_maker() as session:
            _, total = await self._auth.list_users(session, offset=0, limit=1)
            if total > 0:
                raise AlreadyConfiguredError
            if len(password) < MIN_PASSWORD_LENGTH:
                raise WeakPasswordError
            admin = await self._auth.create_user(
                identifier=Identifier(identifier=email),
                password=password,
                session=session,
                roles=["admin"],
            )
            await self._create_default_access_key(admin)
            pair, user = await self._auth.login(
                LoginRequest(identifier=email, secret=password),
                session,
                user_agent=user_agent,
                ip=ip,
            )
            return AuthResult(pair, user)

    async def login(
        self,
        email: str,
        password: str,
        *,
        user_agent: str | None,
        ip: str | None,
    ) -> AuthResult:
        """Authenticate a user and issue a fresh session."""
        async with self._database.session_maker() as session:
            pair, user = await self._auth.login(
                LoginRequest(identifier=email, secret=password),
                session,
                user_agent=user_agent,
                ip=ip,
            )
            return AuthResult(pair, user)

    async def logout(self, refresh_token: str | None) -> None:
        """End the session associated with a refresh token, if any."""
        if not refresh_token:
            return
        async with self._database.session_maker() as session:
            with suppress(USSOException):
                await self._auth.logout(refresh_token, session)

    async def change_password(
        self,
        user_uid: str,
        *,
        current_password: str,
        new_password: str,
    ) -> None:
        """Change a user's password.

        usso.lite invalidates every active session (including the caller's)
        as part of this -- the route clears cookies afterward rather than
        fighting that, so "changing it signs out every existing session"
        stays true, matching the previous UX.
        """
        async with self._database.session_maker() as session:
            user = await self._auth.get_user(user_uid, session)
            if user is None:
                raise USSOException(
                    404,
                    error_code="user_not_found",
                    message={"en": "User not found", "fa": "کاربر پیدا نشد."},
                )
            await self._auth.change_password(
                user,
                old_password=current_password,
                new_password=new_password,
                session=session,
            )

    # ------------------------------------------------------------------
    # User management (admin surface)
    # ------------------------------------------------------------------
    async def create_user(
        self,
        *,
        email: str,
        password: str,
        role: str,
        name: str | None = None,
    ) -> UserSummary:
        """Create a user with one email identifier and one role."""
        if len(password) < MIN_PASSWORD_LENGTH:
            raise WeakPasswordError
        async with self._database.session_maker() as session:
            try:
                user = await self._auth.create_user(
                    identifier=Identifier(identifier=email),
                    password=password,
                    session=session,
                    name=name,
                    roles=[role],
                )
            except USSOException as error:
                # e.g. 409 identifier_exists -- usso.lite's exception is
                # not an HTTPException, so it would surface as a 500 from
                # the API; re-raise in the app's own error vocabulary.
                raise BaseHTTPException(
                    status_code=error.status_code,
                    error_code=error.error_code,
                    detail=str(error),
                ) from error
            await self._create_default_access_key(user)
            return await self._summarize(user, session)

    async def _create_default_access_key(self, user: LocalUser) -> None:
        """Every account starts with one active access key pair -- what
        signs the user's temporary share links (apps/user_access_keys)."""
        if self._access_keys is not None:
            await self._access_keys.ensure_default_key(user.uid)

    async def get_user_summary(self, uid: str) -> UserSummary | None:
        """Return one user's summary, or None if unknown or deleted."""
        async with self._database.session_maker() as session:
            user = await self._auth.get_user(uid, session)
            if user is None:
                return None
            return await self._summarize(user, session)

    async def list_users(self) -> list[UserSummary]:
        """List every (non-deleted) account with its primary email."""
        async with self._database.session_maker() as session:
            _, total = await self._auth.list_users(session, offset=0, limit=1)
            if total == 0:
                return []
            users, _ = await self._auth.list_users(
                session, offset=0, limit=total,
            )
            return [await self._summarize(user, session) for user in users]

    async def count_admins(self) -> int:
        """Count *active* accounts holding the "admin" role.

        Inactive admins cannot sign in, so they must not satisfy the
        "at least one administrator remains" invariant -- otherwise
        deactivating the last admin (or demoting after deactivating a
        spare) locks the install with no recovery path.
        """
        async with self._database.session_maker() as session:
            return await self._count_admins(session)

    async def update_user(
        self,
        uid: str,
        *,
        name: str | None = _UNSET,  # type: ignore[assignment]
        role: str = _UNSET,  # type: ignore[assignment]
        is_active: bool = _UNSET,  # type: ignore[assignment]
    ) -> UserSummary:
        """Update a user's profile, role and/or active state.

        Passing `name=None` explicitly clears the optional name;
        omitting an argument leaves that field untouched. Demoting or
        deactivating the last *active* administrator is refused -- the
        installation must always keep at least one signed-in-able admin.
        """
        async with self._database.session_maker() as session:
            user = await self._auth.get_user(uid, session)
            if user is None:
                raise UserNotFoundError
            is_admin = "admin" in (user.roles or [])
            demotes_admin = (
                role is not _UNSET and role != "admin" and is_admin
            )
            deactivates_admin = (
                is_active is not _UNSET
                and is_active is False
                and bool(user.is_active)
                and is_admin
                and (role is _UNSET or role == "admin")
            )
            if (
                (demotes_admin or deactivates_admin)
                and await self._count_admins(session) <= 1
            ):
                raise LastAdminRequiredError

            data: dict[str, object] = {}
            if name is not _UNSET and name is not None:
                data["name"] = name
            if role is not _UNSET:
                data["roles"] = [role]
            if is_active is not _UNSET:
                data["is_active"] = is_active
            # usso.lite's update_user skips None values, so an explicit
            # `name=None` (clear it) is applied directly instead.
            user = await self._auth.update_user(user, data, session)
            if name is None and name is not _UNSET and user.name is not None:
                user.name = None
                session.add(user)
                await session.commit()
                await session.refresh(user)
            return await self._summarize(user, session)

    async def delete_user(self, uid: str, *, actor_uid: str) -> None:
        """Soft-delete a user account.

        Refused for the caller's own account (an admin locks themselves
        out otherwise) and for the last administrator.
        """
        async with self._database.session_maker() as session:
            user = await self._auth.get_user(uid, session)
            if user is None:
                raise UserNotFoundError
            if user.uid == actor_uid:
                raise CannotDeleteSelfError
            if "admin" in (user.roles or []):
                if await self._count_admins(session) <= 1:
                    raise LastAdminRequiredError
            await self._auth.delete_user(user, session)

    async def _count_admins(self, session: AsyncSession) -> int:
        _, total = await self._auth.list_users(session, offset=0, limit=1)
        if total == 0:
            return 0
        users, _ = await self._auth.list_users(session, offset=0, limit=total)
        return sum(
            1
            for user in users
            if user.is_active and "admin" in (user.roles or [])
        )

    async def _summarize(
        self, user: LocalUser, session: AsyncSession,
    ) -> UserSummary:
        return UserSummary(
            uid=user.uid,
            email=await self._primary_email(user.uid, session),
            name=user.name,
            roles=list(user.roles or []),
            is_active=user.is_active,
        )

    async def _primary_email(
        self, user_uid: str, session: AsyncSession,
    ) -> str:
        """The user's primary email identifier.

        Every UMedia account is created with exactly one email identifier
        (setup and create_user both require it), so this always resolves;
        usso.lite just doesn't store it on the user row itself.
        """
        rows = await self._auth._get_identifier_rows(user_uid, session)
        emails = [
            row for row in rows if row.type == AuthIdentifier.EMAIL.value
        ]
        for row in emails:
            if row.is_primary:
                return row.identifier
        return emails[0].identifier if emails else ""
