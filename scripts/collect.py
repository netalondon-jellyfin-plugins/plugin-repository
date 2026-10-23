#!/usr/bin/env python3
"""Collect plugin release zips and build the combined Jellyfin plugin catalog.

GitHub plugins: reads manifest.json from the plugin repository, mirrors each version's zip from the
plugin's own (private) release into a release of this repository, and lists a version only once its
zip is mirrored here. GitLab plugins: copies the plugin's entry from the public GitLab catalog.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen


GITLAB_CATALOG_URL = "https://plugin-repository-ee1bc5.gitlab.io/manifest.json"
GITHUB_API = "https://api.github.com"
GITHUB_UPLOADS = "https://uploads.github.com"
USER_AGENT = "netalondon-jellyfin-plugin-repository"

REQUIRED_PLUGIN_KEYS = {
    "guid",
    "name",
    "description",
    "overview",
    "owner",
    "category",
    "versions",
}

REQUIRED_VERSION_KEYS = {
    "version",
    "changelog",
    "targetAbi",
    "sourceUrl",
    "checksum",
    "timestamp",
}

SOURCES = {"gitlab", "github"}

# A mirrored release records its zip's MD5 last, so a half-finished mirror never counts as done.
MD5_LINE = re.compile(r"^MD5: ([0-9A-Fa-f]{32})$", re.MULTILINE)


def in_actions() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def warn(message: str) -> None:
    print(f"::warning::{message}" if in_actions() else f"warning: {message}")


def error(message: str) -> None:
    print(f"::error::{message}" if in_actions() else f"error: {message}", file=sys.stderr)


def set_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def fetch(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=60) as response:
        return response.read()


class GitHubError(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class GitHub:
    """Just enough of the GitHub REST API, authenticated with one token."""

    def __init__(self, token: str | None) -> None:
        self.token = token

    def _headers(self, accept: str) -> dict[str, str]:
        headers = {
            "Accept": accept,
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def request(
        self,
        method: str,
        url: str,
        body: Any = None,
        *,
        accept: str = "application/vnd.github+json",
        content_type: str = "application/json",
    ) -> tuple[bytes, Any]:
        if url.startswith("/"):
            url = GITHUB_API + url
        headers = self._headers(accept)
        data = None
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            headers["Content-Type"] = content_type
        request = Request(url, data=data, method=method, headers=headers)
        try:
            with urlopen(request, timeout=300) as response:
                return response.read(), response.headers
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise GitHubError(f"{method} {url}: HTTP {exc.code} {detail}") from exc

    def json(self, method: str, url: str, body: Any = None) -> Any:
        content, _ = self.request(method, url, body)
        return json.loads(content) if content else None

    def paginate(self, url: str) -> list[Any]:
        items: list[Any] = []
        next_url: str | None = url
        while next_url:
            content, headers = self.request("GET", next_url)
            items.extend(json.loads(content))
            next_url = next_link(headers.get("Link"))
        return items

    def file(self, repository: str, path: str) -> bytes:
        """A file from the repository's default branch."""
        content, _ = self.request(
            "GET",
            f"/repos/{repository}/contents/{path}",
            accept="application/vnd.github.raw+json",
        )
        return content

    def download_asset(self, repository: str, asset_id: int) -> bytes:
        # The API answers with a redirect to a signed URL, which must not receive the token.
        url = f"{GITHUB_API}/repos/{repository}/releases/assets/{asset_id}"
        request = Request(url, headers=self._headers("application/octet-stream"))
        try:
            with build_opener(_NoRedirect).open(request, timeout=300) as response:
                return response.read()
        except HTTPError as exc:
            if exc.code not in (301, 302, 303, 307, 308):
                detail = exc.read().decode("utf-8", "replace")[:300]
                raise GitHubError(f"GET {url}: HTTP {exc.code} {detail}") from exc
            location = exc.headers["Location"]
        with urlopen(Request(location, headers={"User-Agent": USER_AGENT}), timeout=300) as response:
            return response.read()

    def upload_asset(self, repository: str, release_id: int, name: str, data: bytes) -> Any:
        url = f"{GITHUB_UPLOADS}/repos/{repository}/releases/{release_id}/assets?name={quote(name)}"
        content, _ = self.request("POST", url, data, content_type="application/zip")
        return json.loads(content)


def next_link(header: str | None) -> str | None:
    for part in (header or "").split(","):
        target, _, params = part.partition(";")
        if 'rel="next"' in params:
            return target.strip().strip("<>")
    return None


