import unittest

from gate.policy import PullRequest, current_approvers, evaluate

HEAD = "head"
MANIFEST = {"payments-web": {"status": "active", "team": "team-payments"}}


def review(user, state, commit=HEAD):
    return {"user": user, "state": state, "commit_id": commit}


def pr(files=("index.js",), reviews=(), repo="payments-web", author="dev"):
    return PullRequest(repo=repo, number=1, author=author, head_sha=HEAD, files=list(files), reviews=list(reviews))


class CurrentApprovers(unittest.TestCase):
    def test_latest_review_wins(self):
        reviews = [review("a", "APPROVED"), review("a", "CHANGES_REQUESTED")]
        self.assertEqual(current_approvers(reviews, HEAD), set())

    def test_comment_does_not_cancel_approval(self):
        reviews = [review("a", "APPROVED"), review("a", "COMMENTED")]
        self.assertEqual(current_approvers(reviews, HEAD), {"a"})

    def test_approval_of_older_commit_is_stale(self):
        self.assertEqual(current_approvers([review("a", "APPROVED", commit="old")], HEAD), set())


class Evaluate(unittest.TestCase):
    def test_plain_change_in_active_repo_passes(self):
        self.assertTrue(evaluate(pr(), MANIFEST, set()).passed)

    def test_repo_missing_from_manifest_fails(self):
        self.assertFalse(evaluate(pr(repo="rogue"), MANIFEST, set()).passed)

    def test_archived_repo_fails(self):
        manifest = {"payments-web": {"status": "archived", "team": "team-payments"}}
        self.assertFalse(evaluate(pr(), manifest, set()).passed)

    def test_workflow_change_without_sentinel_fails(self):
        v = evaluate(pr(files=[".github/workflows/ci.yml"], reviews=[review("peer", "APPROVED")]), MANIFEST, {"sen"})
        self.assertFalse(v.passed)

    def test_workflow_change_with_sentinel_passes(self):
        v = evaluate(pr(files=[".github/workflows/ci.yml"], reviews=[review("sen", "APPROVED")]), MANIFEST, {"sen"})
        self.assertTrue(v.passed)

    def test_sentinel_cannot_approve_own_workflow_change(self):
        v = evaluate(pr(files=[".github/workflows/ci.yml"], reviews=[review("sen", "APPROVED")], author="sen"), MANIFEST, {"sen"})
        self.assertFalse(v.passed)


if __name__ == "__main__":
    unittest.main()
