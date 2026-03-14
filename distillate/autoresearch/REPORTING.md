## Experiment Reporting (Distillate)

### Prior Run Awareness

Before starting, **read `.distillate/runs.jsonl`** and `.distillate/context.md` if they exist. Build on what worked, avoid repeating failures.

### Announcing a Run

BEFORE implementing each experiment, announce it by appending a `"running"` entry to `.distillate/runs.jsonl`:

```json
{"$schema":"distillate/run/v1", "id":"run_NNN", "timestamp":"ISO8601", "status":"running", "description":"one sentence: what you're about to try and why"}
```

This lets the user see what you're attempting while the run trains. Keep the description to one sentence — what changed and the hypothesis (e.g. "Double d_model to 128 — testing if capacity is the bottleneck").

### Recording Results

After EACH experiment run completes, append a NEW line to `.distillate/runs.jsonl` with the same `id` and full results:

```json
{"$schema":"distillate/run/v1", "id":"run_NNN", "timestamp":"ISO8601", "status":"keep|discard|crash", "description":"shortest change summary", "hypothesis":"why you tried this", "changes":"what changed from previous", "hyperparameters":{...}, "results":{...}, "reasoning":"2-3 sentences: what worked, what didn't, what you learned. Be specific with numbers."}
```

**Required fields:** `id`, `timestamp`, `status`, `results`, `reasoning`.

**Recommended fields:**
- `description` — shortest possible change summary (e.g. "seed: 42→137", "d_model: 64→128", "baseline")
- `reasoning` — 2-3 sentences interpreting results: what worked, what didn't, why. Reference metric values.
- `hypothesis` — why you tried this approach
- `learnings` — Array of key takeaways that future sessions should know

**Optional fields:** `hyperparameters`, `changes`, `duration_seconds`, `commit`, `baseline_comparison` (object with `metric`, `baseline`, `delta`).

### Committing

After each run, IMMEDIATELY commit:

```bash
git add -A && git commit -m '<shortest possible change description>: <key_metric>=<value> [<status>]' && git push
```

Examples:
- `git commit -m 'seed: 42->137: val_bpb=1.05 [keep]'`
- `git commit -m 'd_model: 64->128: loss=0.03 [keep]'`
- `git commit -m 'add dropout 0.1: val_bpb=1.12 [discard]'`
- `git commit -m 'baseline: val_bpb=1.45 [keep]'`

Keep descriptions as short as possible — focus on what changed (e.g. "seed: 42→137", "lr: 3e-4→1e-3"). Your commit messages ARE the experiment log. Each commit = one run. Then push.

### Time Budget Enforcement

PROMPT.md specifies a maximum training time per run (e.g. "5 minutes per iteration"). You MUST enforce this in every training script by adding a wall-clock time check:

```python
import time
_start = time.time()
MAX_SECONDS = 300  # ← match the time budget from PROMPT.md

for epoch in range(max_epochs):
    # ... training loop ...
    if time.time() - _start > MAX_SECONDS:
        print(f"Time budget reached at epoch {epoch}")
        break
# evaluation and metric printing happen AFTER the loop — results are never lost
```

This ensures training stops **gracefully** — all metrics up to that point are available for logging. Never rely on external kills or Ctrl+C; always build the time check into the loop itself.

If a run exceeds the budget despite the check (e.g. a single epoch takes too long), kill the process, log `status: "crash"`, and move on immediately.

### Status values

- `keep` — improved on baseline or is the new baseline
- `discard` — did not improve
- `crash` — failed with error

Create the `.distillate/` directory if it doesn't exist. This enables live experiment tracking, notebook generation, and cross-session awareness.