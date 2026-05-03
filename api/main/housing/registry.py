"""
Provider registry.

Providers self-register on import (see providers/__init__.py).
The router calls registry.get() / registry.all() — it never imports providers directly.
"""
from __future__ import annotations

from typing import Optional
from .models import HousingProvider

_providers: dict[str, HousingProvider] = {}


def register(provider: HousingProvider) -> None:
    _providers[provider.provider_id] = provider


def get(provider_id: str) -> Optional[HousingProvider]:
    return _providers.get(provider_id)


def all_providers() -> list[HousingProvider]:
    return list(_providers.values())
