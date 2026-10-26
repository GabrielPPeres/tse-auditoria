"""Coleta a totalização oficial por município em resultados.tse.jus.br (mesmo site do app de resultados).

Uso: enquanto o CSV de votação por seção de um turno não é publicado no portal de dados abertos,
estes arquivos trazem, por município: votos de cada candidato, brancos, nulos, eleitorado,
comparecimento e abstenção. Cada JSON é salvo sem edição, com SHA256, em
originais/resultados-web/e<eleição>/<uf>/.

Os códigos de eleição vêm do arquivo oficial de configuração (ele-c.json):
  6257 = Federal 1º turno (Presidente)   6258 = Federal 2º turno
  6259 = Estadual 1º turno               6260 = Estadual 2º turno (Governador)
"""
import hashlib
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .catalogo import official_url

BASE = 'https://resultados.tse.jus.br/oficial/ele2026'
# (código da eleição, turno, cargo)
ELEICOES = [('6257', 1, '1'), ('6258', 2, '1'), ('6260', 2, '3')]


def _get(url, tentativas=4):
    if not official_url(url):
        raise ValueError(f'URL não oficial: {url}')
    for n in range(tentativas):
        try:
            with urlopen(Request(url, headers={'User-Agent': 'AuditoriaTSE2026/1.0 (+uso pessoal; dados publicos)'}), timeout=60) as r:
                if not official_url(r.url):
                    raise ValueError('Redirecionamento para domínio não oficial')
                return r.read()
        except HTTPError as e:
            if e.code == 404:
                return None
            if n == tentativas - 1:
                raise
        except Exception:
            if n == tentativas - 1:
                raise
        time.sleep(1 + n * 2)


def _salvar(pasta, nome, dados, url, manifesto):
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / nome).write_bytes(dados)
    manifesto[str(Path(pasta.name) / nome)] = {
        'url': url, 'bytes': len(dados), 'sha256': hashlib.sha256(dados).hexdigest(),
        'baixado_em_utc': datetime.now(timezone.utc).isoformat()}


def _int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return 0


