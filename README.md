# Jellyfin Plugin Repository

Single Jellyfin plugin catalog for the custom `netalondon-jellyfin-plugins` plugins.

## Jellyfin URL

Add this URL in Jellyfin under **Dashboard -> Plugins -> Repositories**:

```text
https://netalondon-jellyfin-plugins.github.io/plugin-repository/manifest.json
```

The GitLab catalog (`https://plugin-repository-ee1bc5.gitlab.io/manifest.json`) stays up until
every server has switched.

## How It Works

The plugin repositories are private, so Jellyfin can't download their release zips. This repository
is public, and its `collect` workflow copies each new zip into a release here, where anyone can
download it.

The workflow runs when a plugin's release workflow starts it, every 15 minutes, and on every push to
`main`. For each plugin in `plugins.json`, by `source`:

- `github`: reads `manifest.json` from the plugin repository's default branch. A version whose
  `sourceUrl` points at this repository's releases is listed once its zip is mirrored here: the
  workflow downloads the zip from the plugin's own release, checks its MD5 against the manifest, and
  publishes it as release `<slug>-<version>`. Versions whose `sourceUrl` points elsewhere (older
  zips on GitLab) are listed as they are.
- `gitlab`: copies the plugin's entry, by `guid`, from the public GitLab catalog. This keeps the
  catalog complete while plugins move to GitHub one by one.

The workflow deploys `manifest.json` to Pages only when it differs from the live one.

A version whose zip doesn't match its MD5, or fails to download, is left out and fails the run; the
rest of the catalog still deploys. A version whose plugin release isn't there yet is left out with a
warning and picked up by a later run.

## plugins.json

```json
{
  "name": "Shaderfin",
  "guid": "d5b73a6d-c07d-4520-bd53-90c623b8fe19",
  "source": "github",
  "repo": "shaderfin"
}
```

- `guid`: the plugin's Jellyfin guid, which picks its entry from the manifest.
- `source`: `github` or `gitlab`.
- `repo` (`github`): the repository in this organization, or `owner/name`.
- `project` (`gitlab`): where it lives on GitLab, for reference.

## Secrets

- `PLUGIN_READ_TOKEN`: fine-grained token with Contents: read on the plugin repositories. Needed
  once a plugin's `source` is `github`.

Releases here use the workflow's own `GITHUB_TOKEN`.

## Local Validation

```bash
python3 scripts/collect.py --output public/manifest.json
python3 -m json.tool public/manifest.json >/dev/null
python3 -m unittest discover -s tests
```

With GitHub plugins, `PLUGIN_READ_TOKEN` must be set, and `--dry-run` reports the zips it would
mirror instead of mirroring them.
