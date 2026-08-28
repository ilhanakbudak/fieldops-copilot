"""A synthetic CRM.

Every customer, job and serial number in `fixtures/crm/` is invented. The
addresses use street names that do not exist in the towns named, the phone
numbers are in the 555 range reserved for fiction, and the emails are on
`example.com`. That is deliberate to the point of pedantry: a public repository
containing records that merely *look* real is indistinguishable, to anyone
finding it later, from a repository containing a leak.

The dataset is shaped around the scenarios worth demonstrating rather than
around volume — a customer with a radon system and a warm-water complaint, one
with an outstanding balance, two with the same surname so name search has to
disambiguate.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.connectors.base import (
    Address,
    Customer,
    CustomerDetail,
    Equipment,
    Estimate,
    Invoice,
    Job,
)
from app.lib.phone import normalise_phone

FIXTURES = Path(__file__).resolve().parents[4] / "fixtures" / "crm" / "customers.json"


class MockCrmConnector:
    name = "mock"

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or FIXTURES
        self._records: list[dict[str, Any]] | None = None

    def _load(self) -> list[dict[str, Any]]:
        if self._records is None:
            if not self._path.exists():
                self._records = []
            else:
                self._records = json.loads(self._path.read_text())["customers"]
        return self._records

    async def search_customers(self, query: str, *, limit: int = 5) -> list[Customer]:
        """Match on name, town, phone or account id.

        Scored rather than filtered, so "John Smith Portland" ranks the Portland
        Smith above the one in Gorham instead of returning neither. The office
        staff this is for say the town precisely because there are two.
        """
        terms = [term for term in query.lower().split() if len(term) > 1]
        if not terms:
            return []

        digits = normalise_phone(query)
        scored: list[tuple[int, dict[str, Any]]] = []

        for record in self._load():
            haystack = " ".join(
                [
                    record["name"],
                    record["address"]["city"],
                    record["address"]["state"],
                    record["address"]["line1"],
                    record["id"],
                ]
            ).lower()
            score = sum(1 for term in terms if term in haystack)
            if digits and normalise_phone(record["phone"]) == digits:
                score += 10
            if score:
                scored.append((score, record))

        scored.sort(key=lambda row: (-row[0], row[1]["name"]))
        return [_customer(record) for _score, record in scored[:limit]]

    async def get_customer(self, customer_id: str) -> CustomerDetail | None:
        record = next(
            (item for item in self._load() if item["id"].lower() == customer_id.lower()), None
        )
        if record is None:
            return None

        return CustomerDetail(
            customer=_customer(record),
            equipment=[Equipment(**item) for item in record.get("equipment", [])],
            jobs=[Job(**item) for item in record.get("jobs", [])],
            estimates=[Estimate(**item) for item in record.get("estimates", [])],
            invoices=[Invoice(**item) for item in record.get("invoices", [])],
        )

    async def find_by_phone(self, phone: str) -> Customer | None:
        digits = normalise_phone(phone)
        if not digits:
            return None
        record = next(
            (item for item in self._load() if normalise_phone(item["phone"]) == digits), None
        )
        return _customer(record) if record else None


def _customer(record: dict[str, Any]) -> Customer:
    return Customer(
        id=record["id"],
        name=record["name"],
        phone=record["phone"],
        email=record["email"],
        address=Address(**record["address"]),
        since=record["since"],
        notes=record.get("notes", ""),
    )
