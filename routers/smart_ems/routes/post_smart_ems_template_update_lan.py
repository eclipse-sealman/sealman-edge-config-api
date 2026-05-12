import json
import re
import logging
from exceptions import SEMSTemplateError
from constants import MAX_NAT_ROUTES
from smart_ems import SmartEMS

log = logging.getLogger("EdgeConfigAPI")


class TmplVars:
    _template_variables = []

    @classmethod
    def clear(cls):
        cls._template_variables.clear()

    @classmethod
    def add(cls, name, value):
        if isinstance(value, bool):
            if value:
                value = "true"
            else:
                value = "false"
        if value is None:
            value = "null"
        cls._template_variables.append({"name": str(name), "variableValue": str(value)})

    @classmethod
    def get(cls):
        return cls._template_variables


class NATCheck:
    _int_nat_pool = []
    _ext_nat_pool = []
    _ext_primary_ip = None
    _int_primary_ip = None

    @classmethod
    def clear(cls):
        cls._int_nat_pool.clear()
        cls._ext_nat_pool.clear()
        cls._int_primary_ip = None
        cls._ext_primary_ip = None

    @classmethod
    def add_int_nat(cls, ip):
        if ip in cls._int_nat_pool:
            raise SEMSTemplateError(f"ip foreseen for a NAT destination on LAN3 ({ip}) is already assigned on a "
                                    f"LAN3 NAT rule", 400)
        if ip in cls._ext_nat_pool:
            raise SEMSTemplateError(f"ip foreseen for a NAT destination on LAN3 ({ip}) is already assigned on a "
                                    f"LAN2 NAT rule", 400)
        if ip == cls._ext_primary_ip:
            raise SEMSTemplateError(f"ip foreseen for a NAT destination on LAN3 ({ip}) is already used by the primary "
                                    f"interface on LAN2", 400)
        if ip == cls._int_primary_ip:
            raise SEMSTemplateError(f"ip foreseen for a NAT destination on LAN3 ({ip}) is already used by the primary "
                                    f"interface on LAN3", 400)
        cls._int_nat_pool.append(ip)

    @classmethod
    def add_ext_nat(cls, ip):
        if ip in cls._int_nat_pool:
            raise SEMSTemplateError(f"ip foreseen for a NAT destination on LAN2 ({ip}) is already assigned on a "
                                    f"NAT3 rule", 400)
        if ip in cls._ext_nat_pool:
            raise SEMSTemplateError(f"ip foreseen for a NAT destination on LAN2 ({ip}) is already assigned on a "
                                    f"LAN2 NAT rule", 400)
        if ip == cls._ext_primary_ip:
            raise SEMSTemplateError(f"ip foreseen for a NAT destination on LAN2 ({ip}) is already used by the primary "
                                    f"interface on LAN2", 400)
        if ip == cls._int_primary_ip:
            raise SEMSTemplateError(f"ip foreseen for a NAT destination on LAN2 ({ip}) is already used by the primary "
                                    f"interface on LAN3", 400)
        cls._ext_nat_pool.append(ip)

    @classmethod
    def add_int_primary(cls, ip):
        if ip == cls._ext_primary_ip:
            raise SEMSTemplateError("ip foreseen for primary interface on LAN3 is equal to primary interface on LAN2", 400)
        cls._int_primary_ip = ip

    @classmethod
    def add_ext_primary(cls, ip):
        if ip == cls._int_primary_ip:
            raise SEMSTemplateError("ip foreseen for primary interface on LAN2 is equal to primary interface on LAN3", 400)
        cls._ext_primary_ip = ip


def check_ipv4(address, allow_none=False):
    if allow_none and address is None:
        return address
    elif re.match(r"^((25[0-5]|(2[0-4]|1\d|[1-9]|)\d)\.?\b){4}$", address) is None:
        raise SEMSTemplateError(f"invalid ipv4 address provided on static LAN interface: {address}", 400)
    return address


def check_subnet(subnet, allow_none=False):
    if allow_none and subnet is None:
        return subnet
    elif re.match(r"^([1-9]|[12]\d|3[0-2])$", str(subnet)) is None:
        raise SEMSTemplateError(f"invalid subnet provided on static LAN interface: {subnet}", 400)
    return subnet


def check_if_mode(mode):
    allowed_modes = ["dhcp", "static"]
    if mode not in allowed_modes:
        raise SEMSTemplateError(f"invalid interface mode <{mode}> provided; "
                                f"allowed modes: {allowed_modes}", 400)
    return mode


