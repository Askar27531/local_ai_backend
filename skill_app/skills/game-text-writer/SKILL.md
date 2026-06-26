---
name: game-text-writer
description: Create or repair one to five Guichao Island Markdown game-text candidates under game_docs using supplied lore, NPC profiles, prompt rules, chronology, knowledge boundaries, unlock levels, and prior review evidence. Use for dialogue, rumors, lore, retrieval documents, and narrative revisions that must remain canon-safe, RAG-friendly, workspace-isolated, reviewable, and approval-gated.
---

# Game Text Writer

## Objective

Write one or more player-facing Markdown texts that belong naturally in Guichao Island while remaining traceable, retrievable, spoiler-safe, and consistent with established canon.

## Ground the text

- Use only facts supported by supplied source documents.
- Preserve chronology, locations, factions, terminology, and causal relationships.
- Match each affected NPC's identity, voice, motives, vocabulary, and knowledge boundary.
- Respect what an NPC may know, infer, conceal, misunderstand, or reveal at the intended unlock level.
- Treat absent lore as unknown; do not silently promote invention into canon.
- Record the source paths used to derive each candidate.

## Write the candidate

1. Choose one to five Markdown paths under `game_docs/`.
2. Keep the text focused, normally 300–900 Chinese characters unless the request requires more.
3. Prefer concrete, retrieval-friendly facts and stable names over ambiguous pronouns or developer shorthand.
4. Keep in-world prose free of references to AI, prompts, documents, RAG, vectors, or knowledge bases.
5. Preserve requested tone without imitating unrelated characters.
6. Avoid exposing late-game truths through low-level text, implication, metadata, or headings.

## Vectorized document contract

For paths under `game_docs/vectorized/`:

- Begin with valid YAML frontmatter.
- Include `rag_ingest: true`.
- Include a valid default unlock level and useful tags when applicable.
- Include at least one H2 section.
- Put an explicit `解锁N` marker in every H2 heading.
- Keep each section semantically self-contained enough for retrieval.

## Output contract

Return only one `GameTextCandidateSet`:

- `summary`: concise description of the overall narrative change.
- `candidates`: one to five `GameTextCandidate` items.

Each `GameTextCandidate` contains:

- `path`: relative `.md` path under `game_docs/`.
- `content`: complete Markdown content.
- `summary`: concise description of the narrative change.
- `source_paths`: actual supplied source documents, at most eight.
- `affected_npcs`: NPCs whose voice or knowledge is represented.
- `intended_unlock_level`: integer from 0 through 9.

Do not add fields outside the schema.

## Repair procedure

- Consume validation errors and every blocking lore finding.
- Preserve valid canon and requested tone.
- Correct metadata, headings, voice, chronology, retrieval ambiguity, or spoiler leaks directly.
- Change the candidate set content; never return the rejected version unchanged.
- Do not solve a contradiction by deleting all meaningful requested content.

## Safety and delivery

- Keep all writes in TaskWorkspace.
- Do not update formal source documents, Milvus, embeddings, manifests, or production content before approval.
- Do not claim a new fact is canon when sources do not support it.

## Completion criteria

Finish only when every path and Markdown file is valid, source traceability is present, canon and voice are preserved, unlock and spoiler boundaries hold, vectorized sections are ingestible, and the candidate set is ready for lore review and human approval.
