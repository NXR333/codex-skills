import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import skills
from check_version import LATEST_RELEASE_URL, REPOSITORY, content_files, digest, display, inspect_skill, version_tuple


class VersionSystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.name = "coursework-word"
        self.folder = self.root / "source" / self.name
        self.folder.mkdir(parents=True)

    def release(self, version="1.0.0", text="first"):
        (self.folder / "SKILL.md").write_text(text, encoding="utf-8")
        (self.folder / "version.json").write_bytes(skills.json_bytes({
            "schema": 1, "name": self.name, "version": version,
            "repository": REPOSITORY, "files": content_files(self.folder),
        }))
        archive = skills.make_zip(self.folder)
        package = self.root / f"{version}.zip"
        package.write_bytes(archive)
        catalog = {"schema": 1, "repository": REPOSITORY, "collection_version": version, "skills": {
            self.name: {"version": version, "archive_sha256": digest(archive),
                        "manifest_sha256": digest((self.folder / "version.json").read_bytes()),
                        "archive_url": f"https://github.com/{REPOSITORY}/releases/download/v{version}/{self.name}-{version}.zip"}}}
        return catalog, package

    def test_semver_is_numeric(self):
        self.assertGreater(version_tuple("1.10.0"), version_tuple("1.9.0"))
        for invalid in ["1.0", "01.0.0", "../1.0.0", "latest"]:
            with self.assertRaises(ValueError):
                version_tuple(invalid)

    def test_current_and_modified_even_without_version_change(self):
        catalog, _ = self.release()
        self.assertEqual(inspect_skill(self.folder, catalog)["status"], "current")
        (self.folder / "SKILL.md").write_text("local edit")
        self.assertEqual(inspect_skill(self.folder, catalog)["status"], "locally_modified")

    def test_cli_works_with_windows_console_encoding(self):
        catalog, _ = self.release()
        path = self.root / "catalog.json"
        path.write_text(json.dumps(catalog), encoding="utf-8")
        command = [sys.executable, str(skills.ROOT / "scripts/check_version.py"),
                   "--skill-dir", str(self.folder), "--catalog", str(path)]
        result = subprocess.run(command, env=dict(os.environ, PYTHONIOENCODING="cp1251"), capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_update_link_in_text_and_json(self):
        first, package = self.release()
        target = self.root / "installed"
        skills.install(self.name, target, first, package)
        second, _ = self.release("1.0.1", "second")
        state = inspect_skill(target / self.name, second)
        self.assertEqual(state["status"], "update_available")
        self.assertEqual(state["latest_release_url"], LATEST_RELEASE_URL)
        for status in [state, {"skill": self.name, "status": "unavailable"}]:
            with self.subTest(status=status["status"]):
                with patch("sys.stdout", new_callable=io.StringIO) as output:
                    display(status)
                    self.assertIn(LATEST_RELEASE_URL, output.getvalue())
                with patch("sys.stdout", new_callable=io.StringIO) as output:
                    display(status, as_json=True)
                    self.assertEqual(json.loads(output.getvalue())["latest_release_url"], LATEST_RELEASE_URL)

    def test_added_deleted_files_and_cache(self):
        catalog, _ = self.release()
        cache = self.folder / "__pycache__"
        cache.mkdir()
        (cache / "scratch.pyc").write_bytes(b"cache")
        self.assertEqual(inspect_skill(self.folder, catalog)["status"], "current")
        (self.folder / "extra.txt").write_text("local")
        self.assertEqual(inspect_skill(self.folder, catalog)["modified_files"], ["extra.txt"])
        (self.folder / "extra.txt").unlink()
        (self.folder / "SKILL.md").unlink()
        self.assertEqual(inspect_skill(self.folder, catalog)["modified_files"], ["SKILL.md"])

    def test_install_update_backup_and_protect_edits(self):
        first, first_package = self.release()
        target = self.root / "installed"
        skills.install(self.name, target, first, first_package)
        second, second_package = self.release("1.1.0", "second")
        self.assertEqual(inspect_skill(target / self.name, second)["status"], "update_available")
        backup = skills.install(self.name, target, second, second_package, update=True)
        self.assertEqual((backup / "SKILL.md").read_text(), "first")
        self.assertEqual((target / self.name / "SKILL.md").read_text(), "second")
        third, third_package = self.release("1.2.0", "third")
        (target / self.name / "SKILL.md").write_text("my changes")
        with self.assertRaisesRegex(ValueError, "locally_modified"):
            skills.install(self.name, target, third, third_package, update=True)
        self.assertEqual((target / self.name / "SKILL.md").read_text(), "my changes")

    def test_corrupt_archive_never_replaces_installation(self):
        first, package = self.release()
        target = self.root / "installed"
        skills.install(self.name, target, first, package)
        second, package = self.release("1.1.0", "second")
        package.write_bytes(b"damaged")
        with self.assertRaisesRegex(ValueError, "checksum"):
            skills.install(self.name, target, second, package, update=True)
        self.assertEqual((target / self.name / "SKILL.md").read_text(), "first")

    def test_archive_traversal_is_rejected(self):
        catalog, _ = self.release()
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as z:
            z.writestr(f"{self.name}/../../outside.txt", "escape")
        archive = stream.getvalue()
        entry = dict(catalog["skills"][self.name], archive_sha256=digest(archive))
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            skills.unpack_verified(archive, entry, self.name, self.root / "extract")
        self.assertFalse((self.root / "outside.txt").exists())

    def test_failed_replacement_restores_old_installation(self):
        first, package = self.release()
        target = self.root / "installed"
        skills.install(self.name, target, first, package)
        second, package = self.release("1.1.0", "second")
        rename = Path.rename

        def fail_staged(source, destination):
            if source.parent.name.startswith(".skill-stage-"):
                raise OSError("simulated replacement failure")
            return rename(source, destination)

        with patch.object(Path, "rename", fail_staged):
            with self.assertRaisesRegex(OSError, "simulated"):
                skills.install(self.name, target, second, package, update=True)
        self.assertEqual((target / self.name / "SKILL.md").read_text(), "first")

    def test_reproducible_packages_and_unknown_install(self):
        catalog, _ = self.release()
        self.assertEqual(skills.make_zip(self.folder), skills.make_zip(self.folder))
        target = self.root / "installed"
        destination = target / self.name
        destination.mkdir(parents=True)
        (destination / "personal.txt").write_text("keep me")
        with self.assertRaisesRegex(ValueError, "unversioned"):
            skills.install(self.name, target, catalog, update=True)
        self.assertEqual((destination / "personal.txt").read_text(), "keep me")


if __name__ == "__main__":
    unittest.main()
