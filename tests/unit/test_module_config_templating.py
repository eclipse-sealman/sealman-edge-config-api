import pytest

from routers.module_config import templating as t


VARS = [
    {"name": "host", "type": "string"},
    {"name": "port", "type": "integer"},
    {"name": "interval", "type": "number", "required": False},
    {"name": "secure", "type": "boolean"},
    {"name": "mode", "type": "enum", "options": ["fast", "slow"]},
]
FILLED = {"host": "10.0.0.5", "port": 4840, "secure": False, "mode": "slow"}


def codes(findings, level=None):
    return [f["code"] for f in findings if level is None or f["level"] == level]


def test_whole_placeholder_keeps_type_and_interpolation_makes_text():
    body = {"url": "opc.tcp://${host}:${port}", "port": "${port}", "secure": "${secure}", "mode": "${mode}"}
    config, findings = t.render(body, VARS, FILLED)
    assert config == {"url": "opc.tcp://10.0.0.5:4840", "port": 4840, "secure": False, "mode": "slow"}
    assert codes(findings, "error") == []


def test_a_variable_without_value_is_missing_there_are_no_defaults():
    config, findings = t.render({"p": "${port}", "m": "${mode}"}, VARS, {"host": "h"})
    assert {f["path"]: f["code"] for f in findings if f["level"] == "error"} == {
        "/values/port": "missing-variable", "/values/secure": "missing-variable", "/values/mode": "missing-variable"}
    # only a file variable has a default; on another type it is refused instead of silently ignored
    config, findings = t.render({"p": "${port}"}, [{"name": "port", "type": "integer", "default": 4840}], {})
    assert config is None and [f["code"] for f in findings if f["level"] == "error"] == ["default-without-file"]


def test_values_are_coerced_from_form_strings():
    body = {"port": "${port}", "secure": "${secure}", "interval": "${interval}"}
    config, _ = t.render(body, VARS, {"host": "h", "port": "4841", "secure": "true", "interval": "2.5", "mode": "fast"})
    assert config == {"port": 4841, "secure": True, "interval": 2.5}


def test_optional_without_value_removes_key_and_list_item():
    body = {"interval": "${interval}", "items": ["a", "${interval}"], "text": "every ${interval}s"}
    config, findings = t.render(body, VARS, FILLED)
    assert config == {"items": ["a"], "text": "every s"}
    assert codes(findings, "error") == []


def test_missing_required_and_bad_values_are_errors_with_pointers():
    config, findings = t.render({"a": "${host}", "b": "${port}", "c": "${mode}"}, VARS, {"port": "x", "mode": "medium", "secure": True})
    errors = {f["path"]: f["code"] for f in findings if f["level"] == "error"}
    assert errors == {"/values/host": "missing-variable", "/values/port": "bad-value", "/values/mode": "bad-value"}


def test_device_fields_resolve_nested_and_report_missing():
    body = {"site": "${device.city}", "line": "${device.location.line}", "x": "${device.missing}"}
    config, findings = t.render(body, [], {}, {"city": "Aachen", "location": {"line": 3}})
    assert config == {"site": "Aachen", "line": 3}
    assert [(f["path"], f["code"]) for f in findings] == [("/x", "missing-device-field")]
    _, findings = t.render({"site": "${device.city}"}, [], {}, None)
    assert codes(findings) == ["missing-device-field"]


def test_escape_and_foreign_runtime_syntax_pass_through():
    config, findings = t.render({"a": "$${host}", "b": "{{$ENV}}", "c": "cost: $5"}, [], {})
    assert config == {"a": "${host}", "b": "{{$ENV}}", "c": "cost: $5"}
    assert findings == []


def test_lint_reports_template_problems():
    body = {"$secret": "x", "a": "${undeclared}", "b": "${bad name}", "${k}": 1}
    variables = [
        {"name": "1bad"}, {"name": "device"}, {"name": "dup"}, {"name": "dup"},
        {"name": "e", "type": "enum"},
    ]
    found = codes(t.lint(body, variables))
    for code in ("reserved-key", "unknown-variable", "bad-placeholder", "placeholder-in-key", "bad-variable-name",
                 "reserved-variable-name", "duplicate-variable", "enum-without-options", "unused-variable"):
        assert code in found
    assert codes(t.lint([], [])) == ["body-not-object"]
    assert codes(t.lint([], [], kind="block")) == []
    assert codes(t.lint("x", [], kind="block")) == ["body-not-object-or-array"]


