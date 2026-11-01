"""Change requests: approved deploys of a version to environments within a time window.

The gate asks a ChangeSource which changes cover a deploy. GitChanges reads them from a
repo where each change is a file and approval is the PR review that added it. A Jira or
ServiceNow source would implement the same find().
"""

import urllib.error
from datetime import datetime
from typing import Protocol

import yaml

from .github import GitHub
from .policy import Change, current_approvers


class ChangeSource(Protocol):
    def find(self, repo: str, sha: str, env: str, now: datetime) -> list[Change]: ...


def as_time(v) -> datetime:
    return v if isinstance(v, datetime) else datetime.fromisoformat(str(v))


def covers(doc: dict, sha: str, env: str, now: datetime) -> bool:
    w = doc["window"]
    return doc["version"] == sha and env in doc["environments"] and as_time(w["start"]) <= now <= as_time(w["end"])


class GitChanges:
    def __init__(self, gh: GitHub, org: str, repo: str):
        self.gh, self.org, self.repo = gh, org, repo
        self.base = f"/repos/{org}/{repo}"

    def find(self, repo: str, sha: str, env: str, now: datetime) -> list[Change]:
        try:
            files = self.gh.get(f"{self.base}/contents/changes/{repo}?ref=main")
        except urllib.error.HTTPError as e:
            if e.code == 404:  # no changes for this repo yet
                return []
            raise
        found = []
        for f in files:
            if not f["name"].endswith(".yaml"):
                continue
            doc = yaml.safe_load(self.gh.request("GET", f["download_url"], raw=True))
            if covers(doc, sha, env, now):
                found.append(self.change(f["path"], doc))
        return found

    def change(self, path: str, doc: dict) -> Change:
        """Approvals come from the merged PR behind the file's latest commit on main."""
        commit = self.gh.get(f"{self.base}/commits?path={path}&sha=main&per_page=1")[0]
        pr = next((p for p in self.gh.get(f"{self.base}/commits/{commit['sha']}/pulls") if p["merged_at"]), None)
        if pr is None:
            # Pushed straight to main: there is no review to count.
            return Change(path, commit["html_url"], commit["author"]["login"] if commit["author"] else "", set(),
                          doc["emergency"])
        reviews = [{"user": r["user"]["login"], "state": r["state"], "commit_id": r["commit_id"]}
                   for r in self.gh.paginate(f"{self.base}/pulls/{pr['number']}/reviews")]
        return Change(path, pr["html_url"], pr["user"]["login"], current_approvers(reviews, pr["head"]["sha"]),
                      doc["emergency"])
