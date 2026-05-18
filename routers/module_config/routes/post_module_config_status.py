import asyncio
from constants import IOT_HUB_NAME
from async_requests import get_async
from helper import get_iothub_auth_headers


async def post_module_config_status(device, module_list):
    """
        Returns the config status of each module of the desired device and also the status of the application
        itself. Means if the application tun the configuration successfully or not
        Possible conf status are:
            !desired-conf-id                    --> NO_CONFIG
            desired-conf-id == reported-conf-id --> OK
            desired-conf-id != reported-conf-id --> PENDING
            !reported-conf-id                   --> INITIAL_PENDING
        App-Status:
            status      --> OK /  ERROR / NO_STATUS
            message     --> <specific-app-message>
    """
    modules = module_list.modules

    _requests = []
    responses = {}
    urls = []
    module_by_url = {}

    for module in modules:
        url = f"https://{IOT_HUB_NAME}/twins/{device}/modules/{module}?api-version=2020-05-31-preview"
        urls.append(url)
        module_by_url[url] = module
        headers = get_iothub_auth_headers()
        _requests.append(get_async(url, responses, headers=headers))
    await asyncio.gather(*_requests)

    resp = {}
    for url in urls:

        if responses[url].status_code != 200:
            module_id = module_by_url[url]
            resp[module_id] = {
                "confStatus": "NO_CONFIG",
                "appStatus": "NO_STATUS", 
                "appMessage": ""}
            continue

        module_id = responses[url].json()["moduleId"]

        # !desired-conf-id                    --> NO_CONFIG
        # desired-conf-id == reported-conf-id --> OK
        # desired-conf-id != reported-conf-id --> PENDING
        # !reported-conf-id                   --> INITIAL_PENDING

        if "configId" in responses[url].json()["properties"]["desired"].keys():
            desired_conf_id = responses[url].json()["properties"]["desired"]["configId"]
            if "configId" in responses[url].json()["properties"]["reported"].keys():
                reported_conf_id = responses[url].json()["properties"]["reported"]["configId"]
            else:
                reported_conf_id = None

            if desired_conf_id == reported_conf_id:
                conf_status = "OK"
            else:
                if reported_conf_id is None:
                    conf_status = "INITIAL_PENDING"
                else:
                    conf_status = "PENDING"
            resp[module_id] = {"confStatus": conf_status}
            if (desired_conf_id is not None):
                resp[module_id]["desiredConfId"] = desired_conf_id
            if (reported_conf_id is not None):
                resp[module_id]["reportedConfId"] = reported_conf_id
        else:
            conf_status = "NO_CONFIG"
            resp[module_id] = {"confStatus": conf_status}

        if "config" in responses[url].json()["properties"]["reported"].keys() and conf_status == "OK":
            app_status = responses[url].json()["properties"]["reported"]["config"].get("status")
            app_message = responses[url].json()["properties"]["reported"]["config"].get("message")
            if app_status:
                resp[module_id].update({"appStatus": "OK"})
            else:
                resp[module_id].update({"appStatus": "ERROR"})

            if app_message is not None:
                resp[module_id].update({"appMessage": app_message})
        else:
            resp[module_id].update({"appStatus": "NO_STATUS"})

    return resp
