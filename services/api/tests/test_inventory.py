"""The inventory connector.

The two assertions worth having: that a size qualifier picks the right one of
two near-identical parts, and that cost is withheld from a role that may not see
it — including when the model asks for it.
"""

from __future__ import annotations

import inspect

import pytest
from httpx import AsyncClient

from app.agent.builtin.inventory import FindMaterial
from app.agent.tools import ToolContext
from app.auth.rbac import Principal, Role
from app.connectors import MockInventoryConnector, PlyConnector
from app.connectors.inventory import InventoryConnector
from app.core.ids import new_id
from tests.conftest import login


def _principal(role: Role) -> Principal:
    return Principal(
        user_id=new_id(),
        email=f"{role.value}@example.com",
        full_name=role.value.title(),
        role=role,
        session_id=new_id(),
    )


@pytest.fixture
def inventory() -> MockInventoryConnector:
    return MockInventoryConnector()


def test_the_mock_satisfies_the_protocol(inventory: MockInventoryConnector) -> None:
    assert isinstance(inventory, InventoryConnector)


async def test_the_headline_question_resolves(inventory: MockInventoryConnector) -> None:
    """ "Where is the 1-inch PEX ball valve and how many do we have?" — location
    and count, which is the whole of what somebody in a van is asking."""
    matches = await inventory.search_materials("1-inch PEX ball valve")

    assert matches[0].sku == "PEX-BV-100"
    location = matches[0].stock[0]
    assert location.warehouse == "Portland main"
    assert location.bin == "C4-12"
    assert matches[0].available == 36


async def test_a_size_qualifier_picks_between_near_identical_parts(
    inventory: MockInventoryConnector,
) -> None:
    """Two valves a size apart share every word that matters except one."""
    one_inch = await inventory.search_materials("1 inch PEX ball valve")
    three_quarter = await inventory.search_materials("3/4 inch PEX ball valve")

    assert one_inch[0].sku == "PEX-BV-100"
    assert three_quarter[0].sku == "PEX-BV-075"


async def test_punctuation_in_the_catalogue_does_not_have_to_be_typed(
    inventory: MockInventoryConnector,
) -> None:
    """The catalogue says `3/4-inch`; a technician types `3 4 inch` or `.75`.
    Filtering on exact terms would return nothing for the first two."""
    for written in ("3/4-inch ball valve", "3 4 inch ball valve"):
        matches = await inventory.search_materials(written)
        assert matches, written
        assert matches[0].sku == "PEX-BV-075", written


async def test_available_is_net_of_what_is_committed(
    inventory: MockInventoryConnector,
) -> None:
    """A technician told there are six of something, who arrives to find four
    spoken for, has been given a true number and a useless one."""
    material = await inventory.get_material("NG-BV-14")

    assert material is not None
    assert material.stock[0].on_hand == 5
    assert material.stock[0].committed == 2
    assert material.available == 3


async def test_a_part_below_its_reorder_point_is_flagged(
    inventory: MockInventoryConnector,
) -> None:
    material = await inventory.get_material("NG-BV-14")

    assert material is not None
    assert material.below_reorder


async def test_stock_at_a_second_warehouse_is_still_stock(
    inventory: MockInventoryConnector,
) -> None:
    """Out at the main warehouse is not out of stock."""
    material = await inventory.get_material("UV-LAMP-12")

    assert material is not None
    assert material.stock[0].on_hand == 0
    assert material.available == 2


async def test_pricing_is_absent_unless_asked_for(
    inventory: MockInventoryConnector,
) -> None:
    without = await inventory.search_materials("PEX ball valve")
    with_pricing = await inventory.search_materials("PEX ball valve", include_pricing=True)

    assert without[0].pricing is None
    assert with_pricing[0].pricing is not None
    assert with_pricing[0].pricing.cost_usd == 11.4


async def test_the_tool_gates_cost_on_the_callers_permission() -> None:
    """The gate is field-level, not tool-level: the tool is useful to everyone
    and half of it is not. And it reads the caller's permissions, not the
    model's request — a model asking for cost does not get it."""
    tool = FindMaterial()

    technician = await tool.run(
        ToolContext(principal=_principal(Role.TECHNICIAN), session=None),
        query="1-inch PEX ball valve",
    )
    salesperson = await tool.run(
        ToolContext(principal=_principal(Role.SALES), session=None),
        query="1-inch PEX ball valve",
    )

    assert "bin C4-12" in technician.content
    assert "cost" not in technician.content.lower()
    assert "Halstead" not in technician.content

    assert "bin C4-12" in salesperson.content
    assert "$11.40" in salesperson.content


def test_the_ply_adapter_issues_no_writes() -> None:
    """Read-only by construction, checked structurally — the same guarantee as
    the CRM adapter, and it has to keep holding after somebody adds a feature."""
    source = inspect.getsource(PlyConnector)

    for verb in (".post(", ".put(", ".patch(", ".delete("):
        assert source.count(verb) == 0, f"the adapter must not {verb.strip('.(')}"


def test_the_protocol_has_no_write_methods() -> None:
    methods = {name for name in dir(InventoryConnector) if not name.startswith("_")}

    assert not {name for name in methods if name.startswith(("create", "update", "adjust"))}


def test_the_ply_adapter_refuses_to_start_without_a_key() -> None:
    with pytest.raises(ValueError):
        PlyConnector("")


class TestOverHttp:
    async def test_a_technician_sees_location_but_not_cost(self, client: AsyncClient) -> None:
        await login(client, "technician")

        response = await client.get("/inventory", params={"q": "1-inch PEX ball valve"})

        assert response.status_code == 200
        material = response.json()[0]
        assert material["stock"][0]["bin"] == "C4-12"
        # Absent, not zero. A UI rendering a withheld price as $0.00 is worse
        # than one rendering nothing.
        assert material["pricing"] is None

    async def test_a_salesperson_sees_cost(self, client: AsyncClient) -> None:
        await login(client, "sales")

        material = (await client.get("/inventory", params={"q": "1-inch PEX ball valve"})).json()[0]

        assert material["pricing"]["costUsd"] == 11.4
        assert material["pricing"]["supplier"] == "Halstead Fluid Supply"

    async def test_a_lookup_requires_a_session(self, client: AsyncClient) -> None:
        assert (await client.get("/inventory", params={"q": "valve"})).status_code == 401

    async def test_an_unknown_part_is_a_404(self, client: AsyncClient) -> None:
        await login(client, "technician")

        assert (await client.get("/inventory/NOPE-000")).status_code == 404

    async def test_a_lookup_is_audited(self, client: AsyncClient) -> None:
        from sqlalchemy import select

        from app.db.engine import get_sessionmaker
        from app.db.models import AuditEvent

        await login(client, "technician")
        await client.get("/inventory", params={"q": "valve"})

        async with get_sessionmaker()() as db:
            actions = list((await db.execute(select(AuditEvent.action))).scalars())

        assert "connector.inventory.search" in actions
