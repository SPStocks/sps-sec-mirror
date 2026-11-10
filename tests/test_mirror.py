import gzip, hashlib, json, os, sys, tempfile, datetime as dt
H = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, H)
import sec_mirror_t61 as m, pack_mirror as u
CFG = json.load(open(os.path.join(H, 'config.json')))
HDR = 'Description: x\n\nCIK|Company Name|Form Type|Date Filed|File Name\n--------------------------------------------\n'
ROWS = ['320193|Apple Inc.|10-Q|20261106|edgar/data/320193/0000320193-26-000099.txt',
        '320193|Apple Inc.|10-Q|20261106|edgar/data/320193/0000320193-26-000099.txt',   # duplicata
        '999|Other|10-Q|20261106|edgar/data/999/0000000999-26-000001.txt',               # fora do universo
        '320193|Apple Inc.|4|20261106|edgar/data/320193/0000320193-26-000100.txt',       # INDEX_ONLY CP247
        '19617|JPMORGAN|424B2|20261106|edgar/data/19617/0001213900-26-000001.txt',       # banco 424B2 -> fora
        '19617|JPMORGAN|8-K|20261106|edgar/data/19617/0000019617-26-000002.txt',         # banco 8-K -> baixa
        '789019|MSFT|S-8|20261106|edgar/data/789019/0000789019-26-000003.txt',           # prioridade 2
        '789019|MSFT|8-K|20261105|edgar/data/789019/0000789019-26-000004.txt']           # data != dia do indice
IDXTXT = (HDR + '\n'.join(ROWS) + '\n').encode()
BLOCK = b'<html><h1>Your Request Originates from an Undeclared Automated Tool</h1> Reference ID: 0.abc.123</html>'
CIKS, BY = {320193, 19617, 789019}, {'320193': ['AAPL'], '19617': ['JPM'], '789019': ['MSFT']}
DAY = [dt.date(2026, 11, 6)]

class Clock:
    def __init__(s): s.t = dt.datetime(2026, 11, 10, 12, tzinfo=dt.timezone.utc).timestamp()
    def __call__(s): return s.t
    def sleep(s, x): s.t += x

def mk(codes=None, out=None, big=False, latin=False):
    c, calls, codes = Clock(), [], list(codes or [])
    def fetch(url, ua, mx):
        calls.append(url); c.t += 0.2; code = codes.pop(0) if codes else 200
        if code != 200: return code, {}, BLOCK if code == 403 else b'', None
        if 'daily-index' in url:
            return 200, {}, (b'' if '20261109' in url else IDXTXT), None
        if big: return 200, {}, None, 'DECODED_BODY_TOO_LARGE'
        return 200, {}, (b'<SEC-DOCUMENT>\xe9</SEC-DOCUMENT>' if latin else ('<SEC-DOCUMENT>' + url + ' \u00e9</SEC-DOCUMENT>').encode()), None
    out = out or tempfile.mkdtemp()
    return m.Mirror(out, CFG, 'SPS-Consensus-SEC-Collector/0.61.0 SPS Analytics a@b.co', fetch, c, c.sleep), calls, c, out

def test_filter_matches_pipeline_v022_cp247_and_url_format():
    r, calls, c, out = mk(); res = r.run(CIKS, BY, DAY)
    bodies = [x for x in calls if '/data/' in x]
    assert res['status'] == 'OK' and res['bodiesWanted'] == 3, res
    assert bodies[0] == 'https://www.sec.gov/Archives/edgar/data/19617/000001961726000002/0000019617-26-000002.txt'  # prio0, chave
    assert bodies[1] == 'https://www.sec.gov/Archives/edgar/data/320193/000032019326000099/0000320193-26-000099.txt'
    assert bodies[2].endswith('/0000789019-26-000003.txt')  # S-8 prioridade 2 por ultimo
    for b in bodies: assert m.allowed(b.replace(m.BASE, ''))

def test_sha_is_of_decoded_http_bytes_and_gzip_member():
    r, calls, c, out = mk(); r.run(CIKS, BY, DAY)
    p = 'Archives/edgar/data/320193/000032019326000099/0000320193-26-000099.txt'; it = r.manifest['items'][p]
    raw = gzip.open(os.path.join(out, it['store'])).read()
    assert hashlib.sha256(raw).hexdigest() == it['sha256'] and it['utf8'] and it['httpStatus'] == 200

