"""Central registry of KernelFeature instances, keyed by feature id."""
from .base import KernelFeature

_features: dict[str, KernelFeature] = {}


def register(feature: KernelFeature) -> KernelFeature:
    _features[feature.id] = feature
    return feature


def get(feature_id: str) -> KernelFeature | None:
    return _features.get(feature_id)


def all_features() -> list[KernelFeature]:
    return list(_features.values())


def by_category(category: str) -> list[KernelFeature]:
    return [f for f in _features.values() if f.category == category]


def clear():
    """Test helper — reset the registry between test cases."""
    _features.clear()
