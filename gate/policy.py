"""Merge and deploy policy. Pure functions: facts in, verdict out.

A rule set is a list of rules plus whether break-glass may override it. Every repo gets the
baseline sets and adds its own from the manifest. All selected sets must pass.
"""

from dataclasses import dataclass, field
from datetime import datetime, time
from itertools import permutations
from typing import Callable
from zoneinfo import ZoneInfo

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@dataclass
class Facts:
    org: str
    repo: str
    team: str  # owning team slug from the manifest, e.g. team-payments
    actor: str  # PR author or deploy trigger; never counts as an approver
    sha: str
    now: datetime
    manifest_status: str | None = None
    branch: str = ""  # branch or tag the run was started from
    tags: list[str] = field(default_factory=list)  # release tags (X.Y.Z) on the commit
    on_branches: list[str] = field(default_factory=list)  # release branches (main, release/*) that contain the commit
    deployed_to: dict[str, str] = field(default_factory=dict)  # environment -> link to its last successful deploy of the commit
    files: list[str] = field(default_factory=list)
    approvers: set[str] = field(default_factory=set)
    workflows: list[str] = field(default_factory=list)  # reusable workflows used, "owner/repo/path@ref"
    # check name -> {"conclusion", "url", "pr": {"number", "url"} or None when the check is on the commit itself}
    checks: dict[str, dict] = field(default_factory=dict)
    other_runs: list[dict] = field(default_factory=list)  # other workflow runs on the commit: name, event, conclusion, url
    env: str = ""  # deploys only
    changes: list["Change"] = field(default_factory=list)  # the repo's change requests
    changes_url: str = ""  # where the repo's change requests live
    # attestation_key(params) -> {"signer": workflow ref that signed, "predicate": ...} of a verified attestation, or None
    attestations: dict[str, dict | None] = field(default_factory=dict)


@dataclass
class Change:
    id: str  # where the change lives, e.g. changes/payments-web/x.yaml
    url: str  # where its approvals can be seen
    author: str  # never counts as an approver
    approvers: set[str]
    emergency: bool
    misses: list[str] = field(default_factory=list)  # why it does not cover this deploy; empty if it does


@dataclass
class RuleSet:
    name: str
    rules: list[dict]  # [{rule_name: params}]
    break_glass: bool = False


@dataclass
class Verdict:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # how passing rules were met, for tracing


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


def _groups(slots: list[list[str]], team: str) -> list[list[str]]:
    area = team.removeprefix("team-")
    return [[g.format(team=area) for g in slot] for slot in slots]


def _need(f: Facts, slots: list[list[str]]) -> str:
    """Slots as text, each group linked to its team page."""
    link = lambda g: f"[{g}](https://github.com/orgs/{f.org}/teams/{g})"
    return " and ".join(" or ".join(link(g) for g in slot) for slot in _groups(slots, f.team))


def _who_counts(approvers: set[str], excluded: dict[str, str], slots: list[list[str]], f: Facts,
                members: Members) -> str:
    """Whether each approver counts, and why. excluded: login -> why they never count."""
    groups = sorted({g for slot in _groups(slots, f.team) for g in slot})
    notes = []
    for a in sorted(approvers):
        inside = [g for g in groups if a in members(g)]
        if a in excluded:
            notes.append(f"{a} does not count: {excluded[a]}")
        elif inside:
            notes.append(f"{a} counts for {', '.join(inside)}")
        else:
            notes.append(f"{a} does not count: not in {' or '.join(groups)}")
    return "Approvers: " + ("; ".join(notes) if notes else "none")


def _approvals(f: Facts, p: dict, members: Members) -> str | None:
    if slots_filled(p["slots"], f.approvers - {f.actor}, f.team, members):
        return None
    who = _who_counts(f.approvers, {f.actor: "opened the PR"}, p["slots"], f, members)
    return f"Needs approval from {_need(f, p['slots'])}. {who}"


def _approvals_if_changed(f: Facts, p: dict, members: Members) -> str | None:
    if not any(x.startswith(tuple(p["paths"])) for x in f.files):
        return None
    reason = _approvals(f, p, members)
    return reason and f"Changes under {', '.join(p['paths'])}: {reason[0].lower()}{reason[1:]}"


