"""Build editable Draw.io pages using only the Python standard library."""

from math import dist, isclose
from pathlib import Path
from xml.etree import ElementTree as ET


BASE = 'html=1;fontFamily=Arial;fontColor=#000000;'
VERTEX = 'rect;whiteSpace=wrap;fillColor=#FFFFFF;strokeColor=#000000;fontSize=18;'
EDGE = ('noEdgeStyle=1;rounded=0;strokeColor=#000000;strokeWidth=1.5;endArrow=block;'
        'endSize=7;endFill=1;fontSize=17;labelBackgroundColor=#FFFFFF;')


def element(container, tag, **attributes):
    return ET.SubElement(container, tag, {key: str(value) for key, value in attributes.items()})


class Diagram:
    def __init__(self, key, title, width, height):
        self.page = ET.Element('diagram', id=key, name=title)
        model = element(self.page, 'mxGraphModel', page=1, pageScale=1, grid=0,
                        pageWidth=width, pageHeight=height, background='#FFFFFF')
        self.root = element(model, 'root')
        element(self.root, 'mxCell', id='0')
        element(self.root, 'mxCell', id='1', parent='0')
        self.ids = {'0', '1'}
        self.boxes = {}

    def cell(self, key, **attrs):
        if not key or key in self.ids:
            raise ValueError(f'Empty or repeated cell ID: {key!r}')
        self.ids.add(key)
        return element(self.root, 'mxCell', id=key, **attrs)

    def vertex(self, key, label, bounds, *, style='', role=None, parent='1'):
        """Bounds are relative to parent. Edge coordinates are absolute."""
        x, y, width, height = bounds
        if (parent != '1' and parent not in self.boxes) or width <= 0 or height <= 0:
            raise ValueError(f'{key}: invalid parent or dimensions')
        attrs = {'data-role': role} if role else {}
        cell = self.cell(key, vertex=1, parent=parent, value=label.replace('\n', '<br>'),
                         style=BASE+VERTEX+style, **attrs)
        element(cell, 'mxGeometry', x=x, y=y, width=width, height=height, **{'as': 'geometry'})
        px, py = self.boxes[parent][:2] if parent != '1' else (0, 0)
        self.boxes[key] = (px+x, py+y, width, height)
        return cell

    def port(self, key, point, side):
        x, y, width, height = self.boxes[key]
        fx, fy = (point[0]-x)/width, (point[1]-y)/height
        if not (0 <= fx <= 1 and 0 <= fy <= 1):
            raise ValueError(f'{key}: port {point} is outside its vertex')
        return f'{side}X={fx:.8f};{side}Y={fy:.8f};{side}Perimeter=0;'

    def edge(self, key, label, points, *, source=None, target=None, label_at=None, style=''):
        """Use waypoints between endpoints. Attach label_at to a route segment."""
        if len(points) < 2 or not all(dist(a,b) > 0 for a,b in zip(points, points[1:])):
            raise ValueError(f'{key}: missing or coincident route points')
        attrs = {}
        fixed_ports = ''
        if source:
            attrs['source'] = source
            fixed_ports += self.port(source, points[0], 'exit')
        if target:
            attrs['target'] = target
            fixed_ports += self.port(target, points[-1], 'entry')
        position = self.label_position(points, label_at) if label_at is not None else 0
        edge = self.cell(key, value=label.replace('\n', '<br>'), edge=1, parent='1',
                         style=BASE+EDGE+style+fixed_ports, **attrs)
        geometry = element(edge, 'mxGeometry', relative=1, x=position, **{'as': 'geometry'})
        if not source:
            element(geometry, 'mxPoint', x=points[0][0], y=points[0][1], **{'as': 'sourcePoint'})
        if not target:
            element(geometry, 'mxPoint', x=points[-1][0], y=points[-1][1], **{'as': 'targetPoint'})
        if len(points) > 2:
            array = element(geometry, 'Array', **{'as': 'points'})
            for x, y in points[1:-1]:
                element(array, 'mxPoint', x=x, y=y)
        return edge

    @staticmethod
    def label_position(points, at):
        segments = list(zip(points, points[1:]))
        total = sum(dist(a,b) for a,b in segments)
        travelled = 0
        for a, b in segments:
            length = dist(a,b)
            if isclose(dist(a,at)+dist(at,b), length, abs_tol=1e-6):
                return 2*(travelled+dist(a,at))/total-1
            travelled += length
        raise ValueError(f'Label point {at} is not on its route')


def write_book(path, pages):
    """Write a new uncompressed file. Refuse to overwrite an existing file."""
    pages = list(pages)
    if not pages or len({p.page.get('id') for p in pages}) != len(pages):
        raise ValueError('At least one page and unique page IDs are required')
    book = ET.Element('mxfile', host='app.diagrams.net', compressed='false', pages=str(len(pages)))
    book.extend(page.page for page in pages)
    ET.indent(book, space='  ')
    with Path(path).open('xb') as stream:
        ET.ElementTree(book).write(stream, encoding='utf-8', xml_declaration=True)
