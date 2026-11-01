"""Evaluate open PRs and waiting deploys across the org, and answer as the platform App.

PRs get a `policy` check on their head commit. Deploys waiting on an environment get an
approve or reject through the custom deployment protection rule.
"""

import argparse
import base64
import json
import os
import pathlib
import subprocess
import tempfile
import urllib.error
from datetime import datetime, timezone
from functools import cache

import yaml

from .changes import ChangeSource, GitChanges
from .github import GitHub
from .policy import Facts, RuleSet, attestation_key, current_approvers, evaluate

CHECK_NAME = "policy"


def gate_run_url() -> str | None:
    """Link to this gate run, when running in Actions."""
    if "GITHUB_RUN_ID" not in os.environ:
        return None
    return f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"


def report(org: str, reasons: list[str], sets: list[RuleSet], links: dict[str, str]) -> str:
    """Markdown body: reasons, links, and the rule sets that were checked."""
    lines = [f"- {r}" for r in reasons] or ["All rule sets pass."]
    present = [f"{name}: {url}" for name, url in links.items() if url]
    lines += ["", *present] if present else []
    lines.append("")
    lines.append("Rule sets checked: " + ", ".join(
        f"[{rs.name}](https://github.com/{org}/org-config/blob/main/rulesets/{rs.name}.yaml)" for rs in sets))
    return "\n".join(lines)


class Config:
    def __init__(self, files: dict[str, str]):
        """files: path relative to the org-config root -> text."""
        load = lambda p: yaml.safe_load(files[p])
        self.baseline = load("baseline.yaml")
        self.change_management = load("change-management.yaml")
        self.rulesets = {
            pathlib.PurePath(p).stem: RuleSet(pathlib.PurePath(p).stem, d["rules"], d.get("break_glass", False))
            for p, d in ((p, load(p)) for p in files if p.startswith("rulesets/"))
        }
        self.repos = {}
        for p in (p for p in files if p.startswith("repos/")):
            team = load(p)
            for r in team["repos"]:
                self.repos[r["name"]] = {**r, "team": team["team"]}
        for name in self._all_set_names():
            if name not in self.rulesets:
                raise ValueError(f"Unknown rule set {name}")

    def _all_set_names(self):
        yield from self.baseline["merge"] + self.baseline["deploy"]
        for r in self.repos.values():
            sets = r.get("rulesets", {})
            yield from sets.get("merge", [])
            for names in sets.get("deploy", {}).values():
                yield from names

    def merge_sets(self, repo: str) -> list[RuleSet]:
        own = self.repos.get(repo, {}).get("rulesets", {}).get("merge", [])
        return [self.rulesets[n] for n in self.baseline["merge"] + own]

    def deploy_sets(self, repo: str, env: str) -> list[RuleSet]:
        own = self.repos.get(repo, {}).get("rulesets", {}).get("deploy", {}).get(env, [])
        return [self.rulesets[n] for n in self.baseline["deploy"] + own]

    @classmethod
    def from_dir(cls, root: str):
        root = pathlib.Path(root)
        return cls({str(p.relative_to(root)): p.read_text() for p in root.glob("**/*.yaml")})

    @classmethod
    def from_github(cls, gh: GitHub, org: str):
        files = {}
        for d in ("", "rulesets", "repos"):
            for f in gh.get(f"/repos/{org}/org-config/contents/{d}"):
                if f["type"] == "file" and f["name"].endswith(".yaml"):
                    files[f["path"]] = base64.b64decode(gh.get(f["url"])["content"]).decode()
        return cls(files)


def attested_params(rulesets: list[RuleSet]) -> list[dict]:
    return [r["attested"] for rs in rulesets for r in rs.rules if isinstance(r, dict) and "attested" in r]


