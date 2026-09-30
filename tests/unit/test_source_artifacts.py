from __future__ import annotations

import io
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import tarfile
import unittest
from unittest.mock import patch
import zipfile

from skill_manager.sources import artifacts
from skill_manager.sources.artifacts import extract_source, validate_alias_boundary


def zip_archive(entries: list[tuple[str, str, str | bytes, int]]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        for name, kind, value, mode in entries:
            info = zipfile.ZipInfo("package/" + name)
            info.create_system = 3
            if kind == "dir":
                info.filename += "/"
                info.external_attr = (stat.S_IFDIR | mode) << 16
                bundle.writestr(info, b"")
            elif kind == "link":
                info.external_attr = (stat.S_IFLNK | mode) << 16
                bundle.writestr(info, value)
            else:
                info.external_attr = (stat.S_IFREG | mode) << 16
                bundle.writestr(info, value)
    return output.getvalue()


def tar_archive(entries: list[tuple[str, str, str | bytes, int]]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as bundle:
        for name, kind, value, mode in entries:
            info = tarfile.TarInfo("package/" + name)
            info.mode = mode
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                bundle.addfile(info)
            elif kind == "link":
                info.type = tarfile.SYMTYPE
                info.linkname = str(value)
                bundle.addfile(info)
            else:
                data = value.encode() if isinstance(value, str) else value
                info.size = len(data)
                bundle.addfile(info, io.BytesIO(data))
    return output.getvalue()


class SourceArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.destination = self.root / "artifact"

    def assert_rejected_and_clean(self, data: bytes, *, kind: str = "zip") -> None:
        with self.assertRaises(ValueError):
            extract_source(data, self.destination, kind=kind)
        self.assertFalse(self.destination.exists())

    def test_zip_materialises_file_and_directory_aliases_without_links(self) -> None:
        data = zip_archive([
            ("skills/one/SKILL.md", "file", "portable", 0o644),
            ("skills/one/run.sh", "file", "#!/bin/sh", 0o755),
            (".opencode/skills", "link", "../skills", 0o777),
            ("empty", "dir", "", 0o755),
        ])

        aliases = extract_source(data, self.destination, kind="zip")

        self.assertEqual(aliases, {".opencode/skills": "skills"})
        self.assertEqual((self.destination / ".opencode/skills/one/SKILL.md").read_text(), "portable")
        self.assertEqual((self.destination / ".opencode/skills/one/run.sh").read_text(), "#!/bin/sh")
        self.assertTrue((self.destination / "empty").is_dir())
        self.assertFalse(any(path.is_symlink() for path in self.destination.rglob("*")))
        self.assertTrue((self.destination / "skills/one/run.sh").stat().st_mode & stat.S_IXUSR)
        self.assertTrue((self.destination / ".opencode/skills/one/run.sh").stat().st_mode & stat.S_IXUSR)

    def test_tar_materialises_file_and_directory_aliases(self) -> None:
        data = tar_archive([
            ("target/data.txt", "file", "data", 0o644),
            ("alias-dir", "link", "target", 0o777),
            ("alias-file", "link", "target/data.txt", 0o777),
            ("empty", "dir", "", 0o755),
        ])

        aliases = extract_source(data, self.destination, kind="tar")

        self.assertEqual(aliases, {"alias-dir": "target", "alias-file": "target/data.txt"})
        self.assertEqual((self.destination / "alias-dir/data.txt").read_text(), "data")
        self.assertEqual((self.destination / "alias-file").read_text(), "data")
        self.assertTrue((self.destination / "empty").is_dir())
        self.assertFalse(any(path.is_symlink() for path in self.destination.rglob("*")))

    def test_alias_file_can_resolve_through_a_directory_alias(self) -> None:
        data = zip_archive([
            ("realDir/file.txt", "file", "data", 0o644),
            ("dirAlias", "link", "realDir", 0o777),
            ("a", "link", "dirAlias/file.txt", 0o777),
        ])

        aliases = extract_source(data, self.destination, kind="zip")

        self.assertEqual(aliases, {"dirAlias": "realDir", "a": "dirAlias/file.txt"})
        self.assertEqual((self.destination / "a").read_text(), "data")
        self.assertFalse((self.destination / "a").is_symlink())

    def test_forward_aliases_and_chains_are_copied_to_ordinary_files(self) -> None:
        data = zip_archive([
            ("a", "link", "b", 0o777),
            ("b", "link", "c", 0o777),
            ("c", "file", "content", 0o755),
        ])

        aliases = extract_source(data, self.destination, kind="zip")

        self.assertEqual(aliases, {"a": "b", "b": "c"})
        for name in ("a", "b", "c"):
            self.assertEqual((self.destination / name).read_text(), "content")
            self.assertFalse((self.destination / name).is_symlink())
        self.assertTrue((self.destination / "a").stat().st_mode & stat.S_IXUSR)

    def test_symlink_targets_reject_absolute_escape_missing_and_excluded_paths(self) -> None:
        cases = (
            ("absolute", "/etc/passwd", [("absolute", "link", "/etc/passwd", 0o777)]),
            ("escape", "../../outside", [("nested/escape", "link", "../../outside", 0o777)]),
            ("missing", "not-present", [("missing", "link", "not-present", 0o777)]),
            ("excluded", ".git/config", [("link", "link", ".git/config", 0o777),
                                             (".git/config", "file", "secret", 0o644)]),
        )
        for name, target, entries in cases:
            with self.subTest(name=name, target=target):
                self.assert_rejected_and_clean(zip_archive(entries))

    def test_cycles_and_output_type_collisions_are_rejected(self) -> None:
        self.assert_rejected_and_clean(zip_archive([
            ("a", "link", "b", 0o777),
            ("b", "link", "a", 0o777),
        ]))
        self.assert_rejected_and_clean(zip_archive([
            ("dirAlias", "link", "dirAlias/child", 0o777),
        ]))
        self.assert_rejected_and_clean(zip_archive([
            ("file", "file", "x", 0o644),
            ("file/child", "file", "x", 0o644),
        ]))
        self.assert_rejected_and_clean(zip_archive([
            ("target", "dir", "", 0o755),
            ("alias", "link", "target", 0o777),
            ("alias/child", "file", "collision", 0o644),
        ]))

    def test_archive_special_files_and_duplicate_paths_are_rejected(self) -> None:
        duplicate = zip_archive([
            ("same", "file", "one", 0o644),
            ("same/", "file", "two", 0o644),
        ])
        self.assert_rejected_and_clean(duplicate)

        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as bundle:
            special = tarfile.TarInfo("package/device")
            special.type = tarfile.CHRTYPE
            special.devmajor = 1
            special.devminor = 3
            bundle.addfile(special)
        self.assert_rejected_and_clean(output.getvalue(), kind="tar")

        special_zip = io.BytesIO()
        with zipfile.ZipFile(special_zip, "w") as bundle:
            info = zipfile.ZipInfo("package/device/")
            info.create_system = 3
            info.external_attr = (stat.S_IFCHR | 0o644) << 16
            bundle.writestr(info, b"")
        self.assert_rejected_and_clean(special_zip.getvalue())

        plain_tar = io.BytesIO()
        with tarfile.open(fileobj=plain_tar, mode="w") as bundle:
            member = tarfile.TarInfo("package/file")
            data = b"plain tar"
            member.size = len(data)
            bundle.addfile(member, io.BytesIO(data))
        self.assert_rejected_and_clean(plain_tar.getvalue(), kind="tar")

    def test_malformed_link_encoding_is_rejected(self) -> None:
        self.assert_rejected_and_clean(zip_archive([
            ("bad", "link", b"\xff\xfe", 0o777),
        ]))

    def test_parent_traversal_never_changes_alias_semantics_or_hides_missing_targets(self):
        for builder, kind in ((zip_archive, 'zip'), (tar_archive, 'tar')):
            for index, target in enumerate(('directory-alias/../file', 'missing/../file', '.git/../file')):
                with self.subTest(kind=kind, target=target):
                    self.destination = self.root / f'case-{kind}-{index}'
                    self.assert_rejected_and_clean(builder([
                        ('real/deep', 'dir', '', 0o755),
                        ('real/file', 'file', 'real target', 0o644),
                        ('file', 'file', 'different lexical target', 0o644),
                        ('directory-alias', 'link', 'real/deep', 0o777),
                        ('reference', 'link', target, 0o777),
                    ]), kind=kind)

    def test_input_and_expanded_limits_cover_size_count_and_depth(self) -> None:
        with patch.object(artifacts, "MAX_FILES", 2):
            self.assert_rejected_and_clean(zip_archive([
                ("one", "file", "1", 0o644),
                ("two", "file", "2", 0o644),
                ("three", "file", "3", 0o644),
            ]))
            self.assert_rejected_and_clean(zip_archive([
                ("target/data", "file", "x", 0o644),
                ("alias", "link", "target", 0o777),
            ]))

        with patch.object(artifacts, "MAX_EXPANDED", 3):
            self.assert_rejected_and_clean(zip_archive([
                ("large", "file", "1234", 0o644),
            ]))
        with patch.object(artifacts, "MAX_EXPANDED", 10):
            self.assert_rejected_and_clean(tar_archive([
                ("target/data", "file", "12345678", 0o644),
                ("alias", "link", "target", 0o777),
            ]), kind="tar")

        with patch.object(artifacts, "MAX_DEPTH", 4):
            self.assert_rejected_and_clean(zip_archive([
                ("target/a/b", "file", "x", 0o644),
                ("x/y/z", "link", "../../target", 0o777),
            ]))
        with patch.object(artifacts, "MAX_ALIAS_DEPTH", 2):
            self.assert_rejected_and_clean(zip_archive([
                ("a", "link", "b", 0o777),
                ("b", "link", "c", 0o777),
                ("c", "link", "d", 0o777),
                ("d", "file", "x", 0o644),
            ]))

    def test_failed_materialisation_cleans_partial_output_and_host_links_are_not_followed(self) -> None:
        corrupt = bytearray(zip_archive([
            ("ok", "file", "ok", 0o644),
            ("later", "file", "later", 0o644),
        ]))
        central_header = corrupt.find(b"PK\x01\x02")
        self.assertGreaterEqual(central_header, 0)
        corrupt[central_header + 16] ^= 0xFF
        self.assert_rejected_and_clean(bytes(corrupt))

        real_parent = self.root / "real-parent"
        real_parent.mkdir()
        existing_subdir = real_parent / "existing-subdir"
        existing_subdir.mkdir()
        linked_parent = self.root / "linked-parent"
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            extract_source(zip_archive([("file", "file", "unsafe", 0o644)]),
                          linked_parent / "existing-subdir" / "artifact", kind="zip")
        self.assertFalse((existing_subdir / "artifact").exists())

    def test_invalid_deflate_stream_cleans_partial_output(self) -> None:
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr('package/a-ok', 'first file')
            bundle.writestr('package/z-bad', 'later file')
        data = bytearray(output.getvalue())
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            item = bundle.getinfo('package/z-bad')
            start = item.header_offset + 30 + len(item.filename.encode()) + len(item.extra)
        data[start] = (data[start] & ~6) | 6  # Reserved DEFLATE block type.
        self.assert_rejected_and_clean(bytes(data))

    def test_alias_boundary_allows_nested_targets_and_rejects_crossed_chains(self) -> None:
        validate_alias_boundary(
            {"plugins/native/.opencode/skills": "plugins/native/skills"},
            "plugins/native",
        )
        validate_alias_boundary(
            {
                "plugins/native/a": "plugins/native/dirAlias/file",
                "plugins/native/dirAlias": "plugins/native/realDir",
            },
            "plugins/native",
        )

        for aliases in (
            {"plugins/native/.opencode/skills": "plugins/skills"},
            {"plugins": "plugins/other"},
            {"plugins": "plugins/native"},
            {
                "plugins/native/.opencode/skills": "plugins/native/alias",
                "plugins/native/alias": "plugins/skills",
            },
            {
                "plugins/native/a": "plugins/native/dirAlias/file",
                "plugins/native/dirAlias": "plugins/other",
            },
        ):
            with self.subTest(aliases=aliases):
                with self.assertRaises(ValueError):
                    validate_alias_boundary(aliases, "plugins/native")

    @unittest.skipUnless(hasattr(os, "symlink"), "symbolic links are unavailable")
    def test_extraction_never_creates_host_symlinks(self) -> None:
        extract_source(
            zip_archive([
                ("target/file", "file", "safe", 0o644),
                ("alias", "link", "target", 0o777),
            ]),
            self.destination,
            kind="zip",
        )
        self.assertFalse(any(path.is_symlink() for path in self.destination.rglob("*")))


if __name__ == "__main__":
    unittest.main()
