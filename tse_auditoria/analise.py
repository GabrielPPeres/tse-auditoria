"""Tabelas agregadas e consultas do painel de análise.

As tabelas `ag_*` são derivadas da tabela `votos` (que por sua vez vem dos arquivos
oficiais). Nada aqui é digitado à mão, exceto a lista de regiões do IBGE e as siglas
curtas dos partidos (os nomes completos vêm dos votos de legenda nos próprios dados).
"""
import sqlite3
import time
from pathlib import Path

BRANCO, NULO = '95', '96'

REGIAO = {
    'AC': 'Norte', 'AM': 'Norte', 'AP': 'Norte', 'PA': 'Norte', 'RO': 'Norte', 'RR': 'Norte', 'TO': 'Norte',
    'AL': 'Nordeste', 'BA': 'Nordeste', 'CE': 'Nordeste', 'MA': 'Nordeste', 'PB': 'Nordeste',
    'PE': 'Nordeste', 'PI': 'Nordeste', 'RN': 'Nordeste', 'SE': 'Nordeste',
    'DF': 'Centro-Oeste', 'GO': 'Centro-Oeste', 'MS': 'Centro-Oeste', 'MT': 'Centro-Oeste',
    'ES': 'Sudeste', 'MG': 'Sudeste', 'RJ': 'Sudeste', 'SP': 'Sudeste',
    'PR': 'Sul', 'RS': 'Sul', 'SC': 'Sul',
    'ZZ': 'Exterior',
}
REGIOES = ['Norte', 'Nordeste', 'Centro-Oeste', 'Sudeste', 'Sul']

# Siglas curtas por número; o nome completo de cada partido vem dos dados (votos de legenda).
SIGLA = {
    '10': 'REPUBLICANOS', '11': 'PP', '12': 'PDT', '13': 'PT', '14': 'MISSÃO', '15': 'MDB',
    '16': 'PSTU', '18': 'REDE', '20': 'PODE', '21': 'PCB', '22': 'PL', '23': 'CIDADANIA',
    '25': 'PRD', '27': 'DC', '28': 'PRTB', '29': 'PCO', '30': 'NOVO', '33': 'PMN',
    '35': 'DEMOCRATA', '36': 'AGIR', '40': 'PSB', '43': 'PV', '44': 'UNIÃO', '45': 'PSDB',
    '50': 'PSOL', '55': 'PSD', '65': 'PCdoB', '70': 'AVANTE', '77': 'SOLIDARIEDADE', '80': 'UP',
    '95': 'Branco', '96': 'Nulo',
}

CARGOS = {'1': 'Presidente', '3': 'Governador', '5': 'Senador', '6': 'Deputado Federal',
          '7': 'Deputado Estadual', '8': 'Deputado Distrital'}

AGREGADOS_SQL = [
    ('ag_mun_cand', '''
        CREATE TABLE ag_mun_cand AS
        SELECT turno, cargo_codigo AS cargo, uf, municipio_codigo AS mun, MAX(municipio_nome) AS municipio,
               votavel, MAX(nome_votavel) AS nome, SUM(quantidade) AS votos
        FROM votos GROUP BY turno, cargo_codigo, uf, municipio_codigo, votavel'''),
    ('ag_mun', '''
        CREATE TABLE ag_mun AS
        SELECT turno, cargo, uf, mun, MAX(municipio) AS municipio, substr(votavel, 1, 2) AS partido,
               SUM(votos) AS votos
        FROM ag_mun_cand GROUP BY turno, cargo, uf, mun, substr(votavel, 1, 2)'''),
    ('ag_cand', '''
        CREATE TABLE ag_cand AS
        SELECT turno, cargo, uf, votavel, MAX(nome) AS nome, substr(votavel, 1, 2) AS partido,
               SUM(votos) AS votos
        FROM ag_mun_cand GROUP BY turno, cargo, uf, votavel'''),
    ('ag_mun_tot', '''
        CREATE TABLE ag_mun_tot AS
        SELECT turno, cargo, uf, mun, MAX(municipio) AS municipio, SUM(votos) AS total,
               SUM(CASE WHEN partido = '95' THEN votos ELSE 0 END) AS brancos,
               SUM(CASE WHEN partido = '96' THEN votos ELSE 0 END) AS nulos,
               SUM(CASE WHEN partido NOT IN ('95','96') THEN votos ELSE 0 END) AS validos
        FROM ag_mun GROUP BY turno, cargo, uf, mun'''),
    ('ag_secao', '''
        CREATE TABLE ag_secao AS
        SELECT turno, cargo_codigo AS cargo, uf, municipio_codigo AS mun, zona, secao,
               votavel AS partido, SUM(quantidade) AS votos
        FROM votos WHERE cargo_codigo IN ('1', '3')
        GROUP BY turno, cargo_codigo, uf, municipio_codigo, zona, secao, votavel'''),
    ('ag_secao_tot', '''
        CREATE TABLE ag_secao_tot AS
        SELECT turno, cargo, uf, mun, zona, secao, SUM(votos) AS total,
               SUM(CASE WHEN partido = '95' THEN votos ELSE 0 END) AS brancos,
               SUM(CASE WHEN partido = '96' THEN votos ELSE 0 END) AS nulos,
               SUM(CASE WHEN partido NOT IN ('95','96') THEN votos ELSE 0 END) AS validos
        FROM ag_secao GROUP BY turno, cargo, uf, mun, zona, secao'''),
    ('ag_partidos', '''
        CREATE TABLE ag_partidos AS
        SELECT votavel AS numero, MAX(nome) AS nome FROM ag_cand
        WHERE cargo IN ('6', '7', '8') AND length(votavel) = 2 AND votavel NOT IN ('95', '96')
        GROUP BY votavel'''),
]

