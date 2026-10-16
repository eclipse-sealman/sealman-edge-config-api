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

## Complete neutral test example

The combined integration fixture serves these FastAPI/Pydantic-generated components
and a non-template `/probe` on loopback HTTP. This is a test-only example, not a
production provider or protocol schema. Replace the URL with the provider's
allowlisted HTTP upstream; the integration test substitutes an ephemeral port.
The `required_action` is enrolled by registration but is **not granted** to callers.

```json
{
  "schema_version": 1,
  "name": "neutral_example",
  "description": "Neutral combined lifecycle fixture",
  "upstreams": {
    "svc": {"type": "http", "base_url": "http://127.0.0.1:9000"}
  },
  "actions": [
    {"name": "neutral_example.probe", "description": "Independent probe permission"}
  ],
  "routes": [
    {
      "upstream": "svc",
      "path": "/neutral_example/probe",
      "upstream_path": "/probe",
      "method": "GET",
      "summary": "Neutral probe",
      "response_ref": true,
      "required_action": "neutral_example.probe",
      "query_params": [{"name": "sample", "type": "string", "required": true}]
    }
  ],
  "module_schemas": {
    "neutral-module": {
      "upstream": "svc",
      "schema_ref": "#/components/schemas/NeutralConfig",
      "reported_schema_ref": "#/components/schemas/NeutralReported"
    }
  }
}
```

`GET /extensions/schema` is the authenticated registration JSON Schema, not an
OpenAPI document or a provider schema. The combined test validates this example
against it, registers/enables it through management HTTP, and checks runtime and
OpenAPI behavior. Module operations use persisted snapshots; only the probe
proxies to the provider. Its response is relayed unchanged even if it does not
match the documented `response_ref`.

## Provider-declared template routes (IIOT-2916 feature-branch contract)

> This section describes the reusable IIOT-2916 contract implemented on the
> `feat/module-template` feature branch. It is not a claim that the behavior is
> present on the `d29cc96` base or deployed API. The core does not add a
> template-specific manifest field, router, catalog or provider implementation.

A provider that owns a template catalog declares only the operations it serves.
Use concrete extension routes: `GET /{extension_name}/template/list` for the
raw JSON string-array of names, and `GET /{extension_name}/template` for the
provider's content document (including provider metadata and placeholders).
That content is not a final module configuration. Optional `POST` and `DELETE`
use the same content path only when the provider implements create/delete; a
read-only catalog simply omits them. Do not publish placeholder writes merely
because a different provider supports them.

Use public, unscoped routes (`visibility: "public"`, `scoped: false`, the
manifest default) and a distinct extension action per operation. The action is
registered into the existing global action catalog, but registration does not
add it to any role. Existing role assignment remains the only way to grant it.
The core does not create device scopes or couple template permissions to module
or twin operations.

Each content, write and delete route declares `template_name` as a required
`query_params` entry. It is query metadata, not part of `path` or
`upstream_path`; the existing HTTP proxy forwards the supplied query value,
including encoded Unicode and punctuation. A missing required query parameter
uses the API's existing request-validation response (400).

The provider may map the public route to its own namespaced HTTP handler. For
example, `/{extension_name}/template/list` can map to that provider's
`/{extension_name}/template/list`, while GET/POST/DELETE content operations map
to `/{extension_name}/template`. The service keeps the template catalog and any
provider credentials; the edge API only dispatches the declared operation.

The following read-only manifest is a complete schema-shaped example. The URL
is illustrative loopback HTTP; a deployment must use its configured allowlist.

```json
{
  "schema_version": 1,
  "name": "neutral_catalog",
  "description": "Read-only neutral template catalog",
  "upstreams": {
    "catalog": {"type": "http", "base_url": "http://127.0.0.1:9000"}
  },
  "actions": [
    {"name": "neutral_catalog.templates.list", "description": "List templates"},
    {"name": "neutral_catalog.templates.read", "description": "Read template content"}
  ],
  "routes": [
    {
      "upstream": "catalog",
      "path": "/neutral_catalog/template/list",
      "upstream_path": "/neutral_catalog/template/list",
      "method": "GET",
      "summary": "List neutral templates",
      "response_ref": true,
      "required_action": "neutral_catalog.templates.list"
    },
    {
      "upstream": "catalog",
      "path": "/neutral_catalog/template",
      "upstream_path": "/neutral_catalog/template",
      "method": "GET",
      "summary": "Read neutral template content",
      "query_params": [
        {"name": "template_name", "type": "string", "required": true}
      ],
      "response_ref": true,
      "required_action": "neutral_catalog.templates.read"
    }
  ]
}
```

