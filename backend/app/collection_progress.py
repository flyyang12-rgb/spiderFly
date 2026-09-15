"""Optional JSON-line progress; observations never change execution outcomes."""
import json

PREFIX = 'SPIDERFLY_PROGRESS '
MAX_LINE = 4096
MAX_EVENTS = 40
MAX_COUNT = 1_000_000_000


def parse_event(line):
    if not line.startswith(PREFIX) or len(line) > MAX_LINE:
        return None
    try:
        value = json.loads(line[len(PREFIX):])
    except (ValueError, RecursionError):
        return None
    if not isinstance(value, dict) or value.get('event') not in ('progress', 'summary', 'error'):
        return None
    event = {'event': value['event']}
    for key in ('collected', 'total', 'page', 'failed_pages', 'missing'):
        if key in value:
            number = value[key]
            if number is not None and (type(number) is not int or not 0 <= number <= MAX_COUNT):
                return None
            event[key] = number
    for key in ('stage', 'message'):
        if key in value:
            if not isinstance(value[key], str) or len(value[key]) > 500:
                return None
            event[key] = value[key]
    if 'completeness' in value:
        if value['event'] != 'summary' or value['completeness'] not in ('complete', 'partial', 'unknown'):
            return None
        event['completeness'] = value['completeness']
    return event


def feed_progress(raw, text, at):
    """Handle arbitrary stream chunks, discarding oversized lines until newline."""
    try:
        state = json.loads(raw or '{}')
    except (ValueError, TypeError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    pending = state.get('pending', '')
    dropping = state.get('dropping', False)
    for part in text.splitlines(keepends=True):
        ended = part.endswith(('\n', '\r'))
        if not dropping:
            pending += part.rstrip('\r\n') if ended else part
            if len(pending) > MAX_LINE:
                pending, dropping = '', True
        if ended:
            event = None if dropping else parse_event(pending)
            pending, dropping = '', False
            if event:
                latest = state.get('latest', {})
                latest.update(event)
                if event['event'] != 'summary':
                    latest['completeness'] = 'unknown'
                else:
                    latest['completeness'] = event.get('completeness', 'unknown')
                latest['at'] = at
                state['latest'] = latest
                state['events'] = (state.get('events', []) + [{**event, 'at': at}])[-MAX_EVENTS:]
                state['event_count'] = state.get('event_count', 0) + 1
    state.update(pending=pending, dropping=dropping)
    return json.dumps(state, ensure_ascii=False)


def progress_view(raw):
    try:
        state = json.loads(raw or '{}')
    except (ValueError, TypeError):
        return None
    if not isinstance(state, dict) or not state.get('latest'):
        return None
    return {key: state.get(key) for key in ('latest', 'events', 'event_count')}
