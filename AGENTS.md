# Jellyfin Plugin Repository Agent Notes

## Purpose

This repository publishes a single Jellyfin plugin catalog for the custom
plugins in the `netalondon-jellyfin-plugins` GitHub organization, and hosts
their release zips as its own releases.

## Commands

- Build local catalog:
  - `python3 scripts/collect.py --output public/manifest.json`
- Validate generated catalog:
  - `python3 -m json.tool public/manifest.json >/dev/null`
- Test the collector:
  - `python3 -m unittest discover -s tests`
- Create or replace the GitHub tokens:
  - `scripts/setup-tokens.sh` is interactive and handles secrets: ask the
    user to run it.

## Publishing

- The `collect` workflow mirrors new plugin zips into releases here and
  deploys `public/manifest.json` to GitHub Pages when it changed.
- Plugin release workflows start `collect` after they update their own
  `manifest.json`; a 15-minute schedule catches the rest.

## Design

- Do not use submodules for the plugin repositories unless the user explicitly
  asks for pinned catalog snapshots.
- This repository publishes the latest committed plugin manifests from each
  plugin repo's default branch, or, for plugins still on GitLab, their entries
  in the GitLab catalog.
- Plugin package URLs in the catalog must be reachable by Jellyfin without
  interactive authentication, so a version is listed only once its zip is
  mirrored here.
- This repository was copied from GitLab and has diverged; never force-push a
  fresh copy from GitLab over it.