def coletar(root, originais, atualizar=False, paralelo=8):
    """Baixa config + arquivos municipais de cada eleição. Retorna lista de (ele, turno, cargo, pasta)."""
    feitos = []
    for ele, turno, cargo in ELEICOES:
        E = 'e' + ele.zfill(6)
        pasta = Path(originais) / 'resultados-web' / E
        manifesto_p = pasta / 'manifesto.json'
        manifesto = json.loads(manifesto_p.read_text(encoding='utf-8')) if manifesto_p.exists() else {}
        cfg_url = f'{BASE}/{ele}/config/mun-{E}-cm.json'
        cfg_b = _get(cfg_url)
        if cfg_b is None:
            print(f'[aviso] eleição {ele}: configuração não publicada; pulando')
            continue
        _salvar(pasta, f'mun-{E}-cm.json', cfg_b, cfg_url, manifesto)
        cfg = json.loads(cfg_b)
        c = 'c' + cargo.zfill(4)
        tarefas = []
        for abr in cfg['abr']:
            uf = abr['cd'].lower()
            # Governador: só UFs que tiveram 2º turno (o arquivo da UF existe).
            if cargo == '3':
                url_uf = f'{BASE}/{ele}/dados/{uf}/{uf}-{c}-{E}-u.json'
                b = _get(url_uf)
                if b is None:
                    continue
                _salvar(pasta / uf, f'{uf}-{c}-{E}-u.json', b, url_uf, manifesto)
            for mu in abr['mu']:
                nome = f"{uf}{mu['cd']}-{c}-{E}-u.json"
                destino = pasta / uf / nome
                if destino.exists() and not atualizar:
                    continue
                tarefas.append((uf, nome, f'{BASE}/{ele}/dados/{uf}/{nome}'))
        print(f'Eleição {ele} ({turno}º turno, cargo {cargo}): {len(tarefas)} arquivos a baixar', flush=True)
        falhas = 0
        with ThreadPoolExecutor(max_workers=paralelo) as ex:
            futuros = {ex.submit(_get, url): (uf, nome, url) for uf, nome, url in tarefas}
            for i, f in enumerate(as_completed(futuros), 1):
                uf, nome, url = futuros[f]
                try:
                    b = f.result()
                    if b is None:
                        falhas += 1
                    else:
                        _salvar(pasta / uf, nome, b, url, manifesto)
                except Exception as e:
                    falhas += 1
                    print(f'[ERRO] {url}: {e}')
                if i % 1000 == 0:
                    print(f'  {i}/{len(tarefas)}', flush=True)
        manifesto_p.write_text(json.dumps(manifesto, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'  concluído; {falhas} não encontrados ou com erro', flush=True)
        feitos.append((ele, turno, cargo, pasta))
    return feitos


def importar(db, originais):
    """Lê os JSON salvos e grava web_mun (votos) e web_mun_tot (eleitorado, comparecimento...)."""
    conn = sqlite3.connect(db, timeout=90)
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS web_mun (ele TEXT, turno INTEGER, cargo TEXT, uf TEXT, mun TEXT, municipio TEXT,
        votavel TEXT, nome TEXT, votos INTEGER);
    CREATE TABLE IF NOT EXISTS web_mun_tot (ele TEXT, turno INTEGER, cargo TEXT, uf TEXT, mun TEXT, municipio TEXT,
        eleitorado INTEGER, comparecimento INTEGER, abstencao INTEGER, secoes INTEGER, secoes_totalizadas INTEGER,
        validos INTEGER, brancos INTEGER, nulos INTEGER, gerado TEXT, arquivo TEXT, sha256 TEXT);
    ''')
    total = 0
    for ele, turno, cargo in ELEICOES:
        E = 'e' + ele.zfill(6)
        pasta = Path(originais) / 'resultados-web' / E
        cfg_p = pasta / f'mun-{E}-cm.json'
        if not cfg_p.exists():
            continue
        nomes = {(a['cd'].lower(), m['cd']): m['nm'] for a in json.loads(cfg_p.read_bytes())['abr'] for m in a['mu']}
        c = 'c' + cargo.zfill(4)
        votos, tots = [], []
        for (uf, cd), nm in nomes.items():
            arq = pasta / uf / f'{uf}{cd}-{c}-{E}-u.json'
            if not arq.exists():
                continue
            b = arq.read_bytes()
            d = json.loads(b)
            mun = str(int(cd))              # TSE sem zeros à esquerda, como no CSV de votação por seção
            UF = uf.upper()
            for cg in d.get('carg', []):
                for agr in cg.get('agr', []):
                    for par in agr.get('par', []):
                        for cand in par.get('cand', []):
                            votos.append((ele, turno, cargo, UF, mun, nm, cand['n'], cand['nm'], _int(cand.get('vap'))))
            v, e, s = d.get('v', {}), d.get('e', {}), d.get('s', {})
            if _int(v.get('vb')):
                votos.append((ele, turno, cargo, UF, mun, nm, '95', 'VOTO BRANCO', _int(v.get('vb'))))
            if _int(v.get('tvn')):
                votos.append((ele, turno, cargo, UF, mun, nm, '96', 'VOTO NULO', _int(v.get('tvn'))))
            tots.append((ele, turno, cargo, UF, mun, nm, _int(e.get('te')), _int(e.get('c')), _int(e.get('a')),
                         _int(s.get('ts')), _int(s.get('st')), _int(v.get('vv')), _int(v.get('vb')), _int(v.get('tvn')),
                         f"{d.get('dg')} {d.get('hg')}", str(arq.relative_to(Path(originais))).replace('\\', '/'),
                         hashlib.sha256(b).hexdigest()))
        with conn:
            conn.execute('DELETE FROM web_mun WHERE ele=?', (ele,))
            conn.execute('DELETE FROM web_mun_tot WHERE ele=?', (ele,))
            conn.executemany('INSERT INTO web_mun VALUES (?,?,?,?,?,?,?,?,?)', votos)
            conn.executemany('INSERT INTO web_mun_tot VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', tots)
        print(f'[OK] eleição {ele}: {len(tots):,} municípios, {len(votos):,} linhas de votos', flush=True)
        total += len(votos)
    conn.close()
    return total
