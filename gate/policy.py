"""Merge and deploy policy. Pure functions: facts in, verdict out.

A rule set is a list of rules plus whether break-glass may override it. Every repo gets the
baseline sets and adds its own from the manifest. All selected sets must pass.
"""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from itertools import permutations
from typing import Callable
from zoneinfo import ZoneInfo

APPROVE_DEPLOY = "/approve-deploy"
APPROVE_BREAK_GLASS = "/approve-break-glass"
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@dataclass
class Facts:
    repo: str
    team: str  # owning team slug from the manifest, e.g. team-payments
    actor: str  # PR author or deploy trigger; never counts as an approver
    sha: str
    now: datetime
    manifest_status: str | None = None
    branch: str = ""
    files: list[str] = field(default_factory=list)
    approvers: set[str] = field(default_factory=set)
    workflows: list[str] = field(default_factory=list)  # reusable workflows used, "owner/repo/path@ref"
    checks: dict[str, str] = field(default_factory=dict)  # check name -> conclusion
    other_runs: list[str] = field(default_factory=list)  # conclusions of other workflow runs on the commit
    break_glass: list["BreakGlass"] = field(default_factory=list)


@dataclass
class BreakGlass:
    issue: int
    created_at: datetime
    approvers: set[str]


@dataclass
class RuleSet:
    name: str
    rules: list[dict]  # [{rule_name: params}]
    break_glass: bool = False


@dataclass
class Verdict:
    passed: bool
    reasons: list[str] = field(default_factory=list)


Members = Callable[[str], set[str]]


def current_approvers(reviews: list[dict], head_sha: str) -> set[str]:
    """Users whose latest review approves the head commit."""
    latest = {}
    for r in reviews:
        if r["state"] in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest[r["user"]] = r
    return {u for u, r in latest.items() if r["state"] == "APPROVED" and r["commit_id"] == head_sha}


def slots_filled(slots: list[list[str]], approvers: set[str], team: str, members: Members) -> bool:
    """Each slot is a list of groups. Every slot needs a different approver from one of its groups."""
    area = team.removeprefix("team-")
    eligible = [set().union(*(members(g.format(team=area)) for g in slot)) & approvers for slot in slots]
    return any(all(p in e for p, e in zip(people, eligible)) for people in permutations(approvers, len(slots)))


def _approvals(f: Facts, p: dict, members: Members) -> str | None:
    if slots_filled(p["slots"], f.approvers - {f.actor}, f.team, members):
        return None
    return "Needs approval from " + " and ".join(" or ".join(s) for s in p["slots"]).format(team=f.team.removeprefix("team-"))


def _approvals_if_changed(f: Facts, p: dict, members: Members) -> str | None:
    if not any(x.startswith(tuple(p["paths"])) for x in f.files):
        return None
    reason = _approvals(f, p, members)
    return reason and f"Changes under {', '.join(p['paths'])}: {reason[0].lower()}{reason[1:]}"


def _manifest_active(f: Facts, p: dict, members: Members) -> str | None:
    return None if f.manifest_status == "active" else f"{f.repo} is not active in the org-config manifest"


def _central_template(f: Facts, p: dict, members: Members) -> str | None:
    return None if any(w.split("@")[0] == p["workflow"] for w in f.workflows) else f"Deploy does not use {p['workflow']}"


def _from_branch(f: Facts, p: dict, members: Members) -> str | None:
    return None if f.branch == p["branch"] else f"Deploying from {f.branch}, not {p['branch']}"


def _check_passed(f: Facts, p: dict, members: Members) -> str | None:
    return None if f.checks.get(p["name"]) == "success" else f"Check {p['name']} has not passed on {f.sha[:7]}"


def _pipeline_green(f: Facts, p: dict, members: Members) -> str | None:
    bad = [c for c in f.other_runs if c not in ("success", "skipped", "neutral")]
    return f"{len(bad)} other workflow run(s) on {f.sha[:7]} are not green" if bad else None


def _deploy_window(f: Facts, p: dict, members: Members) -> str | None:
    local = f.now.astimezone(ZoneInfo(p["timezone"]))
    day = DAYS[local.weekday()]
    for block in p["blocked"]:
        if day in block["days"] and local.time() >= time.fromisoformat(block.get("after", "00:00")):
            return f"Deploys are blocked {', '.join(block['days'])} after {block.get('after', '00:00')} {p['timezone']}"
    return None


RULES = {
    "approvals": _approvals,
    "approvals_if_changed": _approvals_if_changed,
    "manifest_active": _manifest_active,
    "central_template": _central_template,
    "from_branch": _from_branch,
    "check_passed": _check_passed,
    "pipeline_green": _pipeline_green,
    "deploy_window": _deploy_window,
}


def _run(rs: RuleSet, f: Facts, members: Members) -> list[str]:
    reasons = []
    for rule in rs.rules:
        name, params = next(iter(rule.items())) if isinstance(rule, dict) else (rule, {})
        reason = RULES[name](f, params or {}, members)
        if reason:
            reasons.append(f"[{rs.name}] {reason}")
    return reasons


def evaluate(
    rulesets: list[RuleSet], f: Facts, members: Members, break_glass: dict | None = None
) -> Verdict:
    """break_glass: {"slots": [...], "ttl_hours": n}. None means no override is possible."""
    hard, soft = [], []
    for rs in rulesets:
        (soft if rs.break_glass else hard).extend(_run(rs, f, members))
    if hard:
        return Verdict(False, hard + soft)
    if not soft:
        return Verdict(True)
    if break_glass:
        ttl = timedelta(hours=break_glass["ttl_hours"])
        for bg in f.break_glass:
            if f.now - bg.created_at <= ttl and slots_filled(break_glass["slots"], bg.approvers - {f.actor}, f.team, members):
                return Verdict(True, soft + [f"Overridden by break-glass issue #{bg.issue}"])
        soft.append(
            f"To override, open an issue labelled break-glass naming {f.sha}, approved with {APPROVE_BREAK_GLASS}"
        )
    return Verdict(False, soft)
