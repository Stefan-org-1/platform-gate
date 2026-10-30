import unittest
from datetime import datetime, timedelta, timezone

from gate.policy import BreakGlass, Facts, RuleSet, current_approvers, evaluate, slots_filled

HEAD = "a" * 40
WED_NOON = datetime(2026, 10, 28, 16, 0, tzinfo=timezone.utc)  # 12:00 Toronto
FRI_EVENING = datetime(2026, 10, 30, 21, 0, tzinfo=timezone.utc)  # 17:00 Toronto
TEMPLATE = "Stefan-org-1/ci-templates/.github/workflows/deploy.yml"

GROUPS = {
    "team-payments": {"dev", "sen", "mgr"},
    "sentinels-payments": {"sen"},
    "managers-payments": {"mgr"},
    "group-sentinels": {"sen", "other-sen"},
}
members = lambda g: GROUPS.get(g, set())

INTEGRITY = RuleSet("platform-integrity", ["manifest_active"])
CENTRAL = RuleSet("central-deploy", [{"central_template": {"workflow": TEMPLATE}}])
WINDOW = RuleSet("deploy-window", [{"deploy_window": {
    "timezone": "America/Toronto", "blocked": [{"days": ["fri"], "after": "16:00"}, {"days": ["sat", "sun"]}]}}],
    break_glass=True)
FROM_MAIN = RuleSet("from-main", [{"from_branch": {"branch": "main"}}, {"check_passed": {"name": "policy"}}], break_glass=True)
TWO_PERSON = RuleSet("two-person-deploy", [{"approvals": {"slots": [["sentinels-{team}"], ["managers-{team}"]]}}])
WORKFLOW_SENTINEL = RuleSet("workflow-sentinel", [{"approvals_if_changed": {
    "paths": [".github/workflows/"], "slots": [["group-sentinels"]]}}])
BREAK_GLASS = {"slots": [["sentinels-{team}"], ["managers-{team}"]], "ttl_hours": 24}


def facts(**kw):
    base = dict(repo="payments-web", team="team-payments", actor="dev", sha=HEAD, now=WED_NOON,
                manifest_status="active", branch="main", workflows=[f"{TEMPLATE}@refs/tags/v1"],
                checks={"policy": "success"})
    return Facts(**{**base, **kw})


def review(user, state, commit=HEAD):
    return {"user": user, "state": state, "commit_id": commit}


class CurrentApprovers(unittest.TestCase):
    def test_latest_review_wins(self):
        self.assertEqual(current_approvers([review("a", "APPROVED"), review("a", "CHANGES_REQUESTED")], HEAD), set())

    def test_comment_does_not_cancel_approval(self):
        self.assertEqual(current_approvers([review("a", "APPROVED"), review("a", "COMMENTED")], HEAD), {"a"})

    def test_approval_of_older_commit_is_stale(self):
        self.assertEqual(current_approvers([review("a", "APPROVED", commit="old")], HEAD), set())


class Slots(unittest.TestCase):
    slots = [["sentinels-{team}"], ["managers-{team}"]]

    def test_two_people_fill_two_slots(self):
        self.assertTrue(slots_filled(self.slots, {"sen", "mgr"}, "team-payments", members))

    def test_one_person_in_both_groups_fills_only_one_slot(self):
        both = lambda g: {"boss"} if g in ("sentinels-payments", "managers-payments") else set()
        self.assertFalse(slots_filled(self.slots, {"boss"}, "team-payments", both))

    def test_team_placeholder_uses_owning_team(self):
        self.assertFalse(slots_filled(self.slots, {"sen", "mgr"}, "team-cards", members))


