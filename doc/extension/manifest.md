# Extension manifest: module schemas and response references

> **Status: WP-1 implemented (IIOT-2789).** `POST /extensions` and `PUT /extensions/{name}` accept `module_schemas` and `response_ref`, resolve and persist their schemas, and activate them on enable and at startup. Manifests without these fields are unchanged (`schema_version: 1`). **Planned, not yet enforced:** the generic configuration and reported GET and the generic configuration POST do not validate against these schemas, and do not return 503 for an unavailable module, until IIOT-2789 WP-2 and WP-3 ship. Until then, `module_schemas` is registration and lifecycle only.

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
| `schema_ref` | yes | Local component in that upstream's OpenAPI document, for configuration. Enforcement on `GET` and `POST /{device}/twin/config/{module}` is planned (WP-2/WP-3). |
| `reported_schema_ref` | no | Local component for the filtered response of `GET /{device}/twin/identity/{module}/reported`. Enforcement is planned (WP-2). Without it, the generic reported response applies. |

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
| Enabled, but a module's stored snapshot cannot be activated | The module ID stays claimed and gets no active schema. Planned: configuration and reported calls for that module return 503 and never fall back to the generic response (WP-2/WP-3). |
| Replace without `reported_schema_ref` | Removes the reported snapshot. The module returns to the generic reported response. The core route stays mounted. |
| Disable | Removes the module's active schemas. The module ID stays claimed by the disabled extension, so another extension still cannot register it. |
| Deregister | Removes the module's active schemas and its claim. The module ID becomes available again. |

## Runtime responses for a declared module (planned)

These rules are enforced by the generic routes in WP-2 and WP-3. They are not active yet.

| Situation | Planned response |
|---|---|
| `POST` configuration violates `schema_ref` | 422 with the validation errors. |
| Stored configuration violates `schema_ref` | Safe server error. The invalid data is not returned. |
| Reported data violates `reported_schema_ref` | Safe server error. The generic response is not substituted. |
| Module ID claimed but unavailable | 503. |
| No `reported_schema_ref`, or the module is not declared | Generic reported response, as today. |

## Route collisions

WP-1 does not check whether two routes share a method and path. That check is planned in the extension template-routes plan (`iiot-XXXX-extension-template-routes`), not in IIOT-2789.

## Maintenance

WP-1 updated this page when its fields shipped. WP-2 and WP-3 must replace the "planned" statements above with the enforced behavior when their generic routes ship, and check every example against the running API.
