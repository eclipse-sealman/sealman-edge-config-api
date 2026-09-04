from datetime import datetime, timezone
import ipaddress
import os
from typing import List
import re
from enum import Enum
import aiofiles
import logging
from azure.identity import WorkloadIdentityCredential
from exceptions import APIError, UnmatchedDependency, IoTBackendAPIError
from constants import SAS_TOKEN, LAN_EDGE_TEMPLATE_VERSIONS, NAT_TEMPLATE_SUPPORT, DEVICE_SERIAL_NUMBER_PATTERN

def validate_and_extract_serial_number(device: str) -> str:
    """Return True if device id matches expected pattern (eg-<serial>, fht-<serial>, e-<serial> or <serial>)."""
    match = re.match(DEVICE_SERIAL_NUMBER_PATTERN, device)
    if match is None:
        raise APIError(
            f"device id: {device} does not follow proper convention for smart ems",
            status_code=400,
        )
    return match.group(1)

class AuditTrail:

    log_file_nr = "0"
    log_file_size = 500 * 1024  # 500 kByte

    if not os.path.isdir(os.path.join(os.getcwd(), "logs")):
        os.mkdir(os.path.join(os.getcwd(), "logs"))

    for log_file in os.listdir(os.path.join(os.getcwd(), "logs")):
        m_obj = re.match(r"^audit_trail_(\d+)\.log$", log_file)
        if m_obj is not None:
            nr = m_obj.group(1)
            if int(nr) > int(log_file_nr):
                log_file_nr = nr

    # initial log file
    audit_log_path = os.path.join(
        os.getcwd(), "logs", f"audit_trail_{log_file_nr}.log")

    @classmethod
    def __make_new_log_file(cls):
        cls.log_file_nr = str(int(cls.log_file_nr) + 1)
        cls.audit_log_path = os.path.join(
            os.getcwd(), "logs", f"audit_trail_{cls.log_file_nr}.log")

    @classmethod
    async def log(cls, user, access, method=None):

        async with aiofiles.open(cls.audit_log_path, "a", encoding='utf-8') as file:
            method_call = ""
            if method is not None:
                method_call = f" [method: {method}]"
            await file.write(f"ts_utc: {datetime.now(timezone.utc)}; user: {user}; access: {access}{method_call}\n")

        if os.path.getsize(cls.audit_log_path) >= cls.log_file_size:
            cls.__make_new_log_file()

    @classmethod
    async def log_route(cls, route):
        async with aiofiles.open(cls.audit_log_path, "a", encoding='utf-8') as file:
            await file.write(f"{route} -> ")

        if os.path.getsize(cls.audit_log_path) >= cls.log_file_size:
            cls.__make_new_log_file()

    @classmethod
    async def get_log(cls):
        # read and return only the newest 2 log-file packets
        logs = []

        available_numbers = []
        for log_file in os.listdir(os.path.join(os.getcwd(), "logs")):
            m_obj = re.match(r"^audit_trail_(\d+)\.log$", log_file)
            if m_obj is not None:
                available_numbers.append(int(m_obj.group(1)))

        available_numbers.sort(reverse=True)
        selected_numbers = sorted(available_numbers[:2])
        
        for nr in selected_numbers:
            async with aiofiles.open(
                    os.path.join(os.getcwd(), "logs", f"audit_trail_{nr}.log"), "r", encoding='utf-8'
            ) as file:
                logs.extend(await file.readlines())
        return logs


class TemplateVariable:
    def __init__(self, name, value):
        self.name = name
        self.value = value

    def get(self):
        return {"name": self.name, "variableValue": self.value}


class TemplateVariableType(str, Enum):
    STRING = "string"
    JSON_OBJECT = "jsonObject"
    BOOLEAN = "boolean"
    FLOAT = "float"
    INTEGER = "integer"

