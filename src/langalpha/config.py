from __future__ import annotations

from functools import lru_cache
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
    openai_web_search_context_size: Literal["low", "medium", "high"] = Field(
        default="medium",
        alias="OPENAI_WEB_SEARCH_CONTEXT_SIZE",
    )
    openai_web_search_max_calls: int = Field(
        default=12,
        alias="OPENAI_WEB_SEARCH_MAX_CALLS",
        ge=1,
        le=100,
    )

    daytona_api_key: SecretStr | None = Field(default=None, alias="DAYTONA_API_KEY")
    daytona_target: str = Field(default="us", alias="DAYTONA_TARGET")
    daytona_auto_stop_minutes: int = Field(default=60, alias="DAYTONA_AUTO_STOP_MINUTES")
    daytona_auto_archive_minutes: int = Field(default=10_080, alias="DAYTONA_AUTO_ARCHIVE_MINUTES")
    daytona_auto_delete_minutes: int = Field(
        default=43_200,
        alias="DAYTONA_AUTO_DELETE_MINUTES",
    )

    langsmith_api_key: SecretStr | None = Field(default=None, alias="LANGSMITH_API_KEY")
    langsmith_project: str = Field(default="langalpha-local", alias="LANGSMITH_PROJECT")
    langsmith_tracing: bool = Field(default=True, alias="LANGSMITH_TRACING")

    sec_user_agent: str | None = Field(default=None, alias="SEC_USER_AGENT")
    fred_api_key: SecretStr | None = Field(default=None, alias="FRED_API_KEY")
    massive_api_key: SecretStr | None = Field(default=None, alias="MASSIVE_API_KEY")
    massive_snapshots_enabled: bool = Field(
        default=False,
        alias="MASSIVE_SNAPSHOTS_ENABLED",
    )

    langgraph_server_url: str = Field(default="http://127.0.0.1:2024", alias="LANGGRAPH_SERVER_URL")
    langgraph_api_key: SecretStr | None = Field(default=None, alias="LANGGRAPH_API_KEY")
    langgraph_assistant_id: str = Field(default="main", alias="LANGGRAPH_ASSISTANT_ID")

    supabase_url: str | None = Field(default=None, alias="SUPABASE_URL")
    supabase_publishable_key: str | None = Field(
        default=None,
        alias="SUPABASE_PUBLISHABLE_KEY",
    )
    supabase_secret_key: SecretStr | None = Field(
        default=None,
        alias="SUPABASE_SECRET_KEY",
    )
    supabase_storage_bucket: str = Field(
        default="langalpha-assets",
        alias="SUPABASE_STORAGE_BUCKET",
    )

    app_project_id: str = Field(default="langalpha", alias="APP_PROJECT_ID")
    app_version: str = Field(default="development", alias="APP_VERSION")
    app_environment: str = Field(default="development", alias="APP_ENVIRONMENT")

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

    max_model_calls: int = 20
    max_tool_calls: int = 80
    max_researcher_model_calls: int = Field(
        default=16,
        alias="MAX_RESEARCHER_MODEL_CALLS",
        ge=2,
        le=40,
    )
    max_researcher_tool_calls: int = Field(
        default=40,
        alias="MAX_RESEARCHER_TOOL_CALLS",
        ge=4,
        le=120,
    )
    max_run_seconds: int = 600
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

    def require_sec_user_agent(self) -> str:
        if self.sec_user_agent is None:
            raise RuntimeError(
                "SEC_USER_AGENT is required, for example 'Your Company research@example.com'"
            )
        return self.sec_user_agent

    def require_fred_key(self) -> str:
        if self.fred_api_key is None:
            raise RuntimeError("FRED_API_KEY is required for macroeconomic research")
        return self.fred_api_key.get_secret_value()

    def require_massive_key(self) -> str:
        if self.massive_api_key is None:
            raise RuntimeError("MASSIVE_API_KEY is required for market data research")
        return self.massive_api_key.get_secret_value()

    def require_supabase_url(self) -> str:
        if self.supabase_url is None:
            raise RuntimeError("SUPABASE_URL is required")
        return self.supabase_url.rstrip("/")

    def require_supabase_publishable_key(self) -> str:
        if self.supabase_publishable_key is None:
            raise RuntimeError("SUPABASE_PUBLISHABLE_KEY is required")
        return self.supabase_publishable_key

    def require_supabase_secret_key(self) -> str:
        if self.supabase_secret_key is None:
            raise RuntimeError("SUPABASE_SECRET_KEY is required")
        return self.supabase_secret_key.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    return Settings()
