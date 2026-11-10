#!/usr/bin/env python3
"""Publica packs e manifesto no GitHub Release. Nao imprime segredos nem o User-Agent."""
import json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
STABLE = 'SPS_SEC_MIRROR_MANIFEST_T61.json'


def run(cmd, **kw):
    return subprocess.run(cmd, check=False, capture_output=True, text=True, **kw)


def fail(msg):
    print('SPS_SEC_PUBLISH_T65 status=ERRO message=' + msg)
    sys.exit(2)


def main():
    cfg = json.load(open(os.path.join(HERE, 'config.json')))
    data = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, 'data')
    plan_path = os.path.join(data, 'outbox', 'UPLOAD_PLAN.json')
    if not os.path.exists(plan_path):
        fail('UPLOAD_PLAN_AUSENTE')
    plan = json.load(open(plan_path))
    mode = os.environ.get('SPS_SEC_MIRROR_MODE', 'full').strip().lower() or 'full'
    tag = cfg['release_tag_proof'] if mode == 'proof' else cfg['release_tag_full']
    title = 'SEC mirror proof' if mode == 'proof' else 'SEC mirror'
    repo = os.environ.get('GITHUB_REPOSITORY') or 'SPStocks/sps-sec-mirror'

    # Recusa se algum arquivo publicado tiver cara de e-mail no manifesto.
    for item in plan['order']:
        if item['name'].endswith('.json'):
            man = json.load(open(item['sourcePath']))
            blob = json.dumps(man)
            if man.get('userAgent') != 'DECLARED' or '@' in blob:
                fail('MANIFESTO_COM_DADO_SENSIVEL')

    files = []
    for item in plan['order']:
        if os.path.exists(item['sourcePath']):
            files.append(item['sourcePath'])
    if not files:
        fail('NENHUM_ARQUIVO')

    view = run(['gh', 'release', 'view', tag, '-R', repo, '--json', 'tagName'])
    if view.returncode != 0:
        notes = 'Espelho publico da SEC (dados ja publicos). Manifesto estavel: %s. Modo: %s.' % (STABLE, mode)
        created = run(['gh', 'release', 'create', tag, '-R', repo, '-t', title, '-n', notes] + (['--latest'] if mode == 'full' else ['--latest=false']))
        if created.returncode != 0:
            fail('RELEASE_CREATE_FALHOU')
        print('SPS_SEC_PUBLISH_T65 created_tag=' + tag)

    up = run(['gh', 'release', 'upload', tag, '-R', repo, '--clobber'] + files)
    if up.returncode != 0:
        # nao despejar stderr completo: pode ecoar caminhos, nao deve ecoar secret
        err = (up.stderr or up.stdout or '').splitlines()
        fail('RELEASE_UPLOAD_FALHOU lines=%s' % len(err))

    confirm = run([sys.executable, os.path.join(HERE, 'pack_mirror.py'), 'confirm', data])
    print((confirm.stdout or '').strip())
    if confirm.returncode != 0:
        fail('CONFIRM_FALHOU')

    summary = {
        'status': 'PUBLISHED',
        'tag': tag,
        'mode': mode,
        'files': [os.path.basename(f) for f in files],
        'items': plan.get('items'),
        'itemsOk': plan.get('itemsOk'),
        'url': 'https://github.com/%s/releases/tag/%s' % (repo, tag),
        'manifestUrl': 'https://github.com/%s/releases/download/%s/%s' % (repo, tag, STABLE),
    }
    print('SPS_SEC_PUBLISH_T65 ' + json.dumps(summary, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