def test_idempotent_second_run_only_rechecks_unpublished_index():
    r, calls, c, out = mk(); r.run(CIKS, BY, DAY)
    r2, calls2, _, _ = mk(None, out); res = r2.run(CIKS, BY, DAY)
    assert res['sourceCalls'] == 0 and calls2 == [], calls2

def test_403_stops_records_reason_and_backoff_no_retry():
    r, calls, c, out = mk([403]); res = r.run(CIKS, BY, DAY)
    assert res['status'] == 'SEC_403_BACKOFF' and len(calls) == 1 and res['retryAfterSeconds'] == 6 * 3600
    st = json.load(open(os.path.join(out, 'state.json')))
    assert st['last403']['title'].startswith('Your Request') and st['last403']['referenceId'] == '0.abc.123'
    r2, calls2, c2, _ = mk(None, out); c2.t = c.t + 60
    assert r2.run(CIKS, BY, DAY)['status'] == 'BACKOFF_ACTIVE' and calls2 == []

def test_429_pauses_and_stops():
    r, calls, c, out = mk([200, 429]); res = r.run(CIKS, BY, DAY)
    assert res['status'] == 'SEC_429_PAUSE' and len(calls) == 2

def test_spacing_at_most_1_per_second():
    r, calls, c, out = mk(); t0 = c.t; r.run(CIKS, BY, DAY)
    assert c.t - t0 >= CFG['spacing_seconds'] * (len(calls) - 1)

def test_budget_cap_partial():
    cfg = dict(CFG, max_calls_per_run=2); c = Clock(); out = tempfile.mkdtemp()
    r = m.Mirror(out, cfg, 'X Y a@b.co', lambda u, a, mx: (200, {}, IDXTXT if 'daily' in u else b'<SEC-DOCUMENT>x</SEC-DOCUMENT>', None), c, c.sleep)
    res = r.run(CIKS, BY, DAY)
    assert res['status'] == 'OK_PARTIAL_BUDGET_CONTINUE_NEXT_RUN' and res['sourceCalls'] == 2 and res['bodiesPending'] == 2, res

def test_too_large_and_utf8_recorded_not_stored():
    r, calls, c, out = mk(big=True); r.run(CIKS, BY, DAY)
    it = r.manifest['items']['Archives/edgar/data/320193/000032019326000099/0000320193-26-000099.txt']
    assert it['note'] == 'DECODED_BODY_TOO_LARGE' and 'store' not in it
    r, calls, c, out = mk(latin=True); r.run(CIKS, BY, DAY)
    it = r.manifest['items']['Archives/edgar/data/320193/000032019326000099/0000320193-26-000099.txt']
    assert it['note'] == 'UTF8_UNVERIFIED' and 'store' not in it

def test_requests_first_and_only_allowed_urls():
    r, calls, c, out = mk()
    req = ['Archives/edgar/data/320193/000032019326000100/0000320193-26-000100.txt', 'Archives/edgar/data/1/2/x.txt', 'https://evil/x']
    res = r.run(CIKS, BY, DAY, req)
    assert calls[0].endswith('0000320193-26-000100.txt') and not any('evil' in x or '/2/x' in x for x in calls), calls

def test_unpublished_index_404_not_failure():
    r, calls, c, out = mk([404]); res = r.run(CIKS, BY, [dt.date(2026, 11, 10)])
    assert res['status'] == 'OK' and res['failures'] == 0 and res['indexNotPublished'] == 1
    assert not r.manifest['items']  # dia recente: nao grava 404, tenta na proxima rodada

def test_pack_offsets_roundtrip_and_confirm():
    r, calls, c, out = mk(); r.run(CIKS, BY, DAY)
    up = u.Uploader(out, CFG, clock=c); plan = up.prepare()
    names = [x['name'] for x in plan['order']]
    assert names[0].startswith('SPS_SEC_MIRROR_PACK_T61_')
    assert any(n.startswith('SPS_SEC_MIRROR_MANIFEST_T61_') and n.endswith('.json') and n != u.STABLE_MANIFEST for n in names)
    assert u.STABLE_MANIFEST in names
    man = json.load(open([x['sourcePath'] for x in plan['order'] if x['name'] == u.STABLE_MANIFEST][0]))
    assert man['schema'] == 'SPS_SEC_MIRROR_MANIFEST_T61_V1' and man['userAgent'] == 'DECLARED' and man['distribution'] == 'github-release'
    assert '@' not in json.dumps(man)
    pack = open(plan['order'][0]['sourcePath'], 'rb').read()
    for path, x in man['items'].items():
        if x['h'] == 200 and 'p' in x:
            body = gzip.decompress(pack[x['o']: x['o'] + x['n']])
            assert hashlib.sha256(body).hexdigest() == x['s'] and len(body) == x['b']
    assert up.confirm()['status'] == 'CONFIRMED'
    up2 = u.Uploader(out, CFG, clock=c); plan2 = up2.prepare()
    assert plan2['newPacks'] == [] and all(x['name'].endswith('.json') or 'MANIFEST' in x['name'] for x in plan2['order'])
    man2 = json.load(open([x['sourcePath'] for x in plan2['order'] if x['name'] == u.STABLE_MANIFEST][0])); assert man2['items'] == man['items']

