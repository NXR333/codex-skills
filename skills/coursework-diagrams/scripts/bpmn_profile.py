"""Basic BPMN educational profile. Graph checks do not prove token semantics."""

NODE_ROLES = {'bpmn-start', 'bpmn-end', 'bpmn-intermediate', 'bpmn-task',
              'bpmn-xor', 'bpmn-and', 'bpmn-or'}
MESSAGE_ROLES = {'bpmn-pool', 'bpmn-task', 'bpmn-start', 'bpmn-end', 'bpmn-intermediate'}
DATA_ROLES = {'bpmn-data', 'bpmn-store'}
EDGE_ROLES = {'bpmn-sequence', 'bpmn-message', 'bpmn-association', 'bpmn-data-association'}
ROLES = NODE_ROLES | DATA_ROLES | EDGE_ROLES | {'bpmn-pool', 'bpmn-lane'}


def check(page):
    roles = {k: page.role(k) for k in page.cells}
    def ancestor(key, role):
        seen = set()
        while key in page.cells and key not in seen:
            seen.add(key)
            if roles[key] == role:
                return key
            key = page.cells[key][1].get('parent')
        return None
    for key, role in roles.items():
        if role and role.startswith('bpmn-') and role not in ROLES:
            page.fail(f'{key}: unsupported BPMN role {role!r}; extend profile or review manually')
        if role in NODE_ROLES | DATA_ROLES | {'bpmn-lane'} and not ancestor(key, 'bpmn-pool'):
            page.fail(f'{key}: BPMN element must belong to a pool')
        if role in ROLES and ((key in page.edges) != (role in EDGE_ROLES)):
            page.fail(f'{key}: BPMN role does not match vertex/edge')
    nodes = {k for k, r in roles.items() if r in NODE_ROLES}
    if not nodes:
        page.fail('identify BPMN flow nodes with data-role=bpmn-*')
        return
    forward = {k: set() for k in nodes}
    reverse = {k: set() for k in nodes}
    for key, edge in page.edges.items():
        role = roles[key]
        if role not in EDGE_ROLES:
            page.fail(f'{key}: label every BPMN connection with its data-role')
            continue
        a, b = edge.get('source'), edge.get('target')
        if a not in page.cells or b not in page.cells:
            page.fail(f'{key}: BPMN connections need existing source and target cells')
            continue
        pa, pb = ancestor(a, 'bpmn-pool'), ancestor(b, 'bpmn-pool')
        style = page.style(key)
        dashed = style.get('dashed') == '1'
        if role == 'bpmn-sequence':
            if a not in nodes or b not in nodes:
                page.fail(f'{key}: sequence flow must join flow nodes')
                continue
            if not pa or pa != pb:
                page.fail(f'{key}: sequence flow cannot cross pools')
            if dashed or style.get('endArrow', 'none') != 'block' or style.get('endFill', '1') != '1':
                page.fail(f'{key}: sequence flow needs solid line and filled block arrow')
            forward[a].add(b); reverse[b].add(a)
        elif role == 'bpmn-message':
            if not pa or not pb or pa == pb:
                page.fail(f'{key}: message flow must connect different pools')
            if roles[a] not in MESSAGE_ROLES or roles[b] not in MESSAGE_ROLES:
                page.fail(f'{key}: invalid message endpoint; gateways, lanes and data cannot send/receive messages')
            if roles[a] == 'bpmn-start' or roles[b] == 'bpmn-end':
                page.fail(f'{key}: start cannot send and end cannot receive messages')
            for endpoint, direction in ((a, 'throw'), (b, 'catch')):
                node, cell = page.cells[endpoint]
                event = node.get('data-event') or cell.get('data-event')
                if roles[endpoint] in {'bpmn-start', 'bpmn-end', 'bpmn-intermediate'}:
                    if event != 'message-' + direction:
                        page.fail(f'{key}: message event needs data-event=message-{direction}')
            if (not dashed or style.get('startArrow') != 'oval' or
                style.get('startFill', '1') != '0' or style.get('endArrow') != 'open'):
                page.fail(f'{key}: message flow needs dashed line, hollow circle and open arrow')
        elif role == 'bpmn-data-association':
            if not ((roles[a] in DATA_ROLES and b in nodes) or (a in nodes and roles[b] in DATA_ROLES)):
                page.fail(f'{key}: data association must join data and a flow node')
            if not pa or pa != pb:
                page.fail(f'{key}: data association cannot cross pools; use messages')
            if not dashed or style.get('endArrow') != 'open':
                page.fail(f'{key}: data association needs dotted directed line')
        elif not dashed:
            page.fail(f'{key}: association needs dotted line')
    for key in nodes:
        role = roles[key]
        node, cell = page.cells[key]
        event = node.get('data-event') or cell.get('data-event')
        if event and (role not in {'bpmn-start', 'bpmn-end', 'bpmn-intermediate'} or event not in {'none', 'timer', 'message-catch', 'message-throw'}):
            page.fail(f'{key}: unsupported event metadata {event!r}')
        if (role == 'bpmn-start' and event == 'message-throw') or (role == 'bpmn-end' and event in {'message-catch', 'timer'}):
            page.fail(f'{key}: incompatible event type for start/end')
        if not page.label(key) and role in {'bpmn-task', 'bpmn-start', 'bpmn-end', 'bpmn-intermediate'}:
            page.fail(f'{key}: task/event needs a meaningful label')
        ins, outs = reverse[key], forward[key]
        if role == 'bpmn-start':
            if ins or not outs: page.fail(f'{key}: start needs no incoming and at least one outgoing sequence flow')
        elif role == 'bpmn-end':
            if not ins or outs: page.fail(f'{key}: end needs incoming and no outgoing sequence flow')
        elif not ins or not outs:
            page.fail(f'{key}: flow node needs incoming and outgoing sequence flow')
        if role in {'bpmn-xor', 'bpmn-and', 'bpmn-or'}:
            if len(ins) < 2 and len(outs) < 2:
                page.fail(f'{key}: gateway must split or merge flows')
            if role in {'bpmn-xor', 'bpmn-or'} and len(outs) > 1:
                outgoing = [k for k,e in page.edges.items() if roles[k] == 'bpmn-sequence' and e.get('source') == key]
                for e in outgoing:
                    if not page.edge_label(e) and page.style(e).get('startArrow') != 'dash':
                        page.fail(f'{e}: conditional branch needs label or default-flow marker')
    def walk(seeds, graph):
        seen, pending = set(), list(seeds)
        while pending:
            k = pending.pop()
            if k not in seen:
                seen.add(k); pending.extend(graph[k] - seen)
        return seen
    reachable = walk([k for k in nodes if roles[k] == 'bpmn-start'], forward)
    finishing = walk([k for k in nodes if roles[k] == 'bpmn-end'], reverse)
    for key in sorted(nodes - reachable): page.fail(f'{key}: not reachable from a start')
    for key in sorted(nodes - finishing): page.fail(f'{key}: cannot reach an end by sequence flow')
