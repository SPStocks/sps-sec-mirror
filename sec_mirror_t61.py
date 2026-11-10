#!/usr/bin/env python3
"""SPS SEC mirror T61 (ponte box -> Google Drive). Somente stdlib.

Baixa da SEC, com UA declarado, <=1 req/s e sem loops, exatamente as URLs que o pipeline Apps Script pede:
  - indices diarios  Archives/edgar/daily-index/YYYY/QTRn/master.YYYYMMDD.idx
  - corpos           Archives/edgar/data/<cik>/<acc sem hifen>/<acc>.txt  (formato de spsSECBodyUrlV025_)
Filtro = V022 (CIK do CIK_ROSTER_1416, data de arquivamento == dia do indice) + CP247 (exclui INDEX_ONLY).
Formas promovidas/outras semanas entram por requests.json (pedidos do Apps Script). Cada corpo e guardado
como um membro gzip proprio em store/; o sha256 e dos bytes HTTP decodificados (igual ao relay V032).
403 => grava titulo/Reference ID, backoff 6h/12h/24h e PARA (sem retry, sem trocar IP). 429 => pausa 15 min e para.
Saida: 1 linha 'SPS_SEC_MIRROR_T61 {json}' e exit 0 (OK/sem trabalho), 3 (403/backoff), 4 (429), 2 (erro).
"""
import datetime as dt, gzip, hashlib, json, os, re, sys, time, urllib.error, urllib.request, zlib

BASE = 'https://www.sec.gov/'
IDX = 'Archives/edgar/daily-index/{y}/QTR{q}/master.{d}.idx'
IDX_RE = re.compile(r'^Archives/edgar/daily-index/(20\d{2})/QTR([1-4])/master\.(\d{8})\.idx$')
BODY_RE = re.compile(r'^Archives/edgar/data/(\d{1,10})/(\d{18})/(\d{10}-\d{2}-\d{6})\.txt$')
ROW_RE = re.compile(r'^edgar/data/\d+/[\w./-]+$')
ACC_RE = re.compile(r'/(\d{10}-\d{2}-\d{6})\.txt$')
BACKOFF = [6 * 3600, 12 * 3600, 24 * 3600]
ET = dt.timezone(dt.timedelta(hours=-5))


