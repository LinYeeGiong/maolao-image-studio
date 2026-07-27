# GHCR Production Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the frontend and backend as public GHCR images and provide a production Compose deployment bound to `127.0.0.1:7820`.

**Architecture:** A GitHub Actions matrix builds the existing frontend and backend Dockerfiles for Linux AMD64 and publishes each image with `latest` and commit-SHA tags. A separate production Compose file pulls those images, keeps FastAPI private on the Compose network, exposes only Nginx on localhost port 7820, and persists `/data` in the existing named volume.

**Tech Stack:** GitHub Actions, Docker Buildx, GitHub Container Registry, Docker Compose, Nginx, FastAPI

## Global Constraints

- Publish `ghcr.io/linyeegiong/maolao-image-studio-backend` and `ghcr.io/linyeegiong/maolao-image-studio-frontend`.
- Build Linux AMD64 images on pushes to `main` and on manual workflow dispatch.
- Publish both `latest` and `sha-<short-commit>` tags.
- Bind the frontend only to `127.0.0.1:7820:80`; do not publish the backend port.
- Keep `MAOLAO_API_KEY` only in the server-local `.env` file.
- Preserve application data in the `maolao_data` named volume.
- Do not restart or rebuild the currently running local Docker services.

---

### Task 1: GitHub Actions image publication

**Files:**
- Create: `.github/workflows/publish-images.yml`

**Interfaces:**
- Consumes: `backend/Dockerfile`, `frontend/Dockerfile`, and the repository root as Docker build context.
- Produces: Two GHCR packages tagged `latest` and `sha-<short-commit>`.

- [ ] **Step 1: Add the publication workflow**

Create a workflow with `contents: read` and `packages: write`, triggered by pushes to `main` and `workflow_dispatch`. Use a matrix containing the backend and frontend Dockerfile paths and lowercase GHCR image names. For each matrix entry, run `docker/login-action@v3`, `docker/metadata-action@v5`, `docker/setup-buildx-action@v3`, and `docker/build-push-action@v6`. Configure metadata tags as:

```yaml
tags: |
  type=raw,value=latest,enable={{is_default_branch}}
  type=sha,prefix=sha-,format=short
```

Configure the build as:

```yaml
context: .
file: ${{ matrix.dockerfile }}
platforms: linux/amd64
push: true
cache-from: type=gha,scope=${{ matrix.component }}
cache-to: type=gha,mode=max,scope=${{ matrix.component }}
```

- [ ] **Step 2: Validate workflow structure**

Run:

```powershell
Get-Content -Raw .github/workflows/publish-images.yml
```

Expected: the workflow contains two matrix entries, `packages: write`, `linux/amd64`, and both metadata tag rules.

### Task 2: Production Compose deployment

**Files:**
- Create: `compose.prod.yml`

**Interfaces:**
- Consumes: Public GHCR images, `.env`, Docker DNS name `backend`, and optional `APP_HOST`, `APP_PORT`, `BACKEND_IMAGE`, and `FRONTEND_IMAGE` overrides.
- Produces: Nginx at `127.0.0.1:7820` and a private backend with persistent `/data`.

- [ ] **Step 1: Add the production Compose file**

Define the backend with:

```yaml
image: ${BACKEND_IMAGE:-ghcr.io/linyeegiong/maolao-image-studio-backend:latest}
pull_policy: always
env_file:
  - .env
environment:
  MAOLAO_BASE_URL: ${MAOLAO_BASE_URL:-https://maolaoapi.com}
  DATA_DIR: /data
  MAOLAO_API_KEY: ${MAOLAO_API_KEY:?Set MAOLAO_API_KEY in .env}
expose:
  - "8000"
volumes:
  - maolao_data:/data
```

Define the frontend with:

```yaml
image: ${FRONTEND_IMAGE:-ghcr.io/linyeegiong/maolao-image-studio-frontend:latest}
pull_policy: always
depends_on:
  - backend
ports:
  - "${APP_HOST:-127.0.0.1}:${APP_PORT:-7820}:80"
```

