"""Build, validate, install and update the two packaged coursework skills."""
import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from check_version import (REPOSITORY, content_files, digest, display, fetch_bytes,
                           inspect_skill, integrity, read_catalog, version_tuple)

ROOT = Path(__file__).resolve().parents[1]
NAMES = ("coursework-word", "coursework-diagrams")


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def default_install_root():
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "skills"


def make_zip(folder):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_STORED) as z:
        paths = [folder / p for p in content_files(folder)] + [folder / "version.json"]
        for p in sorted(paths, key=lambda item: item.relative_to(folder).as_posix()):
            info = zipfile.ZipInfo((Path(folder.name) / p.relative_to(folder)).as_posix(), (2026, 1, 1, 0, 0, 0))
            # Fixed ZIP metadata and no zlib dependency: same bytes on Windows/Linux.
            info.create_system = 3
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o100644 << 16
            z.writestr(info, p.read_bytes())
    return stream.getvalue()


def build():
    collection = (ROOT / "VERSION").read_text().strip()
    version_tuple(collection)
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    catalog = {"schema": 1, "repository": REPOSITORY, "collection_version": collection, "skills": {}}
    for name in NAMES:
        folder = ROOT / "skills" / name
        manifest = json.loads((folder / "version.json").read_bytes())
        if integrity(folder, manifest):
            raise ValueError(f"{name}: version.json must be refreshed before packaging")
        archive_name = f"{name}-{manifest['version']}.zip"
        archive = make_zip(folder)
        (output / archive_name).write_bytes(archive)
        catalog["skills"][name] = {
            "version": manifest["version"], "path": f"skills/{name}",
            "manifest_sha256": digest((folder / "version.json").read_bytes()),
            "archive": archive_name, "archive_sha256": digest(archive),
            "archive_url": f"https://github.com/{REPOSITORY}/releases/download/v{collection}/{archive_name}",
        }
    data = json_bytes(catalog)
    (ROOT / "catalog.json").write_bytes(data)
    (output / "catalog.json").write_bytes(data)
    sums = "".join(f"{digest(p.read_bytes())}  {p.name}\n" for p in sorted(output.glob("*.zip"))
                   if p.name in {x["archive"] for x in catalog["skills"].values()})
    (output / "SHA256SUMS.txt").write_text(sums, encoding="utf-8")
    print(f"Built collection {collection}")


def stamp(name, version, date):
    version_tuple(version)
    folder = ROOT / "skills" / name
    current = folder / "version.json"
    if current.exists():
        previous = json.loads(current.read_bytes())["version"]
        if version_tuple(version) <= version_tuple(previous):
            raise ValueError("A new release version must be greater than the existing version")
    manifest = {"schema": 1, "name": name, "version": version, "released_at": date,
                "repository": REPOSITORY, "files": content_files(folder)}
    current.write_bytes(json_bytes(manifest))


def validate(base_ref=None):
    catalog = read_catalog(ROOT / "catalog.json")
    canonical_checker = (ROOT / "scripts/check_version.py").read_bytes()
    changed_skill = False
    for name in NAMES:
        folder = ROOT / "skills" / name
        manifest = json.loads((folder / "version.json").read_bytes())
        if manifest.get("schema") != 1 or manifest.get("name") != name:
            raise ValueError(f"{name}: invalid manifest")
        result = inspect_skill(folder, catalog)
        if result["status"] != "current":
            raise ValueError(f"{name}: {result}")
        if (folder / "scripts/check_version.py").read_bytes() != canonical_checker:
            raise ValueError(f"{name}: bundled checker is out of sync")
        front = (folder / "SKILL.md").read_text(encoding="utf-8")
        if not front.startswith("---\n") or f"name: {name}" not in front or "description:" not in front:
            raise ValueError(f"{name}: invalid SKILL.md frontmatter")
        for path in folder.rglob("*.md"):
            for target in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
                if re.match(r"[a-z]+:", target) or target.startswith("#"):
                    continue
                destination = (path.parent / unquote(target.split("#")[0])).resolve()
                if not destination.is_relative_to(folder.resolve()) or not destination.exists():
                    raise ValueError(f"{path.relative_to(ROOT)}: missing or external local link {target}")
        archive = make_zip(folder)
        if digest(archive) != catalog["skills"][name]["archive_sha256"]:
            raise ValueError(f"{name}: package does not match catalog")
        if base_ref:
            previous = subprocess.run(["git", "show", f"{base_ref}:skills/{name}/version.json"],
                                      cwd=ROOT, capture_output=True)
            if previous.returncode == 0:
                old = json.loads(previous.stdout)
                changed_skill |= old != manifest
                if old != manifest and version_tuple(manifest["version"]) <= version_tuple(old["version"]):
                    raise ValueError(f"{name}: changed skill requires a higher version")
        print(f"OK {name} {manifest['version']} ({len(manifest['files'])} files)")
    if base_ref and changed_skill:
        previous = subprocess.run(["git", "show", f"{base_ref}:VERSION"], cwd=ROOT, capture_output=True)
        if previous.returncode == 0 and version_tuple(catalog["collection_version"]) <= version_tuple(previous.stdout.decode().strip()):
            raise ValueError("Changed skills require a higher collection VERSION for the next release")