def verify_attestation(org: str, repo: str, sha: str, p: dict) -> dict | None:
    """Predicate of an attestation over "<org>/<repo>@<sha>" signed by the given workflow, if one verifies."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt") as subject:
        subject.write(f"{org}/{repo}@{sha}")
        subject.flush()
        result = subprocess.run(
            ["gh", "attestation", "verify", subject.name, "--repo", f"{org}/{repo}",
             "--signer-workflow", p["signer_workflow"], "--predicate-type", p["predicate_type"], "--format", "json"],
            capture_output=True, text=True, env={**os.environ, "GH_TOKEN": os.environ["GITHUB_TOKEN"]},
        )
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)[0]["verificationResult"]["statement"]["predicate"]


class Scanner:
    def __init__(self, gh: GitHub, org: str, config: Config, changes: ChangeSource, app_slug: str | None,
                 dry_run: bool):
        self.gh, self.org, self.config, self.changes = gh, org, config, changes
        self.app_slug, self.dry_run = app_slug, dry_run
        self.now = datetime.now(timezone.utc)

    @cache
    def members(self, team: str) -> set[str]:
        try:
            return frozenset(m["login"] for m in self.gh.paginate(f"/orgs/{self.org}/teams/{team}/members"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return frozenset()
            raise

    def facts(self, repo: str, **kw) -> Facts:
        entry = self.config.repos.get(repo, {})
        return Facts(repo=repo, team=entry.get("team", ""), manifest_status=entry.get("status"), now=self.now, **kw)

    def attestations(self, repo: str, shas: list[str], rulesets: list[RuleSet]) -> dict[str, dict | None]:
        """For each attested rule, the first of the given commits with a verified attestation."""
        found = {}
        for p in attested_params(rulesets):
            found[attestation_key(p)] = next(
                (pred for sha in shas if (pred := verify_attestation(self.org, repo, sha, p)) is not None), None)
        return found

    def our_check(self, repo: str, sha: str) -> dict | None:
        runs = self.gh.get(f"/repos/{self.org}/{repo}/commits/{sha}/check-runs?check_name={CHECK_NAME}&filter=latest")
        # A check of the same name from another app does not count.
        return next((r for r in runs["check_runs"] if r["app"]["slug"] == self.app_slug), None)

    def scan_pr(self, repo: str, pr: dict):
        base = f"/repos/{self.org}/{repo}/pulls/{pr['number']}"
        sha = pr["head"]["sha"]
        reviews = [{"user": r["user"]["login"], "state": r["state"], "commit_id": r["commit_id"]}
                   for r in self.gh.paginate(f"{base}/reviews")]
        f = self.facts(repo, actor=pr["user"]["login"], sha=sha, branch=pr["head"]["ref"],
                       files=[x["filename"] for x in self.gh.paginate(f"{base}/files")],
                       approvers=current_approvers(reviews, sha))
        sets = self.config.merge_sets(repo)
        f.attestations = self.attestations(repo, [sha], sets)
        v = evaluate(sets, f, self.members)
        conclusion = "success" if v.passed else "failure"
        # No per-run links here: an unchanged summary is how a repeat post is skipped.
        summary = report(self.org, v.reasons, sets, {})
        print(f"merge  {repo}#{pr['number']} {sha[:7]} {conclusion}\n{summary}")
        if self.dry_run:
            return
        existing = self.our_check(repo, sha)
        if existing and existing["conclusion"] == conclusion and existing["output"]["summary"] == summary:
            return
        self.gh.post(f"/repos/{self.org}/{repo}/check-runs", {
            "name": CHECK_NAME, "head_sha": sha, "status": "completed", "conclusion": conclusion,
            "details_url": gate_run_url(),
            "output": {"title": f"Policy {conclusion}", "summary": summary},
        })

    def scan_deploy(self, repo: str, run: dict):
        base = f"/repos/{self.org}/{repo}/actions/runs/{run['id']}"
        sha = run["head_sha"]
        envs = [p["environment"]["name"] for p in self.gh.get(f"{base}/pending_deployments")]
        prs = self.gh.get(f"/repos/{self.org}/{repo}/commits/{sha}/pulls")
        other_runs = [r["conclusion"] or r["status"]
                      for r in self.gh.paginate(f"/repos/{self.org}/{repo}/actions/runs?head_sha={sha}", "workflow_runs")
                      if r["id"] != run["id"]]
        # Merging creates a new commit, so the merge check sits on the PR's head commit.
        check = None
        if self.app_slug:
            for candidate in [sha, *(pr["head"]["sha"] for pr in prs)]:
                check = self.our_check(repo, candidate)
                if check:
                    break

        f = self.facts(repo, actor=run["triggering_actor"]["login"], sha=sha, branch=run["head_branch"],
                       workflows=[w["path"] for w in run.get("referenced_workflows", [])],
                       checks={CHECK_NAME: check["conclusion"]} if check else {},
                       other_runs=other_runs)

        for env in envs:
            sets = self.config.deploy_sets(repo, env)
            f.env = env
            f.changes = self.changes.find(repo, sha, env, self.now)
            f.attestations = self.attestations(repo, [sha, *(pr["head"]["sha"] for pr in prs)], sets)
            v = evaluate(sets, f, self.members, self.config.baseline.get("break_glass"))
            state = "approved" if v.passed else "rejected"
            summary = report(self.org, v.reasons, sets, {"Deploy run": run["html_url"], "Gate run": gate_run_url()})
            print(f"deploy {repo} run {run['id']} {env} {state}\n{summary}")
            if self.dry_run:
                continue
            # The comment is capped at 1024 characters, so the full report with links goes on the commit.
            check = self.gh.post(f"/repos/{self.org}/{repo}/check-runs", {
                "name": f"deploy-policy ({env})", "head_sha": sha, "status": "completed",
                "conclusion": "success" if v.passed else "failure", "details_url": run["html_url"],
                "output": {"title": f"Deploy to {env} {state}", "summary": summary},
            })
            comment = "\n".join(f"- {r}" for r in v.reasons) or "All rule sets pass."
            comment = f"Details: {check['html_url']}\n{comment}"
            try:
                self.gh.post(f"{base}/deployment_protection_rule",
                             {"environment_name": env, "state": state, "comment": comment[:1024]})
            except urllib.error.HTTPError as e:
                # The App is not a protection rule on this environment, or already answered.
                print(f"  not answered: HTTP {e.code}")

    def run(self):
        for repo in self.gh.paginate(f"/orgs/{self.org}/repos?type=all"):
            if repo["archived"]:
                continue
            name = repo["name"]
            for pr in self.gh.paginate(f"/repos/{self.org}/{name}/pulls?state=open"):
                self.scan_pr(name, pr)
            for run in self.gh.paginate(f"/repos/{self.org}/{name}/actions/runs?status=waiting", "workflow_runs"):
                self.scan_deploy(name, run)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--org", required=True)
    parser.add_argument("--config-dir", help="Read org-config from a local checkout")
    parser.add_argument("--app-slug", help="Slug of the App posting results; required unless --dry-run")
    parser.add_argument("--dry-run", action="store_true", help="Print verdicts, post nothing")
    args = parser.parse_args()
    if not args.dry_run and not args.app_slug:
        parser.error("--app-slug is required unless --dry-run")

    gh = GitHub()
    config = Config.from_dir(args.config_dir) if args.config_dir else Config.from_github(gh, args.org)
    changes = GitChanges(gh, args.org, config.change_management["repo"])
    Scanner(gh, args.org, config, changes, args.app_slug, args.dry_run).run()


if __name__ == "__main__":
    main()
