# Distillate Experiment Protocol

You are running an autonomous experiment. Read PROMPT.md and follow it precisely.
You are fully autonomous. Do NOT pause to ask the human anything. The human may be asleep. Work indefinitely until manually stopped.

You have access to Distillate MCP tools for tracking runs and saving insights. Use them — they keep the desktop app in sync.

## One Config Per Run

Each training script invocation MUST train exactly **ONE model configuration**. Do NOT write scripts that loop over multiple hyperparameter configurations or architectures. To try multiple configs, run the script multiple times with different arguments. Sweep scripts defeat the tracking system.

If you discover a qualitatively different approach (new architecture, new technique), that MUST be a separate run with its own commit.

## Run Protocol

For EVERY experiment run, follow this exact sequence:

### Step 0: Plan (BEFORE training)

Read `.distillate/runs.jsonl` and `.distillate/context.md` if they exist. Build on what worked, avoid repeating failures.

Then call the `start_run` MCP tool to announce the run:

```
start_run(project: "<project name>", description: "what you're about to try and why", hypothesis: "why you think this will work")
```

This returns a `run_id` — save it for Step 2.

### Step 1: Train ONE configuration

Write and run a training script for exactly one model configuration. Every training script MUST include a wall-clock time check:

```python
import time
_start = time.time()
MAX_SECONDS = 300  # match the time budget from PROMPT.md

for epoch in range(max_epochs):
    # ... training loop ...
    if time.time() - _start > MAX_SECONDS:
        print(f"Time budget reached at epoch {epoch}")
        break
# evaluation and metric printing happen AFTER the loop
```

Do not spend more than 2 minutes debugging a single error — try a different approach instead.

### Step 2: Record results

Call the `conclude_run` MCP tool with your results:

```
conclude_run(
  project: "<project name>",
  run_id: "<run_id from start_run>",
  status: "keep",
  results: {"metric_name": value, ...},
  reasoning: "2-3 sentences: what worked, what didn't, what you learned. Be specific with numbers.",
  hyperparameters: {"lr": 0.001, ...},
  changes: "what changed from previous run"
)
```

### Every run MUST produce a metric

**`results` must contain at least one numeric metric.** A run without a metric is invisible on the chart and useless for tracking progress. Always ensure your training script evaluates on the test/validation set and you capture the result in `conclude_run`. If the script crashes before producing metrics, use `status: "crash"`.

### Status policy — keep almost everything

- **`keep`** — the default. Use for ALL runs that produced results with metrics, even if metrics didn't improve. Every run is part of the audit trail. Baselines, failed hypotheses, and regressions are all valuable data.
- **`crash`** — use when the run failed with a Python exception, produced zero output, produced no metrics, or could not complete training at all.
- **Never use `discard`.** A run that didn't improve metrics is NOT a failure — it's evidence. Keep it and explain what you learned in the `reasoning` field.

### Step 2b: Update RESULTS.md

After logging results, update `RESULTS.md` at the repo root with a concise research summary:

- **Current best**: Key metric value and which run achieved it
- **Key findings**: What you've learned across runs (specific numbers)
- **What's next**: Your hypothesis for the next experiment

Overwrite the file each time — it should reflect the current state. Keep it under 500 words.

### Step 3: Commit and push

`conclude_run` returns `is_best: true` when the run improved the key metric frontier. Use it for the commit prefix:

```bash
# If is_best was true:
git add -A && git commit -m '[best] <change>: <metric>=<value>' && git push
# Otherwise:
git add -A && git commit -m '<change>: <metric>=<value>' && git push
```

Your commit history IS the experiment tracker. **Each commit = one run.** Commit EVERY run — including ones that didn't improve metrics. The audit trail matters more than a clean git log. Then go back to Step 0 for the next experiment.

Examples:
- `git commit -m '[best] baseline CNN: f1=0.42'`
- `git commit -m 'd_model 64->128: loss=0.03'`
- `git commit -m 'add dropout 0.1: val_bpb=1.12'`
- `git commit -m '[best] HistGBM ensemble: macro_f1=0.80'`

### Step 4: Update insights (when you learn something)

After each run, decide if you learned something worth recording. Update insights when:
- You hit a **new best** result
- A run revealed a **surprising failure** that changes your strategy
- You confirmed a **dead end** worth documenting
- Your overall **trajectory shifted** direction

Skip the update when a run was routine (minor tweak, expected outcome, crashed before producing data). Not every run teaches something new — that's fine.

Call the `save_enrichment` MCP tool with your cumulative findings so far.

```
save_enrichment(
  project: "<project name>",
  key_breakthrough: "macro_f1 improved from 0.42 to 0.76 by adding a 39-bag LDA+GNB cascade on top of the HGBM ensemble.",
  lessons_learned: [
    "Bagging LDA+GNB models is the main lever — 39 bags pushed F1 from 0.75 to 0.76, and each bag only adds 0.15 MB.",
    "The cascade only cares about rank ordering, not raw probabilities — proven via 5 invariance tests.",
    "Binary classification (sigma70 vs sigma38) underperforms multiclass by 3 points — the other 4 classes help LDA discriminate."
  ],
  dead_ends: [
    "Feature engineering (extended box, DNA shape, k-mers) — all added noise, no F1 gain.",
    "SMOTE and class weighting — marginal or harmful, base model already handles class imbalance."
  ],
  trajectory: "Started with standalone HGBM at 0.42. Added LDA+GNB cascade to reach 0.75. Bagging pushed to 0.76 — now at the size-constrained optimum (39 bags, 16 MB)."
)
```

**Format rules — these appear in the desktop UI, write for scannability:**
- `key_breakthrough`: **One sentence.** State the metric improvement and what caused it. No Greek letters, no correlation coefficients, no parenthetical asides. Bad: "macro_f1=0.7645 (39-bag LDA+GNB cascade, 15.98 MB, ~200s). 50 experiments confirmed..." Good: "macro_f1 improved from 0.42 to 0.76 by adding a 39-bag LDA+GNB cascade."
- `lessons_learned`: 3-5 short sentences. Each starts with the finding, then gives one supporting number. No ALL CAPS. No jargon-heavy compressed notation. Write like you're explaining to a smart colleague, not writing a paper abstract.
- `dead_ends`: One sentence each. Name the approach and why it failed.
- `trajectory`: 2-3 sentences. The story arc from baseline to current best.

**Always save insights at least once before your time budget runs out**, even if your last few runs were routine. The desktop app displays these — an experiment with no insights looks broken.
