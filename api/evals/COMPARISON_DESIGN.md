# Eval Comparison System Design

## Problem Statement

We're building and optimizing an AI agent. We need to:
1. Know if changes make things **better** (progress)
2. Know if changes make things **worse** (regression)
3. Have **confidence** that comparisons are meaningful
4. Be honest to ourselves, the community, and investors

The core question: **"Did we actually get better, or are we fooling ourselves?"**

## Key Insight

There are two things being versioned:
1. **The test** (eval scenario code) - should be stable
2. **The code under test** (agent code) - this is what we're optimizing

These are naturally decoupled by our architecture:
- Scenarios inject messages via Celery (API boundary)
- Agent processes asynchronously (black box)
- Scenarios query database for results (observation only)
- Scenarios never import agent code directly

```
Scenario (test)          │         Agent (code under test)
                         │
inject_message() ───────►│───► Celery task ───► Agent processing
                         │                            │
wait_for_idle()          │                            ▼
                         │                      Tool calls, LLM calls,
query database ◄─────────│◄─── writes to DB ◄── messages, artifacts
                         │
assert on results        │
```

## The Approach

### Fingerprinting Strategy

**AST hashing** - hash the Abstract Syntax Tree of the scenario class.

Why AST, not source code?
- Normalizes formatting (whitespace, comments don't affect hash)
- Captures behavioral equivalence
- Simple to implement (`ast.parse()` + `ast.dump()`)

Why not structural fingerprint (just task names/types)?
- Could miss behavioral changes in `run()` method
- Less "honest" - refactoring could hide changes

**"The first principle is that you must not fool yourself — and you are the easiest person to fool."** - Feynman

### What We Capture Per Run

```python
scenario_fingerprint  # AST hash of scenario class (16 char hex)
code_version          # Git commit hash (12 char)
code_branch           # Git branch name
primary_model         # LLM model name (e.g., 'claude-sonnet-4')
```

Plus existing fields:
- `scenario_slug` - identifies which scenario
- `scenario_version` - manual version string
- `llm_routing_profile` - immutable snapshot (already implemented)
- `llm_routing_profile_name` - denormalized for display

### Comparison Logic

**Primary comparison:** Same `scenario_slug`

**Strict comparison:** Same `scenario_fingerprint`

**Safety net:** If fingerprints differ, show warning icon in UI

The fingerprint answers: "Are these runs testing the exact same thing?"
The git hash answers: "What agent code was being tested?"

## Design Decisions

### Why not manual versioning alone?

- Multiple developers, OSS contributors
- Can't rely on "remember to bump version"
- Need automated audit trail for credibility

### Why not hash everything (scenario + agent code together)?

- We WANT to compare different agent code versions
- The scenario fingerprint should be STABLE across agent changes
- Git commit captures agent code version separately

### Why not complex matching tiers?

We discussed three tiers:
1. **Strict:** Same fingerprint + same LLM profile lineage
2. **Pragmatic:** Same fingerprint, any config
3. **Historical:** Same slug, any fingerprint

**Decision:** Capture the data, defer the tier logic. The UI can filter however it wants. For now, just having the fingerprint is enough - humans can decide what to compare.

### Edge Cases

**Scenario imports helper, helper changes:**
- AST hash of scenario won't change
- Behavioral difference not captured
- Mitigation: Keep scenarios self-contained, or accept this limitation

**Decorator changes:**
- AST includes decorators
- `@register_scenario` change would affect hash
- This is probably correct behavior

## Implementation Plan

### Phase 1: Data Capture ✅ COMPLETE

1. Create `api/evals/fingerprint.py`:
   - `compute_scenario_fingerprint(scenario)` - AST hash
   - `get_code_version()` - git commit
   - `get_code_branch()` - git branch

2. Add fields to `EvalRun` model:
   - `scenario_fingerprint` (CharField, indexed)
   - `code_version` (CharField)
   - `code_branch` (CharField)

3. Populate in `EvalRunner` at run start

### Phase 2: Comparison Foundation ✅ COMPLETE

1. API endpoint: `GET /console/api/evals/runs/<id>/compare/`

   **Tier parameter** (controls what's considered "comparable"):
   - `?tier=strict` - Same fingerprint + same LLM profile lineage
   - `?tier=pragmatic` (default) - Same fingerprint, any config
   - `?tier=historical` - Same scenario slug, any fingerprint
   - Returns `fingerprint_warning` when comparing runs with different fingerprints

   **Grouping parameter** (for variable isolation):
   - `?group_by=code_version` - Group runs by git commit
   - `?group_by=primary_model` - Group runs by LLM model
   - `?group_by=llm_profile` - Group runs by routing profile name
   - Grouped response includes `groups[]` with aggregated metrics and `pass_rate`

   **Filter parameters** (narrow the result set):
   - `?run_type=official|adhoc` - Filter by run type
   - `?code_version=abc123` - Filter to specific commit
   - `?primary_model=claude-sonnet-4` - Filter to specific model

2. Run detail response includes:
   - `scenario_fingerprint`, `code_version`, `code_branch`, `primary_model`
   - `comparison.comparable_runs_count`
   - `comparison.has_comparable_runs`

3. Fingerprint mismatch warnings in historical tier comparisons

4. `primary_model` denormalized from routing profile for efficient querying:
   - Extracted via `get_primary_model()` which traverses Profile → TokenRange → Tier → Endpoint
   - DB-indexed for fast grouping/filtering

### Phase 3: UI (Future)

1. "Compare" button on eval runs page
2. Timeline visualization showing progress
3. Side-by-side metrics comparison
4. Warning indicators for fingerprint mismatches

## Variable Isolation

The key to honest evaluation is isolating what you're testing. Three variables typically change:

| Variable | What Changed | Captured By |
|----------|-------------|-------------|
| Agent code | Business logic, prompts | `code_version` |
| LLM model | claude-sonnet-4 vs gpt-4 | `primary_model` |
| LLM config | Temperature, routing | `llm_routing_profile_name` |

### Example Use Cases

**"Did our refactor improve things?"**
```
?group_by=code_version&primary_model=claude-sonnet-4
```
Same model, see performance across code commits.

**"Which model performs best?"**
```
?group_by=primary_model&code_version=abc123
```
Same agent code, compare models.

**"Is our new profile better?"**
```
?group_by=llm_profile&code_version=abc123&primary_model=claude-sonnet-4
```
Same code + model, compare routing configurations.

## Metrics That Matter

For our goal (agent performance + efficiency):

| Metric | Why |
|--------|-----|
| `total_cost` | Primary optimization target |
| `completion_count` | Fewer LLM calls = cheaper |
| `tokens_used` | Efficiency proxy |
| `pass_rate` | Can't regress quality |
| `step_count` | Agent "thinking" efficiency |

## Prior Art

Most eval frameworks (MLflow, W&B, LangSmith) punt on this problem - they let you tag runs and manually decide what's comparable.

OpenAI Evals uses git history (same file path = same eval).

Braintrust versions datasets but not scorer code.

**Our approach is more rigorous:** Automatic fingerprinting of eval code catches accidental changes that other systems miss.

## Open Questions

1. Should we fingerprint at suite level too? (Hash of scenario slugs + fingerprints)
2. Should fingerprint changes auto-increment `scenario_version`?
3. How to handle scenarios with external dependencies (API mocks, fixtures)?

## Key Quotes from Design Discussion

> "The whole point of this system is to prevent self-deception. You *want* the agent to be better. You're *motivated* to see improvement. That's exactly when you need a system that won't let you cheat."

> "When you tell investors 'we reduced agent costs 40% over 3 months', you need receipts. The fingerprint is the receipt."

> "The fingerprint isn't just insurance - it's infrastructure for credibility."

> "Ship it, and it quietly accumulates the audit trail you'll need later."
