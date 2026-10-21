"""Squash delivery retains the original evidence graph across retirement."""
import copy
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest import mock

from scripts import integration, pipeline_git, pipeline_state
from scripts.archive_milestone import ArchiveError
from tests import test_archive_milestone as archives, test_azure_integration as azure
from tests import test_member_pull_request as github
from tests.test_azure_integration import git
from tests.test_azure_repos import SSH


class SquashCoordinatorTests(unittest.TestCase):
    az = azure.AzureIntegrationTests.az
    complete = azure.AzureIntegrationTests.complete
    remote_ref = azure.AzureIntegrationTests.remote_ref
    mode = "pull-request"
    provider = "azure"
    merge_checkout = "azure-merge"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.root = self.directory / "coordinator"
        self.root.mkdir()
        self.member = self.root
        self.remote = self.directory / "origin.git"
        harness = archives.ArchiveMilestoneTests()
        harness.make_publishable_bound_repo(self.root, self.remote)
        # Keep task + ship off main: a one-commit squash also proves a rebase,
        # whose existing Coordinator precedence intentionally needs no source tag.
        baseline = git(self.root, "rev-parse", "HEAD^")
        git(self.remote, "update-ref", "refs/heads/main", baseline)
        if self.mode == "external-landing":
            harness.enable_external_landing_integration(self.root)
        else:
            harness.enable_pull_request_integration(self.root)
        self.configure_remote(self.root)
        self.archive, self.member_tip = harness.ship_canonical_bound(self.root)
        self.calls, self.pulls, self.bound = [], [], "feature/milestone-001"
        command = self.az if self.provider == "azure" else self.gh
        self.patch = mock.patch.object(integration, "run_command", side_effect=command)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def configure_remote(self, checkout):
        git(checkout, "remote", "set-url", "origin", SSH)
        git(checkout, "config", "url." + str(self.remote) + ".insteadOf", SSH)

    def integrate(self):
        return integration.integrate(self.root, "demo")

    def open_pull(self):
        first = self.integrate()
        if self.mode == "external-landing":
            self.assertEqual(self.pulls, [])
            self.assertEqual(first["merge_methods"], ["noFastForward", "squash"])
            # External mode waits for the user's PR instead of creating it.
            repository = integration.pull_request_repository(self.root)
            document = integration.publication(self.root, '.project/archive/' + self.archive, 'coordinator')
            integration.create_provider_pull_request(repository, self.bound, "main", document['title'],
                                                     integration.pr_editorial.render(document), self.member_tip)
        self.assertEqual(self.integrate()["status"], "awaiting-merge")

    def completed(self):
        self.open_pull()
        landing = self.complete(squash=True)
        result = self.integrate()
        self.assertEqual(result["landing"], landing)
        self.assertEqual(result["mode"], self.mode)
        for name, commit in ((f"milestone-source/{self.archive}", self.member_tip),
                             (f"milestone/{self.archive}", landing)):
            ref = "refs/tags/" + name
            self.assertEqual(git(self.remote, "cat-file", "-t", ref), "tag")
            self.assertEqual(self.remote_ref(ref + "^{}"), commit)
        return landing

    def test_squash_source_survives_branch_deletion_clone_and_next_milestone(self):
        landing = self.completed()
        source_ref = f"refs/tags/milestone-source/{self.archive}"
        self.assertEqual(self.remote_ref(source_ref + "^{}"), self.member_tip)
        git(self.remote, "update-ref", "-d", "refs/heads/" + self.bound)
        git(self.remote, "reflog", "expire", "--expire=now", "--all")
        git(self.remote, "gc", "--prune=now")
        self.assertEqual(self.integrate()["landing"], landing)
        clone = self.directory / "fresh"
        git(self.directory, "clone", "-q", "--no-local", str(self.remote), str(clone))
        self.configure_remote(clone)
        integration.refresh_origin(clone)
        self.assertEqual(integration.validate_integrated(clone, "demo", historical=True)["commit"], self.member_tip)
        before = {p.relative_to(clone): p.read_bytes() for p in (clone / ".git").rglob("*") if p.is_file()}
        self.assertEqual(pipeline_state.status_state(clone)["completion"]["status"], "verified")
        after = {p.relative_to(clone): p.read_bytes() for p in (clone / ".git").rglob("*") if p.is_file()}
        self.assertEqual(after, before)
        result = pipeline_git.bind_next_milestone_branch(
            self.root, "feature/milestone-002", self.bound, self.member_tip,
            "origin/main", landing, landing, allow_remote_absent=True)
        self.assertEqual(result["branch"], "feature/milestone-002")
        self.assertEqual(pipeline_state.route_state(self.root)["route"]["action"], "resume-next-handoff")
        self.assertEqual(git(self.root, "branch", "--list", self.bound), "")
        self.assertEqual(pipeline_git._require_tag_integration_proof(
            self.root, self.member_tip, landing, self.bound), landing)
        # Original archived task and review commits remain reachable after GC.
        git(self.root, "reflog", "expire", "--expire=now", "--all")
        git(self.root, "gc", "--prune=now")
        self.assertEqual(git(self.root, "rev-parse", source_ref + "^{}"), self.member_tip)
        final = git(self.root, "show", f"{source_ref}:.project/archive/{self.archive}/review/FINAL.md")
        self.assertIn("Overall verdict: pass", final)
        self.assertEqual(integration.validate_integrated(clone, "demo", historical=True)["commit"], self.member_tip)

    def test_changed_squash_tree_is_rejected_before_publishing_either_tag(self):
        self.open_pull()
        self.complete(squash=True)
        clone = self.directory / self.merge_checkout
        (clone / "foreign.txt").write_text("unreviewed\n", encoding="utf-8")
        git(clone, "add", "foreign.txt")
        git(clone, "commit", "-q", "--amend", "--no-edit")
        forged = git(clone, "rev-parse", "HEAD")
        git(self.remote, "fetch", "-q", str(clone), forged)
        git(self.remote, "update-ref", "refs/heads/main", forged)
        if self.provider == "azure":
            self.pulls[0]["lastMergeCommit"] = {"commitId": forged}
        else:
            self.pulls[0]["merge_commit_sha"] = forged
        with self.assertRaisesRegex(ArchiveError, "tree differs"):
            self.integrate()
        for name in (f"milestone/{self.archive}", f"milestone-source/{self.archive}"):
            ref = "refs/tags/" + name
            self.assertEqual(self.remote_ref(ref), "")
            self.assertEqual(git(self.root, "rev-parse", "--verify", "--quiet", ref, check=False), "")

    def invalid_pull_changes(self):
        return (
            (lambda p: p.update(lastMergeTargetCommit={"commitId": self.member_tip}), "parent differs"),
            (lambda p: p.update(lastMergeSourceCommit={"commitId": "f" * 40}), "ship commit"),
            (lambda p: p["completionOptions"].update(bypassPolicy=True), "bypass"),
            (lambda p: p["completionOptions"].update(mergeStrategy="rebase"), "no-fast-forward"),
        )

    def test_wrong_source_or_provider_provenance_publishes_neither_tag(self):
        self.open_pull()
        self.complete(squash=True)
        valid = copy.deepcopy(self.pulls[0])
        for change, reason in self.invalid_pull_changes():
            self.pulls[0] = copy.deepcopy(valid)
            change(self.pulls[0])
            with self.subTest(reason=reason), self.assertRaisesRegex(ArchiveError, reason):
                self.integrate()
            self.assertEqual(self.remote_ref(f"refs/tags/milestone/{self.archive}"), "")
            self.assertEqual(self.remote_ref(f"refs/tags/milestone-source/{self.archive}"), "")

    def test_missing_or_changed_remote_source_tag_blocks_completion(self):
        self.completed()
        ref = f"refs/tags/milestone-source/{self.archive}"
        git(self.remote, "update-ref", "-d", ref)
        with self.assertRaisesRegex(ArchiveError, "source tag.*published"):
            integration.validate_integrated(self.root, "demo", refresh=False)
        landing_tag = self.remote_ref(f"refs/tags/milestone/{self.archive}")
        git(self.remote, "update-ref", ref, landing_tag)
        with self.assertRaisesRegex(ArchiveError, "source tag.*published"):
            integration.validate_integrated(self.root, "demo", refresh=False)

    def test_interrupted_tag_publication_reuses_retained_source(self):
        self.open_pull()
        landing = self.complete(squash=True)
        run = integration.run_git
        ref = f"refs/tags/milestone/{self.archive}"
        source_ref = f"refs/tags/milestone-source/{self.archive}"
        def fail_milestone(repo, *args):
            if args == ("push", "origin", f"{ref}:{ref}"):
                self.assertEqual(self.remote_ref(source_ref + "^{}"), self.member_tip)
                self.assertEqual(git(self.remote, "cat-file", "-t", source_ref), "tag")
                return CompletedProcess(args, 1, "", "publication interrupted")
            return run(repo, *args)
        with mock.patch.object(integration, "run_git", side_effect=fail_milestone):
            with self.assertRaisesRegex(ArchiveError, "publication interrupted"):
                self.integrate()
        self.assertEqual(self.remote_ref(ref), "")
        retained = self.remote_ref(source_ref)
        self.assertEqual(self.remote_ref(source_ref + "^{}"), self.member_tip)
        self.assertEqual(self.integrate()["landing"], landing)
        self.assertEqual(self.remote_ref(source_ref), retained)

    def test_malformed_historical_tag_is_reported_as_unverified(self):
        self.completed()
        name = f"milestone/{self.archive}"
        contents = git(self.root, "for-each-ref", "--format=%(contents)", "refs/tags/" + name)
        git(self.root, "tag", "-f", "-a", name, "-m", contents + "\nSource-Tag: duplicate")
        completion = pipeline_state.status_state(self.root)["completion"]
        self.assertEqual(completion["status"], "unverified")
        self.assertIn("repeats metadata", completion["reason"])


