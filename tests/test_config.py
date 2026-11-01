import pathlib
import unittest

from gate.__main__ import Config

LAB = pathlib.Path(__file__).parents[2]
MANIFEST, POLICY = LAB / "org-manifest", LAB / "org-policy"


@unittest.skipUnless(MANIFEST.is_dir() and POLICY.is_dir(), "org-manifest and org-policy not next to platform-gate")
class LoadsOrgConfig(unittest.TestCase):
    def test_every_named_rule_set_exists(self):
        config = Config.from_dirs(MANIFEST, POLICY)
        self.assertTrue(config.deploy_sets("payments-web", "prod"))

    def test_unknown_rule_type_is_rejected(self):
        files = {"baseline.yaml": "merge: [x]\ndeploy: {all: []}\n", "change-management.yaml": "source: file\nrepo: x\n",
                 "rulesets/x.yaml": "rules: [no_such_rule]\n"}
        with self.assertRaisesRegex(ValueError, "unknown rule type no_such_rule"):
            Config(files)

    def test_unknown_rule_set_is_rejected(self):
        files = {"baseline.yaml": "merge: [nope]\ndeploy: {all: []}\n", "change-management.yaml": "source: file\nrepo: x\n"}
        with self.assertRaises(ValueError):
            Config(files)
