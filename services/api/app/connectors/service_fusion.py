"""Service Fusion.

**This adapter has never run against the real API**, and that is stated here
rather than discovered. Service Fusion issues credentials to its customers, not
to a public repository, so what can be demonstrated is the shape of the
integration — the OAuth flow, the translation into our own domain models, the
error handling, the read-only guarantee — with the mock behind the same protocol
for anything that has to actually execute.

Three things it does that a first draft usually does not:

**Only GETs.** `_get` is the only request method on the class. There is no
generic `request()` with a verb parameter, because that is the seam through
which a write eventually appears. A test asserts the class exposes no other
HTTP verb.

**Refreshes the token before it expires, not after a 401.** Retrying on 401
works and doubles the latency of every request that straddles an expiry, which
during a customer call is the moment it matters least.

**Translates, rather than passing through.** The model and the interface see our
`Customer`, never Service Fusion's JSON. When a vendor renames a field, this file
changes and nothing else does.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import httpx

from app.connectors.base import (
    Address,
    CrmUnavailableError,
    Customer,
    CustomerDetail,
    Equipment,
    Estimate,
    Invoice,
    Job,
)
from app.core.clock import utcnow

logger = logging.getLogger("fieldops.crm")

BASE_URL = "https://api.servicefusion.com/v1"
TOKEN_URL = "https://api.servicefusion.com/oauth/access_token"

# Refresh this far ahead of expiry, so a request never straddles one.
REFRESH_MARGIN = timedelta(seconds=60)


class ServiceFusionConnector:
    name = "service_fusion"

    def __init__(self, client_id: str, client_secret: str, *, base_url: str = BASE_URL) -> None:
        if not client_id or not client_secret:
            raise ValueError("Service Fusion requires a client id and secret")
        self._client_id = client_id
        self._client_secret = client_secret
        self._base_url = base_url.rstrip("/")
        self._token: str | None = None
        self._expires_at = utcnow()

    # --- HTTP -------------------------------------------------------------

    async def _access_token(self, client: httpx.AsyncClient) -> str:
        if self._token and utcnow() + REFRESH_MARGIN < self._expires_at:
            return self._token

        response = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        response.raise_for_status()
        payload = response.json()

        self._token = str(payload["access_token"])
        self._expires_at = utcnow() + timedelta(seconds=int(payload.get("expires_in", 3600)))
        return self._token

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """The only request method on this class. See the module docstring."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
                token = await self._access_token(client)
                response = await client.get(
                    f"{self._base_url}{path}",
                    params=params,
                    headers={"authorization": f"Bearer {token}"},
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 404:
                return None
            logger.warning("service fusion returned %s for %s", error.response.status_code, path)
            raise CrmUnavailableError("The CRM rejected the request.") from error
        except httpx.HTTPError as error:
            logger.warning("service fusion unreachable: %s", error)
            raise CrmUnavailableError("The CRM could not be reached.") from error

    # --- Protocol ---------------------------------------------------------

    async def search_customers(self, query: str, *, limit: int = 5) -> list[Customer]:
        payload = await self._get("/customers", {"search": query, "per-page": limit})
        return [_customer(item) for item in _items(payload)][:limit]

    async def get_customer(self, customer_id: str) -> CustomerDetail | None:
        payload = await self._get(f"/customers/{customer_id}")
        if not payload:
            return None

        # Four calls, issued in sequence for clarity rather than gathered. The
        # screen pop that needs this fast is the telephony path, which reads a
        # cached summary; this one is a deliberate lookup and a person is
        # already reading the first card by the time the rest arrive.
        jobs = _items(await self._get("/jobs", {"customer_id": customer_id, "per-page": 10}))
        equipment = _items(await self._get(f"/customers/{customer_id}/equipment"))
        estimates = _items(await self._get("/estimates", {"customer_id": customer_id}))
        invoices = _items(await self._get("/invoices", {"customer_id": customer_id}))

        return CustomerDetail(
            customer=_customer(payload),
            equipment=[_equipment(item) for item in equipment],
            jobs=[_job(item) for item in jobs],
            estimates=[_estimate(item) for item in estimates],
            invoices=[_invoice(item) for item in invoices],
        )

    async def find_by_phone(self, phone: str) -> Customer | None:
        matches = await self.search_customers(phone, limit=1)
        return matches[0] if matches else None


# --- Translation ----------------------------------------------------------
#
# Every `.get` has a default. A CRM record filled in by an office manager under
# time pressure is missing fields, and a KeyError during a customer call is the
# worst possible moment to discover which.


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        items = payload.get("items") or payload.get("data") or []
        return [item for item in items if isinstance(item, dict)]
    return [item for item in payload or [] if isinstance(item, dict)]


def _customer(item: dict[str, Any]) -> Customer:
    address = (item.get("addresses") or [{}])[0]
    contact = (item.get("contacts") or [{}])[0]
    return Customer(
        id=str(item.get("id", "")),
        name=item.get("customer_name") or contact.get("fname", "") or "Unknown",
        phone=(contact.get("phones") or [{}])[0].get("phone", ""),
        email=(contact.get("emails") or [{}])[0].get("email", ""),
        address=Address(
            line1=address.get("street_1", ""),
            city=address.get("city", ""),
            state=address.get("state_prov", ""),
            postcode=address.get("postal_code", ""),
        ),
        since=str(item.get("created_at", ""))[:10],
        notes=item.get("private_notes", "") or "",
    )


def _equipment(item: dict[str, Any]) -> Equipment:
    return Equipment(
        id=str(item.get("id", "")),
        model=item.get("equipment_name", "") or item.get("model", ""),
        serial=item.get("serial_number", ""),
        installed_on=str(item.get("install_date", ""))[:10],
        location=item.get("location", ""),
        warranty_until=str(item.get("warranty_expiration", ""))[:10] or None,
    )


def _job(item: dict[str, Any]) -> Job:
    return Job(
        id=str(item.get("id", "")),
        date=str(item.get("start_date") or item.get("created_at", ""))[:10],
        kind=item.get("category", "") or "Service call",
        summary=item.get("description", ""),
        technician=", ".join(item.get("techs", [])) if item.get("techs") else "",
        notes=item.get("job_notes", "") or "",
        status=item.get("status", "") or "unknown",
    )


def _estimate(item: dict[str, Any]) -> Estimate:
    return Estimate(
        id=str(item.get("id", "")),
        date=str(item.get("created_at", ""))[:10],
        summary=item.get("description", ""),
        amount_usd=float(item.get("total") or 0),
        status=item.get("status", "") or "unknown",
    )


def _invoice(item: dict[str, Any]) -> Invoice:
    total = float(item.get("total") or 0)
    paid = float(item.get("amount_paid") or 0)
    return Invoice(
        id=str(item.get("id", "")),
        date=str(item.get("created_at", ""))[:10],
        amount_usd=total,
        balance_usd=round(total - paid, 2),
        status=item.get("status", "") or "unknown",
    )
