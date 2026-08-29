"""The CRM connector.

The read-only guarantee is asserted structurally rather than by inspection: a
test walks the Service Fusion adapter looking for any method that issues a
non-GET request. That is the check that keeps holding after somebody adds a
feature in a hurry.
"""

from __future__ import annotations

import inspect

import pytest
from httpx import AsyncClient

from app.connectors import MockCrmConnector, ServiceFusionConnector
from app.connectors.base import CrmConnector
from app.lib.phone import normalise_phone
from tests.conftest import login


@pytest.fixture
def crm() -> MockCrmConnector:
    return MockCrmConnector()


async def test_the_mock_satisfies_the_protocol(crm: MockCrmConnector) -> None:
    assert isinstance(crm, CrmConnector)


async def test_search_matches_on_name(crm: MockCrmConnector) -> None:
    matches = await crm.search_customers("Raman")

    assert [customer.name for customer in matches] == ["Priya Raman"]


async def test_a_town_disambiguates_two_customers_with_one_surname(
    crm: MockCrmConnector,
) -> None:
    """Office staff say the town precisely because there are two. Scored rather
    than filtered, so the Portland Smith ranks above the one in Gorham instead
    of neither matching."""
    both = await crm.search_customers("John Smith")
    portland = await crm.search_customers("John Smith Portland")

    assert len(both) == 2
    assert portland[0].address.city == "Portland"


async def test_search_matches_on_a_phone_number_in_any_format(
    crm: MockCrmConnector,
) -> None:
    """A caller-ID webhook delivers +1207…, the CRM holds (207) 555-…, and an
    employee types 207-555-…. All three are the same customer."""
    for written in ("(207) 555-0142", "207-555-0142", "+1 207 555 0142", "2075550142"):
        found = await crm.find_by_phone(written)
        assert [customer.id for customer in found] == ["NG-1042"], written


async def test_an_unknown_number_finds_nobody(crm: MockCrmConnector) -> None:
    assert await crm.find_by_phone("(207) 555-9999") == []


async def test_a_record_carries_the_technicians_notes(crm: MockCrmConnector) -> None:
    """Frequently the most useful thing in the record and the hardest to find,
    which is most of the reason this connector exists."""
    detail = await crm.get_customer("NG-1042")

    assert detail is not None
    latest = detail.jobs[0]
    assert "warm" in latest.summary.lower()
    assert "aeration tank sits at basement ambient" in latest.notes


async def test_an_outstanding_balance_is_summed(crm: MockCrmConnector) -> None:
    detail = await crm.get_customer("NG-1042")

    assert detail is not None
    assert detail.outstanding_usd == 110.0


async def test_an_unknown_account_returns_nothing(crm: MockCrmConnector) -> None:
    assert await crm.get_customer("NG-0000") is None


def test_phone_normalisation_needs_ten_digits() -> None:
    assert normalise_phone("(207) 555-0142") == "2075550142"
    assert normalise_phone("+1 207 555 0142") == "2075550142"
    assert normalise_phone("555-0142") == ""


def test_the_service_fusion_adapter_issues_no_writes() -> None:
    """Read-only by construction, checked structurally.

    A generic `request(verb, …)` is the seam through which a write eventually
    appears, so the adapter has exactly one request method and this asserts it
    stays that way.
    """
    source = inspect.getsource(ServiceFusionConnector)

    for verb in (".post(", ".put(", ".patch(", ".delete("):
        # The OAuth token exchange is the one POST, and it is to the token
        # endpoint rather than to a record.
        occurrences = source.count(verb)
        if verb == ".post(":
            assert occurrences == 1, "the only POST should be the token exchange"
            assert "TOKEN_URL" in source
        else:
            assert occurrences == 0, f"the adapter must not {verb.strip('.(')}"


def test_the_service_fusion_adapter_refuses_to_start_without_credentials() -> None:
    with pytest.raises(ValueError):
        ServiceFusionConnector("", "")


def test_the_protocol_has_no_write_methods() -> None:
    """The guarantee the model actually depends on: there is nothing to call."""
    methods = {name for name in dir(CrmConnector) if not name.startswith("_")}

    assert not {name for name in methods if name.startswith(("create", "update", "delete"))}


class TestOverHttp:
    async def test_search_and_open_a_record(self, client: AsyncClient) -> None:
        await login(client, "office")

        results = await client.get("/customers", params={"q": "Raman"})
        assert results.status_code == 200
        assert results.json()[0]["name"] == "Priya Raman"

        detail = await client.get(f"/customers/{results.json()[0]['id']}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["equipment"]
        assert body["jobs"][0]["notes"]

    async def test_a_lookup_requires_a_session(self, client: AsyncClient) -> None:
        assert (await client.get("/customers", params={"q": "Raman"})).status_code == 401

    async def test_an_unknown_account_is_a_404(self, client: AsyncClient) -> None:
        await login(client, "office")

        assert (await client.get("/customers/NG-0000")).status_code == 404

    async def test_a_lookup_is_audited(self, client: AsyncClient) -> None:
        """Reading a customer record is exactly the activity the audit trail
        exists for."""
        from sqlalchemy import select

        from app.db.engine import get_sessionmaker
        from app.db.models import AuditEvent

        await login(client, "office")
        await client.get("/customers", params={"q": "Raman"})

        async with get_sessionmaker()() as db:
            actions = [
                row
                for row in (await db.execute(select(AuditEvent.action))).scalars()
                if row.startswith("connector.crm")
            ]

        assert "connector.crm.search" in actions
