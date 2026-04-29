"""Tests for OpenAIAgentRunner using a fake OpenAI client.

We don't hit the real API. The fake client implements the surface the
runner uses (``client.chat.completions.create(...)`` returning an object
with ``.choices[0].message`` and ``.usage``) and emits a hand-scripted
sequence of "responses" that exercise the loop's control flow:

  - tool_call dispatch
  - hook blocking via PreToolUse
  - the tool message round-trip into history
  - termination on no-more-tool-calls
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from typing import Any

from claude_demo.agents.openai import OpenAIAgentRunner
from claude_demo.core.hooks import HookEngine, PreToolUseEvent, ToolBlocked
from claude_demo.tools.base import Tool


# ---------------------------------------------------------------------------
# Fake OpenAI client
# ---------------------------------------------------------------------------


@dataclass
class _FakeFunction:
    name: str
    arguments: str


@dataclass
class _FakeToolCall:
    id: str
    function: _FakeFunction
    type: str = "function"


@dataclass
class _FakeMessage:
    content: str | None = None
    tool_calls: list[_FakeToolCall] | None = None


@dataclass
class _FakeChoice:
    message: _FakeMessage
    finish_reason: str = "stop"


@dataclass
class _FakeUsage:
    prompt_tokens: int = 1
    completion_tokens: int = 1


@dataclass
class _FakeResponse:
    choices: list[_FakeChoice]
    usage: _FakeUsage = field(default_factory=_FakeUsage)


class _FakeChatCompletions:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeOpenAI: ran out of scripted responses")
        return self._responses.pop(0)


class _FakeChat:
    def __init__(self, completions: _FakeChatCompletions) -> None:
        self.completions = completions


class _FakeOpenAI:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._completions = _FakeChatCompletions(responses)
        self.chat = _FakeChat(self._completions)


def _final(text: str) -> _FakeResponse:
    return _FakeResponse(
        choices=[_FakeChoice(message=_FakeMessage(content=text), finish_reason="stop")]
    )


def _toolcall(call_id: str, name: str, args_json: str) -> _FakeResponse:
    return _FakeResponse(
        choices=[
            _FakeChoice(
                message=_FakeMessage(
                    content=None,
                    tool_calls=[
                        _FakeToolCall(
                            id=call_id, function=_FakeFunction(name=name, arguments=args_json)
                        )
                    ],
                ),
                finish_reason="tool_calls",
            )
        ]
    )


# ---------------------------------------------------------------------------
# Tools used in tests
# ---------------------------------------------------------------------------


def _echo_tool() -> Tool:
    return Tool(
        name="echo",
        description="echoes the msg field",
        input_schema={
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        },
        run=lambda inp: f"echo:{inp.get('msg', '')}",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOpenAIAgentRunner(unittest.TestCase):
    def test_terminates_when_no_tool_calls(self) -> None:
        client = _FakeOpenAI([_final("done.")])
        runner = OpenAIAgentRunner(tools=[_echo_tool()], hooks=HookEngine(), client=client)
        state = runner.run("hello")
        self.assertEqual(state.final_text, "done.")
        self.assertEqual(state.step_count, 1)
        self.assertEqual(state.tool_call_count, 0)

    def test_dispatches_tool_call_then_terminates(self) -> None:
        client = _FakeOpenAI(
            [
                _toolcall("call_1", "echo", '{"msg":"hi"}'),
                _final("Got it: hi."),
            ]
        )
        runner = OpenAIAgentRunner(tools=[_echo_tool()], hooks=HookEngine(), client=client)
        state = runner.run("say hi")
        self.assertEqual(state.tool_call_count, 1)
        first_call = state.steps[0].tool_calls[0]
        self.assertEqual(first_call.tool_name, "echo")
        self.assertEqual(first_call.result, "echo:hi")
        self.assertFalse(first_call.is_error)
        self.assertEqual(state.final_text, "Got it: hi.")

    def test_pre_hook_blocks_tool_call(self) -> None:
        def deny(event: PreToolUseEvent) -> None:
            if event.tool_name == "echo":
                raise ToolBlocked("policy says no")

        client = _FakeOpenAI(
            [
                _toolcall("call_1", "echo", '{"msg":"hi"}'),
                _final("Ok, stopping."),
            ]
        )
        hooks = HookEngine().add_pre(deny)
        runner = OpenAIAgentRunner(tools=[_echo_tool()], hooks=hooks, client=client)
        state = runner.run("say hi")

        call = state.steps[0].tool_calls[0]
        self.assertTrue(call.blocked)
        self.assertIn("policy says no", call.block_reason)
        self.assertTrue(call.is_error)

    def test_unknown_tool_is_marked_error(self) -> None:
        client = _FakeOpenAI(
            [
                _toolcall("call_1", "missing_tool", "{}"),
                _final("oops."),
            ]
        )
        runner = OpenAIAgentRunner(tools=[_echo_tool()], hooks=HookEngine(), client=client)
        state = runner.run("x")
        call = state.steps[0].tool_calls[0]
        self.assertTrue(call.is_error)
        self.assertIn("unknown tool", call.result)

    def test_invalid_json_in_arguments_is_recovered(self) -> None:
        # Model occasionally emits invalid JSON in arguments; the runner
        # should not crash — it should pass the raw text through and let
        # the tool decide what to do.
        client = _FakeOpenAI(
            [
                _toolcall("call_1", "echo", "not valid json"),
                _final("recovered."),
            ]
        )
        runner = OpenAIAgentRunner(tools=[_echo_tool()], hooks=HookEngine(), client=client)
        state = runner.run("x")
        # echo gets {"_raw": "not valid json"}; missing 'msg' so it echoes "" — no crash.
        call = state.steps[0].tool_calls[0]
        self.assertFalse(call.is_error)
        self.assertEqual(state.final_text, "recovered.")

    def test_tool_schemas_are_function_shaped(self) -> None:
        client = _FakeOpenAI([_final("done.")])
        runner = OpenAIAgentRunner(tools=[_echo_tool()], hooks=HookEngine(), client=client)
        runner.run("x")
        sent_tools = client.chat.completions.calls[0]["tools"]
        self.assertEqual(sent_tools[0]["type"], "function")
        self.assertEqual(sent_tools[0]["function"]["name"], "echo")
        self.assertIn("parameters", sent_tools[0]["function"])

    def test_assistant_turn_is_echoed_into_history_with_tool_calls(self) -> None:
        client = _FakeOpenAI(
            [
                _toolcall("call_1", "echo", '{"msg":"a"}'),
                _final("done."),
            ]
        )
        runner = OpenAIAgentRunner(tools=[_echo_tool()], hooks=HookEngine(), client=client)
        runner.run("x")
        # On the SECOND request, the message history must contain the
        # assistant turn (with tool_calls) and the tool result message.
        second_messages = client.chat.completions.calls[1]["messages"]
        roles = [m["role"] for m in second_messages]
        self.assertIn("assistant", roles)
        self.assertIn("tool", roles)
        # Tool message must reference the call ID we sent back.
        tool_msg = next(m for m in second_messages if m["role"] == "tool")
        self.assertEqual(tool_msg["tool_call_id"], "call_1")
        self.assertEqual(tool_msg["content"], "echo:a")


if __name__ == "__main__":
    unittest.main()
