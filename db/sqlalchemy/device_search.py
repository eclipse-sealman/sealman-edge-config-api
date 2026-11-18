from typing import Any, Callable, Dict, List, Literal, Optional, Sequence, Tuple

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from db.registry import register_repository
from db.repos.device_search import ColumnFilter, DeviceSearchRepository, SearchFilter, SearchKind

_LIKE_ESCAPE = "!"

# One row per (instance, field) with the value as the API resolves it (see db/merge.py
# resolve_fields): the stored value, or the type's default when none is stored. Fields that are
# stored but not declared by the type are included too. `{fields}` is the jsonb field schema,
# `{data}` the instance's jsonb data; both are evaluated per instance.
_RESOLVED_FIELDS = """
    select k.key,
           coalesce({data} ->> k.key, {fields} -> k.key ->> 'default') as value,
           {fields} -> k.key ->> 'label' as field_label
    from (
        select jsonb_object_keys({fields}) as key
        union
        select jsonb_object_keys({data})
    ) k
"""

# The value of the field the type's mapping marks with role "name" (see db/type_roles.py).
_NAME_OF = """
    left join lateral (
        select coalesce({data} ->> m.field_key, {fields} -> m.field_key ->> 'default') as name
        from jsonb_each_text({mapping}) as m(field_key, role)
        where m.role = 'name'
        limit 1
    ) {alias} on true
"""

# Devices with their effective field schema `f.fields`: the default device type's fields apply to
# every type, a type's own fields override them (see SqlAlchemyDeviceRepository._effective_fields_for).
_DEVICES = """
    from devices d
    join device_types t on t.type_id = d.type_id
    left join device_types dt on dt.type_id = 'default'
    cross join lateral (select coalesce(dt.fields, '{}'::jsonb) || t.fields as fields) f
"""


def _escape_like(term: str) -> str:
    """Makes `%`, `_` and the escape character itself match literally in a LIKE pattern."""
    return "".join(_LIKE_ESCAPE + char if char in (_LIKE_ESCAPE, "%", "_") else char for char in term)


def _like_any(column: str, terms: Sequence[str]) -> tuple[str, Dict[str, str]]:
    """`(column ilike :t0 escape '!' or ...)` plus its bind values. Only bind names are
    interpolated into the SQL, never the terms themselves."""
    params = {f"t{i}": f"%{_escape_like(term)}%" for i, term in enumerate(terms)}
    clause = " or ".join(f"{column} ilike :{name} escape '{_LIKE_ESCAPE}'" for name in params)
    return f"({clause})", params


def _match_any(
    terms: Sequence[str], keyed_terms: Sequence[Tuple[str, str]]
) -> tuple[str, Dict[str, str]]:
    """Like `_like_any` over `r.value`, plus `(r.key = :kn and r.value ilike :vn ...)` for each
    (key, value) of `keyed_terms`."""
    clauses: List[str] = []
    params: Dict[str, str] = {}
    if terms:
        clause, params = _like_any("r.value", terms)
        clauses.append(clause)
    for i, (key, value) in enumerate(keyed_terms):
        params[f"kk{i}"] = key
        params[f"kv{i}"] = f"%{_escape_like(value)}%"
        clauses.append(
            f"(r.key = cast(:kk{i} as text) and r.value ilike :kv{i} escape '{_LIKE_ESCAPE}')"
        )
    return f"({' or '.join(clauses)})", params


def _field_value(key_param: str) -> str:
    """SQL for the resolved value of the metadata field named by bind parameter `key_param`."""
    key = f"cast(:{key_param} as text)"
    return f"coalesce(d.device_data ->> {key}, f.fields -> {key} ->> 'default')"


def _metadata_values() -> str:
    return _RESOLVED_FIELDS.format(data="d.device_data", fields="f.fields")


def _endpoint_values() -> str:
    resolved = _RESOLVED_FIELDS.format(data="e.endpoint_data", fields="et.fields")
    return f"{resolved} union all select cast(null as text), et.label, cast(null as text)"


