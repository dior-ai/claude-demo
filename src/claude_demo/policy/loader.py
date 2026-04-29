"""YAML policy loader.

Validates schema version, normalizes verdicts, and converts the YAML
shape into the immutable ``Policy`` object the runtime consumes.
Errors are explicit — a malformed policy must not silently default to
"allow everything."
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..core.permissions import Decision
from .schema import ForbiddenPattern, Policy

SUPPORTED_VERSIONS = {1}


class PolicyError(ValueError):
    """Raised when a policy file is malformed or references unknowns."""


def _decision_from_str(value: str, *, where: str) -> Decision:
    try:
        return Decision(value)
    except ValueError as exc:
        valid = ", ".join(d.value for d in Decision)
        raise PolicyError(
            f"{where}: invalid decision '{value}'. Must be one of: {valid}"
        ) from exc


def _coerce_policy(data: dict[str, Any], source: str) -> Policy:
    if not isinstance(data, dict):
        raise PolicyError(f"{source}: top-level must be a mapping, got {type(data).__name__}")

    version = data.get("version")
    if version not in SUPPORTED_VERSIONS:
        raise PolicyError(
            f"{source}: unsupported policy version {version!r}. "
            f"Supported: {sorted(SUPPORTED_VERSIONS)}"
        )

    metadata = data.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise PolicyError(f"{source}: 'metadata' must be a mapping")
    name = metadata.get("name") or Path(source).stem
    description = metadata.get("description") or ""

    tools_block = data.get("tools") or {}
    if not isinstance(tools_block, dict):
        raise PolicyError(f"{source}: 'tools' must be a mapping")

    default_decision = Decision.ALLOW
    tool_rules: dict[str, Decision] = {}
    for tool_name, rule in tools_block.items():
        if not isinstance(rule, dict) or "decision" not in rule:
            raise PolicyError(
                f"{source}: tools.{tool_name} must be a mapping with a 'decision' key"
            )
        verdict = _decision_from_str(rule["decision"], where=f"{source}: tools.{tool_name}")
        if tool_name == "default":
            default_decision = verdict
        else:
            tool_rules[tool_name] = verdict

    raw_allowlist = data.get("http_allowlist") or []
    if not isinstance(raw_allowlist, list):
        raise PolicyError(f"{source}: 'http_allowlist' must be a list")
    http_allowlist = frozenset(str(h) for h in raw_allowlist)

    raw_patterns = data.get("forbidden_code_patterns") or []
    if not isinstance(raw_patterns, list):
        raise PolicyError(f"{source}: 'forbidden_code_patterns' must be a list")
    patterns = []
    for entry in raw_patterns:
        if not isinstance(entry, dict) or "pattern" not in entry:
            raise PolicyError(
                f"{source}: each forbidden_code_patterns entry must be a "
                f"mapping with a 'pattern' key"
            )
        patterns.append(
            ForbiddenPattern(
                pattern=str(entry["pattern"]),
                reason=str(entry.get("reason", "forbidden by policy")),
            )
        )

    return Policy(
        name=str(name),
        description=str(description),
        tool_rules=tool_rules,
        default_decision=default_decision,
        http_allowlist=http_allowlist,
        forbidden_code_patterns=tuple(patterns),
    )


def load_policy(path: str | Path) -> Policy:
    """Read a YAML file and return the parsed Policy object."""
    p = Path(path)
    if not p.is_file():
        raise PolicyError(f"policy file not found: {p}")
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return _coerce_policy(data or {}, source=str(p))
