from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'client'))
import office_search_inventory as inventory

class Inventory(unittest.TestCase):
    def test_empty_missing_and_failed_are_distinct(self):
        sources=[{'source':name,'kind':'file','paths':['/'+name],'available':available} for name,available in [('empty',True),('missing',False),('failed',True)]]
        result=inventory.complete([],sources,{'finished_at':12,'errors':[{'source':'/failed/private','error':'denied'}]})
        self.assertEqual([r['state'] for r in result],['empty','unavailable','error'])
        self.assertEqual([r['total'] for r in result],[0,None,None])
        self.assertTrue(all(r['observed_at']==12 for r in result))

    def test_partial_read_does_not_become_complete_or_duplicate_group(self):
        rows=[{'source':'repo','kind':'file','indexed':2,'state':'indexed'}]
        sources=[{'source':'repo','kind':'file','paths':['/repo'],'available':True}]
        result=inventory.complete(rows,sources,{'finished_at':12,'errors':[{'source':'repo','error':'denied'}]})
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['state'],'partial')
        self.assertEqual(rows[0]['state'],'indexed')

    def test_failed_media_item_is_not_an_empty_catalog(self):
        source={'source':'Library','kind':'podcast','paths':['/manifest.json'],'available':True}
        result=inventory.complete([], [source], {'finished_at':12,'errors':[{'source':'podcast:broken','error':'missing text'}]})
        self.assertEqual(result[0]['state'],'error');self.assertIsNone(result[0]['total'])

    def test_unbuilt_is_never_presented_as_empty(self):
        result=inventory.unbuilt([{'source':'Library','kind':'podcast'}])[0]
        self.assertEqual(result['state'],'unbuilt');self.assertIsNone(result['total']);self.assertIsNone(result['observed_at'])
