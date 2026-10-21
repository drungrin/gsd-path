import json
import subprocess
import unittest
from unittest import mock

from scripts import integration, members
from scripts.archive_milestone import ArchiveError
from tests._editorial_fixtures import publication_fixture, TITLE as EDITORIAL_TITLE
import tests.test_member_integration as member_integration

git = member_integration.git
ARCHIVE = member_integration.ARCHIVE
SUBJECT = member_integration.SUBJECT
TAG = member_integration.TAG
URL = "https://github.com/acme/web/pull/7"


class MemberPullRequestTests(unittest.TestCase):
    fixture = member_integration.MemberIntegrationTests.fixture

    def setUp(self) -> None:
        member_integration.MemberIntegrationTests.setUp(self)
        self.pulls, self.posts, self.patches = [], [], []
        self.tags_at_patch = []
        self.merge_queue = False
        self.auto_merge = False
        self.merge_actor = "User"
        self.merge_event = True
        publication_fixture(self.root, ARCHIVE, 'web', self.member_tip, 'https://github.com/acme/web.git')

    remote_ref = member_integration.MemberIntegrationTests.remote_ref

    pulls: list
    posts: list

    def gh(self, *arguments: str) -> subprocess.CompletedProcess:
        def reply(payload) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(arguments, 0, json.dumps(payload), "")
        if arguments[:2] == ("gh", "auth"):
            return subprocess.CompletedProcess(arguments, 0, "", "")
        if "graphql" in arguments:
            merged = [pull["merge_commit_sha"] for pull in self.pulls if pull["merged_at"]]
            nodes = [{"__typename": "MergedEvent", "actor": {"__typename": self.merge_actor, "login": "owner"},
                      "commit": {"oid": merged[0]}}] if merged and self.merge_event else []
            return reply({"data": {"repository": {"pullRequest": {
                "baseRefOid": self.remote_ref("refs/heads/" + getattr(self, "member_default", "main")),
                "mergeQueue": {"nodes": [{"__typename": "AddedToMergeQueueEvent"}] if self.merge_queue else []},
                "autoMerge": {"nodes": [{"__typename": "AutoMergeEnabledEvent"}] if self.auto_merge else []},
                "mergeAction": {"nodes": nodes}}}}})
        if "PATCH" in arguments:
            self.patches.append(arguments)
            self.tags_at_patch.append(self.remote_ref(f"refs/tags/{TAG}"))
            self.pulls[0]["body"] = next(argument.removeprefix("body=") for argument in arguments
                                         if argument.startswith("body="))
            return reply(self.pulls[0])
        base = next((argument.removeprefix("base=") for argument in arguments if argument.startswith("base=")), None)
        if "POST" in arguments:
            self.posts.append(arguments)
            repository = getattr(self, "repository_name", "acme/web")
            self.pulls.append({"number": 7, "state": "open",
                               "html_url": f"https://github.com/{repository}/pull/7", "merged_at": None,
                               "title": next(a.removeprefix('title=') for a in arguments if a.startswith('title=')),
                               "merge_commit_sha": None,
                               "base": {"ref": base, "sha": self.remote_ref("refs/heads/" + base)},
                               "body": next(argument.removeprefix("body=") for argument in arguments
                                            if argument.startswith("body=")),
                               "head": {"ref": getattr(self, "bound", "feature/demo-milestone-001"),
                                        "sha": self.member_tip, "repo": {"full_name": repository}}})
            return reply(self.pulls[-1])
        # GitHub lists only the pull requests that target the requested base.
        return reply([[pull for pull in self.pulls if pull["base"]["ref"] == base]])

    def integrate(self) -> dict:
        with mock.patch.object(integration, "github_repository", return_value="acme/web"), \
                mock.patch.object(integration, "run_command", side_effect=self.gh):
            return integration.integrate_member_pull_request(self.root, "web", ARCHIVE, self.member_tip)

    def github_merge(self, *extra: str) -> str:
        """Merge the pull request on the remote the way GitHub's merge button does."""
        clone = self.root.parent / "github"
        git(self.root.parent, "clone", "-q", str(self.remote), str(clone))
        git(clone, "config", "user.name", "t")
        git(clone, "config", "user.email", "t@t")
        bound = getattr(self, "bound", "feature/demo-milestone-001")
        git(clone, "merge", "-q", *extra, "-m", "Merge pull request #7", "origin/" + bound)
        if "--squash" in extra:
            git(clone, "commit", "-q", "-m", "Squashed pull request #7")
        git(clone, "push", "-q", "origin", "HEAD:refs/heads/" + getattr(self, "member_default", "main"))
        merge = git(clone, "rev-parse", "HEAD")
        self.pulls[0].update(state="closed", merged_at="2026-09-28T00:00:00Z", merge_commit_sha=merge)
        return merge

    def test_opens_one_pull_request_and_waits(self) -> None:
        result = self.integrate()
        self.assertEqual((result["status"], result["pull_request"]), ("awaiting-merge", URL))
        again = self.integrate()
        self.assertEqual(again["status"], "awaiting-merge")
        self.assertEqual(len(self.posts), 1)
        post = self.posts[0]
        self.assertIn(f"title={EDITORIAL_TITLE}", post)
        self.assertIn("head=feature/demo-milestone-001", post)
        self.assertIn("base=main", post)
        body = next(argument for argument in post if argument.startswith("body="))
        self.assertIn("## Problem", body)
        self.assertNotIn("prepared with", body.lower())
        self.assertEqual(self.remote_ref("refs/heads/feature/demo-milestone-001"), self.member_tip)
        self.assertEqual(self.remote_ref("refs/heads/main"), self.main)
        self.assertTrue(members.authorized(self.member, "demo", "push", "refs/heads/feature/demo-milestone-001",
                                           self.member_tip))

    def test_merged_pull_request_is_tagged_and_validated(self) -> None:
        self.integrate()
        merge = self.github_merge("--no-ff")
        result = self.integrate()
        self.assertEqual(result["merge"], merge)
        self.assertEqual(self.remote_ref(f"refs/tags/{TAG}^{{commit}}"), merge)
        message = git(self.remote, "for-each-ref", "--format=%(contents)", f"refs/tags/{TAG}")
        self.assertIn("Mode: pull-request", message)
        self.assertIn(f"Pull-Request: {URL}", message)
        self.assertIn(f"Reviewed-HEAD: {self.member_tip}", message)
        self.assertTrue(members.authorized(self.member, "demo", "push", f"refs/tags/{TAG}",
                                           self.remote_ref(f"refs/tags/{TAG}")))
        with mock.patch.object(integration, "github_repository", return_value="acme/web"), \
                mock.patch.object(integration, "run_command", side_effect=self.gh):
            self.assertEqual(integration.validate_member_integrated(self.root, "web", ARCHIVE, self.member_tip),
                             result)
            with self.assertRaisesRegex(ArchiveError, "different reviewed HEAD"):
                integration.validate_member_integrated(self.root, "web", ARCHIVE, self.main)

    def test_reused_open_pull_request_repairs_body_without_rewriting_title(self) -> None:
        self.integrate()
        human = self.pulls[0]['body'] + '\nHuman explanation'
        self.pulls[0].update(title="feat(web): existing editorial title", body=human + "\n\n---\nPR prepared with GSD Path.")
        result = self.integrate()
        self.assertEqual(result["status"], "awaiting-merge")
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(len(self.patches), 1)
        self.assertEqual(self.pulls[0]["title"], "feat(web): existing editorial title")
        self.assertEqual(self.pulls[0]["body"],
                         human)

    def test_merged_pull_request_repairs_body_before_tagging(self) -> None:
        self.integrate()
        self.github_merge("--no-ff")
        self.pulls[0]["body"] = "Human explanation\n\nGenerated by Claude Code"
        result = self.integrate()
        self.assertEqual(result["tag"], TAG)
        self.assertEqual(len(self.patches), 1)
        self.assertEqual(self.tags_at_patch, [""])
        self.assertEqual(self.pulls[0]["body"],
                         "Human explanation")

    def test_validation_rejects_unmatched_merge_and_merge_queue(self) -> None:
        self.integrate()
        merge = self.github_merge("--no-ff")
        self.integrate()
        with mock.patch.object(integration, "github_repository", return_value="acme/web"), \
                mock.patch.object(integration, "run_command", side_effect=self.gh):
            self.pulls[0]["merge_commit_sha"] = self.main
            with self.assertRaisesRegex(ArchiveError, "not merged at the tagged landing"):
                integration.validate_member_integrated(self.root, "web", ARCHIVE, self.member_tip)
            self.pulls[0]["merge_commit_sha"] = merge
            self.merge_queue = True
            with self.assertRaisesRegex(ArchiveError, "merge queue"):
                integration.validate_member_integrated(self.root, "web", ARCHIVE, self.member_tip)

    def test_squash_merge_publishes_source_before_landing_and_is_idempotent(self) -> None:
        self.integrate()
        merge = self.github_merge("--squash")
        source = "milestone-source/demo-001-demo"
        pushed = []
        publish = integration._push_member_ref

        def record_push(checkout, project, local, ref, sha, lease):
            if ref.startswith("refs/tags/"):
                pushed.append(ref)
            return publish(checkout, project, local, ref, sha, lease)

        with mock.patch.object(integration, "_push_member_ref", side_effect=record_push):
            result = self.integrate()
        self.assertEqual(pushed, [f"refs/tags/{source}", f"refs/tags/{TAG}"])
        self.assertEqual(result["merge"], merge)
        for name, target in ((source, self.member_tip), (TAG, merge)):
            ref = f"refs/tags/{name}"
            self.assertEqual(git(self.remote, "cat-file", "-t", ref), "tag")
            self.assertEqual(self.remote_ref(ref + "^{}"), target)
            self.assertTrue(members.authorized(self.member, "demo", "push", ref, self.remote_ref(ref)))
        message = git(self.remote, "for-each-ref", "--format=%(contents)", f"refs/tags/{TAG}")
        self.assertIn(f"Source-Tag: {source}", message)
        self.assertIn(f"Reviewed-HEAD: {self.member_tip}", message)
        self.assertIn(f"Pull-Request: {URL}", message)
        before = git(self.remote, "show-ref", "--tags")
        self.assertEqual(self.integrate(), result)
        with mock.patch.object(integration, "github_repository", return_value="acme/web"), \
                mock.patch.object(integration, "run_command", side_effect=self.gh):
            for refresh in (True, False):
                self.assertEqual(integration.validate_member_integrated(
                    self.root, "web", ARCHIVE, self.member_tip, refresh=refresh), result)
        self.assertEqual(git(self.remote, "show-ref", "--tags"), before)
        self.assertEqual(len(self.posts), 1)

    def multi_commit_pull(self, *, squash: bool) -> str:
        bound = "feature/demo-milestone-001"
        git(self.member, "checkout", "-q", bound)
        (self.member / "second.py").write_text("reviewed = True\n", encoding="utf-8")
        git(self.member, "add", "second.py")
        git(self.member, "commit", "-q", "-m", "second member landing")
        self.member_tip = git(self.member, "rev-parse", "HEAD")
        git(self.member, "checkout", "-q", "main")
        publication_fixture(self.root, ARCHIVE, "web", self.member_tip, "https://github.com/acme/web.git")
        originals = git(self.member, "rev-list", "--reverse", f"{self.main}..{self.member_tip}").split()
        self.assertEqual(len(originals), 2)
        self.integrate()
        target = self.root.parent / "target"
        git(self.root.parent, "clone", "-q", str(self.remote), str(target))
        (target / "team.txt").write_text("concurrent work\n", encoding="utf-8")
        git(target, "add", "team.txt")
        git(target, "commit", "-q", "-m", "concurrent target work")
        git(target, "push", "-q", "origin", "main")
        if squash:
            return self.github_merge("--squash")
        git(target, "cherry-pick", *originals)
        git(target, "push", "-q", "origin", "main")
        landing = git(target, "rev-parse", "HEAD")
        self.pulls[0].update(state="closed", merged_at="2026-09-28T00:00:00Z", merge_commit_sha=landing)
        return landing

    def test_multi_commit_squash_is_still_accepted(self) -> None:
        landing = self.multi_commit_pull(squash=True)
        self.assertEqual(self.integrate()["merge"], landing)
        self.assertEqual(self.remote_ref("refs/tags/milestone-source/demo-001-demo^{}"), self.member_tip)

    def test_proven_multi_commit_rebase_cannot_publish_tags(self) -> None:
        landing = self.multi_commit_pull(squash=False)
        git(self.member, "fetch", "-q", "origin")
        self.assertIsNone(integration._common.rebase_landing_problem(self.member, self.member_tip, landing))
        self.assertIsNone(integration._common.squash_landing_problem(self.member, self.member_tip, landing))
        with self.assertRaisesRegex(ArchiveError, "cannot use a multi-commit rebase"):
            self.integrate()
        for name in (TAG, "milestone-source/demo-001-demo"):
            ref = f"refs/tags/{name}"
            self.assertEqual(self.remote_ref(ref), "")
            self.assertEqual(git(self.member, "rev-parse", "--verify", "--quiet", ref, check=False), "")

    def test_squash_merge_with_altered_tree_publishes_neither_tag(self) -> None:
        self.integrate()
        self.github_merge("--squash")
        clone = self.root.parent / "github"
        (clone / "foreign.txt").write_text("unreviewed\n", encoding="utf-8")
        git(clone, "add", "foreign.txt")
        git(clone, "commit", "-q", "--amend", "--no-edit")
        forged = git(clone, "rev-parse", "HEAD")
        git(clone, "push", "-q", "--force", "origin", "HEAD:refs/heads/main")
        self.pulls[0]["merge_commit_sha"] = forged
        with self.assertRaisesRegex(ArchiveError, "tree differs"):
            self.integrate()
        for name in (TAG, "milestone-source/demo-001-demo"):
            ref = f"refs/tags/{name}"
            self.assertEqual(self.remote_ref(ref), "")
            self.assertEqual(git(self.member, "rev-parse", "--verify", "--quiet", ref, check=False), "")

    def test_squash_merge_with_wrong_source_is_still_rejected(self) -> None:
        self.integrate()
        self.github_merge("--squash")
        self.pulls[0]["head"]["sha"] = self.main
        with self.assertRaisesRegex(ArchiveError, "head is not the ship commit"):
            self.integrate()
        self.assertEqual(self.remote_ref(f"refs/tags/{TAG}"), "")
        self.assertEqual(self.remote_ref("refs/tags/milestone-source/demo-001-demo"), "")

    def test_squash_uses_landing_parent_when_target_advances(self) -> None:
        self.integrate()
        target = self.root.parent / "target"
        git(self.root.parent, "clone", "-q", str(self.remote), str(target))
        git(target, "config", "user.name", "t")
        git(target, "config", "user.email", "t@t")
        (target / "team.txt").write_text("concurrent target work\n", encoding="utf-8")
        git(target, "add", "team.txt")
        git(target, "commit", "-q", "-m", "concurrent target work")
        git(target, "push", "-q", "origin", "main")
        landing_parent = git(target, "rev-parse", "HEAD")
        merge = self.github_merge("--squash")
        self.assertEqual(git(self.remote, "rev-parse", merge + "^"), landing_parent)
        clone = self.root.parent / "github"
        (clone / "later.txt").write_text("later target work\n", encoding="utf-8")
        git(clone, "add", "later.txt")
        git(clone, "commit", "-q", "-m", "later target work")
        git(clone, "push", "-q", "origin", "main")
        advanced = git(clone, "rev-parse", "HEAD")
        self.pulls[0]["base"]["sha"] = advanced
        result = self.integrate()
        self.assertEqual(result["merge"], merge)
        self.assertEqual(self.remote_ref("refs/heads/main"), advanced)
        self.assertEqual(self.integrate(), result)

    def test_squash_still_requires_a_manual_human_merge_action(self) -> None:
        self.integrate()
        self.github_merge("--squash")
        for attribute, invalid, reason in (("merge_queue", True, "merge queue"),
                                           ("auto_merge", True, "auto-merge"),
                                           ("merge_actor", "Bot", "human GitHub user"),
                                           ("merge_event", False, "GitHub merge action")):
            previous = getattr(self, attribute)
            setattr(self, attribute, invalid)
            with self.subTest(reason=reason), self.assertRaisesRegex(ArchiveError, reason):
                self.integrate()
            setattr(self, attribute, previous)
            self.assertEqual(self.remote_ref(f"refs/tags/{TAG}"), "")
            self.assertEqual(self.remote_ref("refs/tags/milestone-source/demo-001-demo"), "")

    def test_closed_pull_request_blocks(self) -> None:
        self.integrate()
        self.pulls[0]["state"] = "closed"
        with self.assertRaisesRegex(ArchiveError, "closed without merging"):
            self.integrate()


