from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from skill_manager.application.skills.identity import SourceDescriptor
from skill_manager.application.skills.inventory import InventoryEntry, InventorySighting
from skill_manager.application.skills.presenters import skill_detail_payload


class SkillDetailLocationTests(unittest.TestCase):
    def test_same_physical_configured_runtime_location_is_displayed_once(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "skill"
            path.mkdir()
            alias = Path(temp) / "alias"
            alias.symlink_to(path, target_is_directory=True)
            other = Path(temp) / "different-skill"
            other.mkdir()
            source = SourceDescriptor(kind="runtime", locator="opencode")
            entry = InventoryEntry(skill_ref="test", name="test", description="", kind="unmanaged", source=source)
            for scope, location in (("configured", path), ("runtime", path), ("runtime", alias), ("runtime", other)):
                entry.add_sighting(InventorySighting(
                    kind="harness", harness="opencode", label="OpenCode", scope=scope,
                    path=location, revision="test", source=source,
                ))
            detail = skill_detail_payload(entry, columns=(), document_markdown=None, source_links=None)
            self.assertEqual([s["path"] for s in detail["locations"]], [str(path), str(other)])
            self.assertEqual(len(entry.sightings), 4)
            self.assertEqual({s.scope for s in entry.sightings}, {"configured", "runtime"})