def covering(f: Facts) -> list[Change]:
    return [c for c in f.changes if not c.misses]


def _excluded(c: Change, f: Facts) -> dict[str, str]:
    # The deployer wins when one person is both, since that is the rule that applies to this deploy.
    return {c.author: "requested the change", f.actor: "started this deploy"}


def _change_filled(c: Change, slots: list[list[str]], f: Facts, members: Members) -> bool:
    return slots_filled(slots, c.approvers - _excluded(c, f).keys(), f.team, members)


def _change_explained(c: Change, slots: list[list[str]], f: Facts, members: Members) -> str:
    return f"[{c.id}]({c.url}). {_who_counts(c.approvers, _excluded(c, f), slots, f, members)}"


def _change_approved(f: Facts, p: dict, members: Members) -> str | None:
    found = covering(f)
    if not found:
        lines = [f"No change request covers {f.sha[:7]} to {f.env} at {f.now:%Y-%m-%d %H:%M} UTC. "
                 f"Request one in [changes for {f.repo}]({f.changes_url})"]
        lines += [f"[{c.id}]({c.url}): {'; '.join(c.misses)}" for c in f.changes]
        return "\n  - ".join(lines)
    if any(_change_filled(c, p["slots"], f, members) for c in found):
        return None
    lines = [f"Change needs approval from {_need(f, p['slots'])}"]
    lines += [_change_explained(c, p["slots"], f, members) for c in found]
    return "\n  - ".join(lines)


def _change_approved_note(f: Facts, p: dict, members: Members) -> str:
    c = next(c for c in covering(f) if _change_filled(c, p["slots"], f, members))
    return f"Approved by change {_change_explained(c, p['slots'], f, members)}"


def _manifest_active(f: Facts, p: dict, members: Members) -> str | None:
    return None if f.manifest_status == "active" else f"{f.repo} is not active in org-manifest"


def _central_template(f: Facts, p: dict, members: Members) -> str | None:
    return None if any(w.split("@")[0] == p["workflow"] for w in f.workflows) else f"Deploy does not use {p['workflow']}"


def _from_branch(f: Facts, p: dict, members: Members) -> str | None:
    return None if f.branch == p["branch"] else f"Deploying from {f.branch}, not {p['branch']}"


def _release_tag(f: Facts, p: dict, members: Members) -> str | None:
    if not f.tags:
        return f"{f.sha[:7]} has no release tag (X.Y.Z). Cut a release with ci-templates release.yml"
    if not f.on_branches:
        return f"{', '.join(f.tags)} is on {f.sha[:7]}, which is not on main or a release/X.Y.z branch"
    return None


def _release_tag_note(f: Facts, p: dict, members: Members) -> str:
    return f"Release {', '.join(f.tags)} at {f.sha[:7]}, on {', '.join(f.on_branches)}"


def _deployed_to(f: Facts, p: dict, members: Members) -> str | None:
    env = p["environment"]
    return None if env in f.deployed_to else f"{f.sha[:7]} has not been deployed to {env} successfully. Deploy it there first"


def _deployed_to_note(f: Facts, p: dict, members: Members) -> str:
    env = p["environment"]
    return f"Deployed to {env} before: [deploy]({f.deployed_to[env]})"


def _check_passed(f: Facts, p: dict, members: Members) -> str | None:
    name, check = p["name"], f.checks.get(p["name"])
    if check is None:
        return f"No {name} check on {f.sha[:7]} or the PR that merged it"
    if check["conclusion"] == "success":
        return None
    where = f"PR [#{check['pr']['number']}]({check['pr']['url']})" if check["pr"] else f.sha[:7]
    merged = ", and the PR was merged anyway" if check["pr"] else ""
    return f"Check [{name}]({check['url']}) {check['conclusion']} on {where}{merged}"


