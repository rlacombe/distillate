# Covers: distillate/gemini_sdk.py, distillate/agents.py
import pytest
from unittest.mock import MagicMock, patch
from distillate.agents import get_agent, list_harness_adapters
from distillate.launcher import launch_experiment
from distillate.experiment_tools.workspace_tools import launch_coding_session_tool
from pathlib import Path

def test_gemini_agent_registered():
    agent = get_agent("gemini")
    assert agent["id"] == "gemini"
    assert agent["binary"] == "gemini"
    assert agent["mcp"] is True

def test_gemini_harness_adapter():
    adapters = list_harness_adapters()
    gemini_adapter = next((a for a in adapters if a["id"] == "gemini-cli"), None)
    assert gemini_adapter is not None
    assert gemini_adapter["mcp_support"] is True

@patch("subprocess.run")
def test_launch_experiment_with_gemini(mock_run, tmp_path):
    mock_run.return_value = MagicMock(returncode=0, stdout="tmux 3.3a")
    
    project_path = tmp_path / "test_project"
    project_path.mkdir()
    (project_path / ".distillate").mkdir()
    (project_path / "PROMPT.md").write_text("test prompt")
    
    # We need to mock _next_session_id and other helpers or just let them run
    with patch("distillate.launcher._next_session_id", return_value="session_001"), \
         patch("distillate.launcher._session_name", return_value="test-proj-001"), \
         patch("distillate.launcher._install_hooks_into") as mock_hooks, \
         patch("distillate.launcher._refresh_protocol_files"), \
         patch("distillate.claude_hooks.get_server_port", return_value=0):

        launch_experiment(project_path, agent_type="gemini", project={"name": "test-proj"})

        # Check if hooks were installed for gemini
        mock_hooks.assert_called_once_with(project_path, agent_type="gemini")
        
        # Check if tmux was started with gemini command
        found_gemini = False
        for call in mock_run.call_args_list:
            args = call[0][0]
            if isinstance(args, list) and "new-session" in args:
                cmd = " ".join(args)
                if "gemini" in cmd:
                    found_gemini = True
                    assert "--approval-mode default" in cmd
                    break
        assert found_gemini, f"Gemini command not found in tmux launch. Calls: {mock_run.call_args_list}"

@patch("subprocess.run")
def test_launch_coding_session_with_gemini(mock_run, tmp_path):
    mock_run.return_value = MagicMock(returncode=0)
    
    state = MagicMock()
    workspace = {"id": "test-ws", "name": "Test Workspace", "repos": [{"path": str(tmp_path), "name": "test-repo"}]}
    state.get_workspace.return_value = workspace
    state.workspaces = {"test-ws": workspace}
    
    with patch("distillate.experiment_tools.workspace_tools._find_workspace", return_value=workspace), \
         patch("distillate.claude_hooks.write_hook_config") as mock_hooks, \
         patch("distillate.experiment_tools.workspace_tools._start_transcript_logging"):

        launch_coding_session_tool(state=state, workspace="test-ws", agent="gemini")

        # Workspace coding sessions must NOT install HTTP status hooks —
        # those are reserved for Experimentalist sessions.
        mock_hooks.assert_not_called()

        # Check if tmux was started with gemini command
        found_gemini = False
        for call in mock_run.call_args_list:
            args = call[0][0]
            if isinstance(args, str) and "gemini" in args and "tmux new-session" in args:
                found_gemini = True
                # We now OMIT --resume on first launch for Gemini
                assert "--resume" not in args
                assert "--approval-mode default" in args
                break
        assert found_gemini, "Gemini command not found in tmux launch"

