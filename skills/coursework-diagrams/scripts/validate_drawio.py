"""Check Draw.io structure and optional coursework notation profiles."""

import argparse
import base64
from html import unescape
from math import isfinite
from pathlib import Path
import re
from urllib.parse import unquote
from xml.etree import ElementTree as ET
import zlib


KINDS = {'idef-context', 'idef-decomp', 'dfd', 'dfd-gane-sarson', 'sequence', 'use-case'}
PREFIX = re.compile(r'^[IOCM]\d+\b[\s.:–-]*', re.IGNORECASE)
ACTIVITY = re.compile(r'[AА]\d+')
PROCESS = re.compile(r'p\d+(?:[._]\d+)*')
NUMBER = re.compile(r'^(\d+(?:\.\d+)*)(?:\s|$)')


def normalized(value):
    return ' '.join(unescape(re.sub(r'<[^>]+>', ' ', value)).split())


def read_pages(path):
    document = ET.parse(path).getroot()
    if document.tag == 'mxGraphModel':
        return [('Page 1', document)]
    if document.tag != 'mxfile':
        raise ValueError('Expected mxfile or mxGraphModel')
    pages = []
    for diagram in document.findall('diagram'):
        name = diagram.get('name') or f'Page {len(pages)+1}'
        model = diagram.find('mxGraphModel')
        if model is None:
            payload = (diagram.text or '').strip()
            if not payload:
                raise ValueError(f'{name}: missing page model')
            xml = payload if payload.startswith('<') else unquote(
                zlib.decompress(base64.b64decode(payload), -15).decode('utf-8'))
            model = ET.fromstring(xml)
        if model.tag != 'mxGraphModel':
            raise ValueError(f'{name}: expected mxGraphModel')
        pages.append((name, model))
    if not pages:
        raise ValueError('The workbook has no pages')
    return pages


