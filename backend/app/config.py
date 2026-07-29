from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings


ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    app_name: str = "ReadSync"
    database_url: str = Field(default="sqlite:///./readsync.db", alias="DATABASE_URL")
    llm_provider: str = Field(default="ollama", alias="LLM_PROVIDER")
    ollama_endpoint: str = Field(default="http://127.0.0.1:11434", alias="OLLAMA_ENDPOINT")
    ollama_model: str = Field(default="llama3.2", alias="OLLAMA_MODEL")
    openrouter_base_url: str = Field(default="https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL")
    openrouter_model: str = Field(default="meta-llama/llama-3.1-8b-instruct:free", alias="OPENROUTER_MODEL")
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_referer: str = Field(default="", alias="OPENROUTER_HTTP_REFERER")
    openrouter_title: str = Field(default="ReadSync", alias="OPENROUTER_X_TITLE")
    embedding_model: str = Field(default="sentence-transformers/all-MiniLM-L6-v2", alias="EMBEDDING_MODEL")
    faiss_index_path: str = Field(default="./readsync.faiss", alias="FAISS_INDEX_PATH")
    allow_repo_roots: str = Field(default="", alias="READSYNC_ALLOWED_REPO_ROOTS")
    managed_repo_dir: str = Field(default="./managed_repos", alias="READSYNC_MANAGED_REPO_DIR")
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000",
        alias="CORS_ORIGINS",
    )

    class Config:
        env_file = str(ROOT_ENV)
        extra = "ignore"

    @property
    def sqlite_path(self) -> Path:
        if self.database_url.startswith("sqlite:///"):
            return Path(self.database_url.replace("sqlite:///", "", 1)).resolve()
        return Path("readsync.db").resolve()

    @property
    def managed_repo_path(self) -> Path:
        return Path(self.managed_repo_dir).expanduser().resolve()

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
