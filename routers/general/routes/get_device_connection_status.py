import asyncio
import datetime
import logging
from constants import IOT_HUB_NAME, SEMS_LOOKUP_INTERVAL
from async_requests import get_async
from exceptions import IoTBackendAPIError
from routers.smart_ems.routes.get_smart_ems_device_info import get_smart_ems_device_data
from smart_ems import SmartEMS
from helper import get_iothub_auth_headers


logger = logging.getLogger("EdgeConfigAPI")


async def get_device_connection_status(device_id):
    """
        Returns the connection states of a device
    """
    resp = {
        "iotEdgeRuntime": "Unknown",
        "iotHub": "Unknown",
        "sems": "Unknown"
        }

    responses = {}
    headers = get_iothub_auth_headers()
    # runtime_connection_status
    url1 = f"https://{IOT_HUB_NAME}/twins/{device_id}?api-version=2021-04-12"
    # data_connection_status
    url2 = f"https://{IOT_HUB_NAME}/twins/{device_id}/modules/$edgeHub?api-version=2020-05-31-preview"

    await asyncio.gather(get_async(url1, responses, headers=headers),
                         get_async(url2, responses, headers=headers))

    # get runtime_connection_status
    if responses[url1].status_code == 200:
        device_obj = responses[url1].json()
        resp["iotEdgeRuntime"] = device_obj["connectionState"]    # Connected | Disconnected
    else:
        raise IoTBackendAPIError(f"could not retrieve any device from iot-hub: {responses[url1].text}",
                                 responses[url1].status_code)

    # get data_connection_status
    if responses[url2].status_code == 200:
        edge_hub_twin_obj = responses[url2].json()
        resp["iotHub"] = edge_hub_twin_obj.get("connectionState")     # Connected | Disconnected
    else:
        raise IoTBackendAPIError(f"could not get twin of edgeHub module: {responses[url2].text}",
                                 responses[url2].status_code)

    # handle iotEdgeRuntime status update delay by combining it with the connections state of the iot-hub
    # if the iot-hub (live info) is connected - the runtime cannot be disconnected -> if it reports disconnected
    # due to its update delay -> overwrite it with connected.
    # The case that runtime is connected and iot-hub is disconnected can happen and is still valid -> no overwrites
    if resp["iotHub"] == "Connected" and resp["iotEdgeRuntime"] == "Disconnected":
        resp["iotEdgeRuntime"] = "Connected"

    # get sems_connection_status
    sems_obj = await SmartEMS.get_device_by_serial(device_id)
    dt_last_seen = datetime.datetime.fromisoformat(sems_obj.get(
        "seenAt", datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc).isoformat())
    )
    dt_now = datetime.datetime.now(tz=datetime.timezone.utc)
    dt_delta = dt_now - dt_last_seen
    dt_max_delta = datetime.timedelta(seconds=int(SEMS_LOOKUP_INTERVAL))

    # calc SEMS connected state
    if dt_delta < dt_max_delta:
        resp["sems"] = "Connected"
    else:
        resp["sems"] = "Disconnected"

    # get vpn from "Edge gateway with VPN Container Client"
    # disable VPN until it gets also part of the OSS project
    #if sems_obj["deviceType"]["name"] == "Edge gateway with VPN Container Client":
    #    if sems_obj.get("vpnConnected"):
    #        resp["vpn"] = "Connected"
    #    else:
    #        resp["vpn"] = "Disconnected"

    return resp
