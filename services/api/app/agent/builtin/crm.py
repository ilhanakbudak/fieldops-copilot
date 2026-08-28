"""Customer lookups, as tools.

Two tools rather than one, because they answer different questions and cost
different amounts. *"Which John Smith?"* is a search over names and towns.
*"What did we install and what was the last call about?"* is four record
fetches. Collapsing them into one tool means every disambiguation pulls a full
history for a customer that turns out to be the wrong one.

Both are gated on `customers:read`. Every role in this business holds it — a
technician on a driveway needs the service history as much as the office does —
but the gate is here so that adding a role that should not have it is a change
to the permission table rather than to this file.
"""

from __future__ import annotations

from typing import Any

from app.agent.tools import ToolContext, ToolResult, failed
from app.auth.rbac import Permission
from app.connectors import CrmUnavailableError, CustomerDetail, get_crm


class FindCustomer:
    name = "find_customer"
    description = (
        "Find a customer in the CRM by name, town, phone number or account id. "
        "Returns matching customers with their address and account id. Use this "
        "first when a question names a person, and use the account id it returns "
        "with get_customer_detail."
    )
    # A class attribute rather than a ClassVar annotation: the protocol
    # declares `parameters` as an instance member so that an adapter can set
    # it per instance, which is how MCP tools carry their own schema.
    parameters = {  # noqa: RUF012 - read-only schema, never mutated
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "A name, town, phone number or account id — or several together.",
            }
        },
        "required": ["query"],
    }
    permission: Permission | None = Permission.CUSTOMERS_READ

    async def run(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        query = str(kwargs.get("query") or "").strip()
        if not query:
            return ToolResult(content="No search term was given.", summary="Empty lookup", ok=False)

        try:
            matches = await get_crm().search_customers(query, limit=5)
        except CrmUnavailableError as error:
            return failed(str(error))

        if not matches:
            return ToolResult(
                content=f"No customer in the CRM matches “{query}”.",
                summary=f"No CRM match for “{query}”",
                data={"customers": []},
            )

        lines = [
            f"{customer.id} · {customer.name} · {customer.address} · {customer.phone}"
            for customer in matches
        ]
        # Said explicitly, because a model handed two rows will otherwise pick
        # the first and sound certain about it.
        preamble = (
            "More than one customer matches. Ask which one before answering.\n"
            if len(matches) > 1
            else ""
        )
        return ToolResult(
            content=preamble + "\n".join(lines),
            summary=f"Found {len(matches)} customer{'s' if len(matches) != 1 else ''}",
            data={"customers": [_customer_card(customer) for customer in matches]},
        )


class GetCustomerDetail:
    name = "get_customer_detail"
    description = (
        "Get a customer's full record from the CRM: installed equipment with "
        "serial numbers and warranty dates, recent jobs with the technician's "
        "notes, open estimates, and invoices with any outstanding balance. "
        "Requires the account id from find_customer."
    )
    # A class attribute rather than a ClassVar annotation: the protocol
    # declares `parameters` as an instance member so that an adapter can set
    # it per instance, which is how MCP tools carry their own schema.
    parameters = {  # noqa: RUF012 - read-only schema, never mutated
        "type": "object",
        "properties": {
            "customer_id": {
                "type": "string",
                "description": "The account id, for example NG-1042.",
            }
        },
        "required": ["customer_id"],
    }
    permission: Permission | None = Permission.CUSTOMERS_READ

    async def run(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        customer_id = str(kwargs.get("customer_id") or "").strip()
        if not customer_id:
            return ToolResult(content="No account id was given.", summary="Empty lookup", ok=False)

        try:
            detail = await get_crm().get_customer(customer_id)
        except CrmUnavailableError as error:
            return failed(str(error))

        if detail is None:
            return ToolResult(
                content=f"No customer with account id {customer_id}.",
                summary=f"No such account {customer_id}",
                data={"customer": None},
            )

        return ToolResult(
            content=_render(detail),
            summary=f"Pulled {detail.customer.name} ({detail.customer.id})",
            data={"customer": _detail_card(detail)},
        )


def _render(detail: CustomerDetail) -> str:
    """The record as prose the model can quote from.

    Not JSON. A model handed JSON tends to reproduce its shape in the answer,
    and "the customer's equipment array contains two objects" is not something
    to read out to somebody on the phone.
    """
    customer = detail.customer
    parts = [
        f"{customer.name} — account {customer.id}",
        f"Address: {customer.address}",
        f"Phone: {customer.phone} · Customer since {customer.since}",
    ]
    if customer.notes:
        parts.append(f"Account notes: {customer.notes}")

    if detail.equipment:
        parts.append("\nInstalled equipment:")
        parts.extend(
            f"- {item.model}, serial {item.serial}, installed {item.installed_on}"
            + (f", warranty to {item.warranty_until}" if item.warranty_until else "")
            + (f" ({item.location})" if item.location else "")
            for item in detail.equipment
        )

    if detail.jobs:
        parts.append("\nRecent jobs, newest first:")
        for job in detail.jobs:
            parts.append(f"- {job.date} · {job.kind} · {job.summary} · {job.technician}")
            if job.notes:
                parts.append(f"  Technician's notes: {job.notes}")

    open_estimates = [estimate for estimate in detail.estimates if estimate.status == "open"]
    if open_estimates:
        parts.append("\nOpen estimates:")
        parts.extend(
            f"- {estimate.id} · {estimate.date} · {estimate.summary} · ${estimate.amount_usd:,.2f}"
            for estimate in open_estimates
        )

    if detail.outstanding_usd > 0:
        parts.append(f"\nOutstanding balance: ${detail.outstanding_usd:,.2f}")

    return "\n".join(parts)


def _customer_card(customer: Any) -> dict[str, Any]:
    return {
        "id": customer.id,
        "name": customer.name,
        "phone": customer.phone,
        "email": customer.email,
        "address": str(customer.address),
        "since": customer.since,
        "notes": customer.notes,
    }


def _detail_card(detail: CustomerDetail) -> dict[str, Any]:
    return {
        **_customer_card(detail.customer),
        "equipment": [
            {
                "id": item.id,
                "model": item.model,
                "serial": item.serial,
                "installedOn": item.installed_on,
                "location": item.location,
                "warrantyUntil": item.warranty_until,
            }
            for item in detail.equipment
        ],
        "jobs": [
            {
                "id": job.id,
                "date": job.date,
                "kind": job.kind,
                "summary": job.summary,
                "technician": job.technician,
                "notes": job.notes,
                "status": job.status,
            }
            for job in detail.jobs
        ],
        "estimates": [
            {
                "id": estimate.id,
                "date": estimate.date,
                "summary": estimate.summary,
                "amountUsd": estimate.amount_usd,
                "status": estimate.status,
            }
            for estimate in detail.estimates
        ],
        "invoices": [
            {
                "id": invoice.id,
                "date": invoice.date,
                "amountUsd": invoice.amount_usd,
                "balanceUsd": invoice.balance_usd,
                "status": invoice.status,
            }
            for invoice in detail.invoices
        ],
        "outstandingUsd": detail.outstanding_usd,
    }
