import unittest
from nexus.podcast_quality import alignment
from nexus.podcast_render import passages

class Quality(unittest.TestCase):
    def test_missing_contiguous_sentence_fails_despite_high_total_coverage(self):
        tokens=['word'+str(i) for i in range(1000)]
        editorial={'chapters':[{'title':'Story','text':' '.join(tokens)}]}
        recognized=[{'word':word,'start':i} for i,word in enumerate(tokens) if not 300<=i<320]
        self.assertFalse(alignment(editorial,recognized)['passed'])

    def test_verified_chapters_align_to_recorded_audio(self):
        editorial={'chapters':[{'title':'One','text':'a b c'},{'title':'Two','text':'d e f'}]}
        recognized=[{'word':word,'start':i*2} for i,word in enumerate('abcdef')]
        result=alignment(editorial,recognized)
        self.assertTrue(result['passed'])
        self.assertEqual([r['start_s'] for r in result['chapters']],[0,6])

    def test_chunking_preserves_complete_paragraphs_and_words(self):
        text=' '.join(['first']*90)+'\n\n'+' '.join(['second']*80)+'\n\nFinal thought.'
        self.assertEqual('\n\n'.join(passages(text)),text)