class Page:
    def __init__(self, name, model):
        self.name = name
        self.cells = {}
        self.errors = []
        root = model.find('root')
        if root is None:
            self.fail('missing root')
            return
        for node in root:
            cell = node if node.tag == 'mxCell' else node.find('mxCell')
            if cell is None:
                self.fail(f'unsupported root child {node.tag}')
                continue
            key = node.get('id') or cell.get('id')
            if not key or key in self.cells:
                self.fail(f'missing or repeated cell ID {key!r}')
                continue
            self.cells[key] = (node, cell)

    def fail(self, message):
        self.errors.append(f'{self.name}: {message}')

    @property
    def edges(self):
        return {k:c for k,(_,c) in self.cells.items() if c.get('edge') == '1'}

    def role(self, key):
        node, cell = self.cells[key]
        return node.get('data-role') or cell.get('data-role')

    def style(self, key):
        value = self.cells[key][1].get('style', '')
        return dict(piece.split('=', 1) for piece in value.split(';') if '=' in piece)

    def label(self, key):
        node, cell = self.cells[key]
        return normalized(node.get('label', cell.get('value', '')))

    def edge_label(self, key):
        return self.label(key) or ' '.join(self.label(k) for k,(_,c) in self.cells.items()
                                          if c.get('parent') == key and c.get('vertex') == '1').strip()

    def structural(self):
        if not self.cells:
            self.fail('empty model')
        for key, (_, cell) in self.cells.items():
            for attr in ('parent', 'source', 'target'):
                other = cell.get(attr)
                if other and other not in self.cells:
                    self.fail(f'{key}: unknown {attr} {other!r}')
            if cell.get('edge') != '1' and cell.get('vertex') != '1':
                continue
            geometry = cell.find('mxGeometry')
            if geometry is None:
                self.fail(f'{key}: missing geometry')
                continue
            for point in geometry.iter():
                for field in ('x', 'y', 'width', 'height'):
                    value = point.get(field)
                    if value is None:
                        continue
                    try:
                        number = float(value)
                        if not isfinite(number) or (field in ('width','height') and number < 0):
                            raise ValueError
                    except ValueError:
                        self.fail(f'{key}: invalid {field} {value!r}')
            if cell.get('edge') == '1':
                for end in ('source','target'):
                    if not cell.get(end) and geometry.find(f'mxPoint[@as="{end}Point"]') is None:
                        self.fail(f'{key}: missing {end} cell or point')

    def profile(self, kind):
        if kind.startswith(('idef-', 'dfd')):
            for key in self.edges:
                if PREFIX.match(self.edge_label(key)):
                    self.fail(f'{key}: unexpected flow prefix in {self.edge_label(key)!r}')
        if kind.startswith('idef-'):
            self.idef(kind == 'idef-context')
        if kind.startswith('dfd'):
            self.dfd(kind == 'dfd-gane-sarson')
        if kind == 'sequence':
            self.sequence()
        if kind == 'use-case':
            self.use_case()

    def idef(self, context):
        works = {k for k,(_,c) in self.cells.items() if c.get('vertex') == '1'
                 and (self.role(k) == 'work' or ACTIVITY.fullmatch(k))}
        if not works or (context and len(works) != 1):
            self.fail('identify each work with data-role=work or an A-number cell ID')
        for work in works:
            incoming = [self.style(k) for k,e in self.edges.items() if e.get('target') == work]
            outgoing = [self.style(k) for k,e in self.edges.items() if e.get('source') == work]
            if not any(on_side(s,'entryY',0) for s in incoming):
                self.fail(f'{work}: missing control on the top side')
            for style in incoming:
                if not any(on_side(style,p,v) for p,v in [('entryX',0),('entryY',0),('entryY',1)]):
                    self.fail(f'{work}: set an entry port on the left, top or bottom')
            if not outgoing or any(not on_side(s,'exitX',1) for s in outgoing):
                self.fail(f'{work}: outputs must depart from the right')
        if context and any(not self.edge_label(k) for k in self.edges):
            self.fail('all context arrows need a name')

    def processes(self):
        return {k for k,(_,c) in self.cells.items() if c.get('vertex') == '1'
                and (self.role(k) == 'process' or PROCESS.fullmatch(k))}

    def number(self, key):
        labels = [self.label(key)]
        labels.extend(self.label(k) for k,(_,c) in self.cells.items() if c.get('parent') == key)
        return next((match[1] for label in labels if (match := NUMBER.match(label))), None)

    def dfd(self, gane_sarson):
        processes = self.processes()
        if not processes:
            self.fail('identify DFD processes with data-role=process or IDs such as p1')
        for key, e in self.edges.items():
            if not self.edge_label(key):
                self.fail(f'{key}: unnamed DFD flow')
            if e.get('source') not in processes and e.get('target') not in processes:
                self.fail(f'{key}: a DFD flow needs a process at one end')
            if self.style(key).get('endArrow') == 'none' and self.style(key).get('startArrow','none') == 'none':
                self.fail(f'{key}: DFD flow has no direction')
        for process in processes:
            if not any(e.get('source') == process for e in self.edges.values()):
                self.fail(f'{process}: no outgoing data')
            if not any(e.get('target') == process for e in self.edges.values()):
                self.fail(f'{process}: no incoming data')
        if gane_sarson:
            self.gane_sarson(processes)

    def gane_sarson(self, processes):
        endpoints = {edge.get(side) for edge in self.edges.values() for side in ('source', 'target')}
        for key in (endpoints | processes) & self.cells.keys():
            style = self.style(key)
            tokens = self.cells[key][1].get('style', '').split(';')
            shape = style.get('shape') or next((t for t in tokens if t and '=' not in t), 'rectangle')
            if key in processes:
                if style.get('rounded') != '1' or shape not in ('rectangle', 'rect'):
                    self.fail(f'{key}: Gane–Sarson process must use a rounded rectangle')
            elif self.role(key) == 'store' or shape == 'partialRectangle':
                if shape != 'partialRectangle' or style.get('right') != '0':
                    self.fail(f'{key}: expected a right-open native store')
            elif shape not in ('rectangle', 'rect') or style.get('rounded') == '1':
                self.fail(f'{key}: Gane–Sarson external entity must use a rectangle')
            if key in processes or self.role(key) == 'store' or shape == 'partialRectangle':
                if self.number(key) is None:
                    self.fail(f'{key}: missing visible numeric identifier')
            elif re.match(r'^E\d+\b', self.label(key)):
                self.fail(f'{key}: external entity should use its name without an E-number')

    def sequence(self):
        lifelines = [k for k,e in self.edges.items()
                     if self.style(k).get('dashed') == '1' and not self.edge_label(k)
                     and self.style(k).get('endArrow') == 'none']
        if len(lifelines) < 2:
            self.fail('expected at least two dashed lifelines without arrowheads')
        if not any(self.edge_label(k) for k in self.edges):
            self.fail('expected named messages')

    def use_case(self):
        for key, edge in self.edges.items():
            label = self.edge_label(key).strip('«»<> ').lower()
            if label not in ('include','extend'):
                continue
            ends = [edge.get('source'),edge.get('target')]
            for end in ends:
                if end not in self.cells or not (
                    self.style(end).get('shape') == 'ellipse'
                    or self.cells[end][1].get('style','').startswith('ellipse;')
                    or ';ellipse;' in self.cells[end][1].get('style','')
                ):
                    self.fail(f'{key}: {label} must connect use cases')
            if self.style(key).get('dashed') != '1' or self.style(key).get('endArrow','none') == 'none':
                self.fail(f'{key}: {label} must be dashed and directed')


