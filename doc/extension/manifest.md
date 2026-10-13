# Extension manifest: module schemas and response references

> **Status: WP-1 through WP-4 implemented (IIOT-2789).** `POST /extensions` and `PUT /extensions/{name}` accept `module_schemas` and `response_ref`, resolve and persist their schemas, and activate them on enable and at startup. Generic configuration GET/POST, the four deprecated fixed configuration aliases and reported GET validate against these schemas. Fixed aliases remain first in route order but now delegate to the same generic configuration behavior. Provider adoption for these IDs requires this WP-4 behavior on the deployed API; older versions still bypass extension validation through their typed routes.

## Module schemas

An extension that owns a module's configuration declares it under `module_schemas`, keyed by the exact IoT Hub module ID:

```json
{
  "module_schemas": {
    "example-module": {
      "upstream": "example-http",
      "schema_ref": "#/components/schemas/ExampleModuleConfigV1",
      "reported_schema_ref": "#/components/schemas/ExampleModuleReportedV1"
    }
  }
}
```

| Field | Required | Meaning |
|---|---|---|
| module ID (key) | yes | Exact IoT Hub module ID. Only one registered extension may claim it, including a disabled one. A second claim is rejected with 409. |
| `upstream` | yes | Key of an `http` upstream declared in the same manifest. |
| `schema_ref` | yes | Local component in that upstream's OpenAPI document, for configuration. Enforced on generic `GET/POST /{device}/twin/config/{module}` and the deprecated fixed configuration aliases. |
| `reported_schema_ref` | no | Local component for the filtered response of `GET /{device}/twin/identity/{module}/reported`. Enforced on that GET. Without it, the generic reported response applies. |

A configuration schema never validates reported data, and a reported schema never validates configuration. Each is stored as its own snapshot.

## Route response references

`RouteSpec.response_ref` (boolean, default `false`) works like `body_ref`. When it is `true`, the core reads the response schema for the route's `status_code` (default `200`) from the upstream's OpenAPI document and publishes it in this API's OpenAPI. It documents the response. The core does not validate or change proxied responses.

```json
{
  "upstream": "example-http",
  "path": "/example/items",
  "upstream_path": "/example/items",
  "method": "GET",
  "response_ref": true
}
```

If the upstream cannot be read, the route still registers and its response is simply not documented. The stored response snapshot is never returned by `GET /extensions`.

## Schema resolution

- The core fetches only `{base_url}/openapi.json` of the referenced upstream. Redirects are not followed, and the manifest cannot supply a schema URL.
- Only local `#/components/schemas/...` references are resolved, including nested ones. External references, other local pointers and unresolved components are rejected.
- Each schema is stored as a self-contained document: the referenced schema plus the components it reaches, each embedded once under `components.schemas`. Recursive models stay valid.
- Each snapshot has a `version`: the SHA-256 of the snapshot plus its `resolved_at` time.
- Schemas are fetched when the manifest is saved, re-checked on enable, and refreshed at API startup. Requests never fetch them.
- The core never imports extension Python code.

## Lifecycle

| Event | Result |
|---|---|
| Register or replace with an invalid module schema (422) or an unreachable upstream (502) | Rejected. Nothing is saved, and the previous state is kept. |
| Register or replace with a module ID claimed by another extension | Rejected with 409. Nothing is saved. |
| Enable or API startup | Activates the stored valid snapshots. Enable re-checks the claim (409 if another extension holds it). A failed refresh keeps the last valid snapshot. |
| Enabled, but a module's stored snapshot cannot be activated | The module ID stays claimed and gets no active schema. Generic and fixed configuration `GET/POST`, and reported `GET` calls for that module return 503 and never fall back. |
| Replace without `reported_schema_ref` | Removes the reported snapshot. The module returns to the generic reported response. The core route stays mounted. |
| Disable | Removes the module's active schemas. The module ID stays claimed by the disabled extension, so another extension still cannot register it. |
| Deregister | Removes the module's active schemas and its claim. The module ID becomes available again. |

## Runtime responses for a declared module

Configuration responses below apply to both generic operations and the deprecated fixed aliases.

| Situation | Response |
|---|---|
| Generic `POST` configuration violates `schema_ref` | Structured `422` with `message` and bounded `errors` (`loc`, `rule`, `msg`), without payload values, raw validator exceptions or full schemas. No writes. |
| Generic `POST` with no active configuration schema | Accepts JSON objects only, including unknown modules, disabled/deregistered providers and `EXTENSIONS_ENABLED=false`. Arrays, scalars and `null` return structured `422`. |
| Generic `POST` module claimed but unavailable, or active schema unusable | Fixed safe `503` with `Module schema is unavailable`. No writes or fallback. An unavailable claim is checked before buffering and again after body reading. |
| Generic `POST` empty/malformed/non-finite JSON | `400` with `Request body must be valid finite JSON`; this includes `NaN`, `Infinity` and exponent overflow. No writes. |
| Generic `POST` over 1 MiB | `413` before writes. |
| Generic `POST` without device write authorization | `403` before registry disclosure, body parsing or writes. `device.read` alone is insufficient; `device.module_twin_config.write` and device scope are required. |
| `GET` stored configuration violates `schema_ref` | Safe server error, `500` with the fixed message `Module data could not be validated against its active schema`. The invalid data and the validation details are not returned; they are logged server-side. |
| `GET` reported data violates `reported_schema_ref` | The same safe `500`. The generic response is not substituted. |
| `GET` module ID claimed but unavailable | `503` with `Module schema is unavailable`, before any Blob or IoT Hub read. Device read authorization still runs first. |
| `GET` with no active schema for that purpose, or the module is not declared | Generic response, as today. |
| `GET` for a module with no stored data | `null`, as today. Missing module keeps today's 404 for configuration and today's 500 wrapper for reported. |