def test_render_refuses_invalid_template_and_warns_on_unknown_values():
    config, findings = t.render({"a": "${nope}"}, [], {})
    assert config is None and "unknown-variable" in codes(findings)
    config, findings = t.render({"a": 1}, [], {"extra": 1})
    assert config == {"a": 1} and codes(findings, "warning") == ["unknown-value"]


def test_text_variables_are_multiline_strings_that_keep_their_type():
    variables = [{"name": "script", "type": "text"}]
    config, findings = t.render({"script": "${script}", "n": "line: ${script}"}, variables, {"script": "a\nb"})
    assert config == {"script": "a\nb", "n": "line: a\nb"}
    assert codes(findings, "error") == []
    _, findings = t.render({"script": "${script}"}, variables, {"script": 5})
    assert codes(findings, "error") == ["bad-value"]


def test_references():
    assert t.references({"a": "${x} ${device.city}", "b": ["${y}", "$${z}"]}) == {
        "variables": ["x", "y"], "device": ["city"], "endpoints": [], "endpoint_fields": []}


@pytest.mark.parametrize("decl,value,expected", [
    ({"type": "number"}, "5", 5), ({"type": "number"}, "5.5", 5.5), ({"type": "integer"}, 3.0, 3),
    ({"type": "enum", "options": [1, 2]}, "2", 2), ({"type": "boolean"}, "FALSE", False),
])
def test_coerce(decl, value, expected):
    assert t.coerce(value, decl) == expected


@pytest.mark.parametrize("decl,value", [
    ({"type": "string"}, 5), ({"type": "integer"}, True), ({"type": "integer"}, 1.5), ({"type": "boolean"}, "yes"),
])
def test_coerce_rejects(decl, value):
    with pytest.raises(ValueError):
        t.coerce(value, decl)


def test_endpoint_slots_fill_every_reference_from_the_endpoint_chosen_for_that_slot():
    body = {"a": "opc.tcp://${endpoint[plc1].ip}:4840", "sn": "${endpoint[plc1].machine_2_serial}",
            "b": "${endpoint[plc2].ip}", "c": "${endpoint.ip}"}
    assert t.references(body)["endpoints"] == ["endpoint", "plc1", "plc2"]
    endpoints = {"plc1": {"ip": "10.0.0.1", "machine_2_serial": "SN2"}, "plc2": {"ip": "10.0.0.2"},
                 "endpoint": {"ip": "10.0.0.3"}}
    config, findings = t.render(body, [], {}, None, endpoints)
    assert config == {"a": "opc.tcp://10.0.0.1:4840", "sn": "SN2", "b": "10.0.0.2", "c": "10.0.0.3"}
    assert findings == []


def test_endpoint_placeholders_need_a_chosen_endpoint_with_that_field_and_stay_as_is_without_device():
    body = {"a": "${endpoint[plc].ip}", "b": "${endpoint[plc].missing}", "c": "${endpoint[other].ip}"}
    _, findings = t.render(body, [], {}, None, {"plc": {"ip": "1.2.3.4"}, "other": None})
    assert {f["path"]: f["code"] for f in findings} == {"/b": "missing-endpoint-field", "/c": "missing-endpoint"}
    config, findings = t.render(body, [], {}, None, None)
    assert config == body and findings == []


def test_endpoint_role_fields_are_also_reachable_by_role_name():
    endpoint = {"endpoint_data": {"address": {"value": "10.0.0.9"}, "title": {"value": "PLC A"}}}
    assert t.endpoint_fields(endpoint, {"address": "ip", "title": "name"}) == {
        "address": "10.0.0.9", "title": "PLC A", "ip": "10.0.0.9", "name": "PLC A"}


CLIENT = {"body": {"endpoint": "opc.tcp://${endpoint[plc].ip}:4840", "name": "${machine_name}",
                   "items": {"$ref": "2"}},
          "variables": [{"name": "machine_name", "type": "string"}], "name": "Client"}
