"""Data provider registry — manages available data sources."""

from __future__ import annotations

from typing import Type

from quant_framework.data.provider import DataProvider


class DataProviderRegistry:
    """Registry of available data providers.

    Allows looking up provider classes by name and creating instances.
    """

    _providers: dict[str, Type[DataProvider]] = {}

    @classmethod
    def register(cls, name: str, provider_cls: Type[DataProvider]) -> None:
        """Register a provider class under a name.

        Args:
            name: Provider identifier (e.g. 'ths', 'tushare').
            provider_cls: Provider class (not instance).
        """
        cls._providers[name] = provider_cls

    @classmethod
    def get(cls, name: str) -> Type[DataProvider] | None:
        """Get a registered provider class by name.

        Returns None if not found.
        """
        return cls._providers.get(name)

    @classmethod
    def list_providers(cls) -> list[str]:
        """List all registered provider names."""
        return list(cls._providers.keys())

    @classmethod
    def create(cls, name: str, **kwargs) -> DataProvider | None:
        """Create a provider instance by name.

        Args:
            name: Provider identifier.
            **kwargs: Passed to the provider constructor.

        Returns:
            Provider instance, or None if not registered.
        """
        provider_cls = cls._providers.get(name)
        if provider_cls is None:
            return None
        return provider_cls(**kwargs)
