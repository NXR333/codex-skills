"""Behavioral checks for coursework DOCX audit; no third-party packages."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import zipfile
from xml.etree import ElementTree as ET
from validate_docx import Audit, NS, W

TEMPLATE=Path(__file__).resolve().parents[1]/'assets/coursework-template.docx'

class DocxTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        with zipfile.ZipFile(TEMPLATE) as z:self.parts={n:z.read(n) for n in z.namelist()}
        self.doc=ET.fromstring(self.parts['word/document.xml']);self.styles=ET.fromstring(self.parts['word/styles.xml'])
    def body(self):
        return next(p for p in self.doc.findall('.//w:p',NS) if ''.join(n.text or '' for n in p.findall('.//w:t',NS))=='[Основной текст подраздела.]')
    def property(self,parent,tag,**attrs):
        el=ET.SubElement(parent,W+tag)
        for key,value in attrs.items():el.set(W+key,str(value))
        return el
    def pp(self):
        p=self.body();pr=p.find('w:pPr',NS)
        if pr is None:pr=ET.SubElement(p,W+'pPr')
        return pr
    def codes(self,*,full=True,allow=True):
        self.parts['word/document.xml']=ET.tostring(self.doc,encoding='utf-8')
        self.parts['word/styles.xml']=ET.tostring(self.styles,encoding='utf-8')
        path=Path(self.temp.name)/'case.docx'
        with zipfile.ZipFile(path,'w') as z:
            for name,data in self.parts.items():z.writestr(name,data)
        return {f['code'] for f in Audit(path,full=full,allow_placeholders=allow).run()}
    def test_template(self):self.assertEqual(self.codes(),set())
    def test_wrong_margin(self):
        self.doc.find('.//w:pgMar',NS).set(W+'left','1000')
        self.assertIn('margin',self.codes())
    def test_landscape_a4(self):
        n=self.doc.find('.//w:pgSz',NS);a=n.get(W+'w');n.set(W+'w',n.get(W+'h'));n.set(W+'h',a)
        self.assertNotIn('page-size',self.codes())
    def test_non_a4(self):
        self.doc.find('.//w:pgSz',NS).set(W+'w','15000');self.assertIn('page-size',self.codes())
    def test_body_alignment(self):
        self.property(self.pp(),'jc',val='left');self.assertIn('body-alignment',self.codes())
    def test_body_spacing(self):
        self.property(self.pp(),'spacing',line='240',lineRule='auto');self.assertIn('body-spacing',self.codes())
    def test_body_indent(self):
        self.property(self.pp(),'ind',firstLine='0');self.assertIn('body-indent',self.codes())
    def test_partial_property_inherits_spacing(self):
        self.property(self.pp(),'spacing',after='100');self.assertNotIn('body-spacing',self.codes())
    def test_small_body(self):
        pr=ET.SubElement(self.body().find('w:r',NS),W+'rPr');self.property(pr,'sz',val='20')
        self.assertIn('font-size',self.codes())
    def test_small_table_is_allowed(self):
        p=self.doc.find('.//w:tbl//w:p',NS);pr=ET.SubElement(p.find('w:r',NS),W+'rPr');self.property(pr,'sz',val='20')
        self.assertNotIn('font-size',self.codes())
    def test_direct_font_override(self):
        pr=ET.SubElement(self.body().find('w:r',NS),W+'rPr');self.property(pr,'rFonts',ascii='Arial',hAnsi='Arial')
        self.assertIn('font',self.codes())
    def test_direct_color(self):
        pr=ET.SubElement(self.body().find('w:r',NS),W+'rPr');self.property(pr,'color',val='FF0000')
        self.assertIn('text-color',self.codes())
    def test_long_dash_and_hyphen(self):
        self.body().find('w:r/w:t',NS).text='Тест-кейс — пример'
        self.assertIn('em-dash',self.codes())
    def test_en_dash_and_hyphen(self):
        self.body().find('w:r/w:t',NS).text='Тест-кейс – пример'
        self.assertNotIn('em-dash',self.codes())
    def test_code_is_exempt(self):
        p=self.body();self.property(self.pp(),'pStyle',val='CWCode');p.find('w:r/w:t',NS).text='code — sample'
        self.assertNotIn('em-dash',self.codes());self.assertNotIn('font',self.codes())
    def test_missing_reference(self):
        n=next(n for n in self.doc.findall('.//w:instrText',NS) if 'REF TableExample' in (n.text or ''))
        n.text=' REF MissingBookmark '
        self.assertIn('reference-missing',self.codes())
    def test_shading(self):
        pr=self.doc.find('.//w:tbl/w:tblPr',NS);self.property(pr,'shd',val='clear',fill='FFFFFF')
        self.assertIn('table-shading',self.codes())
    def test_service_table(self):
        pr=self.doc.find('.//w:tbl/w:tblPr',NS);self.property(pr,'tblCaption',val='cw:service');self.property(pr,'shd',val='clear',fill='FFFFFF')
        self.assertNotIn('table-shading',self.codes())
    def test_draft_placeholders(self):
        self.assertIn('placeholder',self.codes(allow=False))
    def test_missing_toc_full_only(self):
        for n in self.doc.findall('.//w:instrText',NS):
            if 'TOC ' in (n.text or ''):n.text=''
        self.assertIn('field-missing',self.codes());self.assertNotIn('field-missing',self.codes(full=False))

if __name__=='__main__':unittest.main()