def test_prepare_twice_without_confirm_relists_same_pack():
    r, calls, c, out = mk(); r.run(CIKS, BY, DAY)
    up = u.Uploader(out, CFG, clock=c); p1 = up.prepare(); c.t += 5
    p2 = u.Uploader(out, CFG, clock=c).prepare()
    packs1 = [x['name'] for x in p1['order'] if x['name'].endswith('.bin')]
    packs2 = [x['name'] for x in p2['order'] if x['name'].endswith('.bin')]
    assert packs1 == packs2
    mans = [f for f in os.listdir(os.path.join(out, 'outbox')) if 'MANIFEST' in f]
    assert u.STABLE_MANIFEST in mans and len(mans) == 2

def test_retention_prunes_old_items():
    r, calls, c, out = mk(); r.run(CIKS, BY, DAY)
    r2, _, c2, _ = mk(None, out); c2.t += 20 * 86400; r2.run(CIKS, BY, [])
    assert not any('/data/' in k for k in r2.manifest['items']), list(r2.manifest['items'])

def test_403_s3_missing_object_stops_without_backoff():
    c, out, calls = Clock(), tempfile.mkdtemp(), []
    xml = b'<?xml version="1.0" encoding="UTF-8"?><Error><Code>AccessDenied</Code><Message>Access Denied</Message><RequestId>X</RequestId></Error>'
    def fetch(url, ua, mx):
        calls.append(url); return (403, {}, xml, None)
    r = m.Mirror(out, CFG, 'X Y a@b.co', fetch, c, c.sleep); res = r.run(CIKS, BY, DAY)
    assert res['status'] == 'SEC_403_MISSING_OBJECT_STOP' and len(calls) == 1 and r.state['until'] == 0 and r.state['consecutive403'] == 0
    assert r.state['last403']['kind'] == 'S3_ACCESS_DENIED_MISSING_OBJECT'

def test_business_days_excludes_today():
    d = m.business_days(3, dt.date(2026, 11, 10))
    assert d == [dt.date(2026, 11, 5), dt.date(2026, 11, 6), dt.date(2026, 11, 9)], d


def test_proof_mode_caps_bodies_and_redacts_ua():
    os.environ['SPS_SEC_MIRROR_MODE'] = 'proof'
    os.environ.pop('SPS_SEC_SPACING_SECONDS', None)
    try:
        cfg = json.load(open(os.path.join(H, 'config.json')))
        assert m.apply_mode(cfg) == 'proof'
        assert cfg['max_bodies_per_run'] == 3 and cfg['spacing_seconds'] >= 2.0 and cfg['index_days_back'] == 5
        r, calls, c, out = mk()
        r.cfg = dict(r.cfg, max_bodies_per_run=3, spacing_seconds=2.0)
        res = r.run(CIKS, BY, DAY)
        bodies = [x for x in calls if '/data/' in x]
        assert res['bodiesWanted'] == 3 and len(bodies) == 3, res
        for it in r.manifest['items'].values():
            assert it['userAgent'] == 'DECLARED' and '@' not in it['userAgent']
    finally:
        os.environ.pop('SPS_SEC_MIRROR_MODE', None)


def test_business_days_skips_today_even_weekday():
    # 10/11/2026 e terca: o indice do proprio dia fica de fora
    d = m.business_days(5, dt.date(2026, 11, 10))
    assert dt.date(2026, 11, 10) not in d
    assert d == [dt.date(2026, 11, 3), dt.date(2026, 11, 4), dt.date(2026, 11, 5), dt.date(2026, 11, 6), dt.date(2026, 11, 9)], d

if __name__ == '__main__':
    n = 0
    for k, f in list(globals().items()):
        if k.startswith('test_'): f(); n += 1; print('PASS', k)
    print(f'{n}/{n} PASS')
