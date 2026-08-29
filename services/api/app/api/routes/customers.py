"""Customer lookups, over HTTP.

The same connector the agent's tools use, exposed directly. Both paths exist on
purpose: office staff searching for an account want a list they can scan, not a
conversation — and an assistant that can only be reached through chat is a
worse tool than one that also has a screen.

Read-only, because the connector is. There is no write endpoint here because
there is nothing on `CrmConnector` to call.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import PrincipalDep, require
from app.api.schemas import (
    CustomerDetailOut,
    CustomerSummary,
    EquipmentOut,
    EstimateOut,
    InvoiceOut,
    JobOut,
)
from app.audit import audit
from app.auth.rbac import Permission
from app.connectors import CrmUnavailableError, Customer, CustomerDetail, get_crm
from app.core.errors import ApiError, NotFoundError

router = APIRouter(prefix="/customers", tags=["customers"])

read = [require(Permission.CUSTOMERS_READ)]


def summary_out(customer: Customer) -> CustomerSummary:
    """Public because the caller-lookup route builds the same shape.

    A screen pop that rendered a customer slightly differently from the search
    page would be two truths about one record, and the difference would show up
    as a bug report about the wrong one.
    """
    return CustomerSummary(
        id=customer.id,
        name=customer.name,
        phone=customer.phone,
        email=customer.email,
        address=str(customer.address),
        since=customer.since,
        notes=customer.notes,
    )


@router.get("", response_model=list[CustomerSummary], dependencies=read)
async def search_customers(
    principal: PrincipalDep,
    q: Annotated[str, Query(min_length=2, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=25)] = 10,
) -> list[CustomerSummary]:
    try:
        matches = await get_crm().search_customers(q, limit=limit)
    except CrmUnavailableError as error:
        # 502, not 500: the fault is upstream and the distinction is what tells
        # an operator whether to look at our logs or the vendor's status page.
        raise ApiError(str(error), detail={"upstream": "crm"}) from error

    await audit(
        "connector.crm.search",
        resource_type="query",
        detail={"query": q[:120], "results": len(matches)},
    )
    return [summary_out(customer) for customer in matches]


@router.get("/{customer_id}", response_model=CustomerDetailOut, dependencies=read)
async def get_customer(customer_id: str, principal: PrincipalDep) -> CustomerDetailOut:
    try:
        detail: CustomerDetail | None = await get_crm().get_customer(customer_id)
    except CrmUnavailableError as error:
        raise ApiError(str(error), detail={"upstream": "crm"}) from error

    if detail is None:
        raise NotFoundError("No such customer.")

    await audit(
        "connector.crm.get_customer",
        resource_type="customer",
        resource_id=customer_id,
        detail={"name": detail.customer.name},
    )

    return detail_out(detail)


def detail_out(detail: CustomerDetail) -> CustomerDetailOut:
    return CustomerDetailOut(
        customer=summary_out(detail.customer),
        equipment=[EquipmentOut.model_validate(item) for item in detail.equipment],
        jobs=[JobOut.model_validate(item) for item in detail.jobs],
        estimates=[EstimateOut.model_validate(item) for item in detail.estimates],
        invoices=[InvoiceOut.model_validate(item) for item in detail.invoices],
        outstanding_usd=detail.outstanding_usd,
    )
