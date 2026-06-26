# Skills

Each domain capability lives in its own directory:

```text
skills/
└─ example-skill/
   ├─ SKILL.md
   ├─ skill.yaml
   └─ references/
```

- `SKILL.md` is the model-facing semantic operating guide.
- `skill.yaml` is the program-facing execution contract.
- `references/` contains optional project knowledge loaded only when needed.

An active workflow Skill must bind only registered workflows, agents, tools,
output schemas, and validators. The Skill Runtime snapshots the manifest and
package hashes before execution. Agent policy, Skill policy, and current-step
permissions are intersected; a Skill can narrow permissions but cannot expand
the platform safety boundary.

The active executable packages are:

- `npc-app-feature`
- `game-text-writer`, including the frozen `lore-reviewer` component

Other Skills remain compatible model-facing modules and will migrate
incrementally.
