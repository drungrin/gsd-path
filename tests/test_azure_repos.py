import copy
import json
import unittest
from subprocess import CompletedProcess
from unittest import mock

from scripts import azure_repos, integration, members
from scripts.archive_milestone import ArchiveError
from tests import test_members as member_tests


SSH = 'git@ssh.dev.azure.com:v3/example-org/Example%20Project/infra-repo'
HTTPS = 'https://dev.azure.com/example-org/Example%20Project/_git/infra-repo'
REPOSITORY = azure_repos.parse_remote(SSH)
HEAD = 'a' * 40
MERGE = 'b' * 40


def azure_pull(number=7, branch='feature/milestone-001', base='main', head=HEAD):
    return {'pullRequestId': number, 'status': 'active', 'sourceRefName': f'refs/heads/{branch}',
            'targetRefName': f'refs/heads/{base}', 'description': 'review',
            'repository': {'id': 'repo-id', 'name': REPOSITORY.name,
                           'project': {'name': REPOSITORY.project}, 'remoteUrl': HTTPS},
            'lastMergeSourceCommit': {'commitId': head}, 'lastMergeCommit': None,
            'autoCompleteSetBy': None, 'completionOptions': {'squashMerge': False}}


class AzureRepositoryTests(unittest.TestCase):
    def test_ssh_https_and_username_urls_have_the_same_decoded_identity(self):
        for remote in (SSH, HTTPS, HTTPS.replace('https://', 'https://example-org@'),
                       SSH.replace('git@ssh.dev.azure.com:', 'ssh://git@ssh.dev.azure.com/')):
            with self.subTest(remote=remote):
                parsed = azure_repos.parse_remote(remote)
                self.assertEqual(parsed.identity, REPOSITORY.identity)
                self.assertEqual(parsed.project, 'Example Project')
                self.assertEqual(parsed.web_url, HTTPS)
                self.assertEqual(members._remote_identity(remote), REPOSITORY.identity)

    def test_rejects_other_hosts_credentials_and_ambiguous_encoded_paths(self):
        for remote in (SSH.replace('ssh.dev.azure.com', 'ssh.dev.azure.com.evil'),
                       HTTPS.replace('dev.azure.com', 'dev.azure.com.evil'),
                       HTTPS.replace('https://', 'https://user:secret@'),
                       SSH.replace('Example%20Project', '%2e%2e'),
                       SSH.replace('Example%20Project', 'project%2fother'),
                       SSH.replace('Example%20Project', 'project%252fother'),
                       SSH + '?token=secret', SSH + '/extra'):
            with self.subTest(remote=remote):
                self.assertIsNone(azure_repos.parse_remote(remote))

    def test_pull_url_is_an_https_azure_repo_and_positive_number(self):
        self.assertTrue(azure_repos.valid_pull_request_url(HTTPS + '/pullrequest/7'))
        for value in (HTTPS + '/pullrequest/0', HTTPS + '/pullrequest/-1',
                      SSH + '/pullrequest/7', HTTPS + '/pullrequest/7?fake=1'):
            self.assertFalse(azure_repos.valid_pull_request_url(value))


class AzureMemberRegistrationTests(unittest.TestCase):
    setUp = member_tests.MemberTests.setUp
    tearDown = member_tests.MemberTests.tearDown
    make_member = member_tests.MemberTests.make_member
    run_members = member_tests.MemberTests.run_members
    add = member_tests.MemberTests.add

    def test_existing_azure_member_is_added_and_validated_with_non_main_default(self):
        member = self.make_member('infra', remote=SSH, default='master')
        result = self.add('infra', member, '--integration', 'pull-request')
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)['members'][0]
        self.assertEqual(row['remote'], SSH)
        self.assertEqual(row['default_branch'], 'master')
        validated = self.run_members('validate')
        self.assertEqual(validated.returncode, 0, validated.stderr)

    def test_azure_clone_duplicates_are_detected_across_url_forms(self):
        first = self.make_member('infra', remote=SSH)
        self.assertEqual(self.add('infra', first).returncode, 0)
        other = self.make_member('clone', remote=HTTPS)
        result = self.add('clone', other)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('member already recorded', result.stderr)


