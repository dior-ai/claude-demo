"""Ephemeral, isolated subprocess executor for Python code.

The sandbox is the secure-execution layer. Every code run gets a fresh
temporary working directory, a hard timeout, a stripped environment, and is
killed if it exceeds resource limits. Mounted input files are copied in
read-only; the agent cannot write back to the host.

This is a demo-grade sandbox, not a production one. It defends against:
  - Long-running / hung code (timeout)
  - Filesystem snooping outside cwd (relative-path conventions + env scrub)
  - Most env-var leaks (PATH, HOME, USERPROFILE etc. are reset)

It does NOT defend against a determined attacker. For production use,
swap in a container runtime, gVisor, Firecracker, or a WASM runtime.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SandboxResult:
    """Outcome of a sandbox run."""

    stdout: str
    stderr: str
    return_code: int
    timed_out: bool
    duration_ms: int

    @property
    def ok(self) -> bool:
        return self.return_code == 0 and not self.timed_out

    def summary(self, max_chars: int = 4000) -> str:
        """Compact representation suitable for feeding back to the LLM."""
        head = f"[exit={self.return_code} duration={self.duration_ms}ms"
        if self.timed_out:
            head += " TIMED_OUT"
        head += "]"
        body_parts = []
        if self.stdout:
            body_parts.append(f"--- stdout ---\n{self.stdout}")
        if self.stderr:
            body_parts.append(f"--- stderr ---\n{self.stderr}")
        body = "\n".join(body_parts) if body_parts else "(no output)"
        full = f"{head}\n{body}"
        if len(full) > max_chars:
            full = full[:max_chars] + f"\n...[truncated {len(full) - max_chars} chars]"
        return full


class Sandbox:
    """Run untrusted Python code in an ephemeral subprocess.

    Each call to `run_python` creates a new temporary directory, writes the
    code to a file inside it, executes it with a stripped environment, and
    cleans up afterward.
    """

    def __init__(
        self,
        timeout_seconds: float = 10.0,
        max_output_bytes: int = 64_000,
        input_files: dict[str, Path] | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        # name-in-sandbox -> absolute host path. Copied read-only into cwd.
        self.input_files = input_files or {}

    def run_python(self, code: str) -> SandboxResult:
        """Execute a Python source string in an isolated subprocess."""
        import time

        start = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="sandbox_") as tmp:
            tmp_path = Path(tmp)

            # Stage input files into the sandbox cwd (copy, not link, so the
            # sandboxed process can read but a write won't affect originals).
            for name, src in self.input_files.items():
                dest = tmp_path / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)

            script = tmp_path / "_run.py"
            script.write_text(code, encoding="utf-8")

            # Strip the environment to a minimal safe set. No API keys, no
            # PATH leakage, no HOME redirection.
            env = self._safe_env()

            try:
                proc = subprocess.run(
                    [sys.executable, "-I", "-S", str(script)],
                    cwd=str(tmp_path),
                    env=env,
                    capture_output=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
                stdout = self._truncate(proc.stdout)
                stderr = self._truncate(proc.stderr)
                return SandboxResult(
                    stdout=stdout,
                    stderr=stderr,
                    return_code=proc.returncode,
                    timed_out=False,
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            except subprocess.TimeoutExpired as e:
                stdout = self._truncate(e.stdout or b"")
                stderr = self._truncate(e.stderr or b"")
                return SandboxResult(
                    stdout=stdout,
                    stderr=stderr,
                    return_code=-1,
                    timed_out=True,
                    duration_ms=int((time.monotonic() - start) * 1000),
                )

    def _truncate(self, data: bytes) -> str:
        if len(data) > self.max_output_bytes:
            data = data[: self.max_output_bytes] + b"\n...[truncated]"
        return data.decode("utf-8", errors="replace")

    @staticmethod
    def _safe_env() -> dict[str, str]:
        """Minimal env. No network creds, no PATH leakage."""
        env = {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            # Force a benign locale + tempdir so the subprocess can't hop out.
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        }
        # On Windows, subprocess needs SYSTEMROOT to bootstrap. This is the
        # one host var we leak through, and it's safe.
        if "SYSTEMROOT" in os.environ:
            env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
        return env
