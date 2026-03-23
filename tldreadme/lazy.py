"""Small memoized loaders for optional heavy dependencies."""

from functools import lru_cache
from importlib import import_module


@lru_cache(maxsize=None)
def load_attr(module_name: str, attr_name: str):
    """Import a module attribute once and cache the result."""

    module = import_module(module_name)
    return getattr(module, attr_name)


@lru_cache(maxsize=None)
def load_module(module_name: str):
    """Import a module once and cache the result."""

    return import_module(module_name)
