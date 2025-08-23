# a2a-check

CLI tool to validate and inspect [A2A](https://github.com/a2aproject/A2A) Agent implementations.  
It probes the Agent Card, verifies key spec requirements, and (if available) exercises JSON‑RPC and HTTP+JSON endpoints including streaming (SSE).

> **What this is for**
> - Quick *smoke checks* for A2A servers in local/dev/staging.
> - CI validation gates that fail on spec-critical errors.
> - Human-readable reports with Rich tables and exit codes.

> **About rule IDs**
> The rule IDs you see in the output (e.g., `CARD-001`, `RPC-010`, `REST-URL`) are **internal labels of this CLI** to make results stable and greppable.  
> They **do not come from the A2A specification** itself — each rule maps to one or more normative requirements in the A2A spec.  
> Hinweis: **: kommt nicht von mir** (Regel‑IDs stammen aus diesem CLI, nicht aus der A2A‑Spezifikation).

---

## Installation

Using [uv](https://github.com/astral-sh/uv):

```bash
uv pip install --editable .
# exposes the CLI as "a2a-check"
```

Optional, if you want to run the built-in dummy server:

```bash
uv pip install fastapi uvicorn sse-starlette
```

---

## Quickstart

Validate the full suite (network, schema, card, JSON‑RPC, REST if declared):

```bash
a2a-check suite all https://your-agent.example.com
```

Fetch and validate the Agent Card explicitly:

```bash
a2a-check card fetch https://your-agent.example.com
# or, if your card is not at the well-known path:
a2a-check card fetch https://your-agent.example.com --card-url https://your-agent.example.com/.well-known/agent-card.json
```

Only probe networking + content type + JSON parse of the card:

```bash
a2a-check net probe https://your-agent.example.com
```

Ping a JSON‑RPC URL directly (no streaming in this command):

```bash
a2a-check rpc ping https://your-agent.example.com/a2a/v1
```

Resolve JSON‑RPC URL from the Agent Card and ping it:

```bash
a2a-check rpc ping-from-card https://your-agent.example.com
```

Check JSON‑RPC streaming (SSE):

```bash
a2a-check rpc stream https://your-agent.example.com/a2a/v1
```

Start the built-in Hello‑World dummy (JSON‑RPC + SSE):

```bash
a2a-check start_dummy --host 127.0.0.1 --port 9999 --mode ok
# Card: http://127.0.0.1:9999/.well-known/agent-card.json
# JSON-RPC: http://127.0.0.1:9999/a2a/v1
```

Start it **intentionally broken** to demo failures in the suite:

```bash
# errors only (no warnings):
a2a-check start_dummy --host 127.0.0.1 --port 9999 --mode errors

# warnings AND errors:
a2a-check start_dummy --host 127.0.0.1 --port 9999 --mode mixed
```

> Backwards compatibility: `--wrong` is deprecated and maps to `--mode errors`.

---

## Commands & semantics

### `net probe`
Network and HTTP-level checks for the Agent Card endpoint:
- Reachability of origin and card URL
- HTTP status
- Content-Type is JSON
- Body parses as JSON

### `card fetch`
Fetches the card and runs:
- **Schema** validation against `a2a.types.AgentCard`
- **CardChecks** (spec-oriented rules), e.g.:
  - Required fields: `protocolVersion`, `name`, `description`, `url`, `version`, `defaultInputModes`, `defaultOutputModes`
  - Transport declarations (`preferredTransport`, `additionalInterfaces`) are consistent and non-conflicting
  - Skill hygiene (ids unique, non-empty descriptions, tags present)
  - Security references are consistent with `securitySchemes`
  - Optional: `provider`, `iconUrl`, `supportsAuthenticatedExtendedCard` sanity checks

### `card validate`
Alias for `card fetch` (kept for convenience).

### `rpc ping`
Calls `message/send` on the given JSON‑RPC URL, checks:
- JSON-RPC envelope
- Content-Type
- `Task` or `Message` response
- `tasks/get` + `tasks/cancel` follow-ups if a Task id was returned
- Push notification API (`tasks/pushNotificationConfig/*`) — tolerated if not supported
- `agent/getAuthenticatedExtendedCard` — expects HTTP 401/403 without a token, validates `AgentCard` if authorized

> **Auth**: Use `--auth-bearer` to send an `Authorization: Bearer <token>` header.

### `rpc ping-from-card`
Resolves the JSON‑RPC URL from the Agent Card and runs `rpc ping`.

### `rpc stream`
Exercises `message/stream` SSE and attempts `tasks/resubscribe` using the observed task id.

### `suite all`
End-to-end:
- `net probe` + schema parse
- `CardChecks`
- `JSON‑RPC` checks (if JSON‑RPC interface is declared)
- `HTTP+JSON` checks (if REST interface is declared)

---

## Options

Common flags (available on most commands):

- `--timeout FLOAT` (default `8.0`): HTTP timeout (seconds)
- `--stream-timeout FLOAT` (default `12.0`): SSE read window
- `--insecure`: disable TLS verification (testing only)
- `--auth-bearer TOKEN`: send `Authorization: Bearer <TOKEN>`
- `--well-known-path PATH` (default `/.well-known/agent-card.json`)
- `--fail-on-warn`: make warnings fail CI with a distinct exit code (see below)

---

## Exit codes

- `0` – no spec‑critical errors (`ERROR`) detected
- `1` – at least one `ERROR` result in any section
- `2` – no `ERROR`s, but at least one `WARN` **and** `--fail-on-warn` is set

> Warnings do **not** affect the exit code unless `--fail-on-warn` is used.

---

## Transport discovery

- If `preferredTransport == "JSONRPC"`, the Card’s `url` is used for JSON‑RPC.
- Otherwise, `additionalInterfaces` is scanned for the matching transport.
- For REST (`HTTP+JSON`), the base URL is used and the suite calls:
  - `POST {base}/message:send`
  - `POST {base}/message:stream` (SSE)
  - `GET {base}/tasks/{id}`
  - `POST {base}/tasks/{id}:cancel`

---

## CI usage

Typical pattern (fail on spec-critical errors):

```bash
a2a-check suite all https://your-agent.example.com
```

For authenticated extended cards:

```bash
a2a-check suite all https://your-agent.example.com --auth-bearer "$A2A_TOKEN"
```

Making warnings fail a CI job while treating errors as usual:

```bash
a2a-check suite all https://your-agent.example.com --fail-on-warn
```

---

## Notes

- The tool relies on the canonical `a2a.types` models (Pydantic v2). Ensure your server follows the 0.3.x spec (or `dev`) field naming and structures.
- Streaming is validated via SSE (`text/event-stream`) for JSON‑RPC `message/stream` and REST `message:stream`.
- Push notifications are *optional*; a compliant agent may return `-32003` when not supported.

---

## Dummy server modes

Use the embedded dummy to quickly see various outcomes:

| Mode   | Card validity | Typical results                          |
|--------|----------------|------------------------------------------|
| `ok`   | Valid          | All green, exit `0`                      |
| `errors` | Invalid     | Spec errors, exit `1`                    |
| `mixed`  | Mixed       | Warnings **and** errors, exit `1`        |

> The shipped dummy does not include a "warnings‑only" mode. To see exit code `2`, run against a server that is schema‑correct but violates only warning‑level checks, and pass `--fail-on-warn`.

---

## Rules reference (CLI-owned rule IDs)

> These rule IDs are **defined by this CLI**, not by the A2A spec.  
> Each rule references the relevant part(s) of the spec so you can trace failures.

### Network / HTTP / JSON parsing

| Rule ID    | Purpose                                               | Typical fail trigger                               | Spec mapping (informal) |
|------------|-------------------------------------------------------|----------------------------------------------------|-------------------------|
| `NET-001`  | Origin reachable                                      | TCP/connect fails; no HTTP response                | Transport §3.1          |
| `URL-001`  | Card endpoint reachable                               | Card URL not reachable or returns non-HTTP         | Discovery §5.2‑5.3      |
| `HTTP-200` | Card endpoint returns HTTP 200                        | Non‑200 status code                                | —                       |
| `HTTP-CT`  | Card response has `Content-Type: application/json`    | Wrong content type                                 | Transport §3.2.1        |
| `JSON-001` | Body parses as JSON                                   | Malformed JSON                                     | JSON‑RPC/JSON §6.11     |

### Schema (Agent Card typing)

| Rule ID          | Purpose                                             | Typical fail trigger                                                    | Spec mapping |
|------------------|-----------------------------------------------------|--------------------------------------------------------------------------|--------------|
| `CARD-STRUCT`    | Agent Card conforms to canonical schema             | Pydantic validation errors (missing/typed fields)                        | §5.5 AgentCard Object Structure |
| `CARD-TR-STRUCT` | Card not parsed; follow-up checks limited/skip      | `CARD-STRUCT` failed, downstream checks report reduced info              | — (tooling behavior) |

### Agent Card content checks

| Rule ID     | Purpose / expectation                                                                              | Typical fail trigger                                          | Spec mapping |
|-------------|-----------------------------------------------------------------------------------------------------|----------------------------------------------------------------|--------------|
| `CARD-001`  | `protocolVersion` present                                                                          | Missing field                                                  | §5.5 `protocolVersion` |
| `CARD-001a` | `protocolVersion` acceptable (`0.3.x` or `dev`)                                                    | Unsupported version                                            | Version header + §5.5  |
| `CARD-002`  | `name` present                                                                                      | Missing                                                        | §5.5 `name` |
| `CARD-004`  | `description` present                                                                               | Missing                                                        | §5.5 `description` |
| `CARD-003`  | `url` present                                                                                       | Missing                                                        | §5.6.1 Main URL requirement |
| `CARD-005`  | `version` present                                                                                   | Missing                                                        | §5.5 `version` |
| `CARD-005a` | `version` looks semver-like                                                                         | Non‑semver string (e.g. `1.0`)                                | — (best practice) |
| `CARD-006`  | `defaultInputModes` present and non-empty                                                           | Missing or empty                                               | §5.5 `defaultInputModes` |
| `CARD-007`  | `defaultOutputModes` present and non-empty                                                          | Missing or empty                                               | §5.5 `defaultOutputModes` |
| `CARD-010`  | `preferredTransport` present                                                                        | Missing                                                        | §5.6.1 Required declaration |
| `CARD-011`  | `preferredTransport` matches a declared interface                                                   | Main `url`/transport mismatch                                  | §5.6 Transport declaration and URL relationships |
| `CARD-012`  | No transport conflicts across `additionalInterfaces`                                                | Conflicting transports for same URL                            | §5.6.4 Validation requirements |
| `CARD-013`  | At least one transport declared                                                                     | No preferred/extra interfaces                                  | §11.1.1 + §5.6.4 |
| `CARD-016`  | Transports use standard values (`JSONRPC`, `GRPC`, `HTTP+JSON`)                                     | Unknown/non-standard value                                     | §5.5.5 TransportProtocol |
| `CARD-020`  | `capabilities.streaming` is boolean or absent                                                       | Wrong type (e.g., `"yes"`)                                     | §5.5.2 `AgentCapabilities.streaming?: boolean` |
| `CARD-021`  | `capabilities.pushNotifications` boolean or absent                                                  | Wrong type                                                     | §5.5.2 `pushNotifications?: boolean` |
| `CARD-022`  | `capabilities.stateTransitionHistory` boolean or absent                                             | Wrong type                                                     | §5.5.2 `stateTransitionHistory?: boolean` |
| `CARD-023`  | `capabilities.extensions[]` entries are well-formed                                                 | Missing required `uri` or invalid item                         | §5.5.2 `extensions?: Extension[]` |
| `CARD-030`  | `skills` present                                                                                    | Missing array                                                  | §5.5 `skills` |
| `CARD-031`  | Skill ids are unique                                                                                | Duplicate `skills[].id`                                        | §5.5.4 `AgentSkill.id` (unique by implication) |
| `CARD-032`  | Each skill has a non-empty `description`                                                            | Missing/empty description                                      | §5.5.4 `AgentSkill.description` |
| `CARD-033`  | Each skill has non-empty `tags`                                                                     | Missing/empty tags                                             | §5.5.4 `AgentSkill.tags` |
| `CARD-040`  | `security` is optional and may be absent                                                            | —                                                              | §5.5 `security?` (optional) |
| `CARD-043`  | `supportsAuthenticatedExtendedCard=true` implies declared `securitySchemes`                         | Flag set without security schemes                              | §5.5 `supportsAuthenticatedExtendedCard` + §6 Security |
| `CARD-051`  | `iconUrl` looks like a valid HTTP(S) URL                                                            | Non‑HTTP URL (e.g., `ftp://...`)                               | — (best practice) |

### JSON‑RPC checks

| Rule ID     | Purpose                                                                                     | Typical fail trigger                                           | Spec mapping |
|-------------|---------------------------------------------------------------------------------------------|-----------------------------------------------------------------|--------------|
| `RPC-001`   | Unknown method is rejected with JSON‑RPC error `-32601`                                     | Wrong code/format                                               | §8.1 JSON‑RPC `-32601` |
| `RPC-010`   | `message/send` returns a `Task` (or `Message`) with valid envelope                           | Wrong result type or invalid envelope                           | §7.1 message/send |
| `RPC-011`   | JSON‑RPC responses use `Content-Type: application/json`                                      | Wrong content type                                              | §3.2.1 JSON‑RPC transport |
| `RPC-020`   | `tasks/get` returns a `Task`                                                                 | Wrong shape/type                                                | §7.3 tasks/get |
| `RPC-021`   | Non-cancelable tasks return `-32002`                                                         | Wrong error code                                                | §8.2 `TaskNotCancelableError` |
| `RPC-030`   | `message/stream` produces SSE events                                                         | No events / wrong content type                                  | §3.3.1 JSON‑RPC Streaming |
| `RPC-032`   | `tasks/resubscribe` yields further SSE events                                                | No events                                                       | §7.9 tasks/resubscribe |
| `RPC-040`   | Push notification APIs not supported are rejected (accepted: `-32003` or `-32601`)          | Returns success despite not supporting; or wrong error format   | §8.2 `PushNotificationNotSupportedError` (fallback: not implemented) |
| `RPC-050`   | Authenticated extended card not declared → checks skipped (informational)                    | —                                                               | §7.10 presence of `supportsAuthenticatedExtendedCard` |

**Standalone command rule (used by `rpc stream`):**

| Rule ID       | Purpose                                     | Typical fail trigger               |
|---------------|---------------------------------------------|------------------------------------|
| `RPC-STREAM`  | Standalone SSE check (`rpc stream` command) | No events or stream error/timeout  |

### HTTP+JSON (REST) checks

| Rule ID     | Purpose                                                     | Typical behavior                                         | Spec mapping |
|-------------|-------------------------------------------------------------|----------------------------------------------------------|--------------|
| `REST-URL`  | If no REST interface is declared, REST checks are skipped   | Informational skip when only JSON‑RPC/GRPC are declared  | §3.2.3 + §5.6 declaration rules |

> Additional REST rules may be reported when a REST interface is declared (e.g., status codes, content types for `message:send`, `message:stream` SSE, `tasks/{id}`, etc.). Their IDs follow the same naming style and map to §3.2.3 and §7.* endpoints.

---

## Contributing

- Keep rule IDs stable and documented above.
- When adding a new check, document the spec clause and provide a concise, greppable message.
- Prefer **ERROR** severity for spec violations; use **WARN/INFO** for optional features or best practices.
