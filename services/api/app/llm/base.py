"""The generation boundary.

One protocol, two implementations, and a deliberate split between *the cheap
call* and *the expensive call* — which is the single largest lever on what this
system costs to run.

`complete()` is a small, non-streamed call used for query analysis: rewrite the
question, pull out filters, classify the intent. It runs on a cheap model.
`stream()` is the answer, on the good one. Routing both through one provider
means a deployment configures a provider once and the tiering is a property of
the call site rather than of the wiring.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    # Billed at a fraction of the input rate. Counted separately rather than
    # folded into the input total, or the cost dashboard cannot show whether
    # caching is working.
    cached_input_tokens: int = 0

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
    """One piece of a streamed answer.

    `delta` carries text; the final event carries `usage` and no text, because
    token counts only exist once the provider has finished.
    """

    delta: str = ""
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
        self, messages: list[Message], *, model: str | None = None, max_tokens: int = 1024
    ) -> AsyncIterator[StreamEvent]: ...
