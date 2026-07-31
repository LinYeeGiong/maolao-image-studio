from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="../.env",
        env_ignore_empty=True,
        extra="ignore",
    )

    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "Maolao Image Studio"
    MAOLAO_BASE_URL: str = "https://maolaoapi.com"
    MAOLAO_API_KEY: str = ""
    MAOLAO_IMAGE_MODEL: str = "gpt-image-2-4k"
    RELAYROUTER_BASE_URL: str = "https://relayrouter.io/v1"
    RELAYROUTER_API_KEY: str = ""
    RELAYROUTER_IMAGE_MODEL: str = "gpt-image-2"
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_API_KEY: str = ""
    OPENAI_IMAGE_MODEL: str = "gpt-image-2"
    REDIS_URL: str = "redis://redis:6379/0"
    ARQ_QUEUE_NAME: str = "maolao-image-jobs"
    ARQ_JOB_TIMEOUT_SECONDS: int = 1800
    ARQ_MAX_JOBS: int = 2
    QUEUE_RECONCILE_INTERVAL_SECONDS: float = 30.0
    PROVIDER_REQUEST_TIMEOUT_SECONDS: float = 300.0
    # Generation legitimately takes minutes, but opening the socket should
    # not. Kept well above a healthy handshake because this host's uplink is
    # congested enough that even connecting is slow under load -- 10s here
    # made a reachable provider look unreachable.
    PROVIDER_CONNECT_TIMEOUT_SECONDS: float = 45.0
    DATA_DIR: str = "/data"
    TASK_POLL_INTERVAL_SECONDS: float = 2.5
    COS_ENABLED: bool = False
    COS_SECRET_ID: str = ""
    COS_SECRET_KEY: str = ""
    COS_BUCKET: str = "huajing-1437302460"
    COS_REGION: str = "ap-guangzhou"
    COS_ENDPOINT: str = "https://huajing-1437302460.cos.ap-guangzhou.myqcloud.com"
    # Domain suffix the SDK talks to, without scheme or bucket, e.g.
    # "cos-internal.ap-guangzhou.myqcloud.com" to keep same-region traffic on
    # the VPC network instead of the bandwidth-capped public egress. Empty
    # lets the SDK derive the public endpoint from COS_REGION.
    COS_API_ENDPOINT: str = ""
    # A generated 4K PNG is ~15MB and this host's public egress is heavily
    # capped, so the SDK's 60s default aborts the upload just as it is about
    # to finish and the object never lands.
    COS_TIMEOUT_SECONDS: int = 600
    # Above this, upload in parts so a stalled chunk retries on its own
    # instead of restarting the whole transfer.
    COS_MULTIPART_THRESHOLD_BYTES: int = 4 * 1024 * 1024
    COS_SIGNED_URL_TTL: int = 3600
    COS_OBJECT_PREFIX: str = "maolao"
    COS_RETRY_INTERVAL_SECONDS: float = 60.0


settings = Settings()
