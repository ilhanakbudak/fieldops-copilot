"""Model Context Protocol client.

The clock in this assistant is not a tool I wrote. It is
[`mcp-server-time`](https://github.com/modelcontextprotocol/servers), one of the
official MCP reference servers, running as a subprocess and speaking MCP over
stdio. This module discovers whatever tools it advertises and hands them to the
agent alongside the ones defined in `app/agent/builtin/`.

**Why bother, when "what is the date" is four lines of Python.** Because the
four lines are not the point. A service business will want the assistant to
reach a dozen systems that already exist, and the choice is between writing and
maintaining a dozen bespoke integrations or speaking the protocol those systems
increasingly already speak. Demonstrating the second on a trivial tool is
cheaper than demonstrating it on a hard one, and it is the same code path.

It also settles the question the tool routing exists to answer: asked the date,
the model calls the clock and the retrieval pipeline never runs.

**Failure is not fatal.** A server that will not start, or that hangs on
handshake, costs the assistant those tools and nothing else. An internal
assistant that cannot answer questions about its own manuals because an
unrelated subprocess died would be a poor trade.

**What comes back is translated, not forwarded.** Every tool in
`app/agent/builtin/` returns prose for the model and structure for the
interface, deliberately and for reasons written down there. MCP servers do not
know about that split — a great many, including the time server, return a JSON
document as their text content. Forwarding it would hand the model a payload to
paraphrase and the interface a code block to render, which is the one thing the
built-in tools were careful not to do. So the response is parsed once and both
halves are produced from it. See `app/agent/render.py`, which is generic: there
is no branch in this client that knows what a timezone is.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

from app.agent.render import fields, humanise, parse_json
from app.agent.tools import ToolContext, ToolResult, failed
from app.auth.rbac import Permission
from app.config import Settings

logger = logging.getLogger("fieldops.mcp")

# A slow server must not become a slow assistant. Both are generous for a local
# subprocess and short enough that a hung one is noticed at boot.
CONNECT_TIMEOUT = 20.0
CALL_TIMEOUT = 20.0


@dataclass(frozen=True, slots=True)
class McpServerSpec:
    name: str
    command: str
    args: list[str]


def parse_servers(raw: str) -> list[McpServerSpec]:
    """Read `MCP_SERVERS` — a JSON array of {name, command, args}.

    Malformed configuration disables MCP rather than stopping the service, for
    the same reason a dead server does.
    """
    if not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("MCP_SERVERS is not valid JSON; no MCP tools will be available")
        return []

    servers: list[McpServerSpec] = []
    for entry in parsed if isinstance(parsed, list) else []:
        if not isinstance(entry, dict) or not entry.get("command"):
            continue
        servers.append(
            McpServerSpec(
                name=str(entry.get("name") or entry["command"]),
                command=str(entry["command"]),
                args=[str(arg) for arg in entry.get("args", [])],
            )
        )
    return servers


class McpTool:
    """One tool advertised by an MCP server, adapted to the agent's protocol.

    `permission` is `None`: these tools carry no access to company data, which
    is exactly why the time server is a reasonable thing to expose to everyone.
    A future MCP server that *did* reach customer records would need a
    permission here, and the fact that this is a per-tool decision rather than a
    per-protocol one is the point of keeping the adapter thin.
    """

    permission: Permission | None = None

    def __init__(self, client: McpClient, name: str, description: str, schema: dict[str, Any]):
        self._client = client
        self._remote_name = name
        # Namespaced, so two servers advertising `search` cannot collide and so
        # the transcript says where a tool came from.
        self.name = f"mcp_{client.server.name}_{name}".replace("-", "_")
        self.description = description
        self.parameters = schema or {"type": "object", "properties": {}}

    async def run(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        return await self._client.call(self._remote_name, kwargs)


class McpClient:
    """A connection to one MCP server, held open for the life of the process."""

    def __init__(self, server: McpServerSpec) -> None:
        self.server = server
        self._session: Any = None
        self._stack: AsyncExitStack | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> list[McpTool]:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        stack = AsyncExitStack()
        try:
            transport = await asyncio.wait_for(
                stack.enter_async_context(
                    stdio_client(
                        StdioServerParameters(command=self.server.command, args=self.server.args)
                    )
                ),
                timeout=CONNECT_TIMEOUT,
            )
            read, write = transport
            session = await stack.enter_async_context(ClientSession(read, write))
            await asyncio.wait_for(session.initialize(), timeout=CONNECT_TIMEOUT)
            listing = await asyncio.wait_for(session.list_tools(), timeout=CONNECT_TIMEOUT)
        except Exception:
            await stack.aclose()
            raise

        self._stack = stack
        self._session = session

        tools = [
            McpTool(
                self,
                tool.name,
                tool.description or f"{tool.name} (from the {self.server.name} MCP server)",
                dict(tool.inputSchema or {}),
            )
            for tool in listing.tools
        ]
        logger.info(
            "mcp server %s connected: %s",
            self.server.name,
            ", ".join(tool.name for tool in listing.tools) or "no tools",
        )
        return tools

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if self._session is None:
            return failed(f"The {self.server.name} tool server is not connected.")

        try:
            # One call at a time per server. The stdio transport is a single
            # pipe; interleaving two requests on it corrupts both.
            async with self._lock:
                response = await asyncio.wait_for(
                    self._session.call_tool(name, arguments), timeout=CALL_TIMEOUT
                )
        except TimeoutError:
            logger.warning("mcp tool %s timed out", name)
            return failed(f"The {self.server.name} tool did not respond in time.")
        except Exception:
            logger.exception("mcp tool %s failed", name)
            return failed(f"The {self.server.name} tool could not be reached.")

        return self._translate(name, response)

    def _translate(self, name: str, response: Any) -> ToolResult:
        """The server's answer, as prose for the model and rows for the screen.

        `structuredContent` is where a current server puts its result and is
        preferred when present. Falling back to parsing the text blocks is not
        a workaround: it is what the servers in the wild actually do today, the
        official time server included, and a client that only read the newer
        field would show a JSON blob for most of the ecosystem.
        """
        text = "\n".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        document = getattr(response, "structuredContent", None) or parse_json(text)
        ok = not getattr(response, "isError", False)

        if document is None:
            # Already prose. Some servers answer in a sentence, which needs
            # nothing from us and must not be mangled by trying.
            return ToolResult(
                content=text or "The tool returned nothing.",
                summary=self._summary(name, ok),
                data={"server": self.server.name, "tool": name, "rows": []},
                ok=ok,
            )

        return ToolResult(
            content=humanise(document) or text or "The tool returned nothing.",
            summary=self._summary(name, ok),
            data={
                "server": self.server.name,
                "tool": name,
                # Labelled rows rather than the raw document: the interface
                # renders these directly, so it never has to know the shape any
                # particular server chose.
                "rows": fields(document),
            },
            ok=ok,
        )

    def _summary(self, name: str, ok: bool) -> str:
        pretty = name.replace("_", " ")
        if not ok:
            return f"{pretty} failed on the {self.server.name} server"
        return f"{pretty} · {self.server.name} MCP server"

    async def close(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception:
                logger.debug("mcp server %s did not close cleanly", self.server.name)
        self._stack = None
        self._session = None


class McpRegistry:
    """Every connected server, and the tools they collectively offer."""

    def __init__(self) -> None:
        self._clients: list[McpClient] = []
        self._tools: list[McpTool] = []

    @property
    def tools(self) -> list[McpTool]:
        return list(self._tools)

    @property
    def servers(self) -> list[str]:
        return [client.server.name for client in self._clients]

    async def start(self, settings: Settings) -> None:
        for spec in parse_servers(settings.mcp_servers):
            client = McpClient(spec)
            try:
                tools = await client.connect()
            except Exception:
                logger.warning(
                    "mcp server %s did not start; continuing without its tools",
                    spec.name,
                    exc_info=True,
                )
                continue
            self._clients.append(client)
            self._tools.extend(tools)

    async def stop(self) -> None:
        for client in self._clients:
            await client.close()
        self._clients.clear()
        self._tools.clear()


_registry = McpRegistry()


def mcp_registry() -> McpRegistry:
    return _registry
