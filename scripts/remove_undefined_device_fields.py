"""Remove device metadata fields absent from their effective device type."""
import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import Text, cast, select, update
from sqlalchemy.dialects.postgresql import ARRAY, array

from db.models.device import Device
from db.models.device_type import DeviceType
from db.session import AsyncSessionLocal, engine
from db.sqlalchemy.device_defaults import effective_type_fields


@dataclass
class CleanupResult:
    devices: int
    fields: dict[str, int]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    field_selection = parser.add_mutually_exclusive_group()
    field_selection.add_argument("--fields", nargs="+", help="Exact field names to remove only where undefined")
    field_selection.add_argument("--all-fields", action="store_true", help="Select all undefined fields")
    type_selection = parser.add_mutually_exclusive_group()
    type_selection.add_argument(
        "--deviceType", dest="device_type", metavar="TYPE_ID",
        help="Select devices assigned to this exact type ID, not its display label",
    )
    type_selection.add_argument("--all-device-types", action="store_true", help="Select every device type")
    parser.add_argument("--all", action="store_true", help="Shorthand for --all-fields --all-device-types")
    parser.add_argument("--apply", action="store_true", help="Commit changes to the configured POSTGRES_URL")
    args = parser.parse_args(argv)
    if args.all:
        if args.fields or args.all_fields or args.device_type is not None or args.all_device_types:
            parser.error("Use --all alone, not with field or device-type selectors.")
        args.all_fields = True
        args.all_device_types = True
    if not args.fields and not args.all_fields:
        parser.error("Select --fields or --all-fields (or use --all).")
    if args.device_type is None and not args.all_device_types:
        parser.error("Select --deviceType or --all-device-types (or use --all).")
    args.fields = args.fields or []
    if any(not name.strip() for name in args.fields):
        parser.error("Field names must not be empty or whitespace-only.")
    if args.device_type is not None and not args.device_type.strip():
        parser.error("Device type ID must not be empty or whitespace-only.")
    return args


async def run(
    fields: list[str], all_fields: bool, apply: bool,
    *, device_type: str | None = None, all_device_types: bool = False,
) -> CleanupResult:
    if bool(fields) == all_fields:
        raise ValueError("Select field names or all undefined fields, exclusively.")
    if (device_type is not None) == all_device_types:
        raise ValueError("Select a device type or all device types, exclusively.")
    if any(not name.strip() for name in fields):
        raise ValueError("Field names must not be empty or whitespace-only.")
    if device_type is not None and not device_type.strip():
        raise ValueError("Device type ID must not be empty or whitespace-only.")
    selected = set(fields)
    removed: Counter[str] = Counter()
    affected_devices = 0
    async with AsyncSessionLocal() as session:
        type_query = select(DeviceType).order_by(DeviceType.type_id).with_for_update(read=True)
        if device_type is not None:
            type_query = type_query.where(DeviceType.type_id.in_(["default", device_type]))
        types = list((await session.execute(type_query)).scalars().all())
        fields_by_type = effective_type_fields(types)
        if "default" not in fields_by_type:
            raise ValueError("Default device type is missing; refusing cleanup.")
        if device_type is not None and device_type not in fields_by_type:
            raise ValueError(f"Device type '{device_type}' not found; refusing cleanup.")

        device_query = (
            select(Device.device_id, Device.type_id, Device.device_data)
            .order_by(Device.device_id).with_for_update()
        )
        if device_type is not None:
            device_query = device_query.where(Device.type_id == device_type)
        rows = (await session.execute(device_query)).mappings().all()
        removals_by_type: dict[str, set[str]] = {}
        device_ids_by_type: dict[str, list[str]] = {}
        for row in rows:
            if row["type_id"] not in fields_by_type:
                raise ValueError(f"Unknown device type for device '{row['device_id']}'; refusing cleanup.")
            metadata = row["device_data"]
            if not isinstance(metadata, dict):
                raise ValueError(f"Invalid metadata for device '{row['device_id']}'; refusing cleanup.")
            undefined = metadata.keys() - fields_by_type[row["type_id"]].keys()
            to_remove = undefined if all_fields else undefined & selected
            if to_remove:
                removals_by_type.setdefault(row["type_id"], set()).update(to_remove)
                device_ids_by_type.setdefault(row["type_id"], []).append(row["device_id"])
                removed.update(to_remove)
                affected_devices += 1

        for type_id, names in removals_by_type.items():
            keys = sorted(names)
            await session.execute(
                update(Device)
                .where(
                    Device.type_id == type_id,
                    Device.device_id.in_(device_ids_by_type[type_id]),
                    Device.device_data.has_any(array(keys)),
                )
                .values(device_data=Device.device_data.op("-")(cast(keys, ARRAY(Text))))
                .execution_options(synchronize_session=False)
            )
        if apply:
            await session.commit()
        else:
            await session.rollback()
    return CleanupResult(devices=affected_devices, fields=dict(sorted(removed.items())))


async def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    print(f"Database: {engine.url}")
    selected_fields = "ALL undefined fields" if args.all_fields else repr(args.fields)
    selected_types = "ALL device types" if args.all_device_types else repr(args.device_type)
    selection = f"fields={selected_fields}; device types={selected_types}"
    if args.all_fields or args.all_device_types:
        mode = "APPLY" if args.apply else "DRY RUN"
        try:
            confirmation = input(
                f"{mode}: {selection} in the configured database.\n"
                "Type ALL to continue: "
            )
        except EOFError:
            raise SystemExit("Confirmation unavailable; cleanup cancelled.")
        if confirmation != "ALL":
            raise SystemExit("Confirmation did not match; cleanup cancelled.")
    try:
        result = await run(
            args.fields, args.all_fields, args.apply,
            device_type=args.device_type, all_device_types=args.all_device_types,
        )
        mode = "Applied" if args.apply else "Dry run (rolled back)"
        print(f"Selection: {selection}")
        print(f"{mode}: {sum(result.fields.values())} field values across {result.devices} devices.")
        for name, count in result.fields.items():
            print(f"  {name!r}: {count}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
