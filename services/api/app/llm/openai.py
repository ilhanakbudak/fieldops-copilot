"""OpenAI.

Two things here are about cost rather than correctness, and both are invisible
until the bill arrives.

**Cheap model for analysis, good model for generation.** Query rewriting and
intent classification are short, structured and forgiving; paying the answer
model to do them is most of a naive RAG system's spend.

**The cacheable prefix comes first.** Prompt caching keys on an exact prefix
match, so the system prompt and the retrieved passages are assembled ahead of
anything that varies per request. Reordering those for readability quietly
disables the discount, which is why the assembly lives in one place and says so.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import Settings
from app.llm.base import Completion, Message, StreamEvent, ToolCall, ToolSpec, Usage

logger = logging.getLogger("fieldops.llm")

ENDPOINT = "https://api.openai.com/v1/chat/completions"


class OpenAiProvider:
    name = "openai"

    def __init__(self, settings: Settings) -> None:
        if not settings.openai_api_key:
            raise ValueError("LLM_PROVIDER=openai requires OPENAI_API_KEY")
        self._key = settings.openai_api_key
        self.chat_model = settings.chat_model
        self.cheap_model = settings.cheap_model
        self._timeout = httpx.Timeout(60.0, connect=10.0)

    def _payload(
        self,
        messages: list[Message],
        model: str,
        max_tokens: int,
        tools: list[ToolSpec] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [_wire(message) for message in messages],
            "max_completion_tokens": max_tokens,
            # Retrieval-grounded answers should not be inventive. The interesting
            # variation belongs in which passages were retrieved, not in how the
            # model chose to phrase a warranty term.
            "temperature": 0.2,
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in tools
            ]
            # "auto", not "required". The whole point of the agent loop is that
            # the model may answer directly — asked the date, it should not be
            # forced to search a corpus of water-treatment manuals for it.
            payload["tool_choice"] = "auto"
        return payload

    async def complete(
        self, messages: list[Message], *, model: str | None = None, max_tokens: int = 512
    ) -> Completion:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                ENDPOINT,
                headers={"authorization": f"Bearer {self._key}"},
                json=self._payload(messages, model or self.cheap_model, max_tokens),
            )
            response.raise_for_status()
            body = response.json()

        return Completion(
            text=body["choices"][0]["message"]["content"] or "",
            usage=_usage(body.get("usage")),
        )

    async def stream(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        tools: list[ToolSpec] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        payload = self._payload(messages, model or self.chat_model, max_tokens, tools)
        payload["stream"] = True
        # Without this the streamed response carries no token counts at all, and
        # every streamed answer costs an unknown amount.
        payload["stream_options"] = {"include_usage": True}

        async with (
            httpx.AsyncClient(timeout=self._timeout) as client,
            client.stream(
                "POST",
                ENDPOINT,
                headers={"authorization": f"Bearer {self._key}"},
                json=payload,
            ) as response,
        ):
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break

                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    logger.warning("unparseable stream chunk")
                    continue

                # The usage-bearing chunk arrives last and has no choices.
                if chunk.get("usage"):
                    yield StreamEvent(usage=_usage(chunk["usage"]))
                    continue

                choices = chunk.get("choices") or []
                if choices:
                    delta = choices[0].get("delta", {}).get("content")
                    if delta:
                        yield StreamEvent(delta=delta)


def _wire(message: Message) -> dict[str, Any]:
    """One message in the shape the API expects."""
    payload: dict[str, Any] = {"role": message.role, "content": message.content}

    if message.role == "tool":
        payload["tool_call_id"] = message.tool_call_id
        if message.name:
            payload["name"] = message.name
    elif message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in message.tool_calls
        ]
        # A tool-calling assistant message has no text, and the API rejects an
        # empty string where it expects null.
        payload["content"] = message.content or None

    return payload


def _assemble(pending: dict[int, dict[str, str]]) -> tuple[ToolCall, ...]:
    """Turn accumulated fragments into whole calls.

    Arguments that do not parse are dropped rather than guessed at. A model that
    emitted broken JSON has not asked for anything actionable, and inventing an
    argument object on its behalf is how an agent ends up calling the right tool
    with the wrong customer.
    """
    calls: list[ToolCall] = []
    for index in sorted(pending):
        slot = pending[index]
        if not slot["name"]:
            continue
        try:
            arguments = json.loads(slot["arguments"] or "{}")
        except json.JSONDecodeError:
            logger.warning("dropping tool call %s with unparseable arguments", slot["name"])
            continue
        if not isinstance(arguments, dict):
            continue
        calls.append(
            ToolCall(id=slot["id"] or f"call_{index}", name=slot["name"], arguments=arguments)
        )
    return tuple(calls)


def _usage(raw: Any) -> Usage:
    """Read counts defensively.

    A provider that changes the shape of its usage block should cost a wrong
    number in the dashboard, not a failed answer to a technician on a roof.
    """
    if not isinstance(raw, dict):
        return Usage()

    details = raw.get("prompt_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, dict) else 0

    return Usage(
        input_tokens=_int(raw.get("prompt_tokens")),
        output_tokens=_int(raw.get("completion_tokens")),
        cached_input_tokens=_int(cached),
    )


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
