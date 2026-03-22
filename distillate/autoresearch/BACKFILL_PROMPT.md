# URGENT: Backfill Missing Runs

STOP what you are doing. You have experiment results that were NOT logged to `.distillate/runs.jsonl`. Fix this NOW before doing anything else.

## Instructions

1. Read `.distillate/runs.jsonl` to see which runs are already logged
2. Run `git log --oneline` and review your conversation history to find ALL results missing from runs.jsonl
3. Use `git log --format='%aI %s'` to get the correct timestamps for each commit
4. For EACH missing result, append a completed entry to `.distillate/runs.jsonl` with the ORIGINAL timestamp (when the experiment actually ran, from git log):

```json
{"$schema":"distillate/run/v1", "id":"run_NNN", "timestamp":"<ORIGINAL ISO8601 from git log>", "status":"keep|discard", "description":"...", "hypothesis":"...", "hyperparameters":{...}, "results":{...}, "reasoning":"..."}
```

5. Use sequential run IDs starting after the last existing run
6. Include ALL numeric results: accuracy, loss, param_count, train_time_sec, etc.
7. Log each distinct configuration/architecture as a separate run. Err on the side of MORE runs
8. Append entries IN CHRONOLOGICAL ORDER (oldest first) using the original timestamps
9. After logging all: `git add -A && git commit -m 'backfill: log N missing runs' && git push`
10. Then resume your normal experiment work.

## What counts as a missing run

- Any training script you ran that produced results but wasn't logged
- Any architecture variant you tested (even within a sweep script)
- Any significant intermediate discovery (e.g., finding that 180 params works before optimizing to 108)

Log each distinct configuration as a separate run. Err on the side of logging MORE runs, not fewer.