ITEMS = {"body": {"t": {"nodeId": "i=2258"}}, "variables": [], "name": "Items"}
BLOCKS = {1: CLIENT, 2: ITEMS}
SKID = {"clients": {"separatorA": {"$ref": "1"}, "separatorB": {"$ref": "1"}}, "id": "${puid}"}


def test_each_copy_of_a_block_gets_its_own_inputs_and_slots():
    i = t.inputs(SKID, [{"name": "puid", "type": "string"}], BLOCKS)
    assert [v["key"] for v in i["variables"]] == ["puid", "separatorA/machine_name", "separatorB/machine_name"]
    assert [e["key"] for e in i["endpoints"]] == ["separatorA/plc", "separatorB/plc"]
    assert [c["key"] for c in i["copies"]] == ["separatorA", "separatorA/items", "separatorB", "separatorB/items"]
    config, findings = t.render(SKID, [{"name": "puid", "type": "string"}],
        {"puid": "P1", "separatorA/machine_name": "A", "separatorB/machine_name": "B"}, None,
        {"separatorA/plc": {"ip": "10.0.0.1"}, "separatorB/plc": {"ip": "10.0.0.1"}}, BLOCKS)
    assert findings == []
    assert config["clients"]["separatorB"] == {"endpoint": "opc.tcp://10.0.0.1:4840", "name": "B", "items": {"t": {"nodeId": "i=2258"}}}


def test_missing_copy_input_is_reported_under_its_prefixed_key():
    _, findings = t.render(SKID, [{"name": "puid", "type": "string"}], {"puid": "P"}, None, {}, BLOCKS)
    assert {f["path"] for f in findings if f["code"] == "missing-variable"} == {"/values/separatorA/machine_name", "/values/separatorB/machine_name"}


def test_copy_names_from_arrays_and_colliding_keys_stay_unique():
    body = {"a": {"x": {"$ref": "2"}}, "b": {"x": {"$ref": "2"}}, "l": [{"$ref": "2"}]}
    assert [c["key"] for c in t.copies(body, BLOCKS)] == ["a.x", "b.x", "l[0]"]
    body = {"a": {"x": {"$ref": "2"}}, "b": {"x": {"$ref": "2"}}, "a.x": {"$ref": "2"}}
    assert [c["key"] for c in t.copies(body, BLOCKS)] == ["a.x", "b.x", "a.x#2"]


def test_ref_must_be_alone_and_well_formed():
    assert codes(t.lint({"a": {"$ref": "1", "x": 1}, "b": {"$ref": "one"}}, []), "error") == ["bad-ref", "bad-ref"]


def test_preview_without_device_keeps_endpoint_placeholders_inside_blocks():
    config, _ = t.render({"c": {"$ref": "1"}}, [], {"c/machine_name": "M"}, None, None, BLOCKS)
    assert config["c"]["endpoint"] == "opc.tcp://${endpoint[plc].ip}:4840"


def test_a_reference_to_an_unknown_block_is_an_error_at_its_path():
    _, findings = t.render({"c": [{"$ref": "9"}]}, [], {}, None, None, BLOCKS)
    assert [(f["path"], f["code"]) for f in findings] == [("/c/0", "missing-block")]


LIST_BLOCKS = {1: {"name": "Client", "body": {"endpoint": "opc.tcp://${endpoint[machine].ip}:4840", "n": "${machine_name}"},
                   "variables": [{"name": "machine_name"}]}}
LIST_BODY = {"clients": {"$list": ["1"]}}


def test_list_renders_entries_with_prefixed_inputs():
    config, findings = t.render(LIST_BODY, [], {"clients/sep1/machine_name": "A"}, None,
                                {"clients/sep1/machine": {"ip": "10.0.0.1"}}, LIST_BLOCKS,
                                lists={"clients": [{"name": "sep1", "block_id": 1}]})
    assert config == {"clients": {"sep1": {"endpoint": "opc.tcp://10.0.0.1:4840", "n": "A"}}}
    assert findings == []