The generic `GET` routes publish their OpenAPI 200 response as `anyOf`: the core response, each active schema of that response's purpose only, and `null`. An unregistered module, a disabled extension or a configuration-only entry removes its reported alternative and keeps the core reported response.

## Generic configuration POST

Runtime selects the exact module ID's current `configuration` snapshot/version after bounded body reading. An active schema governs its declared JSON root shape; object fallback cannot bypass it. Reported and other modules' schemas are never used. Validation does not fetch schemas, call extension services/direct methods or read Blob/IoT Hub data.

The validated JSON is persisted literally: explicit nulls (including required/optional nullable properties, nested dictionaries and array entries) and schema-permitted extra properties survive. Defaults are not injected, types are not coerced and properties are not stripped. The existing core helper uploads to container `iotedge-device-twin`, blob `{device}/{module}/current`, with `overwrite=True`, then patches the exact module twin's desired `configBlobUrl` and UUID-hex `configId`. Identifiers are forwarded unchanged. Upload-before-patch ordering and downstream errors remain unchanged; a failed twin patch does not roll back the upload. Success is HTTP 200 with `[configuration, 200]`, not a new envelope or returned config ID. The deprecated fixed POST aliases use this same literal-JSON behavior.

POST OpenAPI publishes a required `application/json` body with `anyOf`: the object fallback plus every current active **configuration** schema. Nested component references are namespaced and constraints retained. This union describes available contracts; runtime selects the exact module, not an OpenAPI path-parameter selector. Repeated reads do not modify the cached base document. Enable/replace/disable/deregister/startup hydration refresh the alternatives and remove stale schemas while preserving both generic GET contracts.

## Deprecated fixed configuration aliases

The following operations remain reachable with their existing handler names/operation IDs, tags, global JWT protection and device read/write ABAC:

| Method | Fixed path | Exact module ID |
|---|---|---|
| GET / POST | `/{device}/twin/config/seal-module-opcua-client` | `seal-module-opcua-client` |
| GET / POST | `/{device}/twin/config/seal-app-net-discover` | `seal-app-net-discover` |

All four operations are marked `deprecated: true`. They precede the catch-all route: a client constructing one of these concrete URLs from the generic path still reaches the fixed alias, not a separately selectable generic handler. Query/body fields cannot override its exact module ID. Binary configuration, reported and status routes are unchanged.

WP-4's accepted compatibility policy preserves the URLs, authorization, missing-data `null`, helper errors, exact storage/twin targets and POST success tuple. It intentionally removes API-owned protocol models: no OPCUA defaults, network-discovery type coercion or port deduplication, extra-field removal or null pruning occurs. Provider schemas validate the supplied JSON; schema violations now return structured `422` rather than legacy model-validation `400`. Malformed JSON remains `400`.

With no active configuration snapshot, GET uses the generic object/`null` contract and authorized POST accepts JSON objects only. Disable, deregister and `EXTENSIONS_ENABLED=false` do not restore legacy models. Enabled-but-unavailable claims always fail safely with `503` before helper I/O. Extension owners must coordinate adoption against a deployed API with these alias semantics; this guide does not promise provider schema parity or a rollout date.

For each active fixed ID, OpenAPI publishes only its **configuration** snapshot: a nullable GET response and a required POST body, not all modules' alternatives or its reported schema. Inactive aliases publish the generic fallbacks. Nested references reuse the same module/purpose-namespaced components as the generic operations. Replacement/removal and explicit startup hydration refresh these contracts without mutating the cached base document. The description explains that unavailable claims return `503`, not a usable fallback contract.

## Route collisions

WP-1 does not check whether two routes share a method and path. That check is planned in the extension template-routes plan (`iiot-XXXX-extension-template-routes`), not in IIOT-2789.

## Maintenance

WP-1 updated this page when its fields shipped. WP-2 documented enforced generic GET behavior; WP-3 documents generic POST fallback, literal serialization, safe errors and dynamic OpenAPI. WP-4 applies the shared configuration contract to the four deprecated fixed aliases; route-collision checks remain in the separate template-routes plan. Examples and contracts are covered by isolated tests with synthetic schemas and mocked Blob/IoT Hub.