class MasterMemberPullRequestTests(unittest.TestCase):
    """Pull-request member close on a member whose recorded default branch is master."""
    member_default = "master"
    fixture = MemberPullRequestTests.fixture
    setUp = MemberPullRequestTests.setUp
    remote_ref = MemberPullRequestTests.remote_ref
    gh = MemberPullRequestTests.gh
    integrate = MemberPullRequestTests.integrate
    github_merge = MemberPullRequestTests.github_merge

    def test_pull_request_targets_the_recorded_default_branch_and_is_validated_there(self) -> None:
        waiting = self.integrate()
        self.assertEqual((waiting["status"], waiting["pull_request"]), ("awaiting-merge", URL))
        self.assertEqual(self.integrate()["status"], "awaiting-merge")
        self.assertEqual(len(self.posts), 1)
        self.assertIn("base=master", self.posts[0])
        self.assertIn('title=' + EDITORIAL_TITLE, self.posts[0])
        self.assertEqual(self.remote_ref("refs/heads/master"), self.main)
        # A body repair checks the pull request against the same base.
        self.pulls[0]["body"] += "\n\nGenerated by Claude Code"
        self.assertEqual(self.integrate()["status"], "awaiting-merge")
        self.assertEqual(len(self.patches), 1)

        merge = self.github_merge("--no-ff")
        self.pulls[0]["body"] = "Human explanation\n\nGenerated by Claude Code"
        result = self.integrate()
        self.assertEqual(len(self.patches), 2)
        self.assertEqual(result["merge"], merge)
        self.assertEqual(self.remote_ref("refs/heads/master"), merge)
        self.assertEqual(self.remote_ref(f"refs/tags/{TAG}^{{commit}}"), merge)
        with mock.patch.object(integration, "github_repository", return_value="acme/web"), \
                mock.patch.object(integration, "run_command", side_effect=self.gh):
            self.assertEqual(integration.validate_member_integrated(self.root, "web", ARCHIVE, self.member_tip),
                             result)

    def test_merge_that_is_not_on_the_recorded_default_branch_is_refused(self) -> None:
        self.integrate()
        # The human merged the bound branch into another branch, then repointed the pull request.
        self.member_default = "release"
        git(self.remote, "branch", "release", "master")
        merge = self.github_merge("--no-ff")
        self.assertEqual(self.remote_ref("refs/heads/master"), self.main)
        with self.assertRaisesRegex(ArchiveError, "pull-request merge is not on origin/master first-parent history"):
            self.integrate()
        self.assertEqual(self.remote_ref(f"refs/tags/{TAG}"), "")
        self.assertNotEqual(merge, self.main)


if __name__ == "__main__":
    unittest.main()
