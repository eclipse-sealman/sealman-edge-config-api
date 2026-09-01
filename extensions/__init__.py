"""Dynamic API extension system: lets external micro-services register new
routes exposed through this API on three channels -

* ``public``   - user-facing, authorized via the existing JWT + RBAC/ABAC stack
* ``internal`` - service-to-service, authorized via a per-extension X-Internal-Key
* ``device``   - field-ingress (edge module -> micro-service), authorized via a
  per-device X-Device-Key

See ``extensions.registry`` for registration/validation business logic and
``extensions.runtime`` for how a persisted route becomes a live FastAPI route.
"""
from .setup import hydrate_all_routes, setup_extensions, start_side_servers

__all__ = ["setup_extensions", "hydrate_all_routes", "start_side_servers"]
