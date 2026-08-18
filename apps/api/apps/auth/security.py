"""Password hashing and signed session tokens."""

import base64
import hashlib
import hmac
import secrets
from typing import Any

import jwt


class InvalidSessionError(ValueError):
    """Raised when a session token is invalid or expired."""


class PasswordHasher:
    """Hash passwords with the memory-hard scrypt KDF."""

    algorithm = "scrypt"
    n = 2**14
    r = 8
    p = 1
    dklen = 32

    def hash(self, password: str) -> str:
        """Create a self-describing salted password hash."""
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(
            password.encode(),
            salt=salt,
            n=self.n,
            r=self.r,
            p=self.p,
            dklen=self.dklen,
        )
        return "$".join(
            [
                self.algorithm,
                str(self.n),
                str(self.r),
                str(self.p),
                base64.urlsafe_b64encode(salt).decode(),
                base64.urlsafe_b64encode(digest).decode(),
            ],
        )

    def verify(self, password: str, encoded_hash: str) -> bool:
        """Verify a password without leaking comparison timing."""
        try:
            algorithm, n, r, p, encoded_salt, encoded_digest = encoded_hash.split(
                "$",
            )
            if algorithm != self.algorithm:
                return False
            salt = base64.urlsafe_b64decode(encoded_salt)
            expected = base64.urlsafe_b64decode(encoded_digest)
            actual = hashlib.scrypt(
                password.encode(),
                salt=salt,
                n=int(n),
                r=int(r),
                p=int(p),
                dklen=len(expected),
            )
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(actual, expected)


class JwtManager:
    """Issue and verify administrator JSON Web Tokens."""

    def __init__(self, key: bytes, *, ttl_seconds: int) -> None:
        self._key = hashlib.sha256(key).digest()
        self._ttl_seconds = ttl_seconds

    def create(
        self,
        *,
        admin_id: str,
        email: str,
        password_version: int,
        now: int,
    ) -> str:
        """Create a signed JWT with the administrator identity."""
        payload = {
            "sub": admin_id,
            "email": email,
            "iat": now,
            "exp": now + self._ttl_seconds,
            "jti": secrets.token_urlsafe(16),
            "pwd_ver": password_version,
            "type": "access",
            "iss": "umedia",
            "aud": "umedia-api",
        }
        return jwt.encode(payload, self._key, algorithm="HS256")

    def verify(
        self,
        token: str,
        *,
        password_version: int,
        now: int,
    ) -> dict[str, Any]:
        """Validate JWT signature, claims, expiry and password version."""
        try:
            payload = jwt.decode(
                token,
                self._key,
                algorithms=["HS256"],
                audience="umedia-api",
                issuer="umedia",
                options={"verify_exp": False, "require": ["sub", "iat", "exp", "jti"]},
            )
            if payload["exp"] < now:
                raise InvalidSessionError
            if payload["pwd_ver"] != password_version:
                raise InvalidSessionError
            if payload.get("type") != "access":
                raise InvalidSessionError
        except (KeyError, TypeError, ValueError, jwt.PyJWTError) as error:
            raise InvalidSessionError from error
        return payload
