from __future__ import annotations

from langchain_openai import ChatOpenAI

from langalpha.config import get_settings


def build_model() -> ChatOpenAI:
    settings = get_settings()
    return ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.require_openai_key(),
        reasoning={
            "effort": settings.openai_reasoning_effort,
            "summary": "auto",
        },
        output_version="responses/v1",
        use_responses_api=True,
        stream_usage=True,
        timeout=120,
        # Retry ownership belongs to ModelRetryMiddleware in the factory.
        max_retries=0,
    )