def test_list_without_entries_renders_empty_object():
    config, findings = t.render(LIST_BODY, [], {}, None, {}, LIST_BLOCKS, lists=None)
    assert config == {"clients": {}}
    assert codes(findings, "error") == []


def test_check_entries_rules():
    entries = [{"name": "a", "block_id": 1}, {"name": "a", "block_id": 1}, {"name": "b/x", "block_id": 1},
               {"name": "c", "block_id": 9}]
    findings = t.check_entries(LIST_BODY, {"clients": entries, "other": [{"name": "a", "block_id": 1}]}, LIST_BLOCKS)
    assert [(f["path"], f["code"]) for f in findings] == [
        ("/lists/clients/1/name", "duplicate-entry"), ("/lists/clients/2/name", "bad-entry-name"),
        ("/lists/clients/3/block_id", "block-not-allowed"), ("/lists/other", "unknown-list")]


def test_inputs_lists_allowed_blocks_with_relative_keys():
    found = t.inputs(LIST_BODY, [], LIST_BLOCKS)["lists"][0]
    assert (found["key"], found["path"]) == ("clients", "/clients")
    assert [(b["block_id"], b["name"]) for b in found["blocks"]] == [(1, "Client")]
    block_inputs = found["blocks"][0]["inputs"]
    assert block_inputs["variables"][0]["key"] == "machine_name"
    assert block_inputs["endpoints"][0]["slot"] == "machine"


def test_lint_list_placement():
    assert codes(t.lint({"a": [{"$list": ["1"]}]}, []), "error") == ["list-in-array"]
    assert codes(t.lint({"a": {"$list": ["1"]}}, [], "block"), "error") == ["list-in-block"]
    assert codes(t.lint({"a": {"$list": []}}, []), "error") == ["bad-list"]


def test_service_placeholder_renders_chosen_service_port():
    body = {"e": "opc.tcp://${endpoint[machine].ip}:${service[machine].port}"}
    assert t.inputs(body, [], None)["endpoints"][0]["service_fields"] == ["port"]
    config, findings = t.render(body, [], {}, None, {"machine": {"ip": "10.0.0.1"}}, None,
                                services={"machine": {"port": 4840}})
    assert config == {"e": "opc.tcp://10.0.0.1:4840"} and findings == []
    _, findings = t.render(body, [], {}, None, {"machine": {"ip": "10.0.0.1"}}, None, services={})
    assert codes(findings, "error") == ["missing-service"]


def test_service_without_slot_name_uses_default_endpoint_slot():
    body = {"e": "${endpoint.ip}:${service.port}"}
    assert t.lint(body, []) == []
    config, _ = t.render(body, [], {}, None, {"endpoint": {"ip": "10.0.0.1"}}, None,
                         services={"endpoint": {"port": 4840}})
    assert config == {"e": "10.0.0.1:4840"}


def test_lint_service_without_endpoint():
    assert codes(t.lint({"e": "${service[m].port}"}, []), "error") == ["service-without-endpoint"]


def test_list_label():
    assert t.inputs({"s": {"$list": ["1"], "$label": "Slave"}}, [], BLOCKS)["lists"][0]["item_label"] == "Slave"
    assert codes(t.lint({"s": {"$list": ["1"], "x": 1}}, []), "error") == ["bad-list"]
    assert codes(t.lint({"s": {"$label": "Slave"}}, []), "error") == ["bad-list"]


def test_row_placeholder_is_valid_and_reported_per_slot():
    body = {"a": "${endpoint.equipment.identifier}", "b": "${endpoint[plc].equipment.crm_id}",
            "c": "${endpoint[plc].equipment.identifier}", "d": "${endpoint[plc].ip}"}
    assert t.lint(body, []) == []
    slots = {e["key"]: e for e in t.inputs(body, [], None)["endpoints"]}
    assert slots["endpoint"]["fields"] == [] and slots["endpoint"]["row_fields"] == {"equipment": ["identifier"]}
    assert slots["plc"]["fields"] == ["ip"] and slots["plc"]["row_fields"] == {"equipment": ["crm_id", "identifier"]}
    refs = t.references(body)
    assert refs["endpoints"] == ["endpoint", "plc"]
    assert "plc.equipment.crm_id" in refs["endpoint_fields"] and "plc.ip" in refs["endpoint_fields"]


