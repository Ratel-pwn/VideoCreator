---
name: workflow-orchestrator
description: Coordinate reusable video skills by stage and scenario without absorbing their internal logic. Use when a top-level workflow needs to decide what comes next and which specialized skill should run.
---

# Workflow Orchestrator

## Goal

Route a video request through the correct reusable skills based on scenario and stage.

## Owns

- stage progression
- scenario dispatch
- artifact handoff
- stop / confirm points

## Does Not Own

- script internals
- subtitle internals
- visual internals
- packaging internals
