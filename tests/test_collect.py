"""Tests for scripts/collect.py's GitHub mirroring, against a fake GitHub API."""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import collect  # noqa: E402

HERE = "netalondon-jellyfin-plugins/plugin-repository"
PLUGIN_REPO = "netalondon-jellyfin-plugins/shaderfin"
PREFIX = f"https://github.com/{HERE}/releases/download/"
GUID = "d5b73a6d-c07d-4520-bd53-90c623b8fe19"
PLUGIN = {"name": "Shaderfin", "guid": GUID, "source": "github", "repo": "shaderfin"}

NEW_ZIP = b"zip 0.2.0.0"
OLD_URL = "https://gitlab.com/api/v4/projects/82680093/packages/generic/shaderfin/0.1.0.0/shaderfin_0.1.0.0.zip"


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest().upper()


def version(number: str, url: str, checksum: str) -> dict[str, Any]:
    return {
        "version": number,
        "changelog": f"{number}: notes",
        "targetAbi": "10.11.0.0",
        "sourceUrl": url,
        "checksum": checksum,
        "timestamp": "2026-10-23T00:00:00Z",
    }


def manifest(*versions: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "guid": GUID,
            "name": "Shaderfin",
            "description": "d",
            "overview": "o",
            "owner": "netalondon",
            "category": "General",
            "versions": list(versions),
        }
    ]


class FakeGitHub:
    """Repositories with a manifest.json and releases, recording every write."""

    def __init__(self) -> None:
        self.files: dict[tuple[str, str], bytes] = {}
        self.releases: dict[str, list[dict[str, Any]]] = {HERE: [], PLUGIN_REPO: []}
        self.blobs: dict[int, bytes] = {}
        self.writes: list[str] = []
        self.next_id = 1

    def _id(self) -> int:
        self.next_id += 1
        return self.next_id

    def add_release(self, repository: str, tag: str, assets: dict[str, bytes], body: str = "") -> dict[str, Any]:
        release = {"id": self._id(), "tag_name": tag, "body": body, "draft": False, "assets": []}
        for name, data in assets.items():
            asset = {"id": self._id(), "name": name, "state": "uploaded"}
            self.blobs[asset["id"]] = data
            release["assets"].append(asset)
        self.releases[repository].append(release)
        return release

    def file(self, repository: str, path: str) -> bytes:
        return self.files[(repository, path)]

    def paginate(self, url: str) -> list[Any]:
        repository = url.split("/repos/")[1].split("/releases")[0]
        return json.loads(json.dumps(self.releases[repository]))

    def download_asset(self, repository: str, asset_id: int) -> bytes:
        return self.blobs[asset_id]

    def _release(self, release_id: int) -> dict[str, Any]:
        return next(r for r in self.releases[HERE] if r["id"] == release_id)

    def json(self, method: str, url: str, body: Any = None) -> Any:
        self.writes.append(f"{method} {url}")
        if method == "POST":
            release = {"id": self._id(), "tag_name": body["tag_name"], "body": body["body"], "assets": []}
            self.releases[HERE].append(release)
            return json.loads(json.dumps(release))
        if method == "PATCH":
            release = self._release(int(url.rsplit("/", 1)[1]))
            release.update(body)
            return json.loads(json.dumps(release))
        raise AssertionError(url)

    def request(self, method: str, url: str, body: Any = None, **_: Any) -> tuple[bytes, Any]:
        self.writes.append(f"{method} {url}")
        asset_id = int(url.rsplit("/", 1)[1])
        for release in self.releases[HERE]:
            release["assets"] = [a for a in release["assets"] if a["id"] != asset_id]
        return b"", {}

    def upload_asset(self, repository: str, release_id: int, name: str, data: bytes) -> Any:
        self.writes.append(f"UPLOAD {name}")
        asset = {"id": self._id(), "name": name, "state": "uploaded"}
        self.blobs[asset["id"]] = data
        self._release(release_id)["assets"].append(asset)
        return asset


