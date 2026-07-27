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
    DATA_DIR: str = "/data"
    TASK_POLL_INTERVAL_SECONDS: float = 2.5
    COS_ENABLED: bool = False
    COS_SECRET_ID: str = ""
    COS_SECRET_KEY: str = ""
    COS_BUCKET: str = "huajing-1437302460"
    COS_REGION: str = "ap-guangzhou"
    COS_ENDPOINT: str = "https://huajing-1437302460.cos.ap-guangzhou.myqcloud.com"
    COS_SIGNED_URL_TTL: int = 3600
    COS_OBJECT_PREFIX: str = "maolao"
    COS_RETRY_INTERVAL_SECONDS: float = 60.0


settings = Settings()
