from smart_ems import _with_variable_types


def test_marks_json_object_variables_only():
    body = {
        "name": "dev",
        "variables": [
            {"name": "static_compose_files", "variableValue": "{}"},
            {"name": "container_registry_auths", "variableValue": "{}"},
            {"name": "sems_device_url", "variableValue": "sems.localhost"},
        ],
    }

    result = _with_variable_types(body)

    types = {v["name"]: v.get("variableType") for v in result["variables"]}
    assert types == {
        "static_compose_files": "jsonObject",
        "container_registry_auths": "jsonObject",
        "sems_device_url": None,
    }
    assert "variableType" not in body["variables"][0]


def test_body_without_variables_is_unchanged():
    assert _with_variable_types({"name": "dev"}) == {"name": "dev"}
