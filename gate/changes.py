"""Change requests: approved deploys of a version to environments within a time window.

The gate asks a ChangeSource for a repo's changes, each with why it does not cover the deploy,
if it does not. A Jira or ServiceNow source would implement the same interface.
"""

import base64
import urllib.error
from datetime import datetime
from typing import Protocol

import yaml

from .github import GitHub
from .policy import Change


class ChangeSource(Protocol):
    def find(self, repo: str, sha: str, tags: list[str], env: str, now: datetime) -> list[Change]:
        """tags: release tags on sha. A change may name the version by either."""

    def location(self, repo: str) -> str:
        """Where an engineer finds and requests changes for the repo."""


def as_time(v) -> datetime:
    return v if isinstance(v, datetime) else datetime.fromisoformat(str(v))


def misses(doc: dict, sha: str, tags: list[str], env: str, now: datetime) -> list[str]:
    """Why the change does not cover deploying sha, tagged tags, to env now. Empty if it does."""
    start, end = as_time(doc["window"]["start"]), as_time(doc["window"]["end"])
    out = []
    version = str(doc["version"])
    if version != sha and version not in tags:
        shown = lambda v: v[:7] if len(v) == 40 else v
        out.append(f"version is {shown(version)}, not {' or '.join([sha[:7], *tags])}")
    if env not in doc["environments"]:
        out.append(f"environments are {', '.join(doc['environments'])}, not {env}")
    if now < start:
        out.append(f"window starts {start.isoformat()}")
    elif now > end:
        out.append(f"window ended {end.isoformat()}")
    return out


class FileChanges:
    """Changes are files in a repo, each holding its own requester and approvers.

    The files are trusted as is. This stands in for a system that protects who approved.
    """

    def __init__(self, gh: GitHub, org: str, repo: str):
        self.gh, self.base = gh, f"/repos/{org}/{repo}"
        self.html = f"https://github.com/{org}/{repo}"

    def location(self, repo: str) -> str:
        return f"{self.html}/tree/main/changes/{repo}"

    def find(self, repo: str, sha: str, tags: list[str], env: str, now: datetime) -> list[Change]:
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
            # Not download_url: raw.githubusercontent.com may serve a copy minutes old.
            doc = yaml.safe_load(base64.b64decode(self.gh.get(f["url"])["content"]))
            found.append(Change(f["path"], f["html_url"], doc["requested_by"], set(doc["approvers"]),
                                doc["emergency"], misses(doc, sha, tags, env, now)))
        return found
