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
        self._temperature = settings.llm_temperature
        self._reasoning_effort = settings.llm_reasoning_effort
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
        }

        # Sent only when a deployment asks for it.
        #
        # Retrieval-grounded answers should not be inventive, and a low
        # temperature is the obvious way to say so. It is also the fastest way
        # to make every request fail: several current models accept only their
        # default and reject the whole call with a 400 rather than ignoring the
        # parameter. This was found by running against a real key, having
        # passed every test against a stand-in that does not care.
        #
        # So the default is to send nothing and let the model use its own, and
        # a deployment on a model that supports it sets `LLM_TEMPERATURE`.
        if self._temperature is not None:
            payload["temperature"] = self._temperature

        # See the setting. On a reasoning model this has to be `none` for the
        # agent loop to work at all, and on a model that has never heard of the
        # parameter it has to be absent.
        if self._reasoning_effort is not None:
            payload["reasoning_effort"] = self._reasoning_effort
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
            _raise_for_status(response, await _body(response))
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
            _raise_for_status(response, await _body(response))

            # A tool call does not arrive whole. The name comes in one chunk,
            # the arguments as a run of JSON fragments across several more, and
            # a parallel call interleaves with its siblings — which is what the
            # `index` on each fragment is for. They are accumulated here and
            # emitted once, complete, so the agent loop never sees half a call.
            pending: dict[int, dict[str, str]] = {}
            usage = Usage()

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
                    usage = _usage(chunk["usage"])
                    continue

                choices = chunk.get("choices") or []
                if not choices:
                    continue

                delta = choices[0].get("delta") or {}
                if delta.get("content"):
                    yield StreamEvent(delta=delta["content"])

                for fragment in delta.get("tool_calls") or []:
                    _accumulate(pending, fragment)

            calls = _assemble(pending)
            if calls:
                yield StreamEvent(tool_calls=calls)
            # Usage last, and always: the loop adds it up across steps, and a
            # step that spent tokens on a tool call it then executed has still
            # spent them.
            yield StreamEvent(usage=usage)


async def _body(response: httpx.Response) -> str:
    """The provider's own error message, for a response that failed.

    Nothing is read on success: the streaming path must not consume its own
    body before iterating it.
    """
    if response.is_success:
        return ""
    try:
        payload = json.loads(await response.aread())
    except (json.JSONDecodeError, httpx.HTTPError):
        return ""
    error = payload.get("error") if isinstance(payload, dict) else None
    return str(error.get("message", "")) if isinstance(error, dict) else ""


def _raise_for_status(response: httpx.Response, detail: str) -> None:
    """`raise_for_status`, plus what the provider actually said.

    The bare version reports "400 Bad Request for url …" and discards the
    sentence explaining which parameter was wrong — which is the difference
    between a one-line fix and an afternoon.
    """
    if response.is_success:
        return
    if detail:
        logger.error("%s from the model provider: %s", response.status_code, detail)
    response.raise_for_status()


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


def _accumulate(pending: dict[int, dict[str, str]], fragment: Any) -> None:
    """Fold one streamed fragment into the call it belongs to.

    Keyed on `index`, not on arrival order: a model asking for two tools at
    once interleaves their fragments, and appending to whichever call came last
    produces one call with both sets of arguments concatenated into invalid
    JSON.

    Every field is optional in every fragment. The first carries the id and the
    name, the rest carry argument text, and a defensive read here is the
    difference between a malformed chunk costing one tool call and costing the
    answer.
    """
    if not isinstance(fragment, dict):
        return
    index = fragment.get("index", 0)
    if not isinstance(index, int):
        return

    slot = pending.setdefault(index, {"id": "", "name": "", "arguments": ""})
    if fragment.get("id"):
        slot["id"] = str(fragment["id"])

    function = fragment.get("function")
    if not isinstance(function, dict):
        return
    if function.get("name"):
        slot["name"] = str(function["name"])
    if function.get("arguments"):
        slot["arguments"] += str(function["arguments"])


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
