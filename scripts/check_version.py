"""Read-only skill update and integrity check; Python 3.10+, standard library."""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.request import Request, urlopen

REPOSITORY = "NXR333/codex-skills"
CATALOG_URL = f"https://raw.githubusercontent.com/{REPOSITORY}/main/catalog.json"
IGNORE = {"__pycache__", ".git", ".DS_Store"}


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value):
        raise ValueError(f"Invalid version: {value!r}")
    return tuple(map(int, value.split(".")))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def content_files(root):
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(p in IGNORE for p in relative.parts) or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise ValueError(f"Symbolic link not allowed: {relative}")
        if path.is_file() and relative.as_posix() != "version.json":
            result[relative.as_posix()] = digest(path.read_bytes())
    return result


def integrity(root, manifest):
    expected = manifest.get("files")
    if not isinstance(expected, dict) or not expected:
        raise ValueError("Manifest has no file hashes")
    actual = content_files(root)
    return sorted(k for k in actual.keys() | expected.keys() if actual.get(k) != expected.get(k))


def fetch_bytes(url, limit=2_000_000, timeout=10):
    request = Request(url, headers={"User-Agent": "codex-skills-version-check/1.0"})
    with urlopen(request, timeout=timeout) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Download exceeds size limit")
    return data


def read_catalog(source=None):
    data = (Path(source).read_bytes() if source else fetch_bytes(CATALOG_URL))
    catalog = json.loads(data)
    if catalog.get("schema") != 1 or catalog.get("repository") != REPOSITORY:
        raise ValueError("Unexpected catalog schema or repository")
    if not isinstance(catalog.get("skills"), dict):
        raise ValueError("Invalid skill catalog")
    version_tuple(catalog["collection_version"])
    for name, entry in catalog["skills"].items():
        if not re.fullmatch(r"[a-z][a-z0-9-]*", name):
            raise ValueError("Invalid skill name")
        version_tuple(entry["version"])
        for key in ["manifest_sha256", "archive_sha256"]:
            if not re.fullmatch(r"[0-9a-f]{64}", entry[key]):
                raise ValueError("Invalid checksum in catalog")
    return catalog


def inspect_skill(root, catalog):
    root = Path(root)
    manifest_file = root / "version.json"
    result = {"skill": root.name, "status": "unversioned", "local_version": None,
              "latest_version": None, "modified_files": []}
    if not root.is_dir():
        result["status"] = "not_installed"
        return result
    if not manifest_file.is_file():
        return result
    manifest_bytes = manifest_file.read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get("name") != root.name or manifest.get("repository") != REPOSITORY:
        raise ValueError("Local manifest identity mismatch")
    local = version_tuple(manifest["version"])
    changed = integrity(root, manifest)
    result.update(local_version=manifest["version"], modified_files=changed)
    remote = catalog["skills"].get(root.name)
    if remote is None:
        result["status"] = "not_in_catalog"
        return result
    latest = version_tuple(remote["version"])
    result["latest_version"] = remote["version"]
    if changed:
        result["status"] = "locally_modified"
    elif local < latest:
        result["status"] = "update_available"
    elif local > latest:
        result["status"] = "newer_local"
    elif digest(manifest_bytes) != remote["manifest_sha256"]:
        result["status"] = "manifest_mismatch"
    else:
        result["status"] = "current"
    return result


LABELS = {
    "current": "Актуальная версия",
    "update_available": "Доступно обновление",
    "locally_modified": "Есть локальные изменения — автоматическая замена запрещена",
    "newer_local": "Локальная версия новее опубликованной",
    "manifest_mismatch": "Манифест отличается от опубликованного",
    "unversioned": "Копия без сведений о версии",
    "not_installed": "Скилл не установлен",
    "not_in_catalog": "Скилл отсутствует в каталоге",
    "unavailable": "Проверка недоступна; актуальность не подтверждена",
}


def display(result, as_json=False):
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        local, latest = result.get("local_version"), result.get("latest_version")
        print(f"{result['skill']}: {LABELS[result['status']]} ({local or '-'} -> {latest or '-'})")
        for name in result.get("modified_files", []):
            print(f"  {name}")
        if result.get("error"):
            print(f"  {result['error']}")


def main():
    parser = argparse.ArgumentParser(description="Проверить версию скилла без изменения файлов")
    parser.add_argument("--skill-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--catalog", help="Локальный каталог для проверки без сети")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = inspect_skill(args.skill_dir, read_catalog(args.catalog))
    except (OSError, ValueError, KeyError) as exc:
        result = {"skill": args.skill_dir.name, "status": "unavailable", "error": str(exc)}
    display(result, args.json)
    return 0 if result["status"] == "current" else 1 if result["status"] == "update_available" else 2


if __name__ == "__main__":
    sys.exit(main())
