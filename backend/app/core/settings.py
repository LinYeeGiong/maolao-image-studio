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


settings = Settings()
