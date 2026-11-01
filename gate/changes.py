"""Change requests: approved deploys of a version to environments within a time window.

The gate asks a ChangeSource which changes cover a deploy. A Jira or ServiceNow source would
implement the same find().
"""

import urllib.error
from datetime import datetime
from typing import Protocol

import yaml

from .github import GitHub
from .policy import Change


class ChangeSource(Protocol):
    def find(self, repo: str, sha: str, env: str, now: datetime) -> list[Change]: ...


def as_time(v) -> datetime:
    return v if isinstance(v, datetime) else datetime.fromisoformat(str(v))


def covers(doc: dict, sha: str, env: str, now: datetime) -> bool:
    w = doc["window"]
    return doc["version"] == sha and env in doc["environments"] and as_time(w["start"]) <= now <= as_time(w["end"])


class FileChanges:
    """Changes are files in a repo, each holding its own requester and approvers.

    The files are trusted as is. This stands in for a system that protects who approved.
    """

    def __init__(self, gh: GitHub, org: str, repo: str):
        self.gh, self.base = gh, f"/repos/{org}/{repo}"

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
                found.append(Change(f["path"], f["html_url"], doc["requested_by"], set(doc["approvers"]),
                                    doc["emergency"]))
        return found
