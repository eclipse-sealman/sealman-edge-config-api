from constants import IOT_HUB_NAME
from exceptions import IoTBackendAPIError
from async_requests import get_async
import asyncio
from datetime import datetime
from typing import Any
from helper import get_iothub_auth_headers


def _extract_target_condition_value(target_condition: str | None) -> str:
    if not isinstance(target_condition, str):
        return ""

    _, separator, value = target_condition.partition("=")
    normalized = value if separator else target_condition
    return normalized.strip().strip("'").strip('"')


def _to_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _to_timestamp(value) -> float:
    if not value:
        return float("-inf")

    if isinstance(value, datetime):
        return value.timestamp()

    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return float("-inf")

    return float("-inf")


def _is_better_rank(new_rank: dict[str, float | int], current_rank: dict[str, float | int]) -> bool:
    return new_rank["priority"] > current_rank["priority"] or (
        new_rank["priority"] == current_rank["priority"]
        and new_rank["created_time"] > current_rank["created_time"]
    )


async def get_deployment_list():
    """
        Returns the list of deployments of the Azure IoT Hub
    """
    deployment_list = []

    get_deployments_url = f"https://{IOT_HUB_NAME}/configurations?api-version=2020-05-31-preview"
    response = {}
    headers = get_iothub_auth_headers()
    await asyncio.gather(
        get_async(get_deployments_url, response, headers=headers)
    )

    if response[get_deployments_url].status_code == 200:
        deployments = response[get_deployments_url].json()
        selected_by_target: dict[str, dict[str, Any]] = {}

        for deployment in deployments:
            if not deployment.get("id"):
                continue

            target_value = _extract_target_condition_value(deployment.get("targetCondition"))
            rank = {
                "priority": _to_int(deployment.get("priority")),
                "created_time": _to_timestamp(deployment.get("createdTimeUtc")),
            }

            current = selected_by_target.get(target_value)
            if current is None or _is_better_rank(rank, current["rank"]):
                selected_by_target[target_value] = {"rank": rank, "deployment": deployment}

        for target_value, selected in selected_by_target.items():
            deployment_list.append({
                "id": selected["deployment"].get("id"),
                "targetCondition": target_value,
            })

        return deployment_list
    else:
        raise IoTBackendAPIError("Unable to fetch the list of deployments from the Azure IoT Hub.", 404)
