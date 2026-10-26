"""Download binário auditável com retry, versão e SHA256."""
import hashlib
import json
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from .catalogo import official_url


def recursos_filtrados(catalogo, perfil='presidencial', dataset=None, uf=None, termo=None):
    ufs = {x.strip().upper() for x in (uf or '').split(',') if x.strip()}
    available_slugs = {p['slug'].lower() for p in catalogo['conjuntos']}
    for package in catalogo['conjuntos']:
        slug = package['slug'].lower()
        if dataset:
            # Um slug completo restringe a um único conjunto; abreviações são buscas parciais.
            if dataset.lower() in available_slugs and slug != dataset.lower():
                continue
            if dataset.lower() not in available_slugs and dataset.lower() not in slug:
                continue
        if perfil == 'presidencial' and slug != 'resultados-2026':
            continue
        if perfil == 'resultados' and not slug.startswith('resultados-2026'):
            continue
        for resource in package['recursos']:
            url = resource['url']
            filename = Path(urlparse(url).path).name
            description = (resource['nome'] + ' ' + filename).lower()
            if perfil == 'presidencial' and 'votacao_secao_2026_br' not in description:
                continue
            if termo and termo.casefold() not in description.casefold():
                continue
            if ufs:
                # filtro por UF com limites de palavra, mantendo arquivos nacionais BR.
                if not any(re.search(r'(^|[_\-\s])' + re.escape(state.lower()) + r'([_\-.\s]|$)', description) for state in ufs):
                    continue
            yield package, resource


def sha256_file(path, block=1024*1024):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(block), b''):
            h.update(chunk)
    return h.hexdigest()


def pasta_originais(root, originais=None):
    """Pasta dos downloads originais; pode ficar fora de `root` (ex.: Google Drive)."""
    return Path(originais) if originais else Path(root)/'originais'


def baixar_um(root, pacote, recurso, force=False, tentativas=3, originais=None):
    url = recurso['url']
    if not official_url(url):
        raise ValueError(f'URL recusada: {url}')
    name = Path(urlparse(url).path).name
    if not name or name in ('.', '..'):
        raise ValueError('Nome do recurso inválido')
    directory = pasta_originais(root, originais)/pacote['slug']
    directory.mkdir(parents=True, exist_ok=True)
    # ID do recurso evita colisões entre diferentes URLs com mesmo basename.
    tag = hashlib.sha256(url.encode()).hexdigest()[:10]
    dest = directory / f'{tag}_{name}'
    info_path = dest.with_name(dest.name + '.origem.json')
    if dest.exists() and info_path.exists():
        meta = json.loads(info_path.read_text(encoding='utf-8'))
        if not force:
            digest = sha256_file(dest)
            if digest == meta.get('sha256'):
                print(f'[igual] {dest.name} ({dest.stat().st_size:,} bytes)')
                return dest
            print('[aviso] O hash local divergiu; baixando novamente')
        else:
            # --atualizar: pergunta ao servidor se a versão mudou antes de baixar tudo de novo.
            try:
                with urlopen(Request(url, method='HEAD', headers={'User-Agent': 'AuditoriaTSE2026/1.0'}), timeout=60) as h:
                    etag, lm = h.headers.get('ETag'), h.headers.get('Last-Modified')
                if etag and etag == meta.get('etag') and lm == meta.get('ultima_alteracao_http'):
                    print(f'[sem mudança no TSE] {dest.name}')
                    return dest
            except Exception as e:
                print(f'[aviso] não foi possível consultar a versão ({e}); baixando')
        # Guarda a versão anterior fora da pasta de importação, para comparar retificações.
        carimbo = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        arquivo_dir = directory.parent.parent / 'versoes_anteriores' / pacote['slug'] / carimbo
        arquivo_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dest, arquivo_dir / dest.name)
        shutil.copy2(info_path, arquivo_dir / info_path.name)
        print(f'[versão anterior guardada] {arquivo_dir / dest.name}')
    for n in range(1, tentativas+1):
        part = dest.with_name(dest.name + '.part')
        part.unlink(missing_ok=True)
        try:
            req = Request(url, headers={'User-Agent':'AuditoriaTSE2026/1.0', 'Accept': '*/*'})
            with urlopen(req, timeout=120) as res:
                if not official_url(res.url):
                    raise ValueError('URL de redirecionamento não oficial')
                with part.open('wb') as f:
                    shutil.copyfileobj(res, f, length=1024*1024)
                header = {'etag': res.headers.get('ETag'), 'ultima_alteracao_http': res.headers.get('Last-Modified')}
            part.replace(dest)
            meta = {'catalogo': pacote['slug'], 'id_recurso': recurso['id'],
                    'nome_recurso': recurso['nome'], 'url': url,
                    'baixado_em_utc': datetime.now(timezone.utc).isoformat(),
                    'bytes': dest.stat().st_size, 'sha256': sha256_file(dest), **header}
            info_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f'[OK] {dest} SHA256 {meta["sha256"]}')
            return dest
        except Exception as e:
            part.unlink(missing_ok=True)
            if n >= tentativas:
                raise RuntimeError(f'Falha após {tentativas} tentativas: {url} ({e})') from e
            print(f'[retry {n}/{tentativas}] {name}: {e}')
            time.sleep(min(n*2, 8))


def executar(root, catalogo, perfil, dataset=None, uf=None, termo=None, limite=None, force=False, originais=None):
    resources = list(recursos_filtrados(catalogo, perfil, dataset, uf, termo))
    if limite:
        resources = resources[:limite]
    if not resources:
        raise ValueError('Nenhum recurso corresponde aos filtros. Use "recursos" para conferir.')
    print(f'{len(resources)} arquivos selecionados. Downloads oficiais podem consumir dezenas/centenas de GB.')
    failures = []
    for i, (pkg, r) in enumerate(resources, 1):
        print(f'[{i}/{len(resources)}] {pkg["slug"]} :: {r["nome"]}')
        try:
            baixar_um(root, pkg, r, force=force, originais=originais)
        except Exception as ex:
            print(f'[ERRO] {ex}')
            failures.append({'url':r['url'], 'erro':str(ex)})
    if failures:
        file = Path(root)/'falhas_download.json'
        file.write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'[ATENÇÃO] {len(failures)} falhas listadas em {file}')
    return len(resources)-len(failures), failures


def verificar(root, originais=None):
    count = invalid = 0
    for p in pasta_originais(root, originais).rglob('*.origem.json'):
        meta = json.loads(p.read_text(encoding='utf-8'))
        data = p.with_name(p.name.removesuffix('.origem.json'))
        ok = data.exists() and sha256_file(data) == meta['sha256']
        print(('OK    ' if ok else 'FALHA '), str(data))
        count += 1
        invalid += not ok
    print(f'Arquivos: {count}; íntegros localmente: {count-invalid}; divergências: {invalid}')
    return invalid == 0
