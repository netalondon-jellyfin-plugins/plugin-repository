#!/usr/bin/env python3
"""Build a combined Jellyfin plugin repository manifest."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


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


def load_json_from_url(url: str) -> Any:
    headers = {"User-Agent": "netalondon-jellyfin-plugin-repository"}
    token = os.environ.get("CATALOG_FETCH_TOKEN")
    if token:
        headers["PRIVATE-TOKEN"] = token

    request = Request(url, headers=headers)
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def load_plugin_manifest(entry: dict[str, str], config_dir: Path) -> Any:
    url = entry["manifest_url"]
    try:
        return load_json_from_url(url)
    except (HTTPError, URLError, TimeoutError) as exc:
        local_manifest = entry.get("local_manifest")
        if not local_manifest:
            raise RuntimeError(f"failed to fetch {url}: {exc}") from exc

        local_path = (config_dir / local_manifest).resolve()
        if not local_path.exists():
            raise RuntimeError(
                f"failed to fetch {url} and local fallback is missing: {local_path}"
            ) from exc

        print(f"warning: using local fallback for {entry['name']}: {local_path}")
        with local_path.open(encoding="utf-8") as handle:
            return json.load(handle)


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


def build_manifest(config_path: Path) -> list[dict[str, Any]]:
    with config_path.open(encoding="utf-8") as handle:
        plugins = json.load(handle)

    if not isinstance(plugins, list):
        raise ValueError("plugins config must be a JSON array")

    combined: list[dict[str, Any]] = []
    seen_guids: set[str] = set()
    config_dir = config_path.parent

    for plugin in plugins:
        if not isinstance(plugin, dict):
            raise ValueError("plugin config entries must be objects")
        for key in ("name", "manifest_url"):
            if key not in plugin:
                raise ValueError(f"plugin config entry missing {key}")

        manifest = load_plugin_manifest(plugin, config_dir)
        entries = normalize_manifest(manifest, plugin["name"])
        for entry in entries:
            guid = entry["guid"]
            if guid in seen_guids:
                raise ValueError(f"duplicate plugin guid in catalog: {guid}")
            seen_guids.add(guid)
            combined.append(entry)

    return sorted(combined, key=lambda item: item["name"].lower())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugins", type=Path, default=Path("plugins.json"))
    parser.add_argument("--output", type=Path, default=Path("public/manifest.json"))
    args = parser.parse_args()

    manifest = build_manifest(args.plugins.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
        handle.write("\n")

    print(f"wrote {len(manifest)} plugin entries to {args.output}")


if __name__ == "__main__":
    main()
