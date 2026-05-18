from smart_ems import SmartEMS
from exceptions import UnmatchedDependency, SEMSError
from constants import LAN_EDGE_TEMPLATE_VERSIONS


async def get_smart_ems_lan(device: str):
    # read current device info
    device_info = await SmartEMS.get_device_by_serial(device)
    device_id = device_info["id"]
    device_template = device_info["template"]["representation"]
    enabled_flag = device_info["enabled"]

    # check enabled status
    if not enabled_flag:
        raise UnmatchedDependency("the device needs to be enabled in smart-ems for this function", status_code=400)

    # check for supported template
    if device_template not in LAN_EDGE_TEMPLATE_VERSIONS:
        raise UnmatchedDependency(f"this function is only supported by devices using the <{LAN_EDGE_TEMPLATE_VERSIONS}>"
                                  f" template. Currently <{device_template}> is in use", status_code=400)

    interface_config = {}
    config = await SmartEMS.device_config_download(device_id)
    if config.get("network") is not None:
        network = config.get("network")

        for lan_interface in network:

            # skip cellular lan interfaces
            if "cellular" in lan_interface:
                continue

            dhcp = network[lan_interface]["dhcp"]

            ip = network[lan_interface]["ip"]
            if isinstance(ip, list):
                ip = ip[0]
            if ip == "":
                ip = None

            subnet = network[lan_interface]["subnet"]
            if isinstance(subnet, list):
                subnet = subnet[0]
            if subnet == "":
                subnet = None

            gw = network[lan_interface]["gateway"]
            if isinstance(gw, list):
                gw = gw[0]
            if gw == "":
                gw = None

            dns = network[lan_interface].get("dns")
            if isinstance(dns, list):
                dns = dns[0]
            if dns == "":
                dns = None

            interface_config.update({lan_interface: {}})

            # The dhcp key should always be part of the response
            interface_config[lan_interface]["dhcp"] = dhcp

            if (ip is not None):
                interface_config[lan_interface]["ip"] = ip

            if (subnet is not None):
                interface_config[lan_interface]["subnet"] = subnet

            if (gw is not None):
                interface_config[lan_interface]["gw"] = gw
            
            if (dns is not None):
                interface_config[lan_interface]["dns"] = dns

        return {"interfaceConfig": interface_config}
    else:
        raise SEMSError(f"could not generated config from device template <{device_template}> -> "
                        f"Error: {config['errorMessage']}", status_code=400)
