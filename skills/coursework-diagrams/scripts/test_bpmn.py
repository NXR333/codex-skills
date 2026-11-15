"""Regression checks for the basic BPMN profile; no external dependencies."""
from copy import deepcopy
from pathlib import Path
import unittest
from validate_drawio import Page, read_pages

SOURCE = Path(__file__).resolve().parents[1] / 'assets' / 'bpmn-example.drawio'

class BPMNTests(unittest.TestCase):
    def make(self):
        name, model = read_pages(SOURCE)[0]
        return Page(name, deepcopy(model))
    def checked(self, page):
        page.structural(); page.profile('bpmn')
        return '\n'.join(page.errors)
    def test_examples(self):
        for name, model in read_pages(SOURCE):
            with self.subTest(page=name):self.assertEqual(self.checked(Page(name, model)), '')
    def test_cross_pool_sequence(self):
        p=self.make();p.cells['check'][1].set('parent','client')
        self.assertIn('sequence flow cannot cross pools',self.checked(p))
    def test_message_inside_pool(self):
        p=self.make();p.cells['m1'][1].set('source','register')
        self.assertIn('message flow must connect different pools',self.checked(p))
    def test_start_incoming(self):
        p=self.make();p.cells['s2'][1].set('target','start')
        self.assertIn('start needs no incoming',self.checked(p))
    def test_disconnected_node(self):
        p=self.make();p.cells.pop('s5')
        self.assertIn('not reachable from a start',self.checked(p))
    def test_wrong_line_style(self):
        p=self.make();c=p.cells['s1'][1];c.set('style',c.get('style')+'dashed=1;')
        self.assertIn('sequence flow needs solid',self.checked(p))
    def test_unclassified_edge(self):
        p=self.make();p.cells['s1'][1].attrib.pop('data-role')
        self.assertIn('label every BPMN connection',self.checked(p))
    def test_unknown_element(self):
        p=self.make();p.cells['register'][1].set('data-role','bpmn-subprocess')
        self.assertIn('unsupported BPMN role',self.checked(p))
    def test_condition_required(self):
        p=self.make();p.cells['s3'][1].set('value','')
        self.assertIn('conditional branch needs label',self.checked(p))
    def test_or_structure_is_supported(self):
        p=self.make();p.cells['decision'][1].set('data-role','bpmn-or')
        # Basic structure accepts OR; token synchronization remains a manual check.
        self.assertEqual(self.checked(p),'')
    def test_missing_pool(self):
        p=self.make();p.cells['manager'][1].set('parent','1')
        self.assertIn('must belong to a pool',self.checked(p))

    def test_message_gateway_rejected(self):
        p=self.make();p.cells['m1'][1].set('target','decision')
        self.assertIn('invalid message endpoint',self.checked(p))
    def test_start_cannot_send(self):
        p=self.make();p.cells['m2'][1].set('source','start')
        self.assertIn('start cannot send',self.checked(p))
    def test_end_cannot_receive(self):
        p=self.make();p.cells['m1'][1].set('target','end')
        self.assertIn('end cannot receive',self.checked(p))
    def test_message_event_requires_metadata(self):
        name,model=read_pages(SOURCE)[3];p=Page(name,deepcopy(model))
        p.cells['receive'][1].attrib.pop('data-event')
        self.assertIn('message event needs',self.checked(p))
    def test_timer_cannot_receive_message(self):
        name,model=read_pages(SOURCE)[3];p=Page(name,deepcopy(model))
        p.cells['m2'][1].set('target','timer')
        self.assertIn('message event needs',self.checked(p))
    def test_multiple_starts_allowed(self):
        p=self.make()
        c=deepcopy(p.cells['start'][1]);c.set('id','second-start')
        p.cells['second-start']=(c,c)
        e=deepcopy(p.cells['s1'][1]);e.set('id','second-entry');e.set('source','second-start')
        p.cells['second-entry']=(e,e)
        self.assertEqual(self.checked(p),'')

if __name__ == '__main__':unittest.main()
