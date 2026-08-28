"""Selecting a CRM."""

from __future__ import annotations

from app.config import Settings, get_settings
from app.connectors.base import (
    Address,
    CrmConnector,
    CrmUnavailableError,
    Customer,
    CustomerDetail,
    Equipment,
    Estimate,
    Invoice,
    Job,
)
from app.connectors.mock import MockCrmConnector
from app.connectors.service_fusion import ServiceFusionConnector

__all__ = [
    "Address",
    "CrmConnector",
    "CrmUnavailableError",
    "Customer",
    "CustomerDetail",
    "Equipment",
    "Estimate",
    "Invoice",
    "Job",
    "MockCrmConnector",
    "ServiceFusionConnector",
    "build_crm",
    "get_crm",
    "reset_crm",
]

_crm: CrmConnector | None = None


def build_crm(settings: Settings) -> CrmConnector:
    if settings.crm_provider == "service_fusion":
        return ServiceFusionConnector(
            settings.service_fusion_client_id or "",
            settings.service_fusion_client_secret or "",
        )
    return MockCrmConnector()


def get_crm() -> CrmConnector:
    global _crm
    if _crm is None:
        _crm = build_crm(get_settings())
    return _crm


def reset_crm() -> None:
    global _crm
    _crm = None
