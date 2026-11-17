"""
Module configuration templates (global library) and template-based configuration of device
modules with an audited revision history.
"""
from typing import Any, Dict, List, Literal, Optional, Type

from fastapi import Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from auth import validate_jwt
from authorization.abac_permission_check import ABACDeviceListFilter, ABACPermissionCheck, _matches_scope
from authorization.permission_types import Device, ModuleConfig
from db.repos.device import DeviceRepository
from db.repos.endpoint import EndpointRepository
from db.repos.module_config import ModuleConfigRepository, UnknownScope
from db.repos.service import ServiceRepository
from db.repos.user import UserRepository
from db.session import get_repository
from db.sqlalchemy.module_config import module_matches
from helper import validate_iothub_id
from routers.base_api_router import BaseAPIRouter
from routers.module_config import blocks as block_loader, service, templating
from routers.module_config.config_schemas.network_discover_config_v1 import NetworkDiscoverModuleConfigV1
from routers.module_config.config_schemas.seal_module_opcua_client_config_v1 import OpcuaClientModuleConfigV1
from routers.module_config.template_schemas import (
    ApplyRequest, ApplyResponse, DeviceEndpointOption, DeviceServiceOption, ExampleDevice, LintRequest, LintResponse, TemplateGroupCreate, TemplateGroupOut, TemplateGroupUpdate,
    PreviewRequest, ReapplyRequest, RenderRequest, RenderResponse, RevisionDetail, RevisionList, TemplateCreate,
    TemplateDuplicate, TemplateOut, TemplateUpdate, TemplateVersionOut, TemplateVersionSummary, TemplateContent,
)

_repo = Depends(get_repository(ModuleConfigRepository))
_endpoint_repo = Depends(get_repository(EndpointRepository))
_service_repo = Depends(get_repository(ServiceRepository))
_template_read = Depends(ABACPermissionCheck(ModuleConfig.TEMPLATE_READ, device_path=None))
_template_write = Depends(ABACPermissionCheck(ModuleConfig.TEMPLATE_WRITE, device_path=None))

module_config_template_groups = BaseAPIRouter(prefix="/module-config-template-groups", tags=["Module Configuration Templates"])
module_config_templates = BaseAPIRouter(prefix="/module-config-templates", tags=["Module Configuration Templates"])
module_config_devices = BaseAPIRouter(tags=["Module Configuration Templates"])


def _variables(model: Any) -> List[Dict[str, Any]]:
    return [v.model_dump(exclude_none=True) for v in model.variables]


def _findings_error(message: str, findings: List[Dict[str, str]]) -> JSONResponse:
    # The app-wide HTTPException handler stringifies `detail`; findings must stay structured for the UI.
    return JSONResponse(status_code=422, content={"message": message, "findings": findings})


def _version_out(version: Dict[str, Any], blocks: templating.BlockVersions) -> Dict[str, Any]:
    """A stored version as people see it: references written by block name."""
    return {**version, "body": block_loader.show_names(version["body"], blocks),
            "references": templating.references(version["body"], blocks),
            "inputs": templating.inputs(version["body"], version["variables"], blocks)}


