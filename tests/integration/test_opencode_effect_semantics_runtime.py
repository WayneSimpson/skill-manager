"""Isolated OpenCode runtime validation of permission effect semantics.

Runs the installed `opencode debug agent` command (read-only) against a fully
disposable HOME/config to prove through OpenCode itself that:
- explicit agent-level allow/ask/deny (incl. MCP wildcards) are honoured;
- agents with no agent-level permission block fall through to global config
  rules and OpenCode built-in defaults (Inherit semantics).

Skips when the opencode binary is unavailable or not a 1.18.x runtime. Never
touches the live ~/.config/opencode or any running OpenCode process.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

def _opencode_binary() -> str | None:
    return shutil.which("opencode")


def _runtime_version(binary: str) -> str | None:
    try:
        result = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"(\d+\.\d+\.\d+)", result.stdout or "")
    return match.group(1) if match else None


CONFIG = {
    # Global/broader permission rules (V1 top-level block).
    "permission": {
        "bash": "ask",
        "edit": "deny",
    },
    "agent": {
        "explicit": {
            "description": "Agent with explicit effects",
            "mode": "subagent",
            "prompt": "Explicit rules.",
            "permission": {
                "bash": "allow",           # Overrides the global ask.
                "n8n_nccio_*": "deny",     # MCP canonical wildcard.
                "playwright*": "ask",      # Legacy wildcard form.
            },
        },
        "plain": {
            "description": "Agent with no agent-level rules (all Inherit)",
            "mode": "subagent",
            "prompt": "No rules.",
        },
    },
}


class IsolatedEffectSemanticsRuntimeTests(unittest.TestCase):
    BINARY = _opencode_binary()

    @classmethod
    def setUpClass(cls):
        if cls.BINARY is None:
            raise unittest.SkipTest("opencode binary not available")
        version = _runtime_version(cls.BINARY)
        if version is None or not version.startswith("1.18."):
            raise unittest.SkipTest(f"opencode 1.18.x runtime required, found {version}")
        cls.version = version

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="task14b-opencode-")
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.home = self.root / "home"
        self.config_home = self.home / ".config"
        config_dir = self.config_home / "opencode"
        config_dir.mkdir(parents=True)
        (config_dir / "opencode.jsonc").write_text(json.dumps(CONFIG))
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()

    def _env(self) -> dict[str, str]:
        # Isolation is via XDG_* directories only: the opencode launcher
        # wrapper sources nvm from the real HOME, so HOME must stay real.
        # There is no legacy ~/.opencode on this machine, so config resolution
        # comes exclusively from the disposable XDG_CONFIG_HOME fixture.
        env = dict(os.environ)
        env.update({
            "XDG_CONFIG_HOME": str(self.config_home),
            "XDG_DATA_HOME": str(self.home / ".local" / "share"),
            "XDG_CACHE_HOME": str(self.home / ".cache"),
            "XDG_STATE_HOME": str(self.home / ".local" / "state"),
            "OPENCODE_DISABLE_MODELS_FETCH": "true",
            "OPENCODE_DISABLE_AUTOUPDATE": "true",
            "GIT_TERMINAL_PROMPT": "0",
        })
        return env

    def _debug_agent(self, name: str) -> dict:
        result = subprocess.run(
            [self.BINARY, "debug", "agent", name, "--pure"],
            capture_output=True, text=True, timeout=60,
            cwd=str(self.workspace), env=self._env(),
        )
        payload = result.stdout.strip()
        start = payload.find("{")
        self.assertGreaterEqual(
            start, 0, f"no JSON in debug output for {name}: {payload[:400]}")
        return json.loads(payload[start:])

    @staticmethod
    def _rules(payload: dict) -> dict[tuple[str, str], str]:
        """Map (permission, pattern) → action from the resolved rule list."""
        rules = {}
        for entry in payload.get("permission", []):
            if isinstance(entry, dict):
                rules[(entry.get("permission", ""), entry.get("pattern", "*"))] = \
                    entry.get("action", "")
        return rules

    def test_explicit_agent_rules_honoured_by_runtime(self):
        rules = self._rules(self._debug_agent("explicit"))
        # Agent-level explicit effects (V1 agent rules take precedence).
        self.assertEqual(rules.get(("bash", "*")), "allow")  # Beats global ask.
        self.assertEqual(rules.get(("n8n_nccio_*", "*")), "deny")
        self.assertEqual(rules.get(("playwright*", "*")), "ask")
        # Un-overridden global rules still apply for this agent.
        self.assertEqual(rules.get(("edit", "*")), "deny")

    def test_inherit_agent_falls_through_to_global_and_defaults(self):
        rules = self._rules(self._debug_agent("plain"))
        # No agent-level rules exist: global config resolves first...
        self.assertEqual(rules.get(("bash", "*")), "ask")
        self.assertEqual(rules.get(("edit", "*")), "deny")
        # ...and OpenCode built-in defaults are present (fall-through proof).
        self.assertIn(("doom_loop", "*"), rules)
        self.assertIn(("external_directory", "*"), rules)

    def test_live_config_untouched_by_isolated_runs(self):
        # Safety canary: the isolated runs must never touch the real config.
        live = Path.home() / ".config" / "opencode" / "opencode.jsonc"
        if live.exists():
            before = live.read_bytes()
            self._debug_agent("plain")
            self.assertEqual(live.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
