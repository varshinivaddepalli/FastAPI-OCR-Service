from typing import Optional
from pydantic import BaseModel
from pydantic import Field
from pydantic import computed_field
from pydantic_settings import BaseSettings
import os

class Settings(BaseSettings):
    azure_storage_connection_string: Optional[str] = Field(default=None, alias="AZURE_STORAGE_CONNECTION_STRING")
    azure_storage_container: Optional[str] = Field(default=None, alias="AZURE_STORAGE_CONTAINER")
    database_url: Optional[str] = Field(default=None, alias="DATABASE_URL")
    groq_api_key: str = Field(alias="GROQ_API_KEY")
    openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")
    hf_token: Optional[str] = Field(default=None, alias="HF_TOKEN")
    app_env: str = Field(default="dev", alias="APP_ENV")

    @computed_field
    @property
    def effective_database_url(self) -> str:
        # Default to local SQLite so the app can run without MySQL
        return self.database_url or "sqlite:///./app_local.db"

    @computed_field
    @property
    def has_azure_storage(self) -> bool:
        return bool(self.azure_storage_connection_string and self.azure_storage_container)

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()  # type: ignore