class AzurePullMetadataTests(unittest.TestCase):
    def setUp(self):
        self.pull = azure_pull()
        self.commands = []

    def call(self, *args):
        self.commands.append(args)
        if args[:3] == ('repos', 'ref', 'list'):
            return [{'name': self.pull['sourceRefName'], 'objectId': HEAD},
                    {'name': self.pull['sourceRefName'] + '-other', 'objectId': 'f' * 40}]
        if args[:3] == ('repos', 'pr', 'list'):
            return [copy.deepcopy(self.pull)]
        if args[:3] == ('repos', 'pr', 'show'):
            return copy.deepcopy(self.pull)
        self.fail(f'unexpected az request: {args}')

    def complete(self):
        self.pull.update(status='completed', closedDate='2026-10-15T12:00:00Z', mergeStatus='succeeded',
                         lastMergeCommit={'commitId': MERGE},
                         closedBy={'id': 'user-id', 'uniqueName': 'owner@example.com', 'isContainer': False},
                         completionOptions={'mergeStrategy': 'noFastForward', 'triggeredByAutoComplete': False})

    def test_discovery_reads_exact_live_ref_and_uses_explicit_azure_scope(self):
        self.pull['lastMergeSourceCommit'] = None
        result = azure_repos.find(REPOSITORY, 'feature/milestone-001', 'main', self.call)
        self.assertEqual(result['head']['sha'], HEAD)
        self.assertEqual(result['html_url'], HTTPS + '/pullrequest/7')
        first = self.commands[0]
        self.assertIn('Example Project', first)
        self.assertIn('https://dev.azure.com/example-org', first)
        self.assertIn('--detect', first)
        self.assertIn('--skip', first)

    def test_foreign_repo_fork_and_changed_head_block_integration(self):
        mutations = (
            lambda pull: pull['repository'].update(remoteUrl=HTTPS.replace('example-org', 'other')),
            lambda pull: pull['repository']['project'].update(name='other'),
            lambda pull: pull.update(forkSource={'repository': {'name': 'other'}}),
        )
        for change in mutations:
            self.pull = azure_pull()
            change(self.pull)
            with self.subTest(pull=self.pull), self.assertRaises(azure_repos.AzureError):
                azure_repos.find(REPOSITORY, 'feature/milestone-001', 'main', self.call)
        self.pull = azure_pull()
        with mock.patch.object(integration, 'azure_cli_json', side_effect=self.call):
            with self.assertRaisesRegex(ArchiveError, 'ship commit'):
                integration.find_pull_request(REPOSITORY, 'feature/milestone-001', 'c' * 40)

    def test_completion_requires_actual_user_merge_without_automation_or_policy_bypass(self):
        self.complete()
        azure_repos.require_merge_provenance(REPOSITORY, 7, MERGE, self.call)
        valid = copy.deepcopy(self.pull)
        for change in (
            lambda pull: pull.update(autoCompleteSetBy={'id': 'user-id'}),
            lambda pull: pull['completionOptions'].update(triggeredByAutoComplete=True),
            lambda pull: pull['completionOptions'].update(bypassPolicy=True),
            lambda pull: pull['completionOptions'].update(mergeStrategy='squash'),
            lambda pull: pull['completionOptions'].update(mergeStrategy='rebase'),
            lambda pull: pull.update(closedBy={'id': 'service', 'uniqueName': 'Build Service'}),
            lambda pull: pull.update(lastMergeCommit={'commitId': 'f' * 40}),
            lambda pull: pull.update(status='active'),
        ):
            self.pull = copy.deepcopy(valid)
            change(self.pull)
            with self.subTest(pull=self.pull), self.assertRaises(azure_repos.AzureError):
                azure_repos.require_merge_provenance(REPOSITORY, 7, MERGE, self.call)

    def test_squash_requires_the_recorded_target_and_retains_full_metadata(self):
        self.complete()
        self.pull['completionOptions']['mergeStrategy'] = 'squash'
        self.pull['lastMergeTargetCommit'] = {'commitId': 'c' * 40}
        result = azure_repos.require_merge_provenance(REPOSITORY, 7, MERGE, self.call)
        self.assertEqual(result['head']['sha'], HEAD)
        self.assertEqual(result['azure']['lastMergeTargetCommit']['commitId'], 'c' * 40)
        for value in (None, {}, {'commitId': 'bad'}):
            self.pull['lastMergeTargetCommit'] = value
            with self.subTest(value=value), self.assertRaisesRegex(azure_repos.AzureError, 'target commit'):
                azure_repos.require_merge_provenance(REPOSITORY, 7, MERGE, self.call)

    def test_duplicate_pull_requests_and_malformed_responses_are_refused(self):
        def duplicates(*args):
            return [self.pull, dict(self.pull, pullRequestId=8)]
        with self.assertRaisesRegex(azure_repos.AzureError, 'multiple pull requests'):
            azure_repos.find(REPOSITORY, 'feature/milestone-001', 'main', duplicates)
        for value in (None, {}, 'not-json', [{'pullRequestId': True}]):
            with self.subTest(value=value), self.assertRaises(azure_repos.AzureError):
                azure_repos.find(REPOSITORY, 'feature/milestone-001', 'main', lambda *args: value)

    def test_pagination_keeps_searching_and_rejects_repeated_pages(self):
        unrelated = [azure_pull(number=i, branch='other') for i in range(1, 101)]
        wanted = azure_pull(number=101)
        skips = []
        def call(*args):
            if args[:3] == ('repos', 'pr', 'list'):
                skip = args[args.index('--skip') + 1]
                skips.append(skip)
                return unrelated if skip == '0' else [wanted]
            if args[:3] == ('repos', 'pr', 'show'):
                return wanted
            return self.call(*args)
        self.assertEqual(azure_repos.find(REPOSITORY, 'feature/milestone-001', 'main', call)['number'], 101)
        self.assertEqual(skips, ['0', '100'])
        with self.assertRaisesRegex(azure_repos.AzureError, 'pagination repeats'):
            azure_repos.find(REPOSITORY, 'feature/milestone-001', 'main', lambda *args: unrelated)

    def test_invalid_metadata_types_raise_a_provider_error(self):
        for field, value in (('status', []), ('pullRequestId', True), ('sourceRefName', 42)):
            pull = azure_pull()
            pull[field] = value
            with self.subTest(field=field), self.assertRaises(azure_repos.AzureError):
                azure_repos.normalize(REPOSITORY, pull, self.call)

    def test_az_failure_and_invalid_json_are_reported_without_falling_back_to_gh(self):
        for response in (CompletedProcess([], 1, '', 'az devops login required'),
                         CompletedProcess([], 0, 'invalid-json', '')):
            with self.subTest(response=response), mock.patch.object(integration, 'run_command', return_value=response) as run:
                with self.assertRaises(ArchiveError):
                    integration.azure_cli_json('repos', 'pr', 'list')
                self.assertEqual(run.call_args.args[0], 'az')


if __name__ == '__main__':
    unittest.main()