def validate_manifest_entry(entry: dict[str, Any], source_name: str) -> None:
    missing = sorted(REQUIRED_PLUGIN_KEYS - entry.keys())
    if missing:
        raise ValueError(f"{source_name} missing plugin keys: {', '.join(missing)}")

    if not isinstance(entry["versions"], list):
        raise ValueError(f"{source_name} versions must be an array")

    seen_versions: set[str] = set()
    for version in entry["versions"]:
        missing_version = sorted(REQUIRED_VERSION_KEYS - version.keys())
        if missing_version:
            raise ValueError(
                f"{source_name} {version.get('version', '<unknown>')} "
                f"missing version keys: {', '.join(missing_version)}"
            )

        version_id = version["version"]
        if version_id in seen_versions:
            raise ValueError(f"{source_name} has duplicate version {version_id}")
        seen_versions.add(version_id)


def normalize_manifest(raw_manifest: Any, source_name: str) -> list[dict[str, Any]]:
    if not isinstance(raw_manifest, list):
        raise ValueError(f"{source_name} manifest must be a JSON array")

    normalized: list[dict[str, Any]] = []
    for item in raw_manifest:
        if not isinstance(item, dict):
            raise ValueError(f"{source_name} manifest entries must be objects")
        validate_manifest_entry(item, source_name)
        normalized.append(item)

    return normalized


def find_entry(entries: list[dict[str, Any]], plugin: dict[str, Any], source_name: str) -> dict[str, Any]:
    for entry in entries:
        if entry["guid"] == plugin["guid"]:
            return entry
    raise ValueError(f"{source_name} has no entry with guid {plugin['guid']} ({plugin['name']})")


