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

__all__ = [
    "Address",
    "CrmConnector",
    "CrmUnavailableError",
    "Customer",
    "CustomerDetail",
    "Equipment",
    "Estimate",
    "InventoryConnector",
    "InventoryUnavailableError",
    "Invoice",
    "Job",
    "Material",
    "MaterialPricing",
    "MockCrmConnector",
    "MockInventoryConnector",
    "PlyConnector",
    "ServiceFusionConnector",
    "StockLocation",
    "build_crm",
    "build_inventory",
    "get_crm",
    "get_inventory",
    "reset_crm",
    "reset_inventory",
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
