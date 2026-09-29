# Self-host UMedia

## Requirements

- Docker Engine with the Docker Compose plugin.
- A running Traefik instance attached to a Docker network named `traefik-net`.
- A DNS name routed to that Traefik instance. The checked-in `compose.yaml` currently uses `drive.uln.me` in its router rules.

The current Compose file is designed for the project's Traefik deployment, not a standalone local installation. It does not publish host ports for the API or web app. A standalone local Compose profile is not included yet.

## Start the stack

Clone the repository, create the external network if it does not already exist, then build and start the services:

```bash
git clone https://github.com/mahdikiani/umedia.git
cd umedia
docker network create traefik-net
docker compose up --build -d
```

If `traefik-net` already exists, Docker reports that the name is in use; continue with the next command. The API health check must pass before the web container starts.

For a different hostname, update the `Host(...)` rules in `compose.yaml` and configure DNS and TLS in Traefik. The host must resolve to the server running Traefik.

## Persistent data

The default Compose file stores the SQLite database and generated encryption key under `./volumes/data`, and the local provider's files under `./volumes/storage`. Back up both directories together and test restoration before relying on the installation.

## Optional provider credentials

The root `.env.example` documents supported environment variable names, but the current `compose.yaml` does not forward every optional provider variable to the API service automatically. Before configuring OAuth or Telegram, add the needed variables to the API service's `environment` section and supply their values through your deployment's secret-management process. Do not commit real credentials.

- Google Drive: `UMEDIA_GOOGLE_OAUTH_CLIENT_ID` and `UMEDIA_GOOGLE_OAUTH_CLIENT_SECRET`.
- OneDrive: `UMEDIA_ONEDRIVE_OAUTH_CLIENT_ID` and `UMEDIA_ONEDRIVE_OAUTH_CLIENT_SECRET`.
- Dropbox: `UMEDIA_DROPBOX_OAUTH_CLIENT_ID` and `UMEDIA_DROPBOX_OAUTH_CLIENT_SECRET`.
- Telegram: `UMEDIA_TELEGRAM_API_ID` and `UMEDIA_TELEGRAM_API_HASH`.

## First run and troubleshooting

Open the configured hostname in a browser and create the first administrator account if the installation is fresh. Connect a provider from onboarding, then test browsing and file operations with non-critical data.

Check service status and recent logs with:

```bash
docker compose ps
docker compose logs --tail=100 api web
```

When requesting help, redact hostnames if private, tokens, OAuth values, Telegram session strings, and personal file names from logs.
