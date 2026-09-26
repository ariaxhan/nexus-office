from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'client'))
import office_github_actions as actions
import office_github_bump as bump
import office_github_inventory as inventory


def issue(number,labels=(),created='2026-01-01T00:00:00Z',updated='2026-09-01T00:00:00Z'):
    return {'number':number,'title':f'Issue {number}','url':'','created_at':created,'updated_at':updated,
            'author':'a','labels':list(labels),'comments':0}


ROWS=[{'repo':'ariaxhan/app','enabled':True,'tower':True},{'repo':'ariaxhan/away','enabled':True,'tower':True},
      {'repo':'ariaxhan/off','enabled':False,'tower':False},
      {'repo':'thinking-brain-school/tbs','enabled':False,'tower':False},
      {'repo':'ariaxhan/thinking-brain-school','enabled':False,'tower':False}]


class Sets(unittest.TestCase):
    def test_tower_is_enabled_tower_rows_and_put_away_is_its_own_category(self):
        spec=inventory.tower_set(ROWS,{'ariaxhan/away'})
        self.assertEqual(spec['included'],['ariaxhan/app'])
        reasons={row['repo']:row['reason'] for row in spec['excluded']}
        self.assertEqual(reasons['ariaxhan/away'],'put away in Office')
        self.assertEqual(reasons['ariaxhan/off'],'disabled in registry')

    def test_tbs_comes_from_the_org_and_names_every_registry_mismatch(self):
        org=[('Thinking-Brain-School/tbs',False),('Thinking-Brain-School/tbs-gugumon',False),('Thinking-Brain-School/old',True)]
        spec=inventory.tbs_set(ROWS,org,'')
        self.assertEqual(spec['included'],['Thinking-Brain-School/tbs','Thinking-Brain-School/tbs-gugumon'])
        self.assertEqual(spec['org_only'],['Thinking-Brain-School/tbs-gugumon'])
        self.assertEqual(spec['registry_only'],['ariaxhan/thinking-brain-school'])
        self.assertEqual(spec['excluded'],[{'repo':'Thinking-Brain-School/old','reason':'archived'}])

    def test_failed_org_listing_keeps_last_good_repos_and_is_never_complete(self):
        last={'repos':[['Thinking-Brain-School/tbs',False],['Thinking-Brain-School/tbs-gugumon',False]],'fetched_at':'earlier'}
        spec=inventory.tbs_set(ROWS,None,'HTTP 502',last)
        self.assertEqual(spec['included'],['Thinking-Brain-School/tbs','Thinking-Brain-School/tbs-gugumon'])
        self.assertIn('HTTP 502',spec['listing_error'])
        cache={'Thinking-Brain-School/tbs':{'total':3,'issues':[issue(n) for n in range(3)],'fetched_at':'now'},
               'Thinking-Brain-School/tbs-gugumon':{'total':4,'issues':[issue(n) for n in range(4)],'fetched_at':'earlier'}}
        with patch.object(inventory,'active_issues',return_value=(set(),{})):
            result=inventory.assemble({'TBS':spec},cache,{'Thinking-Brain-School/tbs':cache['Thinking-Brain-School/tbs']},{},'','t')
        group=result['groups'][0]
        self.assertEqual(group['total'],7)
        self.assertFalse(group['complete'])
        self.assertIn('repo set unconfirmed, org listing failed: HTTP 502',group['denominator'])

    def test_a_github_timeout_is_a_failed_fetch_not_an_aborted_refresh(self):
        import subprocess
        access=type('A',(),{'read_token_for':lambda self,repo:('me','t')})()
        with patch.object(inventory.subprocess,'run',side_effect=subprocess.TimeoutExpired('gh',60)):
            self.assertEqual((None,'org listing timed out after 60s'),inventory.org_listing(access))
            fresh,errors=inventory.collect(access,['ariaxhan/app'])
        self.assertEqual({},fresh)
        self.assertIn('timed out',errors['ariaxhan/app'])

    def test_an_archived_registry_repo_is_excluded_not_counted(self):
        spec=inventory.tower_set(ROWS,set())
        cache={'ariaxhan/app':{'total':2,'issues':[issue(1),issue(2)],'archived':True,'fetched_at':'now'}}
        with patch.object(inventory,'active_issues',return_value=(set(),{})):
            group=inventory.assemble({'Tower':spec},cache,cache,{},'','t')['groups'][0]
        self.assertEqual(0,sum(row['open'] or 0 for row in group['repos']))
        self.assertIn({'repo':'ariaxhan/app','reason':'archived'},group['excluded'])

    def test_failed_org_listing_with_no_last_good_uses_registry_and_is_incomplete(self):
        spec=inventory.tbs_set(ROWS,None,'HTTP 502')
        self.assertEqual(spec['included'],['thinking-brain-school/tbs'])
        cache={'thinking-brain-school/tbs':{'total':1,'issues':[issue(1)],'fetched_at':'now'}}
        with patch.object(inventory,'active_issues',return_value=(set(),{})):
            group=inventory.assemble({'TBS':spec},cache,cache,{},'','t')['groups'][0]
        self.assertFalse(group['complete'])


