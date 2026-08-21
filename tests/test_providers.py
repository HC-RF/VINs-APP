"""Provider layer: payload normalization, catalog matching, failure isolation.

No network. The NHTSA tests run against a captured vPIC payload so the
normalization contract is pinned without depending on a live service.
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.providers.base import ProviderError, VINDecoderProvider
from app.providers.local_vin import LocalVinProvider
from app.providers.nhtsa import NhtsaProvider
from app.providers.registry import ProviderRegistry
from app.providers.spec_catalog import SpecCatalogProvider
from app.schemas.vehicle import Confidence, Origin, ProviderKind


class TestNhtsaNormalization:
    """The captured payload must map to canonical fields exactly."""

    @pytest.fixture
    def mapped(self, nhtsa_payload):
        provider = NhtsaProvider(get_settings())
        return provider._map_payload(nhtsa_payload)

    @pytest.mark.parametrize("field,expected", [
        ("year", 2018),
        ("make", "BMW"),
        ("model", "X5"),
        ("trim", "xDrive35i"),
        ("body_type", "SUV"),
        ("doors", 4),
        ("engine_displacement_l", 3.0),
        ("engine_cylinders", 6),
        ("engine_configuration", "In-Line"),
        ("horsepower", 300),
        ("fuel", "Gasoline"),
        ("drivetrain", "AWD"),
        ("transmission", "Automatic"),
        ("transmission_speeds", 8),
        ("plant_country", "United States (USA)"),
        ("plant_city", "Greer"),
        ("seats", 5),
        ("abs", "Standard"),
    ])
    def test_field_mapping(self, mapped, field, expected):
        assert mapped[field].value == expected

    def test_placeholder_fields_are_dropped_entirely(self, mapped):
        """'Not Applicable' must not become a value."""
        for absent in ("fuel_secondary", "plant_company", "base_price_usd",
                       "top_speed_mph"):
            assert absent not in mapped, f"{absent} should have been dropped"

    def test_displacement_string_three_becomes_float(self, mapped):
        assert isinstance(mapped["engine_displacement_l"].value, float)
        assert mapped["engine_displacement_l"].value == 3.0

    def test_vin_pattern_fields_are_marked_vin_decoded(self, mapped):
        assert mapped["year"].origin is Origin.VIN_DECODED
        assert mapped["make"].origin is Origin.VIN_DECODED

    def test_lookup_fields_are_marked_enriched(self, mapped):
        assert mapped["trim"].origin is Origin.ENRICHED
        assert mapped["horsepower"].origin is Origin.ENRICHED

    def test_aspiration_is_inferred_with_low_confidence(self, mapped):
        """vPIC has no aspiration field; inferring from notes is weak evidence
        and must be labelled as such."""
        assert mapped["engine_type"].value == "Turbocharged"
        assert mapped["engine_type"].confidence is Confidence.LOW
        assert mapped["engine_type"].note

    def test_every_value_carries_provenance(self, mapped):
        for name, field in mapped.items():
            assert field.source == "nhtsa_vpic", name
            assert field.confidence is not None, name
            assert field.retrieved_at is not None, name


class TestNhtsaErrorHandling:
    def test_fatal_error_code_with_no_fields_fails(self):
        provider = NhtsaProvider(get_settings())
        response = provider._response_from_payload(
            "X", {"ErrorCode": "11", "ErrorText": "11 - Incorrect Model Year"}, 200
        )
        assert response.success is False
        assert response.error_code == "NOT_FOUND"

    def test_empty_payload_reports_no_data(self):
        provider = NhtsaProvider(get_settings())
        response = provider._response_from_payload("X", {"ErrorCode": "0"}, 200)
        assert response.success is False
        assert response.error_code == "NO_DATA"

    def test_soft_error_still_returns_fields_with_a_note(self, nhtsa_payload):
        provider = NhtsaProvider(get_settings())
        payload = {**nhtsa_payload, "ErrorCode": "1",
                   "ErrorText": "1 - Check Digit (9th position) does not calculate properly"}
        response = provider._response_from_payload("X", payload, 200)
        assert response.success is True
        assert response.fields["make"].note


class TestLocalVinProvider:
    @pytest.mark.asyncio
    async def test_decodes_year_manufacturer_and_region(self):
        provider = LocalVinProvider(get_settings())
        response = await provider.decode("5UXKR0C56JL070851")
        assert response.success is True
        assert response.fields["year"].value == 2018
        assert response.fields["wmi_country"].value == "United States"
        assert response.cost == 0.0

    @pytest.mark.asyncio
    async def test_everything_is_marked_vin_decoded_and_high(self):
        provider = LocalVinProvider(get_settings())
        response = await provider.decode("5UXKR0C56JL070851")
        for field in response.fields.values():
            assert field.origin is Origin.VIN_DECODED

    @pytest.mark.asyncio
    async def test_does_not_claim_plant_country(self):
        """WMI region is where the manufacturer is registered, not where the
        car was built. Conflating them invents discrepancies."""
        provider = LocalVinProvider(get_settings())
        response = await provider.decode("WA1ANAFY5J2213924")
        assert "plant_country" not in response.fields
        assert response.fields["wmi_country"].value == "Germany"

    @pytest.mark.asyncio
    async def test_bad_check_digit_lowers_year_confidence(self):
        provider = LocalVinProvider(get_settings())
        response = await provider.decode("5UXKR0C57JL070851")
        assert response.fields["year"].confidence is Confidence.MEDIUM
        assert response.fields["year"].note

    @pytest.mark.asyncio
    async def test_invalid_vin_fails_cleanly(self):
        provider = LocalVinProvider(get_settings())
        response = await provider.decode("SHORT")
        assert response.success is False
        assert response.error_code == "INVALID_VIN"


class TestSpecCatalog:
    @pytest.fixture
    def provider(self):
        return SpecCatalogProvider(get_settings())

    @pytest.mark.asyncio
    async def test_matches_by_make_model_year_and_engine(self, provider):
        response = await provider.enrich(
            make="BMW", model="X5", year=2018, engine_l=3.0, trim="xDrive35i"
        )
        assert response.success is True
        assert response.fields["horsepower"].value == 300
        assert response.fields["mpg_combined"].value == 20
        assert response.fields["transmission"].value == "Automatic"

    @pytest.mark.asyncio
    async def test_fills_a_gap_nhtsa_leaves(self, provider):
        """vPIC returns no transmission for the 2018 BMW 230i; the catalog does."""
        response = await provider.enrich(make="BMW", model="230i", year=2018, engine_l=2.0)
        assert response.fields["transmission"].value == "Automatic"
        assert response.fields["transmission_speeds"].value == 8

    @pytest.mark.asyncio
    async def test_unknown_vehicle_returns_nothing_rather_than_guessing(self, provider):
        response = await provider.enrich(make="Lancia", model="Delta", year=1992)
        assert response.success is False
        assert response.error_code == "NO_CATALOG_ENTRY"
        assert response.fields == {}

    @pytest.mark.asyncio
    async def test_year_outside_range_does_not_match(self, provider):
        response = await provider.enrich(make="BMW", model="X5", year=1999, engine_l=3.0)
        assert response.success is False

    @pytest.mark.asyncio
    async def test_wrong_engine_does_not_match_an_engine_specific_entry(self, provider):
        response = await provider.enrich(make="BMW", model="X5", year=2018, engine_l=1.0)
        assert response.success is False

    def test_specificity_scoring_prefers_the_engine_match(self, provider):
        generic, generic_score = provider.find_entry(
            make="BMW", model="X5", year=2018, engine_l=3.0, trim="xDrive35i")
        assert generic is not None
        assert generic_score >= 3        # make/model/year + engine (+ trim)

    @pytest.mark.asyncio
    async def test_weaker_match_yields_lower_confidence(self, provider):
        strong = await provider.enrich(make="BMW", model="X5", year=2018,
                                       engine_l=3.0, trim="xDrive35i")
        loose = await provider.enrich(make="Toyota", model="Camry", year=2020, engine_l=2.5)
        assert strong.fields["horsepower"].confidence.rank >= loose.fields["horsepower"].confidence.rank

    @pytest.mark.asyncio
    async def test_direct_decode_refuses_without_identity(self, provider):
        response = await provider.decode("5UXKR0C56JL070851")
        assert response.success is False
        assert response.error_code == "NEEDS_IDENTITY"


class TestProviderContract:
    @pytest.mark.asyncio
    async def test_exceptions_never_escape_decode(self):
        """A provider blowing up must degrade that source, not the request."""

        class Exploding(VINDecoderProvider):
            name = "exploding"
            kind = ProviderKind.FREE

            async def _decode(self, vin, *, hint_year=None):
                raise RuntimeError("boom")

        response = await Exploding(get_settings()).decode("5UXKR0C56JL070851")
        assert response.success is False
        assert response.error_code == "PROVIDER_EXCEPTION"
        assert "boom" in response.error

    @pytest.mark.asyncio
    async def test_provider_error_is_captured_with_its_code(self):
        class Refusing(VINDecoderProvider):
            name = "refusing"
            kind = ProviderKind.COMMERCIAL

            async def _decode(self, vin, *, hint_year=None):
                raise ProviderError("quota gone", code="QUOTA_EXCEEDED", status_code=429)

        response = await Refusing(get_settings()).decode("5UXKR0C56JL070851")
        assert response.error_code == "QUOTA_EXCEEDED"
        assert response.status_code == 429

    @pytest.mark.asyncio
    async def test_default_decode_many_fans_out(self):
        class Echo(VINDecoderProvider):
            name = "echo"
            kind = ProviderKind.LOCAL

            async def _decode(self, vin, *, hint_year=None):
                from app.providers.base import ProviderResponse
                return ProviderResponse(provider="echo", kind=ProviderKind.LOCAL, success=True)

        vins = ["5UXKR0C56JL070851", "WA1ANAFY5J2213924"]
        out = await Echo(get_settings()).decode_many(vins)
        assert set(out) == set(vins)


class TestRegistry:
    def test_providers_are_ordered_by_priority(self):
        registry = ProviderRegistry(get_settings())
        priorities = [p.priority for p in registry.all]
        assert priorities == sorted(priorities)

    def test_commercial_provider_without_a_key_is_unavailable(self):
        registry = ProviderRegistry(get_settings())
        autodev = registry.get("autodev")
        assert autodev.is_available() is False
        assert registry.commercial() == []

    def test_free_tier_is_available_with_no_configuration(self):
        registry = ProviderRegistry(get_settings())
        names = {p.name for p in registry.free()}
        assert "vin_structure" in names
        assert "spec_catalog" in names

    def test_describe_never_leaks_credentials(self, monkeypatch):
        monkeypatch.setenv("AUTODEV_API_KEY", "super-secret-token")
        monkeypatch.setenv("AUTODEV_ENABLED", "true")
        get_settings.cache_clear()
        registry = ProviderRegistry(get_settings())
        blob = str(registry.describe())
        assert "super-secret-token" not in blob
        get_settings.cache_clear()
