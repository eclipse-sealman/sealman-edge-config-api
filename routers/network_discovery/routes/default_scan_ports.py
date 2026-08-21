from typing import List

from db.repos.scan_ports import ScanPortsRepository


async def get_default_scan_ports(scan_ports_repo: ScanPortsRepository) -> List[int]:
    return await scan_ports_repo.get_default_ports()


async def add_default_scan_port(port: int, scan_ports_repo: ScanPortsRepository) -> List[int]:
    await scan_ports_repo.add_default_port(port)
    return await scan_ports_repo.get_default_ports()


async def remove_default_scan_port(port: int, scan_ports_repo: ScanPortsRepository) -> List[int]:
    await scan_ports_repo.remove_default_port(port)
    return await scan_ports_repo.get_default_ports()
