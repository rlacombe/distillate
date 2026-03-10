## Experiment Reporting (Distillate)

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