INDICES_SQL = '''
DROP INDEX IF EXISTS ix_ag_mun;
DROP INDEX IF EXISTS ix_ag_secao_tot;
CREATE INDEX IF NOT EXISTS ix_ag_mun2 ON ag_mun(turno, cargo, partido, uf, mun);
CREATE INDEX IF NOT EXISTS ix_ag_mun_tot ON ag_mun_tot(turno, cargo, uf, mun);
CREATE INDEX IF NOT EXISTS ix_ag_cand ON ag_cand(turno, cargo, uf, partido);
CREATE INDEX IF NOT EXISTS ix_ag_secao ON ag_secao(turno, cargo, partido, uf);
CREATE INDEX IF NOT EXISTS ix_ag_secao_tot2 ON ag_secao_tot(turno, cargo, uf, mun, zona, secao);
'''


BASE = ('ag_mun_cand', 'ag_secao', 'ag_secao_tot')            # lidas de `votos` (lentas)
DERIVADAS = ('ag_mun', 'ag_cand', 'ag_mun_tot', 'ag_partidos')  # lidas de ag_mun_cand (segundos)


def _criar(conn, nomes):
    sqls = dict(AGREGADOS_SQL)
    for nome in nomes:
        t = time.time()
        with conn:
            conn.execute(f'DROP TABLE IF EXISTS {nome}')
            conn.execute(sqls[nome])
        n = conn.execute(f'SELECT COUNT(*) FROM {nome}').fetchone()[0]
        print(f'[OK] {nome}: {n:,} linhas ({time.time()-t:.0f}s)', flush=True)


def mesclar_web(conn):
    """Põe em ag_mun_cand a totalização por município (web_mun) só para turno+cargo sem CSV por seção.
    Quando o TSE publicar o CSV daquele turno, a fonte por seção passa a valer sozinha."""
    conn.execute('CREATE TABLE IF NOT EXISTS ag_web_inserido (turno INTEGER, cargo TEXT)')
    with conn:
        for turno, cargo in conn.execute('SELECT turno, cargo FROM ag_web_inserido').fetchall():
            conn.execute('DELETE FROM ag_mun_cand WHERE turno=? AND cargo=?', (turno, cargo))
        conn.execute('DELETE FROM ag_web_inserido')
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='web_mun'").fetchone():
        return []
    usados = []
    for turno, cargo in conn.execute('SELECT DISTINCT turno, cargo FROM web_mun').fetchall():
        tem_csv = conn.execute('SELECT 1 FROM votos WHERE cargo_codigo=? AND turno=? LIMIT 1', (cargo, turno)).fetchone()
        if tem_csv:
            print(f'  {turno}º turno, cargo {cargo}: há CSV por seção; a totalização web fica só para conferência')
            continue
        with conn:
            conn.execute('''INSERT INTO ag_mun_cand
                SELECT turno, cargo, uf, mun, MAX(municipio), votavel, MAX(nome), SUM(votos)
                FROM web_mun WHERE turno=? AND cargo=? GROUP BY turno, cargo, uf, mun, votavel''', (turno, cargo))
            conn.execute('INSERT INTO ag_web_inserido VALUES (?, ?)', (turno, cargo))
        usados.append((turno, cargo))
        print(f'  {turno}º turno, cargo {cargo}: usando a totalização por município (CSV por seção ainda não publicado)')
    return usados


