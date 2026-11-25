# Running UMedia inside another product

UMedia has two jobs: work on its own, and act as the file layer of a bigger
product. The first never depends on the second.

## Authentication modes (`UMEDIA_AUTH_MODE`)

| Mode | Who signs users in | When |
|---|---|---|
| `lite` (default) | UMedia itself (`usso.lite`: first-run admin, optional Google sign-in) | Standalone install |
| `usso` | The host product, through USSO | UMedia is a building block of that product |

### `usso` mode (token only)

Set `UMEDIA_AUTH_MODE=usso` and `USSO_BASE_URL=https://<your-usso>`.
Optionally set `UMEDIA_USSO_TENANT_ID` to accept only that tenant's tokens.

- The USSO access token is read from the `usso-access-token` cookie, the
  `Authorization` header or an API key, and verified against the USSO
  server's JWKS (`apps/auth/usso_mode.py`).
- The user id (`sub`) is the owner of files and connections; the `admin`
  role comes from the token's roles.
- UMedia's own sign-in, setup, password and user-admin endpoints return 404
  (`auth_managed_by_usso`). `GET /auth/state` and `GET /auth/sessions/current`
  still answer.
- Provider tokens (Google Drive, Dropbox, ...) stay encrypted in UMedia's
  database. They are never merged with USSO.

Because the browser already holds the host's USSO cookie, the user needs no
second sign-in. The only unavoidable prompt is the provider's own consent
screen the first time a Drive is connected.

### Not built yet

- Serving UMedia under a route of the host's domain (a configurable base
  path instead of the fixed `/api/v1`, plus the web `basePath`).
- `tenant_id` / `workspace_id` stored on files and connections.
- UMedia acting as an OIDC client of USSO (second mode, lower priority).
- Connecting a Drive with a real redirect callback instead of the paste flow.