def _pipeline_green(f: Facts, p: dict, members: Members) -> str | None:
    # Only runs started by these events count, so a failed release or deploy run does not block later deploys.
    bad = [r for r in f.other_runs if r["event"] in p["events"] and r["conclusion"] not in ("success", "skipped", "neutral")]
    if not bad:
        return None
    runs = ", ".join(f"[{r['name']}]({r['url']}) ({r['event']}): {r['conclusion']}" for r in bad)
    return f"Runs on {f.sha[:7]} are not green: {runs}"


def _deploy_window(f: Facts, p: dict, members: Members) -> str | None:
    local = f.now.astimezone(ZoneInfo(p["timezone"]))
    day = DAYS[local.weekday()]
    for block in p["blocked"]:
        if day in block["days"] and local.time() >= time.fromisoformat(block.get("after", "00:00")):
            when = f" after {block['after']} {p['timezone']}" if "after" in block else f" ({p['timezone']})"
            return f"Deploys are blocked on {', '.join(block['days'])}{when}"
    return None


def attestation_key(p: dict) -> str:
    return "|".join([*p["signer_workflows"], p["predicate_type"]])


def _attested(f: Facts, p: dict, members: Members) -> str | None:
    found = f.attestations.get(attestation_key(p))
    if found is None:
        return f"No attestation for {f.sha[:7]} signed by {' or '.join(p['signer_workflows'])}"
    predicate = found["predicate"]
    wrong = [k for k, v in p.get("require", {}).items() if predicate.get(k) != v]
    if wrong:
        return f"Attestation for {f.sha[:7]} does not show " + ", ".join(f"{k}={p['require'][k]}" for k in wrong)
    return None


# Rules whose pass is worth explaining: name -> function giving the note.
def _attested_note(f: Facts, p: dict, members: Members) -> str:
    found = f.attestations[attestation_key(p)]
    shown = ", ".join(f"{k}={v}" for k, v in found["predicate"].items())
    return f"Signed by {found['signer']}: {shown}"


NOTES = {"change_approved": _change_approved_note, "release_tag": _release_tag_note,
         "deployed_to": _deployed_to_note, "attested": _attested_note}

RULES = {
    "attested": _attested,
    "change_approved": _change_approved,
    "approvals": _approvals,
    "approvals_if_changed": _approvals_if_changed,
    "manifest_active": _manifest_active,
    "central_template": _central_template,
    "from_branch": _from_branch,
    "release_tag": _release_tag,
    "deployed_to": _deployed_to,
    "check_passed": _check_passed,
    "pipeline_green": _pipeline_green,
    "deploy_window": _deploy_window,
}


def _run(rs: RuleSet, f: Facts, members: Members, notes: list[str]) -> list[str]:
    reasons = []
    for rule in rs.rules:
        name, params = next(iter(rule.items())) if isinstance(rule, dict) else (rule, {})
        reason = RULES[name](f, params or {}, members)
        if reason:
            reasons.append(f"[{rs.name}] {reason}")
        elif name in NOTES:
            notes.append(f"[{rs.name}] {NOTES[name](f, params or {}, members)}")
    return reasons


def evaluate(
    rulesets: list[RuleSet], f: Facts, members: Members, break_glass: dict | None = None
) -> Verdict:
    """break_glass: {"slots": [...]}, the approvals an emergency change needs to override soft rule sets."""
    hard, soft, notes = [], [], []
    for rs in rulesets:
        (soft if rs.break_glass else hard).extend(_run(rs, f, members, notes))
    if hard:
        return Verdict(False, hard + soft, notes)
    if not soft:
        return Verdict(True, notes=notes)
    if break_glass:
        slots = break_glass["slots"]
        emergencies = [c for c in covering(f) if c.emergency]
        for c in emergencies:
            if _change_filled(c, slots, f, members):
                return Verdict(True, soft + [f"Overridden by emergency change {_change_explained(c, slots, f, members)}"],
                               notes)
        for c in emergencies:
            soft.append(f"Emergency change cannot override: needs {_need(f, slots)}\n"
                        f"  - {_change_explained(c, slots, f, members)}")
        if not emergencies:
            soft.append(f"To override, request an emergency change with break-glass approvals in "
                        f"[changes for {f.repo}]({f.changes_url})")
    return Verdict(False, soft, notes)
