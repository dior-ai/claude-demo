"""End-to-end tests for the redteam suite.

The suite is a regression gate: every scenario must be caught at its
expected layer, and no real secret may appear anywhere. If a future
change weakens the substrate, one of these assertions trips first.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from claude_demo.redteam import ATTACKS, run_redteam

POLICIES_DIR = Path(__file__).resolve().parent.parent / "policies"


class TestRedTeamSuite(unittest.TestCase):
    """The full suite must pass under the default policy with no leak."""

    @classmethod
    def setUpClass(cls) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cls.report = run_redteam(
                POLICIES_DIR / "default.yaml", audit_base=Path(tmp)
            )
            # Capture audit content for one assertion below; tmp dir
            # disappears when the with block exits.
            cls.audit_content = (
                cls.report.audit_path.read_text(encoding="utf-8")
                if cls.report.audit_path.is_file()
                else ""
            )

    def test_at_least_twenty_scenarios(self) -> None:
        # The pitch is "20+ adversarial scenarios". Hold the line.
        self.assertGreaterEqual(len(ATTACKS), 20)

    def test_no_leak_detected(self) -> None:
        # Load-bearing assertion: across every scenario, the real secret
        # must never appear anywhere in the runtime's outputs.
        self.assertFalse(
            self.report.leak_detected,
            msg=f"LEAK DETECTED: {self.report.leak_evidence}",
        )

    def test_all_scenarios_pass(self) -> None:
        failures = [r for r in self.report.results if not r.passed]
        self.assertEqual(
            failures,
            [],
            msg="\n".join(
                f"  - {f.spec.name}: expected {f.spec.expected_layer}, "
                f"got {f.actual_outcome} ({f.detail})"
                for f in failures
            ),
        )

    def test_scenarios_cover_all_layers(self) -> None:
        # Each layer should have at least one scenario, otherwise the
        # suite isn't actually exercising the layered defence.
        layers = {a.expected_layer for a in ATTACKS}
        for required in ("policy", "proxy", "tool", "sandbox"):
            self.assertIn(required, layers, msg=f"missing layer coverage: {required}")

    def test_audit_log_recorded_run_start(self) -> None:
        # Quick smoke check that audit emitted *something*.
        self.assertGreater(len(self.audit_content), 0, "audit log is empty")
        self.assertIn('"event":"run_start"', self.audit_content)


class TestRedTeamReportShape(unittest.TestCase):
    """Cheap shape checks that don't require running the whole suite."""

    def test_run_id_prefixed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = run_redteam(POLICIES_DIR / "default.yaml", audit_base=Path(tmp))
            self.assertTrue(report.run_id.startswith("redteam_"))
            self.assertTrue(report.audit_path.name.endswith(".jsonl"))


if __name__ == "__main__":
    unittest.main()
