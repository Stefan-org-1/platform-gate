"""Evaluate open PRs across the org and post a `policy` check on each head commit."""

import argparse
import base64
import pathlib

import yaml

from .github import GitHub
from .policy import PullRequest, evaluate

CHECK_NAME = "policy"


def load_manifest(gh: GitHub, org: str, local_dir: str = None) -> dict[str, dict]:
    if local_dir:
        files = [p.read_text() for p in pathlib.Path(local_dir).glob("*.yaml")]
    else:
        files = [
            base64.b64decode(gh.get(f["url"])["content"]).decode()
            for f in gh.get(f"/repos/{org}/org-config/contents/repos")
            if f["name"].endswith(".yaml")
        ]
    manifest = {}
    for text in files:
        team = yaml.safe_load(text)
        for repo in team["repos"]:
            manifest[repo["name"]] = {**repo, "team": team["team"]}
    return manifest


def fetch_pr(gh: GitHub, org: str, repo: str, pr: dict) -> PullRequest:
    base = f"/repos/{org}/{repo}/pulls/{pr['number']}"
    return PullRequest(
        repo=repo,
        number=pr["number"],
        author=pr["user"]["login"],
        head_sha=pr["head"]["sha"],
        files=[f["filename"] for f in gh.paginate(f"{base}/files")],
        reviews=[
            {"user": r["user"]["login"], "state": r["state"], "commit_id": r["commit_id"]}
            for r in gh.paginate(f"{base}/reviews")
        ],
    )


def already_posted(gh: GitHub, app_slug: str, org: str, repo: str, sha: str, conclusion: str, summary: str) -> bool:
    runs = gh.get(f"/repos/{org}/{repo}/commits/{sha}/check-runs?check_name={CHECK_NAME}&filter=latest")["check_runs"]
    # A check of the same name from another app does not count.
    ours = [r for r in runs if r["app"]["slug"] == app_slug]
    return any(r["conclusion"] == conclusion and r["output"]["summary"] == summary for r in ours)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--org", required=True)
    parser.add_argument("--manifest-dir", help="Read the manifest from a local repos/ folder")
    parser.add_argument("--app-slug", help="Slug of the App posting checks; required unless --dry-run")
    parser.add_argument("--dry-run", action="store_true", help="Print verdicts, post nothing")
    args = parser.parse_args()
    if not args.dry_run and not args.app_slug:
        parser.error("--app-slug is required unless --dry-run")

    gh = GitHub()
    manifest = load_manifest(gh, args.org, args.manifest_dir)
    sentinels = {m["login"] for m in gh.paginate(f"/orgs/{args.org}/teams/group-sentinels/members")}

    for repo in gh.paginate(f"/orgs/{args.org}/repos?type=all"):
        if repo["archived"]:
            continue
        for raw in gh.paginate(f"/repos/{args.org}/{repo['name']}/pulls?state=open"):
            pr = fetch_pr(gh, args.org, repo["name"], raw)
            verdict = evaluate(pr, manifest, sentinels)
            conclusion = "success" if verdict.passed else "failure"
            summary = "All policy rules pass." if verdict.passed else "\n".join(f"- {r}" for r in verdict.reasons)
            print(f"{repo['name']}#{pr.number} {pr.head_sha[:7]} {conclusion}: {summary}")
            if args.dry_run or already_posted(gh, args.app_slug, args.org, repo["name"], pr.head_sha, conclusion, summary):
                continue
            gh.post(f"/repos/{args.org}/{repo['name']}/check-runs", {
                "name": CHECK_NAME,
                "head_sha": pr.head_sha,
                "status": "completed",
                "conclusion": conclusion,
                "output": {"title": f"Policy {conclusion}", "summary": summary},
            })


if __name__ == "__main__":
    main()
