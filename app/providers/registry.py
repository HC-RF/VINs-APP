"""Provider registry.

The single place that knows which providers exist. Swapping the data source
behind the application is an edit to :data:`PROVIDER_CLASSES` - no other module
imports a concrete provider.
"""

from __future__ import annotations

from app.config import Settings, get_settings
from app.providers.autodev import AutoDevProvider
from app.providers.base import VINDecoderProvider
from app.providers.local_vin import LocalVinProvider
from app.providers.nhtsa import NhtsaProvider
from app.providers.spec_catalog import SpecCatalogProvider
from app.schemas.vehicle import ProviderKind

#: Registration order is irrelevant; execution order comes from `priority`.
PROVIDER_CLASSES: tuple[type[VINDecoderProvider], ...] = (
    LocalVinProvider,
    NhtsaProvider,
    SpecCatalogProvider,
    AutoDevProvider,
)


class ProviderRegistry:
    """Owns provider instances and their lifecycle."""

    def __init__(self, settings: Settings | None = None,
                 classes: tuple[type[VINDecoderProvider], ...] | None = None) -> None:
        self.settings = settings or get_settings()
        self._providers: list[VINDecoderProvider] = [
            cls(self.settings) for cls in (classes or PROVIDER_CLASSES)
        ]
        self._providers.sort(key=lambda p: p.priority)

    # --- Access --------------------------------------------------------------

    @property
    def all(self) -> list[VINDecoderProvider]:
        return list(self._providers)

    def get(self, name: str) -> VINDecoderProvider | None:
        return next((p for p in self._providers if p.name == name), None)

    def available(self) -> list[VINDecoderProvider]:
        return [p for p in self._providers if p.is_available()]

    def free(self) -> list[VINDecoderProvider]:
        """Providers that cost nothing to call."""
        return [
            p for p in self.available()
            if p.kind in (ProviderKind.FREE, ProviderKind.LOCAL) or p.cost_per_call <= 0
        ]

    def commercial(self) -> list[VINDecoderProvider]:
        return [
            p for p in self.available()
            if p.kind is ProviderKind.COMMERCIAL and p.cost_per_call > 0
        ]

    def spec_catalog(self) -> SpecCatalogProvider | None:
        provider = self.get(SpecCatalogProvider.name)
        return provider if isinstance(provider, SpecCatalogProvider) and provider.is_available() else None

    def describe(self) -> list[dict]:
        """Safe-to-expose provider descriptions. Contains no credentials."""
        return [p.info().model_dump(mode="json") for p in self._providers]

    # --- Lifecycle -----------------------------------------------------------

    async def aclose(self) -> None:
        for provider in self._providers:
            await provider.aclose()


_registry: ProviderRegistry | None = None


def get_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry


async def close_registry() -> None:
    global _registry
    if _registry is not None:
        await _registry.aclose()
        _registry = None
