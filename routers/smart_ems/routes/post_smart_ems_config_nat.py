import json
from helper import DeviceInfo, TemplateVariableType, is_ip_in_subnet
from smart_ems import SmartEMS, generate_resp_from_device_info
from exceptions import SEMSError
from ..schemas import NatConfig


def _validate_nat_rule(rule, ip_lan_3: str, subnet_lan_3: str, seen_ext_ips: set, seen_int_ips: set) -> None:
    """
    Validate a single NAT rule.

    Raises SEMSError if:
    - Internal IP is out of range for LAN3 subnet
    - External IP equals Internal IP
    - External or Internal IP is duplicated in the set of rules
    """

    ext_ip = rule.extIp
    int_ip = rule.intIp

    if ip_lan_3 and subnet_lan_3:
        if not is_ip_in_subnet(int_ip, ip_lan_3, subnet_lan_3):
            raise SEMSError(
                f"NAT ip address {int_ip} out of range for specified LAN address {ip_lan_3} and subnet {subnet_lan_3}",
                status_code=400
            )

    if ext_ip == int_ip:
        raise SEMSError("ExtIp and IntIp cannot be the same", status_code=400)

    if ext_ip in seen_ext_ips or int_ip in seen_int_ips:
        raise SEMSError("No repeated extIp or intIp between nat rules allowed", status_code=400)

    seen_ext_ips.add(ext_ip)
    seen_int_ips.add(int_ip)

    return None


def _get_lan3_network_from_settings(device_info: DeviceInfo) -> tuple[str, str]:
    """Extract LAN3 ip/subnet from lan_settings, returning empty values when unavailable."""
    lan_settings_raw = device_info.get_variable_value("lan_settings")
    if not lan_settings_raw:
        return "", ""

    try:
        lan_settings = json.loads(lan_settings_raw)
    except (TypeError, json.JSONDecodeError):
        return "", ""

    if not isinstance(lan_settings, dict):
        return "", ""

    lan3_settings = lan_settings.get("lan3")
    if not isinstance(lan3_settings, dict):
        return "", ""

    ip_values = lan3_settings.get("ip")
    subnet_values = lan3_settings.get("subnet")

    ip_lan_3 = str(ip_values[0]) if isinstance(ip_values, list) and ip_values else ""
    subnet_lan_3 = str(subnet_values[0]) if isinstance(subnet_values, list) and subnet_values else ""

    return ip_lan_3, subnet_lan_3


async def post_smart_ems_config_nat(device: str, config: NatConfig):
    desired_nat_rules = config.get_nat_rules()

    device_by_serial = await SmartEMS.get_device_by_serial(device)
    device_info = DeviceInfo(device_by_serial)

    device_info.check_eligibility()
    device_info.check_nat_support()
    device_id = device_info.get()["id"]

    mappings = []
    seen_ext_ips = set()
    seen_int_ips = set()

    ip_lan_3, subnet_lan_3 = _get_lan3_network_from_settings(device_info)

    # Iterate over desired NAT rules, validate each, and build mappings
    for rule in desired_nat_rules:
        _validate_nat_rule(rule, ip_lan_3, subnet_lan_3, seen_ext_ips, seen_int_ips)

        mappings.append(
            {
                "name": rule.name,
                "internalIp": str(rule.intIp),
                "externalIp": str(rule.extIp),
            }
        )

    nat_settings = {
        "enabled": config.nat_enabled,
        "mappings": mappings
    }

    device_info.upsert_variable(
        "nat_settings",
        json.dumps(nat_settings, separators=(",", ":")),
        TemplateVariableType.JSON_OBJECT,
    )

    body = generate_resp_from_device_info(device_info.get())
    body["reinstallConfig1"] = True

    if await SmartEMS.edit_device(device_id, body):
        return config
    else:
        raise SEMSError(f"could not update device {device}", status_code=400)