class Honesty(unittest.TestCase):
    def assemble(self,fresh,errors,cache):
        specs={'Tower':{'included':['ariaxhan/app','ariaxhan/b','ariaxhan/c'],'excluded':[],'source':'test'}}
        with patch.object(inventory,'active_issues',return_value=({('ariaxhan/app',2)},{'ariaxhan/app#2':{'state':'working'}})):
            return inventory.assemble(specs,cache,fresh,errors,'','2026-09-26T00:00:00Z')

    def test_failed_fetch_keeps_last_good_and_says_partial_never_zero(self):
        cache={'ariaxhan/app':{'total':2,'issues':[issue(1),issue(2,['ready'])],'fetched_at':'now'},
               'ariaxhan/b':{'total':5,'issues':[issue(n) for n in range(5)],'fetched_at':'earlier'}}
        result=self.assemble({'ariaxhan/app':cache['ariaxhan/app']},{'ariaxhan/b':'timeout','ariaxhan/c':'inaccessible: no identity'},cache)
        group=result['groups'][0]
        self.assertEqual(group['total'],7)
        self.assertFalse(group['complete'])
        self.assertEqual(group['denominator'],'7 open across 1/3 Tower repos (partial: 1 from last-good, 1 inaccessible)')
        states={row['repo']:(row['status'],row['open']) for row in group['repos']}
        self.assertEqual(states['ariaxhan/b'],('stale (last-good)',5))
        self.assertEqual(states['ariaxhan/c'],('inaccessible',None))

    def test_tower_attempt_is_active_and_unlabeled_is_untriaged(self):
        cache={'ariaxhan/app':{'total':2,'issues':[issue(1),issue(2,['ready'])],'fetched_at':'now'}}
        result=self.assemble(cache,{},cache)
        states={item['number']:item['state'] for item in result['issues']}
        self.assertEqual(states,{1:'untriaged',2:'active'})
        self.assertEqual(result['groups'][0]['oldest']['number'],1)

    def test_a_short_fetch_is_an_error_not_a_count(self):
        node={'issues':{'totalCount':3,'pageInfo':{'hasNextPage':False},'nodes':[]}}
        with self.assertRaises(RuntimeError):inventory.complete('ariaxhan/app',node,'token')


class Bump(unittest.TestCase):
    def assess(self,repo,labels,rows=ROWS,gate=(None,None)):
        with patch.object(inventory,'registry_rows',return_value=(rows,'')),patch.object(inventory._work(),'tower_gate',return_value=gate):
            return bump.assess(repo,{'state':'open','labels':[{'name':n} for n in labels],'body':''},'p0')

    def test_ready_tower_issue_gets_ready_and_priority_replacing_other_priorities(self):
        verdict=self.assess('ariaxhan/app',['bug','p2'])
        self.assertTrue(verdict['eligible'])
        self.assertEqual(verdict['labels_after'],['bug','p0','ready'])

    def test_refusals_carry_towers_reason(self):
        self.assertIn('TBS coordinator',self.assess('thinking-brain-school/tbs',[])['reason'])
        self.assertEqual(self.assess('ariaxhan/off',[])['reason'],'disabled in the Tower registry')
        self.assertTrue(self.assess('ariaxhan/app',['hold'])['reason'].startswith('held'))
        self.assertTrue(self.assess('ariaxhan/app',['claimed'])['reason'].startswith('owned'))
        self.assertTrue(self.assess('ariaxhan/app',['tower-v2'])['eligible'])  # Tower's own label, judged in Tower's lane
        self.assertEqual(self.assess('ariaxhan/app',[],gate=(None,'blocked by open dependency x#1'))['reason'],'blocked by open dependency x#1')

    def test_bump_writes_add_and_remove_never_put(self):
        calls=[]
        def request(endpoint,method,payload,token):
            calls.append((method,endpoint));return {'state':'open','labels':[{'name':'p2'},{'name':'bug'}],'body':''}
        with patch.object(actions,'request',side_effect=request),patch.object(bump,'assess',return_value={'eligible':True}):
            actions.perform('bump',{'repo':'ariaxhan/app','number':4,'priority':'p0','request_id':'r'},'who','token',None)
        self.assertEqual([m for m,_ in calls],['GET','POST','DELETE','GET'])
        self.assertTrue(calls[2][1].endswith('/labels/p2'))

    def test_refused_bump_writes_nothing(self):
        with patch.object(actions,'request',return_value={'state':'open','labels':[]}) as request,patch.object(bump,'assess',return_value={'eligible':False,'reason':'held'}):
            with self.assertRaises(PermissionError):actions.perform('bump',{'repo':'r/x','number':4,'priority':'p0','request_id':'r'},'who','token',None)
        self.assertEqual(request.call_count,1)

    def test_outcomes_are_checked_against_github_state(self):
        current={'title':'T','body':'B','labels':[{'name':'ready'},{'name':'p0'}]}
        self.assertTrue(actions.desired_state({'action':'bump','priority':'p0'},current))
        self.assertFalse(actions.desired_state({'action':'bump','priority':'p1'},current))
        self.assertTrue(actions.desired_state({'action':'label_remove','labels':['hold']},current))
        self.assertTrue(actions.desired_state({'action':'edit','title':'T'},current))
        with self.assertRaises(ValueError):actions.validate({'action':'edit','repo':'r/x','number':1})
        with self.assertRaises(ValueError):actions.validate({'action':'bump','repo':'r/x','number':1,'priority':'p9'})


if __name__=='__main__':
    unittest.main()