async def post_smart_ems_template_update_lan(device: str, lanif_conf):
    # lan2 + subnet + gateway + dns + min(lan2ex, lan3int, name) + !nat_enabled!
    # check conf
    # apply and schedule
    lanif_conf = json.loads(lanif_conf.json())

    # clear helper classes
    TmplVars.clear()
    NATCheck.clear()

    # get LAN3 interface config
    int_mode = check_if_mode(lanif_conf.get("intMode"))
    TmplVars.add("mode_lan_3", int_mode)

    if int_mode == "static":
        int_ip = check_ipv4(lanif_conf["intIp"])
        int_subnet = check_subnet(lanif_conf["intSubnet"])
        int_gateway = check_ipv4(lanif_conf.get("intGateway"), allow_none=True)
        int_dns = check_ipv4(lanif_conf.get("intDns"), allow_none=True)
        TmplVars.add("ip_lan_3", int_ip)
        TmplVars.add("subnet_lan_3", int_subnet)
        TmplVars.add("gw_lan_3", int_gateway)
        TmplVars.add("dns_lan_3", int_dns)

    # get LAN2 interface config
    ext_mode = check_if_mode(lanif_conf.get("extMode"))
    TmplVars.add("mode_lan_2", ext_mode)

    if ext_mode == "static":
        ext_ip = check_ipv4(lanif_conf["extIp"])
        ext_subnet = check_subnet(lanif_conf["extSubnet"])
        ext_gateway = check_ipv4(lanif_conf.get("extGateway"), allow_none=True)
        ext_dns = check_ipv4(lanif_conf.get("extDns"), allow_none=True)
        TmplVars.add("ip_lan_2", ext_ip)
        TmplVars.add("subnet_lan_2", ext_subnet)
        TmplVars.add("gw_lan_2", ext_gateway)
        TmplVars.add("dns_lan_2", ext_dns)

    # get NAT config flag
    nat_enabled = lanif_conf["natEnabled"]
    TmplVars.add("nat_enabled", nat_enabled)

    if nat_enabled:
        # check primary ip settings -> need to be static to apply NAT in a safe way
        NATCheck.add_ext_primary(check_ipv4(lanif_conf["extIp"]))
        NATCheck.add_int_primary(check_ipv4(lanif_conf["intIp"]))

        # plausibility checks: are interface modes are static?
        if ext_mode != "static" or int_mode != "static":
            raise SEMSTemplateError("to make use of NAT the LAN2 and LAN3 interface need be configured as static", 400)

        # check amount of NAT routes
        nats = lanif_conf.get("nat")

        if len(nats) == 0:
            raise SEMSTemplateError("NAT was enabled but not a single NAT configuration was done - make sure that at"
                                    "least one NAT-rule is configured", 400)
        if len(nats) > MAX_NAT_ROUTES:
            raise SEMSTemplateError(f"number of supported NATs ar exceeded: number of requested NATs <{len(nats)}>; "
                                    f"max number of supported NATs <{MAX_NAT_ROUTES}>", 400)

        # check NAT routes
        for idx, nat_info in enumerate(nats):
            NATCheck.add_ext_nat(check_ipv4(nat_info.get("extIp")))
            NATCheck.add_int_nat(check_ipv4(nat_info.get("intIp")))
            TmplVars.add(f"nat_machine_{idx}_name", nat_info.get("name"))
            TmplVars.add(f"nat_machine_{idx}_lan_2", check_ipv4(nat_info.get("extIp")))
            TmplVars.add(f"nat_machine_{idx}_lan_3", check_ipv4(nat_info.get("intIp")))

    # get current device info
    device_info = await SmartEMS.get_device_by_serial(device)
    device_id = device_info["id"]
    device_template = device_info["template"]["id"]
    device_serial = device_info["serialNumber"]
    device_description = device_info.get("description")
    update_fw_flag = device_info["updateFirmware"]
    enabled_flag = device_info["enabled"]
    access_tag_config_app = 27  # config-app access tag

    # composite body for template variables of device
    body = {
        "serialNumber": device_serial,
        "description": device_description,
        "template": device_template,
        "updateFirmware": update_fw_flag,
        "updateConfig": True,
        "getConfig": False,
        "accessTags": [
            access_tag_config_app
        ],
        "enabled": enabled_flag,
        "variables": TmplVars.get()
    }

    # update device variables
    await SmartEMS.edit_device(device_id, body)

    # check if config works
    resp = await SmartEMS.device_config_download(device_id)
    generated_config = resp.get("configGenerated")

    return {"request": lanif_conf,
            "deviceUpdate": body,
            "generatedConfig": generated_config}
