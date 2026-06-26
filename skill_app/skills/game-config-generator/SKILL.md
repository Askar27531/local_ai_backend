---
name: game-config-generator
description: Generate or repair deterministic schema-version-1 NPC repeated-question behavior configuration from natural-language requirements. Use when a game-feature task requests YAML, JSON, thresholds, timing, increments, NPC overrides, or NPC groups and requires schema validation, normalized output, traceability, workspace isolation, and approval.
---

# Game Config Generator

## Objective

Translate configuration requirements into a valid, deterministic NPC behavior artifact without inventing unsupported fields.

## Supported schema

Generate schema version 1 with:

- `schema_version`: exactly `1`.
- `feature`: exactly `npc_behavior`.
- `rules.repeated_question.window_seconds`: integer from 1 through 3600.
- `rules.repeated_question.base_increment`: integer from 1 through 100.
- `thresholds.warning`: integer from 0 through 99.
- `thresholds.refuse`: integer from 1 through 100.
- `npc_overrides`: mapping of NPC IDs to supported override objects.
- `npc_groups`: mapping of group IDs to NPC ID lists.

Require `warning < refuse`. Supported override profiles are `default`, `lenient`, and `strict`; `threshold_multiplier` must be from 0.1 through 3.

## Generate configuration

1. Extract explicit values and units from the request.
2. Use workflow defaults only for omitted values.
3. Preserve valid previous values during repair unless evidence requires changing them.
4. Clamp invalid numeric values only during a repair pass and preserve the intended ordering.
5. Keep YAML as the editable candidate source.
6. Produce normalized JSON from the validated model for downstream consumers.
7. Produce a Markdown explanation connecting requirement values to generated fields.

## Output and path rules

- Keep the candidate under `generated/<task-id>/npc_behavior.yaml`.
- Emit stable field ordering and deterministic serialization.
- Do not add unknown schema keys.
- Keep generated artifacts in TaskWorkspace.
- Never edit runtime configuration or source files directly.

## Validation

Validate:

- YAML parses to a mapping.
- Required keys and literals match schema version 1.
- All numeric ranges are valid.
- Warning precedes refusal.
- Override profiles and multipliers are valid.
- NPC groups contain valid string identifiers and remain internally consistent.
- Normalized JSON represents the same validated values as YAML.

## Repair procedure

- Consume validation errors and the previous candidate.
- Preserve already valid values.
- Change only fields responsible for failure.
- Regenerate normalized JSON and explanation after repair.
- Never bypass schema validation to preserve an invalid request value.

## Completion criteria

Finish only when YAML validates, normalized JSON is equivalent, requirement traceability is clear, the source project remains unchanged, and the candidate is ready for artifact approval.
