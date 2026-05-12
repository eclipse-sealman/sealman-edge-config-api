import asyncio
from constants import IOT_HUB_NAME
from async_requests import get_async
from exceptions import IoTBackendAPIError
from smart_ems import SmartEMS
from helper import get_iothub_auth_headers


async def get_device_modules(device: str):
    """
        Returns all modules and their status from a device.
    """
    responses = {}
    url1 = f"https://{IOT_HUB_NAME}/devices/{device}/modules?api-version=2020-05-31-preview"
    url2 = f"https://{IOT_HUB_NAME}/twins/{device}/modules/$edgeAgent?api-version=2020-05-31-preview"
    url3 = f"https://{IOT_HUB_NAME}/twins/{device}?api-version=2020-05-31-preview"

    headers = get_iothub_auth_headers()

    await asyncio.gather(
        get_async(url1, responses, headers=headers, timeout=15),
        get_async(url2, responses, headers=headers, timeout=15),
        get_async(url3, responses, headers=headers, timeout=15),
        SmartEMS.get_compose_container(device, key="get_compose_container", resp=responses)
    )

    # evaluate device tags for deployments and module-alias
    if responses[url3].status_code == 200:
        device_tags = responses[url3].json().get("tags", {})
        module_id_translation = device_tags.get("moduleAlias", {})
    else:
        raise IoTBackendAPIError(responses[url3].text, responses[url3].status_code)

    iot_hub_connection = False
    module_selection = {}

    # pre-detect iot_hub_connection before evaluating edgeAgent twin
    if responses[url1].status_code == 200:
        for module in responses[url1].json():
            if module["moduleId"] == "$edgeHub" and module["connectionState"] == "Connected":
                iot_hub_connection = True
                break

    # evaluate edgeAgent twin
    if responses[url2].status_code == 200:
        modules = []

        # <<<< Check and set: RUNNING STATES >>>>
        if responses[url2].json()["properties"]["reported"].get("modules") is not None:
            reported_module_list = responses[url2].json()["properties"]["reported"]["modules"]
            desired_module_list = responses[url2].json()["properties"]["desired"].get("modules", {})

            for module_id in reported_module_list:
                module_status = reported_module_list[module_id]["runtimeStatus"]

                # mark modules as inactive (grey) if iot hub is not connected -> normally this is the last known state
                # of the specific module before went offline
                if not iot_hub_connection:
                    module_status = module_status + ":grey"
                module_version = reported_module_list[module_id]["settings"]["image"].split(":")[1]

                # since module-id appears in reported and desired properties of edgeAgent it runs on the device
                # the current reported state of the module will be set -> e.g. running, exited, backoff...
                if module_id in desired_module_list.keys():
                    module_selection[module_id] = {"status": module_status, "version": module_version}
                # if module-id appears in reported but not in desired properties of edgeAgent -> skip it
    else:
        raise IoTBackendAPIError(responses[url2].text, responses[url2].status_code)

    # evaluate device modules appearance and connections state due to iot-edge-api
    if responses[url1].status_code == 200:
        reported_module_list = responses[url1].json()
        for module in reported_module_list:
            module_id = module["moduleId"]
            module_conn_state = module["connectionState"]

            if module_id in module_selection:
                module_selection[module_id].update({"moduleName": module_id, "moduleId": module_id,
                                                    "connectionState": module_conn_state})
            elif module_id in ["$edgeAgent", "$edgeHub"]:
                # system modules are stored under systemModules in the edgeAgent twin
                module_selection[module_id] = {"moduleName": module_id, "moduleId": module_id,
                                               "connectionState": module_conn_state}
    else:
        raise IoTBackendAPIError(responses[url1].text, responses[url1].status_code)

    # rewrite edgeAgent and edgeHub state to runtime states
    for module_id in module_selection:
        if module_id in ["$edgeHub", "$edgeAgent"]:
            if not iot_hub_connection:
                module_selection[module_id].update({"status": "runtime_offline"})
            else:
                module_selection[module_id].update({"status": "runtime_online"})

    for module_id in module_selection:
        module_selection[module_id].update({"deploymentType": "base"})
        module_selection[module_id].update({"moduleType": "iotedge"})

    # get sems compose containers
    sems_container = responses["get_compose_container"]

    for container_id in sems_container:
        module_id = sems_container[container_id]["name"]
        version = sems_container[container_id]["version"]
        module_selection[module_id] = {"moduleName": module_id, "moduleId": module_id,
                                       "connectionState": "Disconnected", "status": "running",
                                       "version": f"{version}", "deploymentType": "sems",
                                       "moduleType": "compose"}

    # prepare return object -> mutate from dict to list
    for module_id in module_selection:
        modules.append(module_selection[module_id])

    # ensure system modules are always at the top of the list
    system_module_order = {"$edgeAgent": 0, "$edgeHub": 1}
    modules.sort(key=lambda m: system_module_order.get(m["moduleId"], len(system_module_order)))

    # Set module name if a specific alias was set in device tags for the module
    for module in modules:
        if module["moduleName"] in module_id_translation:
            module["moduleName"] = module_id_translation[module["moduleName"]]
    return modules