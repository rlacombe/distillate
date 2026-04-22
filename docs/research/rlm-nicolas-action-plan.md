# RLM-Nicolas: Recursive Language Model Architecture for Nicolas

> **Status**: IMPLEMENTED 2026-04-13 — `lab_repl` MCP tool live, 48 tests passing
> **Paper**: [Recursive Language Models](https://arxiv.org/abs/2512.24601) — Zhang, Kraska, Khattab (Dec 2025)  
> **Goal**: Adapt the RLM paradigm to make Nicolas the most capable research agent entry point  

---

## 1. Paper Summary & Key Insights

### What RLMs Are

Recursive Language Models (RLMs) are an inference-time paradigm where the LLM **never sees the full context**. Instead:

1. The context is stored as a Python variable in a REPL sandbox
2. The root LLM receives only the query + the ability to write/execute code
3. The LLM can **peek** (examine slices), **grep** (regex search), **partition+map** (chunk and sub-call), and **summarize** (condense subsets)
4. Sub-LLM calls (`llm_query()`, `rlm_query()`) can be spawned from within the REPL — recursively
5. Answers are returned via `FINAL(answer)` or `FINAL_VAR(variable_name)` tags

### Results That Matter for Us

| Finding | Implication for Nicolas |
|---------|----------------------|
| RLMs process 10M+ tokens (100x beyond context windows) | Nicolas could reason over entire experiment histories, full paper libraries |
| Even on short prompts, RLMs outperform vanilla LLMs by 28-114% | Better answers even when context fits — decomposition helps reasoning |
| Cheap models (GPT-5-nano) as sub-callers approach expensive models (GPT-5) | Haiku sub-calls could match Opus quality at 1/20th cost |
| Emergent strategies appear without prompting | Nicolas would develop its own analysis patterns for papers, experiments |
| Cost is comparable or cheaper than base models at median | Not a luxury — a practical improvement |

### The Core Insight

> **Context rot is Nicolas's biggest scaling threat.** As the lab grows (more papers, more experiments, longer histories, more agents), the system prompt and tool results become massive and degrade quality. RLM eliminates this by design — Nicolas never holds the full state, it programmatically explores what it needs.

---

## 2. Current Nicolas Architecture (Friction Points)

### What Works Well
- **MCP tool layer**: 46+ tools, clean dispatch, good isolation
- **Sub-agent protocol**: Knowledge Agent/Research Agent are extensible via `SubAgent` Protocol
- **WebSocket streaming**: Real-time events to desktop
- **Session persistence**: Survives restarts via Claude SDK

### Where RLM Would Transform Things

**Problem 1: System prompt bloat**  
Nicolas's `_build_dynamic_context()` injects ~2-4K tokens of lab state on every turn:
- Library stats (papers read, queue size, awaiting PDF)
- Recent reads (last 5 papers with engagement scores)  
- Research interest tags (top 8 from last 30 days)
- Experiment state (all projects, run counts, best runs)
- Recent notebook entries (last 8)
- Active agent sessions

This grows linearly with lab activity. At 50 experiments and 200 papers, it's already substantial.

**Problem 2: Flat tool calls can't compose**  
Nicolas calls one tool at a time. "Compare my top 3 experiments and cross-reference with relevant papers" requires Nicolas to:
1. Call `list_projects` → parse
2. Call `get_project_details` × 3 → parse each
3. Call `compare_runs` → parse  
4. Call `search_papers` with keywords from experiments → parse
5. Call `get_paper_details` × N → parse each
6. Synthesize everything in its own context

That's 8+ round trips, all held in Nicolas's context window. With a REPL, this becomes one programmatic sweep.

**Problem 3: Rigid sub-agent routing**  
Sub-agents use keyword matching ("queue" → `get_queue`, "trending" → `get_trending_papers`). This can't handle novel combinations like "find papers related to my failing experiment and suggest hypothesis corrections."

**Problem 4: Result truncation destroys information**  
`truncate_result()` clips to 12K chars. Experiment logs, paper highlights, and comparison tables regularly exceed this. Information is permanently lost.

**Problem 5: No cross-entity reasoning**  
Nicolas can query papers OR experiments OR agents — but can't programmatically join them. "Which of my papers' methods would help debug experiment X's convergence issue?" requires reasoning across primitives that the current tool-per-call model can't support.

---

## 3. RLM-Nicolas Architecture

### Design Principle

> **Nicolas becomes a Recursive Language Model.** The user talks to Nicolas naturally. Nicolas reasons in code behind the scenes — querying the lab, spawning sub-calls, building up analysis — then responds with deep, comprehensive answers. The REPL is invisible to the user.

### Architecture Diagram

```
User (natural language)
  │
  ▼
Nicolas (Root LM — Opus)
  │
  ├── Direct response (simple queries, no REPL needed)
  │
  └── REPL-mediated reasoning (complex queries)
        │
        ├── Lab API (injected into sandbox)
        │   ├── papers.*      — query/filter/read papers
        │   ├── experiments.*  — query/filter/read experiments  
        │   ├── notebook.*     — event stream access
        │   ├── agents.*       — agent registry + status
        │   └── projects.*     — project metadata
        │
        ├── Sub-LLM calls
        │   ├── llm_query(prompt)           — single sub-call (Haiku)
        │   ├── llm_query_batch(prompts)    — parallel sub-calls
        │   ├── delegate(prompt, context,   — recursive RLM sub-call  
        │   │            tools, model)        (Sonnet/Haiku)
        │   └── delegate_batch(...)         — parallel recursive
        │
        └── Utilities
            ├── FINAL(answer)      — return to user
            ├── FINAL_VAR(var)     — return variable contents  
            ├── SHOW_VARS()        — debug: list workspace
            └── Standard Python    — math, json, re, collections...
```

### What Changes vs. Current Architecture

| Component | Current | RLM-Nicolas |
|-----------|---------|-------------|
| System prompt | 2-4K tokens of pre-loaded state | Minimal identity + REPL instructions (~500 tokens) |
| Lab state access | Injected into prompt on every turn | Queried on demand via `papers.*`, `experiments.*` |
| Tool orchestration | One MCP tool call per LLM turn | Multi-step Python code in sandbox |
| Sub-agents | Hard-coded Knowledge Agent/Research Agent | Dynamic `delegate()` with context/tool subsetting |
| Result size | Truncated to 12K chars | Unlimited via REPL variables |
| Cross-entity queries | Multiple sequential tool calls | Programmatic joins in Python |
| Model routing | Single model for everything | Root: Opus, Sub-calls: Sonnet/Haiku |
| Cost structure | Linear with complexity | Sub-linear (cheap models do heavy lifting) |

---

## 4. Implementation Phases

### Phase 1: Lab REPL Sandbox (Foundation)

**What**: A new MCP tool `lab_repl` that gives Nicolas a persistent Python sandbox with the lab's data API injected.

**New files**:
- `distillate/agent_runtime/lab_repl.py` — sandbox implementation
- `distillate/agent_runtime/lab_api.py` — typed API objects injected into sandbox

**Lab API surface** (injected as Python objects):

```python
# Papers
papers.search(query: str) -> list[Paper]
papers.get(key: str) -> Paper  
papers.recent(days: int = 7) -> list[Paper]
papers.queue() -> list[Paper]
papers.by_tag(tag: str) -> list[Paper]
papers.highlights(key: str) -> list[Highlight]

# Experiments  
experiments.list() -> list[Experiment]
experiments.get(id: str) -> Experiment
experiments.runs(id: str) -> list[Run]
experiments.logs(run_id: str, tail: int = 100) -> str
experiments.metrics(run_id: str) -> dict
experiments.active() -> list[Experiment]

# Notebook
notebook.recent(n: int = 20) -> list[Entry]
notebook.search(query: str) -> list[Entry]
notebook.by_date(start: str, end: str) -> list[Entry]

# Projects
projects.list() -> list[Project]
projects.get(id: str) -> Project
projects.papers(id: str) -> list[Paper]
```

**Sandbox constraints**:
- Safe builtins (no eval/exec/compile/input)
- Standard library: `math`, `json`, `re`, `collections`, `statistics`, `datetime`
- Persistent namespace within session (variables survive across REPL calls)
- 30-second timeout per execution
- Thread-safe with lock

**MCP tool interface**:
```python
@tool("lab_repl")
def lab_repl(code: str) -> str:
    """Execute Python code in Nicolas's lab sandbox.
    
    Available objects: papers, experiments, notebook, projects
    Available functions: llm_query(), delegate(), FINAL(), FINAL_VAR(), SHOW_VARS()
    
    Variables persist across calls within this session.
    """
```

**Deliverable**: Nicolas can write code to query the lab, manipulate data, and build up analysis. No sub-calls yet — just the sandbox + data API.

### Phase 2: Lean System Prompt

**What**: Strip the dynamic lab state from the system prompt. Replace with REPL-aware instructions.

**Changes to `agent_sdk.py` / `agent_core.py`**:

The `_build_dynamic_context()` function currently injects:
- Library stats → **remove** (available via `papers.search()`, `papers.recent()`)
- Recent reads → **remove** (available via `papers.recent()`)
- Research tags → **remove** (derivable from papers data)
- Queue snapshot → **remove** (available via `papers.queue()`)
- Experiment state → **remove** (available via `experiments.list()`)
- Notebook entries → **remove** (available via `notebook.recent()`)
- Active agents → **keep** (small, needed for routing)

New system prompt addition (~400 tokens):
```
## Lab REPL

You have a persistent Python sandbox for reasoning about the lab. Use it when:
- A question spans multiple papers, experiments, or primitives
- You need to filter, sort, rank, or compare entities
- The answer requires multi-step analysis
- You want to generate a comprehensive report

Available in the sandbox:
- papers.*, experiments.*, notebook.*, projects.* — typed data API
- llm_query(prompt) — spawn a sub-LLM call (fast, cheap model)
- delegate(prompt, context) — spawn a recursive sub-call with context
- FINAL(answer) — return your answer

For simple questions ("how many papers have I read?"), just call the
appropriate tool directly — no need for the REPL.
```

**Key design decision**: Nicolas retains all existing MCP tools. The REPL is additive — a new capability for complex reasoning, not a replacement for simple tool calls. This avoids a risky migration and lets Nicolas naturally learn when to use each.

### Phase 3: Sub-LLM Calls (Recursive Depth)

**What**: Enable `llm_query()` and `delegate()` functions inside the sandbox.

**Implementation in `lab_repl.py`**:

```python
def llm_query(prompt: str, model: str = "haiku") -> str:
    """Single LLM sub-call. Fast, cheap. Good for:
    - Classifying a paper/run
    - Extracting specific info from a text block
    - Answering a focused question about a context snippet
    """
    # Calls Anthropic API directly (not through Claude Code)
    # Uses the specified model (default: Haiku for cost)
    # Returns the text response

def delegate(prompt: str, context: Any = None,
             tools: list[str] | None = None,
             model: str = "sonnet") -> str:
    """Recursive RLM sub-call. Spawns a mini-Nicolas with:
    - Its own REPL sandbox
    - The specified context loaded as a variable
    - Optional tool subset (defaults to all lab tools)
    - Its own iteration budget (max 5 turns)
    
    Good for:
    - Analyzing a single experiment in depth
    - Summarizing a batch of papers
    - Any task that benefits from focused, isolated reasoning
    """

def llm_query_batch(prompts: list[str], model: str = "haiku") -> list[str]:
    """Parallel sub-calls via ThreadPoolExecutor."""

def delegate_batch(tasks: list[dict], model: str = "sonnet") -> list[str]:
    """Parallel recursive sub-calls."""
```

**Depth control**: Max recursion depth = 2 (root → sub-call → sub-sub-call). Prevents runaway costs.

**Budget control**: Each `delegate()` call gets a token budget (default 8K output tokens). Parent can override.

**Breadcrumbs**: Each sub-call emits breadcrumbs to the WebSocket so the desktop shows progress:
```
⚗️ Delegating: "Analyze convergence of experiment X" (Sonnet)
⚗️ Sub-call scanning 3 runs...  
⚗️ Sub-call complete (2.1s, 1.2K tokens)
```

### Phase 4: Emergent Sub-Agents (Replaces Hard-Coded Routing)

**What**: Knowledge Agent, Research Agent become **delegation templates** rather than Python classes.

**RLM-Nicolas** (templates injected into REPL):
```python
# These are convenience wrappers Nicolas can use, not rigid code paths
def knowledge_agent(task: str) -> str:
    """Delegate a notebook/writing task to a specialist sub-call."""
    return delegate(task, tools=NOTEBOOK_TOOLS, model="sonnet")

def research_agent(task: str) -> str:
    """Delegate a discovery/search task to a specialist sub-call."""  
    return delegate(task, tools=DISCOVERY_TOOLS, model="haiku")
```

But crucially, Nicolas is **not limited to these templates**. It can write:
```python
# Novel cross-entity analysis — no predefined sub-agent for this
failing_xps = [x for x in experiments.active() if x.trend == "declining"]
for xp in failing_xps:
    logs = experiments.logs(xp.id, tail=500)
    diagnosis = delegate(
        f"Diagnose why this experiment is failing. Suggest fixes.",
        context={"logs": logs, "config": xp.config, "metrics": xp.metrics}
    )
    related = papers.search(xp.topic)
    suggestions = delegate(
        f"Which methods from these papers could fix the issues?",
        context={"diagnosis": diagnosis, "papers": related}
    )
    notebook.add(f"## Auto-diagnosis: {xp.name}\n{diagnosis}\n\n## Suggestions\n{suggestions}")
```

This is something no fixed sub-agent architecture could do. The LLM invents the orchestration.

### Phase 5: Model Tiering & Cost Control

**What**: Automatic model selection by depth and task type.

**Tiering**:
| Depth | Default Model | Use Case |
|-------|--------------|----------|
| 0 (root) | Opus | User interaction, planning, synthesis |
| 1 (sub-call) | Sonnet | Analysis, summarization, diagnosis |
| 2 (sub-sub-call) | Haiku | Extraction, classification, scanning |

**Cost guardrails**:
- Per-session budget (configurable, default $0.50)
- Per-delegate budget (default $0.05)
- Budget tracking exposed in REPL: `budget.remaining()`, `budget.spent()`
- Warning at 80% budget, hard stop at 100%
- Breadcrumb: "Budget: $0.12 / $0.50 spent"

### Phase 6: Experiment Log Analysis (Keystone Use Case)

**What**: The flagship capability — deep experiment analysis that serves the keystone loop.

**Scenario**: "Why is experiment X not improving after 5 runs?"

**Without RLM** (today): Nicolas calls `get_run_details` 5 times, each truncated to 12K chars, tries to reason over ~60K tokens of partial data. Misses patterns. Gives vague advice.

**With RLM-Nicolas**:
```python
# Nicolas writes this in the REPL
xp = experiments.get("X")
runs = experiments.runs(xp.id)

# Parallel analysis of each run
analyses = delegate_batch([
    {"prompt": f"Analyze run {r.id}: metrics, loss curves, hyperparams, errors",
     "context": {"logs": experiments.logs(r.id), "metrics": r.metrics, "config": r.config}}
    for r in runs
], model="haiku")

# Cross-run comparison
comparison = delegate(
    "Compare these run analyses. Identify: (1) what changed between runs, "
    "(2) what consistently fails, (3) the most promising direction",
    context={"analyses": analyses, "run_configs": [r.config for r in runs]}
)

# Check literature for solutions
papers_on_topic = papers.search(xp.topic)
lit_review = delegate(
    "Which methods from these papers address the failure modes identified?",
    context={"comparison": comparison, "papers": papers_on_topic}
)

FINAL(f"## Diagnosis\n{comparison}\n\n## Literature-Informed Suggestions\n{lit_review}")
```

**Result**: Deep, multi-perspective analysis with literature cross-referencing. No truncation. Cheap (5 Haiku + 2 Sonnet calls). The user gets a comprehensive lab report.

---

## 5. Integration with Existing Architecture

### What Stays the Same
- **MCP tool server**: All 46+ tools remain. REPL is a new tool, not a replacement.
- **WebSocket protocol**: Events stream as before. REPL adds `tool_start`/`tool_done` for `lab_repl`.
- **Desktop renderer**: Shows REPL tool calls like any other tool. Optionally: collapsible code blocks.
- **Session persistence**: Claude SDK still owns conversation history.
- **Agent registry**: Tier 3a/3b agents (Experimentalist, Spirits) untouched.

### What Changes
- **System prompt**: Slimmer (~500 tokens less). REPL instructions added.
- **Sub-agent layer**: Knowledge Agent/Research Agent become REPL templates, not Python classes.
- **agent_runtime/**: New `lab_repl.py` and `lab_api.py` files.
- **MCP server**: One new tool registered (`lab_repl`).
- **Cost tracking**: New budget system for sub-calls.

### Desktop UX Considerations
- REPL code blocks could render as collapsible "Nicolas is thinking..." sections
- Breadcrumbs from sub-calls show as inline status indicators (already wired)
- The `FINAL()` output is what the user sees as Nicolas's response
- For simple queries, Nicolas skips the REPL entirely — no visible change

---

## 6. Risks & Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Nicolas writes buggy REPL code | Medium | Safe builtins, 30s timeout, error recovery with retry |
| Runaway costs from recursive calls | Low | Budget system, depth limit (2), per-delegate cap |
| REPL adds latency to simple queries | N/A | Nicolas chooses when to use REPL; simple queries bypass it |
| Security: code injection via user input | Low | Sandbox has no network, no file I/O, no eval/exec; context is read-only |
| Over-engineering: REPL used when tool call suffices | Medium | System prompt guides when to use REPL vs. direct tools |
| Sub-call quality with Haiku | Low | Focused prompts + narrow context = Haiku's strength |

---

## 7. Why This Makes Nicolas the Best Entry Point

1. **Infinite effective context**: Users can ask questions that span their entire lab — all papers, all experiments, all history. No other research agent can do this.

2. **Emergent intelligence**: Nicolas develops its own analysis strategies rather than following hardcoded playbooks. Each lab is different; Nicolas adapts.

3. **Deep cross-entity reasoning**: "Find connections between my papers and experiments" becomes a programmatic join, not a multi-tool-call prayer.

4. **Comprehensive outputs**: No more truncated results. Nicolas builds reports, comparisons, and analyses of arbitrary length.

5. **Cost-efficient depth**: Haiku sub-calls make thorough analysis affordable. A full experiment diagnosis costs ~$0.05, not $0.50.

6. **The keystone loop, turbocharged**: "Run experiment → see it improve" becomes "run experiment → Nicolas deeply analyzes why it isn't improving → suggests literature-informed fixes → you iterate faster."

7. **Natural language in, deep reasoning out**: The user never sees the REPL. They ask a question, Nicolas thinks in code, they get a brilliant answer. The complexity is invisible.

---

## 8. Open Questions for Discussion

1. **REPL visibility**: Should the desktop show Nicolas's REPL code (like Claude Code shows tool calls), or hide it entirely? Showing it builds trust and lets power users learn; hiding it keeps the magic.

2. **Persistent vs. ephemeral sandbox**: Should the REPL state persist across conversation turns? The RLM paper uses persistent mode. This lets Nicolas build up analysis incrementally. But it also means stale state.

3. **Custom tools in sandbox**: Should users be able to inject their own Python functions into Nicolas's REPL? (e.g., custom metric calculators, domain-specific analysis scripts)

4. **Training a recursive Nicolas**: The RLM paper post-trained RLM-Qwen3-8B. Could we fine-tune a model specifically for Distillate's lab domain? Probably premature, but worth tracking.

5. **Async sub-calls**: The paper identifies synchronous sub-calls as a major performance bottleneck. Should we implement async from day 1, or start synchronous and optimize later?

---

## 9. Suggested Implementation Order

| Step | Phase | Effort | Impact |
|------|-------|--------|--------|
| 1 | Lab REPL sandbox + Lab API | 2-3 sessions | Foundation for everything |
| 2 | New MCP tool registration | 1 session | Wires REPL into Nicolas |
| 3 | Lean system prompt | 1 session | Immediate context relief |
| 4 | `llm_query()` sub-calls | 1-2 sessions | Unlocks recursive reasoning |
| 5 | `delegate()` recursive calls | 2 sessions | Full RLM capability |
| 6 | Batch sub-calls | 1 session | Performance for sweeps |
| 7 | Budget system | 1 session | Cost safety |
| 8 | Desktop REPL UX | 1-2 sessions | User-facing polish |
| 9 | Migrate sub-agents to templates | 1 session | Simplify architecture |
| 10 | Model tiering | 1 session | Cost optimization |

**Total**: ~12-15 sessions for full RLM-Nicolas.  
**MVP** (Phases 1-3): ~4-5 sessions. Already transformative.

---

## 10. References

- [Recursive Language Models](https://arxiv.org/abs/2512.24601) — Zhang, Kraska, Khattab (2025)
- [RLM Library](https://github.com/alexzhang13/rlm) — Official implementation
- [RLM Minimal](https://github.com/alexzhang13/rlm-minimal) — Minimal reference implementation
- [Blog Post](https://alexzhang13.github.io/blog/2025/rlm/) — Author's explanation
- [DSPy](https://github.com/stanfordnlp/dspy) — Khattab's declarative LM programming (related philosophy)
