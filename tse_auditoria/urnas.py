"""Logs das urnas eletrônicas: horário de cada voto e tipo de habilitação do eleitor (biometria).

Fontes oficiais (TSE):
  1º turno: "Resultados 2026 - Arquivos transmitidos para totalização" (um ZIP por UF, ~95 GB no total).
            Cada ZIP é baixado para dados/tmp, processado e apagado; fica registrado o SHA256.
  2º turno: arquivos por urna em resultados.tse.jus.br/oficial/ele2026/arquivo-urna/3221
            (publicados na noite da eleição); cada log é lido em memória, sem gravar em disco.

O log (logd.dat, dentro do .jez, que é um ZIP) tem uma linha por evento:
  data hora <TAB> nível <TAB> id da urna <TAB> aplicação <TAB> mensagem <TAB> hash
Para cada eleitor, a sequência é: "Aguardando digitação do identificador do eleitor" →
[digital: "Tipo de habilitação do eleitor [biométrica]"] → "Eleitor foi habilitado" →
"Voto confirmado para [...]" → "O voto do eleitor foi computado".
Só são guardadas contagens. O log não registra em quem o eleitor votou.
"""
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import time
import zipfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .catalogo import official_url

PLEITO = {'03220': 1, '03221': 2}
DATA_ELEICAO = {1: '04/10/2026', 2: '25/10/2026'}
WEB = 'https://resultados.tse.jus.br/oficial/ele2026/arquivo-urna'
NOME_ARQ = re.compile(r'o(\d{5})([a-z]{2})(\d{5})(\d{4})(\d{4})-log\.jez$')
TENT = re.compile(r'Capturada a digital\. Tentativa \[(\d+)\]')
UA = {'User-Agent': 'AuditoriaTSE2026/1.0 (+uso pessoal; dados publicos)'}

ESQUEMA = '''
CREATE TABLE IF NOT EXISTS log_secao (turno INTEGER, uf TEXT, mun TEXT, zona TEXT, secao TEXT,
    votos INTEGER, bio INTEGER, sem_bio INTEGER, tent_mais1 INTEGER, primeiro TEXT, ultimo TEXT,
    apos15 INTEGER, apos16 INTEGER, apos17 INTEGER, fora_data INTEGER, dur_soma INTEGER, dur_n INTEGER, fonte TEXT, sha256 TEXT,
    PRIMARY KEY (turno, uf, mun, zona, secao));
CREATE TABLE IF NOT EXISTS log_minuto (turno INTEGER, uf TEXT, mun TEXT, minuto TEXT, votos INTEGER, bio INTEGER);
CREATE TABLE IF NOT EXISTS log_duracao (turno INTEGER, uf TEXT, faixa INTEGER, eleitores INTEGER);
CREATE TABLE IF NOT EXISTS log_tentativas (turno INTEGER, uf TEXT, tentativas INTEGER, eleitores INTEGER);
CREATE TABLE IF NOT EXISTS log_fontes (turno INTEGER, uf TEXT, url TEXT, bytes INTEGER, sha256 TEXT,
    urnas INTEGER, processado_em TEXT, PRIMARY KEY (turno, uf));
'''