class TemplateVariables:
    _template_variables: List[dict]

    def __init__(self):
        self._template_variables = []

    def clear(self):
        self._template_variables.clear()

    def add(self, name, value, variable_type: TemplateVariableType = TemplateVariableType.STRING):
        if isinstance(value, bool):
            value = str(value).lower()
        if value is None:
            value = "null"
        variable = {"name": str(name), "variableValue": str(value)}
        if variable_type != TemplateVariableType.STRING:
            variable["type"] = variable_type.value
        self._template_variables.append(variable)

    def add_variables(self, variables):
        if not variables:
            return
        for var in variables:
            raw_type = var.get("type", TemplateVariableType.STRING)
            if isinstance(raw_type, TemplateVariableType):
                variable_type = raw_type
            else:
                try:
                    variable_type = TemplateVariableType(str(raw_type))
                except ValueError:
                    variable_type = TemplateVariableType.STRING
            self.add(var.get("name"), var.get("variableValue"), variable_type)

    def add_from_dict(self, variables_dict):
        for name in variables_dict:
            self.add(name, variables_dict[name])

    def get(self):
        return self._template_variables


class DeviceInfo:
    _device_info: dict

    def __init__(self, device_info):
        self._device_info = device_info
        self._device_variables = TemplateVariables()
        self._device_variables.add_variables(device_info.get("variables"))

    def is_sems_enabled(self) -> bool:
        return self._device_info["enabled"]

    def is_template_supported(self) -> bool:
        return self._device_info["template"]["representation"] in LAN_EDGE_TEMPLATE_VERSIONS
    
    def check_eligibility(self):
        if not self.is_sems_enabled():
            raise UnmatchedDependency(
                "the device needs to be enabled in smart-ems for this function", status_code=400)
        if not self.is_template_supported():
            raise UnmatchedDependency(
                f"this function is only supported by devices using the <{LAN_EDGE_TEMPLATE_VERSIONS}>"
                f" template. Currently <{self._device_info['template']['representation']}> is in use",
                status_code=400)

    def check_nat_support(self):
        if self._device_info["template"]["representation"] not in NAT_TEMPLATE_SUPPORT:
            raise UnmatchedDependency(
                f"NAT functionalities are only supported by templates: {NAT_TEMPLATE_SUPPORT}."
                f"Currently <{self._device_info['template']['representation']}> is in use",
                status_code=400
            )

    def upsert_variable(self, name: str, value: str, variable_type: TemplateVariableType) -> None:
        variables = self.get_device_variables()
        for variable in variables:
            if variable.get("name") == name:
                variable["variableValue"] = value
                variable["variableType"] = variable_type.value
                return

        variables.append(
            {
                "name": name,
                "variableValue": value,
                "variableType": variable_type.value,
            }
        )

    def get_variable_value(self, name: str, default: str = "") -> str:
        for variable in self.get_device_variables():
            if variable.get("name") == name:
                value = variable.get("variableValue")
                return default if value is None else str(value)
        return default

    def get_device_variables(self):
        return self._device_info.get("variables", [])

    def get(self):
        return self._device_info


def is_ip_in_subnet(ip, network_ip, subnet):
    ip_addr = ipaddress.IPv4Address(ip)
    network = f"{network_ip}/{subnet}"
    subnet_network = ipaddress.IPv4Network(network, strict=False)
    return ip_addr in subnet_network


def get_iothub_auth_headers() -> dict:
    """Return Authorization headers for IoT Hub using SAS token if set, otherwise workload identity."""
    logger = logging.getLogger("EdgeConfigAPI")
    if SAS_TOKEN:
        return {"Authorization": f"{SAS_TOKEN}"}
    try:
        credential = WorkloadIdentityCredential()
        token = credential.get_token("https://iothubs.azure.net/.default").token
        return {"Authorization": f"Bearer {token}"}
    except Exception as exc:
        logger.error("No valid authentication method for IoT Hub. WorkloadIdentityCredential error: %s", exc)
        raise IoTBackendAPIError("No valid authentication method for IoT Hub.", status_code=500)
    
def normalize_database_url(url: str) -> str:
    url = url.strip()

    # --- PostgreSQL ---
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://")

    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://")

    if url.startswith("postgresql+psycopg://"):
        return url

    # --- SQLite ---
    if url.startswith("sqlite:///"):
        return url.replace("sqlite:///", "sqlite+aiosqlite:///")

    if url.startswith("sqlite+aiosqlite://"):
        return url

    raise ValueError(f"Unsupported DATABASE_URL: {url}")