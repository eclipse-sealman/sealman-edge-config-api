import pytest

from db.type_roles import (
    ENDPOINT_ROLE_TYPES,
    SERVICE_ROLE_TYPES,
    drop_roles_of_missing_fields,
    validate_type_mapping,
)
from exceptions import ValidationError

STRING = {"type": "string", "label": "Text"}
INTEGER = {"type": "integer", "label": "Number"}
BOOLEAN = {"type": "boolean", "label": "Flag"}


def _reject(fields, mapping, role_types) -> str:
    with pytest.raises(ValidationError) as exc_info:
        validate_type_mapping(fields, mapping, role_types)
    assert exc_info.value.status_code == 422
    return str(exc_info.value)


class TestValidateTypeMapping:
    def test_empty_mapping_on_empty_fields_is_valid(self):
        validate_type_mapping({}, {}, ENDPOINT_ROLE_TYPES)

    def test_ip_and_name_roles_on_string_fields_are_valid(self):
        validate_type_mapping(
            {"addr": STRING, "title": STRING},
            {"addr": "ip", "title": "name"},
            ENDPOINT_ROLE_TYPES,
        )

    def test_port_role_on_integer_field_is_valid(self):
        validate_type_mapping({"p": INTEGER}, {"p": "port"}, SERVICE_ROLE_TYPES)

    def test_unknown_role_is_rejected(self):
        message = _reject({"addr": STRING}, {"addr": "hostname"}, ENDPOINT_ROLE_TYPES)
        assert "Unknown role 'hostname'" in message

    def test_role_names_are_case_sensitive(self):
        message = _reject({"addr": STRING}, {"addr": "IP"}, ENDPOINT_ROLE_TYPES)
        assert "Unknown role 'IP'" in message

    def test_port_role_is_not_allowed_on_endpoint_types(self):
        _reject({"p": INTEGER}, {"p": "port"}, ENDPOINT_ROLE_TYPES)

    def test_ip_role_is_not_allowed_on_service_types(self):
        _reject({"addr": STRING}, {"addr": "ip"}, SERVICE_ROLE_TYPES)

    def test_role_on_two_fields_is_rejected(self):
        message = _reject(
            {"a": STRING, "b": STRING}, {"a": "ip", "b": "ip"}, ENDPOINT_ROLE_TYPES
        )
        assert "more than one field" in message

    def test_role_on_missing_field_is_rejected(self):
        message = _reject({"a": STRING}, {"nope": "ip"}, ENDPOINT_ROLE_TYPES)
        assert "not a field on this type" in message

    def test_ip_role_on_integer_field_is_rejected(self):
        message = _reject({"addr": INTEGER}, {"addr": "ip"}, ENDPOINT_ROLE_TYPES)
        assert "needs a field of type 'string'" in message

    def test_name_role_on_boolean_field_is_rejected(self):
        _reject({"flag": BOOLEAN}, {"flag": "name"}, ENDPOINT_ROLE_TYPES)

    def test_port_role_on_string_field_is_rejected(self):
        message = _reject({"p": STRING}, {"p": "port"}, SERVICE_ROLE_TYPES)
        assert "needs a field of type 'integer'" in message


class TestDropRolesOfMissingFields:
    def test_keeps_roles_of_present_fields(self):
        assert drop_roles_of_missing_fields(
            {"ip": "ip", "name": "name"}, {"ip": STRING, "name": STRING}
        ) == {"ip": "ip", "name": "name"}

    def test_drops_role_of_removed_field(self):
        assert drop_roles_of_missing_fields(
            {"ip": "ip", "name": "name"}, {"name": STRING}
        ) == {"name": "name"}

    def test_none_mapping_gives_empty_mapping(self):
        assert drop_roles_of_missing_fields(None, {"ip": STRING}) == {}
