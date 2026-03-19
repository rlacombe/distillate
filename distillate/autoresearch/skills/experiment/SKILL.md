---
name: experiment
description: Start a new experiment with hypothesis and success criteria
disable-model-invocation: true
---

Before running the next experiment, document your plan:

1. State your **hypothesis** — what you expect to happen and why
2. Define **success criteria** — specific metrics and thresholds
3. Describe the **changes** you'll make from the current baseline

After the experiment completes, append a line to `.distillate/runs.jsonl`:

```json
{"$schema":"distillate/run/v1", "id":"run_NNN", "timestamp":"ISO8601", "status":"keep|discard|crash", "hypothesis":"...", "changes":"...", "hyperparameters":{...}, "results":{...}, "reasoning":"..."}
```

Set `status` to `keep` if results improved, `discard` if not, or `crash` on failure.
Include `reasoning` to explain your decision.
