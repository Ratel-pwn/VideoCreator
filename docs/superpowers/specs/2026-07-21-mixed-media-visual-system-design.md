# Mixed-Media Visual System Design

## Status

Approved in conversation on 2026-07-21. Pending implementation planning.

## Context

The current `资本主义潘多拉魔盒` visual plan requests six video scenes, but
the resolved asset manifest contains seventeen images and no videos. The asset
audit checks that each manifest file matches its own declared type, but it does
not compare that type with the visual plan. A requested video can therefore be
silently replaced with an image and still pass the quality gate.

The current contracts also assume exactly one asset per scene. They cannot
represent either of the two new presentation forms:

1. a fixed object or entity image over a blurred contextual background
2. a Remotion explanatory animation over a blurred contextual background

This iteration adds reusable mixed-media capabilities and then replans and
re-renders the target video.

## Goals

1. Require moving footage for the opening shot.
2. Mix footage and still images according to meaning, without a mechanical
   alternation frequency or percentage quota.
3. Add reusable object/entity cards with a fixed foreground image and a
   primary label plus optional secondary label.
4. Add a reusable Remotion explainer protocol and the first four templates:
   flow, list, quote highlight, and relation/feedback loop.
5. Use blurred contextual media behind entity cards and explainers.
6. Prevent planned video slots from silently resolving to images.
7. Support multiple asset roles per scene and audit each role independently.
8. Record provenance, attribution, retrieval date, known rights data, and a
   rights note for every public web asset.
9. Replan, source, render, and audit `资本主义潘多拉魔盒` with the new system.

## Non-Goals

- image or video generation API calls
- a fixed image/video alternation cadence
- source-video audio in the final composition
- automatic legal clearance of public web footage
- a complete educational animation engine in this iteration
- formula, function graph, music notation, or code templates in the first
  implementation; they receive stable extension points only
- transitions between scenes other than the existing hard cuts

Public visibility does not imply permission to republish. The user accepts
publicly accessible footage as a search scope. The system must preserve known
rights information and visibly warn when rights are unknown or restricted.

## Presentation Modes

Every v2 visual-plan scene selects exactly one `presentation_mode`:

| Mode | Purpose | Required slots |
| --- | --- | --- |
| `footage` | Show motion, change, circulation, environment, or process | `primary` video |
| `still` | Show inspectable evidence, maps, documents, or atmosphere | `primary` image |
| `entity_card` | Introduce a physical item or contextually important broad entity | `background` image/video, `display` image |
| `explainer` | Explain a process, list, quotation, relationship, or feedback loop | `background` image/video, `explainer` config |
| `subtitle_only` | Carry a judgment without external visual competition | no asset slot |

`entity_card` is not limited to a fixed taxonomy. Books, documents, objects,
machines, people, buildings, institutions, and maps may use it when the exact
narration segment benefits from deliberate identification. The planner decides
from topic and sentence purpose.

## Visual Plan V2

The top level gains `schema_version: 2`. Each scene keeps timing, subtitle IDs,
text, brief, visual role, and hard-cut ordering, and adds an explicit mode and
slot requirements.

```json
{
  "schema_version": 2,
  "segment_id": "scene-005",
  "subtitle_segment_ids": ["seg-007", "seg-008"],
  "start_ms": 27840,
  "end_ms": 37600,
  "text": "...",
  "brief": "波兰尼与大转型",
  "presentation_mode": "entity_card",
  "visual_role": "evidential",
  "slots": [
    {
      "role": "background",
      "required_type": "image",
      "search_queries": ["Karl Polanyi archive desk"]
    },
    {
      "role": "display",
      "required_type": "image",
      "search_queries": ["The Great Transformation book cover"]
    }
  ],
  "entity": {
    "primary_label": "《大转型》",
    "secondary_label": "The Great Transformation"
  },
  "explainer": null,
  "transition": "cut",
  "notes": "Use the book as the fixed evidence anchor."
}
```

Rules:

- scene ordering and subtitle coverage remain continuous with no gaps or overlap
- scene count may increase when a long narration scene has multiple semantic
  beats that need different presentations
