"""The inventory boundary.

Same shape as the CRM connector — a protocol, a mock and a vendor adapter — with
one addition that matters more here than anywhere else in the codebase.

**Cost is a separate field from stock, and separately gated.** A technician
standing in a warehouse needs the aisle and the count. What they do not need,
and should not have on a customer's driveway, is what the company paid for the
part. So `MaterialStock` carries location and quantity, `MaterialPricing` carries
cost and margin, and a lookup returns the second only when the caller holds
`pricing:read`. Field-level gating rather than tool-level: the tool is useful to
everyone, and half of it is not.

The vendor adapter is Ply-shaped. Ply issues credentials to its customers, not
to a public repository, so — like Service Fusion — what is demonstrable is the
boundary and the translation, with the mock behind the same protocol for
anything that has to actually run.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx

logger = logging.getLogger("fieldops.inventory")

FIXTURES = Path(__file__).resolve().parents[4] / "fixtures" / "inventory" / "materials.json"


@dataclass(frozen=True, slots=True)
class StockLocation:
    warehouse: str
    aisle: str
    row: str
    bin: str
    on_hand: int
    # Reserved against scheduled jobs. The number that matters is what is left
    # after these: a technician told there are six of something, who arrives to
    # find four of them spoken for, has been given a true number and a useless
    # one.
    committed: int = 0

    @property
    def available(self) -> int:
        return max(self.on_hand - self.committed, 0)

    @property
    def where(self) -> str:
        return f"{self.warehouse} · aisle {self.aisle}, row {self.row}, bin {self.bin}"


@dataclass(frozen=True, slots=True)
class MaterialPricing:
    """Only ever populated for a caller holding `pricing:read`."""

    supplier: str
    supplier_sku: str
    lead_time_days: int
    cost_usd: float
    list_usd: float


@dataclass(frozen=True, slots=True)
class Material:
    id: str
    sku: str
    name: str
    category: str
    unit: str
    reorder_point: int
    stock: list[StockLocation] = field(default_factory=list)
    # Absent unless the caller may see it. `None` and "zero" are different
    # things, and a UI that renders a missing price as $0.00 is worse than one
    # that renders nothing.
    pricing: MaterialPricing | None = None

    @property
    def available(self) -> int:
        return sum(location.available for location in self.stock)

    @property
    def below_reorder(self) -> bool:
        return self.available < self.reorder_point


class InventoryUnavailableError(Exception):
    """The inventory system could not be reached.

    Distinct from "no such part", for the same reason as the CRM: one means it
    does not exist, the other means we do not know.
    """


@runtime_checkable
class InventoryConnector(Protocol):
    name: str

    async def search_materials(
        self, query: str, *, limit: int = 5, include_pricing: bool = False
    ) -> list[Material]: ...

    async def get_material(
        self, material_id: str, *, include_pricing: bool = False
    ) -> Material | None: ...


# --- Mock -------------------------------------------------------------------


class MockInventoryConnector:
    name = "mock"

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or FIXTURES
        self._records: list[dict[str, Any]] | None = None

    def _load(self) -> list[dict[str, Any]]:
        if self._records is None:
            self._records = (
                json.loads(self._path.read_text())["materials"] if self._path.exists() else []
            )
        return self._records

    async def search_materials(
        self, query: str, *, limit: int = 5, include_pricing: bool = False
    ) -> list[Material]:
        """Scored, so a size qualifier decides between near-identical parts.

        "1-inch PEX ball valve" and "3/4-inch PEX ball valve" share every word
        that matters except one. Filtering on all terms returns nothing when the
        catalogue writes `1-inch` and the technician types `1 inch`; scoring
        ranks the right one first and still shows the neighbour, which is what
        somebody standing at the shelf wants to see.
        """
        terms = _significant(_tokens(query))
        if not terms:
            return []

        scored: list[tuple[int, dict[str, Any]]] = []
        for record in self._load():
            haystack = _tokens(
                " ".join([record["name"], record["sku"], record["category"], record["id"]])
            )
            score = sum(1 for term in terms if term in haystack)
            if score:
                scored.append((score, record))

        scored.sort(key=lambda row: (-row[0], row[1]["name"]))
        return [_material(record, include_pricing) for _score, record in scored[:limit]]

    async def get_material(
        self, material_id: str, *, include_pricing: bool = False
    ) -> Material | None:
        wanted = material_id.strip().lower()
        record = next(
            (
                item
                for item in self._load()
                if item["id"].lower() == wanted or item["sku"].lower() == wanted
            ),
            None,
        )
        return _material(record, include_pricing) if record else None


def _significant(tokens: set[str]) -> list[str]:
    """Drop stray letters, keep every digit.

    A single alphabetic character is noise. A single *digit* is the whole
    question: `1` and `3`/`4` are what separate a 1-inch valve from a 3/4-inch
    one, and filtering them out — which the first version did — made the two
    indistinguishable and let an alphabetical tie-break decide.
    """
    return [token for token in tokens if len(token) > 1 or token.isdigit()]


def _tokens(text: str) -> set[str]:
    # Punctuation split, so `3/4-inch` matches `3 4 inch` and `1-inch` matches
    # `1 inch`. A technician types what is quickest, not what the catalogue says.
    cleaned = "".join(character if character.isalnum() else " " for character in text.lower())
    return {token for token in cleaned.split() if token}


def _material(record: dict[str, Any], include_pricing: bool) -> Material:
    return Material(
        id=record["id"],
        sku=record["sku"],
        name=record["name"],
        category=record["category"],
        unit=record["unit"],
        reorder_point=record["reorder_point"],
        stock=[StockLocation(**location) for location in record.get("stock", [])],
        pricing=MaterialPricing(
            supplier=record["supplier"],
            supplier_sku=record["supplier_sku"],
            lead_time_days=record["lead_time_days"],
            cost_usd=record["cost_usd"],
            list_usd=record["list_usd"],
        )
        if include_pricing
        else None,
    )


# --- Ply --------------------------------------------------------------------


class PlyConnector:
    """Ply-shaped, and never run against the real API — see the module docstring.

    Read-only by construction, like the CRM adapter: `_get` is the only request
    method and there is no generic verb parameter for a write to appear through.
    """

    name = "ply"

    def __init__(self, api_key: str, *, base_url: str = "https://api.ply.io/v1") -> None:
        if not api_key:
            raise ValueError("Ply requires an API key")
        self._key = api_key
        self._base_url = base_url.rstrip("/")

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
                response = await client.get(
                    f"{self._base_url}{path}",
                    params=params,
                    headers={"authorization": f"Bearer {self._key}"},
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 404:
                return None
            raise InventoryUnavailableError("The inventory system rejected the request.") from error
        except httpx.HTTPError as error:
            logger.warning("ply unreachable: %s", error)
            raise InventoryUnavailableError("The inventory system could not be reached.") from error

    async def search_materials(
        self, query: str, *, limit: int = 5, include_pricing: bool = False
    ) -> list[Material]:
        payload = await self._get("/materials", {"q": query, "limit": limit})
        items = payload.get("items", []) if isinstance(payload, dict) else (payload or [])
        return [_from_ply(item, include_pricing) for item in items][:limit]

    async def get_material(
        self, material_id: str, *, include_pricing: bool = False
    ) -> Material | None:
        payload = await self._get(f"/materials/{material_id}")
        return _from_ply(payload, include_pricing) if payload else None


def _from_ply(item: dict[str, Any], include_pricing: bool) -> Material:
    """Translate. Every `.get` has a default, because a catalogue maintained
    under time pressure has gaps and a KeyError in a warehouse is the worst
    place to find one."""
    locations = [
        StockLocation(
            warehouse=location.get("warehouse_name", ""),
            aisle=location.get("aisle", ""),
            row=location.get("row", ""),
            bin=location.get("bin", ""),
            on_hand=int(location.get("quantity_on_hand") or 0),
            committed=int(location.get("quantity_allocated") or 0),
        )
        for location in item.get("locations", [])
    ]

    pricing = None
    if include_pricing:
        supplier = item.get("preferred_supplier") or {}
        pricing = MaterialPricing(
            supplier=supplier.get("name", ""),
            supplier_sku=supplier.get("supplier_part_number", ""),
            lead_time_days=int(supplier.get("lead_time_days") or 0),
            cost_usd=float(item.get("unit_cost") or 0),
            list_usd=float(item.get("list_price") or 0),
        )

    return Material(
        id=str(item.get("id", "")),
        sku=item.get("part_number", ""),
        name=item.get("description", ""),
        category=item.get("category", ""),
        unit=item.get("unit_of_measure", "each"),
        reorder_point=int(item.get("reorder_point") or 0),
        stock=locations,
        pricing=pricing,
    )
