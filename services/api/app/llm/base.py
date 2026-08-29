"""The generation boundary.

One protocol, two implementations, and a deliberate split between *the cheap
call* and *the expensive call* — which is the single largest lever on what this
system costs to run.

`complete()` is a small, non-streamed call used for query analysis: rewrite the
question, pull out filters, classify the intent. It runs on a cheap model.
`stream()` is the answer, on the good one. Routing both through one provider
means a deployment configures a provider once and the tiering is a property of
the call site rather than of the wiring.

`stream()` also carries the tool-calling contract. Tool *calls* stream back as
fragments — a name arrives before its arguments, and the arguments arrive as
partial JSON — so providers accumulate them and emit whole `ToolCall`s. A
half-parsed argument object is not something the agent loop should ever have to
reason about.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One tool the model decided to call."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A tool as the model sees it.

    `parameters` is a JSON Schema object. The description matters more than it
    looks: it is the entire basis on which the model decides whether to call
    this or answer directly, and a vague one produces a system that searches the
    knowledge base for the time of day.
    """

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str
    # Set on an assistant message that asked for tools.
    tool_calls: tuple[ToolCall, ...] = ()
    # Set on a tool result message, tying it back to the call it answers.
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    # Billed at a fraction of the input rate. Counted separately rather than
    # folded into the input total, or the cost dashboard cannot show whether
    # caching is working.
    cached_input_tokens: int = 0

    @property
    def total(self) -> int:
        """Tokens on this call. Zero means nothing was spent and nothing needs
        recording — a provider that never answered, or one that costs nothing."""
        return self.input_tokens + self.output_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
        )


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True, slots=True)
class StreamEvent:
    """One piece of a streamed turn.

    `delta` carries text. `tool_calls` arrives whole, once the provider has
    finished streaming their fragments. The final event carries `usage` and
    nothing else, because token counts only exist once the provider is done.
    """

    delta: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage | None = None


@runtime_checkable
class LlmProvider(Protocol):
    name: str
    chat_model: str
    cheap_model: str

    async def complete(
        self, messages: list[Message], *, model: str | None = None, max_tokens: int = 512
    ) -> Completion: ...

    def stream(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        tools: list[ToolSpec] | None = None,
    ) -> AsyncIterator[StreamEvent]: ...