def test_whole_list_reference_is_unchanged_next_to_a_row_reference():
    body = {"all": "${endpoint.equipment}", "one": "${endpoint.equipment.identifier}"}
    slot = t.inputs(body, [], None)["endpoints"][0]
    assert slot["fields"] == ["equipment"] and slot["row_fields"] == {"equipment": ["identifier"]}


def test_row_placeholder_satisfies_a_service_placeholder_of_the_same_slot():
    assert t.lint({"a": "${endpoint[m].equipment.identifier}", "b": "${service[m].port}"}, []) == []


def test_four_part_endpoint_reference_is_still_invalid():
    assert codes(t.lint({"a": "${endpoint.a.b.c}"}, []), "error") == ["bad-placeholder"]


ROWS = [{"id": "r1", "identifier": "10330", "crm_id": "21289", "port": 3},
        {"id": "r2", "identifier": "41234"}]
ROW_BODY = {"m": "${endpoint.equipment.identifier}", "c": "${endpoint.equipment.crm_id}"}


def render_rows(body, equipment, rows=None, **fields):
    return t.render(body, [], {}, None, {"endpoint": {"equipment": equipment, **fields}}, None, rows=rows)


def test_single_row_is_used_without_a_choice():
    config, findings = render_rows(ROW_BODY, ROWS[:1])
    assert config == {"m": "10330", "c": "21289"} and findings == []


def test_several_rows_need_a_choice_and_the_chosen_row_is_used():
    _, findings = render_rows(ROW_BODY, ROWS)
    assert codes(findings, "error") == ["missing-row", "missing-row"]
    assert findings[0]["message"] == "Choose a row of 'equipment' for 'endpoint'"
    config, findings = render_rows({"m": "${endpoint.equipment.identifier}"}, ROWS, {"endpoint": {"equipment": "r2"}})
    assert config == {"m": "41234"} and findings == []


def test_chosen_row_without_the_sub_field_is_an_error():
    config, findings = render_rows(ROW_BODY, ROWS, {"endpoint": {"equipment": "r2"}})
    assert config == {"m": "41234"} and codes(findings, "error") == ["missing-row-field"]
    assert findings[0]["message"] == "'endpoint.equipment.crm_id': the chosen row has no 'crm_id'"


@pytest.mark.parametrize("equipment", [None, [], "10.0.0.1", ["junk", 3]])
def test_field_without_rows_is_a_missing_endpoint_field(equipment):
    _, findings = render_rows({"m": "${endpoint.equipment.identifier}"}, equipment)
    assert codes(findings, "error") == ["missing-endpoint-field"]
    assert findings[0]["message"] == "'endpoint.equipment.identifier': the chosen endpoint has no rows in 'equipment'"


def test_row_value_keeps_its_type_as_a_whole_placeholder_and_is_text_inside_a_string():
    config, _ = render_rows({"p": "${endpoint.equipment.port}", "s": "port ${endpoint.equipment.port}"}, ROWS[:1])
    assert config == {"p": 3, "s": "port 3"}


def test_two_list_fields_of_one_slot_are_chosen_independently():
    body = {"a": "${endpoint.equipment.identifier}", "b": "${endpoint.sensors.tag}"}
    endpoint = {"equipment": ROWS, "sensors": [{"id": "s1", "tag": "T1"}]}
    config, findings = t.render(body, [], {}, None, {"endpoint": endpoint}, None, rows={"endpoint": {"equipment": "r1"}})
    assert config == {"a": "10330", "b": "T1"} and findings == []


def test_row_choice_inside_a_block_copy_is_keyed_by_the_copy():
    blocks = {1: {"body": {"m": "${endpoint[plc].equipment.identifier}"}, "variables": []}}
    config, findings = t.render({"sep": {"$ref": "1"}}, [], {}, None, {"sep/plc": {"equipment": ROWS}}, blocks,
                                rows={"sep/plc": {"equipment": "r2"}})
    assert config == {"sep": {"m": "41234"}} and findings == []