- the first scene must use `footage`
- `footage.primary.required_type` must be `video`
- `still.primary.required_type` must be `image`
- `entity_card.display.required_type` must be `image`
- `entity.secondary_label` is optional; all other entity fields are required
- `explainer` exists only for `explainer` mode
- no slot may declare an image/video fallback type

## Asset Request And Manifest V2

Asset requests become slot-based. The stable request ID is
`<scene_id>:<role>`, for example `scene-005:display`.

The manifest moves from one record per scene to one record per required slot:

```json
{
  "schema_version": 2,
  "assets": [
    {
      "request_id": "scene-005:display",
      "scene_id": "scene-005",
      "role": "display",
      "asset_type": "image",
      "local_path": "assets/scene-005-display.jpg",
      "source_page_url": "https://example.com/source",
      "direct_download_url": "https://example.com/file.jpg",
      "provider": "Example Provider",
      "license": "unknown",
      "credit": "Creator name if known",
      "retrieved_at": "2026-07-21",
      "rights_status": "unknown",
      "rights_note": "Publicly accessible; republication rights not confirmed.",
      "duration_ms": null,
      "fit_mode": "contain",
      "trim_start_ms": 0,
      "short_video_policy": "reject",
      "review_status": "approved"
    }
  ]
}
```

Allowed `rights_status` values are `public_domain`, `cc`, `licensed`, `unknown`,
and `restricted`. `unknown` and `restricted` produce audit warnings rather than
being silently represented as safe. Missing source, attribution fields, rights
status, rights note, or local file remains an error.

The audit must enforce:

- exactly one approved manifest record for every required slot
- no unexpected duplicate role within a scene
- manifest `asset_type` equals the slot `required_type`
- actual probed media kind equals both values
- the opening `primary` is a video
- video resolution is at least 1280x720
- video duration and configured short-video policy can cover the scene
- every video is rendered muted
- no visible watermark or loading state during human review

If a required video cannot be found, the request remains unresolved. Downgrade
to a still requires an explicit visual-plan edit and a new audit; the resolver
may not make that choice by itself.

## Entity Card Presentation

Entity cards render inside the existing 1700x852 editorial media viewport.

Background layer:

- fills the viewport using a related image or muted video
- applies `blur(22px) brightness(0.48) saturate(0.75)`
- scales to 1.08 so blur does not expose edges
- receives a restrained dark scrim for stable contrast

Foreground layer:

- uses one fixed image for the full scene; no pan, scale, or parallax
- fits inside a clean matte panel with contain sizing
- keeps the object/entity visually dominant without covering subtitles
- does not attempt automatic background removal

Caption:

- appears near the upper-left of the media viewport
- uses a vertical accent rule, large primary label, and smaller optional
  secondary label
- remains separate from narration subtitles
- never includes source or licensing prose in the picture

## Explainer Presentation

Explainers use the same blurred-background treatment as entity cards. A
centered translucent teaching surface sits above the background and reserves
the lower subtitle-safe band.

All animations are deterministic functions of Remotion frame and fps. They do
not use timers, random values, CSS transitions, or runtime network calls.

The v1 registry is:

```ts
type ExplainerConfig =
  | {kind: 'flow'; title?: string; nodes: Node[]; edges: Edge[]}
  | {kind: 'list'; title?: string; items: ListItem[]}
  | {kind: 'quote_highlight'; source?: string; lines: QuoteLine[]}
  | {kind: 'relation_loop'; title?: string; nodes: Node[]; edges: Edge[]};
```

Template behavior:

- `flow`: nodes reveal in order and connector strokes draw between them
- `list`: items enter in a readable stagger with one active emphasis state
- `quote_highlight`: text reveals by line and selected phrases receive an
  animated hand-drawn-style underline
- `relation_loop`: nodes settle around a loop and directional edges animate to
  show reinforcement or circulation

The registry uses one component per `kind`. Future formula, function, music,
and code components can extend the discriminated union without changing scene
composition or asset contracts.

## Remotion Render Contract

