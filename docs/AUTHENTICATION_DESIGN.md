# Authentication Design

> **Superseded** by the `00`–`09` doc series (start at
> [`08-implementation-plan.md`](./08-implementation-plan.md)). Kept for
> reference; auth now runs on `usso.lite` instead of the hand-rolled
> scheme described below, see
> [`02-architecture.md`](./02-architecture.md#auth-concrete).

UMedia is a single-tenant, single-administrator installation.

## First-run setup

The first visit asks for an administrator email address and a password of at
least 12 characters. Setup is single-use and protected by a fixed singleton
database identity.

Passwords are salted and hashed with `scrypt`. Plaintext passwords are never
stored or logged.

## JWT sessions

Successful setup or login issues an HS256 JWT containing:

- administrator subject and email
- issued-at and expiry timestamps
- unique token ID
- token type and issuer/audience restrictions
- current password version

The signing key is derived from the installation master key. Browser sessions
use a `Secure`, `HttpOnly`, `SameSite=Strict` cookie. API clients may use the
returned token as `Authorization: Bearer <token>`. Access tokens expire after
24 hours.

Changing the password increments `password_version`, immediately invalidating
every previously issued JWT.

## Central enforcement

Authentication is enforced by middleware at the `/api/v1` boundary, so adding
a route without a dependency cannot accidentally expose it.

The explicit public allowlist is limited to:

- health/readiness probes (`/health` and `/ready`)
- authentication state, setup and login
- `GET` and `HEAD` public-share content routes

All other resources require a current JWT, including file metadata, file
content, provider connections, jobs, search, settings, and API documentation.
Public-share routes must additionally validate the share token, expiry,
password policy, and download limit in the sharing service.

Cookie-authenticated unsafe requests are subject to an Origin check in addition
to `SameSite=Strict`.