def ler_log(nome, dados):
    """Processa um .jez (bytes). Retorna dict com contagens, ou None se não for log de votação."""
    m = NOME_ARQ.search(nome)
    if not m:
        return None
    pleito, uf, mun, zona, secao = m.groups()
    turno = PLEITO.get(pleito)
    if not turno:
        return None
    try:
        z = zipfile.ZipFile(io.BytesIO(dados))
        txt = z.read('logd.dat').decode('latin-1')
    except Exception:
        return {'erro': True, 'turno': turno, 'uf': uf.upper(), 'mun': str(int(mun)), 'zona': str(int(zona)), 'secao': str(int(secao))}
    dia = DATA_ELEICAO[turno]
    tipo = None; tent = 0; t_hab = None; hab_bio = False
    votos = bio = sem_bio = tent_mais1 = fora = dur_soma = dur_n = 0
    primeiro = ultimo = None
    minuto = Counter(); minuto_bio = Counter(); dur = Counter(); tent_c = Counter(); apos = Counter()
    for linha in txt.splitlines():
        p = linha.split('\t')
        if len(p) < 5:
            continue
        msg = p[4]
        if msg.startswith('Aguardando digita'):
            tipo = None; tent = 0
        elif msg.startswith('Tipo de habilita'):
            tipo = msg
        elif msg.startswith('Capturada a digital'):
            mt = TENT.search(msg)
            if mt:
                tent = max(tent, int(mt.group(1)))
        elif msg == 'Eleitor foi habilitado':
            hab_bio = tipo is not None and 'biom' in tipo
            t_hab = p[0]
        elif msg == 'O voto do eleitor foi computado':
            data, hora = p[0][:10], p[0][11:19]
            if data != dia:
                # Eventos de outra data: o log do 2º turno carrega o do 1º; também há testes e relógio errado.
                fora += 1
                hab_bio = False; t_hab = None; tipo = None
                continue
            votos += 1
            if hab_bio:
                bio += 1
                tent_c[min(tent, 4)] += 1
                if tent > 1:
                    tent_mais1 += 1
            else:
                sem_bio += 1
            if True:
                mi = hora[:5]
                minuto[mi] += 1
                if hab_bio:
                    minuto_bio[mi] += 1
                if primeiro is None or hora < primeiro:
                    primeiro = hora
                if ultimo is None or hora > ultimo:
                    ultimo = hora
                # Horário local da urna; depois convertido para Brasília (fuso de 0, 1 ou 2 horas).
                for lim in (15, 16, 17):
                    if hora >= f'{lim}:00:00':
                        apos[lim] += 1
                if t_hab and t_hab[:10] == data:
                    try:
                        s = (int(hora[:2]) * 3600 + int(hora[3:5]) * 60 + int(hora[6:8])) - \
                            (int(t_hab[11:13]) * 3600 + int(t_hab[14:16]) * 60 + int(t_hab[17:19]))
                        if 0 <= s < 3600:
                            dur_soma += s; dur_n += 1; dur[min(s // 10, 60)] += 1
                    except ValueError:
                        pass
            hab_bio = False; t_hab = None; tipo = None
    return {'turno': turno, 'uf': uf.upper(), 'mun': str(int(mun)), 'zona': str(int(zona)), 'secao': str(int(secao)),
            'votos': votos, 'bio': bio, 'sem_bio': sem_bio, 'tent_mais1': tent_mais1, 'primeiro': primeiro, 'ultimo': ultimo,
            'apos15': apos[15], 'apos16': apos[16], 'apos17': apos[17], 'fora': fora, 'dur_soma': dur_soma, 'dur_n': dur_n,
            'minuto': dict(minuto), 'minuto_bio': dict(minuto_bio), 'dur': dict(dur), 'tent': dict(tent_c),
            'sha256': hashlib.sha256(dados).hexdigest()}


class Acumulador:
    """Junta os resultados das urnas de uma UF e grava no SQLite de uma vez."""
    def __init__(self, turno, uf, fonte):
        self.turno, self.uf, self.fonte = turno, uf, fonte
        self.secoes = []; self.minuto = Counter(); self.minuto_bio = Counter()
        self.dur = Counter(); self.tent = Counter(); self.erros = 0

    def add(self, r):
        if r is None:
            return
        if r.get('erro'):
            self.erros += 1; return
        self.secoes.append((r['turno'], r['uf'], r['mun'], r['zona'], r['secao'], r['votos'], r['bio'], r['sem_bio'],
                            r['tent_mais1'], r['primeiro'], r['ultimo'], r['apos15'], r['apos16'], r['apos17'], r['fora'],
                            r['dur_soma'], r['dur_n'], self.fonte, r['sha256']))
        for k, v in r['minuto'].items():
            self.minuto[(r['mun'], k)] += v
        for k, v in r['minuto_bio'].items():
            self.minuto_bio[(r['mun'], k)] += v
        for k, v in r['dur'].items():
            self.dur[int(k)] += v
        for k, v in r['tent'].items():
            self.tent[int(k)] += v

    def gravar(self, db, url, nbytes, sha):
        conn = sqlite3.connect(db, timeout=7200)   # espera outras gravações longas (ex.: agregar)
        conn.executescript(ESQUEMA)
        t, uf = self.turno, self.uf
        with conn:
            for tab in ('log_secao', 'log_minuto', 'log_duracao', 'log_tentativas'):
                conn.execute(f'DELETE FROM {tab} WHERE turno=? AND uf=?', (t, uf))
            conn.executemany('INSERT OR REPLACE INTO log_secao VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', self.secoes)
            conn.executemany('INSERT INTO log_minuto VALUES (?,?,?,?,?,?)',
                             [(t, uf, m, mi, v, self.minuto_bio.get((m, mi), 0)) for (m, mi), v in self.minuto.items()])
            conn.executemany('INSERT INTO log_duracao VALUES (?,?,?,?)', [(t, uf, k, v) for k, v in self.dur.items()])
            conn.executemany('INSERT INTO log_tentativas VALUES (?,?,?,?)', [(t, uf, k, v) for k, v in self.tent.items()])
            conn.execute('INSERT OR REPLACE INTO log_fontes VALUES (?,?,?,?,?,?,?)',
                         (t, uf, url, nbytes, sha, len(self.secoes), datetime.now(timezone.utc).isoformat()))
        conn.close()
        print(f'[OK] {t}º turno {uf}: {len(self.secoes):,} urnas, {sum(s[5] for s in self.secoes):,} votos'
              f'{f", {self.erros} logs ilegíveis" if self.erros else ""}', flush=True)


def _ler_membro(args):
    caminho, nome = args
    with zipfile.ZipFile(caminho) as z:
        return ler_log(nome, z.read(nome))


def _feitos(db, turno):
    try:
        conn = sqlite3.connect(db, timeout=60)
        conn.executescript(ESQUEMA)
        r = {u for (u,) in conn.execute('SELECT uf FROM log_fontes WHERE turno=?', (turno,))}
        conn.close()
        return r
    except sqlite3.Error:
        return set()


def processar_1t(db, root, catalogo, ufs=None, refazer=False, processos=None):
    """Baixa o ZIP de arquivos transmitidos de cada UF, extrai as contagens dos logs e apaga o ZIP."""
    pacote = next(p for p in catalogo['conjuntos'] if p['slug'] == 'resultados-2026-arquivos-transmitidos-para-totalizacao')
    tmp = Path(root) / 'tmp'
    tmp.mkdir(parents=True, exist_ok=True)
    feitos = set() if refazer else _feitos(db, 1)
    recursos = []
    for r in pacote['recursos']:
        m = re.search(r'_1t_([A-Z]{2})\.zip$', r['url'])
        if m and (not ufs or m.group(1) in ufs) and m.group(1) not in feitos:
            recursos.append((m.group(1), r['url']))
    # Menores primeiro: resultados aparecem logo e o disco fica livre para SP no fim.
    tamanhos = {}
    for uf, url in recursos:
        with urlopen(Request(url, method='HEAD', headers=UA), timeout=60) as h:
            tamanhos[uf] = int(h.headers.get('Content-Length') or 0)
    recursos.sort(key=lambda x: tamanhos[x[0]])
    print(f'1º turno: {len(recursos)} UFs a processar ({sum(tamanhos.values())/1e9:.1f} GB)', flush=True)
    for uf, url in recursos:
        livre = shutil.disk_usage(tmp).free
        if livre < tamanhos[uf] + 4e9:
            print(f'[PULADO] {uf}: precisa de {tamanhos[uf]/1e9:.1f} GB + 4 GB de folga; livre {livre/1e9:.1f} GB', flush=True)
            continue
        if not official_url(url):
            raise ValueError(url)
        destino = tmp / Path(url).name
        t0 = time.time(); h = hashlib.sha256()
        with urlopen(Request(url, headers=UA), timeout=120) as resp, open(destino, 'wb') as f:
            while True:
                b = resp.read(4 << 20)
                if not b:
                    break
                f.write(b); h.update(b)
        print(f'  {uf}: baixado {tamanhos[uf]/1e9:.2f} GB em {time.time()-t0:.0f}s; processando logs...', flush=True)
        acc = Acumulador(1, uf, Path(url).name)
        with zipfile.ZipFile(destino) as z:
            nomes = [n for n in z.namelist() if n.endswith('-log.jez')]
        with ProcessPoolExecutor(max_workers=processos or max(2, (os.cpu_count() or 4) - 1)) as ex:
            for r in ex.map(_ler_membro, [(str(destino), n) for n in nomes], chunksize=64):
                acc.add(r)
        acc.gravar(db, url, destino.stat().st_size, h.hexdigest())
        destino.unlink()


def _get(url, tentativas=4):
    for n in range(tentativas):
        try:
            with urlopen(Request(url, headers=UA), timeout=60) as r:
                return r.read()
        except HTTPError as e:
            if e.code == 404:
                return None
            if n == tentativas - 1:
                raise
        except Exception:
            if n == tentativas - 1:
                raise
        time.sleep(1 + 2 * n)


def _log_2t(uf, mun, zona, secao):
    base = f'{WEB}/3221/dados/{uf}/{mun}/{zona}/{secao}'
    aux = _get(f'{base}/p003221-{uf}-m{mun}-z{zona}-s{secao}-aux.json')
    if not aux:
        return None
    d = json.loads(aux)
    for hh in d.get('hashes', []):
        for a in hh.get('arq', []):
            if a.get('tp') == 'log':
                b = _get(f"{base}/{hh['hash']}/{a['nm']}")
                if b:
                    return ler_log(a['nm'], b)
    return None


def processar_2t(db, ufs=None, refazer=False, paralelo=32):
    """Lê o log de cada urna do 2º turno direto do site de resultados (sem gravar arquivos)."""
    cfg_ufs = ufs or ['ac', 'al', 'am', 'ap', 'ba', 'ce', 'df', 'es', 'go', 'ma', 'mg', 'ms', 'mt', 'pa', 'pb', 'pe',
                      'pi', 'pr', 'rj', 'rn', 'ro', 'rr', 'rs', 'sc', 'se', 'sp', 'to', 'zz']
    feitos = set() if refazer else _feitos(db, 2)
    for uf in [u.lower() for u in cfg_ufs]:
        if uf.upper() in feitos:
            continue
        cfg_url = f'{WEB}/3221/config/{uf}/{uf}-p003221-cs.json'
        cfg_b = _get(cfg_url)
        if not cfg_b:
            print(f'[aviso] 2º turno {uf.upper()}: configuração não encontrada', flush=True)
            continue
        cfg = json.loads(cfg_b)
        tarefas = [(uf, m['cd'], z['cd'], s['ns']) for a in cfg['abr'] for m in a['mu'] for z in m['zon'] for s in z['sec']]
        t0 = time.time()
        acc = Acumulador(2, uf.upper(), 'arquivo-urna/3221')
        falhas = 0
        with ThreadPoolExecutor(max_workers=paralelo) as ex:
            futuros = [ex.submit(_log_2t, *t) for t in tarefas]
            for i, f in enumerate(as_completed(futuros), 1):
                try:
                    r = f.result()
                    if r is None:
                        falhas += 1
                    acc.add(r)
                except Exception:
                    falhas += 1
                if i % 20000 == 0:
                    print(f'  2º turno {uf.upper()}: {i:,}/{len(tarefas):,} urnas ({time.time()-t0:.0f}s)', flush=True)
        if falhas:
            print(f'  2º turno {uf.upper()}: {falhas} urnas sem log (sem arquivo publicado ou erro de rede)', flush=True)
        acc.gravar(db, cfg_url, len(cfg_b), hashlib.sha256(cfg_b).hexdigest())
