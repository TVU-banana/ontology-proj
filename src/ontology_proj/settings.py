from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment configuration. No implicit .env or experiment configuration."""

    model_config = SettingsConfigDict(
        env_file=None, extra="forbid", hide_input_in_errors=True,
    )
    app_host: Literal["127.0.0.1", "localhost", "::1"] = "127.0.0.1"
    app_port: int = Field(default=8000, ge=1, le=65535)
    actor_id: str = 'local_operator'
    max_upload_bytes: int = Field(default=50*1024*1024, ge=1)
    max_pages: int = Field(default=100, ge=1)
    data_root: Path = Path("./data")
    docling_artifacts_path: Path | None = None
    docling_num_threads: int = Field(default=4, ge=1, le=64)
    docling_ocr_mode: Literal["auto", "off"] = "auto"
    docling_ocr_models_path: Path | None = None
    docling_ocr_force_pages: list[int] = Field(default_factory=list)
    neo4j_uri: str = ""
    neo4j_database: str = "neo4j"
    neo4j_user: str = ""
    neo4j_password: SecretStr = SecretStr("")
    llm_provider: str = "openai_compatible"
    llm_base_url: str = Field(default="", validation_alias=AliasChoices('LLM_BASE_URL', 'EXTRACTION_BASE_URL', 'llm_base_url'))
    llm_model: str = Field(default="", validation_alias=AliasChoices('LLM_MODEL', 'EXTRACTION_MODEL', 'llm_model'))
    llm_api_key: SecretStr = Field(default=SecretStr(""), validation_alias=AliasChoices('LLM_API_KEY', 'EXTRACTION_API_KEY', 'llm_api_key'))
    llm_fallback_models: list[str] = Field(default_factory=lambda: ["glm-5.3-flash"], max_length=5)
    embedding_provider: str = ""
    embedding_model: str = ""
    embedding_base_url: str = ""
    embedding_model_digest: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")
    embedding_num_ctx: int = Field(default=4096, ge=256, le=32768)
    embedding_dimension: int | None = Field(default=None, ge=1)
    quality_mode: Literal["demo_accept_all", "demo_one_in_ten"] = "demo_accept_all"
    identity_registry_path: Path | None = None
    identity_registry_sha256: str = Field(default='', pattern=r'^$|^[0-9a-f]{64}$')

    @field_validator("llm_fallback_models")
    @classmethod
    def valid_fallback_models(cls, value):
        if any(not model.strip() or model != model.strip() for model in value):
            raise ValueError("Fallback model IDs must be nonempty and have no surrounding whitespace")
        return list(dict.fromkeys(value))

    @field_validator("data_root")
    @classmethod
    def absolute_data_root(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator("docling_artifacts_path", "identity_registry_path", "docling_ocr_models_path", mode="before")
    @classmethod
    def explicit_artifacts_path(cls, value: object) -> Path | None:
        return None if value is None or value == "" else Path(value).expanduser().resolve()

    @field_validator("embedding_dimension", mode="before")
    @classmethod
    def empty_dimension(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("docling_ocr_force_pages", mode="before")
    @classmethod
    def valid_ocr_pages(cls, value):
        if not isinstance(value, list) or any(type(p) is not int or p < 1 for p in value) or len(set(value)) != len(value):
            raise ValueError("OCR force pages must be unique positive physical page numbers")
        return sorted(value)

    @model_validator(mode='after')
    def compatible_ocr_mode(self):
        if self.docling_ocr_mode == 'off' and self.docling_ocr_force_pages:
            raise ValueError('Forced OCR pages require auto mode')
        return self
