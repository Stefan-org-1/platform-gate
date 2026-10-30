# platform-gate

Posts a `policy` check on every open PR in Stefan-org-1, signed as the platform GitHub App. Repo rulesets require `policy` from that App, so no other workflow can satisfy it.

Runs every 10 minutes and on demand.

## Rules

- The repo is active in the `org-config` manifest.
- Changes under `.github/workflows/` are approved by a sentinel other than the author, on the latest commit.

## Run locally

```
GITHUB_TOKEN=$(gh auth token) python -m gate --org Stefan-org-1 --dry-run
```

`--manifest-dir path/to/org-config/repos` reads the manifest from disk.
