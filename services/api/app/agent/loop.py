"""The agent loop.

    model → tool calls → results → model → … → answer

This is the change from milestone 3, and it is a change of shape rather than of
degree. Retrieval used to be what the endpoint *did*: every question ran the
pipeline, including "what is today's date", which searched a corpus of
water-treatment manuals for a calendar. Now retrieval is one of several things
the model may *choose* to do, and choosing not to is a valid outcome.

Four constraints:

**A step ceiling.** Without one, a model that keeps calling the same tool
because it dislikes the answer is an unbounded bill. When the ceiling is
reached the loop asks for a final answer with tools withdrawn, rather than
returning nothing.

**Tools run concurrently within a step.** A model that asks for a customer
lookup and a manual search at once should wait for the slower, not the sum.

**A failing tool is a result, not an exception.** "The CRM did not answer" goes
into the transcript for the model to relay. The alternative is a 500 that loses
the conversation, and the model saying nothing about why.

**Structured results never reach the model.** A tool returns prose for the model
and a payload for the interface. The customer cards and source panels are built
from the payload; the model reads the prose. Keeping them separate is what stops
an interface change from silently altering what the model is told.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from app.agent.tools import Tool, ToolContext, ToolResult, spec
from app.config import Settings
from app.llm.base import LlmProvider, Message, ToolCall, Usage

logger = logging.getLogger("fieldops.agent")

SYSTEM_PROMPT_TEMPLATE = """You are the assistant for a service business, \
helping its office staff, salespeople and technicians.

The business operates in the {timezone} timezone. When a tool needs a timezone \
and the question does not name one, use that.

You have tools. Use them when they are the right way to answer, and answer \
directly when they are not — a question about the date, a greeting, or a \
follow-up you can already answer from this conversation does not need a tool.

When you use the knowledge base:
- Answer only from the passages it returns. If they do not contain the answer, \
say so plainly. Never fall back on general knowledge: a plausible answer about \
somebody else's equipment is worse than no answer.
- Cite every factual claim with the marker of the passage it came from, like \
[S1], at the end of the sentence it supports. Only the markers you were shown \
exist; never invent one.

When you look a customer up:
- If more than one matches, ask which before answering.
- Quote the technician's notes rather than paraphrasing them. They are what the \
next person on site needs.

Be brief and concrete. The person reading this usually has a customer waiting.
Format with Markdown — short paragraphs, and a list when you are giving steps."""


def system_prompt(settings: Settings) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(timezone=settings.business_timezone)


@dataclass(frozen=True, slots=True)
class ToolRun:
    """One tool call and what came back. Rendered as the trail in the UI."""

    id: str
    name: str
    arguments: dict[str, Any]
    result: ToolResult
    duration_ms: int


@dataclass(frozen=True, slots=True)
class AgentEvent:
    """One thing that happened on the way to an answer."""

    # A tool is about to run.
    tool_started: ToolCall | None = None
    # A tool finished.
    tool_finished: ToolRun | None = None
    # Answer text.
    delta: str = ""
    # The turn is over.
    done: bool = False
    usage: Usage = field(default_factory=Usage)


async def run_agent(
    question: str,
    tools: list[Tool],
    provider: LlmProvider,
    context: ToolContext,
    *,
    settings: Settings,
    history: list[Message] | None = None,
) -> AsyncIterator[AgentEvent]:
    by_name = {tool.name: tool for tool in tools}
    specs = [spec(tool) for tool in tools]

    messages: list[Message] = [Message(role="system", content=system_prompt(settings))]
    if history:
        messages.extend(history)
    messages.append(Message(role="user", content=question))

    usage = Usage()

    for step in range(settings.agent_max_steps):
        # On the last step the tools are withdrawn. The model has to answer with
        # what it has, which is a better failure than an empty response.
        last_step = step == settings.agent_max_steps - 1
        offered = None if last_step else specs

        text_parts: list[str] = []
        calls: list[ToolCall] = []

        async for event in provider.stream(messages, tools=offered):
            if event.delta:
                text_parts.append(event.delta)
                yield AgentEvent(delta=event.delta)
            if event.tool_calls:
                calls.extend(event.tool_calls)
            if event.usage:
                usage = usage + event.usage

        if not calls:
            yield AgentEvent(done=True, usage=usage)
            return

        # A model that emitted both text and tool calls has narrated what it is
        # about to do. That text has already been streamed, and it belongs in
        # the transcript so the model does not repeat it.
        messages.append(
            Message(
                role="assistant",
                content="".join(text_parts),
                tool_calls=tuple(calls),
            )
        )

        for call in calls:
            yield AgentEvent(tool_started=call)

        runs = await asyncio.gather(*(_invoke(by_name, call, context) for call in calls))

        for run in runs:
            yield AgentEvent(tool_finished=run)
            messages.append(
                Message(
                    role="tool",
                    content=run.result.content,
                    tool_call_id=run.id,
                    name=run.name,
                )
            )

    # The ceiling was reached with tools still being requested.
    logger.warning("agent hit the step ceiling of %d", settings.agent_max_steps)
    yield AgentEvent(done=True, usage=usage)


async def _invoke(by_name: dict[str, Tool], call: ToolCall, context: ToolContext) -> ToolRun:
    started = time.perf_counter()
    tool = by_name.get(call.name)

    if tool is None:
        # A model asking for a tool it was not offered is either confused or
        # being steered. Either way it is told plainly, and the loop continues.
        logger.warning("model asked for unknown tool %s", call.name)
        result = ToolResult(
            content=f"There is no tool called {call.name}.",
            summary=f"Unknown tool {call.name}",
            ok=False,
        )
    else:
        try:
            result = await tool.run(context, **call.arguments)
        except TypeError as error:
            # Wrong or missing arguments. Recoverable: the model is told what
            # happened and usually corrects itself on the next step.
            logger.warning("bad arguments for %s: %s", call.name, error)
            result = ToolResult(
                content=f"{call.name} was called with arguments it does not accept: {error}",
                summary=f"{call.name} rejected its arguments",
                ok=False,
            )
        except Exception:
            logger.exception("tool %s failed", call.name)
            result = ToolResult(
                content=f"{call.name} failed and returned nothing.",
                summary=f"{call.name} failed",
                ok=False,
            )

    return ToolRun(
        id=call.id,
        name=call.name,
        arguments=call.arguments,
        result=result,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
