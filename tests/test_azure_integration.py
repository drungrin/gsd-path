import copy
import json
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest import mock

from scripts import integration
from scripts.archive_milestone import ArchiveError
from tests import test_member_integration as member_tests
from tests import test_archive_milestone as archive_tests
from tests import test_pipeline_git as pipeline_tests
from tests.test_azure_repos import SSH, HTTPS, REPOSITORY
from tests._editorial_fixtures import publication_fixture

git = member_tests.git
ARCHIVE = member_tests.ARCHIVE
TAG = member_tests.TAG


class AzureIntegrationTests(unittest.TestCase):
    fixture = member_tests.MemberIntegrationTests.fixture
    remote_ref = member_tests.MemberIntegrationTests.remote_ref

    def setUp(self):
        member_tests.MemberIntegrationTests.setUp(self)
        git(self.member, 'remote', 'set-url', 'origin', SSH)
        git(self.member, 'config', 'url.' + str(self.remote) + '.insteadOf', SSH)
        path = self.root / '.project/MEMBERS.md'
        path.write_text(path.read_text().replace('https://github.com/acme/web.git', SSH))
        self.calls, self.pulls = [], []
        self.bound = 'feature/demo-milestone-001'
        publication_fixture(self.root, ARCHIVE, 'web', self.member_tip, SSH)

    def az(self, *args):
        self.calls.append(args)
        self.assertEqual(args[0], 'az')
        def option(flag):
            return args[args.index(flag) + 1]
        self.assertEqual(option('--organization'), 'https://dev.azure.com/example-org')
        self.assertEqual(option('--detect'), 'false')
        self.assertEqual(option('--output'), 'json')
        if args[1:4] == ('repos', 'ref', 'list'):
            payload = [{'name': 'refs/heads/' + self.bound, 'objectId': self.member_tip}]
        elif args[1:4] == ('repos', 'pr', 'list'):
            self.assertEqual(option('--project'), 'Example Project')
            self.assertEqual(option('--repository'), REPOSITORY.name)
            self.assertEqual(option('--target-branch'), getattr(self, 'member_default', 'main'))
            payload = self.pulls
        elif args[1:4] == ('repos', 'pr', 'create'):
            self.assertEqual(self.remote_ref('refs/heads/' + self.bound), self.member_tip)
            self.pulls.append({'pullRequestId': 7, 'status': 'active',
                               'title': option('--title'),
                               'sourceRefName': 'refs/heads/' + option('--source-branch'),
                               'targetRefName': 'refs/heads/' + option('--target-branch'),
                               'description': option('--description'),
                               'repository': {'name': REPOSITORY.name, 'project': {'name': REPOSITORY.project},
                                              'remoteUrl': HTTPS},
                               'lastMergeSourceCommit': {'commitId': self.member_tip},
                               'lastMergeCommit': None, 'autoCompleteSetBy': None,
                               'completionOptions': {'squashMerge': False}})
            payload = self.pulls[-1]
        elif args[1:4] == ('repos', 'pr', 'show'):
            payload = self.pulls[0]
        elif args[1:4] == ('repos', 'pr', 'update'):
            self.pulls[0]['description'] = option('--description')
            if '--title' in args:
                self.pulls[0]['title'] = option('--title')
            payload = self.pulls[0]
        else:
            self.fail(f'unexpected az command: {args}')
        return CompletedProcess(args, 0, json.dumps(copy.deepcopy(payload)), '')

    def integrate(self):
        with mock.patch.object(integration, 'run_command', side_effect=self.az):
            return integration.integrate_member_pull_request(self.root, 'web', ARCHIVE, self.member_tip)

    def complete(self, squash=False):
        clone = self.root.parent / 'azure-merge'
        default = getattr(self, 'member_default', 'main')
        git(self.root.parent, 'clone', '-q', str(self.remote), str(clone))
        git(clone, 'fetch', '-q', 'origin', self.bound)
        target = git(clone, 'rev-parse', 'HEAD')
        if squash:
            git(clone, 'merge', '--squash', 'FETCH_HEAD')
            git(clone, 'commit', '-q', '-m', 'squashed')
        else:
            git(clone, 'merge', '-q', '--no-ff', '-m', 'User-completed Azure PR', 'FETCH_HEAD')
        merge = git(clone, 'rev-parse', 'HEAD')
        git(clone, 'push', '-q', 'origin', default)
        self.pulls[0].update(status='completed', closedDate='2026-10-15T12:00:00Z',
                             mergeStatus='succeeded', lastMergeCommit={'commitId': merge},
                             lastMergeTargetCommit={'commitId': target},
                             closedBy={'id': 'user', 'uniqueName': 'owner@example.com'},
                             completionOptions={'mergeStrategy': 'squash' if squash else 'noFastForward',
                                                'triggeredByAutoComplete': False})
        return merge

    def test_pr_is_opened_once_and_waits_without_moving_default_or_publishing_tag(self):
        first = self.integrate()
        second = self.integrate()
        self.assertEqual(first, second)
        self.assertEqual(first['status'], 'awaiting-merge')
        self.assertEqual(first['pull_request'], HTTPS + '/pullrequest/7')
        self.assertEqual(len(self.pulls), 1)
        self.assertEqual(self.remote_ref('refs/heads/main'), self.main)
        self.assertEqual(self.remote_ref('refs/tags/' + TAG), '')
        self.assertNotIn('prepared with', self.pulls[0]['description'].lower())

    def test_completed_pr_is_verified_tagged_and_resumable_even_after_source_deletion(self):
        self.integrate()
        merge = self.complete()
        git(self.remote, 'update-ref', '-d', 'refs/heads/feature/demo-milestone-001')
        result = self.integrate()
        self.assertEqual(result['merge'], merge)
        self.assertEqual(self.remote_ref(f'refs/tags/{TAG}^{{commit}}'), merge)
        self.assertEqual(self.integrate()['merge'], merge)
        with mock.patch.object(integration, 'run_command', side_effect=self.az):
            self.assertEqual(integration.validate_member_integrated(
                self.root, 'web', ARCHIVE, self.member_tip)['merge'], merge)

    def test_auto_complete_is_rejected_and_human_squash_preserves_source(self):
        self.integrate()
        self.pulls[0]['autoCompleteSetBy'] = {'id': 'user'}
        with self.assertRaisesRegex(ArchiveError, 'auto-complete'):
            self.integrate()
        self.pulls[0]['autoCompleteSetBy'] = None
        merge = self.complete(squash=True)
        result = self.integrate()
        self.assertEqual(result['merge'], merge)
        self.assertEqual(self.remote_ref('refs/tags/' + TAG + '^{}'), merge)
        source = integration._common.source_tag_name(TAG)
        self.assertEqual(self.remote_ref('refs/tags/' + source + '^{}'), self.member_tip)
        self.assertEqual(self.integrate()['merge'], merge)

    def test_azure_direct_integration_needs_no_az_or_gh(self):
        with mock.patch.object(integration, 'run_command', side_effect=AssertionError('forge CLI used')):
            result = integration.integrate_member(self.root, 'web', ARCHIVE, self.member_tip)
        self.assertEqual(result['merge'], self.remote_ref('refs/heads/main'))

    def test_reused_pr_description_is_repaired_before_publication(self):
        self.integrate()
        human = self.pulls[0]['description'] + '\nHuman explanation\n'
        self.pulls[0]['description'] = human + '\n---\nPR prepared with GSD Path.'
        self.integrate()
        self.assertEqual(self.pulls[0]['description'], human.rstrip())
        self.assertTrue(any(args[1:4] == ('repos', 'pr', 'update') for args in self.calls))