def on_side(style, port, value):
    try:
        return abs(float(style.get(port, 'nan'))-value) < 1e-6
    except ValueError:
        return False


def pair(value, separator):
    parts = value.split(separator, 1)
    if len(parts) != 2:
        raise ValueError(f'Expected two values separated by {separator!r}: {value!r}')
    return parts


def select(pages, number):
    index = int(number)
    if index < 1 or index > len(pages):
        raise ValueError(f'Page index {index} outside 1–{len(pages)}')
    return pages[index-1]


def check_dfd_levels(pages):
    """Check the three-level decomposition profile from section 8."""
    sheets = []
    number_pages = {}
    for page in pages:
        processes = page.processes()
        if not 3 <= len(processes) <= 9:
            page.fail(f'expected 3–9 DFD processes, found {len(processes)}')
        terminals = {e.get(side) for e in page.edges.values() for side in ('source', 'target')}
        blocks = processes | {key for key, (_, cell) in page.cells.items()
                              if cell.get('vertex') == '1' and
                              (key in terminals or page.role(key) in ('store', 'external'))}
        if not 3 <= len(blocks) <= 9:
            page.fail(f'expected 3–9 DFD model blocks, found {len(blocks)}')
        numbers = []
        for process in sorted(processes):
            number = page.number(process)
            if number is None:
                page.fail(f'{process}: a visible process number is required for the level check')
                continue
            if number in number_pages:
                page.fail(f'repeated DFD process number {number}')
            number_pages[number] = page
            numbers.append(number)
        depths = {number.count('.') + 1 for number in numbers}
        if len(depths) != 1:
            page.fail('expected process numbers of a single depth on the page')
            continue
        depth = depths.pop()
        parents = {number.rpartition('.')[0] for number in numbers}
        if len(parents) != 1:
            page.fail('process numbers must share a single parent on the page')
            continue
        sheets.append((page, depth, parents.pop()))

    depths = {depth for _, depth, _ in sheets}
    if depths != {1, 2, 3}:
        pages[0].fail(f'expected DFD decomposition levels 1, 2 and 3, found {sorted(depths)}')
    expanded = set()
    for page, depth, parent in sheets:
        if parent in expanded:
            page.fail(f'repeated decomposition sheet for {parent or "level 1"}')
        expanded.add(parent)
        if depth > 1 and parent not in number_pages:
            page.fail(f'parent process {parent} is missing on the previous level')


