---
name: workflow-orchestrator
description: Coordinate reusable video skills by stage and scenario without absorbing their internal logic. Use when a top-level workflow needs to decide what comes next and which specialized skill should run.
---

# Workflow Orchestrator

## Goal

Route a video request through shared capabilities based on the project's validated declarative template and stage.

## Owns

- stage progression
- template validation and snapshot dispatch
- artifact handoff
- stop / confirm points

## Does Not Own

- script internals
- subtitle internals
- visual internals
- packaging internals
- template-owned writing, pacing, subtitle, and composition policy
