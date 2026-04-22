# Covers: distillate/experiment_tools/run_tools.py, distillate/hooks/post_bash.py
import io
import json
from unittest.mock import MagicMock, patch, mock_open
from distillate.experiment_tools.run_tools import conclude_run

@patch("distillate.lab_notebook.append_entry")
def test_conclude_run_writes_notebook_entry(mock_append):
    state = MagicMock()
    with patch("distillate.experiment_tools.run_tools._resolve_project", return_value=({"name": "test_proj", "path": "/tmp/test"}, None)):
        # Mock file operations to bypass FileNotFoundError
        with patch("builtins.open", mock_open()):
            with patch("pathlib.Path.exists", return_value=False):
                conclude_run(
                    state=state,
                    project="test_proj",
                    run_id="run123",
                    results={"val_loss": 0.312},
                    reasoning="good",
                    outcome="It worked",
                    verdict="confirmed",
                    belief_update="Lower LR is good",
                )
    
    mock_append.assert_called_once()
    args, kwargs = mock_append.call_args
    assert "Run ? [best]: val_loss=0.312" in args[0]
    # Outcome dropped from the template — it duplicated metric+verdict.
    assert "Outcome:" not in args[0]
    assert "Belief: Lower LR is good" in args[0]
    assert kwargs["entry_type"] == "run_completed"

@patch("distillate.lab_notebook.append_entry")
def test_conclude_run_notebook_entry_no_prediction(mock_append):
    state = MagicMock()
    with patch("distillate.experiment_tools.run_tools._resolve_project", return_value=({"name": "test_proj", "path": "/tmp/test"}, None)):
        with patch("builtins.open", mock_open()):
            with patch("pathlib.Path.exists", return_value=False):
                conclude_run(
                    state=state,
                    project="test_proj",
                    run_id="run123",
                    results={"val_loss": 0.312},
                    reasoning="good",
                )
    
    mock_append.assert_called_once()
    assert "Prediction:" not in mock_append.call_args[0][0]

@patch("distillate.lab_notebook.append_entry")
def test_conclude_run_notebook_entry_crash(mock_append):
    state = MagicMock()
    with patch("distillate.experiment_tools.run_tools._resolve_project", return_value=({"name": "test_proj", "path": "/tmp/test"}, None)):
        with patch("builtins.open", mock_open()):
            with patch("pathlib.Path.exists", return_value=False):
                conclude_run(
                    state=state,
                    project="test_proj",
                    run_id="run123",
                    status="crash",
                    results={},
                    reasoning="crashed",
                )
    
    mock_append.assert_called_once()
    assert "Run ? [crash]: (no metrics)" in mock_append.call_args[0][0]
    assert mock_append.call_args[1]["entry_type"] == "run_completed"


@patch("distillate.lab_notebook.append_entry")
def test_post_bash_no_longer_writes_notebook(mock_append):
    """post_bash.py must NOT write lab notebook entries for training runs.

    The rich entry is now written by conclude_run() after the agent
    has interpreted results. The old thin post_bash entry was removed.
    """
    import os
    from distillate.hooks import post_bash

    event = {
        "tool_name": "Bash",
        "tool_input": {"command": "python train.py --lr 0.01"},
        "tool_result": "Epoch 1: loss=0.5\nEpoch 2: loss=0.4",
        "session_id": "sess-abc",
    }

    env = {"DISTILLATE_SESSION": "1"}
    with patch.dict(os.environ, env), \
         patch("sys.stdin", io.StringIO(json.dumps(event))), \
         patch("distillate.hooks.post_bash._find_project_root", return_value=MagicMock()), \
         patch("distillate.hooks.post_bash._append_event"), \
         patch("distillate.hooks.post_bash._emit_epoch_metrics"), \
         patch("distillate.hooks.post_bash._check_running_entry"), \
         patch("distillate.hooks.post_bash._check_dirty_git"):
        post_bash.main()

    mock_append.assert_not_called()