def _service_values() -> str:
    resolved = _RESOLVED_FIELDS.format(data="s.service_data", fields="st.fields")
    return f"{resolved} union all select cast(null as text), st.label, cast(null as text)"


_ENDPOINTS = """
    from endpoints e
    join endpoint_types et on et.type_id = e.type_id
"""

_SERVICES = """
    from services s
    join service_types st on st.type_id = s.type_id
    join endpoints e on e.endpoint_id = s.endpoint_id
"""


def _exists_metadata(param: str) -> str:
    return f"exists (select 1 from ({_metadata_values()}) r where r.value ilike :{param} escape '{_LIKE_ESCAPE}')"


def _exists_endpoint(param: str, key_param: Optional[str] = None) -> str:
    keyed = f"r.key = cast(:{key_param} as text) and " if key_param else ""
    return (
        f"exists (select 1 {_ENDPOINTS} cross join lateral ({_endpoint_values()}) r "
        f"where e.device_id = d.device_id and {keyed}r.value ilike :{param} escape '{_LIKE_ESCAPE}')"
    )


def _exists_service(param: str, key_param: Optional[str] = None) -> str:
    keyed = f"r.key = cast(:{key_param} as text) and " if key_param else ""
    return (
        f"exists (select 1 {_SERVICES} join endpoint_types et on et.type_id = e.type_id "
        f"cross join lateral ({_service_values()}) r "
        f"where e.device_id = d.device_id and {keyed}r.value ilike :{param} escape '{_LIKE_ESCAPE}')"
    )


# conditions on the "contains" kinds: the EXISTS over the device's own values / endpoints / services
_CONTAINS_CONDITIONS: Dict[str, Callable[..., str]] = {
    "metadata": _exists_metadata,
    "endpoint": _exists_endpoint,
    "service": _exists_service,
}