def load_plugins(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        plugins = json.load(handle)

    if not isinstance(plugins, list):
        raise ValueError("plugins config must be a JSON array")

    for plugin in plugins:
        if not isinstance(plugin, dict):
            raise ValueError("plugin config entries must be objects")
        for key in ("name", "guid", "source"):
            if key not in plugin:
                raise ValueError(f"plugin config entry missing {key}: {plugin}")
        if plugin["source"] not in SOURCES:
            raise ValueError(f"{plugin['name']}: source must be one of {', '.join(sorted(SOURCES))}")
        if plugin["source"] == "github" and "repo" not in plugin:
            raise ValueError(f"{plugin['name']}: a github plugin needs repo")

    return plugins


class Mirror:
    """This repository's releases: one per mirrored plugin zip, tagged <slug>-<version>."""

    def __init__(self, api: GitHub, repository: str, *, dry_run: bool = False) -> None:
        self.api = api
        self.repository = repository
        self.dry_run = dry_run
        self.download_prefix = f"https://github.com/{repository}/releases/download/"
        self.releases = {
            release["tag_name"]: release
            for release in api.paginate(f"/repos/{repository}/releases?per_page=100")
        }

    def has(self, tag: str, filename: str, checksum: str) -> bool:
        release = self.releases.get(tag)
        if release is None:
            return False
        recorded = MD5_LINE.search(release.get("body") or "")
        if recorded is None or recorded.group(1).upper() != checksum.upper():
            return False
        return any(
            asset["name"] == filename and asset.get("state", "uploaded") == "uploaded"
            for asset in release["assets"]
        )

    def publish(self, tag: str, title: str, filename: str, data: bytes, md5: str, origin: str) -> None:
        if self.dry_run:
            print(f"dry run: would mirror {filename} from {origin} as release {tag}")
            return

        body = f"Mirrored from {origin}."
        release = self.releases.get(tag)
        if release is None:
            release = self.api.json(
                "POST",
                f"/repos/{self.repository}/releases",
                {"tag_name": tag, "name": title, "body": body, "make_latest": "false"},
            )
        else:
            # The plugin re-released this version with a different zip.
            for asset in release["assets"]:
                if asset["name"] == filename:
                    self.api.request("DELETE", f"/repos/{self.repository}/releases/assets/{asset['id']}")
        self.api.upload_asset(self.repository, release["id"], filename, data)
        self.releases[tag] = self.api.json(
            "PATCH",
            f"/repos/{self.repository}/releases/{release['id']}",
            {"body": f"{body}\n\nMD5: {md5}\n"},
        )
        print(f"mirrored {filename} from {origin} as release {tag}")


def collect_github_plugin(
    plugin: dict[str, Any], owner: str, api: GitHub, mirror: Mirror
) -> tuple[dict[str, Any], list[str]]:
    """The plugin's catalog entry, keeping only versions whose zip is reachable, and any errors."""
    repository = plugin["repo"] if "/" in plugin["repo"] else f"{owner}/{plugin['repo']}"
    source_name = f"{plugin['name']} ({repository})"
    entries = normalize_manifest(json.loads(api.file(repository, "manifest.json")), source_name)
    entry = find_entry(entries, plugin, source_name)

    errors: list[str] = []
    assets: dict[str, tuple[dict[str, Any], dict[str, Any]]] | None = None
    versions: list[dict[str, Any]] = []
    for version in entry["versions"]:
        url = version["sourceUrl"]
        if not url.startswith(mirror.download_prefix):
            # An older zip hosted elsewhere, such as on GitLab.
            versions.append(version)
            continue

        label = f"{plugin['name']} {version['version']}"
        tag, _, filename = url[len(mirror.download_prefix) :].partition("/")
        checksum = version["checksum"].upper()
        if mirror.has(tag, filename, checksum):
            versions.append(version)
            continue

        try:
            if assets is None:
                assets = {
                    asset["name"]: (release, asset)
                    for release in api.paginate(f"/repos/{repository}/releases?per_page=100")
                    if not release.get("draft")
                    for asset in release["assets"]
                }
            if filename not in assets:
                warn(f"{label}: {repository} has no release with {filename} yet; leaving it out")
                continue

            release, asset = assets[filename]
            origin = f"{repository} release {release['tag_name']}"
            data = api.download_asset(repository, asset["id"])
            md5 = hashlib.md5(data).hexdigest().upper()
            if md5 != checksum:
                errors.append(
                    f"{label}: {filename} from {origin} has MD5 {md5}, but the manifest says "
                    f"{checksum}; not publishing it"
                )
                continue

            mirror.publish(tag, label, filename, data, md5, origin)
            versions.append(version)
        except (GitHubError, URLError, TimeoutError) as exc:
            errors.append(f"{label}: mirroring {filename} failed: {exc}")

    return {**entry, "versions": versions}, errors


def build_catalog(
    plugins: list[dict[str, Any]],
    gitlab_catalog: Callable[[], list[dict[str, Any]]],
    collect_github: Callable[[dict[str, Any]], tuple[dict[str, Any], list[str]]],
) -> tuple[list[dict[str, Any]], list[str]]:
    combined: list[dict[str, Any]] = []
    errors: list[str] = []
    seen_guids: set[str] = set()
    gitlab_entries: list[dict[str, Any]] | None = None

    for plugin in plugins:
        if plugin["guid"] in seen_guids:
            raise ValueError(f"duplicate plugin guid in catalog: {plugin['guid']}")
        seen_guids.add(plugin["guid"])

        if plugin["source"] == "gitlab":
            if gitlab_entries is None:
                gitlab_entries = gitlab_catalog()
            entry = find_entry(gitlab_entries, plugin, "GitLab catalog")
        else:
            entry, plugin_errors = collect_github(plugin)
            errors.extend(plugin_errors)

        if not entry["versions"]:
            warn(f"{plugin['name']} has no reachable version yet; leaving it out")
            continue
        combined.append(entry)

    return sorted(combined, key=lambda item: item["name"].lower()), errors


def live_catalog_differs(text: str, url: str) -> bool:
    # The query string gets past Pages' CDN cache.
    try:
        live = fetch(f"{url}?run={int(time.time())}")
    except (HTTPError, URLError, TimeoutError) as exc:
        print(f"live catalog unavailable ({exc}); deploying")
        return True
    return live != text.encode("utf-8")


def run(args: argparse.Namespace) -> int:
    plugins = load_plugins(args.plugins)
    owner = args.repository.split("/")[0]
    plugin_api = GitHub(os.environ.get("PLUGIN_READ_TOKEN"))
    mirror: Mirror | None = None

    def gitlab_catalog() -> list[dict[str, Any]]:
        return normalize_manifest(json.loads(fetch(args.gitlab_catalog)), "GitLab catalog")

    def collect_github(plugin: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        nonlocal mirror
        if not plugin_api.token:
            raise RuntimeError("PLUGIN_READ_TOKEN is required to read GitHub plugins")
        if mirror is None:
            mirror = Mirror(GitHub(os.environ.get("GITHUB_TOKEN")), args.repository, dry_run=args.dry_run)
        return collect_github_plugin(plugin, owner, plugin_api, mirror)

    manifest, errors = build_catalog(plugins, gitlab_catalog, collect_github)
    text = json.dumps(manifest, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    print(f"wrote {len(manifest)} plugin entries to {args.output}")

    if args.live_catalog:
        changed = live_catalog_differs(text, args.live_catalog)
        print("catalog changed" if changed else "catalog unchanged")
        set_output("changed", "true" if changed else "false")

    # Failed versions are left out above, so the rest of the catalog still deploys.
    for message in errors:
        error(message)
    return 1 if errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugins", type=Path, default=Path("plugins.json"))
    parser.add_argument("--output", type=Path, default=Path("public/manifest.json"))
    parser.add_argument("--gitlab-catalog", default=GITLAB_CATALOG_URL)
    parser.add_argument(
        "--repository",
        default=os.environ.get("GITHUB_REPOSITORY", "netalondon-jellyfin-plugins/plugin-repository"),
        help="this repository, which hosts the mirrored zips",
    )
    parser.add_argument(
        "--live-catalog",
        help="published manifest.json URL; sets the changed output when the new catalog differs",
    )
    parser.add_argument("--dry-run", action="store_true", help="report zips to mirror instead of mirroring them")
    args = parser.parse_args()

    try:
        return run(args)
    except (ValueError, RuntimeError, URLError, TimeoutError) as exc:
        # Nothing is written, so the live catalog stays as it is.
        error(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
