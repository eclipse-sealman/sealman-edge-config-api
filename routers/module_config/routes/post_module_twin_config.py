from helper import validate_iothub_id
from routers.module_config.service import write_supplied_config


async def post_module_twin_config(device, module, request, actor=None, source="legacy-api", repo=None, files_repo=None):
    """
        Post a configuration to the module twin and triggers a twin-change event.
        Kept for the typed legacy endpoints; the write itself (blob + twin pointer + revision, a mask of the
        current revision restored to the file's content) is `service.write_supplied_config`. The response
        echoes the request as sent.
    """
    validate_iothub_id(device)
    validate_iothub_id(module)
    content = request.model_dump(mode="json", exclude_none=True) if hasattr(request, "model_dump") else request
    await write_supplied_config(device, module, content, source=source, actor=actor, repo=repo, files_repo=files_repo)
    return request, 200