def iso(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def ua_from_env():
    ua = os.environ.get('SPS_SEC_UA', '').strip()
    if not re.fullmatch(r'[A-Za-z0-9][\w .\-/]{2,80} [^\s<>@]+@[^\s<>@]+\.[^\s<>@]+', ua):
        raise SystemExit('SPS_SEC_UA invalido: use "Empresa contato@dominio"')
    return ua


def body_path(cik, acc):
    return 'Archives/edgar/data/%s/%s/%s.txt' % (str(int(cik)), acc.replace('-', ''), acc)


def allowed(path):
    m = BODY_RE.match(path)
    if m:
        return m.group(2) == m.group(3).replace('-', '')
    return bool(IDX_RE.match(path))


def http_get(url, ua, max_bytes, timeout=60):
    req = urllib.request.Request(url, headers={'User-Agent': ua, 'Accept': 'text/plain', 'Accept-Encoding': 'gzip, deflate', 'Host': 'www.sec.gov'})
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        r = e
    with r:
        code, headers, wire = r.code, {k.lower(): v for k, v in r.headers.items()}, r.read(max_bytes + 1)
    if len(wire) > max_bytes:
        return code, headers, None, 'WIRE_BODY_TOO_LARGE'
    enc = headers.get('content-encoding', '')
    try:
        if enc in ('gzip', 'deflate'):
            d = zlib.decompressobj(16 + zlib.MAX_WBITS if enc == 'gzip' else zlib.MAX_WBITS)
            body = d.decompress(wire, max_bytes + 1)
            if len(body) > max_bytes or d.unconsumed_tail:
                return code, headers, None, 'DECODED_BODY_TOO_LARGE'
        else:
            body = wire
    except zlib.error:
        return code, headers, None, 'ENCODING_TRUNCATED_OR_TRAILING'
    return code, headers, body, None


def block_info(body):
    t = (body or b'')[:20000].decode('utf-8', 'replace')
    h1 = re.search(r'<h1>(.*?)</h1>', t, re.S)
    ref = re.search(r'Reference ID:?\s*([\w.\-]{4,80})', t)
    s3 = re.search(r'<Code>(AccessDenied|NoSuchKey)</Code>', t)
    kind = 'SEC_BLOCK_PAGE' if (h1 or ref) else 'S3_ACCESS_DENIED_MISSING_OBJECT' if s3 else 'UNKNOWN_403'
    return {'title': (h1.group(1).strip()[:120] if h1 else None), 'referenceId': (ref.group(1) if ref else None),
            'bytes': len(body or b''), 'kind': kind, 'snippet': re.sub(r'\s+', ' ', re.sub(r'<[^>]{0,200}>', ' ', t))[:160]}


def priority(form, cfg):
    return 0 if re.match(cfg['priority0_re'], form) else 1 if re.match(cfg['priority1_re'], form) else 2


def wanted(cik, form, cfg, roster_ticker_by_cik):
    """CP247 sem contexto de noticia: True = pipeline baixa o corpo (BODY_PENDING)."""
    f = form.strip().upper()
    if f in cfg['index_only_forms']:
        return False
    if f in cfg['bank_product_forms']:
        t = cfg['bank_ciks'].get(str(cik))
        if t and t in roster_ticker_by_cik.get(str(cik), ()):
            return False
    return True


def parse_index(text, compact):
    """Mesmo recorte de spsSECV022Parse_: linhas validas com data == dia do indice, sem duplicatas."""
    out, seen, started = [], set(), False
    for line in text.replace('\r\n', '\n').split('\n'):
        s = line.strip()
        if not started:
            started = s in ('CIK|Company Name|Form Type|Date Filed|File Name', 'CIK|Company Name|Form Type|Date Filed|Filename')
            continue
        if not s or re.fullmatch(r'[-| ]+', s):
            continue
        v = s.split('|')
        if len(v) != 5 or not v[0].isdigit() or not v[1] or not v[2] or not ROW_RE.match(v[4]):
            continue
        if v[3].replace('-', '') != compact:
            continue
        a = ACC_RE.search(v[4])
        if not a:
            continue
        cik = str(int(v[0]))
        key = (cik, a.group(1))
        if key in seen:
            continue
        seen.add(key)
        out.append({'cik': cik, 'form': v[2], 'acc': a.group(1)})
    return out


class Mirror:
    def __init__(self, out, cfg, ua, fetch=http_get, clock=time.time, sleep=time.sleep):
        self.out, self.cfg, self.ua, self.fetch, self.clock, self.sleep = out, cfg, ua, fetch, clock, sleep
        self.store = os.path.join(out, 'store')
        os.makedirs(self.store, exist_ok=True)
        self.mpath, self.spath = os.path.join(out, 'manifest.json'), os.path.join(out, 'state.json')
        self.manifest = self._load(self.mpath, {'version': 'T61', 'items': {}})
        self.state = self._load(self.spath, {'consecutive403': 0, 'until': 0, 'last403': None, 'runs': []})
        self.calls, self.last_call, self.t0 = 0, 0.0, clock()

    @staticmethod
    def _load(p, d):
        try:
            with open(p) as f:
                return json.load(f)
        except (OSError, ValueError):
            return d

    def _save(self):
        for p, v in ((self.mpath, self.manifest), (self.spath, self.state)):
            tmp = p + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(v, f, indent=0, sort_keys=True)
            os.replace(tmp, p)

    def _budget_left(self):
        return self.calls < self.cfg['max_calls_per_run'] and self.clock() - self.t0 < self.cfg['max_run_seconds']

    def _get(self, path):
        wait = self.last_call + self.cfg['spacing_seconds'] - self.clock()
        if wait > 0:
            self.sleep(wait)
        self.last_call = self.clock()
        self.calls += 1
        return self.fetch(BASE + path, self.ua, self.cfg['max_body_bytes'])

    def _record(self, path, code, body, meta, note=None):
        it = {'secUrl': BASE + path, 'httpStatus': code, 'collectedUTC': iso(self.clock()), 'userAgent': 'DECLARED',
              'origin': self.cfg['origin_id'], **meta}
        if code == 200 and body is not None and note is None:
            try:
                body.decode('utf-8', 'strict')
                it['utf8'] = True
            except UnicodeDecodeError:
                it['utf8'] = False
                note = 'UTF8_UNVERIFIED'
            it['sha256'], it['bytes'] = hashlib.sha256(body).hexdigest(), len(body)
            if note is None:
                fp = os.path.join(self.store, path + '.gz')
                os.makedirs(os.path.dirname(fp), exist_ok=True)
                tmp = fp + '.tmp'
                with open(tmp, 'wb') as f:
                    f.write(gzip.compress(body, compresslevel=9, mtime=0))
                os.replace(tmp, fp)
                it['store'] = 'store/' + path + '.gz'
        if note:
            it['note'] = note
        self.manifest['items'][path] = it
        return it

    def _fetch_one(self, path, meta):
        """Retorna None se ok/continuar, ou dict de parada (403/429)."""
        code, headers, body, note = self._get(path)
        if code == 403:
            info = block_info(body)
            if info['kind'] == 'S3_ACCESS_DENIED_MISSING_OBJECT':
                # arquivo inexistente (ex.: indice ainda nao publicado): para a rodada, sem backoff de bloqueio
                self._record(path, 403, None, meta, 'S3_ACCESS_DENIED_MISSING_OBJECT') if meta.get('kind') == 'body' else None
                self.state['last403'] = {'path': path, 'atUTC': iso(self.clock()), **info}
                return {'status': 'SEC_403_MISSING_OBJECT_STOP', 'retryAfterSeconds': 0}
            n = self.state['consecutive403'] + 1
            wait = BACKOFF[min(n, len(BACKOFF)) - 1]
            self.state.update(consecutive403=n, until=self.clock() + wait,
                              last403={'path': path, 'atUTC': iso(self.clock()), **info})
            return {'status': 'SEC_403_BACKOFF', 'retryAfterSeconds': wait, 'consecutive403': n}
        if code == 429:
            self.state['until'] = self.clock() + 900
            return {'status': 'SEC_429_PAUSE', 'retryAfterSeconds': 900}
        self.state['consecutive403'] = 0
        if code == 200:
            self._record(path, 200, body, meta, note)
        elif code == 404 and meta.get('kind') == 'index':
            if meta.get('day', '99999999') < (dt.datetime.fromtimestamp(self.clock(), ET).date() - dt.timedelta(days=2)).strftime('%Y%m%d'):
                self._record(path, 404, None, meta, 'INDEX_NOT_PUBLISHED_HOLIDAY_OR_CLOSED')
            self.notpub += 1
        else:
            self._record(path, code, None, meta, 'SEC_NON200')
            self.failures += 1
        return None

    def run(self, ciks, roster_ticker_by_cik, days, requests=()):
        self.failures, self.notpub = 0, 0
        if self.state.get('until', 0) > self.t0:
            return self._finish({'status': 'BACKOFF_ACTIVE', 'untilUTC': iso(self.state['until']), 'sourceCalls': 0})
        res, items, queue = {'status': 'OK'}, self.manifest['items'], []
        try:
            # 1) pedidos do Apps Script (bloqueiam o pipeline), limitados e so URLs permitidas
            req = [p for p in dict.fromkeys(requests) if allowed(p) and p not in items][: self.cfg['max_requests_per_run']]
            for path in req:
                if not self._budget_left():
                    raise StopIteration
                m = BODY_RE.match(path)
                stop = self._fetch_one(path, {'kind': 'body' if m else 'index', 'via': 'REQUEST',
                                              **({'cik': m.group(1), 'acc': m.group(3)} if m else {'day': IDX_RE.match(path).group(3)})})
                if stop:
                    res = stop
                    raise StopIteration
            # 2) indices dos ultimos dias uteis
            for d in days:
                compact = d.strftime('%Y%m%d')
                path = IDX.format(y=d.year, q=(d.month - 1) // 3 + 1, d=compact)
                if path not in items:
                    if not self._budget_left():
                        raise StopIteration
                    stop = self._fetch_one(path, {'kind': 'index', 'day': compact})
                    if stop:
                        res = stop
                        raise StopIteration
                it = items.get(path)
                if not it or it.get('httpStatus') != 200 or not it.get('store'):
                    continue
                text = gzip.open(os.path.join(self.out, it['store'])).read().decode('utf-8', 'replace')
                for r in parse_index(text, compact):
                    if int(r['cik']) in ciks and wanted(r['cik'], r['form'], self.cfg, roster_ticker_by_cik):
                        bp = body_path(r['cik'], r['acc'])
                        if bp not in items:
                            queue.append((priority(r['form'], self.cfg), -int(compact), bp, {'kind': 'body', 'cik': r['cik'], 'acc': r['acc'], 'form': r['form'], 'day': compact}))
            # 3) corpos, mesma ordem do pipeline (prioridade 0 primeiro), mais recentes antes
            queue.sort(key=lambda x: (x[0], x[1], x[2]))
            cap = self.cfg.get('max_bodies_per_run')
            if cap is not None:
                queue = queue[: int(cap)]
            for _, _, path, meta in queue:
                if not self._budget_left():
                    raise StopIteration
                stop = self._fetch_one(path, meta)
                if stop:
                    res = stop
                    raise StopIteration
                if self.failures >= 5:
                    res = {'status': 'STOP_5_FAILURES'}
                    break
        except StopIteration:
            if res['status'] == 'OK':
                res = {'status': 'OK_PARTIAL_BUDGET_CONTINUE_NEXT_RUN'}
        done = sum(1 for _, _, p, _ in queue if p in items)
        res.update(sourceCalls=self.calls, bodiesWanted=len(queue), bodiesFetchedThisRun=done, bodiesPending=len(queue) - done,
                   failures=self.failures, indexNotPublished=self.notpub, requests=len([p for p in requests if allowed(p)]))
        return self._finish(res)

    def _finish(self, res):
        self._prune()
        res['atUTC'] = iso(self.clock())
        res['manifestItems'] = len(self.manifest['items'])
        self.state['runs'] = (self.state.get('runs') or [])[-49:] + [res]
        self.state['lastRun'] = res
        self._save()
        return res

    def _prune(self):
        cut = (dt.datetime.fromtimestamp(self.clock(), ET).date() - dt.timedelta(days=self.cfg['retention_days'])).strftime('%Y%m%d')
        lim = self.clock() - self.cfg['retention_days'] * 86400
        for path, it in list(self.manifest['items'].items()):
            t = dt.datetime.strptime(it['collectedUTC'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=dt.timezone.utc).timestamp()
            day = it.get('day')
            if (day is None or day < cut) and t < lim:
                if it.get('store'):
                    try:
                        os.remove(os.path.join(self.out, it['store']))
                    except OSError:
                        pass
                del self.manifest['items'][path]


def business_days(days_back, today=None):
    today = today or dt.datetime.now(ET).date()
    out, d = [], today  # so dias ANTERIORES a hoje (ET): o master.idx do dia sai a noite
    while len(out) < days_back:
        d -= dt.timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return sorted(out)


def load_universe(here, cfg):
    ciks, by = set(), {}
    for line in open(os.path.join(here, cfg['ciks_file'])):
        c = line.split('#')[0].strip()
        if c:
            ciks.add(int(c))
            by[str(int(c))] = [t.strip() for t in (line.split('#')[1] if '#' in line else '').split(',') if t.strip()]
    return ciks, by


def load_requests(out):
    p = os.path.join(out, 'requests.json')
    try:
        data = json.load(open(p))
    except (OSError, ValueError):
        return []
    urls = data.get('urls') if isinstance(data, dict) else data
    return [str(u).replace(BASE, '') for u in (urls or [])][:2000]


def apply_mode(cfg):
    """Ajustes por env, sem alterar o arquivo de config. proof = poucos pedidos."""
    mode = os.environ.get('SPS_SEC_MIRROR_MODE', '').strip().lower()
    if os.environ.get('SPS_SEC_SPACING_SECONDS', '').strip():
        cfg['spacing_seconds'] = max(float(os.environ['SPS_SEC_SPACING_SECONDS']), 2.0)
    else:
        cfg['spacing_seconds'] = max(float(cfg.get('spacing_seconds', 2.0)), 2.0)
    if mode == 'proof':
        cfg['index_days_back'] = int(os.environ.get('SPS_SEC_PROOF_INDEX_DAYS', '5'))
        cfg['max_calls_per_run'] = int(os.environ.get('SPS_SEC_PROOF_MAX_CALLS', '20'))
        cfg['max_run_seconds'] = int(os.environ.get('SPS_SEC_PROOF_MAX_SECONDS', '300'))
        cfg['max_bodies_per_run'] = int(os.environ.get('SPS_SEC_PROOF_BODIES', '3'))
        cfg['max_requests_per_run'] = 0
    return mode


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    cfg = json.load(open(os.path.join(here, 'config.json')))
    mode = apply_mode(cfg)
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, 'data')
    try:
        ciks, by = load_universe(here, cfg)
        res = Mirror(out, cfg, ua_from_env()).run(ciks, by, business_days(cfg['index_days_back']), load_requests(out))
    except Exception as e:  # erro inesperado: registra e sai 2, sem retry
        print('SPS_SEC_MIRROR_T61 ' + json.dumps({'status': 'ERRO', 'error': type(e).__name__, 'message': str(e)[:200]}))
        return 2
    res['mode'] = mode
    print('SPS_SEC_MIRROR_T61 ' + json.dumps(res, sort_keys=True))
    return {'SEC_403_BACKOFF': 3, 'BACKOFF_ACTIVE': 3, 'SEC_403_MISSING_OBJECT_STOP': 3, 'SEC_429_PAUSE': 4, 'STOP_5_FAILURES': 2}.get(res['status'], 0)


if __name__ == '__main__':
    sys.exit(main())
