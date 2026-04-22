## Experiment Reporting (Distillate)

### One Config Per Run

Each training script invocation MUST train exactly **ONE model configuration**. Do NOT write scripts that loop over multiple hyperparameter configurations or architectures. To try multiple configs, run the script multiple times with different arguments. Sweep scripts defeat the tracking system — each distinct experiment must be a separate run with its own `runs.jsonl` entry and git commit.

If you discover a qualitatively different approach (new architecture, new technique), that MUST be a separate run with its own commit even if found during exploration.

### Prior Run Awareness

Before starting, **read `.distillate/runs.jsonl`** and `.distillate/context.md` if they exist. Build on what worked, avoid repeating failures.

### Pre-registering a Run

BEFORE training, append a timestamped `"running"` entry to `.distillate/runs.jsonl` with your prediction. This is the pre-registration artifact — it gets committed alongside your results, with timestamps proving the prediction came first.

Use the `start_run` MCP tool if available — it validates fields, generates the run ID, and writes the entry for you:

```
start_run(
  project: "<project name>",
  description: "what you're about to try and why",
  hypothesis: "why you think this will work",
  prediction: "what you expect to happen — concrete and falsifiable",
  predicted_metric: "val_loss",
  predicted_value: 0.5,
  confidence: 70,
  rationale: "xp-abc showed lr=0.01 cut loss 30%; doubling should yield similar"
)
```

The **prediction** must be concrete and falsifiable. Good: "loss should drop below 0.5 since we doubled model capacity." Bad: "this should improve things."

**`predicted_metric` + `predicted_value`** make your prediction machine-readable. **`confidence`** (0-100) enables calibration tracking. **`rationale`** grounds the prediction in prior evidence.

This returns a `run_id` — save it for `conclude_run`.

### Recording Results

After EACH experiment run completes, append a completed entry to `.distillate/runs.jsonl` with results, verdict, and belief update. Use the `conclude_run` MCP tool if available — it auto-detects best/completed status, computes prediction error, and returns the suggested commit message:

```
conclude_run(
  project: "<project name>",
  run_id: "<run_id from start_run>",
  results: {"val_loss": 0.38},
  reasoning: "2-3 sentences: what worked, what didn't, what you learned.",
  outcome: "val_loss hit 0.38, beating the 0.5 prediction",
  verdict: "confirmed",
  belief_update: "model width more effective than expected; try 256 next",
  hyperparameters: {"d_model": 128},
  changes: "what changed from previous run"
)
```

**Required fields:** `run_id`, `results`, `reasoning`, `outcome`.

**CRITICAL: Every run MUST produce a metric in `results`.** A run without a numeric metric is invisible on the chart and useless for tracking progress. If your training script fails to output metrics, that's a `crash`. Always ensure your script prints and captures at least one evaluation metric (e.g. `macro_f1_test`, `val_loss`, `accuracy`) before concluding the run.

**Recommended fields:**
- `description` — shortest possible change summary (e.g. "seed: 42→137", "d_model: 64→128", "baseline")
- `reasoning` — 2-3 sentences interpreting results: what worked, what didn't, why. Reference metric values.
- `hypothesis` — why you tried this approach
- `verdict` — "confirmed", "refuted", or "inconclusive" (auto-detected from numeric prediction if omitted)
- `belief_update` — what changed in your understanding, feeds your next prediction
- `learnings` — Array of key takeaways that future sessions should know

**Optional fields:** `hyperparameters`, `changes`, `duration_seconds`, `commit`, `baseline_comparison` (object with `metric`, `baseline`, `delta`).

### Committing

After each run, IMMEDIATELY commit using the `suggested_commit_msg` from `conclude_run`:

```bash
git add -A && git commit -m '<suggested_commit_msg from conclude_run>' && git push
```

The format is: `[best] <change>: <metric>=<value> (predicted <threshold>, <verdict>)`

Commit EVERY run — including ones that didn't improve. The audit trail matters more than a clean git log.

Examples:
- `git commit -m 'baseline CNN: f1=0.42'`
- `git commit -m '[best] d_model 64->128: val_loss=0.38 (predicted <0.5, confirmed)'`
- `git commit -m 'lr 0.01->0.1: val_loss=0.72 (predicted <0.4, refuted)'`
- `git commit -m '[best] HistGBM ensemble: macro_f1=0.80 (predicted >0.75, confirmed)'`

Your commit messages ARE the experiment log with prediction tracking. Each commit = one run. Then push.

### Time Budget Enforcement

The training time budget lives in `.distillate/budget.json` (`train_budget_seconds`). Always launch training via the `distillate-run` wrapper — it reads the budget, exec's your command, sends SIGTERM at the deadline, and SIGKILL after a short grace window.

```bash
distillate-run python3 train.py
```

Print metrics line-by-line during training (one line per epoch). When the wrapper kills the process at the budget, everything printed so far is captured — partial results survive. After the kill you have a `wrap_budget_seconds` grace window to call `conclude_run`, commit, and push.

#### In-script guard — NEVER hardcode the budget

For clean epoch-boundary stops (so the wrapper's SIGTERM doesn't chop mid-step), also add a wall-clock guard inside the training loop. **Never hardcode MAX_SECONDS, timeout values, or run durations.** Always derive them from `.distillate/budget.json` via the canonical helper:

```python
from distillate.budget import read_train_budget

MAX_SECONDS = read_train_budget()  # train_budget_seconds - 300s reserve

_start = time.time()
for epoch in range(max_epochs):
    # ... training loop ...
    if time.time() - _start > MAX_SECONDS:
        print(f"Time budget reached at epoch {epoch}")
        break
# evaluation and metric printing happen AFTER the loop — results are never lost
```

Updating the budget in `.distillate/budget.json` (e.g. from the desktop UI) must change the effective timeout with no script edit. Any hardcoded value defeats this.

If a run terminates with no metrics at all (e.g. the script crashed early), log `status: "crash"` and move on immediately.

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