"""Accounting contracts: partial transcripts, prices, persistence and PR retries."""
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import hashlib
import io
import json
from decimal import Decimal
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from scripts import azure_repos, gen_prices, integration, pr_editorial, usage_collect, usage_publish, usage_report
from tests._editorial_fixtures import publication_fixture

SINCE = '2026-01-01T00:00:00Z'
UNTIL = '2026-01-02T00:00:00Z'
ARCHIVE = '.project/archive/001-example'
HEAD = 'a' * 40


def prices():
    return gen_prices.convert({'anthropic': {'models': {'claude-example': {'name': 'Example Large',
        'cost': {'input': 4, 'output': 20, 'cache_read': .2, 'cache_write': 5}}}}},
        '2026-01-01', SINCE)


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.repo = self.root/'repo'
        self.repo.mkdir()
        subprocess.run(['git','init','-q','-b','feature/milestone-001',str(self.repo)],check=True)
        (self.repo/'.project').mkdir()
        (self.repo/'.project/STATE.md').write_text('---\nbranch: feature/milestone-001\n---\n')
        self.projects = self.root/'transcripts'
        self.projects.mkdir()
        self.path = self.root/'session.jsonl'
        self.price_file = self.root/'prices.json'
        self.price_file.write_text(json.dumps(prices()))
        self.aliases = self.root/'aliases.json'
        self.aliases.write_text('{}')
        usage_collect.configure(self.repo,SINCE,UNTIL,projects=self.projects)

    def event(self, identifier='msg-1', output=10, **extra):
        row={'type':'assistant','sessionId':'session','cwd':str(self.repo),'version':'2.1.300',
             'timestamp':'2026-01-01T12:00:00Z','message':{'id':identifier,'model':'claude-example',
             'usage':{'input_tokens':100,'output_tokens':output,'cache_creation_input_tokens':30,
             'cache_creation':{'ephemeral_5m_input_tokens':10,'ephemeral_1h_input_tokens':20},
             'cache_read_input_tokens':50}}}
        row.update(extra)
        return row

    def write(self, rows, path=None):
        target=path or self.path
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        return target

    def collect(self, rows):
        self.write(rows)
        return usage_collect.collect(self.repo,[self.path])

    def report(self):
        return usage_report.report(self.repo,self.price_file,self.aliases)

    def test_stream_updates_and_cross_file_duplicates_count_one_message(self):
        low, high=self.event(output=1),self.event(output=10)
        other=self.write([high],self.root/'copy.jsonl')
        self.write([low,high,low])
        first=usage_collect.collect(self.repo,[self.path,other])
        self.assertEqual((first['status'],first['messages']),('complete',1))
        ledger=Path(first['ledger']); before=ledger.read_bytes()
        usage_collect.collect(self.repo,[self.path,other])
        self.assertEqual(ledger.read_bytes(),before)
        value=self.report()
        self.assertEqual(value['total']['tokens']['output'],10)
        self.assertEqual(Decimal(value['total']['cost_usd']),Decimal('.00082'))

    def test_unknown_schema_and_truncated_json_are_visible_without_losing_known_usage(self):
        self.write([self.event(),self.event('other',version='3.0.0')])
        with self.path.open('a') as handle:handle.write('{partial')
        result=usage_collect.observe(self.repo,transcripts=[self.path])
        self.assertEqual(result['status'],'incomplete')
        rows=usage_collect.ledger_rows(Path(result['ledger']))
        self.assertTrue(any(r['kind']=='collection_failed' for r in rows))
        self.assertEqual(self.report()['total']['messages'],1)
        self.assertIn('Collection incomplete',usage_report.summary(self.report()))

    def test_raw_unicode_line_separators_inside_events_do_not_split_records(self):
        self.path.write_text(json.dumps(self.event(toolUseResult='a\u2028b\u2029c\x85d'),ensure_ascii=False)+'\n',
                             encoding='utf-8')
        result=usage_collect.collect(self.repo,[self.path])
        self.assertEqual((result['status'],result['messages']),('complete',1))

    def test_final_usage_corrects_provisional_input_into_cached_tokens(self):
        initial,final=self.event(output=0),self.event(output=10)
        initial['message']['usage']={'input_tokens':2500,'output_tokens':0}
        final['message']['stop_reason']='tool_use'
        final['message']['usage']['input_tokens']=300
        final['message']['usage']['cache_read_input_tokens']=2200
        self.collect([initial])
        self.collect([initial,final])
        result=self.report()['total']['tokens']
        self.assertEqual((result['input'],result['cache_read'],result['output']),(300,2200,10))

    def test_scope_excludes_neighbouring_projects_and_other_sessions(self):
        a=self.event('outside',cwd=str(self.repo)+'-neighbour')
        b=self.event('old',timestamp='2025-12-01T00:00:00Z')
        self.collect([self.event(),a,b])
        self.assertEqual(self.report()['total']['messages'],1)

    def test_sessions_can_be_scoped_with_explicit_identity(self):
        config,_=usage_collect.location(self.repo)
        value=json.loads(config.read_text());value['sessions']=['session'];config.write_text(json.dumps(value))
        self.collect([self.event(),self.event('other',sessionId='another')])
        self.assertEqual(self.report()['total']['messages'],1)

    def test_retry_attempts_are_summed_and_assigned_using_metadata(self):
        files=[]
        for agent in ('one','two'):
            p=self.root/'session/subagents'/('agent-'+agent+'.jsonl')
            self.write([self.event(agent,agentId=agent)],p)
            p.with_suffix('.meta.json').write_text(json.dumps({'description':'build_t001','finished':True}))
            files.append(p)
        usage_collect.collect(self.repo,files)
        result=self.report()
        task=result['tasks'][0]
        self.assertEqual((task['name'],task['attempts'],task['tokens']['output']),('build_t001',2,20))
        self.assertEqual(result['phases'][0]['name'],'build')

    def test_running_or_unlabelled_child_has_explicit_incomplete_or_unattributed_usage(self):
        p=self.root/'session/subagents/agent-one.jsonl'
        self.write([self.event()],p)
        result=usage_collect.collect(self.repo,[p])
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(self.report()['tasks'][0]['name'],'unattributed')

    def test_synthetic_zero_messages_are_excluded(self):
        row=self.event('synthetic')
        row['message']={'model':'<synthetic>','usage':{'input_tokens':0,'output_tokens':0}}
        self.collect([self.event(),row])
        self.assertEqual(self.report()['total']['messages'],1)

    def test_conflicting_usage_is_not_silently_repriced(self):
        a,b=self.event(),self.event()
        b['message']['usage']['input_tokens']=1
        b['message']['usage']['output_tokens']=20
        self.assertEqual(self.collect([a,b])['status'],'incomplete')
        self.assertEqual(self.report()['total']['tokens']['output'],10)

    def test_missing_cache_ttl_or_price_retains_tokens_and_marks_cost_unknown(self):
        row=self.event()
        del row['message']['usage']['cache_creation']
        self.collect([row])
        value=self.report()
        self.assertIsNone(value['total']['cost_usd'])
        self.assertNotIn('US$ 0.00',usage_report.summary(value))
        self.assertEqual(value['total']['tokens']['cache_write_unknown'],30)
        self.assertIn('unpriced',usage_report.markdown(value))
        catalog=prices();catalog['models']['claude-example']['cache_read']=None
        self.price_file.write_text(json.dumps(catalog));self.collect([self.event()])
        self.assertIsNone(self.report()['total']['cost_usd'])

    def test_personal_aliases_never_enter_the_portable_report(self):
        row=self.event();row['message']['model']='personal-routing-variant'
        self.aliases.write_text('{"personal-routing-variant":"claude-example"}')
        self.collect([row]);result=self.report()
        self.assertEqual(result['models'][0]['name'],'Example Large')
        self.assertNotIn('personal-routing-variant',json.dumps(result))
        self.assertNotIn(str(self.root),json.dumps(result))
        self.assertIn('personal-routing-variant',Path(usage_collect.location(self.repo)[1]).read_text())

    def test_completed_observations_survive_host_transcript_cleanup(self):
        first=self.collect([self.event()])
        self.path.unlink()
        self.assertEqual(self.report()['total']['tokens']['output'],10)
        usage_collect.collect(self.repo)
        self.assertEqual(self.report()['total']['tokens']['output'],10)

    def test_a_later_provisional_copy_cannot_override_recorded_final_usage(self):
        final=self.event();final['message']['stop_reason']='end_turn'
        self.collect([final])
        provisional=self.event(output=20)
        self.collect([provisional])
        self.assertEqual(self.report()['total']['tokens']['output'],10)

    def test_cli_children_are_excluded_visibly_and_start_retry_preserves_the_exclusion(self):
        self.write([self.event()])
        usage_collect.collect(self.repo,[self.path],unobserved_attempt='T001/1')
        usage_collect.configure(self.repo)
        self.assertEqual(usage_collect.collect(self.repo,[self.path])['status'],'incomplete')

    def test_snapshot_round_trip_works_without_local_git_ledger(self):
        self.collect([self.event()]);value=self.report()
        stored=self.root/'snapshot.json';stored.write_text(json.dumps({'usage':value}))
        Path(usage_collect.location(self.repo)[1]).unlink()
        result=subprocess.run([sys.executable,'-B',str(Path(usage_report.__file__)),'--repo',str(self.repo),
            '--snapshot',str(stored),'--format','markdown'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('Example Large',result.stdout)

    def test_snapshot_attachment_preserves_evidence_and_is_immutable_after_commit(self):
        self.collect([self.event()])
        publication_fixture(self.repo,ARCHIVE,'coordinator',HEAD,'https://github.com/example/product.git')
        path=pr_editorial.publication_path(self.repo,ARCHIVE)
        before=json.loads(path.read_text())
        usage_report.attach(self.repo,ARCHIVE,'coordinator',self.report())
        after=json.loads(path.read_text())
        self.assertEqual({k:after[k] for k in before},before)
        subprocess.run(['git','-C',str(self.repo),'add','.'],check=True)
        subprocess.run(['git','-C',str(self.repo),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid',
                        'commit','-qm','fixture'],check=True)
        with self.assertRaisesRegex(ValueError,'immutable'):
            usage_report.attach(self.repo,ARCHIVE,'coordinator',self.report())

    def test_tampered_snapshot_totals_are_rejected(self):
        self.collect([self.event()]);value=self.report();value['total']['tokens']['output']+=1
        with self.assertRaisesRegex(ValueError,'totals'):usage_report.validate_snapshot(value)

    def test_parser_never_copies_prompt_or_response_text(self):
        row=self.event();row['message']['content']='private transcript content'
        result=self.collect([row])
        self.assertNotIn('private transcript content',Path(result['ledger']).read_text())

    def test_parallel_collectors_do_not_drop_or_duplicate_messages(self):
        one=self.write([self.event('one')],self.root/'one.jsonl')
        two=self.write([self.event('two')],self.root/'two.jsonl')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda p:usage_collect.collect(self.repo,[p]),[one,two]))
        self.assertTrue(all(r['status']=='complete' for r in results))
        self.assertEqual(self.report()['total']['messages'],2)

    def test_editorial_prepare_automatically_freezes_configured_usage(self):
        self.collect([self.event()])
        fixture=publication_fixture(self.repo,ARCHIVE,'coordinator',HEAD,'https://github.com/example/product.git')
        pr_editorial.publication_path(self.repo,ARCHIVE).unlink()
        subprocess.run(['git','-C',str(self.repo),'remote','add','origin','https://github.com/example/product.git'],check=True)
        result=pr_editorial.prepare(self.repo,ARCHIVE,'coordinator',HEAD,{'title':fixture['title'],'sections':fixture['sections']})
        value=pr_editorial.load(self.repo,ARCHIVE,'coordinator',HEAD)
        self.assertEqual(value['usage']['total']['messages'],1)
        self.assertNotIn(str(self.root),json.dumps(value['usage']))
        self.assertEqual(result['usage_unpriced_models'],['claude-example'])
        self.assertNotIn('claude-example',json.dumps(value))

    def test_unpriced_raw_ids_are_named_after_alias_mapping(self):
        rows=[self.event(),self.event('msg-2'),self.event('msg-3')]
        rows[1]['message']['model']='personal-routing-variant'
        rows[2]['message']['model']='new-model'
        self.aliases.write_text('{"personal-routing-variant":"claude-example"}')
        self.collect(rows)
        self.assertEqual(usage_report.unpriced(self.repo,self.price_file,self.aliases),['new-model'])

    def test_accounting_failure_cannot_reverse_a_successful_task_landing(self):
        from scripts import dispatch_driver
        with patch.object(dispatch_driver,'land_task',return_value={'commit':HEAD}), \
             patch.object(dispatch_driver.isolation,'retire'), \
             patch.object(dispatch_driver.subprocess,'run',side_effect=OSError('accounting unavailable')):
            self.assertEqual(dispatch_driver.finish_task(self.repo,{'mode':'serial','worktree':str(self.repo),
                'task_id':'T001'})['commit'],HEAD)


