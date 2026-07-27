# Result URL Image Download Design

## Problem

Maolao image tasks can complete successfully with generated images exposed as
absolute pre-signed HTTPS URLs in `result.data[].url`. The current backend
ignores those URLs and always requests the Maolao compatibility endpoint
`/v1/images/tasks/{task_id}/content/{index}`. That endpoint can return
`404 image content not found` even while the absolute result URL returns the
generated PNG successfully.

## Result selection

For each delivered image, the backend selects its download target in this
order:

1. An absolute HTTPS URL in `result.data[index].url`.
2. A relative URL in `result.data[index].url`, resolved against
   `MAOLAO_BASE_URL`.
3. The existing `/v1/images/tasks/{task_id}/content/{index}` endpoint when the
   result item has no usable URL.

Absolute result URLs receive no Maolao authorization header, preventing the API
key from being disclosed to S3 or another image host. Relative and fallback
Maolao URLs use the existing bearer authorization header. Absolute URLs with a
scheme other than HTTPS are rejected.

## Download retry

Downloading an already-generated result is safe to retry because it does not
create a new generation task. The backend retries the same URL after temporary
HTTP statuses `404`, `409`, `425`, `429`, and `5xx`, with a short fixed delay
and a bounded attempt count. Other `4xx` responses fail immediately.

The backend never automatically resubmits a failed generation task, so this
change cannot cause duplicate image generation or duplicate billing.

## Error reporting

If an exception has an empty string representation, the stored task error uses
the exception class name, such as `ReadTimeout`, instead of an empty message.
Upstream task failures such as `openai_error` remain unchanged.

## Testing

Unit tests cover:

- absolute HTTPS result URLs without authorization;
- relative result URLs with Maolao authorization;
- missing result URLs falling back to the content endpoint;
- rejection of insecure absolute URLs;
- retry of a temporary content response followed by success;
- immediate failure for a permanent client error;
- non-empty formatting for exceptions such as `httpx.ReadTimeout`.

Existing request construction, multi-reference upload, backend tests, frontend
production build, Compose configuration, and secret checks must continue to
pass.

## Deployment

After verification, the fix is committed and pushed to `main`. GitHub Actions
publishes updated public frontend and backend images. Local production
verification pulls the new images through `compose.prod.yml`, recreates the
services without deleting the named data volume, and verifies the frontend and
backend through the local port.
