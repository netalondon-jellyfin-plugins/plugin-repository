# Jellyfin Plugin Repository Agent Notes

## Purpose

This repository publishes a single Jellyfin plugin catalog for the custom
plugins in the `jellyfin-plugins` GitLab.com group.

## Commands

- Build local catalog:
  - `python3 scripts/build-manifest.py --plugins plugins.json --output public/manifest.json`
- Validate generated catalog:
  - `python3 -m json.tool public/manifest.json >/dev/null`

## Publishing

- GitLab Pages publishes `public/manifest.json`.
- The Jellyfin repository URL should point at the Pages URL for that file.
- Plugin release pipelines should trigger this repository's pipeline after they
  update their own `manifest.json`.

## Design

- Do not use submodules for the plugin repositories unless the user explicitly
  asks for pinned catalog snapshots.
- This repository should publish the latest committed plugin manifests from
  each plugin repo's default branch.
- Plugin package URLs in the source manifests must be reachable by Jellyfin
  without interactive authentication.