def agregar(db, somente_web=False):
    """Recria as tabelas ag_*. Com somente_web=True, só refaz a mescla web + derivadas (segundos)."""
    conn = sqlite3.connect(db, timeout=90)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA cache_size=-400000')
    if not somente_web:
        _criar(conn, BASE)
    mesclar_web(conn)
    _criar(conn, DERIVADAS)
    conn.executescript(INDICES_SQL)
    conn.execute('ANALYZE')
    conn.close()
    print('Agregados concluídos.', flush=True)


# ----------------------------------------------------------------------------
# Consultas do painel (somente leitura)

def _ro(db):
    conn = sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro', uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args)]


def _uf_clause(uf, col='uf'):
    return (f' AND {col} = ?', [uf.upper()]) if uf else ('', [])


def nomes_partidos(conn):
    nomes = {r['numero']: r['nome'] for r in conn.execute('SELECT numero, nome FROM ag_partidos')}
    return {n: {'sigla': SIGLA.get(n, nomes.get(n, n)), 'nome': nomes.get(n, SIGLA.get(n, n))}
            for n in set(nomes) | set(SIGLA)}


def meta(db):
    with _ro(db) as conn:
        tabelas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        nomes = [n for n, _ in AGREGADOS_SQL]
        prontas = sum(1 for n in nomes if n in tabelas)
        if prontas < len(nomes):
            return {'pronto': False, 'etapas': len(nomes), 'prontas': prontas,
                    'aviso': ('Preparando os dados para os gráficos' if prontas else
                              'Os dados ainda não foram preparados. Rode "py -3 main.py agregar".')}
        turnos = [r[0] for r in conn.execute('SELECT DISTINCT turno FROM ag_mun_tot ORDER BY turno')]
        cargos = [{'codigo': r[0], 'nome': CARGOS.get(r[0], r[0])}
                  for r in conn.execute('SELECT DISTINCT cargo FROM ag_mun_tot ORDER BY CAST(cargo AS INT)')]
        por_turno = {}
        for t, c in conn.execute('SELECT DISTINCT turno, cargo FROM ag_mun_tot ORDER BY turno, CAST(cargo AS INT)'):
            por_turno.setdefault(str(t), []).append(c)
        web = set()
        if 'ag_web_inserido' in tabelas:
            web = {f'{t}-{c}' for t, c in conn.execute('SELECT turno, cargo FROM ag_web_inserido')}
        gerado = None
        if 'web_mun_tot' in tabelas:
            gerado = conn.execute("SELECT MAX(gerado) FROM web_mun_tot WHERE turno=2").fetchone()[0]
        ufs = [r[0] for r in conn.execute('SELECT DISTINCT uf FROM ag_mun_tot ORDER BY uf')]
        return {'pronto': True, 'turnos': turnos, 'cargos': cargos, 'cargos_por_turno': por_turno,
                'fonte_web': sorted(web), 'web_gerado': gerado, 'tem_comparecimento': 'web_mun_tot' in tabelas,
                'ufs': ufs, 'regiao': REGIAO, 'partidos': nomes_partidos(conn)}


