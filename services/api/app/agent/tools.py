"""The tool boundary.

An assistant that answers every question by searching a corpus of manuals is a
search box with extra latency. Asked the date it should look at a clock; asked
who a customer is it should query the CRM; asked what `E-04` means it should
search the manuals. Deciding which is the model's job, and this is the interface
it decides across.

Two properties are load-bearing.

**Tools are role-gated, like documents.** `Tool.permission` names what a caller
must hold to be *offered* it, and the registry filters the toolset per request.
A technician's model is never told a pricing lookup exists — which is stronger
than refusing the call, because a tool the model cannot see is one it cannot be
talked into using.

**A failing tool returns a result, not an exception.** A CRM timeout should
produce "the CRM did not answer" in the transcript, which the model can relay,
rather than a 500 that loses the conversation. The one thing it must not do is
look like an empty answer — "no customer found" and "the lookup failed" lead to
completely different next actions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.auth.rbac import Permission, Principal
from app.llm.base import ToolSpec


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Everything a tool is allowed to know about the caller.

    Deliberately not the request: a tool that could reach the HTTP layer could
    reach the session cookie, and there is no reason for a CRM lookup to be able
    to do that.
    """

    principal: Principal
    session: Any


@dataclass(frozen=True, slots=True)
class ToolResult:
    """What the model is shown after a tool runs."""

    # Rendered into the transcript for the model to read.
    content: str
    # Structured payload for the interface — customer cards, source panels.
    # Never sent to the model; it reads `content`.
    data: dict[str, Any] = field(default_factory=dict)
    # A short line for the "what happened" trail in the UI.
    summary: str = ""
    ok: bool = True


@runtime_checkable
class Tool(Protocol):
    name: str
    description: str
    # A JSON Schema object. The *description* matters more than the schema: it
    # is the entire basis on which the model decides whether to call this or
    # answer directly, and a vague one produces a system that searches a corpus
    # of water-treatment manuals for the time of day.
    parameters: dict[str, Any]
    permission: Permission | None

    async def run(self, context: ToolContext, **kwargs: Any) -> ToolResult: ...


def spec(tool: Tool) -> ToolSpec:
    return ToolSpec(name=tool.name, description=tool.description, parameters=tool.parameters)


def failed(message: str) -> ToolResult:
    """A failure the model can talk about.

    Phrased as a fact rather than an apology, because the model will paraphrase
    whatever it is given and "I'm sorry, an error occurred" becomes a worse
    sentence every time it is passed through.
    """
    return ToolResult(content=message, summary=message, ok=False)
