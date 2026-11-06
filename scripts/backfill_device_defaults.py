"""Materialize missing device defaults before legacy cutover; dry-run unless --apply."""
import argparse
import asyncio

from sqlalchemy import select

from db.models.device_type import DeviceType
from db.session import AsyncSessionLocal, engine
from db.sqlalchemy.device_defaults import backfill_device_defaults, effective_type_fields


async def run(apply: bool) -> int:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DeviceType).order_by(DeviceType.type_id).with_for_update(read=True)
        )
        populated = await backfill_device_defaults(
            session, effective_type_fields(list(result.scalars().all()))
        )
        if apply:
            await session.commit()
        else:
            await session.rollback()
        return populated


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit changes to the configured POSTGRES_URL")
    args = parser.parse_args()
    try:
        populated = await run(args.apply)
        print(f"{'Applied' if args.apply else 'Dry run (rolled back)'}: {populated} missing field values.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
