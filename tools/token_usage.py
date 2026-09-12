#!/usr/bin/env python3
"""Sum Claude Code token usage for the Mlaser project from local transcripts.

Scans ~/.claude/projects/*/<session>.jsonl plus <session>/subagents/**/agent-*.jsonl,
dedupes per API response (message.id + requestId), and writes docs/TOKEN-USAGE.md.
Run:  python3 tools/token_usage.py [--session <id>]
"""
import json, sys, glob, os, collections, datetime

HOME = os.path.expanduser('~')
PROJ = os.path.join(HOME, '.claude', 'projects')
SESSION = '24376926-df7c-475a-835b-230b0f9b6974'
if '--session' in sys.argv:
    SESSION = sys.argv[sys.argv.index('--session') + 1]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'docs', 'TOKEN-USAGE.md')

def files():
    for d in glob.glob(os.path.join(PROJ, '*')):
        main = os.path.join(d, SESSION + '.jsonl')
        if os.path.exists(main):
            yield 'main', main
        for f in glob.glob(os.path.join(d, SESSION, '**', 'agent-*.jsonl'), recursive=True):
            label = None
            meta = f.replace('.jsonl', '.meta.json')
            if os.path.exists(meta):
                try:
                    m = json.load(open(meta))
                    label = m.get('label') or m.get('description') or m.get('name')
                except Exception:
                    pass
            yield label or os.path.basename(f)[:14], f

Z = lambda: dict(input=0, cache_w5=0, cache_w1h=0, cache_r=0, output=0, thinking=0, calls=0)
by_key = collections.defaultdict(Z)           # (date, source, model)
seen = {}
for source, path in files():
    for line in open(path, errors='replace'):
        try:
            e = json.loads(line)
        except Exception:
            continue
        m = e.get('message')
        if e.get('type') != 'assistant' or not isinstance(m, dict) or not m.get('usage'):
            continue
        rid = (m.get('id'), e.get('requestId'))
        u = m['usage']
        # streamed responses repeat the same message id across entries with a growing
        # output count; keep the entry with the largest output_tokens per response
        prev = seen.get(rid)
        if prev is None or u.get('output_tokens', 0) >= prev[1].get('output_tokens', 0):
            seen[rid] = ((e.get('timestamp', '')[:10] or 'unknown', source, m.get('model', '?')), u)

seen_list = seen
seen = set(seen_list)
for key, u in seen_list.values():
        cc = u.get('cache_creation') or {}
        k = by_key[key]
        k['input'] += u.get('input_tokens', 0)
        k['cache_w5'] += cc.get('ephemeral_5m_input_tokens', 0)
        k['cache_w1h'] += cc.get('ephemeral_1h_input_tokens', 0)
        if not cc:
            k['cache_w5'] += u.get('cache_creation_input_tokens', 0)
        k['cache_r'] += u.get('cache_read_input_tokens', 0)
        k['output'] += u.get('output_tokens', 0)
        k['thinking'] += (u.get('output_tokens_details') or {}).get('thinking_tokens', 0)
        k['calls'] += 1

def fmt(n): return f'{n:,}'
def row(name, k):
    total = k['input'] + k['cache_w5'] + k['cache_w1h'] + k['cache_r'] + k['output']
    return f"| {name} | {fmt(k['calls'])} | {fmt(k['input'])} | {fmt(k['cache_w5'])} | {fmt(k['cache_w1h'])} | {fmt(k['cache_r'])} | {fmt(k['output'])} | {fmt(k['thinking'])} | {fmt(total)} |"
HDR = ("| | API calls | Input (uncached) | Cache write 5m | Cache write 1h | Cache read | Output | of which thinking | Total tokens |\n"
       "|---|---:|---:|---:|---:|---:|---:|---:|---:|")

def agg(keys):
    t = Z()
    for kk in keys:
        for f in t: t[f] += by_key[kk][f]
    return t

dates = sorted({k[0] for k in by_key}); sources = sorted({k[1] for k in by_key}); models = sorted({k[2] for k in by_key})
lines = [f"# Token usage — Mlaser project\n", f"Generated {datetime.date.today()} by `tools/token_usage.py` from the local Claude Code transcripts (session `{SESSION}` and its workflow agents). Counts are API-billed tokens as reported in `usage`; \"Total\" is the sum of all input kinds plus output.\n"]
lines += ["## Grand total\n", HDR, row('all', agg(by_key)), ""]
lines += ["## By day\n", HDR] + [row(d, agg([k for k in by_key if k[0] == d])) for d in dates] + [""]
lines += ["## By model\n", HDR] + [row(mo, agg([k for k in by_key if k[2] == mo])) for mo in models] + [""]
lines += ["## By source (main session vs. each workflow agent)\n", HDR]
srows = [(agg([k for k in by_key if k[1] == s]), s) for s in sources]
srows.sort(key=lambda x: -(x[0]['input'] + x[0]['cache_w5'] + x[0]['cache_w1h'] + x[0]['cache_r'] + x[0]['output']))
lines += [row(s, t) for t, s in srows] + [""]
lines += ["## Day × source × model (raw)\n", HDR] + [row(f"{k[0]} · {k[1]} · {k[2]}", by_key[k]) for k in sorted(by_key)] + [""]
open(OUT, 'w').write('\n'.join(lines))
print('\n'.join(lines[:6]))
print(f"... wrote {OUT} ({len(by_key)} raw rows, {len(seen)} API calls)")
