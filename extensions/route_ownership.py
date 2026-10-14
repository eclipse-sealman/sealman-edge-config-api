"""Exact claims for extension routes published into separate visibility apps."""

from typing import Any, Dict, Iterable, Optional, Set, Tuple

RouteClaim = Tuple[str, str, str]


def route_claim(route: Dict[str, Any]) -> RouteClaim:
    """Returns a route's visibility, normalized method, and exact manifest path."""
    return (
        route.get("visibility") or "public",
        (route.get("method") or "GET").upper(),
        route["path"],
    )


def duplicate_claim(routes: Iterable[Dict[str, Any]]) -> Optional[RouteClaim]:
    """Returns the first duplicate claim within one proposed manifest, if any."""
    seen: Set[RouteClaim] = set()
    for route in routes:
        claim = route_claim(route)
        if claim in seen:
            return claim
        seen.add(claim)
    return None


def conflict(
    proposed: Iterable[Dict[str, Any]], owners: Iterable[Tuple[str, Iterable[Dict[str, Any]]]]
) -> Optional[Tuple[str, RouteClaim]]:
    """Returns the first proposed claim already held by another named owner."""
    owner_by_claim: Dict[RouteClaim, str] = {}
    for owner, routes in owners:
        for route in routes:
            owner_by_claim.setdefault(route_claim(route), owner)

    for route in proposed:
        claim = route_claim(route)
        owner = owner_by_claim.get(claim)
        if owner is not None:
            return owner, claim
    return None
