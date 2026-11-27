import json

import pytest

from exceptions import APIError
from routers.platform_configuration.service import validate_template_variable


def _auths(count):
    return json.dumps({f"r{i}.example.com": {"auth": "abc"} for i in range(count)})


def test_accepts_up_to_four_registries():
    validate_template_variable("container_registry_auths", _auths(4))
    validate_template_variable("container_registry_auths", "{}")


def test_rejects_more_than_four_registries():
    with pytest.raises(APIError):
        validate_template_variable("container_registry_auths", _auths(5))


@pytest.mark.parametrize("name,value", [
    ("container_registry_auths", "not json"),
    ("container_registry_auths", '{"r": {"auth": 1}}'),
    ("static_compose_files", "[]"),
    ("static_compose_files", '{"stack": 1}'),
    ("static_compose_files", '{"bad\\"name": "x"}'),
    ("container_registry_auths", '{"": {"auth": null}}'),
])
def test_rejects_malformed_values(name, value):
    with pytest.raises(APIError):
        validate_template_variable(name, value)


def test_accepts_compose_stacks_and_ignores_other_variables():
    validate_template_variable("static_compose_files", json.dumps({"stack": "version: '3'"}))
    validate_template_variable("sems_device_url", "anything")
