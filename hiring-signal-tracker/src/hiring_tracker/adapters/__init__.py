"""Adapter registry."""

from __future__ import annotations

import httpx

from ..config import get_settings
from .ashby import AshbyAdapter
from .base import Adapter, AdapterError, FetchResult, NormalizedPosting
from .custom_careers import CustomCareersAdapter
from .greenhouse import GreenhouseAdapter
from .lever import LeverAdapter
from .workable import WorkableAdapter

_REGISTRY = {
    "greenhouse": GreenhouseAdapter,
    "lever": LeverAdapter,
    "ashby": AshbyAdapter,
    "workable": WorkableAdapter,
    "custom": CustomCareersAdapter,
}

__all__ = [
    "Adapter",
    "AdapterError",
    "FetchResult",
    "NormalizedPosting",
    "GreenhouseAdapter",
    "LeverAdapter",
    "AshbyAdapter",
    "WorkableAdapter",
    "CustomCareersAdapter",
    "get_adapter",
    "supported_ats_types",
]


def supported_ats_types() -> list[str]:
    return list(_REGISTRY)


def get_adapter(ats_type: str, client: httpx.Client | None = None):
    """Instantiate the adapter for an ATS type."""
    try:
        cls = _REGISTRY[ats_type]
    except KeyError:
        raise AdapterError(f"no adapter registered for ats_type={ats_type!r}")
    return cls(user_agent=get_settings().user_agent, client=client)
