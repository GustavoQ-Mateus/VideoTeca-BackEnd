from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://videoteca:password@localhost:5432/videoteca"
    cors_origins: str = "http://localhost:3000"
    jwt_secret: str = "videoteca-unifor-secret-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440  # 24h


settings = Settings()