def _merge(name_findings: List[Dict[str, str]], findings: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Name findings first; a reference or list whose name did not resolve is not reported again as malformed."""
    unresolved = {f["path"] for f in name_findings}
    return name_findings + [f for f in findings
                            if not (f["code"] in ("bad-ref", "bad-list") and f["path"] in unresolved)]


def _lists(model: Any) -> Dict[str, List[Dict[str, Any]]]:
    return {key: [entry.model_dump() for entry in entries] for key, entries in model.lists.items()}


async def _checked(
    repo: ModuleConfigRepository, body: Any, variables: List[Dict[str, Any]], kind: str, group_id: Optional[int],
    self_id: Optional[int] = None, by_name: bool = True,
):
    """(stored body, blocks, findings) of a body: references written by name resolved (`by_name`, else the body
    is already stored), lint for its kind plus, with a group, the checks of its block references. Blocks is None
    without a group (references stay unresolved)."""
    name_findings: List[Dict[str, str]] = []
    if by_name:
        body, name_findings = await block_loader.resolve_names(repo, body)
    findings = _merge(name_findings, templating.lint(body, variables, kind))
    if group_id is None:
        return body, None, findings
    blocks, ref_findings = await block_loader.load_blocks(repo, body, group_id, self_id)
    return body, blocks, findings + ref_findings


async def _version_blocks(repo: ModuleConfigRepository, template: Dict[str, Any], version: Dict[str, Any]):
    """The blocks of a saved version (archived blocks included) and the findings of loading them. No group
    check: saving enforced it, and a saved version must render the same after its row moved groups."""
    return await block_loader.load_blocks(repo, version["body"], None, template["id"])


# Modules with a typed legacy write endpoint: rendered configs must pass the same schema.
KNOWN_MODULE_SCHEMAS: Dict[str, Type[BaseModel]] = {
    "seal-app-net-discover": NetworkDiscoverModuleConfigV1,
    "seal-module-opcua-client": OpcuaClientModuleConfigV1,
    # the module name deployed on the edge devices
    "seal-opcua-client": OpcuaClientModuleConfigV1,
}


def _schema_findings(module: str, config: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    schema = KNOWN_MODULE_SCHEMAS.get(module)
    if schema is None or config is None:
        return []
    try:
        schema.model_validate(config)
    except ValidationError as exc:
        return [
            templating.finding(templating.pointer(error["loc"]), "error", "schema", error["msg"])
            for error in exc.errors()
        ]
    return []


def available_for_device(template: Dict[str, Any], device_meta: Optional[Dict[str, Any]]) -> bool:
    """A template group without scopes is available for every device, otherwise the device's metadata must
    match at least one of its scopes."""
    scopes = template["scopes"]
    return not scopes or any(
        _matches_scope(scope["attr"], scope["access_rule"], device_meta or {}) for scope in scopes
    )


async def _device_endpoints(endpoint_repo: EndpointRepository, device: str) -> Dict[str, Dict[str, Any]]:
    """The device's endpoints by id, each with its flat fields (role fields `ip`/`name` included)."""
    mappings = {t["type_id"]: t["mapping"] for t in await endpoint_repo.get_endpoint_types()}
    return {
        e["endpoint_id"]: {**e, "fields": templating.endpoint_fields(e, mappings.get(e["type_id"], {}))}
        for e in await endpoint_repo.get_endpoints(device)
    }


def _slots(body: Any, blocks: Optional[templating.BlockVersions],
           lists: Optional[Dict[str, List[Dict[str, Any]]]]) -> List[Dict[str, Any]]:
    """The endpoint slots of a body (see `templating.inputs`) plus those of the chosen list entries, keyed
    `<list key>/<entry name>/<slot>`."""
    slots = templating.inputs(body, [], blocks)["endpoints"]
    for key, entries in (lists or {}).items():
        for entry in entries:
            block = (blocks or {}).get(entry["block_id"])
            if block:
                prefix = templating.COPY_SEP.join((key, entry["name"], ""))
                slots += [{**e, "key": prefix + e["key"]}
                          for e in templating.inputs(block["body"], [], blocks)["endpoints"]]
    return slots


async def _chosen_endpoints(
    endpoint_repo: EndpointRepository, device: str, body: Any, chosen: Dict[str, str],
    blocks: Optional[templating.BlockVersions], lists: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, Optional[Dict[str, Any]]]:
    """Slot -> fields of the endpoint chosen for it (None if none was chosen). Slots of block copies are
    keyed `<copy>/<slot>`, those of list entries `<list key>/<entry name>/<slot>`. A chosen endpoint must
    belong to the device."""
    slots = [e["key"] for e in _slots(body, blocks, lists)]
    if not slots:
        return {}
    endpoints = await _device_endpoints(endpoint_repo, device)
    unknown = [slot for slot in slots if chosen.get(slot) and chosen[slot] not in endpoints]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Endpoint chosen for '{unknown[0]}' is not on device '{device}'")
    return {slot: endpoints[chosen[slot]]["fields"] if chosen.get(slot) else None for slot in slots}


async def _service_mappings(service_repo: ServiceRepository) -> Dict[str, Dict[str, str]]:
    return {t["type_id"]: t["mapping"] for t in await service_repo.get_service_types()}


async def _chosen_services(
    service_repo: ServiceRepository, body: Any, endpoints: Dict[str, str], chosen: Dict[str, str],
    blocks: Optional[templating.BlockVersions], lists: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, Optional[Dict[str, Any]]]:
    """Slot -> fields of the service chosen for it (None if none was chosen), for every endpoint slot used by
    a `${service[...]}` placeholder. A chosen service must be one of the endpoint chosen for that slot."""
    slots = [e["key"] for e in _slots(body, blocks, lists) if e["service_fields"]]
    if not slots:
        return {}
    mappings = await _service_mappings(service_repo)
    out: Dict[str, Optional[Dict[str, Any]]] = {}
    for slot in slots:
        if not chosen.get(slot):
            out[slot] = None
            continue
        found = await service_repo.get_service(chosen[slot])
        if found is None or not endpoints.get(slot) or found["endpoint_id"] != endpoints[slot]:
            raise HTTPException(status_code=422, detail=f"Service chosen for '{slot}' is not on the chosen endpoint")
        out[slot] = templating.service_fields(found, mappings.get(found["type_id"], {}))
    return out


async def _group_or_404(repo: ModuleConfigRepository, group_id: int) -> Dict[str, Any]:
    group = await repo.get_group(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Template group not found")
    return group


async def _refuse_group_move(repo: ModuleConfigRepository, template: Dict[str, Any], group_id: int) -> None:
    """Block references stay within a template group: a row that uses blocks or is used by one cannot
    leave its group."""
    if group_id == template["group_id"]:
        return
    version = await repo.get_version(template["id"])
    if (version and block_loader.refs_in(version["body"])) or await block_loader.referenced_by_others(
            repo, template["id"], template["group_id"]):
        raise HTTPException(status_code=409, detail=f"Cannot move '{template['name']}' to another template group: "
                                                    f"it uses or is used by blocks")


async def _template_or_404(repo: ModuleConfigRepository, template_id: int) -> Dict[str, Any]:
    template = await repo.get_template(template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


# ------------------------------------------------------------------ groups


@module_config_template_groups.get("", response_model=List[TemplateGroupOut])
async def list_groups(
    module: Optional[str] = Query(None, description="only groups that list this module name"),
    repo: ModuleConfigRepository = _repo,
    _=_template_read,
):
    groups = await repo.list_groups()
    return [n for n in groups if module is None or module_matches(module, n["module_names"])]


@module_config_template_groups.post("", response_model=TemplateGroupOut, status_code=201)
async def create_group(req: TemplateGroupCreate, repo: ModuleConfigRepository = _repo, auth=_template_write):
    try:
        created = await repo.create_group(
            name=req.name, description=req.description, module_names=req.module_names,
            scope_ids=req.scope_ids, created_by=auth["user_name"],
        )
    except UnknownScope as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if created is None:
        raise HTTPException(status_code=409, detail=f"A template group named '{req.name}' already exists")
    return created


@module_config_template_groups.get("/{group_id}", response_model=TemplateGroupOut)
async def get_group(group_id: int, repo: ModuleConfigRepository = _repo, _=_template_read):
    return await _group_or_404(repo, group_id)


@module_config_template_groups.get("/{group_id}/example-devices", response_model=List[ExampleDevice])
async def list_example_devices(
    group_id: int,
    q: Optional[str] = Query(None, description="only devices whose id or metadata contains this text"),
    limit: int = Query(100, ge=1, le=500),
    repo: ModuleConfigRepository = _repo,
    device_repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
    visible=Depends(ABACDeviceListFilter(Device.READ)),
    _=_template_read,
):
    """Devices to preview the template group's templates with: those the caller may read that are also within the
    template group's scopes (every device if it has none)."""
    group = await _group_or_404(repo, group_id)
    needle = (q or "").strip().lower()

    def matches(device: Dict[str, Any]) -> bool:
        if not needle:
            return True
        text = [device["device_id"], *(str(v) for v in device["device_data"].values() if not isinstance(v, (dict, list)))]
        return any(needle in t.lower() for t in text)

    devices = [
        d for d in await device_repo.get_all_devices_raw()
        if visible["filter_device"](d["device_data"]) and available_for_device(group, d["device_data"]) and matches(d)
    ]
    return sorted(devices, key=lambda d: d["device_id"])[:limit]


@module_config_template_groups.patch("/{group_id}", response_model=TemplateGroupOut)
async def update_group(group_id: int, req: TemplateGroupUpdate, repo: ModuleConfigRepository = _repo,
                           _=_template_write):
    """Changes the modules or scopes the template group's templates apply to. Takes effect for the next render or apply."""
    try:
        updated = await repo.update_group(
            group_id, name=req.name, description=req.description, module_names=req.module_names,
            scope_ids=req.scope_ids,
        )
    except UnknownScope as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ValueError:
        raise HTTPException(status_code=409, detail=f"A template group named '{req.name}' already exists")
    if updated is None:
        raise HTTPException(status_code=404, detail="Template group not found")
    return updated


@module_config_template_groups.delete("/{group_id}", status_code=204)
async def delete_group(group_id: int, repo: ModuleConfigRepository = _repo, _=_template_write):
    """Only an empty template group can be deleted; move or archive its templates first."""
    outcome = await repo.delete_group(group_id)
    if outcome == "not_found":
        raise HTTPException(status_code=404, detail="Template group not found")
    if outcome == "in_use":
        raise HTTPException(status_code=409, detail="The template group still contains templates")


# ------------------------------------------------------------------ template library


@module_config_templates.get("", response_model=List[TemplateOut])
async def list_templates(
    module: Optional[str] = Query(None, description="only templates applicable to this module name"),
    group_id: Optional[int] = Query(None, description="only templates of this group"),
    kind: Optional[Literal["template", "block"]] = Query(None, description="only templates or only blocks"),
    include_archived: bool = False,
    repo: ModuleConfigRepository = _repo,
    _=_template_read,
):
    return await repo.list_templates(module=module, include_archived=include_archived, group_id=group_id, kind=kind)


@module_config_templates.post("/lint", response_model=LintResponse)
async def lint_template(req: LintRequest, repo: ModuleConfigRepository = _repo, _=_template_read):
    """Checks an unsaved template or block; with `group_id` also its block references."""
    body, blocks, findings = await _checked(repo, req.body, _variables(req), req.kind, req.group_id)
    return {"ok": not templating.has_errors(findings), "findings": findings,
            "references": templating.references(body, blocks),
            "inputs": templating.inputs(body, _variables(req), blocks)}


@module_config_templates.post("/preview", response_model=RenderResponse)
async def preview_template(
    req: PreviewRequest,
    repo: ModuleConfigRepository = _repo,
    device_repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
    auth_context: dict = Depends(validate_jwt),
    user_repo: UserRepository = Depends(get_repository(UserRepository)),
    endpoint_repo: EndpointRepository = _endpoint_repo,
    service_repo: ServiceRepository = _service_repo,
    _=_template_read,
):
    """Renders an unsaved template with sample values and either sample device metadata (`device`) or the
    metadata of a real example device (`device_id`, which must be in the caller's scope and in the template group's)."""
    device_meta = req.device
    if req.device_id is not None:
        if req.group_id is None:
            raise HTTPException(status_code=422, detail="group_id is required with device_id")
        group = await _group_or_404(repo, req.group_id)
        visible = await ABACDeviceListFilter(Device.READ)(auth_context, user_repo)
        validate_iothub_id(req.device_id)
        device_meta = await device_repo.get_device_meta_raw(req.device_id)
        if device_meta is None or not visible["filter_device"](device_meta):
            raise HTTPException(status_code=404, detail="Device not found")
        if not available_for_device(group, device_meta):
            raise HTTPException(
                status_code=403,
                detail=f"Template group '{group['name']}' is not available for device '{req.device_id}' (outside its scopes)")
    body, name_findings = await block_loader.resolve_names(repo, req.body)
    blocks, ref_findings = (None, [])
    if req.group_id is not None:
        blocks, ref_findings = await block_loader.load_blocks(repo, body, req.group_id)
    lists = _lists(req)
    entry_findings = templating.check_entries(body, lists, blocks)
    if templating.has_errors(entry_findings):
        return _findings_error("The list entries have errors", entry_findings)
    endpoints, services = None, None
    if req.device_id is not None and req.endpoints is not None:
        endpoints = await _chosen_endpoints(endpoint_repo, req.device_id, body, req.endpoints, blocks, lists)
        services = await _chosen_services(service_repo, body, req.endpoints, req.services, blocks, lists)
    config, findings = templating.render(body, _variables(req), req.values, device_meta, endpoints, blocks, req.kind,
                                         lists, services)
    findings = _merge(name_findings, ref_findings + findings)
    return {"ok": config is not None and not templating.has_errors(findings), "config": config, "findings": findings}


@module_config_templates.post("", response_model=TemplateOut, status_code=201)
async def create_template(req: TemplateCreate, repo: ModuleConfigRepository = _repo, auth=_template_write):
    await _group_or_404(repo, req.group_id)
    body, _, findings = await _checked(repo, req.body, _variables(req), req.kind, req.group_id)
    if templating.has_errors(findings):
        return _findings_error("The template has errors", findings)
    created = await repo.create_template(
        name=req.name, description=req.description, group_id=req.group_id,
        body=body, variables=_variables(req), comment=req.comment,
        created_by=auth["user_name"], kind=req.kind,
    )
    if created is None:
        raise HTTPException(status_code=409, detail=f"A template named '{req.name}' already exists")
    return created


@module_config_templates.get("/{template_id}", response_model=TemplateOut)
async def get_template(template_id: int, repo: ModuleConfigRepository = _repo, _=_template_read):
    return await _template_or_404(repo, template_id)


@module_config_templates.patch("/{template_id}", response_model=TemplateOut, response_model_exclude_none=True)
async def update_template(template_id: int, req: TemplateUpdate, repo: ModuleConfigRepository = _repo,
                          _=_template_write):
    """Changes metadata (name, description, group) or archives/restores. Content changes
    create a new version instead."""
    if req.group_id is not None:
        await _group_or_404(repo, req.group_id)
        await _refuse_group_move(repo, await _template_or_404(repo, template_id), req.group_id)
    try:
        updated = await repo.update_template(
            template_id, name=req.name, description=req.description,
            group_id=req.group_id, archived=req.archived,
        )
    except ValueError:
        raise HTTPException(status_code=409, detail=f"A template named '{req.name}' already exists")
    if updated is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return updated


@module_config_templates.delete("/{template_id}", response_model=TemplateOut)
async def archive_template(template_id: int, repo: ModuleConfigRepository = _repo, _=_template_write):
    """Archives the template. Versions and the revisions that used them are kept."""
    updated = await repo.update_template(template_id, archived=True)
    if updated is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return updated


@module_config_templates.get("/{template_id}/versions", response_model=List[TemplateVersionSummary])
async def list_template_versions(template_id: int, repo: ModuleConfigRepository = _repo, _=_template_read):
    await _template_or_404(repo, template_id)
    return await repo.list_versions(template_id)


@module_config_templates.get("/{template_id}/versions/{version}", response_model=TemplateVersionOut)
async def get_template_version(template_id: int, version: str, repo: ModuleConfigRepository = _repo,
                               _=_template_read):
    """`version` is a number or `latest`."""
    if version != "latest" and not version.isdigit():
        raise HTTPException(status_code=400, detail="version must be a number or 'latest'")
    template = await _template_or_404(repo, template_id)
    found = await repo.get_version(template_id, None if version == "latest" else int(version))
    if found is None:
        raise HTTPException(status_code=404, detail="Template version not found")
    blocks, _ = await _version_blocks(repo, template, found)
    return _version_out(found, blocks)


@module_config_templates.post("/{template_id}/versions", response_model=TemplateVersionOut, status_code=201)
async def add_template_version(template_id: int, req: TemplateContent, repo: ModuleConfigRepository = _repo,
                               auth=_template_write):
    """Saves an edit as the next immutable version."""
    template = await _template_or_404(repo, template_id)
    if template["archived"]:
        raise HTTPException(status_code=409, detail="The template is archived")
    body, blocks, findings = await _checked(repo, req.body, _variables(req), template["kind"], template["group_id"],
                                            template_id)
    if templating.has_errors(findings):
        return _findings_error("The template has errors", findings)
    version = await repo.add_version(
        template_id, body=body, variables=_variables(req), comment=req.comment, created_by=auth["user_name"],
    )
    return _version_out(version, blocks)


@module_config_templates.post("/{template_id}/duplicate", response_model=TemplateOut, status_code=201)
async def duplicate_template(template_id: int, req: TemplateDuplicate, repo: ModuleConfigRepository = _repo,
                             auth=_template_write):
    """"Save as": copies a version into a new template (version 1) that records where it came from."""
    source = await _template_or_404(repo, template_id)
    version = await repo.get_version(template_id, req.version)
    if version is None:
        raise HTTPException(status_code=404, detail="Template version not found")
    group_id = req.group_id if req.group_id is not None else source["group_id"]
    await _group_or_404(repo, group_id)
    await _refuse_group_move(repo, source, group_id)
    _, _, findings = await _checked(repo, version["body"], version["variables"], source["kind"], group_id, by_name=False)
    if templating.has_errors(findings):
        return _findings_error("The copy has errors", findings)
    created = await repo.create_template(
        name=req.name,
        description=req.description if req.description is not None else source["description"],
        group_id=group_id, body=version["body"], variables=version["variables"],
        comment=f"Duplicated from {source['name']} #{version['version']}", created_by=auth["user_name"],
        derived_from_template_id=template_id, derived_from_version=version["version"],
        kind=source["kind"],
    )
    if created is None:
        raise HTTPException(status_code=409, detail=f"A template named '{req.name}' already exists")
    return created


# ------------------------------------------------------------------ device modules


async def _render_for_device(
    device: str, module: str, req: RenderRequest, repo: ModuleConfigRepository, device_repo: DeviceRepository,
    endpoint_repo: EndpointRepository, service_repo: ServiceRepository,
):
    validate_iothub_id(device)
    validate_iothub_id(module)
    template = await _template_or_404(repo, req.template_id)
    if template["kind"] != "template":
        raise HTTPException(status_code=422, detail=f"'{template['name']}' is a block, not a template")
    if not module_matches(module, template["module_names"]):
        raise HTTPException(status_code=422, detail=f"Template '{template['name']}' does not apply to module '{module}'")
    device_meta = await device_repo.get_device_meta_raw(device)
    if not available_for_device(template, device_meta):
        raise HTTPException(
            status_code=403,
            detail=f"Template group '{template['group_name']}' is not available for device '{device}' (outside its scopes)")
    version = await repo.get_version(req.template_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Template version not found")
    blocks, ref_findings = await _version_blocks(repo, template, version)
    lists = _lists(req)
    entry_findings = templating.check_entries(version["body"], lists, blocks)
    if templating.has_errors(entry_findings):
        return template, version, None, ref_findings + entry_findings
    endpoints = await _chosen_endpoints(endpoint_repo, device, version["body"], req.endpoints, blocks, lists)
    services = await _chosen_services(service_repo, version["body"], req.endpoints, req.services, blocks, lists)
    config, findings = templating.render(version["body"], version["variables"], req.values, device_meta, endpoints,
                                         blocks, lists=lists, services=services)
    findings = ref_findings + findings
    if not templating.has_errors(findings):
        findings += _schema_findings(module, config)
    return template, version, config, findings


@module_config_devices.get("/{device}/modules/{module}/config/templates", response_model=List[TemplateOut])
async def list_templates_for_module(
    device: str, module: str,
    repo: ModuleConfigRepository = _repo,
    device_repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
    _=Depends(ABACPermissionCheck(Device.READ)),
):
    """The templates that can be applied to this module on this device: their group lists the
    module and its scopes (if any) match the device."""
    validate_iothub_id(device)
    validate_iothub_id(module)
    device_meta = await device_repo.get_device_meta_raw(device)
    return [t for t in await repo.list_templates(module=module, kind="template") if available_for_device(t, device_meta)]


@module_config_devices.get("/{device}/config/endpoints", response_model=List[DeviceEndpointOption])
async def list_endpoints_for_device(
    device: str,
    endpoint_repo: EndpointRepository = _endpoint_repo,
    _=Depends(ABACPermissionCheck(Device.READ)),
):
    """The device's endpoints to choose from for a template's endpoint slots."""
    validate_iothub_id(device)
    endpoints = await _device_endpoints(endpoint_repo, device)
    return [
        {"endpoint_id": e["endpoint_id"], "type_id": e["type_id"], "type_label": e["type_label"],
         "name": e["fields"].get("name"), "ip": e["fields"].get("ip"), "fields": e["fields"]}
        for e in endpoints.values()
    ]


@module_config_devices.get("/{device}/config/endpoints/{endpoint_id}/services", response_model=List[DeviceServiceOption])
async def list_services_for_endpoint(
    device: str, endpoint_id: str,
    endpoint_repo: EndpointRepository = _endpoint_repo,
    service_repo: ServiceRepository = _service_repo,
    _=Depends(ABACPermissionCheck(Device.READ)),
):
    """The services of one of the device's endpoints, to choose from for `${service[slot].*}`."""
    validate_iothub_id(device)
    if not any(e["endpoint_id"] == endpoint_id for e in await endpoint_repo.get_endpoints(device)):
        raise HTTPException(status_code=404, detail=f"Endpoint '{endpoint_id}' is not on device '{device}'")
    mappings = await _service_mappings(service_repo)
    out = []
    for s in await service_repo.get_services(endpoint_id):
        fields = templating.service_fields(s, mappings.get(s["type_id"], {}))
        out.append({"service_id": s["service_id"], "type_id": s["type_id"], "type_label": s["type_label"],
                    "name": fields.get("name"), "port": fields.get("port"), "fields": fields})
    return out


@module_config_devices.post("/{device}/modules/{module}/config/render", response_model=RenderResponse)
async def render_module_config(
    device: str, module: str, req: RenderRequest,
    repo: ModuleConfigRepository = _repo,
    device_repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
    endpoint_repo: EndpointRepository = _endpoint_repo,
    service_repo: ServiceRepository = _service_repo,
    _=Depends(ABACPermissionCheck(Device.READ)),
):
    """Dry run: renders the template for this device and returns the config and findings."""
    _, version, config, findings = await _render_for_device(device, module, req, repo, device_repo, endpoint_repo,
                                                            service_repo)
    return {
        "ok": config is not None and not templating.has_errors(findings),
        "template_id": req.template_id, "version": version["version"], "config": config, "findings": findings,
    }


@module_config_devices.post("/{device}/modules/{module}/config/apply", response_model=ApplyResponse)
async def apply_module_config(
    device: str, module: str, req: ApplyRequest,
    repo: ModuleConfigRepository = _repo,
    device_repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
    endpoint_repo: EndpointRepository = _endpoint_repo,
    service_repo: ServiceRepository = _service_repo,
    auth=Depends(ABACPermissionCheck(Device.MODULE_CONFIG_APPLY)),
):
    """Renders the template for this device and, if there are no errors, writes it to the module."""
    template, version, config, findings = await _render_for_device(device, module, req, repo, device_repo, endpoint_repo,
                                                                   service_repo)
    if template["archived"]:
        raise HTTPException(status_code=409, detail="The template is archived")
    if config is None or templating.has_errors(findings):
        return _findings_error("The configuration has errors", findings)
    values = {k: v for k, v in req.values.items() if not k.startswith("$")}
    if req.endpoints:
        values["$endpoints"] = req.endpoints
    if req.lists:
        values["$lists"] = _lists(req)
    if req.services:
        values["$services"] = req.services
    result = await service.write_module_config(
        device, module, config, source="template", actor=auth["user_name"], repo=repo,
        template_id=template["id"], values=values, comment=req.comment,
    )
    revision = result["revision"]
    if revision:
        revision = {**revision, "template_name": template["name"], "current": True}
    return {"configId": result["configId"], "revision": revision, "findings": findings}


async def _with_template_names(repo: ModuleConfigRepository, revisions: List[Dict], current: Optional[str]):
    template_ids = {r["template_id"] for r in revisions if r["template_id"]}
    names = {t["id"]: t["name"] for t in await repo.list_templates(include_archived=True)} if template_ids else {}
    return [
        {**r, "template_name": names.get(r["template_id"]), "current": r["config_id"] == current}
        for r in revisions
    ]


@module_config_devices.get("/{device}/modules/{module}/config/revisions", response_model=RevisionList)
async def list_module_config_revisions(
    device: str, module: str,
    limit: int = Query(100, ge=1, le=1000),
    repo: ModuleConfigRepository = _repo,
    _=Depends(ABACPermissionCheck(Device.READ)),
):
    """Revision history, newest first. A config written outside this service (old clients,
    direct IoT Hub writes) is recorded as an `external` revision on read."""
    validate_iothub_id(device)
    validate_iothub_id(module)
    current = await service.sync_external(device, module, repo)
    revisions = await repo.list_revisions(device, module, limit)
    return {"current_config_id": current, "revisions": await _with_template_names(repo, revisions, current)}


@module_config_devices.get("/{device}/modules/{module}/config/revisions/{revision_id}", response_model=RevisionDetail)
async def get_module_config_revision(
    device: str, module: str, revision_id: int,
    repo: ModuleConfigRepository = _repo,
    _=Depends(ABACPermissionCheck(Device.READ)),
):
    revision = await repo.get_revision(device, module, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    content = await service.read_revision_content(device, module, revision["blob_name"])
    [named] = await _with_template_names(repo, [revision], None)
    named.pop("current")
    return {**named, "content": content, "content_available": content is not None and revision["blob_name"] != "current"}


@module_config_devices.post("/{device}/modules/{module}/config/revisions/{revision_id}/reapply",
                            response_model=ApplyResponse)
async def reapply_module_config_revision(
    device: str, module: str, revision_id: int, req: ReapplyRequest,
    repo: ModuleConfigRepository = _repo,
    auth_context: dict = Depends(validate_jwt),
    user_repo: UserRepository = Depends(get_repository(UserRepository)),
    device_repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
):
    """Rollback: writes the exact content of an earlier revision again as a new revision.
    A template revision needs `device.module_config.apply`, anything else the raw write action."""
    validate_iothub_id(device)
    validate_iothub_id(module)
    revision = await repo.get_revision(device, module, revision_id)
    permission = Device.MODULE_CONFIG_APPLY if revision and revision["template_id"] is not None else Device.MODULE_TWIN_CONFIG_WRITE
    auth = await ABACPermissionCheck(permission)._authorize(auth_context, user_repo, device_repo, device)
    if revision is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    if revision["blob_name"] == "current":
        raise HTTPException(status_code=409, detail="This revision's content was stored in the mutable legacy blob and cannot be re-applied")
    content = await service.read_revision_content(device, module, revision["blob_name"])
    if content is None:
        raise HTTPException(status_code=410, detail="The revision's content is no longer available")
    result = await service.write_module_config(
        device, module, content, source="reapply", actor=auth["user_name"], repo=repo,
        template_id=revision["template_id"],
        values=revision["values"], comment=req.comment or f"Re-applied revision {revision_id}",
        reapplied_from_id=revision_id,
    )
    return {"configId": result["configId"], "revision": result["revision"] and {**result["revision"], "current": True}}
