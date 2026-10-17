import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import azure_repos, azure_work_items, integration, path_config, pr_editorial
from tests._editorial_fixtures import publication_fixture

ARCHIVE = '.project/archive/001-example'
HEAD = 'a' * 40
LANDING = 'b' * 40
AZURE = azure_repos.Repository('example-org', 'Example Project', 'product')


class FakeBoards:
    def __init__(self):
        self.calls, self.writes, self.links = [], [], set()
        self.value = {'id': 101, 'rev': 1, 'fields': {'System.TeamProject': AZURE.project,
            'System.WorkItemType': 'Task', 'System.State': 'Active', 'System.Title': 'APP-1: reviewed feature',
            'System.Description': 'The reviewed acceptance.'}}
        self.updates = []
        self.conflict, self.fail = False, False

    def __call__(self, *args):
        self.calls.append(args)
        def option(flag): return args[args.index(flag) + 1]
        assert option('--organization') == AZURE.organization_url
        if args[:3] == ('boards', 'work-item', 'show'):
            return copy.deepcopy(self.value)
        if args[:4] == ('repos', 'pr', 'work-item', 'list'):
            return [{'id': str(number)} for number in self.links]
        if args[:4] == ('repos', 'pr', 'work-item', 'add'):
            self.links.add(int(args[args.index('--work-items')+1]))
            return []
        if args[:2] != ('devops', 'invoke'): raise AssertionError(args)
        resource = option('--resource')
        if resource == 'updates': return {'value': copy.deepcopy(self.updates)}
        if resource == 'workItemTypeStates':
            return {'value': [{'name': 'Closed', 'category': 'Completed'},
                              {'name': 'Removed', 'category': 'Removed'},
                              {'name': 'Active', 'category': 'InProgress'}]}
        if resource != 'workitems': raise AssertionError(args)
        if self.fail: raise RuntimeError('temporary Azure outage')
        patch = json.loads(Path(option('--in-file')).read_text())
        assert option('--media-type') == 'application/json-patch+json'
        if self.conflict:
            self.value['rev'] += 1
            self.conflict = False
        if patch[0] != {'op': 'test', 'path': '/rev', 'value': self.value['rev']}:
            raise RuntimeError('revision conflict')
        self.writes.append(patch)
        for op in patch[1:]: self.value['fields'][op['path'].removeprefix('/fields/')] = op['value']
        self.value['rev'] += 1
        self.updates.append({'id': self.value['rev'], 'fields': {'System.History': {
            'newValue': self.value['fields']['System.History']}}})
        return copy.deepcopy(self.value)


class EditorialTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)/'repo'
        self.repo.mkdir()
        subprocess.run(['git', 'init', '-q', '-b', 'feature/milestone-001', str(self.repo)],check=True)
        subprocess.run(['git', '-C', str(self.repo), 'remote', 'add', 'origin', AZURE.web_url],check=True)
        fixture = publication_fixture(self.repo, ARCHIVE, 'coordinator', HEAD, AZURE.web_url)
        pr_editorial.publication_path(self.repo, ARCHIVE, 'coordinator').unlink()
        intent = self.repo/ARCHIVE/'intent/INTENT.md'
        intent.write_text('# Intent\n\nAPP-1 (board 101) is in scope.\n')
        self.authored = {'title': 'feat(api): adicionar validação',
                         'sections': fixture['sections'], 'work_items': []}
        self.defaults = mock.patch.object(path_config, 'user_defaults', return_value={})
        self.defaults.start(); self.addCleanup(self.defaults.stop)
        self.boards = FakeBoards()
        self.pull = {'number': 7, 'html_url': AZURE.web_url+'/pullrequest/7', 'state': 'closed',
                     'merged_at': '2026-01-01T00:00:00Z', 'merge_commit_sha': LANDING, 'head': {'sha': HEAD}}

    def prepare(self, disposition=None):
        value = copy.deepcopy(self.authored)
        if disposition:
            value['work_items'] = [{'id': 101, 'disposition': disposition,
                'summary': 'Entrega do critério APP-1.', 'evidence': 'SC1: controle plantado e suíte registrada.',
                **({'remaining': 'The follow-up criterion remains open.'} if disposition=='partial' else {}),
                **({'reason': 'The reviewed intent supersedes this task.'} if disposition=='obsolete' else {})}]
        pr_editorial.prepare(self.repo, ARCHIVE, 'coordinator', HEAD, value, self.boards)
        return pr_editorial.load(self.repo, ARCHIVE, 'coordinator', HEAD)

    def sync(self):
        return pr_editorial.synchronize(self.repo,ARCHIVE,'coordinator',HEAD,AZURE,self.pull,LANDING,self.boards)

    def test_missing_editorial_blocks_publication(self):
        with self.assertRaisesRegex(pr_editorial.EditorialError,'editorial-required'):
            pr_editorial.load(self.repo,ARCHIVE,'coordinator',HEAD)

    def test_localized_copy_uses_conventional_title_and_all_sections(self):
        (self.repo/'.project/config.json').write_text('{"editorial_language":"pt-BR"}')
        value=self.prepare()
        body=pr_editorial.render(value)
        self.assertIn('## Validação',body)
        self.assertNotIn('Archive:',body)
        self.assertEqual(value['language'],'pt-BR')
        pr_editorial.require_body_format(body,'pt-BR')

    def test_bad_title_and_missing_sections_are_refused(self):
        for field,value in [('title','integrate: M001'),('sections',{'problem':'Only one section'})]:
            with self.subTest(field=field):
                old=copy.deepcopy(self.authored)
                self.authored[field]=value
                with self.assertRaises(pr_editorial.EditorialError):self.prepare()
                self.authored=old

    def test_temporary_paths_and_attribution_are_not_published(self):
        for value in ('See /tmp/proof.log','Generated by Claude Code'):
            self.authored['sections']['validation']=value
            with self.assertRaises(pr_editorial.EditorialError):self.prepare()

    def test_changed_evidence_and_wrong_head_invalidate_editorial(self):
        self.prepare()
        with self.assertRaisesRegex(pr_editorial.EditorialError,'HEAD'):
            pr_editorial.load(self.repo,ARCHIVE,'coordinator','c'*40)
        (self.repo/ARCHIVE/'MANIFEST.md').write_text('Changed evidence\n')
        with self.assertRaisesRegex(pr_editorial.EditorialError,'evidence'):
            pr_editorial.load(self.repo,ARCHIVE,'coordinator',HEAD)

    def test_unknown_work_item_is_not_associated(self):
        (self.repo/ARCHIVE/'intent/INTENT.md').write_text('# No linked scope\n')
        with self.assertRaisesRegex(pr_editorial.EditorialError,'approved intent'):self.prepare('complete')
        self.assertFalse(self.boards.calls)

    def test_links_are_native_and_repeat_safe_without_closing_before_merge(self):
        value=self.prepare('complete')
        pr_editorial.link(value,AZURE,self.pull,self.boards)
        pr_editorial.link(value,AZURE,self.pull,self.boards)
        additions=[a for a in self.boards.calls if a[:4]==('repos','pr','work-item','add')]
        self.assertEqual(len(additions),1)
        self.assertEqual(self.boards.value['fields']['System.State'],'Active')
        self.assertFalse(self.boards.writes)

    def test_open_wrong_source_or_wrong_landing_never_changes_boards(self):
        self.prepare('complete');self.boards.links.add(101)
        for changes in ({'state':'open','merged_at':None},{'head':{'sha':'c'*40}}, {'merge_commit_sha':'c'*40}):
            with self.subTest(changes=changes):
                original=copy.deepcopy(self.pull);self.pull.update(changes)
                with self.assertRaisesRegex(pr_editorial.EditorialError,'validated merged PR'):self.sync()
                self.pull=original
        self.assertFalse(self.boards.writes)

    def test_completed_delivery_updates_state_and_comment_in_one_revision_patch(self):
        self.prepare('complete');self.boards.links.add(101)
        first=self.sync()
        self.assertEqual(first['status'],'complete')
        self.assertEqual(self.boards.value['fields']['System.State'],'Closed')
        self.assertEqual(self.boards.writes[0][0]['path'],'/rev')
        self.assertIn(LANDING,self.boards.value['fields']['System.History'])
        self.assertEqual(self.sync()['items'][0]['status'],'already-applied')
        self.assertEqual(len(self.boards.writes),1)
        pr_editorial.require_synchronized(self.repo,ARCHIVE,'coordinator',LANDING)

    def test_partial_and_obsolete_items_preserve_their_history(self):
        for disposition,expected in (('partial','Active'),('obsolete','Removed')):
            with self.subTest(disposition=disposition):
                self.boards=FakeBoards();self.prepare(disposition);self.boards.links.add(101)
                self.assertEqual(self.sync()['status'],'complete')
                self.assertEqual(self.boards.value['fields']['System.State'],expected)
                self.assertTrue(self.boards.value['fields']['System.History'])
                self.assertFalse(any('DELETE' in a for a in self.boards.calls))

    def test_changed_acceptance_and_revision_conflict_leave_a_pending_receipt(self):
        self.prepare('complete');self.boards.links.add(101)
        self.boards.value['fields']['System.Description']='Different acceptance'
        self.assertEqual(self.sync()['status'],'pending')
        self.assertFalse(self.boards.writes)
        with self.assertRaisesRegex(pr_editorial.EditorialError,'pending'):
            pr_editorial.require_synchronized(self.repo,ARCHIVE,'coordinator',LANDING)
        self.boards.value['fields']['System.Description']='The reviewed acceptance.'
        self.boards.conflict=True
        self.assertEqual(self.sync()['status'],'pending')
        self.assertFalse(self.boards.writes)
        self.assertEqual(self.sync()['status'],'complete')

    def test_outage_can_resume_and_a_human_reopen_is_not_undone(self):
        self.prepare('complete');self.boards.links.add(101);self.boards.fail=True
        self.assertEqual(self.sync()['status'],'pending')
        self.boards.fail=False
        self.assertEqual(self.sync()['status'],'complete')
        self.boards.value['fields']['System.State']='Active'
        self.assertEqual(self.sync()['status'],'complete')
        self.assertEqual(self.boards.value['fields']['System.State'],'Active')
        self.assertEqual(len(self.boards.writes),1)

    def test_a_human_removal_before_merge_is_not_reopened(self):
        self.prepare('complete');self.boards.links.add(101)
        self.boards.value['fields']['System.State']='Removed'
        self.assertEqual(self.sync()['status'],'pending')
        self.assertEqual(self.boards.value['fields']['System.State'],'Removed')
        self.assertFalse(self.boards.writes)

    def test_crash_after_remote_patch_does_not_duplicate_the_comment(self):
        self.prepare('complete');self.boards.links.add(101)
        with mock.patch.object(pr_editorial._common,'atomic_write',side_effect=OSError('interrupted receipt')):
            with self.assertRaises(OSError):self.sync()
        self.assertEqual(self.sync()['status'],'complete')
        self.assertEqual(len(self.boards.writes),1)

    def test_ambiguous_completion_state_requires_an_explicit_choice(self):
        client=azure_work_items.Client(AZURE,self.boards)
        with mock.patch.object(client,'invoke',return_value={'value':[{'name':'Done','category':'Completed'},
                                                                   {'name':'Closed','category':'Completed'}]}):
            with self.assertRaisesRegex(azure_work_items.WorkItemError,'explicit'):
                client.target_state(self.boards.value,'complete')
            self.assertEqual(client.target_state(self.boards.value,'complete','Done'),'Done')

    def test_wrong_project_and_provider_are_refused(self):
        self.boards.value['fields']['System.TeamProject']='Other Project'
        with self.assertRaisesRegex(azure_work_items.WorkItemError,'different Azure project'):self.prepare('complete')
        value=self.prepare()
        with self.assertRaisesRegex(pr_editorial.EditorialError,'GitHub origin'):
            pr_editorial.link(value,'example/other',self.pull,self.boards)


if __name__=='__main__': unittest.main()
