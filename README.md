# platform-gate

Answers for the platform GitHub App, so no other workflow can produce its results:

- **Merge:** a `policy` check on every open PR. Repo rulesets require it from the App.
- **Deploy:** approve or reject deploys waiting on an environment where the App is a custom protection rule.

Runs every 10 minutes and on demand.

## Rules

Rule sets live in `org-config/rulesets/`. Every repo gets the sets in `org-config/baseline.yaml` and adds its own in its manifest entry. All selected sets must pass.

A deploy needs a change in `change-requests` that covers its version, environment and time. The `change_approved` rule in the repo's rule sets says whose approval counts. The requester and the deployer never count.

Sets marked `break_glass: true` can be overridden by an emergency change with the approvals `baseline.yaml` lists.

Rule types are in `gate/policy.py`. Change requests are read in `gate/changes.py`.

## Results

| Gate | Short result | Full report |
|---|---|---|
| Merge | `policy` check on the PR | Same check |
| Deploy | Comment in the deploy run's summary | `deploy-policy (<env>)` check on the commit |

Results list what was not met and how the rest was met. Each line names its rule set, links to its definition, and says why.

## Run locally

```
GITHUB_TOKEN=$(gh auth token) python -m gate --org Stefan-org-1 --dry-run
GITHUB_TOKEN=$(gh auth token) python -m gate --org Stefan-org-1 --dry-run --app-slug stefan-org-1-deploy-gate \
  --run payments-web/<run id> --env prod
```

- `--config-dir path/to/org-config` reads the rule sets and manifest from disk.
- `--run` evaluates one deploy run, waiting or not.
