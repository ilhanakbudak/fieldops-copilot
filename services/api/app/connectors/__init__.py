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
from app.connectors.inventory import (
    InventoryConnector,
    InventoryUnavailableError,
    Material,
    MaterialPricing,
    MockInventoryConnector,
    PlyConnector,
    StockLocation,
)
from app.connectors.mock import MockCrmConnector
from app.connectors.service_fusion import ServiceFusionConnector
from app.connectors.telephony import (
    InboundCall,
    MockTelephonyConnector,
    RingCentralConnector,
    TelephonyConnector,
    TelephonyUnavailableError,
)

__all__ = [
    "Address",
    "CrmConnector",
    "CrmUnavailableError",
    "Customer",
    "CustomerDetail",
    "Equipment",
    "Estimate",
    "InboundCall",
    "InventoryConnector",
    "InventoryUnavailableError",
    "Invoice",
    "Job",
    "Material",
    "MaterialPricing",
    "MockCrmConnector",
    "MockInventoryConnector",
    "MockTelephonyConnector",
    "PlyConnector",
    "RingCentralConnector",
    "ServiceFusionConnector",
    "StockLocation",
    "TelephonyConnector",
    "TelephonyUnavailableError",
    "build_crm",
    "build_inventory",
    "build_telephony",
    "get_crm",
    "get_inventory",
    "get_telephony",
    "reset_crm",
    "reset_inventory",
    "reset_telephony",
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


_inventory: InventoryConnector | None = None


def build_inventory(settings: Settings) -> InventoryConnector:
    if settings.inventory_provider == "ply":
        return PlyConnector(settings.ply_api_key or "")
    return MockInventoryConnector()


def get_inventory() -> InventoryConnector:
    global _inventory
    if _inventory is None:
        _inventory = build_inventory(get_settings())
    return _inventory


def reset_inventory() -> None:
    global _inventory
    _inventory = None


_telephony: TelephonyConnector | None = None


def build_telephony(settings: Settings) -> TelephonyConnector:
    if settings.telephony_provider == "ringcentral":
        return RingCentralConnector(settings.ringcentral_verification_token or "")
    return MockTelephonyConnector()


def get_telephony() -> TelephonyConnector:
    global _telephony
    if _telephony is None:
        _telephony = build_telephony(get_settings())
    return _telephony


def reset_telephony() -> None:
    global _telephony
    _telephony = None
