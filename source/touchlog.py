"""Parse pymobiledevice3 `oslog --format json` lines (BackBoard TouchEvents) into per-contact strokes.

parseTouchLog() in viewer_template.html implements the same rules for files opened in the viewer;
keep both in sync so a log gives identical strokes whether it is embedded at build time or loaded later.
"""
import collections
import datetime
import json
import re

SUB_RX = re.compile(r'path:\s*(\d+);\s*\{([\d.]+),\s*([\d.]+)\};\s*(.*?)touchID:\s*(0x[\da-fA-F]+)')
TOKEN_RX = re.compile(r'Digitizer token:\s*(0x[\da-fA-F]+)')
PHASES = ['unchanged', 'down', 'move', 'up', 'range_out']


def phase_of(txt):
    if re.search(r'\bup;', txt):
        return 3
    if re.search(r'\bdown;', txt):
        return 1
    if 'range (out)' in txt:
        return 4
    if 'move' in txt:
        return 2
    return 0


def load(path):
    """Return events, per-contact samples and viewer strokes for one JSONL capture.

    Times are integer ms from the first record. XY are stored doubled (x2) as in the viewer's STROKES.
    Lines that are not oslog JSON are skipped. If the capture mixes several Digitizer tokens,
    only the most frequent one is used (a single coordinate system).
    """
    recs = []
    skipped = 0
    tokens = collections.Counter()
    with open(path, encoding='utf-8-sig') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                j = json.loads(line)
                ts = datetime.datetime.fromisoformat(j['timestamp'])
                msg = j['message']
            except (ValueError, KeyError, TypeError):
                skipped += 1
                continue
            if not isinstance(msg, str):
                skipped += 1
                continue
            m = TOKEN_RX.search(msg)
            tok = m.group(1) if m else None
            if tok:
                tokens[tok] += 1
            recs.append((ts, msg, tok))
    token = tokens.most_common(1)[0][0] if len(tokens) > 1 else None
    if token:
        recs = [r for r in recs if r[2] == token]
    if not recs:
        raise ValueError(f'no touch records in {path}')

    zero = recs[0][0]
    events = []
    per_id = {}
    for ts, msg, _ in recs:
        ms = int(round((ts - zero).total_seconds() * 1000))
        lst = []
        for path_no, x, y, txt, ident in SUB_RX.findall(msg):
            x = float(x)
            y = float(y)
            ident = int(ident, 16)
            phase = phase_of(txt)
            lst.append((ident, int(path_no), x, y, phase))
            per_id.setdefault(ident, []).append((ms, int(round(2 * x)), int(round(2 * y)), phase, int(path_no)))
        events.append((ts, ms, lst))
    duration = int(round((recs[-1][0] - zero).total_seconds() * 1000))
    strokes = [[ident, ss[0][0], ss[-1][0], [[t, x, y, p] for t, x, y, p, _ in ss]] for ident, ss in per_id.items()]
    strokes.sort(key=lambda a: a[1])
    return {
        'zero': zero, 'events': events, 'per_id': per_id, 'strokes': strokes, 'duration': duration,
        'records': len(recs), 'skipped': skipped, 'token': token,
    }


def onset_bins(strokes, duration, width_ms=10000):
    """Count contact starts per bin (used by the standalone replay and the timeline PNG)."""
    n = max(1, -(-duration // width_ms))
    bins = [0] * n
    for _, start, _, _ in strokes:
        bins[min(start // width_ms, n - 1)] += 1
    return bins
