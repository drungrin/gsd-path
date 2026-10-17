"""Real member merge/tag gates with a synthetic Azure Boards service."""
import json
import subprocess
import unittest
from unittest import mock

from scripts import integration, pr_editorial
from scripts.archive_milestone import ArchiveError
from tests import test_azure_integration as azure
from tests.test_pr_editorial import FakeBoards


class EditorialDeliveryTests(unittest.TestCase):
    fixture = azure.AzureIntegrationTests.fixture
    remote_ref = azure.AzureIntegrationTests.remote_ref
    az = azure.AzureIntegrationTests.az
    complete = azure.AzureIntegrationTests.complete

    def setUp(self):
        azure.AzureIntegrationTests.setUp(self)
        self.boards = FakeBoards()
        intent = self.root/azure.ARCHIVE/'intent/INTENT.md'
        intent.write_text(intent.read_text()+'\nAPP-1 (board 101) is included.\n')
        old = json.loads(pr_editorial.publication_path(self.root,azure.ARCHIVE,'web').read_text())
        authored = {'title':old['title'],'sections':old['sections'],'work_items':[{
            'id':101,'disposition':'complete','summary':'Deliver APP-1.',
            'evidence':'The reviewed criterion and its fixture pass.'}]}
        pr_editorial.prepare(self.root,azure.ARCHIVE,'web',self.member_tip,authored,self.boards)

    def commands(self,*args):
        if (args[1:3] in (('devops','invoke'),('boards','work-item'))
                or args[1:4]==('repos','pr','work-item')):
            return subprocess.CompletedProcess(args,0,json.dumps(self.boards(*args[1:])), '')
        return self.az(*args)

    def integrate(self):
        with mock.patch.object(integration,'run_command',side_effect=self.commands):
            return integration.integrate_member_pull_request(self.root,'web',azure.ARCHIVE,self.member_tip)

    def test_boards_are_updated_only_after_the_real_squash_and_published_tags(self):
        self.assertEqual(self.integrate()['status'],'awaiting-merge')
        self.assertEqual(self.boards.value['fields']['System.State'],'Active')
        landing=self.complete(squash=True)
        self.boards.fail=True
        result=self.integrate()
        self.assertEqual(result['status'],'work-items-pending')
        self.assertEqual(result['merge'],landing)
        self.assertEqual(self.remote_ref('refs/tags/'+azure.TAG+'^{}'),landing)
        with mock.patch.object(integration,'run_command',side_effect=self.commands):
            self.assertEqual(integration.validate_member_integrated(self.root,'web',azure.ARCHIVE,self.member_tip)['merge'],landing)
        with self.assertRaisesRegex(pr_editorial.EditorialError,'synchronization pending'):
            pr_editorial.require_synchronized(self.root,azure.ARCHIVE,'web',landing)
        self.boards.fail=False
        self.assertEqual(self.integrate()['work_items']['status'],'complete')
        self.assertEqual(self.boards.value['fields']['System.State'],'Closed')
        self.integrate()
        self.assertEqual(len(self.boards.writes),1)

    def test_an_unverified_merge_never_reaches_the_boards_update(self):
        self.integrate()
        self.complete(squash=True)
        self.pulls[0]['completionOptions']['bypassPolicy']=True
        with self.assertRaisesRegex(ArchiveError,'bypass'):
            self.integrate()
        self.assertFalse(self.boards.writes)
