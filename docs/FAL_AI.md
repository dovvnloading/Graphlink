# Fal.ai in Graphlink

Graphlink can call **arbitrary fal.ai Model API endpoints**, including nested paths:
`fal-ai/flux/schnell`, `blackforestlabs/flux-3-max/text-to-video`,
speech/audio endpoints, video generators, image upscalers, 3D models,
custom deployed endpoints, and future models. Fal is a **generation and
inference service**, not a substitute for Graphlink's chat LLM provider.

This integration intentionally avoids an out-of-date hardcoded model list:
the model-specific JSON input/output schema is taken from Fal’s live catalog at <https://fal.ai/docs/platform-apis/v1/models>.

## Live model discovery, schemas and cost checks

Open **Settings > Fal.ai > Live model discovery & pricing**. Search the
actual Fal registry by a keyword and/or category (such as `text-to-video`,
`image-to-image`, `text-to-speech`, `image-to-3d`, `training`).
Select **Use This Model** beside a result to set its exact endpoint ID,
including subpaths. Next-page pagination is supported.

For any selected endpoint, choose **Inspect Selected Model / Schema** to
retrieve its published OpenAPI 3.0 input/output definitions. Choose
**Check Current Pricing** to retrieve current per-billing-unit rates, or
**Estimate Cost** with a quantity and one of Fal's two estimation modes:

- `historical_api_price` estimates cost per API call using past usage.
- `unit_price` estimates cost per billing unit (e.g. image or video second).

These are **read-only requests**. They require no model execution approval
and do not incur inference charges. Pricing and estimation are provisional:
resolution, duration, quality and custom account rates may affect billing.
The Model API itself will validate your actual argument JSON on submission;
the UI intentionally accepts arbitrary model-specific fields rather than
hardcoding a form for one version of a model.

Builder and Workspace Agent can search the public catalog and inspect schemas
before an API key is configured. Account pricing, cost estimates, inference
and remote job access use the configured key. A custom endpoint that Fal
doesn't list can still be run by its exact ID with known arguments.

## Setup

1. Create a Fal API key from <https://fal.ai/dashboard/keys>.
2. Open **Settings > Fal.ai**, paste the key and choose **Save API Key**.
3. Type a model ID and its JSON arguments, then submit to the queue or
   run synchronously.

Alternatively, set `FAL_KEY` or `GRAPHLINK_FAL_API_KEY` in Graphlink's
**server-side** environment; the latter takes precedence. An in-app saved
key takes precedence over both. Only `stored`, `environment` or `none`
crosses the WebSocket settings channel; the raw key does not.

Secrets use the same Windows DPAPI-backed storage as Graphlink's other API
keys. On non-Windows systems where DPAPI is unavailable, Graphlink warns
that secrets may be stored unencrypted in its local session file. Prefer
an environment variable if that is unacceptable.

### Quick example: queue an image generation

Model ID: `fal-ai/flux/schnell`

Input JSON:

```json
{
  "prompt": "A realistic miniature greenhouse built inside a crystal",
  "image_size": "landscape_4_3",
  "num_images": 1
}
```

Options JSON:

```json
{
  "priority": "normal",
  "store_io": false,
  "max_queue_length": 20,
  "object_lifecycle": {"expiration_duration_seconds": 3600}
}
```

Select **Submit to Queue** and confirm the paid operation. Graphlink records
the returned request ID and model ID. Under **Queue jobs**, select **Refresh /
Fetch Result** to retrieve completed JSON and generated media links. Request
IDs and bounded output snapshots persist across restarts.

For fast endpoints, **Run Synchronously** executes `POST https://fal.run/`.
Its last result is shown on the settings page during the current app
session. Prefer the queue for long video/voice/training jobs.

Fal uses status `COMPLETED` for **successful, failed, and cancelled** jobs;
Graphlink examines the reported error and only fetches success outputs.
Cancellation can be requested for a job in progress, but it is
best-effort and may not stop computation or charges.

### Models and endpoint paths

Fal IDs are `owner/app[/nested/route]`; for example,
`blackforestlabs/flux-3-max/text-to-video`.

- Submission posts to
  `https://queue.fal.run/owner/app[/nested/route]`.