def resumo(db, turno, cargo, uf=None):
    """KPIs + votos por candidato (cargos majoritários) ou por partido (proporcionais)."""
    w, a = _uf_clause(uf)
    with _ro(db) as conn:
        tot = conn.execute(f'''SELECT SUM(total) total, SUM(validos) validos, SUM(brancos) brancos,
                                      SUM(nulos) nulos, COUNT(*) municipios
                               FROM ag_mun_tot WHERE turno=? AND cargo=?{w}''', [turno, cargo] + a).fetchone()
        secoes = conn.execute(f'SELECT COUNT(*) FROM ag_secao_tot WHERE turno=? AND cargo=?{w}',
                              [turno, cargo] + a).fetchone()[0] if cargo in ('1', '3') else None
        secoes = secoes or None   # 2º turno via totalização por município: sem dados por seção
        # Presidente, ou governador/senador dentro de uma UF: por candidato.
        # Governador/senador no país inteiro e deputados: por partido (candidatos mudam por UF).
        if cargo == '1' or (uf and cargo in ('3', '5')):
            itens = _rows(conn, f'''SELECT votavel AS chave, MAX(nome) AS nome, partido, SUM(votos) AS votos
                                    FROM ag_cand WHERE turno=? AND cargo=?{w} AND partido NOT IN ('95','96')
                                    GROUP BY votavel ORDER BY votos DESC''', [turno, cargo] + a)
        else:
            legenda = ("SUM(CASE WHEN length(votavel)=2 THEN votos ELSE 0 END)"
                       if cargo in ('6', '7', '8') else 'NULL')
            itens = _rows(conn, f'''SELECT partido AS chave, partido, SUM(votos) AS votos, {legenda} AS legenda
                                    FROM ag_cand WHERE turno=? AND cargo=?{w} AND partido NOT IN ('95','96')
                                    GROUP BY partido ORDER BY votos DESC''', [turno, cargo] + a)
        return {'totais': dict(tot), 'secoes': secoes, 'itens': itens}


def por_uf(db, turno, cargo):
    """Votos por UF e por candidato/partido (top do país) + totais por UF."""
    with _ro(db) as conn:
        totais = _rows(conn, '''SELECT uf, SUM(total) total, SUM(validos) validos, SUM(brancos) brancos,
                                       SUM(nulos) nulos FROM ag_mun_tot WHERE turno=? AND cargo=?
                                GROUP BY uf ORDER BY uf''', [turno, cargo])
        votos = _rows(conn, '''SELECT uf, partido, SUM(votos) votos FROM ag_mun
                               WHERE turno=? AND cargo=? AND partido NOT IN ('95','96')
                               GROUP BY uf, partido''', [turno, cargo])
        return {'totais': totais, 'votos': votos}


def municipios(db, turno, cargo, a, b=None, uf=None):
    """Por município: % dos válidos para A (e B), total de votos, UF e região."""
    w, args = _uf_clause(uf, 't.uf')
    with _ro(db) as conn:
        linhas = _rows(conn, f'''
            SELECT t.uf, t.mun, t.municipio, t.total, t.validos,
                   COALESCE(ma.votos, 0) AS a, COALESCE(mb.votos, 0) AS b
            FROM ag_mun_tot t
            LEFT JOIN ag_mun ma ON ma.turno=t.turno AND ma.cargo=t.cargo AND ma.uf=t.uf AND ma.mun=t.mun AND ma.partido=?
            LEFT JOIN ag_mun mb ON mb.turno=t.turno AND mb.cargo=t.cargo AND mb.uf=t.uf AND mb.mun=t.mun AND mb.partido=?
            WHERE t.turno=? AND t.cargo=? AND t.validos > 0 AND t.uf <> 'ZZ'{w}''',
                       [a, b or '', turno, cargo] + args)
    for r in linhas:
        r['regiao'] = REGIAO.get(r['uf'], '')
    return linhas