def test_row_placeholder_is_kept_without_device_context():
    config, _ = t.render({"m": "${endpoint.equipment.identifier}"}, [], {}, None, None, None)
    assert config == {"m": "${endpoint.equipment.identifier}"}


def test_lint_slot_declarations():
    body = {"a": "${endpoint.ip}", "b": "${endpoint[plc].ip}"}
    codes = lambda slots: [f["code"] for f in t.lint(body, [], "template", slots)]
    assert codes([{"name": "plc", "endpoint_types": ["plc"], "service_types": []}]) == []
    assert codes([{"name": "other", "endpoint_types": ["plc"], "service_types": []}]) == ["unknown-slot"]
    assert codes([{"name": "plc", "endpoint_types": [], "service_types": []}] * 2) == ["duplicate-slot"]
    assert codes([{"name": "plc", "endpoint_types": [], "service_types": ["opcua"]}]) == ["service-types-without-service"]


def test_inputs_carry_the_restriction_of_the_body_and_of_block_copies():
    block = {"name": "Client", "body": {"url": "${endpoint.ip}:${service.port}"}, "variables": [],
             "slots": [{"name": "endpoint", "endpoint_types": ["plc"], "service_types": ["opcua"]}]}
    body = {"own": "${endpoint[gw].ip}", "client": {"$ref": "7"}}
    found = {e["key"]: e for e in t.inputs(
        body, [], {7: block}, [{"name": "gw", "endpoint_types": ["gateway"], "service_types": []}])["endpoints"]}
    assert found["gw"]["endpoint_types"] == ["gateway"] and found["gw"]["service_types"] == []
    assert found["client/endpoint"]["endpoint_types"] == ["plc"]
    assert found["client/endpoint"]["service_types"] == ["opcua"]


def _ref(file_id):
    return {"$file": file_id, "name": "x", "kind": "certificate", "not_after": None, "uploaded_at": "2026-01-01T00:00:00Z"}


def _file(text, field, secret=False, name="client.crt", owner_kind="endpoint"):
    return t.RenderFile(text=text, secret=secret, name=name, sha256="ab" * 32, owner_kind=owner_kind,
                        owner_id="o1", field=field)


def _secret(path, field, name, owner_kind="endpoint"):
    return {"path": path, "owner_kind": owner_kind, "owner_id": "o1", "field": field, "name": name, "sha256": "ab" * 32}


def test_file_reference_renders_pem_and_reports_secret_paths():
    files = {"f1": _file("CERT PEM\nSECOND", "cert"), "f2": _file("KEY PEM", "key", True, "client.key"),
             "f3": _file("SVC KEY", "skey", True, "svc.key", "service"),
             "f4": _file("DEV KEY", "dkey", True, "dev.key", "device")}
    body = {"c": [{"serverCertificate": "${endpoint.cert}", "clientPrivateKey": "${endpoint.key}"}],
            "s": "${service[endpoint].skey}", "d": "${device.dkey}"}
    secrets = []
    config, findings = t.render(
        body, [], {}, {"dkey": _ref("f4")}, {"endpoint": {"cert": _ref("f1"), "key": _ref("f2")}},
        services={"endpoint": {"skey": _ref("f3")}}, files=files, secrets=secrets)
    assert findings == []
    assert config == {"c": [{"serverCertificate": "CERT PEM\nSECOND", "clientPrivateKey": "KEY PEM"}],
                      "s": "SVC KEY", "d": "DEV KEY"}
    assert secrets == [_secret("/c/0/clientPrivateKey", "key", "client.key"),
                       _secret("/s", "skey", "svc.key", "service"), _secret("/d", "dkey", "dev.key", "device")]


def test_file_placeholder_inside_text_is_an_error():
    files = {"f2": _file("TOP SECRET KEY", "key", True, "client.key")}
    config, findings = t.render({"a": {"b": "cert: ${endpoint.key}"}}, [], {}, None,
                                {"endpoint": {"key": _ref("f2")}}, files=files, secrets=[])
    assert [(f["path"], f["code"]) for f in findings] == [("/a/b", "file-not-whole-value")]
    assert "TOP SECRET" not in str(findings) and "TOP SECRET" not in str(config)