class PriceTests(unittest.TestCase):
    def test_tiers_apply_above_their_actual_boundary_per_request(self):
        model={'input':1,'output':2,'tiers':[{'over':272000,'input':3,'output':4}]}
        for n,expected in [(200001,'0.200001'),(272000,'.272'),(272001,'.816003')]:
            row={'prompt_size':n,'tokens':{'input':n}}
            self.assertEqual(usage_report.message_cost(row,model),Decimal(expected))

    def test_generator_records_primary_prices_and_cache_ttls_without_legacy_tier_alias(self):
        catalog={'anthropic':{'models':{'claude-example':{'name':'Example','cost':{'input':4,
            'output':20,'cache_write':5,'context_over_200k':{'input':999}}}}},
            'reseller':{'models':{'claude-example':{'name':'Other','cost':{'input':999}}}}}
        result=gen_prices.convert(catalog,'2026-01-01',SINCE)['models']['claude-example']
        self.assertEqual((result['input'],result['cache_write_5m'],result['cache_write_1h']),(4,5,8))
        self.assertEqual(result['tiers'],[])

    def test_invalid_prices_and_large_changes_require_review(self):
        for n in (-1,float('nan'),float('inf'),True,'invalid'):
            value=prices();value['models']['claude-example']['input']=n
            with self.assertRaises(ValueError):usage_report.validate_prices(value)
        old=prices();new=copy.deepcopy(old);new['models']['claude-example']['input']=8
        self.assertTrue(gen_prices.changes(old,new))

    def test_conditional_refresh_preserves_the_dated_snapshot_on_304(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'prices.json';value=prices();value['etag']='snapshot-etag'
            path.write_text(json.dumps(value));before=path.read_bytes()
            def unchanged(request,timeout):
                self.assertEqual(request.get_header('If-none-match'),'snapshot-etag')
                raise HTTPError(gen_prices.SOURCE,304,'Not Modified',{},None)
            with patch.object(sys,'argv',['gen_prices','--output',str(path),'--as-of','2026-01-02']), \
                 patch.object(gen_prices,'urlopen',side_effect=unchanged),redirect_stdout(io.StringIO()):
                self.assertEqual(gen_prices.main(),0)
            self.assertEqual(path.read_bytes(),before)


class PublicationTests(unittest.TestCase):
    setUp = UsageTests.setUp
    event = UsageTests.event
    write = UsageTests.write
    collect = UsageTests.collect
    report = UsageTests.report

    def setup_publish(self):
        self.collect([self.event()])
        self.document={'archive':ARCHIVE,'member':'coordinator','language':'pt-BR','usage':self.report()}
        self.azure=azure_repos.Repository('example-org','Example Project','product')
        self.pull={'number':7,'html_url':self.azure.web_url+'/pullrequest/7','azure':{'createdBy':{'id':'owner'}}}
        self.threads=[];self.writes=[]

    def call(self,*args):
        resource=args[args.index('--resource')+1]
        route=dict(bit.split('=',1) for bit in args[args.index('--route-parameters')+1:args.index('--api-version')])
        method=args[args.index('--http-method')+1] if '--http-method' in args else 'GET'
        if resource=='pullRequestThreads' and method=='GET':return {'value':copy.deepcopy(self.threads)}
        if method!='GET':
            payload=json.loads(Path(args[args.index('--in-file')+1]).read_text());self.writes.append((method,payload))
        if resource=='pullRequestThreads':
            row={'id':10,'status':payload['status'],'comments':[{'id':1,'author':{'id':'owner'},'content':payload['comments'][0]['content']}]}
            self.threads.append(row);return copy.deepcopy(row)
        comment=next(t for t in self.threads if t['id']==int(route['threadId']))['comments'][0]
        if method=='PATCH':comment['content']=payload['content']
        return copy.deepcopy(comment)

    def publish(self):
        return usage_publish.publish(self.repo,self.azure,self.pull,self.document,self.call,None)

    def test_publish_is_idempotent_and_keeps_the_thread_resolved(self):
        self.setup_publish()
        self.assertEqual(self.publish()['status'],'published')
        self.assertEqual(self.publish()['status'],'published')
        self.assertEqual(len(self.writes),1)
        self.assertEqual(self.threads[0]['status'],'closed')

    def test_publish_adopts_the_approved_manual_table_once(self):
        self.setup_publish();self.document['usage_thread']=10
        content=usage_report.markdown(self.document['usage'],'pt-BR').replace('| Total |','| **Total** |')
        self.threads=[{'id':10,'status':None,'comments':[{'id':1,'author':{'id':'owner'},'content':content}]}]
        self.assertEqual(self.publish()['status'],'published')
        self.assertEqual([w[0] for w in self.writes],['PATCH'])
        self.assertIsNone(self.threads[0]['status'])

    def test_publish_preserves_human_comment_edits_and_wrong_author(self):
        self.setup_publish();self.publish()
        self.threads[0]['comments'][0]['content']+='Human note\n'
        self.assertEqual(self.publish()['status'],'incomplete')
        self.assertEqual(len(self.writes),1)
        self.threads[0]['comments'][0]['author']['id']='someone-else'
        self.assertEqual(self.publish()['status'],'incomplete')

    def test_publish_recovers_after_receipt_loss_without_duplicate_comments(self):
        self.setup_publish()
        with patch.object(usage_publish._common,'atomic_write',side_effect=OSError('interrupted')):
            self.assertEqual(self.publish()['status'],'incomplete')
        self.assertEqual(self.publish()['status'],'published')
        self.assertEqual(len(self.writes),1)

    def test_publish_replaces_only_the_usage_section_in_human_copy(self):
        self.setup_publish()
        body='## Problema\n\nHuman explanation.\n\n## Consumo de tokens e custo\n\nOld totals.\n\n## Extra\n\nKeep me.\n'
        updated=usage_publish.description(body,self.document)
        self.assertIn('Human explanation.',updated);self.assertIn('Keep me.',updated)
        self.assertNotIn('Old totals.',updated)
        self.assertEqual(usage_publish.description(updated,self.document),updated)

    def test_publish_preserves_edits_inside_an_already_managed_summary(self):
        self.setup_publish()
        body=usage_publish.description('## Problem\n\nKeep.\n',self.document)
        with self.assertRaisesRegex(ValueError,'edited by a person'):
            usage_publish.description(body.replace('tokens de saída','custom tokens'),self.document)
        old_hash=hashlib.sha256(usage_publish.section(self.document).strip().encode()).hexdigest()
        self.document['usage']['coverage']='incomplete'
        self.assertIn('Coleta incompleta.',usage_publish.description(body,self.document,old_hash))

    def test_publish_rejects_oversized_azure_copy_before_creating_a_pr(self):
        self.setup_publish()
        body='\n\n'.join('## '+title+'\n\n'+('x'*900) for title in pr_editorial.LANGUAGES['en'][:5])
        with patch.object(integration,'azure_cli_json') as api:
            with self.assertRaisesRegex(integration.ArchiveError,'4000'):
                integration.create_provider_pull_request(self.azure,'feature/milestone-001','main','feat: example',body,HEAD)
            api.assert_not_called()

    def test_publish_github_comment_reuses_the_same_record(self):
        self.setup_publish();comments=[];writes=[]
        def api(endpoint,*args):
            if endpoint=='user':return {'id':42}
            method=args[args.index('--method')+1] if '--method' in args else 'GET'
            if endpoint.endswith('/comments') and method=='GET':return [copy.deepcopy(comments)]
            if method=='POST':
                body=next(v[5:] for v in args if v.startswith('body='))
                comments.append({'id':20,'body':body,'user':{'id':42}});writes.append(body)
                return copy.deepcopy(comments[0])
            return copy.deepcopy(comments[0])
        def publish():return usage_publish.publish(self.repo,'example/product',self.pull,self.document,None,api)
        self.assertEqual(publish()['status'],'published')
        self.assertEqual(publish()['status'],'published')
        self.assertEqual(len(writes),1)


if __name__=='__main__':unittest.main()
