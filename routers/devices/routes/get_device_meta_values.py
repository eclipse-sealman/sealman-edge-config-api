import logging
from db.repos.device import DeviceRepository

logger = logging.getLogger("EdgeConfigAPI")


async def get_device_meta_values(
    repo: DeviceRepository,
) -> dict:
    devices = await repo.get_all_devices_raw()

    meta_values: dict[str, set] = {}

    for device in devices:
        device_meta = device.get("device_meta") or {}
        for key, value in device_meta.items():
            if value is None or str(value).strip() == "":
                continue
            if key not in meta_values:
                meta_values[key] = set()
            meta_values[key].add(str(value).strip())

    return {key: sorted(list(values)) for key, values in meta_values.items()}