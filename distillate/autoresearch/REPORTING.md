## Experiment Reporting (Distillate)

### Prior Run Awareness

Before starting any new experiment iteration, **read `.distillate/runs.jsonl`** if it exists. This file contains the history of all prior runs. Use it to:
- Understand what has already been tried
- Build on successful approaches (status: "keep")
- Avoid repeating failed approaches (status: "discard")
- Reference specific run IDs in your hypothesis and reasoning

If `.distillate/context.md` exists, it contains a formatted summary of prior runs that was injected at launch time. Read it for a quick overview.

### Recording Results

After each experiment iteration, append one JSON line to `.distillate/runs.jsonl`:

```json
{"$schema":"distillate/run/v1", "id":"run_NNN", "timestamp":"ISO8601", "status":"keep|discard|crash", "hypothesis":"...", "changes":"...", "hyperparameters":{...}, "results":{...}, "reasoning":"..."}
```

**Required fields:** `id`, `timestamp`, `status`, `hypothesis`, `results`.

**Optional fields:** `hyperparameters`, `changes`, `duration_seconds`, `reasoning`, `commit`, `baseline_comparison` (object with `metric`, `baseline`, `delta`).

**Status values:**
- `keep` — experiment improved on baseline or is the new baseline
- `discard` — experiment did not improve, reverting
- `crash` — experiment failed with an error
- `running` — experiment is still in progress

Create the `.distillate/` directory if it doesn't exist. This enables live experiment tracking, notebook generation, and cross-session awareness.

### File Size Limit

**CRITICAL:** Tool results must not exceed 51,200 bytes. When using the Read tool on files longer than ~400 lines, always use `offset` and `limit` parameters to read in chunks. When writing code, keep individual Python files under 400 lines — split large scripts into separate modules (e.g., `train.py`, `evaluate.py`, `utils.py`).
