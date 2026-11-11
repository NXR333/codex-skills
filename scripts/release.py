"""Publish validated ZIPs to a GitHub release from a tag-triggered workflow."""
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from check_version import REPOSITORY, digest
from skills import ROOT, validate


def api(path, method="GET", body=None, binary=False):
    token = os.environ["GITHUB_TOKEN"]
    headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2026-03-10", "User-Agent": "codex-skills-release"}
    if body is not None:
        headers["Content-Type"] = "application/octet-stream" if binary else "application/json"
        if not binary:
            body = json.dumps(body).encode()
    host = "https://uploads.github.com" if binary else "https://api.github.com"
    with urlopen(Request(host + path, data=body, headers=headers, method=method), timeout=60) as r:
        return json.load(r)


def main():
    validate()
    version = (ROOT / "VERSION").read_text().strip()
    tag = "v" + version
    if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY or os.environ.get("GITHUB_REF_NAME") != tag:
        raise ValueError("Unexpected repository or release tag")
    base = f"/repos/{REPOSITORY}/releases"
    try:
        release = api(base + "/tags/" + tag)
    except HTTPError as exc:
        if exc.code != 404:
            raise
        release = api(base, "POST", {"tag_name": tag, "name": f"Курсовые скиллы {version}",
            "draft": True, "body": (ROOT / "RELEASE_NOTES.md").read_text(encoding="utf-8")})
    assets = {a["name"]: a for a in release.get("assets", [])}
    catalog = json.loads((ROOT / "catalog.json").read_bytes())
    filenames = [entry["archive"] for entry in catalog["skills"].values()] + ["catalog.json", "SHA256SUMS.txt"]
    for name in filenames:
        data = (ROOT / "dist" / name).read_bytes()
        if name in assets:
            if assets[name].get("digest") != "sha256:" + digest(data):
                raise ValueError(f"Existing asset cannot be verified: {name}; refusing overwrite")
            continue
        if not release.get("draft"):
            raise ValueError("Refusing to modify an already published release")
        api(f"{base}/{release['id']}/assets?" + urlencode({"name": name}), "POST", data, binary=True)
    if release.get("draft"):
        api(f"{base}/{release['id']}", "PATCH", {"draft": False})
    print(f"Published https://github.com/{REPOSITORY}/releases/tag/{tag}")


if __name__ == "__main__":
    main()
