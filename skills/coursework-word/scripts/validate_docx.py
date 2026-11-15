"""Read-only DOCX checks for the coursework profile; Python 3.10+, standard library.
Checks explicit/inherited OOXML properties, never pagination or visual correctness.
"""
import argparse
import json
from pathlib import Path
import re
import zipfile
from xml.etree import ElementTree as ET

NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
W = '{' + NS['w'] + '}'

def val(node, attr='val'):
    return node.get(W+attr) if node is not None else None

def props(node):
    return {x.tag.removeprefix(W): dict(x.attrib) for x in node} if node is not None else {}

def merge(base, override):
    for name, attributes in override.items():
        base[name] = {**base.get(name, {}), **attributes}
    return base

def get(p, name, attr='val'):
    return p.get(name, {}).get(W+attr)

def enabled(value):
    return value is not None and value not in {'0', 'false', 'off'}

class Audit:
    def __init__(self, path, *, full=False, allow_placeholders=False):
        self.findings=[]; self.full=full; self.allow_placeholders=allow_placeholders
        with zipfile.ZipFile(path) as z:
            self.document=ET.fromstring(z.read('word/document.xml'))
            self.styles=ET.fromstring(z.read('word/styles.xml'))
            self.parts={'document':self.document}
            for name in z.namelist():
                if re.fullmatch(r'word/(?:header|footer)\d+\.xml',name):
                    self.parts[name]=ET.fromstring(z.read(name))
        self.stylemap={val(s,'styleId'):s for s in self.styles.findall('w:style',NS)}
        self.parent={child:parent for parent in self.document.iter() for child in parent}
        self.defaults={k:props(self.styles.find(f'w:docDefaults/w:{k}Default/w:{k}',NS)) for k in ['pPr','rPr']}
        self.default_p=next((k for k,s in self.stylemap.items() if s.get(W+'type')=='paragraph' and s.get(W+'default')=='1'),None)

    def add(self, code, location, message, severity='error'):
        item={'severity':severity,'code':code,'location':location,'message':message}
        if item not in self.findings:self.findings.append(item)

    def inherited(self, style_id, kind):
        chain=[];seen=set()
        while style_id in self.stylemap and style_id not in seen:
            seen.add(style_id);style=self.stylemap[style_id];chain.append(style)
            style_id=val(style.find('w:basedOn',NS))
        result={}
        for style in reversed(chain):merge(result,props(style.find('w:'+kind,NS)))
        return result

    def paragraph(self,p):
        direct=props(p.find('w:pPr',NS));style=get(direct,'pStyle') or self.default_p
        pp=dict(self.defaults['pPr']);merge(pp,self.inherited(style,'pPr'));merge(pp,direct)
        rp=dict(self.defaults['rPr']);merge(rp,self.inherited(style,'rPr'))
        return style,pp,rp

    def in_table(self,p):
        node=p
        while node in self.parent:
            node=self.parent[node]
            if node.tag==W+'tbl':return node
        return None

    def run(self):
        for i,s in enumerate(self.document.findall('.//w:sectPr',NS),1):
            where=f'section {i}';size=s.find('w:pgSz',NS);margin=s.find('w:pgMar',NS)
            try:
                dimensions=sorted([int(val(size,'w')),int(val(size,'h'))])
                if any(abs(a-b)>10 for a,b in zip(dimensions,[11906,16838])):self.add('page-size',where,'Expected A4 in portrait or landscape orientation')
            except (TypeError,ValueError):self.add('page-size',where,'Page size is absent or invalid','warning')
            for field,target,minimum in [('left',1701,False),('right',850,False),('top',1134,True),('bottom',1134,True)]:
                try:
                    value=int(val(margin,field))
                    if (value<target-5 if minimum else abs(value-target)>5):self.add('margin',where,f'Unexpected {field} margin ({value} twips)')
                except (TypeError,ValueError):self.add('margin',where,f'Cannot resolve {field} margin','warning')
            if i>1 and val(s.find('w:pgNumType',NS),'start') is not None:self.add('page-restart',where,'Section explicitly restarts page numbering; confirm continuity','warning')
        bookmarks={}
        for root in self.parts.values():
            for b in root.findall('.//w:bookmarkStart',NS):
                name=val(b,'name')
                if name:bookmarks[name]=bookmarks.get(name,0)+1
        for name,count in bookmarks.items():
            if count>1:self.add('bookmark-duplicate','document',f'Duplicate bookmark {name}')
        instructions=[]
        for part,root in self.parts.items():
            for p in root.findall('.//w:p',NS):
                instructions.append(''.join(n.text or '' for n in p.findall('.//w:instrText',NS)))
            instructions.extend(val(n,'instr') or '' for n in root.findall('.//w:fldSimple',NS))
        for instruction in instructions:
            for name in re.findall(r'\b(?:REF|PAGEREF)\s+"?([^\s"\\]+)',instruction,re.I):
                if name not in bookmarks:self.add('reference-missing','document',f'Field points to absent bookmark {name}')
        allfields=' '.join(instructions)
        if self.full:
            for field in ['PAGE','TOC']:
                if not re.search(r'\b'+field+r'\b',allfields,re.I):self.add('field-missing','document',f'Missing {field} field')
            first=self.document.find('.//w:sectPr',NS)
            if first is not None and first.find('w:titlePg',NS) is None:self.add('title-page','section 1','No different first-page header/footer setting','warning')
        for i,p in enumerate(self.document.findall('.//w:p',NS),1):
            text=''.join(n.text or '' for n in p.findall('.//w:t',NS))
            if not text.strip():continue
            where=f'paragraph {i}';style,pp,base_rp=self.paragraph(p);sid=((style or '')+' '+str(val(self.stylemap[style].find('w:name',NS)) if style in self.stylemap else '')).lower()
            table=self.in_table(p)
            special=any(x in sid for x in ['title','toc','caption','structural','service','placeholder','bibliography'])
            heading=get(pp,'outlineLvl') is not None or bool(re.match(r'heading\d',sid))
            code='code' in sid or 'код' in sid
            body=table is None and not special and not heading and not code
            if not self.allow_placeholders and re.search(r'\[[^\[\]\n]{2,100}\]',text):self.add('placeholder',where,'Review bracketed text: possible unfilled placeholder','warning')
            if not code:
                if '\u2014' in text:self.add('em-dash',where,'Use U+2013 for authored dashes; inspect quotations and technical strings','warning')
                if any(x in text.lower() for x in ['таким образом','в настоящее время']):self.add('wording',where,'Review discouraged phrase in authored text','warning')
            if body:
                for property,expected in [('jc','both')]:
                    if get(pp,property)!=expected:self.add('body-alignment',where,'Body text must be justified')
                try:
                    if abs(int(get(pp,'ind','firstLine') or 0)-709)>8:self.add('body-indent',where,'Expected first-line indent 1.25 cm')
                except ValueError:self.add('body-indent',where,'Cannot resolve paragraph indent','warning')
                if get(pp,'spacing','line')!='360' or get(pp,'spacing','lineRule') not in {None,'auto'}:self.add('body-spacing',where,'Expected 1.5 line spacing')
            for run in p.findall('.//w:r',NS):
                if not ''.join(x.text or '' for x in run.findall('w:t',NS)).strip():continue
                rp=dict(base_rp);direct=props(run.find('w:rPr',NS))
                merge(rp,self.inherited(get(direct,'rStyle'),'rPr'));merge(rp,direct)
                color=get(rp,'color')
                if color and color.lower() not in {'000000','auto'}:self.add('text-color',where,'Text color must be black')
                if get(rp,'color','themeColor'):self.add('theme-color',where,'Theme color needs explicit black formatting','warning')
                if not code:
                    font=get(rp,'rFonts','ascii') or get(rp,'rFonts','hAnsi')
                    if font and font!='Liberation Serif':self.add('font',where,f'Unexpected font {font}')
                    elif not font:self.add('font-unresolved',where,'Font requires theme/default resolution','warning')
                    # Tables, captions, title/TOC and service parts may use task-specific sizes.
                    size=get(rp,'sz')
                    if body or heading:
                        try:
                            if float(size)<24:self.add('font-size',where,'Main text/headings require at least 12 pt')
                        except (TypeError,ValueError):self.add('font-size',where,'Cannot resolve font size','warning')
                if (body or table is not None) and 'b' in rp and get(rp,'b') not in {'0','false','off'}:
                    self.add('bold',where,'Review bold body/table text; special notation may be justified','warning')
            if 'caption' in sid and not re.search(r'\bSEQ\b',''.join(n.text or '' for n in p.findall('.//w:instrText',NS))+' '.join(val(n,'instr') or '' for n in p.findall('.//w:fldSimple',NS)),re.I):
                self.add('caption-field',where,'Caption has no automatic SEQ field','warning')
        for i,t in enumerate(self.document.findall('.//w:tbl',NS),1):
            where=f'table {i}';pr=t.find('w:tblPr',NS)
            if val(pr.find('w:tblCaption',NS) if pr is not None else None)=='cw:service':continue
            style_id=val(pr.find('w:tblStyle',NS) if pr is not None else None)
            style=self.stylemap.get(style_id)
            roots=[t]+([style] if style is not None else [])
            for root in roots:
                for shd in root.findall('.//w:shd',NS):
                    if val(shd,'fill') not in {None,'auto'} or val(shd) not in {None,'clear','nil'}:
                        self.add('table-shading',where,'Direct or table-style shading must be removed')
            borders=pr.find('w:tblBorders',NS) if pr is not None else None
            if borders is None and style is not None:borders=style.find('w:tblPr/w:tblBorders',NS)
            if borders is None:self.add('table-borders',where,'Cannot resolve table grid; inspect inherited and cell borders','warning')
            else:
                sizes=set()
                for border in borders:
                    if val(border) not in {'single','nil','none'}:self.add('table-borders',where,'Expected single black borders','warning')
                    if val(border,'color') not in {None,'000000','auto'}:self.add('table-borders',where,'Border color must be black')
                    if val(border,'sz'):sizes.add(val(border,'sz'))
                if len(sizes)>1:self.add('table-borders',where,'Nonuniform border widths','warning')
        return self.findings

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file',type=Path);parser.add_argument('--full',action='store_true',help='Require PAGE and TOC for a complete report')
    parser.add_argument('--allow-placeholders',action='store_true',help='Template/draft mode')
    parser.add_argument('--json',action='store_true')
    args=parser.parse_args()
    try:findings=Audit(args.file,full=args.full,allow_placeholders=args.allow_placeholders).run()
    except (OSError,KeyError,ValueError,ET.ParseError,zipfile.BadZipFile) as exc:
        parser.exit(2,f'Cannot inspect DOCX: {exc}\n')
    if args.json:print(json.dumps(findings,ensure_ascii=False,indent=2))
    else:
        for f in findings:print(f"{f['severity'].upper()} [{f['code']}] {f['location']}: {f['message']}")
        print(f"{sum(f['severity']=='error' for f in findings)} error(s), {sum(f['severity']=='warning' for f in findings)} warning(s); visual review still required")
    return int(any(f['severity']=='error' for f in findings))
if __name__=='__main__':raise SystemExit(main())
