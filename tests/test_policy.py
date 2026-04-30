"""Tests for the policy loader + evaluator."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from claude_demo.core.hooks import HookEngine, PreToolUseEvent
from claude_demo.core.permissions import Decision
from claude_demo.policy import as_pre_hook, load_policy
from claude_demo.policy.loader import PolicyError
from claude_demo.policy.schema import ForbiddenPattern, ForbiddenSelector, Policy

POLICIES_DIR = Path(__file__).resolve().parent.parent / "policies"


class TestPolicyLoader(unittest.TestCase):
    def test_loads_default_policy(self) -> None:
        policy = load_policy(POLICIES_DIR / "default.yaml")
        self.assertEqual(policy.name, "default")
        self.assertEqual(policy.default_decision, Decision.ALLOW)
        self.assertIn("api.local", policy.http_allowlist)
        self.assertTrue(any("import socket" in fp.pattern for fp in policy.forbidden_code_patterns))

    def test_loads_prod_restricted(self) -> None:
        policy = load_policy(POLICIES_DIR / "prod-restricted.yaml")
        self.assertEqual(policy.decide("code_runner"), Decision.CONFIRM)
        self.assertEqual(policy.decide("bash"), Decision.DENY)
        self.assertEqual(policy.decide("anything_else"), Decision.ALLOW)

    def test_loads_gov_airgapped(self) -> None:
        policy = load_policy(POLICIES_DIR / "gov-airgapped.yaml")
        self.assertEqual(policy.default_decision, Decision.DENY)
        self.assertEqual(policy.decide("file_tool"), Decision.ALLOW)
        self.assertEqual(policy.decide("code_runner"), Decision.DENY)
        self.assertEqual(len(policy.http_allowlist), 0)

    def test_missing_file(self) -> None:
        with self.assertRaises(PolicyError):
            load_policy(Path("/no/such/policy.yaml"))

    def test_invalid_decision_string(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(
                "version: 1\n"
                "metadata: {name: bad}\n"
                "tools:\n"
                "  default: {decision: maybe}\n"
            )
            path = Path(f.name)
        try:
            with self.assertRaises(PolicyError):
                load_policy(path)
        finally:
            path.unlink(missing_ok=True)

    def test_unsupported_version(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write("version: 99\nmetadata: {name: future}\n")
            path = Path(f.name)
        try:
            with self.assertRaises(PolicyError):
                load_policy(path)
        finally:
            path.unlink(missing_ok=True)


class TestPolicyEvaluator(unittest.TestCase):
    def _hook(self, policy: Policy, *, confirm_fn=None):
        engine = HookEngine().add_pre(as_pre_hook(policy, confirm_fn=confirm_fn))
        return engine

    def test_deny_decision_blocks(self) -> None:
        policy = Policy(
            name="t",
            description="",
            tool_rules={"code_runner": Decision.DENY},
        )
        engine = self._hook(policy)
        event = PreToolUseEvent("code_runner", {"code": "print(1)"})
        engine.fire_pre(event)
        self.assertTrue(event.blocked)
        self.assertIn("denies tool", event.block_reason)

    def test_allow_decision_passes(self) -> None:
        policy = Policy(name="t", description="", default_decision=Decision.ALLOW)
        engine = self._hook(policy)
        event = PreToolUseEvent("any_tool", {})
        engine.fire_pre(event)
        self.assertFalse(event.blocked)

    def test_confirm_yes(self) -> None:
        policy = Policy(name="t", description="", default_decision=Decision.CONFIRM)
        engine = self._hook(policy, confirm_fn=lambda *_: True)
        event = PreToolUseEvent("any_tool", {})
        engine.fire_pre(event)
        self.assertFalse(event.blocked)

    def test_confirm_no(self) -> None:
        policy = Policy(name="t", description="", default_decision=Decision.CONFIRM)
        engine = self._hook(policy, confirm_fn=lambda *_: False)
        event = PreToolUseEvent("any_tool", {})
        engine.fire_pre(event)
        self.assertTrue(event.blocked)
        self.assertIn("declined", event.block_reason)

    def test_forbidden_code_pattern_blocks(self) -> None:
        policy = Policy(
            name="t",
            description="",
            forbidden_code_patterns=(
                ForbiddenPattern(pattern="import socket", reason="no network"),
            ),
        )
        engine = self._hook(policy)
        event = PreToolUseEvent(
            "code_runner", {"code": "import socket\ns = socket.socket()"}
        )
        engine.fire_pre(event)
        self.assertTrue(event.blocked)
        self.assertIn("import socket", event.block_reason)

    def test_forbidden_pattern_only_for_code_runner(self) -> None:
        policy = Policy(
            name="t",
            description="",
            forbidden_code_patterns=(
                ForbiddenPattern(pattern="dangerous", reason="x"),
            ),
        )
        engine = self._hook(policy)
        # http_request input has the bad pattern but isn't code_runner.
        event = PreToolUseEvent("http_request", {"body": "dangerous payload"})
        engine.fire_pre(event)
        self.assertFalse(event.blocked)


class TestBrowserPolicyEvaluator(unittest.TestCase):
    def _hook(self, policy: Policy, *, confirm_fn=None):
        return HookEngine().add_pre(as_pre_hook(policy, confirm_fn=confirm_fn))

    def test_browser_op_deny_blocks(self) -> None:
        policy = Policy(
            name="t",
            description="",
            browser_ops={"fill": Decision.DENY},
        )
        engine = self._hook(policy)
        event = PreToolUseEvent(
            "browser_tool", {"op": "fill", "selector": "#x", "value": "y"}
        )
        engine.fire_pre(event)
        self.assertTrue(event.blocked)
        self.assertIn("fill", event.block_reason)

    def test_browser_op_allow_passes(self) -> None:
        policy = Policy(
            name="t",
            description="",
            browser_ops={"goto": Decision.ALLOW},
        )
        engine = self._hook(policy)
        event = PreToolUseEvent(
            "browser_tool", {"op": "goto", "url": "http://x.local"}
        )
        engine.fire_pre(event)
        self.assertFalse(event.blocked)

    def test_browser_forbidden_selector_blocks(self) -> None:
        policy = Policy(
            name="t",
            description="",
            browser_forbidden_selectors=(
                ForbiddenSelector(pattern="card-number", reason="cc off-limits"),
            ),
        )
        engine = self._hook(policy)
        event = PreToolUseEvent(
            "browser_tool",
            {"op": "fill", "selector": "#card-number", "value": "..."},
        )
        engine.fire_pre(event)
        self.assertTrue(event.blocked)
        self.assertIn("card-number", event.block_reason)

    def test_browser_forbidden_selector_only_for_browser_tool(self) -> None:
        policy = Policy(
            name="t",
            description="",
            browser_forbidden_selectors=(
                ForbiddenSelector(pattern="card-number", reason="x"),
            ),
        )
        engine = self._hook(policy)
        # file_tool with a misleading "selector" key — not browser_tool.
        event = PreToolUseEvent(
            "file_tool", {"op": "read", "selector": "#card-number"}
        )
        engine.fire_pre(event)
        self.assertFalse(event.blocked)

    def test_browser_op_falls_back_to_tool_decision(self) -> None:
        # No per-op rule, but tool-level says deny → all ops blocked.
        policy = Policy(
            name="t",
            description="",
            tool_rules={"browser_tool": Decision.DENY},
        )
        engine = self._hook(policy)
        event = PreToolUseEvent("browser_tool", {"op": "goto", "url": "x"})
        engine.fire_pre(event)
        self.assertTrue(event.blocked)


class TestBrowserPolicyLoader(unittest.TestCase):
    def test_loads_browser_fields_from_default(self) -> None:
        policy = load_policy(POLICIES_DIR / "default.yaml")
        # Default doesn't restrict ops but does forbid sensitive selectors.
        patterns = {fs.pattern for fs in policy.browser_forbidden_selectors}
        self.assertIn("card-number", patterns)
        self.assertIn("password", patterns)

    def test_loads_browser_op_decision(self) -> None:
        policy = load_policy(POLICIES_DIR / "prod-restricted.yaml")
        self.assertEqual(policy.decide_browser_op("fill"), Decision.CONFIRM)

    def test_gov_airgapped_denies_browser_at_tool_level(self) -> None:
        policy = load_policy(POLICIES_DIR / "gov-airgapped.yaml")
        self.assertEqual(policy.decide("browser_tool"), Decision.DENY)


if __name__ == "__main__":
    unittest.main()