The normalized render scene becomes mode-aware while retaining timing and media
playback fields. Common fields are `id`, `fromFrame`, `durationInFrames`, and
`presentationMode`.

- `footage` and `still` receive `primaryAsset`
- `entity_card` receives `backgroundAsset`, `displayAsset`, and `entity`
- `explainer` receives `backgroundAsset` and `explainer`
- `subtitle_only` receives no asset

`Scene.tsx` becomes a dispatcher only. Focused components own each mode:

- `FullBleedScene`
- `EntityCardScene`
- `ExplainerScene`
- `BlurredBackground`
- `explainers/FlowExplainer`
- `explainers/ListExplainer`
- `explainers/QuoteHighlightExplainer`
- `explainers/RelationLoopExplainer`

All source-video components set `muted`. Hard-cut `Sequence` behavior remains
unchanged.

## Planner And Audit Rules

The reusable visual-planning skill gains these decisions in order:

1. Does motion carry the meaning? Use `footage`.
2. Is the narration deliberately introducing an item or entity? Consider
   `entity_card` using the strict/broad choice that best serves the sentence.
3. Is the narration explaining structure, order, comparison, quotation, or a
   feedback mechanism? Use `explainer`.
4. Is inspectable evidence the main value? Use `still`.
5. Would visuals weaken the judgment? Use `subtitle_only`.

The audit fails an all-image non-text timeline and fails any mismatch between
planned slot type and resolved asset type. It reports, but does not enforce, the
overall image/video ratio because the user explicitly wants irregular cadence.

## Backward Compatibility

Existing v1 plans and manifests continue through the current one-asset render
path and preserve full support for already completed projects. A v2 plan must
use a v2 slot-based manifest and the mode-aware render path. Mixing a v2 plan
with a v1 manifest, or a v1 plan with a v2 manifest, fails validation with an
explicit schema-version error. The target video is migrated fully to v2.

## Target Video Migration

The target is replanned from the final SRT and approved article. The existing 18
scenes are inputs, not fixed boundaries. The expected direction is:

- opening: real moving industrial or financial footage
- historical location and inspectable evidence: selected stills
- enclosure, industrial labor, discipline, globalization, and circulation:
  real footage where public material can be sourced
- `《大转型》`, VOC documentary evidence, Bank of England, Adam Smith, and
  `《资本论》`: entity cards when the exact sentence benefits from identification
- land/identity/community relations, money accumulation, market ideology, and
  land-money-labor-state reprogramming: explainer templates
- closing: either a restrained quote-highlight explainer or subtitle-only hold,
  chosen after reviewing the final pacing

Long scenes may split into multiple hard-cut shots so one 20-28 second segment
does not hold a single image or clip for its entire duration.

## Verification

Automated tests cover:

- visual-plan v2 mode and slot validation
- slot request generation
- exact type matching and unresolved-video failure
- multi-record manifest auditing
- rights warning behavior
- render-contract normalization for every mode
- Zod discriminated unions
- entity card labels and fixed foreground geometry
- muted background and primary video playback
- deterministic output from all four explainer templates
- v1 plans continue through the legacy path and v2 plans reject v1 manifests

Target-video acceptance requires:

- opening frame sequence visibly comes from a video
- footage, stills, entity cards, and explainers all appear where planned
- no planned video is delivered as an image
- object/entity foreground images remain fixed
- all source video audio is muted
- subtitles remain one line with no trailing punctuation
- frame title/date/creator metadata remain correct
- representative frames from every scene pass visual review
- the final MP4 fully decodes and remains H.264/AAC, 1920x1080, 25fps
- source and rights records exist for every downloaded asset

## Failure Handling

- inaccessible or undownloadable public video: leave the slot unresolved and
  continue researching; do not substitute an image automatically
- video too short: reject, or use an explicitly approved loop/freeze policy
- missing display image for entity card: fail the manifest audit
- unsupported explainer kind: fail schema validation before bundling
- unknown rights: retain the asset only with an audit warning and explicit
  rights note
- foreground/background collision with subtitles: fail visual review and adjust
  layout or shot plan before final render
