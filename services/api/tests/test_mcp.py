"""The MCP client.

Two things worth asserting, and one deliberately not.

**Asserted:** that a server which fails to start costs its tools and nothing
else, and that discovered tools are namespaced so two servers cannot collide.

**Asserted against a real server:** the official `mcp-server-time` is a project
dependency, so the integration test launches it as a subprocess and calls it. It
is marked so it can be skipped where spawning a process is unwelcome, but it
runs by default — an MCP client that has only ever been tested against a stub is
a client that has never spoken the protocol.

**Not asserted:** what the time server returns. That is somebody else's code and
somebody else's test suite.
"""

from __future__ import annotations

import pytest

from app.agent.mcp import McpRegistry, McpServerSpec, parse_servers
from app.config import get_settings


def test_server_specs_are_parsed_from_json() -> None:
    specs = parse_servers('[{"name": "time", "command": "python", "args": ["-m", "x"]}]')

    assert specs == [McpServerSpec(name="time", command="python", args=["-m", "x"])]


def test_malformed_configuration_disables_mcp_rather_than_stopping_the_service() -> None:
    """The same reasoning as a dead server: an assistant that cannot answer
    questions about its own manuals because a config line has a stray comma
    would be a poor trade."""
    assert parse_servers("not json at all") == []
    assert parse_servers("") == []
    assert parse_servers('[{"name": "broken"}]') == []


async def test_a_server_that_will_not_start_costs_only_its_tools() -> None:
    registry = McpRegistry()
    settings = get_settings().model_copy(
        update={"mcp_servers": '[{"name": "ghost", "command": "definitely-not-a-real-binary"}]'}
    )

    await registry.start(settings)

    assert registry.tools == []
    assert registry.servers == []
    await registry.stop()


@pytest.mark.mcp
async def test_the_official_time_server_connects_and_answers() -> None:
    """`mcp-server-time` is one of the official reference servers, installed as
    a dependency. This is the clock the assistant uses when asked the date —
    reached over the protocol rather than through a bespoke integration."""
    from app.agent.tools import ToolContext

    registry = McpRegistry()
    await registry.start(get_settings())

    try:
        if not registry.servers:  # pragma: no cover - environment without a python on PATH
            pytest.skip("the time server did not start in this environment")

        names = {tool.name for tool in registry.tools}
        # Namespaced by server, so two servers advertising `search` cannot
        # shadow each other or `search_knowledge_base`.
        assert all(name.startswith("mcp_time_") for name in names)
        assert "mcp_time_get_current_time" in names

        clock = next(tool for tool in registry.tools if tool.name == "mcp_time_get_current_time")
        result = await clock.run(
            ToolContext(principal=None, session=None), timezone="America/New_York"
        )

        assert result.ok
        assert "datetime" in result.content
    finally:
        await registry.stop()


@pytest.mark.mcp
async def test_an_mcp_tool_carries_its_own_schema() -> None:
    """The adapter is thin on purpose: a server describes its own tools, and
    this client does not second-guess the description."""
    registry = McpRegistry()
    await registry.start(get_settings())

    try:
        if not registry.servers:  # pragma: no cover
            pytest.skip("the time server did not start in this environment")

        clock = next(tool for tool in registry.tools if tool.name.endswith("get_current_time"))

        assert clock.parameters.get("type") == "object"
        assert clock.description
        # No permission: the clock reaches no company data, which is why it is
        # offered to every role.
        assert clock.permission is None
    finally:
        await registry.stop()
