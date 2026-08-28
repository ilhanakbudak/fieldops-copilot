"""Finding a part, as a tool.

One tool, not two. Unlike the CRM — where disambiguating a customer and pulling
their history cost very different amounts — a parts lookup is one query either
way, and the answer to "where is it" is the same record as the answer to "how
many are there".

The interesting part is the pricing gate. The tool is offered to everyone with
`inventory:read`, which is every role: a technician in a warehouse needs the
aisle and the count. Cost and supplier are attached only for a caller who also
holds `pricing:read`, so the same question asked by a technician and by a
salesperson returns the same locations and a different amount of commercial
detail.

That is field-level gating rather than tool-level, and it is the honest shape of
the requirement — the tool is useful to everyone and half of it is not.
"""

from __future__ import annotations

from typing import Any

from app.agent.tools import ToolContext, ToolResult, failed
from app.auth.rbac import Permission
from app.connectors import InventoryUnavailableError, Material, get_inventory


class FindMaterial:
    name = "find_material"
    description = (
        "Find a part or material in the warehouse inventory by name, part number "
        "or category. Returns where it is stored — warehouse, aisle, row and bin "
        "— and how many are available after allocations. Use this for anything "
        "about stock, parts, materials or where something is kept."
    )
    parameters = {  # noqa: RUF012 - read-only schema
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "A part name, part number or category, as the person said it.",
            }
        },
        "required": ["query"],
    }
    permission: Permission | None = Permission.INVENTORY_READ

    async def run(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        query = str(kwargs.get("query") or "").strip()
        if not query:
            return ToolResult(content="No part was named.", summary="Empty lookup", ok=False)

        # The caller's permissions, not the model's request. A model asking for
        # pricing does not get it; a salesperson asking about a valve does.
        with_pricing = context.principal.can(Permission.PRICING_READ)

        try:
            matches = await get_inventory().search_materials(
                query, limit=5, include_pricing=with_pricing
            )
        except InventoryUnavailableError as error:
            return failed(str(error))

        if not matches:
            return ToolResult(
                content=f"Nothing in the inventory matches “{query}”.",
                summary=f"No part matching “{query}”",
                data={"materials": []},
            )

        return ToolResult(
            content="\n\n".join(_render(material) for material in matches),
            summary=f"Found {len(matches)} part{'s' if len(matches) != 1 else ''}",
            data={"materials": [_card(material) for material in matches]},
        )


def _render(material: Material) -> str:
    lines = [f"{material.name} ({material.sku}) — {material.available} available"]

    for location in material.stock:
        detail = f"- {location.where}: {location.on_hand} on hand"
        if location.committed:
            detail += f", {location.committed} committed to jobs, {location.available} free"
        lines.append(detail)

    if material.below_reorder:
        # Said in the transcript rather than left for the model to work out from
        # two numbers, because it is the one thing worth mentioning unprompted.
        lines.append(
            f"- Below the reorder point of {material.reorder_point}. Worth flagging to the office."
        )

    if material.pricing:
        lines.append(
            f"- Supplier {material.pricing.supplier} ({material.pricing.supplier_sku}), "
            f"{material.pricing.lead_time_days} day lead time, "
            f"cost ${material.pricing.cost_usd:,.2f}, list ${material.pricing.list_usd:,.2f}"
        )

    return "\n".join(lines)


def _card(material: Material) -> dict[str, Any]:
    return {
        "id": material.id,
        "sku": material.sku,
        "name": material.name,
        "category": material.category,
        "unit": material.unit,
        "available": material.available,
        "reorderPoint": material.reorder_point,
        "belowReorder": material.below_reorder,
        "stock": [
            {
                "warehouse": location.warehouse,
                "aisle": location.aisle,
                "row": location.row,
                "bin": location.bin,
                "onHand": location.on_hand,
                "committed": location.committed,
                "available": location.available,
            }
            for location in material.stock
        ],
        "pricing": {
            "supplier": material.pricing.supplier,
            "supplierSku": material.pricing.supplier_sku,
            "leadTimeDays": material.pricing.lead_time_days,
            "costUsd": material.pricing.cost_usd,
            "listUsd": material.pricing.list_usd,
        }
        if material.pricing
        else None,
    }
