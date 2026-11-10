# sps-sec-mirror

Espelho público dos dados da SEC (EDGAR) usado pelo AI Consensus da SPS Analytics.
Os arquivos espelhados são registros públicos da SEC. O repositório não contém chaves, tokens nem o User-Agent declarado (ele fica só no secret `SPS_SEC_UA` do GitHub Actions).

## Como roda

O workflow `.github/workflows/mirror.yml` baixa da SEC com ritmo de 0,5 req/s (2 s entre pedidos), para no primeiro 403 ou 429, não faz loop de retry e não tenta trocar de IP. O índice do dia corrente (ET) é ignorado: o `master.idx` desse dia ainda não foi publicado.

A saída é a mesma do espelho do box: packs `.bin` (membros gzip concatenados) e um manifesto JSON no esquema `SPS_SEC_MIRROR_MANIFEST_T61_V1`, com offset, tamanho e SHA-256 de cada URL. O Apps Script lê o manifesto e o trecho do pack (HTTP Range) e confere o hash.

Publicação: GitHub Release (não o git, porque os packs passam de 50 MB).

- Prova (`mode=proof`): tag `proof` — últimos dias úteis de índice + 3 corpos.
- Espelho completo (`mode=full`): tag `mirror` (latest) — pode ser disparado de novo para continuar pedaços. Sem agendamento até a prova e o ciclo completo estarem ok.

O cron 3×/dia (10:00, 16:00 e 22:00 UTC) está no YAML, **comentado**, até o funcionamento estar comprovado.

## Disparo manual

```bash
gh workflow run mirror.yml -R SPStocks/sps-sec-mirror -f mode=proof
gh workflow run mirror.yml -R SPStocks/sps-sec-mirror -f mode=full
```

## Leitura (Apps Script)

Manifesto estável:

`https://github.com/SPStocks/sps-sec-mirror/releases/latest/download/SPS_SEC_MIRROR_MANIFEST_T61.json`

Packs no mesmo release, pelo nome que o manifesto indica no campo `p`.

O override proposto `SPS_SecMirrorGithubT65.gs` (ainda não aplicado) lê esse release, com fallback para o espelho do Drive (T61) e rollback por Script Property.