Both services use `restart: unless-stopped`; retain the current backend DNS servers and the named `maolao_data` volume.

- [ ] **Step 2: Render and inspect the Compose configuration**

Run:

```powershell
$env:MAOLAO_API_KEY='validation-only'; docker compose -f compose.prod.yml config; Remove-Item Env:MAOLAO_API_KEY
```

Expected: exit code 0, frontend published on host IP `127.0.0.1` and port `7820`, backend has no `ports` entry, and both GHCR image references are present.

### Task 3: Deployment runbook

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: `compose.prod.yml` and the GitHub Actions workflow.
- Produces: Copy-paste commands for first deployment, updates, logs, rollback, and package visibility.

- [ ] **Step 1: Add production deployment instructions**

Document these exact operational steps:

1. Copy `compose.prod.yml` and `.env.example` to an empty server directory and rename `.env.example` to `.env`.
2. Set `MAOLAO_API_KEY` in `.env` and keep `MAOLAO_BASE_URL=https://maolaoapi.com`.
3. Run `docker compose -f compose.prod.yml pull` and `docker compose -f compose.prod.yml up -d`.
4. Point the reverse proxy to `http://127.0.0.1:7820`.
5. Upgrade with the same `pull` and `up -d` commands.
6. Inspect with `docker compose -f compose.prod.yml ps` and `docker compose -f compose.prod.yml logs -f --tail=200`.
7. Roll back by setting `BACKEND_IMAGE` and `FRONTEND_IMAGE` to matching `sha-...` tags in `.env`, then run `up -d`.
8. After the first workflow run, set both GHCR packages to Public in GitHub Packages if they are private.

Explicitly warn that `docker compose down -v` deletes persisted conversations and images.

- [ ] **Step 2: Verify documentation references**

Run:

```powershell
rg -n "compose.prod.yml|127.0.0.1:7820|BACKEND_IMAGE|FRONTEND_IMAGE|GitHub Packages" README.md
```

Expected: each deployment concept appears at least once.

### Task 4: Full verification, commit, push, and publication check

**Files:**
- Verify: `.github/workflows/publish-images.yml`
- Verify: `compose.prod.yml`
- Verify: all existing backend and frontend source changes

**Interfaces:**
- Consumes: the complete working tree and configured `origin` remote.
- Produces: a pushed `main` branch and a successful GitHub Actions publication run.

- [ ] **Step 1: Run backend tests**

Run:

```powershell
uv run --package app pytest backend/unit_tests -q
```

Expected: exit code 0 with all tests passing.

- [ ] **Step 2: Run the frontend production build**

Run:

```powershell
bun run build
```

Expected: exit code 0 and Vite reports a successful production build.

- [ ] **Step 3: Check diffs and secret exclusions**

Run:

```powershell
git diff --check
git status --short
git check-ignore .env
git grep -n "sk-" -- ':!.env.example' ':!docs/**'
```

Expected: no whitespace errors, `.env` is ignored, and no real API key appears in tracked files.

- [ ] **Step 4: Commit all intended project changes**

Run:

```powershell
git add .
git commit -m "feat: add conversations and GHCR deployment"
```

Expected: one commit containing the existing completed application work plus the deployment workflow, production Compose file, plan, and documentation; `.env` and generated caches remain untracked or ignored.

- [ ] **Step 5: Push main**

Run:

```powershell
git push origin main
```

Expected: GitHub accepts the new commits and starts the `Publish container images` workflow.

- [ ] **Step 6: Confirm the publication run**

Run:

```powershell
gh run list --workflow publish-images.yml --limit 1
gh run watch --exit-status
```

Expected: the latest workflow finishes with `success`. If `gh` is unavailable or unauthenticated, provide the repository Actions URL and state that publication status requires user confirmation in GitHub.
