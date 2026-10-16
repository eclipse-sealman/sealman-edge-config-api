"""Public surface of the extension system. Re-exports only."""

from .setup import hydrate_all_routes, setup_extensions, start_side_apps

__all__ = ["setup_extensions", "hydrate_all_routes", "start_side_apps"]