def histograma_secoes(db, turno, cargo, partido, uf=None, bins=20):
    """Distribuição do % dos válidos de um partido/candidato por seção (inclui seções com 0 votos)."""
    w, a = _uf_clause(uf)
    with _ro(db) as conn:
        cont = {r[0]: r[1] for r in conn.execute(f'''
            SELECT MIN(CAST(100.0 * s.votos / t.validos / (100.0/?) AS INT), ? - 1) AS bin, COUNT(*)
            FROM ag_secao s JOIN ag_secao_tot t
              ON t.turno=s.turno AND t.cargo=s.cargo AND t.uf=s.uf AND t.mun=s.mun AND t.zona=s.zona AND t.secao=s.secao
            WHERE s.turno=? AND s.cargo=? AND s.partido=? AND t.validos > 0{w.replace('uf', 's.uf')}
            GROUP BY 1''', [bins, bins, turno, cargo, partido] + a)}
        total = conn.execute(f'SELECT COUNT(*) FROM ag_secao_tot WHERE turno=? AND cargo=? AND validos>0{w}',
                             [turno, cargo] + a).fetchone()[0]
        extremos = _rows(conn, f'''
            SELECT s.uf, s.mun, s.zona, s.secao, s.votos, t.validos, t.total,
                   ROUND(100.0 * s.votos / t.validos, 1) AS pct
            FROM ag_secao s JOIN ag_secao_tot t
              ON t.turno=s.turno AND t.cargo=s.cargo AND t.uf=s.uf AND t.mun=s.mun AND t.zona=s.zona AND t.secao=s.secao
            WHERE s.turno=? AND s.cargo=? AND s.partido=? AND t.validos >= 50{w.replace('uf', 's.uf')}
            ORDER BY 1.0 * s.votos / t.validos DESC LIMIT 50''', [turno, cargo, partido] + a)
        nomes = {r[0]: r[1] for r in conn.execute(
            'SELECT DISTINCT uf || mun, municipio FROM ag_mun_tot WHERE turno=? AND cargo=?', [turno, cargo])}
    com_votos = sum(cont.values())
    cont[0] = cont.get(0, 0) + max(total - com_votos, 0)   # seções sem nenhum voto para o partido
    for e in extremos:
        e['municipio'] = nomes.get(e['uf'] + e['mun'], e['mun'])
    largura = 100 / bins
    return {'bins': [{'de': round(i * largura, 2), 'ate': round((i + 1) * largura, 2), 'secoes': cont.get(i, 0)}
                     for i in range(bins)],
            'secoes': total, 'secoes_zero': max(total - com_votos, 0), 'extremos': extremos}


def brancos_nulos_secoes(db, turno, cargo, uf=None, bins=20):
    """Distribuição do % de brancos+nulos por seção."""
    w, a = _uf_clause(uf)
    with _ro(db) as conn:
        cont = {r[0]: r[1] for r in conn.execute(f'''
            SELECT MIN(CAST(100.0 * (brancos + nulos) / total / (100.0/?) AS INT), ? - 1), COUNT(*)
            FROM ag_secao_tot WHERE turno=? AND cargo=? AND total > 0{w} GROUP BY 1''',
                                                  [bins, bins, turno, cargo] + a)}
    largura = 100 / bins
    return [{'de': round(i * largura, 2), 'ate': round((i + 1) * largura, 2), 'secoes': cont.get(i, 0)}
            for i in range(bins)]


def candidatos(db, turno, cargo, uf=None, limite=300):
    """Candidatos (nominais) com partido, para treemap/lollipop dos proporcionais."""
    w, a = _uf_clause(uf)
    with _ro(db) as conn:
        return _rows(conn, f'''SELECT uf, votavel, MAX(nome) nome, partido, SUM(votos) votos FROM ag_cand
                               WHERE turno=? AND cargo=?{w} AND length(votavel) > 2
                               GROUP BY uf, votavel ORDER BY votos DESC LIMIT ?''',
                     [turno, cargo] + a + [int(limite)])


def entre_turnos(db, cargo, a, b):
    """Por município, 1º e 2º turno: votos de A, B, demais, brancos+nulos, válidos e comparecimento."""
    with _ro(db) as conn:
        tem_web = conn.execute("SELECT 1 FROM sqlite_master WHERE name='web_mun_tot'").fetchone()
        linhas = _rows(conn, '''
            SELECT t.turno, t.uf, t.mun, t.municipio, t.total, t.validos, t.brancos + t.nulos AS bn,
                   SUM(CASE WHEN m.partido = ? THEN m.votos ELSE 0 END) AS a,
                   SUM(CASE WHEN m.partido = ? THEN m.votos ELSE 0 END) AS b
            FROM ag_mun_tot t JOIN ag_mun m ON m.turno=t.turno AND m.cargo=t.cargo AND m.uf=t.uf AND m.mun=t.mun
            WHERE t.cargo = ? AND t.turno IN (1, 2) AND m.partido IN (?, ?)
            GROUP BY t.turno, t.uf, t.mun''', [a, b, cargo, a, b])
        comp = {}
        if tem_web:
            for r in conn.execute('''SELECT turno, uf, mun, eleitorado, comparecimento FROM web_mun_tot
                                     WHERE cargo = ?''', [cargo]):
                comp[(r[0], r[1], r[2])] = (r[3], r[4])
    por = {}
    for r in linhas:
        k = (r['uf'], r['mun'])
        d = por.setdefault(k, {'uf': r['uf'], 'mun': r['mun'], 'municipio': r['municipio'],
                               'regiao': REGIAO.get(r['uf'], '')})
        s = str(r['turno'])
        el, co = comp.get((r['turno'], r['uf'], r['mun']), (None, None))
        d.update({'a' + s: r['a'], 'b' + s: r['b'], 'val' + s: r['validos'], 'tot' + s: r['total'],
                  'bn' + s: r['bn'], 'out' + s: r['validos'] - r['a'] - r['b'], 'ele' + s: el, 'comp' + s: co})
    return [d for d in por.values() if 'val1' in d and 'val2' in d]


