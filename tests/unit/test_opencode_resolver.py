from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
import unittest

from skill_manager.harness.catalog import supported_harness_definitions
from skill_manager.jsonc import strip_jsonc
from skill_manager.harness.resolution import resolve_context
from skill_manager.harness.contracts import ConfigSubtreeBindingProfile
from skill_manager.opencode.resolver import (
    OpenCodeConfigResolution,
    opencode_config_paths,
    opencode_skill_paths,
    resolve_opencode_config,
)


class OpenCodeResolverTests(unittest.TestCase):
    def test_resolves_xdg_paths_with_deterministic_precedence(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            xdg = root / "xdg"
            legacy = home / ".opencode" / "opencode.jsonc"
            xdg_json = xdg / "opencode" / "opencode.json"
            xdg_jsonc = xdg / "opencode" / "opencode.jsonc"
            legacy.parent.mkdir(parents=True)
            xdg_json.parent.mkdir(parents=True)
            legacy.write_text(
                '{"models": ["legacy"], "mcp": {"legacy": {"url": "legacy"}}, '
                '"nested": {"legacy": true, "shared": "legacy"}}',
                encoding="utf-8",
            )
            xdg_json.write_text(
                '{"models": ["xdg-json"], "mcp": {"json": {"url": "json"}}, '
                '"nested": {"json": true, "shared": "json"}}',
                encoding="utf-8",
            )
            xdg_jsonc.write_text(
                '{"models": ["xdg-jsonc"], "mcp": {"jsonc": {"url": "jsonc"}}, '
                '"nested": {"jsonc": true, "shared": "jsonc",},}',
                encoding="utf-8",
            )

            result = resolve_opencode_config(
                resolve_context({"HOME": str(home), "XDG_CONFIG_HOME": str(xdg)})
            )

        self.assertEqual(
            result.config,
            {
                "models": ["xdg-jsonc"],
                "mcp": {
                    "legacy": {"url": "legacy"},
                    "json": {"url": "json"},
                    "jsonc": {"url": "jsonc"},
                },
                "nested": {
                    "legacy": True,
                    "json": True,
                    "jsonc": True,
                    "shared": "jsonc",
                },
            },
        )
        self.assertEqual(
            [source.status for source in result.sources],
            ["loaded", "loaded", "loaded"],
        )
        self.assertEqual([source.path for source in result.sources], [
            Path(temp) / "home" / ".opencode" / "opencode.jsonc",
            Path(temp) / "xdg" / "opencode" / "opencode.json",
            Path(temp) / "xdg" / "opencode" / "opencode.jsonc",
        ])

    def test_missing_configs_return_empty_static_result(self) -> None:
        with TemporaryDirectory() as temp:
            result = resolve_opencode_config(
                resolve_context({"HOME": str(Path(temp) / "home")})
            )

        self.assertIsInstance(result, OpenCodeConfigResolution)
        self.assertEqual(result.config, {})
        self.assertEqual([source.status for source in result.sources], ["missing"] * 3)
        self.assertEqual(result.diagnostics, ())
        self.assertTrue(result.static_only)
        self.assertIn("static", result.limitation.lower())
        self.assertIn("plugin-added paths", result.limitation)

    def test_invalid_source_is_reported_without_blocking_valid_sources(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            xdg = root / "xdg"
            legacy = home / ".opencode" / "opencode.jsonc"
            invalid = xdg / "opencode" / "opencode.json"
            legacy.parent.mkdir(parents=True)
            invalid.parent.mkdir(parents=True)
            legacy.write_text('{"mcp": {"legacy": {"url": "legacy"}}}', encoding="utf-8")
            invalid.write_text("{not valid", encoding="utf-8")

            result = resolve_opencode_config(
                resolve_context({"HOME": str(home), "XDG_CONFIG_HOME": str(xdg)})
            )

        self.assertEqual(result.config, {"mcp": {"legacy": {"url": "legacy"}}})
        self.assertEqual([source.status for source in result.sources], ["loaded", "invalid", "missing"])
        self.assertEqual(len(result.diagnostics), 1)
        self.assertIn("invalid", result.diagnostics[0].lower())
        self.assertNotIn("not valid", result.diagnostics[0])

    def test_unterminated_block_comment_raises_json_decode_error_with_location(self) -> None:
        with self.assertRaises(json.JSONDecodeError) as captured:
            strip_jsonc('{} /* broken')

        self.assertEqual(captured.exception.pos, 3)
        self.assertEqual(captured.exception.lineno, 1)
        self.assertEqual(captured.exception.colno, 4)

    def test_non_object_config_is_reported_as_invalid(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "xdg" / "opencode" / "opencode.json"
            config.parent.mkdir(parents=True)
            config.write_text("[]", encoding="utf-8")

            result = resolve_opencode_config(
                resolve_context({
                    "HOME": str(root / "home"),
                    "XDG_CONFIG_HOME": str(root / "xdg"),
                })
            )

        self.assertEqual(result.config, {})
        self.assertEqual(result.sources[1].status, "invalid")
        self.assertIn("expected an object", result.sources[1].diagnostic or "")

    def test_skill_paths_keep_absolute_directories_and_skip_invalid_entries(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            valid = root / "valid"
            valid.mkdir()
            config = root / "xdg" / "opencode" / "opencode.json"
            config.parent.mkdir(parents=True)
            config.write_text(
                json.dumps(
                    {
                        "skills": {
                            "paths": [
                                str(valid),
                                "relative/path",
                                str(root / "missing"),
                                None,
                                42,
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )

            paths = opencode_skill_paths(
                resolve_context({
                    "HOME": str(root / "home"),
                    "XDG_CONFIG_HOME": str(root / "xdg"),
                })
            )

        self.assertEqual(paths, (valid,))

    def test_unreadable_source_is_reported_without_raising(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "xdg" / "opencode" / "opencode.json"
            config.parent.mkdir(parents=True)
            config.write_text("{}", encoding="utf-8")
            context = resolve_context(
                {"HOME": str(root / "home"), "XDG_CONFIG_HOME": str(root / "xdg")}
            )

            with mock.patch.object(Path, "read_text", side_effect=PermissionError):
                result = resolve_opencode_config(context)

        self.assertEqual(result.config, {})
        self.assertEqual(result.sources[1].status, "unreadable")
        self.assertEqual(len(result.diagnostics), 1)
        self.assertIn("unreadable", result.diagnostics[0].lower())
        self.assertNotIn("PermissionError", result.diagnostics[0])

    def test_jsonc_preserves_string_content_comments_and_trailing_commas(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "xdg" / "opencode" / "opencode.jsonc"
            config.parent.mkdir(parents=True)
            config.write_text(
                r'''
                {
                  // comment outside a string
                  "url": "https://example.test/mcp",
                  "literal": "// keep this /* text */",
                  "escaped": "quote: \" and slash: \\",
                  "punctuation": "comma, } ] // /*",
                  "nested": {
                    "value": "preserved",
                  },
                }
                ''',
                encoding="utf-8",
            )

            result = resolve_opencode_config(
                resolve_context({
                    "HOME": str(root / "home"),
                    "XDG_CONFIG_HOME": str(root / "xdg"),
                })
            )

        self.assertEqual(result.config["url"], "https://example.test/mcp")
        self.assertEqual(result.config["literal"], "// keep this /* text */")
        self.assertEqual(result.config["escaped"], 'quote: " and slash: \\')
        self.assertEqual(result.config["punctuation"], "comma, } ] // /*")
        self.assertEqual(result.config["nested"], {"value": "preserved"})

    def test_source_provenance_and_path_helper_are_read_only(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            xdg = root / "xdg"
            config = xdg / "opencode" / "opencode.jsonc"
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({"name": "static"}), encoding="utf-8")
            before = config.read_bytes()
            context = resolve_context({"HOME": str(home), "XDG_CONFIG_HOME": str(xdg)})

            self.assertEqual(opencode_config_paths(context)[-1], config)
            result = resolve_opencode_config(context)

            self.assertEqual(config.read_bytes(), before)

        loaded = next(source for source in result.sources if source.status == "loaded")
        self.assertEqual(loaded.path, config)
        self.assertEqual(loaded.format, "jsonc")

    def test_mcp_paths_reuse_static_sources_and_default_to_modern_jsonc(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            context = resolve_context({
                "HOME": str(root / "home"),
                "XDG_CONFIG_HOME": str(root / "xdg"),
            })
            definition = next(
                definition
                for definition in supported_harness_definitions()
                if definition.harness == "opencode"
            )
            profile = definition.binding_for("mcp")
            self.assertIsInstance(profile, ConfigSubtreeBindingProfile)

            mutation_paths = profile.resolve_discovery_config_paths(context)

        self.assertEqual(set(opencode_config_paths(context)), set(mutation_paths))
        self.assertEqual(profile.resolve_config_path(context), opencode_config_paths(context)[-1])


if __name__ == "__main__":
    unittest.main()
