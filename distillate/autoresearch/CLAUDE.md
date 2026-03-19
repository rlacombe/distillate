# Distillate Experiment Protocol

You are running an autonomous experiment. Read PROMPT.md and follow it precisely.
You are fully autonomous. Do NOT pause to ask the human anything. The human may be asleep. Work indefinitely until manually stopped.

## One Config Per Run

Each training script invocation MUST train exactly **ONE model configuration**. Do NOT write scripts that loop over multiple hyperparameter configurations or architectures. To try multiple configs, run the script multiple times with different arguments. Sweep scripts defeat the tracking system.

If you discover a qualitatively different approach (new architecture, new technique), that MUST be a separate run with its own commit.

## Run Protocol

For EVERY experiment run, follow this exact sequence:

### Step 0: Announce (BEFORE training)

Read `.distillate/runs.jsonl` and `.distillate/context.md` if they exist. Build on what worked, avoid repeating failures. Then append a `"running"` entry:

```json
{"$schema":"distillate/run/v1", "id":"run_NNN", "timestamp":"ISO8601", "status":"running", "description":"one sentence: what you're about to try and why"}
```

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

Append a completed entry to `.distillate/runs.jsonl` with the same `id`:

```json
{"$schema":"distillate/run/v1", "id":"run_NNN", "timestamp":"ISO8601", "status":"keep|discard|crash", "description":"shortest change summary", "hypothesis":"why you tried this", "changes":"what changed from previous", "hyperparameters":{...}, "results":{...}, "reasoning":"2-3 sentences: what worked, what didn't, what you learned. Be specific with numbers."}
```

**Required fields:** `id`, `timestamp`, `status`, `results`, `reasoning`.

Status values:
- `keep` — improved on baseline or is the new baseline
- `discard` — did not improve
- `crash` — failed with error

### Step 2b: Update RESULTS.md

After logging results, update `RESULTS.md` at the repo root with a concise research summary:

- **Current best**: Key metric value and which run achieved it
- **Key findings**: What you've learned across runs (specific numbers)
- **What's next**: Your hypothesis for the next experiment

Overwrite the file each time — it should reflect the current state. Keep it under 500 words.

### Step 3: Commit and push

```bash
git add -A && git commit -m '<shortest change desc>: <metric>=<value> [keep|discard]' && git push
```

Your commit history IS the experiment tracker. **Each commit = one run.** Then go back to Step 0 for the next experiment.

Examples:
- `git commit -m 'd_model: 64->128: loss=0.03 [keep]'`
- `git commit -m 'add dropout 0.1: val_bpb=1.12 [discard]'`
- `git commit -m 'pairwise multiplication: accuracy=100% params=108 [keep]'`
