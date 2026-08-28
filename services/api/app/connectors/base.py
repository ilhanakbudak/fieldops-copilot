"""The CRM boundary.

**Read-only by construction, not by convention.** There is no `create_job` on
this protocol and no `update_customer`. An assistant that can be talked into
editing a customer record is a liability, and the way to guarantee it cannot is
for the capability to be absent from the interface the model reaches — not
present and guarded by a prompt instruction.

The domain models are ours, not Service Fusion's. An adapter translates. That is
the difference between an integration and a dependency: when the vendor renames
a field, one file changes and the tools, the prompt and the interface do not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Address:
    line1: str
    city: str
    state: str
    postcode: str

    def __str__(self) -> str:
        return f"{self.line1}, {self.city}, {self.state} {self.postcode}"


@dataclass(frozen=True, slots=True)
class Equipment:
    id: str
    model: str
    serial: str
    installed_on: str
    location: str
    warranty_until: str | None = None


@dataclass(frozen=True, slots=True)
class Job:
    id: str
    date: str
    kind: str
    summary: str
    technician: str
    # Free-text notes a technician left on site. Frequently the most useful
    # thing in the record and the hardest to find, which is most of the reason
    # this connector exists.
    notes: str = ""
    status: str = "completed"


@dataclass(frozen=True, slots=True)
class Estimate:
    id: str
    date: str
    summary: str
    amount_usd: float
    status: str


@dataclass(frozen=True, slots=True)
class Invoice:
    id: str
    date: str
    amount_usd: float
    balance_usd: float
    status: str


@dataclass(frozen=True, slots=True)
class Customer:
    id: str
    name: str
    phone: str
    email: str
    address: Address
    since: str
    notes: str = ""


@dataclass(frozen=True, slots=True)
class CustomerDetail:
    customer: Customer
    equipment: list[Equipment] = field(default_factory=list)
    jobs: list[Job] = field(default_factory=list)
    estimates: list[Estimate] = field(default_factory=list)
    invoices: list[Invoice] = field(default_factory=list)

    @property
    def outstanding_usd(self) -> float:
        return round(sum(invoice.balance_usd for invoice in self.invoices), 2)


class CrmUnavailableError(Exception):
    """The CRM could not be reached.

    Distinct from "no customer found", and the distinction matters: one means
    the customer does not exist, the other means we do not know. They lead to
    completely different next actions and must never be collapsed into an empty
    list.
    """


@runtime_checkable
class CrmConnector(Protocol):
    name: str

    async def search_customers(self, query: str, *, limit: int = 5) -> list[Customer]: ...

    async def get_customer(self, customer_id: str) -> CustomerDetail | None: ...

    async def find_by_phone(self, phone: str) -> Customer | None: ...