- Queue status/result/cancel use **only** `owner/app`, not the nested route:
  `GET /owner/app/requests/<uuid>/status`,
  `GET /owner/app/requests/<uuid>`,
  `PUT /owner/app/requests/<uuid>/cancel`.
- A status SSE endpoint
  `GET /owner/app/requests/<uuid>/status/stream` is supported by the Python
  `FalClient.stream_status()` API. The user-facing job list uses refresh
  buttons rather than holding WebSocket sessions open indefinitely.

All network requests are made by the Python backend, not the React page.
Graphlink never follows arbitrary `response_url` / `status_url` values
that an endpoint supplies. Model IDs, request UUIDs and request options are
validated, and redirects are disabled on credential-bearing calls.

### Inputs: images, video, audio and other media

Each Fal model expects its **own** parameter names (usually `image_url`,
`video_url`, `audio_url`, etc.). The Settings page can read a local file
**you select** into a base64 data URI inside a chosen input parameter (up to
8 MiB). Data URIs increase request size and are unsuitable for large media.

For larger files, install the optional official Fal client:

```bash
pip install -e ".[fal]"
# or: pip install fal-client
```

Type a local file path in **Settings > Fal.ai > Input media** and select
**Upload File to Fal CDN**. The resulting URL is available for copying
into a model's input JSON. This explicitly sends the local file's bytes to
Fal; do not upload secrets or private data without consent.

The Python client also exposes `FalClient.upload_file(path)` and
`FalClient.realtime(model_id)`, powered by the official `fal-client`
optional dependency. Realtime is useful for models exposing the
`/realtime` WebSocket interface; its connection lifetime is owned by
the caller rather than by a persistent Graphlink UI socket.

## Using Fal from Builder or Workspace Agent

Ask the Builder to create something on the canvas, or use Workspace Agent
for a task that involves files in its workspace. For example:

> Find an image-to-3D model that accepts my image URL. Show me the required
> inputs and current pricing. After approval, queue it and put the resulting
> mesh and preview on my canvas.

> Transcribe the recording in this workspace with speaker timestamps.
> Show me the transcript, then look up a speech model that can read it back.

Both agents use the model catalog, schemas, and inference tools below.
Local file upload also requires the workspace file-read scope.

| Tool | Meaning | Approval |
| --- | --- | --- |
| `fal.search_models` | Search live model catalog with pagination | Automatic, if scoped |
| `fal.model_details` | Fetch live endpoint metadata and OpenAPI schema | Automatic, if scoped |
| `fal.model_io` | Inspect inference inputs, final outputs and their referenced definitions, with JSON Pointer and pagination | Automatic, if scoped |
| `fal.list_jobs` | List saved model IDs, request IDs and last known status, optionally filtered by model | Automatic, if scoped |
| `fal.model_pricing` | Check current billing-unit pricing | Automatic, if scoped |
| `fal.estimate_cost` | Request historical/unit price estimate | Automatic, if scoped |
| `fal.submit` | Queue a paid model inference | **Always, every call** |
| `fal.run` | Synchronous paid inference | **Always, every call** |
| `fal.status` | Read status / optional runner logs | Automatic, if scoped |
| `fal.result` | Read the full result or a selected field, including typed asset URLs and their JSON Pointers | Automatic, if scoped |
| `fal.wait` | Wait (bounded poll) and retrieve result | Automatic, if scoped |
| `fal.cancel` | Request cancellation | **Always, every call** |
| `fal.upload` | Upload local file bytes | **Always, every call** |

Each tool requires Graphlink's `provider.call` scope; `fal.upload` also
requires `fs.read`. Even if the Builder is in Autopilot, paid operations
and local file uploads **must** receive a fresh approval. A session grant
cannot silently authorize repeat charges or file exfiltration.

Examples the Builder can execute after approval:

```json
{"model_id": "fal-ai/flux/schnell",
 "arguments": {"prompt": "A product mockup of a blue umbrella"}}
```

```json
{"model_id": "blackforestlabs/flux-3-max/text-to-video",
 "arguments": {"prompt": "A camera orbiting a futuristic observatory"},
 "options": {"priority": "low", "store_io": false}}
```

```json
{"model_id": "fal-ai/flux/schnell",
 "request_id": "764cabcf-b745-4b3e-ae38-1200304cf45b"}
```

### Chaining model outputs

