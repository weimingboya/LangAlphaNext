from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.middleware.filesystem import FilesystemPermission
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
    ToolCallLimitMiddleware,
    ToolRetryMiddleware,
)
from langchain_core.tools import BaseTool
from langchain_quickjs import CodeInterpreterMiddleware

from langalpha.agent.async_subagents import CompactAsyncSubAgentMiddleware
from langalpha.agent.context import RunContext
from langalpha.agent.model import build_model
from langalpha.agent.prompts import (
    MAIN_SYSTEM_PROMPT,
    RESEARCHER_SYSTEM_PROMPT,
)
from langalpha.agent.responses import ResearchResult
from langalpha.agent.state import LangAlphaAgentState
from langalpha.agent.tools import HOST_TOOLS
from langalpha.backends import get_context_daytona_backend, get_researcher_backend
from langalpha.capabilities.finance import FINANCE_TOOLS, RESEARCH_FINANCE_TOOLS
from langalpha.capabilities.macro import MACRO_TOOLS
from langalpha.capabilities.openai_web import (
    OpenAIWebSearchBudgetMiddleware,
    build_openai_web_search_tool,
)
from langalpha.capabilities.sec import SEC_TOOLS
from langalpha.config import get_settings
from langalpha.integrations.mcp import load_mcp_tools

Profile = Literal["main", "researcher"]

register_harness_profile(
    "openai",
    HarnessProfile(
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
    ),
)

_PROMPTS = {
    "main": MAIN_SYSTEM_PROMPT,
    "researcher": RESEARCHER_SYSTEM_PROMPT,
}

_RESPONSE_FORMATS = {
    "main": None,
    "researcher": ResearchResult,
}

FILESYSTEM_PERMISSIONS = (
    FilesystemPermission(
        operations=["write"],
        paths=["/skills/**", "/memory/**", "/memos/**"],
        mode="deny",
    ),
)

RESEARCHER_PERMISSIONS = (
    FilesystemPermission(
        operations=["write"],
        paths=["/**"],
        mode="deny",
    ),
)

RESEARCHER_SKILLS = [
    "/skills/financial-research/",
    "/skills/sec-filing-analysis/",
]


class DeepAgentFactory:
    """The only place in LangAlpha that constructs Deep Agents."""

    def create(
        self,
        profile: Profile,
        *,
        tools: Sequence[BaseTool | Any] | None = None,
        include_async_subagents: bool = False,
    ):
        settings = get_settings()
        is_main = profile == "main"
        mcp_tools = list(load_mcp_tools()) if is_main else []
        web_search_tool = build_openai_web_search_tool()
        main_finance_tools = [
            tool
            for tool in FINANCE_TOOLS
            if tool.name != "market_get_snapshots" or settings.massive_snapshots_enabled
        ]
        default_tools = (
            [
                web_search_tool,
                *HOST_TOOLS,
                *main_finance_tools,
                *SEC_TOOLS,
                *MACRO_TOOLS,
                *mcp_tools,
            ]
            if is_main
            else [
                web_search_tool,
                *RESEARCH_FINANCE_TOOLS,
                *SEC_TOOLS,
                *MACRO_TOOLS,
            ]
        )
        selected_tools = list(tools if tools is not None else default_tools)
        selected_tool_names = {tool.name for tool in selected_tools if isinstance(tool, BaseTool)}
        retryable_tools = [
            *(
                tool
                for tool in [*FINANCE_TOOLS, *SEC_TOOLS, *MACRO_TOOLS]
                if tool.name in selected_tool_names
            ),
            *(
                tool
                for tool in mcp_tools
                if tool.name in selected_tool_names
                and tool.name not in {"materialize_dataset", "ask_user", "submit_plan"}
            ),
        ]
        ptc_names = [
            name
            for name in settings.mcp_ptc_allowlist
            if any(tool.name == name for tool in mcp_tools)
        ]
        ptc_config = (
            ptc_names
            if ptc_names and all(isinstance(tool, BaseTool) for tool in selected_tools)
            else None
        )
        model_call_limit = (
            settings.max_model_calls if is_main else settings.max_researcher_model_calls
        )
        tool_call_limit = settings.max_tool_calls if is_main else settings.max_researcher_tool_calls
        middleware: list[Any] = [
            ModelCallLimitMiddleware(
                run_limit=model_call_limit,
                exit_behavior="end",
            ),
            ToolCallLimitMiddleware(
                run_limit=tool_call_limit,
                exit_behavior="error",
            ),
            OpenAIWebSearchBudgetMiddleware(
                max_calls=settings.openai_web_search_max_calls,
            ),
            ModelRetryMiddleware(
                max_retries=3,
                initial_delay=1,
                max_delay=8,
                on_failure="error",
            ),
        ]
        if include_async_subagents:
            middleware.append(
                ToolCallLimitMiddleware(
                    tool_name="start_async_task",
                    run_limit=settings.max_async_subagents,
                    exit_behavior="continue",
                )
            )
            middleware.append(
                CompactAsyncSubAgentMiddleware(
                    async_subagents=[
                        {
                            "name": "researcher",
                            "description": "并行完成证据检索、数据验证和可复现分析。",
                            "graph_id": "researcher",
                        },
                    ]
                )
            )
        if retryable_tools:
            middleware.append(
                ToolRetryMiddleware(
                    max_retries=2,
                    tools=retryable_tools,
                    initial_delay=0.5,
                    max_delay=4,
                    on_failure="continue",
                )
            )
        middleware.append(
            CodeInterpreterMiddleware(
                memory_limit=64 * 1024 * 1024,
                timeout=5,
                max_ptc_calls=128,
                max_result_chars=8_000,
                subagents=False,
                ptc=ptc_config,
                mode="thread",
            )
        )
        return create_deep_agent(
            name=f"langalpha-{profile}",
            model=build_model(),
            tools=selected_tools,
            system_prompt=_PROMPTS[profile],
            middleware=middleware,
            skills=["/skills/"] if is_main else RESEARCHER_SKILLS,
            memory=["/memory/AGENTS.md"] if is_main else None,
            permissions=(list(FILESYSTEM_PERMISSIONS) if is_main else list(RESEARCHER_PERMISSIONS)),
            backend=(get_context_daytona_backend() if is_main else get_researcher_backend()),
            response_format=_RESPONSE_FORMATS[profile],
            state_schema=LangAlphaAgentState,
            context_schema=RunContext,
            checkpointer=True,
        )
