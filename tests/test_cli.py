import json
import re

import pytest

from quickfix import claude_cli as c


def test_args_are_safe_for_a_cmd_wrapper_and_never_skip_permissions():
    args = c.build_args("claude", "sonnet", "low")
    assert "--dangerously-skip-permissions" not in args and "bypassPermissions" not in args
    assert args[args.index("--tools") + 1] == ""
    assert "--no-session-persistence" in args and "--safe-mode" in args
    # the schema has its own quoting; the free text must have no cmd.exe metacharacters
    assert not re.search(r'[&|<>^%"]', c.SYSTEM_PROMPT)


def test_selected_text_never_goes_on_the_command_line():
    secret = "slaptas tekstas"
    assert secret not in " ".join(c.build_args("claude", "sonnet", "low"))
    assert secret.encode() in c.build_stdin("fix", secret)


def test_stdin_is_utf8_with_lithuanian_and_emoji():
    raw = c.build_stdin("fix", "ąčęėįšųūž 😀\r\nantra")
    assert "ąčęėįšųūž 😀\nantra".encode("utf-8") in raw


def test_env_strips_what_would_bill_or_redirect():
    env = c.child_env({"ANTHROPIC_API_KEY": "x", "ANTHROPIC_AUTH_TOKEN": "x", "ANTHROPIC_BASE_URL": "http://evil",
                       "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_SIMPLE": "1", "PATH": "p",
                       "CLAUDE_CODE_OAUTH_TOKEN": "keep"})
    assert env["PATH"] == "p" and env["CLAUDE_CODE_OAUTH_TOKEN"] == "keep"
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK",
                 "CLAUDE_CODE_SIMPLE"):
        assert name not in env


def test_billing_warning():
    assert "ANTHROPIC_API_KEY" in c.billing_warning({"ANTHROPIC_API_KEY": "x"})
    assert c.billing_warning({"PATH": "p"}) == ""


@pytest.mark.parametrize("message,kind", [
    ("Not logged in · Please run /login", c.NOT_LOGGED_IN),
    ("Login expired · Please run /login", c.NOT_LOGGED_IN),
    ("Failed to authenticate: OAuth session expired and could not be refreshed", c.NOT_LOGGED_IN),
    ("Invalid API key", c.NOT_LOGGED_IN),
    ("You've hit your session limit · resets 7pm", c.USAGE_LIMIT),
    ("You've hit your weekly limit", c.USAGE_LIMIT),
    ("You've hit your Sonnet limit", c.USAGE_LIMIT),
    ("Server is temporarily limiting requests", c.USAGE_LIMIT),
    ("Request rejected (429)", c.USAGE_LIMIT),
    ("Credit balance is too low", c.USAGE_LIMIT),
    ("API Error: Repeated 529 Overloaded errors", c.OVERLOADED),
    ("Opus is experiencing high load", c.OVERLOADED),
    ("Unable to connect to API", c.NETWORK),
    ("API Error: 500 Internal Server Error", c.OVERLOADED),
    ("something else", c.OTHER),
])
def test_classify(message, kind):
    assert c.classify(message) == kind


def test_parse_prefers_structured_output():
    out = json.dumps({"is_error": False, "result": "x", "structured_output": {"corrected": "Aš čia 😀"}}).encode()
    assert c.parse_output(out, b"", 0).text == "Aš čia 😀"


def test_parse_falls_back_to_result():
    assert c.parse_output(json.dumps({"is_error": False, "result": "ok"}).encode(), b"", 0).text == "ok"


def test_parse_error_result_raises_with_kind():
    out = json.dumps({"is_error": True, "result": "Not logged in · Please run /login"}).encode()
    with pytest.raises(c.CliError) as e:
        c.parse_output(out, b"", 1)
    assert e.value.kind == c.NOT_LOGGED_IN


def test_parse_garbage_uses_stderr():
    with pytest.raises(c.CliError) as e:
        c.parse_output(b"", b"Unable to connect to API", 1)
    assert e.value.kind == c.NETWORK


def test_find_claude_respects_a_bad_override():
    assert c.find_claude(r"C:\nope\claude.exe") is None


def test_no_text_at_all_is_an_error_not_an_empty_success():
    with pytest.raises(c.CliError):
        c.parse_output(json.dumps({"is_error": False, "result": ""}).encode(), b"", 0)


def test_runner_retries_without_schema_after_an_unknown_failure(monkeypatch):
    runner, seen = c.ClaudeRunner(), []

    def once(instruction, text, cfg, schema):
        seen.append(schema)
        if schema:
            raise c.CliError(c.OTHER, "structured output failed")
        return c.CliResult("ok")

    monkeypatch.setattr(runner, "_run_once", once)
    assert runner.run("i", "t", None).text == "ok" and runner.run("i", "t", None).text == "ok"
    assert seen == [True, False, False]


@pytest.mark.parametrize("kind,detail", [(c.NOT_LOGGED_IN, "Not logged in"), (c.OTHER, "something else")])
def test_runner_does_not_resend_text_after_other_failures(monkeypatch, kind, detail):
    runner, seen = c.ClaudeRunner(), []

    def once(instruction, text, cfg, schema):
        seen.append(schema)
        raise c.CliError(kind, detail)

    monkeypatch.setattr(runner, "_run_once", once)
    with pytest.raises(c.CliError):
        runner.run("i", "t", None)
    assert seen == [True]


def test_cancel_before_the_process_exists_is_honoured(tmp_path, monkeypatch):
    monkeypatch.setenv("QUICKFIX_CONFIG_DIR", str(tmp_path))
    fake = tmp_path / "claude.exe"
    fake.write_bytes(b"")
    runner = c.ClaudeRunner()
    runner.begin()
    runner.cancel()
    cfg = type("Cfg", (), {"claude_path": str(fake), "model": "sonnet", "effort": "low", "timeout_s": 5})
    with pytest.raises(c.CliError) as e:
        runner.run("i", "t", cfg)
    assert e.value.kind == c.CANCELLED
