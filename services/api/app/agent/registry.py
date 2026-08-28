"""Which tools a caller is offered.

Filtered per request against the principal, not per deployment. A technician's
model is never told a pricing lookup exists — which is stronger than refusing
the call, because a tool the model cannot see is one it cannot be talked into
using, and prompt injection has nothing to work with.

MCP tools are appended rather than merged: they are namespaced by server, so a
server advertising `search` cannot shadow `search_knowledge_base`.
"""

from __future__ import annotations

from app.agent.builtin.crm import FindCustomer, GetCustomerDetail
from app.agent.builtin.inventory import FindMaterial
from app.agent.builtin.knowledge import SearchKnowledgeBase
from app.agent.mcp import mcp_registry
from app.agent.tools import Tool
from app.auth.rbac import Principal

BUILTIN: list[Tool] = [
    SearchKnowledgeBase(),
    FindCustomer(),
    GetCustomerDetail(),
    FindMaterial(),
]


def tools_for(principal: Principal) -> list[Tool]:
    allowed = [
        tool for tool in BUILTIN if tool.permission is None or principal.can(tool.permission)
    ]
    allowed.extend(mcp_registry().tools)
    return allowed
