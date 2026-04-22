# Distillate Experiment Protocol (Pi Agent)

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

Then call the `start_run` MCP tool to preregister the run:

```
start_run(
  project: "<project name>",
  description: "what you're about to try and why",
  hypothesis: "why you think this will work",
  prediction: "what you expect to happen — concrete and falsifiable"
)
```

The **prediction** must be concrete and falsifiable. Good: "loss should drop below 0.5 since we doubled model capacity." Bad: "this should improve things."

This returns both a `run_id` (for `conclude_run` in Step 2) and a `run_number` — the canonical position across the whole project history. **Use `run_number` whenever you refer to this run in prose** (summaries, commits, `save_enrichment`). Never maintain your own counter; it resets on restarts and drifts from the real history.

### Step 1: Train ONE configuration

Write and run a training script for exactly one model configuration. **Always launch training through `distillate-run`** — it reads `.distillate/budget.json` and kills the process at the budget (SIGTERM, then SIGKILL after grace).

```bash
distillate-run python3 train.py
```

Print metrics incrementally during training (one line per epoch), so partial results are captured even if the wrapper kills the process at the budget. The budget lives in `.distillate/budget.json` (`train_budget_seconds` for the kill, `wrap_budget_seconds` for your post-training wrap-up).

Do not spend more than 2 minutes debugging a single error — try a different approach instead.

### Step 2: Record results

Call the `conclude_run` MCP tool with your results:

```
conclude_run(
  project: "<project name>",
  run_id: "<run_id from start_run>",
  results: {"metric_name": value, ...},
  reasoning: "2-3 sentences: what worked, what didn't, what you learned. Be specific with numbers.",
  outcome: "one sentence: what actually happened vs your prediction",
  hyperparameters: {"lr": 0.001, ...},
  changes: "what changed from previous run"
)
```

### The outcome must reference the prediction

The **outcome** closes the loop. Compare what happened against what you predicted. Good: "loss hit 0.38, beating the 0.5 prediction — extra capacity helped more than expected." Bad: "it worked."

### Every run MUST produce a metric

**`results` must contain at least one numeric metric.** A run without a metric is invisible on the chart and useless for tracking progress. Always ensure your training script evaluates on the test/validation set and you capture the result in `conclude_run`. If the script crashes before producing metrics, pass `status: "crash"`.

### Status is auto-detected

You don't need to pass a `status` field. The tool compares your key metric against prior best runs and auto-detects:
- **`best`** — this run improved the frontier
- **`completed`** — valid run, didn't beat the best

Only pass `status: "crash"` for runs that failed with an exception, produced zero output, or no metrics at all.

### Step 2b: Update RESULTS.md

After logging results, update `RESULTS.md` at the repo root with a concise research summary:

- **Current best**: Key metric value and which run achieved it
- **Key findings**: What you've learned across runs (specific numbers)
- **What's next**: Your hypothesis for the next experiment

Overwrite the file each time — it should reflect the current state. Keep it under 500 words.

### Step 2c: Save checkpoints (on best runs)

When a run improves the key metric (`is_best: true` from `conclude_run`), save the best model weights to `.distillate/checkpoints/`:

```python
import shutil
from pathlib import Path

ckpt_dir = Path(".distillate/checkpoints")
ckpt_dir.mkdir(parents=True, exist_ok=True)
# Copy your best model file(s) here
shutil.copy2("best_model.pt", ckpt_dir / "best_model.pt")
```

The orchestrator will automatically upload checkpoints to GitHub Releases (or HuggingFace Hub if configured) after the run concludes.

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
- `key_breakthrough`: **One sentence.** "Metric went from X to Y because Z." No parentheticals, no jargon, no Greek letters.
- `lessons_learned`: **Max 3 bullets.** Each under 15 words. Start with the insight, end with the number.
- `dead_ends`: **Max 3 bullets.** "X didn't work because Y." One sentence each.
- `trajectory`: **2 sentences max.** "Started at X. Reached Y by doing Z."

**Always save insights at least once before your time budget runs out**, even if your last few runs were routine. The desktop app displays these — an experiment with no insights looks broken.

## HuggingFace Jobs (Cloud GPU Compute)

If this experiment uses HF compute (you'll see `HF_TOKEN` in your environment), use the `submit_hf_job` MCP tool to run training scripts on cloud GPUs instead of running them locally:

```
submit_hf_job(
  project: "<project name>",
  script: "train.py",
  gpu_flavor: "A100",
  timeout_minutes: 10,
  volumes: ["hf://datasets/org/dataset-name:/data"],
  env: {"WANDB_DISABLED": "true"}
)
```

This returns a `job_id`. Poll for completion with:

```
check_hf_job(job_id: "<job_id>", include_logs: true)
```

The job output directory is mounted at `/output` (a persistent HF storage bucket). Write your results, checkpoints, and logs there.

**When to use HF Jobs vs local execution:**
- If `HF_TOKEN` is set and compute provider is `hfjobs`: use `submit_hf_job`
- Otherwise: run `python3 train.py` locally as usual
