# Result URL Image Download Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Download successful Maolao image results from the URL returned in each result item, while retaining an authenticated compatibility fallback.

**Architecture:** Pure helpers select a download URL and authorization policy from each result item. An async downloader retries only retrieval of an already-generated image, and `process_turn` saves the downloaded content without ever resubmitting generation.

**Tech Stack:** Python 3.14, FastAPI, HTTPX, pytest, Docker Compose, GitHub Actions

## Global Constraints

- Prefer `result.data[index].url` over the compatibility content endpoint.
- Never send `MAOLAO_API_KEY` to an absolute external result URL.
- Reject non-HTTPS absolute result URLs.
- Authenticate relative result URLs and the compatibility fallback.
- Retry only image retrieval for `404`, `409`, `425`, `429`, and `5xx`.
- Never automatically create a replacement generation task.
- Store an exception class name when the exception message is empty.
- Preserve all existing request construction and multi-reference behavior.

---

### Task 1: Result request selection

**Files:**
- Modify: `backend/unit_tests/test_image_gateway.py`
- Modify: `backend/app/api/routes/images.py`

**Interfaces:**
- Consumes: a result item, upstream task ID, result index, `MAOLAO_BASE_URL`, and `_headers()`.
- Produces: `ImageDownloadRequest(url: str, headers: dict[str, str] | None)`.

- [ ] **Step 1: Add failing selection tests**

Add tests asserting that `build_image_download_request`:

```python
external = build_image_download_request(
    result_item={"url": "https://example-bucket.s3.amazonaws.com/result.png"},
    upstream_task_id="task-1",
    index=0,
)
assert external.url == "https://example-bucket.s3.amazonaws.com/result.png"
assert external.headers is None
```

Also assert that a relative `/v1/...` URL and a missing URL use Maolao
authorization, and that `http://external.example/result.png` raises
`ValueError`.

- [ ] **Step 2: Verify the tests fail**

Run:

```powershell
uv run --package app pytest backend/unit_tests/test_image_gateway.py -q
```

Expected: collection fails because `build_image_download_request` does not
exist.

- [ ] **Step 3: Implement request selection**

Add an immutable `ImageDownloadRequest` dataclass and
`build_image_download_request`. Parse the supplied URL with `urlparse`.
Absolute URLs must use `https` and return `headers=None`; relative URLs use
`urljoin(settings.MAOLAO_BASE_URL + "/", result_url.lstrip("/"))` and
`_headers()`. Missing URLs build the existing quoted content endpoint with
`_headers()`.

- [ ] **Step 4: Verify selection tests pass**

Run the same targeted pytest command and expect all tests to pass.

### Task 2: Bounded image retrieval and useful errors

**Files:**
- Modify: `backend/unit_tests/test_image_gateway.py`
- Modify: `backend/app/api/routes/images.py`

**Interfaces:**
- Consumes: `httpx.AsyncClient`, `ImageDownloadRequest`, retry attempt count,
  and delay.
- Produces: a successful `httpx.Response` from
  `download_generated_image(...)` and a non-empty string from
  `exception_message(exc)`.

- [ ] **Step 1: Add failing async retry tests**

Use `httpx.MockTransport` to return `404` and then `200 image/png`. Assert two
requests occur and that the second response is returned. Add a `400` test that
asserts only one request occurs and `RuntimeError` includes the upstream error.
Add:

```python
assert exception_message(httpx.ReadTimeout("")) == "ReadTimeout"
```

- [ ] **Step 2: Verify retry tests fail**

Run the targeted test module and expect missing helper failures.

- [ ] **Step 3: Implement bounded retrieval**

Implement `download_generated_image` with three attempts by default and a
five-second delay by default. Retry only statuses `404`, `409`, `425`, `429`,
and `500` through `599`; raise immediately for other non-success responses.
Implement `exception_message` as `str(exc).strip() or type(exc).__name__`.

- [ ] **Step 4: Integrate with `process_turn`**

For every delivered result item, build its request, call the downloader, and
save the response. When there are no delivered items, retain `n` compatibility
fallback downloads. Replace `error=str(exc)` with
`error=exception_message(exc)`.

- [ ] **Step 5: Verify all backend tests**

Run:

```powershell
uv run --package app pytest backend/unit_tests -q
```

Expected: all tests pass.

### Task 3: Publish and locally verify the production images

**Files:**
- Verify: `compose.prod.yml`
- Verify: `.github/workflows/publish-images.yml`

**Interfaces:**
- Consumes: committed `main`, public GHCR packages, and the existing local
  `.env` and named data volume.
- Produces: updated public images and locally running production services on
  `127.0.0.1:7820`.

- [ ] **Step 1: Run complete local checks**

Run backend tests, `npm --prefix frontend run build`,
`docker compose -f compose.prod.yml config -q`, `git diff --check`,
`git check-ignore .env`, and a tracked API-key pattern scan. All commands must
exit successfully and no real key may be tracked.

- [ ] **Step 2: Commit and push**

Commit the implementation, tests, and plan with:

```powershell
git add .
git commit -m "fix: download images from task result URLs"
git push origin main
```

- [ ] **Step 3: Confirm GitHub Actions**

Query the latest `publish-images.yml` run through the GitHub API until the run
for the implementation commit completes successfully. Confirm anonymous GHCR
manifest requests for both `latest` images return HTTP 200.

- [ ] **Step 4: Pull and deploy locally**

Run:

```powershell
docker compose -f compose.prod.yml pull
docker compose -f compose.prod.yml up -d
```

Do not pass `-v` and do not run `down`, so the named volume remains intact.
Verify `docker compose -f compose.prod.yml ps`, request
`http://127.0.0.1:7820/`, and request
`http://127.0.0.1:7820/api/v1/conversations`. Both HTTP requests must succeed.