class Merge(unittest.TestCase):
    def test_plain_change_passes(self):
        self.assertTrue(evaluate([INTEGRITY, WORKFLOW_SENTINEL], facts(files=["index.js"]), members).passed)

    def test_inactive_repo_fails(self):
        self.assertFalse(evaluate([INTEGRITY], facts(manifest_status="archived"), members).passed)

    def test_workflow_change_needs_sentinel(self):
        f = facts(files=[".github/workflows/ci.yml"], approvers={"mgr"})
        self.assertFalse(evaluate([WORKFLOW_SENTINEL], f, members).passed)

    def test_workflow_change_with_sentinel_passes(self):
        f = facts(files=[".github/workflows/ci.yml"], approvers={"other-sen"})
        self.assertTrue(evaluate([WORKFLOW_SENTINEL], f, members).passed)

    def test_author_cannot_approve_own_change(self):
        f = facts(actor="sen", files=[".github/workflows/ci.yml"], approvers={"sen"})
        self.assertFalse(evaluate([WORKFLOW_SENTINEL], f, members).passed)


class Deploy(unittest.TestCase):
    sets = [INTEGRITY, CENTRAL, WINDOW, FROM_MAIN, TWO_PERSON]

    def test_compliant_deploy_passes(self):
        self.assertTrue(evaluate(self.sets, facts(approvers={"sen", "mgr"}), members, BREAK_GLASS).passed)

    def test_missing_second_approver_fails(self):
        self.assertFalse(evaluate(self.sets, facts(approvers={"sen"}), members, BREAK_GLASS).passed)

    def test_deploy_outside_template_cannot_be_broken_glass(self):
        bg = BreakGlass(7, WED_NOON, {"sen", "mgr"})
        f = facts(workflows=[], approvers={"sen", "mgr"}, break_glass=[bg])
        self.assertFalse(evaluate(self.sets, f, members, BREAK_GLASS).passed)

    def test_friday_evening_blocked(self):
        v = evaluate(self.sets, facts(now=FRI_EVENING, approvers={"sen", "mgr"}), members, BREAK_GLASS)
        self.assertFalse(v.passed)
        self.assertIn("deploy-window", v.reasons[0])

    def test_break_glass_overrides_window(self):
        bg = BreakGlass(7, FRI_EVENING - timedelta(hours=1), {"sen", "mgr"})
        f = facts(now=FRI_EVENING, approvers={"sen", "mgr"}, break_glass=[bg])
        self.assertTrue(evaluate(self.sets, f, members, BREAK_GLASS).passed)

    def test_expired_break_glass_ignored(self):
        bg = BreakGlass(7, FRI_EVENING - timedelta(hours=25), {"sen", "mgr"})
        f = facts(now=FRI_EVENING, approvers={"sen", "mgr"}, break_glass=[bg])
        self.assertFalse(evaluate(self.sets, f, members, BREAK_GLASS).passed)

    def test_break_glass_needs_both_approvers(self):
        bg = BreakGlass(7, FRI_EVENING, {"sen"})
        f = facts(now=FRI_EVENING, approvers={"sen", "mgr"}, break_glass=[bg])
        self.assertFalse(evaluate(self.sets, f, members, BREAK_GLASS).passed)

    def test_hotfix_branch_without_merge_check_needs_break_glass(self):
        f = facts(branch="hotfix", checks={}, approvers={"sen", "mgr"})
        self.assertFalse(evaluate(self.sets, f, members, BREAK_GLASS).passed)


class Attested(unittest.TestCase):
    params = {"signer_workflow": "Stefan-org-1/ci-templates/.github/workflows/node-build.yml",
              "predicate_type": "https://example.com/scan/v1", "require": {"secret_scan": "pass"}}
    sets = [RuleSet("scanned", [{"attested": params}])]
    key = f"{params['signer_workflow']}|{params['predicate_type']}"

    def test_missing_attestation_fails(self):
        self.assertFalse(evaluate(self.sets, facts(), members).passed)

    def test_passing_attestation_passes(self):
        f = facts(attestations={self.key: {"secret_scan": "pass"}})
        self.assertTrue(evaluate(self.sets, f, members).passed)

    def test_failing_predicate_fails(self):
        f = facts(attestations={self.key: {"secret_scan": "fail"}})
        self.assertFalse(evaluate(self.sets, f, members).passed)


if __name__ == "__main__":
    unittest.main()
