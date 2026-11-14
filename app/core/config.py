from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "videoteca-unifor-secret-change-in-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"  # development | staging | production
    database_url: str = "postgresql://videoteca:password@localhost:5432/videoteca"
    cors_origins: str = "http://localhost:3000"
    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440  # 24h
    expose_docs: bool | None = None  # default: true fora de produção
    login_max_attempts: int = 10
    login_window_seconds: int = 300

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def docs_enabled(self) -> bool:
        return (not self.is_production) if self.expose_docs is None else self.expose_docs

    @model_validator(mode="after")
    def production_guards(self):
        # Fail fast at startup instead of running production with insecure defaults.
        if self.is_production:
            if self.jwt_secret == DEFAULT_JWT_SECRET or len(self.jwt_secret) < 32:
                raise ValueError("JWT_SECRET deve ser definido com pelo menos 32 caracteres em produção")
            if "*" in self.cors_origins:
                raise ValueError("CORS_ORIGINS não pode conter '*' em produção")
        return self


settings = Settings()
