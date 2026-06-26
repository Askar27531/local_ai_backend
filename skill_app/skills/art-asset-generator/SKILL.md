---
name: art-asset-generator
description: Create, approve, generate, validate, compare, select, and package traceable game-art candidates for item icons, UI icons, and concept-art drafts. Use when an art request requires a structured Visual Brief, controlled image-provider invocation, multiple reproducible candidates, specification validation, human selection, and delivery without source-project modification.
---

# Art Asset Generator

## Objective

Turn a supported game-art request into one selected, validated, reproducible asset through two explicit human approval gates.

## Supported scope

Support only:

- Item icons.
- UI icons.
- Concept-art drafts.

Do not present generated work as a final production animation, full UI system, 3D model, rig, level, or legally verified brand asset.

## Create the Visual Brief

Capture:

- Asset type, title, primary request, and use case.
- Style and medium.
- Composition and framing.
- Lighting, mood, and optional color palette.
- Positive and negative prompts.
- Width, height, output format, alpha requirement, and candidate count.
- Reference artifact IDs when supplied.

Use PNG, WebP, or JPEG; dimensions must be from 64 through 3840 per side and no more than 8,294,400 total pixels. Generate two through six candidates.

## Approval and generation flow

1. Persist the Visual Brief as an artifact.
2. Request human approval of prompt, negative prompt, dimensions, alpha, references, and candidate count.
3. Do not invoke any image provider before Brief approval.
4. Generate all candidates from the approved Brief using distinct recorded seeds.
5. Record provider, model, prompt, negative prompt, seed, dimensions, MIME type, alpha, references, and provider metadata for every candidate.
6. Validate every candidate against the Brief.
7. Build a Contact Sheet for comparison.
8. Request a second human approval to select one candidate.
9. Package only the selected approved candidate in the delivery manifest.

## Validation

Verify:

- Actual dimensions match the Brief.
- MIME type and output format are valid.
- Alpha presence satisfies the requirement.
- Candidate bytes are readable.
- Candidate metadata is complete and traceable.
- Every candidate belongs to the approved Brief.

Treat any failed candidate validation as blocking.

## Safety and integrity

- Do not mutate the Brief after approval; create a new approval cycle for changes.
- Do not silently replace references, prompts, seeds, or provider metadata.
- Do not write generated assets into the source project.
- Do not bypass candidate selection by treating generation success as approval.
- Preserve all candidate and validation artifacts for auditability.

## Delivery contract

Deliver an `ArtManifest` containing the task and workflow IDs, approved Visual Brief artifact, all candidate artifact IDs, validation report, Contact Sheet, selected artifact, and `source_project_modified: false`.

## Completion criteria

Finish only when both approvals exist, all candidates validate, selection is explicit, the manifest is traceable and schema-valid, and the source project remains unchanged.