def check_idef_levels(pages):
    """Check three decomposition depths and their parent works from section 7."""
    works_by_number = {}
    sheets = []
    for page in pages:
        works = [key for key, (_, cell) in page.cells.items()
                 if cell.get('vertex') == '1'
                 and (page.role(key) == 'work' or ACTIVITY.fullmatch(key))]
        if not 3 <= len(works) <= 9:
            page.fail(f'expected 3–9 IDEF0 works, found {len(works)}')
        numbers = []
        for key in works:
            labels = [key, page.label(key)]
            labels.extend(page.label(child) for child, (_, cell) in page.cells.items()
                          if cell.get('parent') == key)
            number = next((value.replace('А', 'A') for value in labels
                           if re.fullmatch(r'[AА][1-9]{1,3}', value)), None)
            if number is None:
                page.fail(f'{key}: expected work number such as A1, A31 or A321')
                continue
            if number in works_by_number:
                page.fail(f'repeated IDEF0 work number {number}')
            works_by_number[number] = page
            numbers.append(number)
        depths = {len(number)-1 for number in numbers}
        parents = {number[:-1] for number in numbers}
        if len(depths) != 1 or len(parents) != 1:
            page.fail('IDEF0 works must share one depth and one parent on the page')
            continue
        sheets.append((page, depths.pop(), parents.pop()))
    depths = {depth for _, depth, _ in sheets}
    if depths != {1, 2, 3}:
        pages[0].fail(f'expected IDEF0 decomposition levels 1, 2 and 3, found {sorted(depths)}')
    expanded = set()
    for page, depth, parent in sheets:
        if parent in expanded:
            page.fail(f'repeated decomposition sheet for {parent}')
        expanded.add(parent)
        if depth > 1 and parent not in works_by_number:
            page.fail(f'parent IDEF0 work {parent} is missing on the previous level')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path)
    parser.add_argument('--kind', action='append', default=[], metavar='PAGE=PROFILE',
                        help='Page index is 1-based. Profiles: '+', '.join(sorted(KINDS)))
    parser.add_argument('--balance', action='append', default=[], metavar='PARENT:CHILD',
                        help='Check names of parent boundary arrows on a child page')
    parser.add_argument('--dfd-levels', metavar='PAGES',
                        help='Section 8 check on comma-separated decomposition page indices')
    parser.add_argument('--idef-levels', metavar='PAGES',
                        help='Section 7 check on comma-separated IDEF0 decomposition page indices')
    args = parser.parse_args()
    try:
        pages = [Page(name, model) for name, model in read_pages(args.file)]
        for page in pages:
            page.structural()
        for spec in args.kind:
            index, kind = pair(spec,'=')
            if kind not in KINDS:
                raise ValueError(f'Unknown notation profile {kind!r}')
            select(pages,index).profile(kind)
        for spec in args.balance:
            a,b = pair(spec,':')
            parent, child = select(pages,a), select(pages,b)
            labels = {parent.edge_label(k) for k,e in parent.edges.items()
                      if not e.get('source') or not e.get('target')} - {''}
            available = {child.edge_label(k) for k in child.edges}
            for missing in sorted(labels-available):
                child.fail(f'no flow named {missing!r} from parent {parent.name!r}')
        if args.dfd_levels is not None:
            dfd_pages = [select(pages,index) for index in args.dfd_levels.split(',')]
            if len(set(dfd_pages)) != len(dfd_pages):
                raise ValueError('DFD decomposition page indices must be unique')
            check_dfd_levels(dfd_pages)
        if args.idef_levels is not None:
            idef_pages = [select(pages,index) for index in args.idef_levels.split(',')]
            if len(set(idef_pages)) != len(idef_pages):
                raise ValueError('IDEF0 decomposition page indices must be unique')
            check_idef_levels(idef_pages)
    except (OSError, ValueError, ET.ParseError, zlib.error) as error:
        parser.exit(1, f'Invalid Draw.io file or arguments: {error}\n')
    errors = [error for page in pages for error in page.errors]
    for error in errors:
        print(f'ERROR {error}')
    if errors:
        return 1
    print(f'OK: {len(pages)} page(s), {sum(len(page.edges) for page in pages)} edge(s)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
