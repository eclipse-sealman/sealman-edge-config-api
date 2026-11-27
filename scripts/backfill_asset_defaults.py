"""Persist missing endpoint/service defaults before cutover; dry-run unless --apply."""
import argparse
import asyncio

from sqlalchemy import select

from db.merge import newly_defaulted_fields
from db.models.endpoint import Endpoint, EndpointType
from db.models.service import Service, ServiceType
from db.session import AsyncSessionLocal, engine
from db.sqlalchemy.asset_defaults import backfill_asset_defaults, validate_fields
from exceptions import APIError


async def run(apply: bool, entity: str = "all", type_id: str | None = None) -> dict[str, int]:
    if entity not in ("endpoints", "services", "all") or (entity == "all" and type_id is not None):
        raise ValueError("Select endpoints or services when filtering by type ID")
    counts = {}
    async with AsyncSessionLocal() as session:
        for name, type_model, model in (
            ("endpoints", EndpointType, Endpoint),
            ("services", ServiceType, Service),
        ):
            if entity not in ("all", name):
                continue
            stmt = select(type_model).order_by(type_model.type_id).with_for_update(read=True)
            if type_id is not None:
                stmt = stmt.where(type_model.type_id == type_id)
            types = (await session.execute(stmt)).scalars().all()
            if type_id is not None and not types:
                raise APIError(f"Unknown {name} type '{type_id}'", 404)
            fields = {}
            for definition in types:
                validate_fields(definition.fields or {})
                fields[definition.type_id] = newly_defaulted_fields({}, definition.fields or {})
            counts[name] = await backfill_asset_defaults(session, model, fields)
        if apply:
            await session.commit()
        else:
            await session.rollback()
    return counts


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entity", choices=("endpoints", "services", "all"), default="all")
    parser.add_argument("--type-id", help="Exact type ID; requires --entity endpoints or services")
    parser.add_argument("--apply", action="store_true", help="Commit changes to configured POSTGRES_URL")
    args = parser.parse_args()
    if args.type_id is not None and args.entity == "all":
        parser.error("--type-id requires --entity endpoints or services")
    try:
        counts = await run(args.apply, args.entity, args.type_id)
        for name, count in counts.items():
            print(f"{'Applied' if args.apply else 'Dry run (rolled back)'}: {count} missing {name} field/cell values.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
