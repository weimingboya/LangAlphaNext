from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    openai_api_key: SecretStr | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-5.6-luna", alias="OPENAI_MODEL")
    openai_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] = Field(
        default="medium", alias="OPENAI_REASONING_EFFORT"
    )
    openai_input_cost_per_million: float | None = Field(
        default=None,
        alias="OPENAI_INPUT_COST_PER_MILLION",
    )
    openai_output_cost_per_million: float | None = Field(
        default=None,
        alias="OPENAI_OUTPUT_COST_PER_MILLION",
    )

    daytona_api_key: SecretStr | None = Field(default=None, alias="DAYTONA_API_KEY")
    daytona_target: str = Field(default="us", alias="DAYTONA_TARGET")
    daytona_auto_stop_minutes: int = Field(default=60, alias="DAYTONA_AUTO_STOP_MINUTES")
    daytona_auto_archive_minutes: int = Field(default=10_080, alias="DAYTONA_AUTO_ARCHIVE_MINUTES")
    daytona_auto_delete_minutes: int = Field(default=-1, alias="DAYTONA_AUTO_DELETE_MINUTES")

    langsmith_api_key: SecretStr | None = Field(default=None, alias="LANGSMITH_API_KEY")
    langsmith_project: str = Field(default="langalpha-local", alias="LANGSMITH_PROJECT")
    langsmith_tracing: bool = Field(default=True, alias="LANGSMITH_TRACING")

    langgraph_server_url: str = Field(default="http://127.0.0.1:2024", alias="LANGGRAPH_SERVER_URL")
    langgraph_assistant_id: str = Field(default="main", alias="LANGGRAPH_ASSISTANT_ID")
    langalpha_api_url: str = Field(default="http://127.0.0.1:8000", alias="LANGALPHA_API_URL")
    langalpha_internal_token: SecretStr | None = Field(
        default=None,
        alias="LANGALPHA_INTERNAL_TOKEN",
    )
    langalpha_database_path: Path = Field(
        default=Path(".data/langalpha.db"), alias="LANGALPHA_DATABASE_PATH"
    )
    langalpha_project_id: str = Field(default="langalpha-local", alias="LANGALPHA_PROJECT_ID")
    langalpha_owner_id: str = Field(default="local-user", alias="LANGALPHA_OWNER_ID")

    mcp_connections: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        alias="MCP_CONNECTIONS",
    )
    mcp_tool_name_prefix: bool = Field(default=True, alias="MCP_TOOL_NAME_PREFIX")
    mcp_ptc_allowlist: list[str] = Field(
        default_factory=list,
        alias="MCP_PTC_ALLOWLIST",
    )
    mcp_tool_allowlist: list[str] = Field(
        default_factory=list,
        alias="MCP_TOOL_ALLOWLIST",
    )
    mcp_max_calls_per_run: int = Field(
        default=100,
        alias="MCP_MAX_CALLS_PER_RUN",
    )

    max_model_calls: int = 40
    max_tool_calls: int = 150
    max_run_seconds: int = 1_200
    max_async_subagents: int = 3
    max_upload_bytes: int = 25 * 1024 * 1024

    def require_openai_key(self) -> str:
        if self.openai_api_key is None:
            raise RuntimeError("OPENAI_API_KEY is required to invoke the agent")
        return self.openai_api_key.get_secret_value()

    def require_daytona_key(self) -> str:
        if self.daytona_api_key is None:
            raise RuntimeError("DAYTONA_API_KEY is required for sandbox operations")
        return self.daytona_api_key.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    return Settings()
