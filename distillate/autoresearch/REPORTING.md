## Experiment Reporting (Distillate)

### One Config Per Run

Each training script invocation MUST train exactly **ONE model configuration**. Do NOT write scripts that loop over multiple hyperparameter configurations or architectures. To try multiple configs, run the script multiple times with different arguments. Sweep scripts defeat the tracking system — each distinct experiment must be a separate run with its own `runs.jsonl` entry and git commit.

If you discover a qualitatively different approach (new architecture, new technique), that MUST be a separate run with its own commit even if found during exploration.

### Prior Run Awareness

Before starting, **read `.distillate/runs.jsonl`** and `.distillate/context.md` if they exist. Build on what worked, avoid repeating failures.

### Announcing a Run

BEFORE implementing each experiment, call the `start_run` MCP tool. This generates a unique run ID (`xp-{slug}`) and appends a `"running"` entry to `.distillate/runs.jsonl`. **Do NOT write to runs.jsonl directly** — always use the MCP tools.

```
start_run(project: "<project name>", description: "what you're about to try and why", hypothesis: "why you think this will work")
```

This returns a `run_id` — save it for `conclude_run`.

### Recording Results

After EACH experiment run completes, call the `conclude_run` MCP tool with the `run_id` from `start_run`:

```
conclude_run(
  project: "<project name>",
  run_id: "<run_id from start_run>",
  status: "keep",
  results: {"metric_name": value, ...},
  reasoning: "2-3 sentences: what worked, what didn't, what you learned.",
  hyperparameters: {"lr": 0.001, ...},
  changes: "what changed from previous run"
)
```

**Required fields:** `run_id`, `status`, `results`, `reasoning`.

**CRITICAL: Every run MUST produce a metric in `results`.** A run without a numeric metric is invisible on the chart and useless for tracking progress. If your training script fails to output metrics, that's a `crash`, not a `keep`. Always ensure your script prints and captures at least one evaluation metric (e.g. `macro_f1_test`, `val_loss`, `accuracy`) before concluding the run.

**Recommended fields:**
- `description` — shortest possible change summary (e.g. "seed: 42→137", "d_model: 64→128", "baseline")
- `reasoning` — 2-3 sentences interpreting results: what worked, what didn't, why. Reference metric values.
- `hypothesis` — why you tried this approach
- `learnings` — Array of key takeaways that future sessions should know

**Optional fields:** `hyperparameters`, `changes`, `duration_seconds`, `commit`, `baseline_comparison` (object with `metric`, `baseline`, `delta`).

### Committing

After each run, IMMEDIATELY commit:

```bash
git add -A && git commit -m '<shortest change desc>: <metric>=<value>' && git push
```

Commit EVERY run — including ones that didn't improve. The audit trail matters more than a clean git log.

Examples:
- `git commit -m 'baseline CNN: f1=0.42'`
- `git commit -m 'd_model: 64->128: loss=0.03'`
- `git commit -m 'add dropout 0.1: val_bpb=1.12 (no improvement)'`
- `git commit -m 'baseline: val_bpb=1.45'`

Keep descriptions as short as possible — focus on what changed. Your commit messages ARE the experiment log. Each commit = one run. Then push.

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

### Run status

Call `conclude_run` with your results — it auto-detects whether the run is `best` (frontier-improving) or `completed`. You don't need to pass a status unless the run crashed.

- **`crash`** — pass `status: "crash"` ONLY when the run failed with a Python exception, produced zero output, or could not complete training at all.
- For all other runs, omit `status`. The tool compares against the key metric frontier and returns `is_best: true/false`.

Create the `.distillate/` directory if it doesn't exist. This enables live experiment tracking and cross-session awareness.

### Updating RESULTS.md

After each run, update `RESULTS.md` at the repo root. This is your research narrative, displayed in the Distillate app. Write in first person as the researcher. Structure:

- Current best result (metric = value, from which run)
- Key findings with specific numbers
- Failed approaches and why
- Next hypothesis

Overwrite the full file each run. Keep it under 500 words.