@register_repository(DeviceSearchRepository)
class SqlAlchemyDeviceSearchRepository(DeviceSearchRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def _rows(
        self,
        sql: str,
        params: Dict[str, Any],
        device_column: str,
        allowed_device_ids: Optional[List[str]],
        expanding: Sequence[str] = (),
    ) -> List[Dict[str, Any]]:
        """Runs `sql`, replacing its `{allowed}` placeholder with the device restriction.
        `expanding` names the list-valued parameters (rendered as IN (...) lists)."""
        expanding = list(expanding)
        params = dict(params)
        if allowed_device_ids is not None:
            sql = sql.replace("{allowed}", f"and {device_column} in :allowed_device_ids")
            params["allowed_device_ids"] = allowed_device_ids
            expanding.append("allowed_device_ids")
        else:
            sql = sql.replace("{allowed}", "")
        statement = text(sql).bindparams(*[bindparam(name, expanding=True) for name in expanding])
        result = await self._session.execute(statement, params)
        return [dict(row) for row in result.mappings().all()]

    async def find_device_ids(
        self,
        filters: List[SearchFilter],
        column_filters: List[ColumnFilter],
        allowed_device_ids: Optional[List[str]] = None,
    ) -> List[str]:
        if allowed_device_ids == []:
            return []

        conditions: List[str] = []
        params: Dict[str, Any] = {}
        expanding: List[str] = []

        for i, search_filter in enumerate(filters):
            value = f"v{i}"
            if search_filter.kind == "device_id":
                conditions.append(f"d.device_id ilike :{value} escape '{_LIKE_ESCAPE}'")
                params[value] = f"%{_escape_like(search_filter.value)}%"
            elif search_filter.kind == "field":
                if not search_filter.field:
                    raise ValueError("A field filter needs a field")
                key = f"k{i}"
                conditions.append(f"lower({_field_value(key)}) = lower(:{value})")
                params[key] = search_filter.field
                params[value] = search_filter.value
            elif search_filter.kind in _CONTAINS_CONDITIONS:
                if search_filter.field and search_filter.kind in ("endpoint", "service"):
                    key = f"k{i}"
                    conditions.append(_CONTAINS_CONDITIONS[search_filter.kind](value, key))
                    params[key] = search_filter.field
                else:
                    conditions.append(_CONTAINS_CONDITIONS[search_filter.kind](value))
                params[value] = f"%{_escape_like(search_filter.value)}%"
            else:
                raise ValueError(f"Unknown search filter kind '{search_filter.kind}'")

        for j, column_filter in enumerate(column_filters):
            key, values = f"ck{j}", f"cv{j}"
            conditions.append(f"{_field_value(key)} in :{values}")
            params[key] = column_filter.field
            params[values] = list(column_filter.values)
            expanding.append(values)

        where = "".join(f" and {condition}" for condition in conditions)
        sql = f"select d.device_id {_DEVICES} where true{where} {{allowed}} order by d.device_id"
        rows = await self._rows(sql, params, "d.device_id", allowed_device_ids, expanding)
        return [row["device_id"] for row in rows]

    async def suggest_values(
        self,
        kind: SearchKind,
        field: Optional[str],
        query: Optional[str],
        limit: int,
        allowed_device_ids: Optional[List[str]] = None,
    ) -> List[str]:
        if allowed_device_ids == [] or limit <= 0:
            return []

        params: Dict[str, Any] = {"limit": limit}
        if query:
            params["q"] = f"%{_escape_like(query)}%"

        def matches(column: str) -> str:
            return f"and {column} ilike :q escape '{_LIKE_ESCAPE}'" if query else ""

        # a field of an endpoint / service: only that field's values
        keyed = "and r.key = cast(:k as text)" if field and kind in ("endpoint", "service") else ""
        if keyed:
            params["k"] = field
        non_empty = "r.value is not null and r.value <> ''"
        device_column = "d.device_id"
        if kind == "device_id":
            sql = (
                f"select d.device_id as value from devices d where true {matches('d.device_id')} {{allowed}} "
                "order by d.device_id limit :limit"
            )
        elif kind == "field":
            if not field:
                raise ValueError("A field suggestion needs a field")
            params["k"] = field
            sql = (
                f"select v.value from (select distinct {_field_value('k')} as value {_DEVICES} where true {{allowed}}) v "
                f"where v.value is not null and v.value <> '' {matches('v.value')} order by v.value limit :limit"
            )
        elif kind == "metadata":
            sql = (
                f"select distinct r.value {_DEVICES} cross join lateral ({_metadata_values()}) r "
                f"where {non_empty} {matches('r.value')} {{allowed}} order by r.value limit :limit"
            )
        elif kind == "endpoint":
            device_column = "e.device_id"
            sql = (
                f"select distinct r.value {_ENDPOINTS} cross join lateral ({_endpoint_values()}) r "
                f"where {non_empty} {keyed} {matches('r.value')} {{allowed}} order by r.value limit :limit"
            )
        elif kind == "service":
            device_column = "e.device_id"
            sql = (
                f"select distinct r.value {_SERVICES} join endpoint_types et on et.type_id = e.type_id "
                f"cross join lateral ({_service_values()}) r "
                f"where {non_empty} {keyed} {matches('r.value')} {{allowed}} order by r.value limit :limit"
            )
        else:
            raise ValueError(f"Unknown suggestion kind '{kind}'")

        rows = await self._rows(sql, params, device_column, allowed_device_ids)
        return [row["value"] for row in rows]

    async def list_field_keys(
        self,
        kind: Literal["endpoint", "service"],
        query: Optional[str],
        limit: int,
        allowed_device_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Optional[str]]]:
        if allowed_device_ids == [] or limit <= 0:
            return []
        if kind == "endpoint":
            source = f"{_ENDPOINTS} cross join lateral ({_RESOLVED_FIELDS.format(data='e.endpoint_data', fields='et.fields')}) r"
        else:
            source = f"{_SERVICES} cross join lateral ({_RESOLVED_FIELDS.format(data='s.service_data', fields='st.fields')}) r"
        params: Dict[str, Any] = {"limit": limit}
        matching = ""
        if query:
            params["q"] = f"%{_escape_like(query)}%"
            matching = f"and (r.key ilike :q escape '{_LIKE_ESCAPE}' or r.field_label ilike :q escape '{_LIKE_ESCAPE}')"
        sql = (
            f"select r.key, max(r.field_label) as label {source} "
            f"where r.value is not null and r.value <> '' {matching} {{allowed}} "
            "group by r.key order by count(*) desc, r.key limit :limit"
        )
        return await self._rows(sql, params, "e.device_id", allowed_device_ids)

    async def get_device_fields(
        self,
        field_keys: List[str],
        device_ids: List[str],
    ) -> List[Dict[str, Any]]:
        if not field_keys or not device_ids:
            return []
        sql = (
            f"select d.device_id, r.key as field_key, r.field_label, r.value {_DEVICES} "
            f"cross join lateral ({_metadata_values()}) r "
            "where r.key in :field_keys and r.value is not null and r.value <> '' and d.device_id in :device_ids "
            "order by d.device_id, r.key"
        )
        params = {"field_keys": field_keys, "device_ids": device_ids}
        return await self._rows(sql, params, "d.device_id", None, expanding=("field_keys", "device_ids"))

    async def search_device_metadata(
        self,
        terms: List[str],
        allowed_device_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        if not terms or allowed_device_ids == []:
            return []
        condition, params = _like_any("r.value", terms)
        sql = f"""
            select d.device_id, r.key as field_key, r.field_label, r.value
            {_DEVICES}
            cross join lateral ({_metadata_values()}) r
            where {condition} {{allowed}}
            order by d.device_id, r.key
        """
        return await self._rows(sql, params, "d.device_id", allowed_device_ids)

    async def search_endpoints(
        self,
        terms: List[str],
        allowed_device_ids: Optional[List[str]] = None,
        keyed_terms: Sequence[Tuple[str, str]] = (),
    ) -> List[Dict[str, Any]]:
        if (not terms and not keyed_terms) or allowed_device_ids == []:
            return []
        condition, params = _match_any(terms, keyed_terms)
        name_of = _NAME_OF.format(data="e.endpoint_data", fields="et.fields", mapping="et.mapping", alias="n")
        sql = f"""
            select e.device_id, e.endpoint_id, et.label as type_label, n.name as endpoint_name,
                   r.key as field_key, r.field_label, r.value
            {_ENDPOINTS}
            {name_of}
            cross join lateral ({_endpoint_values()}) r
            where {condition} {{allowed}}
            order by e.device_id, e.created_at, e.endpoint_id, r.key nulls first
        """
        return await self._rows(sql, params, "e.device_id", allowed_device_ids)

    async def search_services(
        self,
        terms: List[str],
        allowed_device_ids: Optional[List[str]] = None,
        keyed_terms: Sequence[Tuple[str, str]] = (),
    ) -> List[Dict[str, Any]]:
        if (not terms and not keyed_terms) or allowed_device_ids == []:
            return []
        condition, params = _match_any(terms, keyed_terms)
        name_of = _NAME_OF.format(data="e.endpoint_data", fields="et.fields", mapping="et.mapping", alias="n")
        sql = f"""
            select e.device_id, e.endpoint_id, n.name as endpoint_name, s.service_id,
                   st.label as type_label, r.key as field_key, r.field_label, r.value
            {_SERVICES}
            join endpoint_types et on et.type_id = e.type_id
            {name_of}
            cross join lateral ({_service_values()}) r
            where {condition} {{allowed}}
            order by e.device_id, e.created_at, e.endpoint_id, s.created_at, s.service_id, r.key nulls first
        """
        return await self._rows(sql, params, "e.device_id", allowed_device_ids)
