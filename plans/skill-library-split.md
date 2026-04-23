# Skill Library Split

## Source Projects

This split plan uses two existing projects as references only:

- `ChaosMuseum`
  - strengths: topic preparation, article generation, TTS, subtitle alignment, semantic visual planning, asset lookup workflow, pipeline orchestration
- `remotion-demo`
  - strengths: polished product-intro video rules, Remotion composition patterns, subtitle presentation rules, visual audit rules, packaging rules, cover and metadata rules

## Core Principle

Do not merge by project.
Merge by responsibility.

The reusable unit is not "the ChaosMuseum workflow" or "the Remotion project intro workflow".
The reusable unit is a stable capability with a clear contract.

## Skill Layers

### 1. Core Skills

Cross-scenario infrastructure that should be reusable for nearly all video types:

- `source-material-verifier`
- `subtitle-segmentation`
- `subtitle-layout-audit`
- `tts-cache-guard`
- `tts-align-srt`
- `visual-audit`
- `cover-generator`
- `metadata-generator`
- `project-packager`

### 2. Writing Skills

Scenario-specific copy generation:

- `script-product-intro`
- `script-science-explainer`
- `script-ai-daily`

These should share interface shape, but not force the same writing logic.

### 3. Visual Planning Skills

Scenario-specific shot and asset planning:

- `scene-planner-product-intro`
- `scene-planner-science-explainer`
- `scene-planner-ai-daily`

### 4. Scenario Skills

Thin composition skills that define which reusable skills are invoked and with what constraints:

- `scenario-product-intro`
- `scenario-science-explainer`
- `scenario-ai-daily`

### 5. Orchestration Skill

- `workflow-orchestrator`

This should coordinate stages only.
It should not absorb writing, subtitle, or visual logic internally.

## High-Priority Extraction Targets

These should be implemented first because they are high-value and broadly reusable:

1. subtitle segmentation
2. TTS cache guard
3. aligned subtitle generation
4. visual audit
5. project packaging

## Contract Direction

Each reusable skill should define:

- input artifacts
- output artifacts
- validation rules
- failure conditions
- dependencies on scripts or other skills

## Separation Rules

### Keep in core

- subtitle one-line preference
- no trailing punctuation
- orphan-word prevention
- subtitle timing alignment
- voice reuse / cache policy
- screenshot cleanliness checks
- final deliverable packaging

### Keep scenario-specific

- copy structure
- evidence hierarchy
- shot rhythm
- opening pattern
- product-demo language vs science-explainer language vs news-recap language

## Why This Split

This structure allows:

- adding a new scenario without rebuilding the pipeline
- changing TTS behavior without touching writing prompts
- changing subtitle rules without touching Remotion scene design
- evolving packaging independently from generation logic