A full-CRUD provider adds only the write/delete operations it implements. Its
OpenAPI document declares the request model (including nested local references)
and responses; no request schema is copied into the manifest:

```json
{
  "schema_version": 1,
  "name": "neutral_editor",
  "description": "Neutral catalog with provider-owned create and delete",
  "upstreams": {
    "catalog": {"type": "http", "base_url": "http://127.0.0.1:9000"}
  },
  "actions": [
    {"name": "neutral_editor.templates.list", "description": "List templates"},
    {"name": "neutral_editor.templates.read", "description": "Read template content"},
    {"name": "neutral_editor.templates.write", "description": "Create a template"},
    {"name": "neutral_editor.templates.delete", "description": "Delete a template"}
  ],
  "routes": [
    {
      "upstream": "catalog",
      "path": "/neutral_editor/template/list",
      "upstream_path": "/neutral_editor/template/list",
      "method": "GET",
      "summary": "List neutral templates",
      "response_ref": true,
      "required_action": "neutral_editor.templates.list"
    },
    {
      "upstream": "catalog",
      "path": "/neutral_editor/template",
      "upstream_path": "/neutral_editor/template",
      "method": "GET",
      "summary": "Read neutral template content",
      "query_params": [
        {"name": "template_name", "type": "string", "required": true}
      ],
      "response_ref": true,
      "required_action": "neutral_editor.templates.read"
    },
    {
      "upstream": "catalog",
      "path": "/neutral_editor/template",
      "upstream_path": "/neutral_editor/template",
      "method": "POST",
      "status_code": 201,
      "summary": "Create neutral template",
      "query_params": [
        {"name": "template_name", "type": "string", "required": true}
      ],
      "body_ref": true,
      "response_ref": true,
      "required_action": "neutral_editor.templates.write"
    },
    {
      "upstream": "catalog",
      "path": "/neutral_editor/template",
      "upstream_path": "/neutral_editor/template",
      "method": "DELETE",
      "summary": "Delete neutral template",
      "query_params": [
        {"name": "template_name", "type": "string", "required": true}
      ],
      "response_ref": true,
      "required_action": "neutral_editor.templates.delete"
    }
  ]
}
```

`body_ref` and `response_ref` reuse the upstream's own OpenAPI document. With
`body_ref`, the provider remains responsible for request validation: the
`upstream_declared` body is streamed through the HTTP proxy and is not buffered
or validated by this core. This differs from a literal manifest `body`, which
uses the existing bounded buffering and JSON Schema validation path. The
upstream's documented response is for OpenAPI only; `response_ref` does not
validate, materialize or rewrite the proxied response. Provider status, body
(including error bodies) and streaming behavior remain the upstream contract.
If the route's OpenAPI response cannot be resolved, the route still registers
without that response schema, matching the existing response-reference policy.

### Active extension route claims

The feature-branch policy applies to every enabled extension route, not only
template paths. A claim is the exact manifest path and uppercase HTTP method
within its visibility app: public and internal apps are independent, and the
same path with a different method is a separate claim. This is exact ownership,
not generic path-pattern overlap detection or hardening of static core routes.

A duplicate route inside one manifest is rejected with 422. A registered but
disabled extension owns no active path. Enabling an extension, or replacing an
enabled extension's manifest, that would duplicate another active claim returns
409 before its saved enabled state/manifest or current routes are changed; the
current owner remains reachable. Self-replacement excludes the extension's
own existing claims. Disabling or deregistering releases its active paths.

At startup, enabled extensions are hydrated in deterministic name order. The
first mounted owner remains active; a conflicting candidate is logged and
skipped without preventing unrelated routes from mounting. Resolve persisted
conflicts by changing/disabling the affected extension, not by relying on
route-registration order. These collision guarantees are part of the IIOT-2916
feature branch contract and are not present on the `d29cc96` base.

`tests/integration/test_extension_templates.py` is the protocol-neutral core
conformance suite, using synthetic providers rather than a production catalog.
The core implementer owns that reusable suite. Each downstream provider's
implementer owns its provider-specific catalog, schema, registration and
conformance evidence against it; passing the neutral suite does not establish a
protocol provider's schema/catalog parity.

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