def unpack_verified(archive, entry, name, destination):
    if digest(archive) != entry["archive_sha256"]:
        raise ValueError("Archive checksum mismatch")
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        if sum(x.file_size for x in z.infolist()) > 100_000_000:
            raise ValueError("Archive expands beyond the size limit")
        seen = set()
        for info in z.infolist():
            path = PurePosixPath(info.filename)
            if (path.is_absolute() or ".." in path.parts or "\\" in info.filename or
                    ":" in info.filename or not path.parts or path.parts[0] != name or
                    (info.external_attr >> 16) & 0o170000 == 0o120000 or info.filename in seen):
                raise ValueError("Unsafe archive member")
            seen.add(info.filename)
        z.extractall(destination)
    staged = destination / name
    manifest_data = (staged / "version.json").read_bytes()
    if digest(manifest_data) != entry["manifest_sha256"]:
        raise ValueError("Manifest checksum mismatch")
    manifest = json.loads(manifest_data)
    if (manifest.get("name") != name or manifest.get("version") != entry["version"] or
            manifest.get("repository") != REPOSITORY or integrity(staged, manifest)):
        raise ValueError("Extracted skill does not match manifest")
    return staged


def install(name, install_root, catalog, package=None, update=False):
    install_root = Path(install_root).resolve()
    install_root.mkdir(parents=True, exist_ok=True)
    destination = install_root / name
    if destination.is_symlink() or destination.resolve().parent != install_root:
        raise ValueError("Unexpected installation path")
    if destination.exists():
        if not update:
            raise ValueError("Skill already exists; use update")
        state = inspect_skill(destination, catalog)
        if state["status"] == "current":
            print(f"{name}: already current")
            return None
        if state["status"] != "update_available":
            raise ValueError(f"Refusing to overwrite {state['status']}: preserve or review local changes first")
    elif update:
        raise ValueError("Skill is not installed; use install")
    entry = catalog["skills"][name]
    expected_url = f"https://github.com/{REPOSITORY}/releases/download/v{catalog['collection_version']}/{name}-{entry['version']}.zip"
    if entry["archive_url"] != expected_url:
        raise ValueError("Unexpected package URL")
    archive = Path(package).read_bytes() if package else fetch_bytes(expected_url, limit=50_000_000, timeout=60)
    backup = None
    with tempfile.TemporaryDirectory(prefix=".skill-stage-", dir=install_root) as temporary:
        staged = unpack_verified(archive, entry, name, Path(temporary))
        if destination.exists():
            backup_root = install_root.parent / "skill-backups"
            if backup_root.is_symlink():
                raise ValueError("Backup directory cannot be a symbolic link")
            backup_root.mkdir(exist_ok=True)
            stamp_name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = backup_root / f"{name}-{stamp_name}-{uuid.uuid4().hex[:8]}"
            destination.rename(backup)
        try:
            staged.rename(destination)
        except BaseException:
            if backup and not destination.exists():
                backup.rename(destination)
            raise
    print(f"Installed {name} {entry['version']}" + (f"; backup: {backup}" if backup else ""))
    return backup


def main():
    parser = argparse.ArgumentParser(description="Версии курсовых скиллов")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build")
    v = sub.add_parser("validate")
    v.add_argument("--base-ref")
    p = sub.add_parser("stamp")
    p.add_argument("skill", choices=NAMES)
    p.add_argument("version")
    p.add_argument("--date", default=datetime.now(timezone.utc).date().isoformat())
    for command in ["check", "install", "update"]:
        p = sub.add_parser(command)
        p.add_argument("skill", choices=NAMES, nargs="?" if command == "check" else None)
        p.add_argument("--root", type=Path, default=default_install_root())
        p.add_argument("--catalog", type=Path)
        if command != "check":
            p.add_argument("--package", type=Path, help="Проверяемый локальный ZIP вместо загрузки")
    args = parser.parse_args()
    try:
        if args.command == "build":
            build()
        elif args.command == "validate":
            validate(args.base_ref)
        elif args.command == "stamp":
            stamp(args.skill, args.version, args.date)
        else:
            catalog = read_catalog(args.catalog)
            if args.command == "check":
                code = 0
                for name in [args.skill] if args.skill else NAMES:
                    result = inspect_skill(args.root / name, catalog)
                    display(result)
                    code = max(code, 0 if result["status"] == "current" else 1)
                return code
            install(args.skill, args.root, catalog, args.package, args.command == "update")
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
