#!/usr/bin/env python3
"""Empacota o que sec_mirror_t61.py baixou no mesmo formato T61 do box (packs .bin + manifesto).

Pack = concatenacao de membros gzip independentes. O manifesto publica offset/tamanho/sha256
de cada URL. O User-Agent declarado NUNCA entra no manifesto publico (campo userAgent='DECLARED').
Nao sobe para o Drive: a publicacao e um GitHub Release (publish_release.py).
"""
import datetime as dt, hashlib, json, os, sys, time

SCHEMA = 'SPS_SEC_MIRROR_MANIFEST_T61_V1'
STABLE_MANIFEST = 'SPS_SEC_MIRROR_MANIFEST_T61.json'


def iso(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def stamp(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def load(p, d):
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return d


def save(p, v, indent=0):
    tmp = p + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(v, f, indent=indent, sort_keys=True, separators=(',', ':') if indent is None else None)
    os.replace(tmp, p)


def sha_file(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def looks_like_email(s):
    return isinstance(s, str) and '@' in s and '.' in s.split('@')[-1]


def assert_no_secret(obj, path='root'):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in ('useragent', 'ua', 'sps_sec_ua') and looks_like_email(v):
                raise SystemExit('RECUSA: manifesto publico nao pode carregar User-Agent com e-mail (%s)' % path)
            if looks_like_email(v) and k.lower() in ('useragent', 'email', 'contact', 'secret', 'token'):
                raise SystemExit('RECUSA: campo sensivel no manifesto publico (%s)' % path)
            assert_no_secret(v, path + '.' + str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            assert_no_secret(v, path + '[%s]' % i)


class Uploader:
    def __init__(self, out, cfg, clock=time.time):
        self.out, self.cfg, self.clock = out, cfg, clock
        self.outbox = os.path.join(out, 'outbox')
        os.makedirs(self.outbox, exist_ok=True)
        self.mpath, self.upath, self.spath = (os.path.join(out, n) for n in ('manifest.json', 'uploads.json', 'state.json'))
        self.manifest = load(self.mpath, {'items': {}})
        self.up = load(self.upath, {'packs': {}, 'manifests': {}})

    def _pack_new(self):
        todo = sorted(p for p, it in self.manifest['items'].items() if it.get('store') and not it.get('pack'))
        made, cur, f, off, n = [], None, None, 0, 0
        t = self.clock()
        for path in todo:
            it = self.manifest['items'][path]
            data = open(os.path.join(self.out, it['store']), 'rb').read()
            if f is None or off + len(data) > self.cfg['pack_max_bytes']:
                if f:
                    f.close(); made.append(cur)
                n += 1
                cur = 'SPS_SEC_MIRROR_PACK_T61_%s_%02d.bin' % (stamp(t), n)
                f, off = open(os.path.join(self.outbox, cur + '.tmp'), 'wb'), 0
            f.write(data)
            it.update(pack=cur, offset=off, gzBytes=len(data))
            off += len(data)
        if f:
            f.close(); made.append(cur)
        for name in made:
            p = os.path.join(self.outbox, name)
            os.replace(p + '.tmp', p)
            self.up['packs'][name] = {'status': 'PENDING', 'sha256': sha_file(p), 'bytes': os.path.getsize(p), 'createdUTC': iso(t)}
        return made

    def _rebuild_missing_packs(self):
        """Recria no outbox packs ja confirmados (apagados apos o upload) a partir do store, mesmos offsets."""
        by = {}
        for path, it in self.manifest['items'].items():
            if it.get('pack') and it.get('store') and 'offset' in it and 'gzBytes' in it:
                by.setdefault(it['pack'], []).append(it)
        rebuilt = []
        for name, members in by.items():
            dest = os.path.join(self.outbox, name)
            if os.path.exists(dest):
                continue
            members = sorted(members, key=lambda it: it['offset'])
            tmp = dest + '.tmp'
            with open(tmp, 'wb') as f:
                off = 0
                for it in members:
                    if it['offset'] != off:
                        os.remove(tmp)
                        raise ValueError('PACK_OFFSET_GAP %s at %s' % (name, off))
                    data = open(os.path.join(self.out, it['store']), 'rb').read()
                    if len(data) != it['gzBytes']:
                        os.remove(tmp)
                        raise ValueError('PACK_MEMBER_SIZE %s' % name)
                    f.write(data)
                    off += len(data)
            os.replace(tmp, dest)
            rec = self.up['packs'].setdefault(name, {})
            rec.update(sha256=sha_file(dest), bytes=os.path.getsize(dest), status=rec.get('status') or 'PENDING')
            rebuilt.append(name)
        return rebuilt

    def prepare(self):
        made = self._pack_new()
        rebuilt = self._rebuild_missing_packs()
        save(self.mpath, self.manifest)
        t = self.clock()
        live = {n for n, v in self.up['packs'].items()}
        items = {}
        for path, it in self.manifest['items'].items():
            if it.get('pack') and it['pack'] not in live:
                continue
            row = {'h': it['httpStatus'], 'c': it['collectedUTC']}
            for k_src, k in (('pack', 'p'), ('offset', 'o'), ('gzBytes', 'n'), ('sha256', 's'), ('bytes', 'b'), ('note', 'x'), ('day', 'd'), ('form', 'f'), ('utf8', 'u')):
                if k_src in it:
                    row[k] = it[k_src]
            items[path] = row
        used = sorted({r['p'] for r in items.values() if 'p' in r})
        st = load(self.spath, {})
        last403 = st.get('last403')
        if isinstance(last403, dict):
            last403 = {k: last403[k] for k in last403 if k in ('path', 'atUTC', 'title', 'referenceId', 'kind', 'bytes') and not looks_like_email(last403[k])}
        m = {
            'schema': SCHEMA,
            'generatedUTC': iso(t),
            'origin': self.cfg['origin_id'],
            'distribution': 'github-release',
            'userAgent': 'DECLARED',
            'retentionDays': self.cfg['retention_days'],
            'packs': used,
            'source': {
                'lastRun': st.get('lastRun'),
                'consecutive403': st.get('consecutive403', 0),
                'backoffUntilUTC': iso(st['until']) if st.get('until') else None,
                'last403': last403,
            },
            'items': items,
        }
        assert_no_secret(m)
        for old in os.listdir(self.outbox):
            if old.startswith('SPS_SEC_MIRROR_MANIFEST_T61_') or old == STABLE_MANIFEST:
                os.remove(os.path.join(self.outbox, old))
                self.up['manifests'].pop(old, None)
        mname = 'SPS_SEC_MIRROR_MANIFEST_T61_%s.json' % stamp(t)
        mp = os.path.join(self.outbox, mname)
        save(mp, m, indent=None)
        stable = os.path.join(self.outbox, STABLE_MANIFEST)
        save(stable, m, indent=None)
        rec = {'status': 'PENDING', 'sha256': sha_file(mp), 'bytes': os.path.getsize(mp), 'createdUTC': iso(t)}
        self.up['manifests'][mname] = rec
        self.up['manifests'][STABLE_MANIFEST] = dict(rec)
        pend = [n for n, v in sorted(self.up['packs'].items()) if v['status'] == 'PENDING']
        # Packs confirmados de rodadas anteriores tambem precisam ir (ou permanecer) no release.
        all_packs = [n for n in used]
        pack_entries = []
        seen = set()
        for n in pend + all_packs:
            if n in seen:
                continue
            seen.add(n)
            src = os.path.join(self.outbox, n)
            if not os.path.exists(src):
                continue  # ja publicado em release anterior; publish_release nao reapaga
            pack_entries.append({'sourcePath': src, 'name': n, **{k: self.up['packs'][n][k] for k in ('sha256', 'bytes')}})
        plan = {
            'order': pack_entries + [
                {'sourcePath': mp, 'name': mname, 'sha256': rec['sha256'], 'bytes': rec['bytes']},
                {'sourcePath': stable, 'name': STABLE_MANIFEST, 'sha256': rec['sha256'], 'bytes': rec['bytes']},
            ],
            'newPacks': made,
            'items': len(items),
            'itemsOk': sum(1 for r in items.values() if r['h'] == 200 and 'p' in r),
            'rule': 'Publicar no GitHub Release na ordem; se o upload falhar, nao rodar confirm.',
        }
        save(os.path.join(self.outbox, 'UPLOAD_PLAN.json'), plan, indent=1)
        save(self.upath, self.up, indent=1)
        return plan

    def confirm(self):
        plan = load(os.path.join(self.outbox, 'UPLOAD_PLAN.json'), None)
        if not plan:
            return {'status': 'NO_PLAN'}
        done = []
        for x in plan['order']:
            n = x['name']
            rec = self.up['packs'].get(n) or self.up['manifests'].get(n)
            if rec and rec['status'] == 'PENDING':
                rec.update(status='CONFIRMED', confirmedUTC=iso(self.clock()))
                done.append(n)
            p = os.path.join(self.outbox, n)
            if os.path.exists(p):
                os.makedirs(os.path.join(self.out, 'sent'), exist_ok=True)
                if n.startswith('SPS_SEC_MIRROR_MANIFEST_T61') or n == STABLE_MANIFEST:
                    # manifesto estavel fica no outbox para a proxima publicacao; timestamped vai para sent
                    if n != STABLE_MANIFEST:
                        os.replace(p, os.path.join(self.out, 'sent', n))
                else:
                    os.remove(p)
        if os.path.exists(os.path.join(self.outbox, 'UPLOAD_PLAN.json')):
            os.remove(os.path.join(self.outbox, 'UPLOAD_PLAN.json'))
        sent = sorted(os.listdir(os.path.join(self.out, 'sent'))) if os.path.isdir(os.path.join(self.out, 'sent')) else []
        for old in sent[:-5]:
            os.remove(os.path.join(self.out, 'sent', old))
        save(self.upath, self.up, indent=1)
        return {'status': 'CONFIRMED', 'files': done}

    def status(self):
        return {'pendingPacks': [n for n, v in self.up['packs'].items() if v['status'] == 'PENDING'],
                'confirmedPacks': sum(1 for v in self.up['packs'].values() if v['status'] == 'CONFIRMED'),
                'planExists': os.path.exists(os.path.join(self.outbox, 'UPLOAD_PLAN.json'))}


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    cfg = json.load(open(os.path.join(here, 'config.json')))
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'prepare'
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(here, 'data')
    u = Uploader(out, cfg)
    try:
        res = {'prepare': u.prepare, 'confirm': u.confirm, 'status': u.status}[cmd]()
    except Exception as e:
        print('SPS_SEC_UPLOAD_T61 ' + json.dumps({'status': 'ERRO', 'error': type(e).__name__, 'message': str(e)[:200]}))
        return 2
    if cmd == 'prepare':
        res = {'status': 'PLAN_READY', 'files': [(x['name'], x['bytes']) for x in res['order']], 'items': res['items'], 'itemsOk': res['itemsOk'],
               'plan': os.path.join(u.outbox, 'UPLOAD_PLAN.json')}
    print('SPS_SEC_UPLOAD_T61 ' + json.dumps(res))
    return 0


if __name__ == '__main__':
    sys.exit(main())
