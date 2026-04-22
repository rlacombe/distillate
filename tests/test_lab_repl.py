# Covers: distillate/agent_runtime/lab_repl.py
"""Tests for the Lab REPL sandbox and Lab API."""

import pytest
from unittest.mock import MagicMock, patch

from distillate.agent_runtime.lab_repl import (
    CostTracker,
    BudgetExhaustedError,
    FinalResult,
    _validate_ast,
    _make_safe_builtins,
    _validate_tool_name,
    _extract_fallback_result,
    _run_delegate_turn,
    _build_delegate_sandbox,
    execute,
    reset_sandbox,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_repl():
    """Reset sandbox between tests."""
    reset_sandbox()
    yield
    reset_sandbox()


@pytest.fixture
def state():
    """Minimal State mock for sandbox init."""
    s = MagicMock()
    s.reload = MagicMock()
    s.documents_with_status = MagicMock(return_value=[])
    s.documents_processed_since = MagicMock(return_value=[])
    s.agents = {}
    return s


# ---------------------------------------------------------------------------
# AST Security Scanner
# ---------------------------------------------------------------------------

class TestASTValidation:
    def test_valid_code(self):
        assert _validate_ast("x = 1 + 2") == []

    def test_valid_multiline(self):
        assert _validate_ast("x = [1, 2, 3]\ny = sum(x)") == []

    def test_rejects_import(self):
        errors = _validate_ast("import os")
        assert len(errors) == 1
        assert "import not allowed" in errors[0]

    def test_rejects_from_import(self):
        errors = _validate_ast("from subprocess import run")
        assert len(errors) == 1
        assert "import not allowed" in errors[0]

    def test_rejects_dunder_class(self):
        errors = _validate_ast("x.__class__")
        assert any("__class__" in e for e in errors)

    def test_rejects_dunder_globals(self):
        errors = _validate_ast("f.__globals__")
        assert any("__globals__" in e for e in errors)

    def test_rejects_dunder_subclasses(self):
        errors = _validate_ast("().__class__.__subclasses__()")
        assert any("__subclasses__" in e or "__class__" in e for e in errors)

    def test_rejects_eval(self):
        errors = _validate_ast("eval('1+1')")
        assert any("eval" in e for e in errors)

    def test_rejects_exec(self):
        errors = _validate_ast("exec('x=1')")
        assert any("exec" in e for e in errors)

    def test_rejects_open(self):
        errors = _validate_ast("open('/etc/passwd')")
        assert any("open" in e for e in errors)

    def test_rejects_compile(self):
        errors = _validate_ast("compile('x', '', 'exec')")
        assert any("compile" in e for e in errors)

    def test_rejects_dunder_import(self):
        errors = _validate_ast("__import__('os')")
        assert any("__import__" in e for e in errors)

    def test_syntax_error(self):
        errors = _validate_ast("def (")
        assert len(errors) == 1
        assert "SyntaxError" in errors[0]

    def test_allows_normal_attributes(self):
        assert _validate_ast("x.title") == []
        assert _validate_ast("d.get('key')") == []


# ---------------------------------------------------------------------------
# Safe Builtins
# ---------------------------------------------------------------------------

class TestSafeBuiltins:
    def test_includes_core_types(self):
        b = _make_safe_builtins()
        for name in ["int", "str", "float", "bool", "list", "dict", "set", "tuple"]:
            assert name in b, f"Missing: {name}"

    def test_includes_iteration(self):
        b = _make_safe_builtins()
        for name in ["range", "enumerate", "zip", "map", "filter", "sorted"]:
            assert name in b, f"Missing: {name}"

    def test_includes_math_helpers(self):
        b = _make_safe_builtins()
        for name in ["abs", "max", "min", "sum", "round", "len"]:
            assert name in b, f"Missing: {name}"

    def test_excludes_dangerous(self):
        b = _make_safe_builtins()
        for name in ["eval", "exec", "compile", "open", "input", "__import__"]:
            assert name not in b, f"Should be excluded: {name}"

    def test_includes_constants(self):
        b = _make_safe_builtins()
        assert b["True"] is True
        assert b["False"] is False
        assert b["None"] is None


# ---------------------------------------------------------------------------
# FINAL Mechanism
# ---------------------------------------------------------------------------

class TestFINAL:
    def test_final_returns_result(self, state):
        result = execute("FINAL('hello world')", state)
        assert result["success"] is True
        assert result["output"] == "hello world"

    def test_final_stops_execution(self, state):
        code = "x = 1\nFINAL('done')\nx = 2"
        result = execute(code, state)
        assert result["success"] is True
        assert result["output"] == "done"

    def test_final_converts_to_string(self, state):
        result = execute("FINAL(42)", state)
        assert result["success"] is True
        assert result["output"] == "42"

    def test_final_with_print(self, state):
        code = "print('step 1')\nFINAL('result')"
        result = execute(code, state)
        assert result["success"] is True
        assert result["output"] == "result"
        assert "step 1" in result["stdout"]


# ---------------------------------------------------------------------------
# Print Capture
# ---------------------------------------------------------------------------

class TestPrintCapture:
    def test_captures_stdout(self, state):
        result = execute("print('hello')\nprint('world')", state)
        assert result["success"] is True
        assert "hello" in result["stdout"]
        assert "world" in result["stdout"]

    def test_no_output_message(self, state):
        result = execute("x = 1", state)
        assert result["success"] is True
        assert "no output" in result["output"].lower() or "FINAL" in result["output"]


# ---------------------------------------------------------------------------
# Namespace Persistence
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_variable_persists_across_calls(self, state):
        execute("my_var = 42", state)
        result = execute("FINAL(str(my_var))", state)
        assert result["success"] is True
        assert result["output"] == "42"

    def test_variables_listed(self, state):
        result = execute("foo = 1\nbar = 'hello'", state)
        assert "foo" in result["variables"]
        assert "bar" in result["variables"]

    def test_reserved_names_not_in_variables(self, state):
        result = execute("x = 1", state)
        assert "lab" not in result["variables"]
        assert "llm_query" not in result["variables"]
        assert "FINAL" not in result["variables"]

    def test_reserved_names_restored_after_overwrite(self, state):
        execute("lab = 'overwritten'", state)
        result = execute("FINAL(str(type(lab).__name__))", state)
        assert result["success"] is True
        assert result["output"] == "LabAPI"


# ---------------------------------------------------------------------------
# Error Handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_name_error(self, state):
        result = execute("print(undefined_var)", state)
        assert result["success"] is False
        assert "NameError" in result["output"]

    def test_type_error(self, state):
        result = execute("x = 1 + 'a'", state)
        assert result["success"] is False
        assert "TypeError" in result["output"]

    def test_zero_division(self, state):
        result = execute("x = 1/0", state)
        assert result["success"] is False
        assert "ZeroDivisionError" in result["output"]

    def test_security_error_import(self, state):
        result = execute("import os", state)
        assert result["success"] is False
        assert "Security error" in result["output"]

    def test_security_error_dunder(self, state):
        result = execute("x = ().__class__.__subclasses__()", state)
        assert result["success"] is False
        assert "Security error" in result["output"]


# ---------------------------------------------------------------------------
# Standard Library Access
# ---------------------------------------------------------------------------

class TestStdlib:
    def test_math_available(self, state):
        result = execute("FINAL(str(math.sqrt(16)))", state)
        assert result["success"] is True
        assert result["output"] == "4.0"

    def test_json_available(self, state):
        result = execute("FINAL(json.dumps({'a': 1}))", state)
        assert result["success"] is True
        assert result["output"] == '{"a": 1}'

    def test_re_available(self, state):
        result = execute("FINAL(str(bool(re.match(r'\\d+', '123'))))", state)
        assert result["success"] is True
        assert result["output"] == "True"

    def test_statistics_available(self, state):
        result = execute("FINAL(str(statistics.mean([1, 2, 3])))", state)
        assert result["success"] is True
        assert result["output"] == "2"

    def test_collections_available(self, state):
        result = execute(
            "c = collections.Counter([1,1,2,3])\nFINAL(str(c.most_common(1)[0]))",
            state,
        )
        assert result["success"] is True
        assert "(1, 2)" in result["output"]


# ---------------------------------------------------------------------------
# Lab API (smoke test with mocked state)
# ---------------------------------------------------------------------------

class TestLabAPI:
    def test_lab_available(self, state):
        result = execute("FINAL(type(lab).__name__)", state)
        assert result["success"] is True
        assert result["output"] == "LabAPI"

    def test_lab_repr(self, state):
        result = execute("FINAL(repr(lab))", state)
        assert result["success"] is True
        assert "papers" in result["output"]
        assert "experiments" in result["output"]


# ---------------------------------------------------------------------------
# Cost Tracker
# ---------------------------------------------------------------------------

class TestCostTracker:
    def test_initial_state(self):
        ct = CostTracker()
        assert ct.api_calls == 0
        assert ct.estimated_cost_usd == 0.0

    def test_record_updates(self):
        ct = CostTracker()
        mock_resp = MagicMock()
        mock_resp.usage.input_tokens = 1000
        mock_resp.usage.output_tokens = 500
        ct.record(mock_resp)
        assert ct.api_calls == 1
        assert ct.input_tokens == 1000
        assert ct.output_tokens == 500
        assert ct.estimated_cost_usd > 0

    def test_budget_exceeded(self):
        ct = CostTracker(session_budget_usd=0.001)
        mock_resp = MagicMock()
        mock_resp.usage.input_tokens = 100000
        mock_resp.usage.output_tokens = 100000
        ct.record(mock_resp)
        with pytest.raises(BudgetExhaustedError):
            ct.check_session_budget()

    def test_summary(self):
        ct = CostTracker()
        s = ct.summary()
        assert "api_calls" in s
        assert "est_usd" in s

    def test_cost_included_in_result(self, state):
        result = execute("x = 1", state)
        assert "cost" in result
        assert "api_calls" in result["cost"]


# ---------------------------------------------------------------------------
# SHOW_VARS
# ---------------------------------------------------------------------------

class TestShowVars:
    def test_show_vars(self, state):
        execute("a = 1\nb = 'hello'", state)
        result = execute("FINAL(SHOW_VARS())", state)
        assert result["success"] is True
        assert "a:" in result["output"]
        assert "b:" in result["output"]


# ---------------------------------------------------------------------------
# Lab API Deep Copy
# ---------------------------------------------------------------------------

class TestLabAPIDeepCopy:
    def test_returns_deep_copy(self):
        from distillate.agent_runtime.lab_api import _deep

        original = [{"key": "value", "nested": [1, 2, 3]}]
        copy = _deep(original)
        copy[0]["key"] = "modified"
        copy[0]["nested"].append(4)
        assert original[0]["key"] == "value"
        assert len(original[0]["nested"]) == 3


# ---------------------------------------------------------------------------
# Fluent collection API
# ---------------------------------------------------------------------------

def _make_experiments_state(experiments):
    """Build a minimal State mock returning the given experiments list."""
    from unittest.mock import patch

    s = MagicMock()
    s.reload = MagicMock()
    s.documents_with_status = MagicMock(return_value=[])
    s.agents = {}

    list_result = {"experiments": experiments}
    patcher = patch(
        "distillate.experiment_tools.list_experiments",
        return_value=list_result,
    )
    return s, patcher


class TestLabAPIFluent:
    _EXPERIMENTS = [
        {
            "id": "xp-1",
            "name": "Alpha",
            "created_at": "2024-01-10T10:00:00",
            "runs": [
                {"id": "r1", "completed_at": "2024-01-11T12:00:00", "results": {"accuracy": 0.82}},
                {"id": "r2", "completed_at": "2024-01-12T09:00:00", "results": {"accuracy": 0.91}},
            ],
        },
        {
            "id": "xp-2",
            "name": "Beta",
            "created_at": "2024-01-08T10:00:00",
            "runs": [
                {"id": "r3", "completed_at": "2024-01-09T08:00:00", "results": {"accuracy": 0.75}},
                {"id": "r4", "completed_at": "2024-01-09T14:00:00", "results": {"accuracy": 0.78}},
            ],
        },
    ]

    def test_experiments_api_not_overwritten(self, state):
        from distillate.agent_runtime.lab_api import LabAPI, ExperimentsAPI
        api = LabAPI(state)
        assert isinstance(api.experiments, ExperimentsAPI)

    def test_projects_api_accessible(self, state):
        from distillate.agent_runtime.lab_api import LabAPI, ProjectsAPI
        api = LabAPI(state)
        assert isinstance(api.projects, ProjectsAPI)

    def test_paper_example(self, state):
        s, patcher = _make_experiments_state(self._EXPERIMENTS)
        with patcher:
            result = execute(
                "FINAL(str(lab.experiments.recent(n=5).runs.peak_metric()))",
                s,
            )
        assert result["success"] is True
        assert "accuracy" in result["output"]
        assert "0.91" in result["output"]

    def test_run_collection_peak_metric_by_name(self, state):
        from distillate.agent_runtime.lab_api import RunCollection
        runs = [
            {"results": {"loss": 0.4, "accuracy": 0.80}},
            {"results": {"loss": 0.3, "accuracy": 0.85}},
        ]
        rc = RunCollection(runs)
        assert rc.peak_metric("accuracy") == 0.85
        assert rc.peak_metric("loss") == 0.4
        assert rc.peak_metric("nonexistent") is None

    def test_experiment_collection_runs_aggregates(self, state):
        from distillate.agent_runtime.lab_api import ExperimentCollection
        exps = [
            {"id": "e1", "runs": [{"id": "r1"}, {"id": "r2"}]},
            {"id": "e2", "runs": [{"id": "r3"}, {"id": "r4"}]},
        ]
        ec = ExperimentCollection(exps)
        assert len(ec.runs) == 4

    def test_recent_sorts_by_activity(self, state):
        s, patcher = _make_experiments_state(self._EXPERIMENTS)
        with patcher:
            result = execute(
                "ec = lab.experiments.recent(n=2)\nFINAL(list(ec)[0]['name'])",
                s,
            )
        # Alpha has a run completed 2024-01-12, Beta's latest is 2024-01-09
        assert result["success"] is True
        assert result["output"] == "Alpha"

    def test_run_collection_peak_metric_no_arg(self, state):
        from distillate.agent_runtime.lab_api import RunCollection
        runs = [
            {"results": {"accuracy": 0.80, "f1": 0.78}},
            {"results": {"accuracy": 0.85, "f1": 0.82}},
        ]
        rc = RunCollection(runs)
        best = rc.peak_metric()
        assert isinstance(best, dict)
        assert best["accuracy"] == 0.85
        assert best["f1"] == 0.82

    def test_experiment_collection_empty_runs(self, state):
        from distillate.agent_runtime.lab_api import ExperimentCollection
        ec = ExperimentCollection([{"id": "e1", "runs": []}, {"id": "e2"}])
        assert len(ec.runs) == 0

    def test_lab_repr_mentions_fluent_example(self, state):
        result = execute("FINAL(repr(lab))", state)
        assert result["success"] is True
        assert "recent" in result["output"]
        assert "peak_metric" in result["output"]


# ---------------------------------------------------------------------------
# DSPy HIGH-priority: Tool validation at init
# ---------------------------------------------------------------------------

class TestToolValidation:
    def test_valid_identifier_accepted(self):
        assert _validate_tool_name("my_tool") is None

    def test_valid_identifier_with_numbers(self):
        assert _validate_tool_name("tool2") is None

    def test_invalid_identifier_rejected(self):
        err = _validate_tool_name("not-valid")
        assert err is not None
        assert "not a valid Python identifier" in err

    def test_invalid_identifier_with_space(self):
        err = _validate_tool_name("my tool")
        assert err is not None

    def test_invalid_starts_with_digit(self):
        err = _validate_tool_name("2bad")
        assert err is not None

    def test_reserved_name_rejected(self):
        err = _validate_tool_name("FINAL")
        assert err is not None
        assert "reserved" in err

    def test_reserved_name_lab(self):
        err = _validate_tool_name("lab")
        assert err is not None
        assert "reserved" in err

    def test_reserved_name_eval(self):
        err = _validate_tool_name("eval")
        assert err is not None

    def test_sandbox_init_does_not_warn_for_own_tools(self):
        """Regression: _build_delegate_sandbox must not warn about its own injected names."""
        with patch("distillate.agent_runtime.lab_repl.log") as mock_log:
            sub_ns, _ = _build_delegate_sandbox(None, None)
        # The sandbox's own tools (FINAL, llm_query, etc.) must NOT trigger warnings
        mock_log.warning.assert_not_called()


# ---------------------------------------------------------------------------
# DSPy HIGH-priority: Fallback extraction on iteration timeout
# ---------------------------------------------------------------------------

class TestFallbackExtraction:
    def test_extracts_answer_var(self):
        ns = {"answer": "the answer is 42"}
        result = _extract_fallback_result(ns, "")
        assert result == "the answer is 42"

    def test_extracts_result_var(self):
        ns = {"result": [1, 2, 3]}
        result = _extract_fallback_result(ns, "")
        assert result == "[1, 2, 3]"

    def test_extracts_output_var(self):
        ns = {"output": "some output"}
        result = _extract_fallback_result(ns, "")
        assert result == "some output"

    def test_priority_answer_over_result(self):
        ns = {"answer": "from answer", "result": "from result"}
        result = _extract_fallback_result(ns, "")
        assert result == "from answer"

    def test_falls_back_to_last_output(self):
        ns = {}
        result = _extract_fallback_result(ns, "last stdout line")
        assert result == "last stdout line"

    def test_returns_none_when_nothing_available(self):
        result = _extract_fallback_result({}, "")
        assert result is None

    def test_handles_unstringifiable_var(self):
        class Broken:
            def __str__(self):
                raise RuntimeError("cannot stringify")
        ns = {"answer": Broken()}
        # Should not raise; falls through to next candidate
        result = _extract_fallback_result(ns, "fallback")
        assert result == "fallback"


# ---------------------------------------------------------------------------
# DSPy HIGH-priority: Delegate turn processing
# ---------------------------------------------------------------------------

class TestDelegateTurn:
    def test_no_code_block_returns_text_as_final(self):
        ns = {}
        step = _run_delegate_turn("Here is my analysis: the answer is 7.", ns)
        assert step.final == "Here is my analysis: the answer is 7."
        assert step.next_user_msg is None

    def test_code_block_executes_and_returns_next_msg(self):
        ns = {"__builtins__": __builtins__}
        step = _run_delegate_turn("```python\nx = 1 + 1\nprint(x)\n```", ns)
        assert step.final is None
        assert "2" in (step.next_user_msg or "")

    def test_code_block_with_final_returns_final(self):
        ns = {"__builtins__": __builtins__, "FINAL": FinalResult.__init__}
        # Use real FinalResult mechanism
        from distillate.agent_runtime.lab_repl import _final
        ns["FINAL"] = _final
        step = _run_delegate_turn("```python\nFINAL('done')\n```", ns)
        assert step.final == "done"
        assert step.next_user_msg is None

    def test_security_error_in_code_block(self):
        ns = {}
        step = _run_delegate_turn("```python\nimport os\n```", ns)
        assert step.final is None
        assert "Security error" in (step.next_user_msg or "")

    def test_runtime_error_in_code_block(self):
        ns = {"__builtins__": __builtins__}
        step = _run_delegate_turn("```python\nx = 1 / 0\n```", ns)
        assert step.final is None
        assert "ZeroDivisionError" in (step.next_user_msg or "")
