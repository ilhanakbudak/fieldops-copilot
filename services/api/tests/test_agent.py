"""The agent loop.

What is asserted here is *routing and control flow*, not answer quality: that
the model is offered only the tools its role may use, that a failing tool does
not lose the conversation, that the step ceiling holds, and that a tool it was
never offered is refused.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.agent.loop import run_agent
from app.agent.registry import tools_for
from app.agent.tools import ToolContext, ToolResult
from app.auth.rbac import Permission, Principal, Role
from app.config import get_settings
from app.core.ids import new_id
from app.llm.base import Completion, Message, StreamEvent, ToolCall, ToolSpec


def _principal(role: Role) -> Principal:
    return Principal(
        user_id=new_id(),
        email=f"{role.value}@example.com",
        full_name=role.value.title(),
        role=role,
        session_id=new_id(),
    )


class ScriptedProvider:
    """A provider that plays a fixed sequence of turns.

    Deterministic on purpose: the loop's behaviour should be assertable without
    depending on what a model decides on a given afternoon.
    """

    name = "scripted"
    chat_model = "scripted"
    cheap_model = "scripted"

    def __init__(self, turns: list[list[StreamEvent]]) -> None:
        self._turns = turns
        self.offered: list[list[ToolSpec] | None] = []

    async def complete(
        self, messages: list[Message], *, model: str | None = None, max_tokens: int = 512
    ) -> Completion:
        return Completion(text="{}")

    async def stream(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        tools: list[ToolSpec] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.offered.append(tools)
        events = self._turns.pop(0) if self._turns else [StreamEvent(delta="done")]
        for event in events:
            yield event


class RecordingTool:
    name = "record"
    description = "Records that it was called."
    parameters = {  # noqa: RUF012 - read-only schema
        "type": "object",
        "properties": {"value": {"type": "string"}},
    }
    permission: Permission | None = None

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        self.calls.append(kwargs)
        return ToolResult(content="recorded", summary="Recorded")


class StrictTool(RecordingTool):
    """Declares its arguments explicitly, so a wrong one raises TypeError —
    which is exactly what the loop has to survive."""

    name = "strict"
    description = "Takes one named argument."

    async def run(self, context: ToolContext, *, value: str = "") -> ToolResult:  # type: ignore[override]
        self.calls.append({"value": value})
        return ToolResult(content="ok", summary="ok")


class ExplodingTool(RecordingTool):
    name = "explode"
    description = "Always fails."

    async def run(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        raise RuntimeError("the CRM is on fire")


async def _drain(provider: Any, tools: list[Any], principal: Principal) -> list[Any]:
    context = ToolContext(principal=principal, session=None)
    return [
        event
        async for event in run_agent(
            "a question", tools, provider, context, settings=get_settings()
        )
    ]


async def test_a_turn_with_no_tool_calls_just_answers() -> None:
    """The branch that makes this an agent rather than a pipeline: deciding not
    to use a tool is a valid outcome."""
    provider = ScriptedProvider([[StreamEvent(delta="It is Friday.")]])
    tool = RecordingTool()

    events = await _drain(provider, [tool], _principal(Role.OFFICE))

    assert "".join(event.delta for event in events) == "It is Friday."
    assert tool.calls == []


async def test_a_tool_call_is_executed_and_fed_back() -> None:
    provider = ScriptedProvider(
        [
            [StreamEvent(tool_calls=(ToolCall(id="1", name="record", arguments={"value": "x"}),))],
            [StreamEvent(delta="Recorded it.")],
        ]
    )
    tool = RecordingTool()

    events = await _drain(provider, [tool], _principal(Role.OFFICE))

    assert tool.calls == [{"value": "x"}]
    assert [event.tool_finished.name for event in events if event.tool_finished] == ["record"]
    assert "".join(event.delta for event in events) == "Recorded it."


async def test_several_tools_in_one_step_run_concurrently() -> None:
    """A model asking for two lookups should wait for the slower, not the sum."""
    provider = ScriptedProvider(
        [
            [
                StreamEvent(
                    tool_calls=(
                        ToolCall(id="1", name="record", arguments={"value": "a"}),
                        ToolCall(id="2", name="record", arguments={"value": "b"}),
                    )
                )
            ],
            [StreamEvent(delta="Both done.")],
        ]
    )
    tool = RecordingTool()

    events = await _drain(provider, [tool], _principal(Role.OFFICE))

    assert tool.calls == [{"value": "a"}, {"value": "b"}]
    assert sum(1 for event in events if event.tool_finished) == 2


async def test_a_failing_tool_becomes_a_result_the_model_can_relay() -> None:
    """A CRM timeout should produce a sentence, not a 500 that loses the
    conversation."""
    provider = ScriptedProvider(
        [
            [StreamEvent(tool_calls=(ToolCall(id="1", name="explode", arguments={}),))],
            [StreamEvent(delta="The CRM did not answer.")],
        ]
    )

    events = await _drain(provider, [ExplodingTool()], _principal(Role.OFFICE))

    finished = next(event.tool_finished for event in events if event.tool_finished)
    assert finished.result.ok is False
    assert "".join(event.delta for event in events) == "The CRM did not answer."


async def test_a_tool_that_was_never_offered_is_refused() -> None:
    """Either the model is confused or it is being steered. Either way it is
    told plainly and the loop continues."""
    provider = ScriptedProvider(
        [
            [StreamEvent(tool_calls=(ToolCall(id="1", name="delete_everything", arguments={}),))],
            [StreamEvent(delta="I cannot do that.")],
        ]
    )

    events = await _drain(provider, [RecordingTool()], _principal(Role.OFFICE))

    finished = next(event.tool_finished for event in events if event.tool_finished)
    assert finished.result.ok is False
    assert "no tool called" in finished.result.content


async def test_bad_arguments_are_recoverable() -> None:
    provider = ScriptedProvider(
        [
            [StreamEvent(tool_calls=(ToolCall(id="1", name="strict", arguments={"nope": 1}),))],
            [StreamEvent(delta="Sorry, retrying.")],
        ]
    )

    events = await _drain(provider, [StrictTool()], _principal(Role.OFFICE))

    finished = next(event.tool_finished for event in events if event.tool_finished)
    assert finished.result.ok is False
    assert "arguments" in finished.result.content


async def test_the_step_ceiling_withdraws_the_tools_and_ends() -> None:
    """A model that keeps calling the same tool because it dislikes the answer
    is an unbounded bill. On the last step the tools are withdrawn so it has to
    answer with what it has."""
    settings = get_settings().model_copy(update={"agent_max_steps": 2})
    call = StreamEvent(tool_calls=(ToolCall(id="1", name="record", arguments={}),))
    provider = ScriptedProvider([[call], [call], [call]])
    tool = RecordingTool()

    context = ToolContext(principal=_principal(Role.OFFICE), session=None)
    events = [event async for event in run_agent("q", [tool], provider, context, settings=settings)]

    assert len(tool.calls) <= 2
    assert events[-1].done
    # Withdrawn on the final step, which is what forces an answer.
    assert provider.offered[-1] is None


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (Role.ADMIN, {"search_knowledge_base", "find_customer", "get_customer_detail"}),
        (Role.OFFICE, {"search_knowledge_base", "find_customer", "get_customer_detail"}),
        (Role.TECHNICIAN, {"search_knowledge_base", "find_customer", "get_customer_detail"}),
    ],
)
def test_the_toolset_is_built_from_the_principal(role: Role, expected: set[str]) -> None:
    names = {tool.name for tool in tools_for(_principal(role))}

    assert expected <= names


def test_a_role_without_a_permission_is_never_offered_that_tool() -> None:
    """Stronger than refusing the call: a tool the model cannot see is one it
    cannot be talked into using."""
    from app.agent.registry import BUILTIN

    class PricingTool(RecordingTool):
        name = "read_pricing"
        permission: Permission | None = Permission.PRICING_READ

    BUILTIN.append(PricingTool())
    try:
        technician = {tool.name for tool in tools_for(_principal(Role.TECHNICIAN))}
        sales = {tool.name for tool in tools_for(_principal(Role.SALES))}
    finally:
        BUILTIN.pop()

    assert "read_pricing" not in technician
    assert "read_pricing" in sales
