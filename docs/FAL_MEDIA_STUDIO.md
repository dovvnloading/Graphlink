# Fal Media Studio

A first-class **canvas node** for image, video and audio generation with Fal.
The Studio is designed for both one-off creations and a longer creative
workflow: browse → compare cost → configure → confirm → monitor → review.

## Launch

1. Configure your Fal.ai key in **Settings → Fal.ai**, or set
   `FAL_KEY` / `GRAPHLINK_FAL_API_KEY` in the backend environment.
2. Open Graphlink's **plugin picker** and select **Fal Media Studio** under
   **Build & Execution**. No node selection is required; the Studio can be
   added to an empty canvas, or connected to an existing parent branch.
3. Choose **Image**, **Video**, or **Audio**, and the kind of generation:
   text-to-image, image-to-image, text-to-video, image-to-video,
   video-to-video, text-to-audio, text-to-speech, or audio-to-audio.
4. Search the live Fal catalog. Model cards expose **verified live unit
   prices** (and their billing units), descriptions, and the original
   endpoint ID. Save models with ☆. Favorites persist between sessions.
5. Select a model to load its OpenAPI-backed parameter schema. Fill in the
   prompt and other visible controls, or expand **Advanced model JSON** for
   full, model-specific input fields (seeds, resolution, duration, image
   reference URLs and anything else Fal supports).
6. Select **Review cost & generate**. The server checks live pricing and
   asks Fal for a per-request estimate. There is no billable call yet.
7. Select **Confirm & generate**. This is the ONE user-approved, queue-backed
   generation. A new confirmation is required for every repeat request.
8. Monitor queue status automatically or choose **Check status**. The
   Studio stores the job ID, so sessions can resume tracking it after
   restart. You can request cancellation (not guaranteed if the request
   already started). Completed jobs appear in the output gallery.

## Price ranking — economy to premium

**Economy first** is the default sort. Fal can charge by image, by
megapixel, by video second, by audio duration, by character, and other
units. A raw price like `$0.01` is *not comparable* without its unit.

Therefore the Studio explicitly groups models by
`currency / billing-unit` and sorts from cheapest to most expensive
**within each comparable group**. Models without a verifiable current
price appear last, never as free. The user can switch to Premium first
or Name A–Z. Prices and rate displays are refreshed from Fal's actual
catalog and pricing APIs; neither prices nor model IDs are hardcoded.

The quote includes two distinct numbers where Fal returns them:
a live rate per model billing unit and a nonbinding estimated cost for one
request. An estimate is **not** a spending limit or guaranteed bill.
Longer videos or higher resolutions can incur higher final costs.

## Image, video and audio previews

- Images are shown through Graphlink's **authenticated same-origin
  proxy**, which fetches only completed Fal CDN URLs from a particular
  Studio node's own output history. This allows previews without
  weakening Graphlink's existing `img-src 'self' data:` CSP. The proxy
  enforces a Fal-owned CDN hostname allowlist, HTTPS, redirect blocking,
  allowed raster MIME types and a 20 MiB maximum.
- Videos use the native HTML5 player with playback controls.
- Audio uses the native HTML5 player with seek/pause controls.
- The original HTTPS media links are provided for opening the file
  separately. Remote URL expiry and media browser compatibility are
  governed by Fal and the model, not Graphlink.
- No user content or binary media is put in the scene's JSON wire payload.

Studio history stores up to 12 previous generations, with a bounded set
of output links. Inputs, selected model, and queue IDs are saved
with the graph. API key values are never saved in nodes or sent to
the browser. Catalog pages and model schema caches are transient; they
are fetched again instead of persisting stale prices.

## Reliability and billing safety

- A submission can occur **only after** a server-issued review token tied
  to the exact model + full JSON arguments. The token expires after three
  minutes and is consumed before submitting to the Fal API.
- While a job is running, the node refuses edits that could change the
  model or input identity of that active job.
- The app **never auto-retries a paid submission** on reload. If an
  interruption happens before it receives the remote request ID, it shows
  an explicit warning to check the Fal dashboard before resubmitting.
- Queue-status polling, manual refresh, and cancellation are not billable
  inference calls. Requests continue remotely if the desktop window is
  closed, and may incur usage charges even if cancellation is attempted.
- If the Studio node is removed while the remote request completes,
  the Fal queue ID is still kept in Graphlink's Settings-level Fal job
  history once the submit call returns.
- Confirmation is per-generation; a previous confirmation can't be
  replayed to create repeat charges.

## Current scope

The Studio covers Fal-hosted inference endpoints surfaced by the public
Model API, with schema-driven inputs, image/video/audio generation and
persistent results. It is not a dedicated visual editor for the parameter
schema of *every* current and future model; models with complex JSON
parameters can be used through the advanced editor. Local media
attachments can be uploaded using Settings → Fal.ai and their hosted
URLs supplied to the Studio's `image_url`/`video_url`/`audio_url`
arguments where required.

The Studio does not replace Graphlink's chat provider, the dedicated
Image Node, Fal's account administration tools or Fal serverless
deployment management. No real paid Fal request is made by tests.

See also [Fal integration reference](FAL_AI.md).

Fal API references:
- [Model catalog](https://fal.ai/docs/platform-apis/v1/models)
- [Current model pricing](https://fal.ai/docs/platform-apis/v1/models/pricing)
- [Cost estimate](https://fal.ai/docs/platform-apis/v1/models/pricing/estimate)
- [Submit to queue](https://fal.ai/docs/api-reference/inference-api/queue/submit)
- [Queue status](https://fal.ai/docs/api-reference/inference-api/queue/status)
- [Queue cancellation](https://fal.ai/docs/api-reference/inference-api/queue/cancel)
