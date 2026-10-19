"""The transcript runner must never keep a run that is not a genuine answer obtained through the server."""

import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("run_transcripts", Path(__file__).resolve().parents[1] / "scripts" / "run_transcripts.py")
rt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rt)

CALL = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "mcp__gb-carbon-charge__describe_dataset", "input": {}}]}}
RESULT = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "ok"}]}]}}
OK = {"type": "result", "subtype": "success", "is_error": False, "result": "The answer.", "num_turns": 2, "duration_ms": 1500}


def test_a_genuine_run_is_accepted_and_rendered_in_full():
    events = [CALL, RESULT, {"type": "assistant", "message": {"content": [{"type": "text", "text": "The answer."}]}}, OK]
    rt.check_run(events)
    md = rt.render("A question?", events)
    assert "**Tool call:** `describe_dataset`" in md and "**Tool result:** `describe_dataset`" in md and "**Agent:** The answer." in md


def test_a_login_failure_is_refused_not_recorded_as_an_answer():
    failed = {"type": "result", "is_error": True, "result": 'Failed to authenticate. API Error: 401 {"type":"error"}'}
    with pytest.raises(RuntimeError, match="the run failed: Failed to authenticate"):
        rt.check_run([{"type": "assistant", "message": {"content": [{"type": "text", "text": "Failed to authenticate"}]}}, failed])


def test_an_answer_given_without_ever_using_the_server_is_refused():
    with pytest.raises(RuntimeError, match="never called a tool on the server"):
        rt.check_run([{"type": "assistant", "message": {"content": [{"type": "text", "text": "From memory..."}]}}, OK])


def test_a_stream_with_no_result_is_refused():
    with pytest.raises(RuntimeError, match="no result event"):
        rt.check_run([CALL, RESULT])


def test_long_tool_results_are_truncated_in_the_markdown_but_say_so():
    big = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "x" * 5000}]}]}}
    md = rt.render("Q?", [CALL, big, OK])
    assert "3,200 more characters in the raw transcript" in md and "(5,000 characters)" in md


def test_local_paths_are_scrubbed_before_saving():
    text = rt.sanitise('{"cwd": "/Users/someone/Projects/x", "db": "/Users/someone/repo/data/c.duckdb"}', Path("/Users/someone/repo"))
    assert "someone" not in text and "<repo>" in text