Use `fal.model_io` to inspect the destination model's input names and types.
On `fal.result`, `pointer` selects an exact field such as `/images/0/url`
or `/text`. Large values provide `next_offset` or a list of keys to inspect.
On queued jobs, `fal.list_jobs` can recover the model and request ID after
a restart. Use `fal.status` for the current remote state.

For a second `fal.submit` or `fal.run`, supply static values in
`arguments` and dynamic values in `input_bindings`. Each binding names a
top-level destination field and a source job and pointer. Graphlink fetches
the original value after approval, preserving arrays, objects, numbers,
text and asset URLs. A missing pointer fails before the destination is
submitted. A queued source can be reused while its result is available
from Fal. Synchronous calls have no durable queue ID for this handoff.

To place a queued result on the canvas, use `graph.attach_fal_result` with
its `model_id`, `request_id`, and optionally a `parent_id`. This creates an
undoable Fal Media Studio node from the saved result, or retrieves the
existing remote result if the saved JSON was truncated. Repeating it for
the same destination reuses the node.

For example, after looking up a destination that accepts `image_url`,
the agent can fill that input from the first image of a completed job:

```json
{
  "model_id": "fal-ai/trellis",
  "arguments": {},
  "input_bindings": [{
    "argument": "image_url",
    "model_id": "fal-ai/flux/schnell",
    "request_id": "764cabcf-b745-4b3e-ae38-1200304cf45b",
    "pointer": "/images/0/url"
  }]
}
```

Replace the example request ID with the source job's real ID. Each
billable stage gets its own approval. For work with long transcripts,
training outputs or many files, use the queue so the agent can reopen
the result and inspect individual fields.

## Supported request controls

`options` supports the following non-secret values:

- `start_timeout`: maximum seconds before processing starts (0.1–3600)
- `priority`: `normal` or `low`
- `runner_hint`: same-runner routing hint
- `no_retry`, `disable_fallback`: booleans
- `retry_config`: per-error retry policy JSON (owner apps only)
- `store_io`: whether fal stores request/response history
- `object_lifecycle`: generated media expiry/access preference JSON
- `max_queue_length`: positive queue admission limit (queue only)
- `webhook_url`: public HTTPS completion hook (queue only)

The last two are passed as query parameters, not headers.
Fal may ignore or reject unsupported controls for a particular model.

Disabling `store_io` **does not guarantee zero data retention**: Fal CDN
assets, model-side logs, user-managed webhooks and custom deployments may
have separate retention settings. Use Fal's ZDR guidance for sensitive work.

## Scope, security and limitations

- Fal inference may incur charges; check model-specific rates before running.
- Inputs and generated content are sent to the external Fal service.
- Model arguments are not saved to Graphlink's persistent job ledger.
  Request IDs, status, output URLs and a bounded output JSON snapshot are
  saved in `~/.graphlink/session.dat`; clear that file/history as needed.
- The REST client returns at most 4 MiB of inline JSON output; output media
  should be accessed through generated URLs. Request JSON is limited to
  24 MiB; SDK CDN upload supports larger files.
- The integration covers **Fal Model API inference and queue lifecycle**,
  plus optional CDN upload/realtime primitives. It does not manage Fal
  account billing, API-key administration, deployment creation, private
  endpoint permissions, or the entire Fal Platform API control plane.
- The Settings page currently provides manual refresh, not automatic
  polling. Queued jobs continue on the remote service if Graphlink closes.

Official references:
[submit](https://fal.ai/docs/api-reference/inference-api/queue/submit),
[nested routes](https://fal.ai/docs/api-reference/inference-api/queue/submit-path),
[status](https://fal.ai/docs/api-reference/inference-api/queue/status),
[status stream](https://fal.ai/docs/api-reference/inference-api/queue/status-stream),
[cancel](https://fal.ai/docs/api-reference/inference-api/queue/cancel),
[data retention](https://fal.ai/docs/documentation/model-apis/media-expiration),
[Python SDK](https://fal-ai.github.io/fal/client/fal_client.html),
[Model search API](https://fal.ai/docs/platform-apis/v1/models),
[Model pricing API](https://fal.ai/docs/platform-apis/v1/models/pricing),
[Cost estimate API](https://fal.ai/docs/platform-apis/v1/models/pricing/estimate).