def test_file_reference_inside_a_structured_value_is_an_error():
    files = {"f1": _file("CERT PEM", "certs")}
    config, findings = t.render({"a": "${device.certs}"}, [], {}, {"certs": [_ref("f1")]}, files=files, secrets=[])
    assert [(f["path"], f["code"]) for f in findings] == [("/a", "file-not-whole-value")]
    assert "CERT PEM" not in str(config) and "$file" not in str(config)


def test_secret_paths_follow_the_final_array_positions():
    from routers.module_config.secrets import mask_config
    files = {"f2": _file("KEY PEM", "key", True, "client.key")}
    mask = "<secret: client.key #abababab>"
    opt = {"name": "opt", "type": "string", "required": False}
    ep = {"endpoint": {"key": _ref("f2")}}
    cases = [
        ({"c": ["${opt}", "${endpoint.key}", "other"]}, {"c": [mask, "other"]}),
        ({"c": ["${endpoint.nope}", {"k": "${endpoint.key}"}, "other"]}, {"c": [{"k": mask}, "other"]}),
        ({"c": [["${opt}", "${endpoint.key}"], ["x"]]}, {"c": [[mask], ["x"]]}),
        ({"c": ["${opt}", ["${opt}", {"k": "${endpoint.key}"}]]}, {"c": [[{"k": mask}]]}),
    ]
    for body, expected in cases:
        secrets = []
        config, findings = t.render(body, [opt], {}, None, ep, files=files, secrets=secrets)
        assert len(secrets) == 1, body
        assert mask_config(config, secrets) == expected, (body, secrets)

    # an omitted item before the secret inside a block used via $ref
    blocks = {1: {"body": {"arr": ["${opt}", "${endpoint.key}", "other"]}, "variables": [opt], "name": "Tls"}}
    secrets = []
    config, findings = t.render({"x": {"$ref": "1"}}, [], {}, None, {"x/endpoint": {"key": _ref("f2")}}, blocks,
                                files=files, secrets=secrets)
    assert findings == []
    assert [s["path"] for s in secrets] == ["/x/arr/0"]
    assert mask_config(config, secrets) == {"x": {"arr": [mask, "other"]}}


def test_unknown_file_id():
    config, findings = t.render({"a": "${endpoint.cert}"}, [], {}, None, {"endpoint": {"cert": _ref("gone")}}, files={})
    assert [(f["path"], f["code"]) for f in findings] == [("/a", "missing-file")]


def test_secret_path_inside_a_block_copy_is_the_final_pointer():
    blocks = {1: {"body": {"key": "${endpoint[plc].key}"}, "variables": [], "name": "Tls"}}
    files = {"f2": _file("KEY PEM", "key", True, "client.key")}
    secrets = []
    config, findings = t.render({"clients": {"sepA": {"$ref": "1"}}}, [], {}, None,
                                {"sepA/plc": {"key": _ref("f2")}}, blocks, files=files, secrets=secrets)
    assert findings == []
    assert config == {"clients": {"sepA": {"key": "KEY PEM"}}}
    assert [s["path"] for s in secrets] == ["/clients/sepA/key"]


FILE_REF = {"$file": "f1", "name": "root.crt", "kind": "certificate", "not_after": None,
            "uploaded_at": "2026-01-01T00:00:00Z"}


def _file_variable_findings(decl):
    return [(f["path"], f["code"]) for f in t.lint({"a": "${v}"}, [{"name": "v", **decl}]) if f["level"] == "error"]


def test_file_variable_declaration_rules():
    for decl in ({"type": "file"}, {"type": "file", "secret": True}, {"type": "file", "default": FILE_REF},
                 {"type": "file", "required": False}):
        assert _file_variable_findings(decl) == []
    assert _file_variable_findings({"type": "file", "options": ["a"]}) == [("/variables/0", "file-variable-options")]
    assert _file_variable_findings({"type": "string", "secret": True}) == [("/variables/0", "secret-without-file")]
    assert _file_variable_findings({"type": "string", "default": FILE_REF}) == [("/variables/0", "default-without-file")]
    assert _file_variable_findings({"type": "file", "default": "text"}) == [("/variables/0", "bad-default")]