Save and refresh are different: a replacement whose configuration resolves but
whose reported reference is invalid rejects the entire save, preserving the
manifest, both snapshots, active behavior and OpenAPI. On enable/startup refresh,
each failed purpose retains its last valid snapshot while an independently
successful purpose can advance its version. A syntactically invalid persisted
snapshot is not a usable last-valid contract: if refresh cannot repair it, hydration keeps
the module claimed/unavailable and module operations fail with 503.

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

## Shared registered-method discovery (IIOT-2913)

```http
GET /extensions/{name}/direct-methods?device_id={device_id}
```

This static public endpoint requires a normal caller JWT, **not** `extension.read`
or an internal key. `{name}` is the persisted extension name; `device_id` is a
required, nonempty device-table key. Missing/invalid authentication, including a
token without `oid`/`sub`, retains the public application's existing `401`.
Discovery's own missing caller identifier or unresolved persisted user is `403`.
Unknown extension/device is `404`; missing/empty or repeated `device_id` is `400`.
Normal user provisioning remains unchanged.

The typed response contains `extension`, `device_id`, `enabled`, and `methods`.
A disabled registration returns `enabled: false, methods: []`; an enabled
registration with no permitted bindings returns `enabled: true, methods: []`.
Unknown/deregistered registrations have no catalog fallback.

Only enabled persisted PUBLIC routes on an `iotedge` upstream with operation
`direct_method` and a method name are considered. Each binding uses its actual
invocation action and device-scope policy via the existing permission evaluator.
Scoped actions use the requested device's current metadata; unscoped actions do
not use device scope. A persisted public binding without an action mirrors the
runtime's no-action policy. This does **not** relax registration validation: new
public IoT Edge bindings must remain scoped and action-bound.

Methods are grouped by exact `(module_id, method_name)`, sorted by those values.
All authorized aliases are retained, sorted by path and HTTP verb. Each invocation
contains its actual `path`, `http_method`, summary/description, sorted
`query_params` (including the runtime's implicit target parameters), `scoped`,
`scope_param`, `scope_in`, and `request_schema`. The path template retains path
parameters. Unscoped placement and absent bodies are explicit JSON `null`.
Request schemas are returned as persisted, with locally resolvable references
inside the descriptor; defaults are not injected and schemas are not rewritten.

**Approved failure policy:** if an authorized binding's persisted body is not
self-contained (for example a remote or dangling schema reference), discovery
fails with exactly HTTP `500` and
`{"message": "This route's body schema is misconfigured"}`. The check runs only
after permission/device filtering. Unauthorized bindings, other extensions and
disabled registrations cannot trigger it. The server logs only the extension
name and route ID, never schema/body/reference/exception content; the response
contains no route ID or exception text. No reference retrieval is attempted.

There is no caller-filtered cache, manifest/README reread, protocol-specific
inventory or runtime enumeration. Replacement, role/device metadata changes,
enable/disable, deregistration and startup hydration use current persistence.
The generic discovery route remains in OpenAPI when protocol invocation routes
are disabled. HTTP/internal/twin/configuration bindings, upstream URLs, private
keys/credentials, health metadata and route request examples are not projected.
Listing makes no dispatch, health, upstream, schema-service, IoT Hub or Blob call.
It describes registered capabilities, **not** deployment, online status or
successful execution. Action registration still grants no role permissions.

## Maintenance

WP-1 updated this page when its fields shipped. WP-2 documented enforced generic GET behavior; WP-3 documents generic POST fallback, literal serialization, safe errors and dynamic OpenAPI. WP-4 applies the shared configuration contract to the four deprecated fixed aliases. Examples and contracts are covered by isolated tests with synthetic schemas and mocked Blob/IoT Hub. `tests/integration/test_module_schema_lifecycle.py` combines real loopback schema/probe HTTP, management lifecycle, PostgreSQL snapshots, explicit startup hydration, non-admin ABAC, literal POST-to-GET storage parity and both OpenAPI hooks. The IIOT-2916 feature-branch template contract and all-extension route-claim policy are described above and tested by `tests/integration/test_extension_templates.py`; neither is implied to be available on the `d29cc96` base or deployed API. See [testing](../testing.md) for the reproducible commands and isolation boundaries. This does not establish production provider, live-device, cloud or OIDC parity.
