"""Shared test fixtures.

Every test runs against a throwaway SQLite file and offline providers. The
suite makes no network calls, so it is deterministic and runs in CI without
credentials.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolated_environment(tmp_path, monkeypatch):
    """Point every test at a fresh database and disable external providers."""
    db_file = tmp_path / "test.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("NHTSA_ENABLED", "false")
    monkeypatch.setenv("AUTODEV_ENABLED", "false")
    monkeypatch.setenv("AUTODEV_API_KEY", "")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("CACHE_ENABLED", "true")
    monkeypatch.setenv("ENVIRONMENT", "test")

    from app.config import get_settings
    from app.db import base as db_base
    from app.providers import registry as registry_module
    from app.services import decode_service as decode_module

    get_settings.cache_clear()
    db_base.reset_engine()
    registry_module._registry = None
    decode_module.reset_decode_service()

    yield

    db_base.reset_engine()
    get_settings.cache_clear()
    registry_module._registry = None
    decode_module.reset_decode_service()


@pytest.fixture
def settings():
    from app.config import get_settings
    return get_settings()


@pytest.fixture
def initialised_db(settings):
    from app.db.base import init_db
    init_db(settings)
    return settings


# --- Offline provider doubles ----------------------------------------------

@pytest.fixture
def stub_provider_factory():
    """Build a provider that returns canned fields, for merge/API tests."""
    from app.providers.base import ProviderResponse, VINDecoderProvider
    from app.schemas.vehicle import Confidence, FieldValue, Origin, ProviderKind

    def factory(
        name: str,
        fields: dict,
        *,
        kind: ProviderKind = ProviderKind.FREE,
        priority: int = 50,
        confidence: Confidence = Confidence.MEDIUM,
        origin: Origin = Origin.ENRICHED,
        cost: float = 0.0,
        fail: str | None = None,
    ):
        # `_decode` must live in the class body: ABCMeta freezes
        # __abstractmethods__ at class-creation time, so assigning the method
        # afterwards would leave the class abstract and uninstantiable.
        class _Stub(VINDecoderProvider):
            async def _decode(self, vin, *, hint_year=None):
                if fail:
                    return ProviderResponse(
                        provider=name, kind=kind, success=False,
                        error=fail, error_code="TEST_FAILURE",
                    )
                return ProviderResponse(
                    provider=name, kind=kind, success=True,
                    fields={
                        key: FieldValue(
                            value=value, source=name, source_kind=kind,
                            confidence=confidence, origin=origin, raw_value=value,
                        )
                        for key, value in fields.items()
                    },
                    cost=cost,
                )

        _Stub.name = name
        _Stub.label = name
        _Stub.kind = kind
        _Stub.priority = priority
        _Stub.cost_per_call = cost
        _Stub.provides = tuple(fields)
        _Stub.description = "Test double."
        return _Stub

    return factory


@pytest.fixture
def nhtsa_payload():
    """A real vPIC response, captured so provider tests need no network."""
    return {
        "VIN": "5UXKR0C56JL070851",
        "ModelYear": "2018",
        "Make": "BMW",
        "Model": "X5",
        "Trim": "xDrive35i",
        "Series": "",
        "BodyClass": "Sport Utility Vehicle [SUV]/Multipurpose Vehicle [MPV]",
        "VehicleType": "MULTIPURPOSE PASSENGER VEHICLE (MPV)",
        "Doors": "4",
        "DisplacementL": "3",
        "DisplacementCC": "3000.0",
        "EngineCylinders": "6",
        "EngineConfiguration": "In-Line",
        "EngineHP": "300",
        "FuelTypePrimary": "Gasoline",
        "FuelTypeSecondary": "Not Applicable",
        "DriveType": "AWD/All-Wheel Drive",
        "TransmissionStyle": "Automatic",
        "TransmissionSpeeds": "8",
        "Manufacturer": "BMW MANUFACTURER CORPORATION / BMW NORTH AMERICA",
        "PlantCountry": "UNITED STATES (USA)",
        "PlantCity": "GREER",
        "PlantState": "SOUTH CAROLINA",
        "PlantCompanyName": "",
        "GVWR": "Class 2E: 6,001 - 7,000 lb (2,722 - 3,175 kg)",
        "Seats": "5",
        "SeatRows": "2",
        "ABS": "Standard",
        "ESC": "Standard",
        "TPMS": "Direct",
        "AirBagLocFront": "1st Row (Driver and Passenger)",
        "BusType": "Not Applicable",
        "TrailerType": "Not Applicable",
        "MotorcycleChassisType": "Not Applicable",
        "BasePrice": "",
        "TopSpeedMPH": "",
        "OtherEngineInfo": "Turbocharged inline six",
        "ErrorCode": "0",
        "ErrorText": "0 - VIN decoded clean. Check Digit (9th position) is correct",
    }