def conferencia_1t(db):
    """Totalização oficial por município (site de resultados) × soma do CSV por seção, 1º turno, Presidente."""
    with _ro(db) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='web_mun'").fetchone():
            return {'disponivel': False}
        web = {(r[0], r[1], r[2]): r[3] for r in conn.execute(
            "SELECT uf, mun, votavel, votos FROM web_mun WHERE ele='6257'")}
        csv_ = {(r[0], r[1], r[2]): r[3] for r in conn.execute(
            "SELECT uf, mun, votavel, votos FROM ag_mun_cand WHERE turno=1 AND cargo='1'")}
        nomes = {(r[0], r[1]): r[2] for r in conn.execute(
            "SELECT uf, mun, municipio FROM ag_mun_tot WHERE turno=1 AND cargo='1'")}
    if not web:
        return {'disponivel': False}
    difs = []
    for k in set(web) | set(csv_):
        w, c = web.get(k, 0), csv_.get(k, 0)
        if w != c:
            difs.append({'uf': k[0], 'mun': k[1], 'municipio': nomes.get(k[:2], k[1]), 'votavel': k[2],
                         'totalizacao': w, 'csv_secao': c, 'diferenca': w - c})
    difs.sort(key=lambda d: -abs(d['diferenca']))
    # Classifica: candidato com votos no CSV que a totalização conta como nulo (candidatura anulada → nulo técnico).
    por_mun = {}
    for d in difs:
        por_mun.setdefault((d['uf'], d['mun']), {})[d['votavel']] = d['diferenca']
    anulados, outros = {}, 0
    for ds in por_mun.values():
        cands = [v for v in ds if v != '96']
        if len(cands) == 1 and '96' in ds and ds['96'] == -ds[cands[0]] and ds[cands[0]] < 0:
            anulados[cands[0]] = anulados.get(cands[0], 0) - ds[cands[0]]
        else:
            outros += 1
    so_web = {k[:2] for k in web} - {k[:2] for k in csv_}
    return {'disponivel': True, 'pares_comparados': len(set(web) | set(csv_)),
            'municipios_divergentes': len(por_mun), 'municipios_explicados_nulo_tecnico': len(por_mun) - outros,
            'municipios_nao_explicados': outros, 'votos_anulados_por_candidato': anulados,
            'municipios_so_totalizacao': len(so_web), 'votos_municipios_so_totalizacao': sum(n for k, n in web.items() if k[:2] in so_web),
            'municipios_web': len({k[:2] for k in web}), 'municipios_csv': len({k[:2] for k in csv_}),
            'votos_web': sum(web.values()), 'votos_csv': sum(csv_.values()),
            'divergencias': len(difs), 'lista': difs[:200]}


def partido_por_cargo(db, turno, partidos):
    """% dos válidos de cada partido, por UF e por cargo (para dumbbell/slope/coordenadas paralelas)."""
    marcadores = ','.join('?' * len(partidos))
    with _ro(db) as conn:
        val = {(r['cargo'], r['uf']): r['validos'] for r in conn.execute(
            'SELECT cargo, uf, SUM(validos) validos FROM ag_mun_tot WHERE turno=? GROUP BY cargo, uf', [turno])}
        linhas = _rows(conn, f'''SELECT cargo, uf, partido, SUM(votos) votos FROM ag_mun
                                 WHERE turno=? AND partido IN ({marcadores}) GROUP BY cargo, uf, partido''',
                       [turno] + list(partidos))
    for r in linhas:
        v = val.get((r['cargo'], r['uf'])) or 0
        r['pct'] = round(100.0 * r['votos'] / v, 2) if v else None
    return linhas