def test_only_a_file_variable_takes_a_file_reference():
    assert t.coerce(FILE_REF, {"type": "file"}) == FILE_REF
    with pytest.raises(ValueError, match="expected an uploaded file"):
        t.coerce("text", {"type": "file"})
    config, findings = t.render({"a": "${v}"}, [{"name": "v", "type": "string"}], {"v": FILE_REF})
    assert "bad-value" in codes(findings, "error") and config in (None, {})
    declared = [{"name": "ca", "type": "file", "secret": True, "default": FILE_REF}]
    found = t.inputs({"a": "${ca}"}, declared, None)["variables"]
    assert found[0]["secret"] is True and found[0]["default"] == FILE_REF


def _default_file(text, secret=False, owner_id="7", field="ca", name="root.crt"):
    return t.RenderFile(text=text, secret=secret, name=name, sha256="cd" * 32, owner_kind="template",
                        owner_id=owner_id, field=field)


def test_file_variable_default_and_required():
    decl = {"name": "ca", "type": "file", "default": _ref("d1")}
    body = {"rootCertificates": ["${ca}"]}
    defaults = {("7", "ca"): _default_file("CA PEM")}
    config, findings = t.render(body, [decl], {}, default_files=defaults, template_id=7)
    assert findings == [] and config == {"rootCertificates": ["CA PEM"]}

    secrets = []
    config, findings = t.render(body, [decl], {}, default_files={("7", "ca"): _default_file("KEY PEM", secret=True)},
                                template_id=7, secrets=secrets)
    assert findings == [] and config == {"rootCertificates": ["KEY PEM"]}
    assert secrets == [{"path": "/rootCertificates/0", "owner_kind": "template", "owner_id": "7", "field": "ca",
                        "name": "root.crt", "sha256": "cd" * 32}]

    no_default = {"name": "ca", "type": "file"}
    config, findings = t.render(body, [no_default], {}, default_files=defaults, template_id=7)
    assert codes(findings) == ["missing-variable"]
    config, findings = t.render(body, [{**no_default, "required": False}], {}, default_files=defaults, template_id=7)
    assert findings == [] and config == {"rootCertificates": []}

    config, findings = t.render({"a": "prefix ${ca}"}, [decl], {}, default_files=defaults, template_id=7)
    assert [(f["path"], f["code"]) for f in findings] == [("/a", "file-not-whole-value")]
    assert "CA PEM" not in str(config)


def test_each_declaration_renders_its_own_owners_default_file():
    """The default resolves by (owner, variable name): the `$file` id of a declaration or of a value selects nothing."""
    shared = _ref("same-id")
    blocks = {3: {"body": {"ca": "${ca}"}, "variables": [{"name": "ca", "type": "file", "default": shared}],
                  "name": "Tls"}}
    defaults = {("7", "ca"): _default_file("TEMPLATE CA"), ("3", "ca"): _default_file("BLOCK CA", owner_id="3")}
    entity = {"same-id": _file("ENDPOINT CERT", "cert")}
    body = {"own": "${ca}", "a": {"$ref": "3"}, "b": {"$ref": "3"}}
    config, findings = t.render(body, [{"name": "ca", "type": "file", "default": shared}], {}, None, None, blocks,
                                files=entity, default_files=defaults, template_id=7)
    assert findings == []
    assert config == {"own": "TEMPLATE CA", "a": {"ca": "BLOCK CA"}, "b": {"ca": "BLOCK CA"}}

    # no file for the declaring owner (or no owner known): missing, never another owner's file of the same id
    config, findings = t.render(body, [{"name": "ca", "type": "file", "default": shared}], {}, None, None, blocks,
                                files=entity, default_files={("3", "ca"): defaults[("3", "ca")]})
    assert [(f["path"], f["code"]) for f in findings] == [("/own", "missing-file")]
    # a supplied value never renders a loaded file by its id
    config, findings = t.render({"own": "${v}"}, [{"name": "v", "type": "file"}], {"v": _ref("same-id")},
                                files=entity, default_files=defaults, template_id=7)
    assert [(f["path"], f["code"]) for f in findings] == [("/own", "missing-file")]
    assert "ENDPOINT CERT" not in str(config)
