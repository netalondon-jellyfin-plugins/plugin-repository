# Jellyfin Plugin Repository

Single Jellyfin plugin catalog for the custom `jellyfin-plugins` projects.

## Jellyfin URL

After GitLab Pages is enabled and the first pipeline succeeds, add this URL in
Jellyfin under **Dashboard -> Plugins -> Repositories**:

```text
https://jellyfin-plugins.gitlab.io/plugin-repository/manifest.json
```

If GitLab assigns a unique Pages domain, use the URL shown in
**Deploy -> Pages** and append `/manifest.json`.

## How It Works

This repository does not use submodules. The Pages pipeline downloads each
plugin repo's `manifest.json`, merges the entries, validates the result, and
publishes `public/manifest.json`.

Submodules would also work, but every plugin release would then need to update
the submodule pointer in this repository. Fetching the latest default-branch
manifests keeps the catalog moving automatically after each release pipeline
triggers a rebuild.

## Included Plugins

- CollectionImages: `jellyfin-plugins/collection-posters`
- TMDB Collection Integrity: `jellyfin-plugins/tmdb-collection-integrity`
- CollectionSync: `jellyfin-plugins/collection-sync`
- CollectionMemberships: `jellyfin-plugins/collection-memberships`
- LibraryCovers: `jellyfin-plugins/library-covers`
- PeopleAggregation: `jellyfin-plugins/people-aggregation`
- WebUiEnhancements: `jellyfin-plugins/web-ui-enhancements`
- WhisperSubs Companion: `jellyfin-plugins/whisper-subs-companion`

## Release Pipeline Expectations

Each plugin release pipeline should:

1. Build and package the plugin zip.
2. Upload the zip to the GitLab.com generic package registry.
3. Update that plugin repo's `manifest.json` on `main`.
4. Trigger this repository's pipeline so Pages republishes the aggregate
   catalog.

For Jellyfin to install updates unattended, both the aggregate manifest URL and
each plugin version's `sourceUrl` must be reachable from the Jellyfin container
without browser login.

If the plugin source repositories stay private, configure this repository's CI
with `CATALOG_FETCH_TOKEN` so it can read the private source manifests. The
published package URLs still need to be public; the cleanest private-source
setup is to upload release zips to this public repository's package registry
and write those public package URLs into the plugin manifests.

## CI Variables

Optional variables:

- `CATALOG_FETCH_TOKEN`: token with read access to the plugin repositories'
  `manifest.json` files. Required when the plugin repos are private.

## Plugin Release Variables

Set these in each plugin project so release jobs can refresh this catalog:

- `PUBLIC_PLUGIN_PACKAGE_PROJECT_ID`: this repository's project ID. Use
  `82680093` for `jellyfin-plugins/plugin-repository` on GitLab.com.
- `PUBLIC_PACKAGE_TOKEN`: token with permission to upload packages to this
  repository.
- `PLUGIN_CATALOG_PROJECT_ID`: this repository's project ID. Use `82680093`.
- `CATALOG_TRIGGER_TOKEN`: pipeline trigger token created in this repository.

## Local Validation

```bash
python3 scripts/build-manifest.py --plugins plugins.json --output public/manifest.json
python3 -m json.tool public/manifest.json >/dev/null
```
