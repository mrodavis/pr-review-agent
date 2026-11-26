"""Model access: one small interface. BedrockLLM calls Amazon Bedrock through
LangChain's ChatBedrockConverse, forcing each answer through tool use into a
Pydantic schema, with an optional Bedrock Guardrail on every call. The offline
stand-in used by tests and CI lives in scripted.py.
"""

from __future__ import annotations

import os
import time
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """Base class for model call failures the graph knows how to degrade from."""


class StructuredOutputError(LLMError):
    """The model answered, but not in the required shape."""


class LLMUnavailableError(LLMError):
    """Bedrock refused or failed the call after botocore's own retries (throttling, access, outage)."""


class StructuredLLM(Protocol):
    name: str

    def structured(self, schema: type[T], system: str, user: str) -> tuple[T, dict[str, Any]]: ...


# ---- Bedrock ----------------------------------------------------------------------


class BedrockLLM:
    """Bedrock Converse via langchain-aws. Credentials come from the normal AWS chain
    (env vars, profile, or the task/instance role); nothing is read from this repo."""

    def __init__(
        self,
        model_id: str,
        region: str,
        guardrail_id: str | None = None,
        guardrail_version: str | None = None,
        client: Any = None,
        max_tokens: int = 2000,
        temperature: float | None = None,
    ):
        from botocore.config import Config
        from langchain_aws import ChatBedrockConverse

        kwargs: dict[str, Any] = {
            "model": model_id,
            "region_name": region,
            "max_tokens": max_tokens,
        }
        if temperature is not None:  # some newer Claude models on Bedrock reject temperature
            kwargs["temperature"] = temperature
        if client is not None:
            kwargs["client"] = client
        else:
            kwargs["config"] = Config(retries={"max_attempts": 4, "mode": "adaptive"}, read_timeout=60)
        if guardrail_id:
            kwargs["guardrail_config"] = {
                "guardrailIdentifier": guardrail_id,
                "guardrailVersion": guardrail_version or "DRAFT",
                "trace": "enabled",
            }
        self.chat = ChatBedrockConverse(**kwargs)
        self.name = f"bedrock:{model_id}"

    @classmethod
    def from_env(cls) -> "BedrockLLM":
        model_id = os.environ.get("BEDROCK_MODEL_ID")
        if not model_id:
            raise RuntimeError("Set BEDROCK_MODEL_ID (see .env.example) or run with LLM_MODE=offline")
        return cls(
            model_id=model_id,
            region=os.environ.get("AWS_REGION", "us-east-1"),
            guardrail_id=os.environ.get("BEDROCK_GUARDRAIL_ID") or None,
            guardrail_version=os.environ.get("BEDROCK_GUARDRAIL_VERSION") or None,
        )

    def structured(self, schema: type[T], system: str, user: str) -> tuple[T, dict[str, Any]]:
        from botocore.exceptions import BotoCoreError, ClientError
        from langchain_core.messages import HumanMessage, SystemMessage

        runnable = self.chat.with_structured_output(schema, include_raw=True)
        started = time.perf_counter()
        try:
            out = runnable.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        except (ClientError, BotoCoreError) as e:
            raise LLMUnavailableError(f"{type(e).__name__}: {e}") from e
        meta: dict[str, Any] = {"latency_ms": round((time.perf_counter() - started) * 1000)}
        raw = out.get("raw")
        usage = getattr(raw, "usage_metadata", None) or {}
        meta["input_tokens"] = usage.get("input_tokens")
        meta["output_tokens"] = usage.get("output_tokens")
        stop = (getattr(raw, "response_metadata", None) or {}).get("stopReason")
        if stop == "guardrail_intervened":
            raise StructuredOutputError("Bedrock Guardrail intervened")
        if out.get("parsing_error") is not None or out.get("parsed") is None:
            raise StructuredOutputError(str(out.get("parsing_error") or "no structured output returned"))
        return out["parsed"], meta
