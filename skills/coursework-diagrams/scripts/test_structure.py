"""Regression tests for common Draw.io structure and explicit styling."""
from pathlib import Path
from copy import deepcopy
import unittest
from drawio_builder import Diagram
from validate_drawio import Page, read_pages

class StructureTests(unittest.TestCase):
    def make(self):
        d=Diagram('page','page',500,300)
        d.vertex('box','Example',(20,20,150,80))
        return Page('page', d.page.find('mxGraphModel'))
    def errors(self,p):
        p.structural();p.monochrome()
        return '\n'.join(p.errors)
    def test_standard_diagram(self):self.assertEqual(self.errors(self.make()),'')
    def test_zero_and_negative_sizes(self):
        for value in ['0','-1','nan','inf']:
            with self.subTest(value=value):
                p=self.make();p.cells['box'][1].find('mxGeometry').set('width',value)
                self.assertIn('width must be positive',self.errors(p))
    def test_missing_height(self):
        p=self.make();del p.cells['box'][1].find('mxGeometry').attrib['height']
        self.assertIn('height must be positive',self.errors(p))
    def test_parent_self_cycle(self):
        p=self.make();p.cells['box'][1].set('parent','box')
        self.assertIn('cyclic parent',self.errors(p))
    def test_two_parent_cycle(self):
        p=self.make();c=deepcopy(p.cells['box'][1]);c.set('id','other');c.set('parent','box')
        p.cells['other']=(c,c);p.cells['box'][1].set('parent','other')
        self.assertIn('cyclic parent',self.errors(p))
    def test_colored_and_gray_styles(self):
        for style in ['fillColor=#ff0000;','strokeColor=#888888;','fontColor=blue;', 'strokeColor=#6F6F6;']:
            with self.subTest(style=style):
                p=self.make();c=p.cells['box'][1];c.set('style',c.get('style')+style)
                self.assertIn('non-monochrome',self.errors(p))
    def test_shadows_and_gradients(self):
        for style in ['shadow=1;','gradientColor=#000000;']:
            with self.subTest(style=style):
                p=self.make();c=p.cells['box'][1];c.set('style',c.get('style')+style)
                self.assertTrue(self.errors(p))
    def test_allowed_black_symbol(self):
        p=self.make();c=p.cells['box'][1];c.set('style',c.get('style')+'fillColor=black;')
        self.assertEqual(self.errors(p),'')
    def test_inline_text_color(self):
        p=self.make();p.cells['box'][1].set('value','<font color="#FF0000">Bad</font>')
        self.assertIn('non-black HTML text',self.errors(p))
    def test_inline_white_background_is_not_text_color(self):
        p=self.make();p.cells['box'][1].set('value','<span style="background-color:#ffffff;color:#000000">OK</span>')
        self.assertEqual(self.errors(p),'')
    def test_page_background(self):
        p=self.make();p.model.set('background','#cccccc')
        self.assertIn('page background',self.errors(p))
    def test_all_examples(self):
        root=Path(__file__).resolve().parents[1]/'assets'
        for filename in ['repair-requests.drawio','bpmn-example.drawio']:
            for name,model in read_pages(root/filename):
                with self.subTest(file=filename,page=name):
                    self.assertEqual(self.errors(Page(name,model)),'')

if __name__=='__main__':unittest.main()
