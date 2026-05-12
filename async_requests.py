from typing import List
import httpx
from constants import ALLOW_INSECURE_HTTPS

client = httpx.AsyncClient(
    verify=not ALLOW_INSECURE_HTTPS,
    limits=httpx.Limits(
        max_connections=300,
        max_keepalive_connections=150
    )
)

async def get_async(url: str, data: dict, headers: dict = {}, timeout=5) -> None:
    response = await client.get(
        url,
        headers=headers,
        timeout=timeout
    )
    data[url] = response


async def post_async(url: str, responses: dict, _json: dict = {}, headers: dict = {}, timeout=5) -> None:

    response = await client.post(
        url,
        headers=headers,
        json=_json,
        timeout=timeout
    )
    responses[url] = response


async def put_async(url: str, responses: dict, _json: dict | List = {}, headers: dict = {}, timeout=5) -> None:

    response = await client.put(
        url,
        headers=headers,
        json=_json,
        timeout=timeout
    )
    responses[url] = response


async def patch_async(url: str, responses: dict, _json: dict = {}, headers: dict = {}, timeout=5) -> None:

    response = await client.patch(
        url,
        headers=headers,
        json=_json,
        timeout=timeout
    )
    responses[url] = response


async def delete_async(url: str, responses: dict, headers: dict = {}, timeout=5) -> None:
    response = await client.delete(
        url,
        headers=headers,
        timeout=timeout
    )
    responses[url] = response
