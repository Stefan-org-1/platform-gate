# platform-gate

Answers for the platform GitHub App, so no other workflow can produce its results:

- **Merge:** a `policy` check on every open PR. Repo rulesets require it from the App.
- **Deploy:** approve or reject deploys waiting on an environment where the App is a custom protection rule.

Runs every 10 minutes and on demand.

## Rules

Rule sets live in `org-config/rulesets/`. Every repo gets the sets in `org-config/baseline.yaml` and adds its own in its manifest entry. All selected sets must pass. Sets marked `break_glass: true` can be overridden by an open issue labelled `break-glass` that names the commit SHA, approved with `/approve-break-glass` by the people `baseline.yaml` lists, within its time limit.

Deploy approvals come from environment reviewers and from `/approve-deploy` comments on the PR that merged the commit, posted after the deploy started.

Rule types are in `gate/policy.py`.

## Run locally

```
GITHUB_TOKEN=$(gh auth token) python -m gate --org Stefan-org-1 --dry-run --config-dir ../org-config
```

`--config-dir path/to/org-config` reads the rule sets and manifest from disk.
