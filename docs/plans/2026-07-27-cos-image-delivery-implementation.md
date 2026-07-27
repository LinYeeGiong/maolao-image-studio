# COS Image Delivery Implementation Plan

> **For agentic workers:** Execute this plan task-by-task in the current session. Use test-driven development and verify each task before moving on.

**Goal:** Store all newly uploaded and generated images in private Tencent COS, serve lightweight variants directly from COS, retain legacy local media, and fall back safely to local storage during COS outages.

**Architecture:** A focused storage service owns image transformation and local/COS persistence. Stable FastAPI image routes either return legacy/local files or redirect to signed private COS URLs. SQLite records the storage backend and variant keys so conversations remain compatible across restarts.

**Tech Stack:** FastAPI, SQLite, Pillow, Tencent COS Python SDK, React 19, TypeScript, Docker Compose.

## Global Constraints

- COS remains private read; credentials exist only in backend environment variables.
- Existing image rows continue to read from `/data/media` without migration.
- New images fall back to local files with `pending_upload` when COS is unavailable.
- Chat messages load 1440px WebP previews; compact UI loads 480px WebP thumbnails; originals load only for download/full-size use.
- Do not start or rebuild Docker and do not make a live COS request before the user configures credentials.
- Do not expose credentials, signed URLs, or broad COS delete operations in logs or APIs.

---

### Task 1: Configuration and backward-compatible schema

**Files:**
- Modify: `backend/app/core/settings.py`
- Modify: `backend/app/core/database.py`
- Modify: `backend/pyproject.toml`
- Modify: `backend/uv.lock`
- Test: `backend/unit_tests/test_storage_database.py`

**Interfaces:**
- Produces settings named `COS_ENABLED`, `COS_SECRET_ID`, `COS_SECRET_KEY`, `COS_BUCKET`, `COS_REGION`, `COS_ENDPOINT`, `COS_SIGNED_URL_TTL`, and `COS_OBJECT_PREFIX`.
- Produces nullable image columns `storage_backend`, `object_key`, `preview_key`, `thumbnail_key`, and non-null `storage_status`.
- Produces `pending_storage_deletions(id, object_key, created_at, attempts, last_error)`.

- [ ] Write a failing test that initializes a legacy database, calls `init_database()`, and asserts the new columns and deletion queue exist while the legacy row remains readable.
- [ ] Run `uv run pytest unit_tests/test_storage_database.py -v` and verify the schema assertions fail.
- [ ] Add the COS settings with safe empty/default values; add Pillow and `cos-python-sdk-v5` dependencies.
- [ ] Extend `init_database()` with idempotent `PRAGMA table_info` migrations and the deletion queue table. Existing rows must resolve to local storage through `COALESCE(storage_backend, 'local')` semantics.
- [ ] Run the focused test and then `uv run pytest unit_tests -v`.
- [ ] Commit as `feat: add COS storage configuration and schema`.

### Task 2: Image variants and storage service

**Files:**
- Create: `backend/app/core/image_storage.py`
- Test: `backend/unit_tests/test_image_storage.py`

**Interfaces:**
- Produces `StoredImage` dataclass containing `stored_name`, `storage_backend`, `storage_status`, `object_key`, `preview_key`, and `thumbnail_key`.
- Produces `store_image(conversation_id, turn_id, image_id, extension, mime_type, content) -> StoredImage`.
- Produces `read_original(row) -> bytes`, `signed_url(row, variant, download_name=None) -> str`, `delete_image_objects(row)`, `retry_pending_uploads_once()`, and `retry_pending_deletions_once()`.
- COS client creation is lazy and only occurs when all required settings are present.

- [ ] Write failing tests using a generated Pillow image and a fake COS client. Assert previews have a maximum long edge of 1440, thumbnails a maximum long edge of 480, both are WebP, original bytes are preserved, and object keys remain under the configured conversation prefix.
- [ ] Add tests proving a COS upload failure returns local `pending_upload`, a later retry switches the row to COS, and disabled COS produces ready local storage.
- [ ] Run `uv run pytest unit_tests/test_image_storage.py -v` and verify failures.
- [ ] Implement image orientation, RGB/RGBA-safe WebP conversion, immutable object naming, local-first persistence, all-or-nothing COS upload, signed GET URLs, scoped object deletion, and persisted retries.
- [ ] Ensure exceptions are reported as `cos_upload_error`, `cos_read_error`, or `cos_delete_error` without secret values or signed query strings.
- [ ] Run focused tests, Ruff, and all backend unit tests.
- [ ] Commit as `feat: add private COS image storage`.

