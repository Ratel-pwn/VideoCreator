# Implementation Roadmap

## Phase 0: Design Freeze

Goal:
- stabilize skill boundaries before implementation

Tasks:
- confirm the first batch of reusable skills
- confirm their input/output contracts
- confirm where script logic should live versus where prompt logic should live

Deliverable:
- approved skill boundary map

## Phase 1: Core Subtitle Infrastructure

Goal:
- make subtitle processing reusable across all video scenarios

Implement:
- `subtitle-segmentation`
- `subtitle-layout-audit`

Requirements:
- one-line preference by ratio
- split before visual wrap when possible
- split orphan words / tiny trailing clauses into balanced segments
- no trailing punctuation
- avoid flicker-like ultra-short segments

Deliverable:
- reusable subtitle rules and supporting scripts or script stubs

## Phase 2: TTS Infrastructure

Goal:
- make voice generation safe and cost-controlled

Implement:
- `tts-cache-guard`
- `tts-align-srt`

Requirements:
- reuse existing voice output by default
- do not regenerate paid cloned voice unless script materially changes
- treat aligned subtitle output as the primary timing source

Deliverable:
- reusable TTS wrapper policy and aligned subtitle pipeline

## Phase 3: Audit And Packaging

Goal:
- make output quality and final project structure reusable

Implement:
- `visual-audit`
- `cover-generator`
- `metadata-generator`
- `project-packager`

Requirements:
- pre-render and post-render audit
- source capture cleanliness checks
- cover ratios
- publishing metadata
- cleanup rules that preserve source assets while removing disposable intermediate versions

Deliverable:
- reusable output-quality and packaging layer

## Phase 4: Scenario Writing

Goal:
- separate copy generation by scenario while keeping interface compatibility

Implement:
- `script-product-intro`
- `script-science-explainer`
- `script-ai-daily`

Requirements:
- same input contract style
- different rhetorical structure and output expectations by scenario

Deliverable:
- first full set of scenario-specific writing skills

## Phase 5: Scenario Visual Planning

Goal:
- separate visual planning by scenario

Implement:
- `scene-planner-product-intro`
- `scene-planner-science-explainer`
- `scene-planner-ai-daily`

Deliverable:
- scenario-appropriate shot planning layer

## Phase 6: Orchestration

Goal:
- compose the reusable parts into one controllable application flow

Implement:
- `workflow-orchestrator`
- scenario skills

Deliverable:
- one entrypoint that chooses scenario and dispatches reusable skills

## Recommended First Implementation Order

1. subtitle-segmentation
2. tts-cache-guard
3. tts-align-srt
4. visual-audit
5. project-packager
6. script-product-intro
7. script-science-explainer
8. workflow-orchestrator
