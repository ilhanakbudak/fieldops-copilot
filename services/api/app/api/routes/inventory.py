"""Parts lookups, over HTTP.

The same connector the agent's tool uses. Pricing is attached from the caller's
permissions rather than from a query parameter — a client asking for cost does
not get it, a salesperson asking about a valve does.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import PrincipalDep, require
from app.api.schemas import MaterialOut
from app.audit import audit
from app.auth.rbac import Permission
from app.connectors import InventoryUnavailableError, Material, get_inventory
from app.core.errors import ApiError, NotFoundError

router = APIRouter(prefix="/inventory", tags=["inventory"])

read = [require(Permission.INVENTORY_READ)]


def _out(material: Material) -> MaterialOut:
    from app.agent.builtin.inventory import _card

    return MaterialOut.model_validate(_card(material))


@router.get("", response_model=list[MaterialOut], dependencies=read)
async def search_materials(
    principal: PrincipalDep,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=25)] = 10,
) -> list[MaterialOut]:
    try:
        matches = await get_inventory().search_materials(
            q, limit=limit, include_pricing=principal.can(Permission.PRICING_READ)
        )
    except InventoryUnavailableError as error:
        raise ApiError(str(error), detail={"upstream": "inventory"}) from error

    await audit(
        "connector.inventory.search",
        resource_type="query",
        detail={"query": q[:120], "results": len(matches)},
    )
    return [_out(material) for material in matches]


@router.get("/{material_id}", response_model=MaterialOut, dependencies=read)
async def get_material(material_id: str, principal: PrincipalDep) -> MaterialOut:
    try:
        material = await get_inventory().get_material(
            material_id, include_pricing=principal.can(Permission.PRICING_READ)
        )
    except InventoryUnavailableError as error:
        raise ApiError(str(error), detail={"upstream": "inventory"}) from error

    if material is None:
        raise NotFoundError("No such part.")

    await audit(
        "connector.inventory.get_material",
        resource_type="material",
        resource_id=material.id,
        detail={"sku": material.sku},
    )
    return _out(material)