### Task 3: Conversation integration and stable media routes

**Files:**
- Create: `backend/app/api/routes/media.py`
- Modify: `backend/app/api/main.py`
- Modify: `backend/app/api/routes/conversations.py`
- Modify: `backend/app/api/routes/images.py`
- Modify: `backend/app/main.py`
- Test: `backend/unit_tests/test_media_delivery.py`
- Test: `backend/unit_tests/test_reference_uploads.py`

**Interfaces:**
- Produces GET routes `/api/v1/images/{image_id}/thumbnail`, `/preview`, `/original`, and `/download`.
- Image payloads include `url`, `thumbnail_url`, `preview_url`, and `download_url`.
- Lifecycle starts one cancellable retry loop for pending uploads and deletions.

- [ ] Write failing API/unit tests asserting legacy local rows return file responses, COS rows redirect to the signer, missing records return 404, and payloads expose stable application URLs rather than COS keys.
- [ ] Add a failing test proving conversation deletion queues only the exact COS keys belonging to that conversation.
- [ ] Route new references and generated results through `store_image`; load inherited/source originals through `read_original` so COS references still work with Maolao edits.
- [ ] Preserve `stored_name` for every new row and populate the storage columns from `StoredImage`.
- [ ] Update conversation deletion to delete local files and persist exact COS keys before deleting conversation rows. Trigger an immediate bounded cleanup attempt after commit.
- [ ] Register media routes and add a cancellable lifecycle retry task without changing pending Maolao turn resumption.
- [ ] Run focused tests, all backend tests, and Ruff.
- [ ] Commit as `feat: serve optimized images from COS`.

### Task 4: Frontend optimized loading and in-site retry

**Files:**
- Create: `frontend/src/OptimizedImage.tsx`
- Modify: `frontend/src/ImageGenerator.tsx`
- Modify: `frontend/src/index.css`

**Interfaces:**
- `StudioImage` consumes `thumbnail_url`, `preview_url`, and `download_url`, retaining `url` as fallback.
- `OptimizedImage` accepts `src`, `alt`, optional `className`, optional `loading`, and optional `aspectRatio`; it uses asynchronous decoding and an in-site retry control.

- [ ] Implement `OptimizedImage` with a loading skeleton, `loading="lazy"`, `decoding="async"`, a retry nonce, and no browser alert.
- [ ] Render reference grids and selected source images with thumbnail URLs; render generated chat results with preview URLs; use download URLs for original downloads.
- [ ] Pass aspect ratios derived from the selected generation size and reserve layout space before image decode.
- [ ] Update the composer note to say new images use private cloud storage while existing records remain supported.
- [ ] Add CSS for skeletons, retry overlays, responsive aspect ratios, and existing hover actions.
- [ ] Run `npm run build` and verify TypeScript and Vite succeed.
- [ ] Commit as `feat: load optimized image previews`.

### Task 5: Deployment configuration and offline verification

**Files:**
- Modify: `.env.example`
- Modify: `compose.prod.yml`
- Modify: `README.md`

**Interfaces:**
- Production Compose passes all COS settings only to the backend.
- Documentation describes private bucket, least-privilege CAM, CORS, and the exact environment variables without containing a real secret.

- [ ] Add blank secret values and the confirmed bucket defaults to `.env.example`.
- [ ] Pass COS variables through `compose.prod.yml` with disabled/empty-safe defaults so local-only startup remains possible.
- [ ] Document Tencent COS CORS (`GET`, `HEAD`; site origins; exposed `ETag`, `Content-Length`, `Content-Type`; max age 86400) and least-privilege object permissions under `maolao/*`.
- [ ] Run `uv run pytest unit_tests -v`, `uv run ruff check app unit_tests`, `npm run build`, `docker compose -f compose.prod.yml config --quiet`, and `git diff --check`. The Compose command validates configuration only and must not start containers.
- [ ] Inspect `git diff` for secrets and verify no real `COS_SECRET_ID` or `COS_SECRET_KEY` value is present.
- [ ] Commit as `docs: configure COS image delivery`.
- [ ] Stop and ask the user to configure environment variables. Do not start Docker, push Git, or perform a live COS smoke test.