class SquashExternalLandingTests(SquashCoordinatorTests):
    mode = "external-landing"


class GithubSquashCoordinatorTests(SquashCoordinatorTests):
    """Run the same retention/retry/retirement contract through GitHub."""
    provider = "github"
    repository_name = "open-gsd/demo"
    merge_checkout = "github"
    gh = github.MemberPullRequestTests.gh
    github_merge = github.MemberPullRequestTests.github_merge

    def setUp(self):
        super().setUp()
        self.posts, self.patches, self.tags_at_patch = [], [], []
        self.merge_queue, self.auto_merge = False, False
        self.merge_actor, self.merge_event = "User", True

    def configure_remote(self, checkout):
        remote = f"https://github.com/{self.repository_name}.git"
        git(checkout, "remote", "set-url", "origin", remote)
        git(checkout, "config", "url." + str(self.remote) + ".insteadOf", remote)

    def complete(self, squash=False):
        return self.github_merge("--squash" if squash else "--no-ff")

    def invalid_pull_changes(self):
        return (
            (lambda p: p["head"].update(sha="f" * 40), "ship commit"),
            (lambda p: p["head"]["repo"].update(full_name="other/demo"), "repository is not origin"),
        )

    def test_unavailable_rebase_proof_does_not_publish_squash_tags(self):
        self.open_pull()
        harness = archives.ArchiveMilestoneTests()
        landing = harness.rebase_merge(self.root, self.member_tip, moved=True)
        self.pulls[0].update(state="closed", merged_at="2026-09-28T00:00:00Z", merge_commit_sha=landing)
        with mock.patch.object(integration._common, "_patch_ids", side_effect=ValueError("could not read commit patches")):
            with self.assertRaisesRegex(ArchiveError, "rebase proof is unavailable"):
                self.integrate()
        for name in (f"milestone/{self.archive}", f"milestone-source/{self.archive}"):
            ref = "refs/tags/" + name
            self.assertEqual(self.remote_ref(ref), "")
            self.assertEqual(git(self.root, "rev-parse", "--verify", "--quiet", ref, check=False), "")
        self.assertEqual(self.integrate()["landing"], landing)
        self.assertEqual(integration.validate_integrated(self.root, "demo", historical=True)["landing"], landing)
        self.assertEqual(self.remote_ref(f"refs/tags/milestone-source/{self.archive}"), "")

    def test_existing_single_commit_rebase_proof_keeps_precedence(self):
        # One original commit is indistinguishable from a one-commit rebase.
        reviewed = git(self.root, "rev-parse", self.member_tip + "^")
        git(self.remote, "update-ref", "refs/heads/main", reviewed)
        self.open_pull()
        landing = self.complete(squash=True)
        self.assertEqual(self.integrate()["landing"], landing)
        self.assertIsNone(integration._common.rebase_landing_problem(self.root, self.member_tip, landing))
        self.assertEqual(self.remote_ref(f"refs/tags/milestone-source/{self.archive}"), "")
        contents = git(self.remote, "for-each-ref", "--format=%(contents)",
                       f"refs/tags/milestone/{self.archive}")
        self.assertNotIn("Source-Tag:", contents)
        self.assertEqual(integration.validate_integrated(self.root, "demo", historical=True)["landing"], landing)


class SquashTreeProofTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.test")
        (self.repo / "value.txt").write_text("first\n" + "middle\n" * 20 + "last\n", encoding="utf-8")
        self.commit("base")
        git(self.repo, "checkout", "-q", "-b", "feature/change")
        path = self.repo / "value.txt"
        path.write_text(path.read_text().replace("first", "changed first"), encoding="utf-8")
        self.commit("source")
        self.source = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "checkout", "-q", "main")

    def commit(self, subject):
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", subject)

    def test_concurrent_changes_to_same_file_are_preserved_without_repository_writes(self):
        path = self.repo / "value.txt"
        path.write_text(path.read_text().replace("last", "changed last"), encoding="utf-8")
        self.commit("concurrent target")
        target = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "merge", "--squash", self.source)
        self.commit("squash")
        landing = git(self.repo, "rev-parse", "HEAD")
        before = {p.relative_to(self.repo): p.read_bytes() for p in (self.repo / ".git/objects").rglob("*") if p.is_file()}
        self.assertIsNone(integration._common.squash_landing_problem(self.repo, self.source, landing, target))
        after = {p.relative_to(self.repo): p.read_bytes() for p in (self.repo / ".git/objects").rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_conflict_resolution_is_not_accepted_from_provider_metadata_alone(self):
        path = self.repo / "value.txt"
        path.write_text(path.read_text().replace("first", "other first"), encoding="utf-8")
        self.commit("conflicting target")
        git(self.repo, "merge", "--squash", self.source, check=False)
        path.write_text("hand resolution\n", encoding="utf-8")
        self.commit("resolved squash")
        self.assertIn("cannot be reproduced cleanly", integration._common.squash_landing_problem(
            self.repo, self.source, git(self.repo, "rev-parse", "HEAD")))


if __name__ == "__main__":
    unittest.main()
