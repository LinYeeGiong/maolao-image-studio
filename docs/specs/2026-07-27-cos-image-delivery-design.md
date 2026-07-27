# COS image storage and delivery design

## Goal

Reduce image loading time without breaking existing conversations or changing the image generation workflow. New reference and generated images use a private Tencent COS bucket, while existing images continue to use the Docker data volume.

Configured COS target:

- Bucket: `huajing-1437302460`
- Region: `ap-guangzhou`
- Endpoint: `https://huajing-1437302460.cos.ap-guangzhou.myqcloud.com`
- Access: private read

Credentials are backend-only environment variables. They must not be committed, embedded in the frontend, or included in container images.

## Selected approach

The backend uploads new images to COS and exposes stable application URLs. When a browser requests a COS-backed image, the backend returns a short-lived redirect to a signed COS URL. The browser then downloads the bytes directly from COS.

This preserves private access and stable frontend URLs while removing large image transfers from FastAPI and the application server.

Rejected alternatives:

- Proxying COS bytes through FastAPI preserves the current public bandwidth bottleneck.
- Browser uploads with temporary STS credentials add unnecessary authorization and state-management complexity for the current application.

## Storage abstraction

Image business logic uses a `StorageService` interface rather than directly reading or writing files.

Implementations:

- `LocalStorage`: reads historical images and stores COS outage fallbacks.
- `CosStorage`: stores new reference images, generated originals, previews, and thumbnails.

Database image records retain `stored_name` for backward compatibility and add:

- `storage_backend`: `local` or `cos`
- `object_key`: original COS object key
- `preview_key`: preview COS object key
- `thumbnail_key`: thumbnail COS object key
- `storage_status`: `ready` or `pending_upload`

`stored_name` remains populated for new COS rows so the current non-null constraint and older application code remain safe during a rolling deployment. Existing rows are treated as local without requiring a bulk migration.

A separate `pending_storage_deletions` table stores COS object keys that could not be deleted after their conversation records were removed. This makes deletion retries independent of the deleted image rows.

## Object naming

Objects are isolated by conversation, turn, and image:

```text
maolao/{conversation_id}/{turn_id}/{image_id}/original.{extension}
maolao/{conversation_id}/{turn_id}/{image_id}/preview.webp
maolao/{conversation_id}/{turn_id}/{image_id}/thumbnail.webp
```

Object keys are immutable. Replacing image content creates a new image ID and new keys.

## Image processing

For each new reference image and generated image:

1. Validate the source type and content using the existing limits.
2. Keep the source in a bounded temporary file while processing.
3. Preserve the source as the original.
4. Create a WebP preview with a maximum 1440-pixel long edge.
5. Create a WebP thumbnail with a maximum 480-pixel long edge.
6. Preserve aspect ratio and apply EXIF orientation.
7. Upload all variants to COS and persist their object keys.
8. Delete temporary files after successful persistence.

The original image is always used when sending a reference to the upstream image API. Preview variants are only for browser display.

## Failure and retry behavior

COS errors are distinct from upstream generation errors and must never be reported as `openai_error`.

If a COS upload fails:

- Save the original and generated variants in the local data volume.
- Mark the image as `pending_upload`.
- Return working local URLs so the completed generation remains available.
- Retry COS upload in the background and periodically while the backend is running.
- After all variants are confirmed in COS, switch the record to `cos`, mark it `ready`, and remove only the corresponding fallback files.

Retries are bounded and use backoff. A single backend lifecycle worker scans persisted pending uploads at startup and at a fixed interval. Restarting the backend therefore resumes pending records from the database. A failed retry must not make an existing local image unavailable.

If COS is disabled or credentials are incomplete, the application starts in local-only mode and emits a clear configuration warning.

## API delivery

Each image response includes stable application URLs:

- `url`: backward-compatible display URL
- `thumbnail_url`: 480-pixel variant
- `preview_url`: 1440-pixel variant
- `download_url`: original image

Routes use the image ID rather than exposing COS keys:

```text
/api/v1/images/{image_id}/thumbnail
/api/v1/images/{image_id}/preview
/api/v1/images/{image_id}/original
/api/v1/images/{image_id}/download
```

For local images, routes return a file response. If a historical image has no variants, thumbnail and preview routes return the original.

For COS images, routes issue a short-lived HTTP redirect to a signed COS URL. Download redirects request attachment content disposition using the original filename. Missing database records return 404; missing storage objects return a specific storage error and are logged without leaking credentials or signed URLs.

The default signature lifetime is one hour. COS object responses use long-lived caching because keys are immutable; application redirect responses use short caching.

## Frontend behavior

- Conversation messages render `preview_url`, not the 4K original.
- Small history cards and compact selectors render `thumbnail_url`.
- The original is requested only when the user opens the full-size viewer or downloads it.
- Images use lazy loading, asynchronous decoding, and an explicit aspect ratio.
- Loading placeholders reserve layout space and prevent visual shifting.
- Multiple results use bounded concurrent loading rather than starting every full request at once.
- A failed image shows an in-site retry control; no browser alert boxes are used.
- Existing image actions and the ability to use a generated image as the next reference remain unchanged.

## Deletion

Deleting a conversation:

- Deletes existing local images using the current scoped behavior.
- Deletes COS objects listed by that conversation's image records.
- Never performs a broad bucket or unvalidated prefix deletion.
- Writes failed COS object keys to `pending_storage_deletions` before removing the conversation rows, then retries them from the lifecycle worker while allowing the conversation to disappear from the UI.

Credentials and signed URLs are excluded from logs.

## Configuration

Backend environment variables:

```env
COS_ENABLED=true
COS_SECRET_ID=
COS_SECRET_KEY=
COS_BUCKET=huajing-1437302460
COS_REGION=ap-guangzhou
COS_ENDPOINT=https://huajing-1437302460.cos.ap-guangzhou.myqcloud.com
COS_SIGNED_URL_TTL=3600
COS_OBJECT_PREFIX=maolao
```

The production Compose file passes these variables to the backend. Secret values remain in the server `.env`; GitHub Actions builds do not require them.

The COS CORS policy permits the production site origin and local development origin:

- Methods: `GET`, `HEAD`
- Allowed headers: `*`
- Exposed headers: `ETag`, `Content-Length`, `Content-Type`
- Max age: `86400`

The CAM identity follows least privilege and is scoped to the configured bucket and object prefix for upload, read, and delete operations.

## Verification

Backend tests cover:

- Automatic schema upgrade and legacy local rows.
- COS upload, signing, redirect, read, and scoped deletion.
- Correct original, preview, and thumbnail dimensions and formats.
- COS failure fallback and restart-safe retry.
- Local-only startup when COS is not configured.
- No credential leakage in API responses or logs.
- Multiple generated images and up to 16 reference images.
- Conversation deletion cannot remove objects from another conversation.

Frontend tests and checks cover:

- Correct use of thumbnail, preview, and original routes.
- Lazy loading and stable image layout.
- Full-size viewing and download behavior.
- In-site retry behavior.
- Compatibility with historical local image records.

Docker verification covers local-only mode and COS-enabled mode. A live COS smoke test is run only after credentials are configured and uses a dedicated test prefix that is removed after verification.

## Success criteria

- Existing conversations and local images remain readable.
- New images are stored privately in COS when COS is available.
- Chat rendering no longer downloads 4K originals by default.
- Generated images remain available during a COS outage.
- Image bytes are delivered directly by COS after authorization.
- Secrets never reach the frontend, repository, image, or logs.
