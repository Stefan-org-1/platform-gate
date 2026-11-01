import pathlib
import unittest

from gate.__main__ import Config

ORG_CONFIG = pathlib.Path(__file__).parents[2] / "org-config"


@unittest.skipUnless(ORG_CONFIG.is_dir(), "org-config checkout not next to platform-gate")
class LoadsOrgConfig(unittest.TestCase):
    def test_every_named_rule_set_exists(self):
        config = Config.from_dir(ORG_CONFIG)
        self.assertTrue(config.deploy_sets("payments-web", "prod"))

    def test_unknown_rule_set_is_rejected(self):
        files = {"baseline.yaml": "merge: [nope]\ndeploy: []\n", "change-management.yaml": "repo: x\n"}
        with self.assertRaises(ValueError):
            Config(files)
