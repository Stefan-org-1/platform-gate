"""Merge policy. Pure functions: facts in, verdict out."""

from dataclasses import dataclass, field

WORKFLOW_DIR = ".github/workflows/"


@dataclass
class PullRequest:
    repo: str
    number: int
    author: str
    head_sha: str
    files: list[str]
    reviews: list[dict]


@dataclass
class Verdict:
    passed: bool
    reasons: list[str] = field(default_factory=list)


def current_approvers(reviews: list[dict], head_sha: str) -> set[str]:
    """Users whose latest review approves the head commit."""
    latest = {}
    for r in reviews:
        if r["state"] in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest[r["user"]] = r
    return {u for u, r in latest.items() if r["state"] == "APPROVED" and r["commit_id"] == head_sha}


def evaluate(pr: PullRequest, manifest: dict[str, dict], sentinels: set[str]) -> Verdict:
    reasons = []

    entry = manifest.get(pr.repo)
    if entry is None:
        reasons.append(f"{pr.repo} is not in the org-config manifest")
    elif entry["status"] != "active":
        reasons.append(f"{pr.repo} is {entry['status']} in the org-config manifest")

    if any(f.startswith(WORKFLOW_DIR) for f in pr.files):
        approvers = current_approvers(pr.reviews, pr.head_sha) - {pr.author}
        if not approvers & sentinels:
            reasons.append("Workflow changes need approval from a sentinel other than the author")

    return Verdict(passed=not reasons, reasons=reasons)
