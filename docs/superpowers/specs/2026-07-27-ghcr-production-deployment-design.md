# GHCR Production Deployment Design

## Goal

Publish the existing frontend and backend as public GitHub Container Registry
images so a server can deploy or upgrade the application with Docker Compose
without building source code locally.

## Image architecture

The existing two-container architecture remains unchanged:

- `ghcr.io/linyeegiong/maolao-image-studio-backend`
- `ghcr.io/linyeegiong/maolao-image-studio-frontend`

The frontend image serves the built application with Nginx. Requests under
`/api/` continue to be proxied to `backend:8000` over the private Compose
network. The backend is not published on the host.

Each successful build from `main` publishes both `latest` and a commit-SHA tag.
The SHA tag provides a stable rollback target. The workflow also supports
manual dispatch.

## Server deployment

A separate production Compose file references the registry images instead of
local build contexts. It publishes the frontend as
`127.0.0.1:7820:80`, leaving public access and TLS termination to the server's
existing reverse proxy.

The backend reads `MAOLAO_API_KEY` and optional settings from a server-local
`.env` file. Secrets are never copied into an image or committed to Git.
Conversation and generation records remain in the named `maolao_data` volume,
so image upgrades do not remove application data.

Deployment and upgrades use:

```bash
docker compose -f compose.prod.yml pull
docker compose -f compose.prod.yml up -d
```

## Publication and visibility

GitHub Actions uses the repository `GITHUB_TOKEN` with `packages: write` to
authenticate to GHCR. After the first successful publication, both container
packages must be set to Public in GitHub Packages if GitHub does not inherit
public visibility automatically. A public package lets the production server
pull images without registry credentials.

## Failure handling and verification

The workflow builds both images for Linux AMD64 and stops if either build
fails. Build cache is stored through GitHub Actions. Before pushing, local
verification covers backend tests, frontend production build, and Compose
configuration rendering. After pushing, the GitHub Actions run must complete
successfully before the images are considered deployable.

The production runbook documents initial installation, `.env` creation,
reverse-proxy target, upgrade, rollback by SHA tag, status inspection, and log
commands.

## Scope

This change adds image publication, production Compose configuration, and
deployment documentation. It does not restart or replace the currently running
local Docker containers, automatically SSH into the server, or store production
credentials in GitHub.
