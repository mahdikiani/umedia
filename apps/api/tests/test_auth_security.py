import pytest

from apps.auth.security import (
    InvalidSessionError,
    JwtManager,
    PasswordHasher,
)


def test_password_hash_is_salted_and_verifiable() -> None:
    hasher = PasswordHasher()

    first = hasher.hash("correct horse battery staple")
    second = hasher.hash("correct horse battery staple")

    assert first != second
    assert hasher.verify("correct horse battery staple", first)
    assert not hasher.verify("wrong password", first)


def test_jwt_contains_identity_expiry_and_password_version() -> None:
    manager = JwtManager(b"x" * 32, ttl_seconds=3600)

    token = manager.create(
        admin_id="installation",
        email="admin@example.com",
        password_version=4,
        now=1_000,
    )

    claims = manager.verify(token, password_version=4, now=1_001)
    assert claims["sub"] == "installation"
    assert claims["email"] == "admin@example.com"
    with pytest.raises(InvalidSessionError):
        manager.verify(token, password_version=5, now=1_001)
    with pytest.raises(InvalidSessionError):
        manager.verify(token, password_version=4, now=5_000)