class AzureOtherDefaultIntegrationTests(AzureIntegrationTests):
    member_default = 'master'

    def test_pr_is_opened_once_and_waits_without_moving_default_or_publishing_tag(self):
        first = self.integrate()
        self.assertEqual(first['status'], 'awaiting-merge')
        self.assertEqual(self.remote_ref('refs/heads/master'), self.main)

    def test_azure_direct_integration_needs_no_az_or_gh(self):
        with mock.patch.object(integration, 'run_command', side_effect=AssertionError('forge CLI used')):
            result = integration.integrate_member(self.root, 'web', ARCHIVE, self.member_tip)
        self.assertEqual(result['merge'], self.remote_ref('refs/heads/master'))


class AzureCoordinatorIntegrationTests(unittest.TestCase):
    az = AzureIntegrationTests.az
    complete = AzureIntegrationTests.complete
    remote_ref = AzureIntegrationTests.remote_ref

    def test_coordinator_pr_is_created_completed_tagged_and_revalidated_through_az(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.root = root / 'coordinator'
        self.root.mkdir()
        self.member = self.root
        self.remote = root / 'origin.git'
        harness = archive_tests.ArchiveMilestoneTests()
        harness.make_publishable_bound_repo(self.root, self.remote)
        git(self.root, 'remote', 'set-url', 'origin', SSH)
        git(self.root, 'config', 'url.' + str(self.remote) + '.insteadOf', SSH)
        harness.enable_pull_request_integration(self.root)
        archive, self.member_tip = harness.ship_canonical_bound(self.root)
        self.calls, self.pulls, self.bound = [], [], 'feature/milestone-001'
        with mock.patch.object(integration, 'run_command', side_effect=self.az):
            waiting = integration.integrate(self.root, 'demo')
            self.assertEqual(waiting['status'], 'awaiting-merge')
            merge = self.complete()
            result = integration.integrate(self.root, 'demo')
            self.assertEqual(result['landing'], merge)
            self.assertEqual(result['pull_request'], HTTPS + '/pullrequest/7')
            self.assertEqual(self.remote_ref(f'refs/tags/milestone/{archive}^{{commit}}'), merge)
            self.assertEqual(integration.validate_integrated(self.root, 'demo')['landing'], merge)

    def test_next_milestone_accepts_azure_pull_request_tag_proof(self):
        with tempfile.TemporaryDirectory() as temporary:
            main, primary, ship, landing = pipeline_tests.ship_then_forge_deletes_branch(temporary, 'pull-request')
            pipeline_tests.publish_milestone_tag(main, 'milestone 001-first\n\nMode: pull-request\n'
                f'Pull-Request: {HTTPS}/pullrequest/7\nShip: {ship}\nLanding: {landing}', landing)
            pipeline_tests.run_git(primary, 'fetch', 'origin', '--tags')
            from scripts import pipeline_git
            result = pipeline_git._require_tag_integration_proof(primary, ship, landing, 'feature/milestone-001')
            self.assertEqual(result, landing)


if __name__ == '__main__':
    unittest.main()