class CollectGitHubPluginTest(unittest.TestCase):
    def setUp(self) -> None:
        self.api = FakeGitHub()
        self.new = version("0.2.0.0", f"{PREFIX}shaderfin-0.2.0.0/shaderfin_0.2.0.0.zip", md5(NEW_ZIP))
        self.old = version("0.1.0.0", OLD_URL, "0" * 32)
        self.set_manifest(self.new, self.old)

    def set_manifest(self, *versions: dict[str, Any]) -> None:
        self.api.files[(PLUGIN_REPO, "manifest.json")] = json.dumps(manifest(*versions)).encode()

    def collect(self) -> tuple[list[str], list[str]]:
        mirror = collect.Mirror(self.api, HERE)  # type: ignore[arg-type]
        entry, errors = collect.collect_github_plugin(PLUGIN, "netalondon-jellyfin-plugins", self.api, mirror)  # type: ignore[arg-type]
        return [v["version"] for v in entry["versions"]], errors

    def mirrored(self) -> dict[str, bytes]:
        return {
            release["tag_name"]: self.api.blobs[release["assets"][0]["id"]]
            for release in self.api.releases[HERE]
            if release["assets"]
        }

    def test_mirrors_a_new_release_and_keeps_old_gitlab_versions(self) -> None:
        self.api.add_release(PLUGIN_REPO, "v0.2", {"shaderfin_0.2.0.0.zip": NEW_ZIP})

        self.assertEqual(self.collect(), (["0.2.0.0", "0.1.0.0"], []))
        self.assertEqual(self.mirrored(), {"shaderfin-0.2.0.0": NEW_ZIP})
        self.assertIn(f"MD5: {md5(NEW_ZIP)}", self.api.releases[HERE][0]["body"])

    def test_second_run_writes_nothing(self) -> None:
        self.api.add_release(PLUGIN_REPO, "v0.2.0.0", {"shaderfin_0.2.0.0.zip": NEW_ZIP})
        self.collect()
        writes = len(self.api.writes)

        self.assertEqual(self.collect(), (["0.2.0.0", "0.1.0.0"], []))
        self.assertEqual(len(self.api.writes), writes)

    def test_leaves_out_a_version_whose_release_is_not_there_yet(self) -> None:
        self.assertEqual(self.collect(), (["0.1.0.0"], []))
        self.assertEqual(self.api.writes, [])

    def test_refuses_a_zip_whose_md5_differs(self) -> None:
        self.api.add_release(PLUGIN_REPO, "v0.2.0.0", {"shaderfin_0.2.0.0.zip": b"something else"})

        versions, errors = self.collect()
        self.assertEqual(versions, ["0.1.0.0"])
        self.assertEqual(len(errors), 1)
        self.assertIn("MD5", errors[0])
        self.assertEqual(self.api.writes, [])

    def test_replaces_the_zip_of_a_re_released_version(self) -> None:
        self.api.add_release(PLUGIN_REPO, "v0.2.0.0", {"shaderfin_0.2.0.0.zip": b"first build"})
        self.set_manifest(version("0.2.0.0", self.new["sourceUrl"], md5(b"first build")))
        self.collect()

        self.api.releases[PLUGIN_REPO].clear()
        self.api.add_release(PLUGIN_REPO, "v0.2.0.0", {"shaderfin_0.2.0.0.zip": NEW_ZIP})
        self.set_manifest(self.new)

        self.assertEqual(self.collect(), (["0.2.0.0"], []))
        self.assertEqual(self.mirrored(), {"shaderfin-0.2.0.0": NEW_ZIP})
        self.assertEqual(len(self.api.releases[HERE]), 1)

    def test_finishes_a_mirror_that_stopped_before_recording_its_md5(self) -> None:
        self.api.add_release(HERE, "shaderfin-0.2.0.0", {}, body="Mirrored from somewhere.")
        self.api.add_release(PLUGIN_REPO, "v0.2.0.0", {"shaderfin_0.2.0.0.zip": NEW_ZIP})

        self.assertEqual(self.collect(), (["0.2.0.0", "0.1.0.0"], []))
        self.assertEqual(self.mirrored(), {"shaderfin-0.2.0.0": NEW_ZIP})


class BuildCatalogTest(unittest.TestCase):
    def test_takes_gitlab_plugins_from_the_gitlab_catalog_by_guid(self) -> None:
        gitlab = manifest(version("0.1.0.0", OLD_URL, "0" * 32))
        plugins = [{"name": "Old label", "guid": GUID, "source": "gitlab"}]

        catalog, errors = collect.build_catalog(plugins, lambda: gitlab, lambda _: self.fail("no GitHub plugin"))
        self.assertEqual((catalog, errors), (gitlab, []))

    def test_fails_when_a_gitlab_plugin_is_missing_from_the_gitlab_catalog(self) -> None:
        plugins = [{"name": "Gone", "guid": "00000000-0000-0000-0000-000000000000", "source": "gitlab"}]
        with self.assertRaises(ValueError):
            collect.build_catalog(plugins, lambda: manifest(), lambda _: self.fail("no GitHub plugin"))


if __name__ == "__main__":
    unittest.main()
