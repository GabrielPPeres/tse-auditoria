"""ETL local: CSVs TSE -> SQLite auditável; dados originais permanecem intactos."""
import csv
import hashlib
import io
import json
import re
import sqlite3
import unicodedata
import zipfile
from pathlib import Path
from .coleta import sha256_file


# WAL é mais rápido em disco local; em pasta sincronizada (Google Drive) use DELETE,
# para o banco ser um arquivo único e consistente entre transações.
JOURNAL_MODE = 'WAL'


def db_connect(db):
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db, timeout=90)
    conn.execute(f'PRAGMA journal_mode={JOURNAL_MODE}')
    conn.execute('PRAGMA busy_timeout=90000')
    conn.execute('PRAGMA cache_size=-120000')
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS votos (
        fonte TEXT NOT NULL, arquivo_interno TEXT NOT NULL,
        ano INTEGER, turno INTEGER, eleicao TEXT,
        uf TEXT, municipio_codigo TEXT, municipio_nome TEXT,
        zona TEXT, secao TEXT, cargo TEXT, cargo_codigo TEXT,
        votavel TEXT, nome_votavel TEXT, quantidade INTEGER
    );
    CREATE TABLE IF NOT EXISTS importacoes (
        identificador TEXT PRIMARY KEY, sha256 TEXT NOT NULL,
        tipo TEXT NOT NULL, linhas INTEGER, importado_em TEXT DEFAULT CURRENT_TIMESTAMP
    );
    ''')
    return conn


def norm(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(s)) if not unicodedata.combining(c)).strip().upper()


def number(s, default=None):
    try:
        return int(str(s).strip())
    except (ValueError, TypeError):
        return default


def entries(path):
    """Abre ZIP sem extração em disco, ou CSV simples. Fechamento dos handles pelo consumidor."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                if info.is_dir() or not info.filename.lower().endswith(('.csv', '.txt')):
                    continue
                with z.open(info) as raw:
                    # Os dados TSE costumam usar ISO-8859-1, com possibilidade de UTF-8.
                    yield info.filename, raw, info.file_size
    elif path.suffix.lower() in ('.csv', '.txt'):
        with path.open('rb') as f:
            yield path.name, f, path.stat().st_size


def reader_text(raw):
    """Decodifica sem perda: teste do cabeçalho; não depende de pandas/Excel."""
    sample = raw.read(2048)
    encoding = 'utf-8-sig'
    try:
        sample.decode(encoding)
    except UnicodeDecodeError:
        encoding = 'iso-8859-1'
    # Cabeçalho ASCII não detecta 100% de latin1; escolhemos a convenção oficial TSE
    # quando a amostra não apresenta BOM UTF-8 nem padrão UTF8 inequívoco.
    if not sample.startswith(b'\xef\xbb\xbf') and b'\xc3' not in sample:
        encoding = 'iso-8859-1'
    stream = io.TextIOWrapper(raw, encoding=encoding, errors='replace', newline='')
    # sample será usado se arquivo não seekable (ZipExtFile suporta seek, mas pode ser lento).
    stream.seek(0)
    first = stream.readline()
    delim = ';' if first.count(';') >= first.count(',') else ','
    stream.seek(0)
    return csv.DictReader(stream, delimiter=delim)


def header_dict(row):
    return {norm(key): val for key, val in row.items() if key}


def col(r, *alternatives):
    for name in alternatives:
        if name in r and r[name] not in ('', None):
            return r[name]
    return ''


def is_votes_source(path):
    return bool(re.search(r'votacao_secao_2026_(BR|[A-Z]{2})', path.name, re.I))


def pasta_originais(root, originais=None):
    return Path(originais) if originais else Path(root)/'originais'


def identificador(path, orig):
    # Sempre relativo à pasta dos originais, para o ID não mudar se a pasta for movida (ex.: Google Drive).
    return 'originais/' + str(Path(path).relative_to(orig)).replace('\\', '/')


def importar_votos(db_path, root, limite_arquivos=None, forcar=False, originais=None):
    orig = pasta_originais(root, originais)
    sources = [p for p in orig.rglob('*')
               if p.is_file() and is_votes_source(p) and not p.name.endswith('.json')]
    if limite_arquivos:
        sources = sources[:limite_arquivos]
    if not sources:
        print('Nenhum ZIP de votação por seção baixado. Execute: python main.py baixar --perfil presidencial')
        return 0
    conn = db_connect(db_path)
    total = 0
    for path in sorted(sources):
        identifier = identificador(path, orig)
        digest = sha256_file(path)
        previous = conn.execute('SELECT sha256 FROM importacoes WHERE identificador=? AND tipo=?', (identifier,'votos')).fetchone()
        if previous and previous[0] == digest and not forcar:
            print('[igual] já importado:', identifier)
            continue
        national = bool(re.search(r'votacao_secao_2026_BR', path.name, re.I))
        count = 0
        with conn:
            conn.execute('DELETE FROM votos WHERE fonte=?', (identifier,))
            for member, raw, _ in entries(path):
                data = reader_text(raw)
                fields = {norm(x) for x in data.fieldnames or []}
                essentials = {'NR_TURNO', 'CD_MUNICIPIO', 'NR_ZONA', 'NR_SECAO', 'NR_VOTAVEL', 'QT_VOTOS'}
                if not essentials.issubset(fields):
                    print(f'[ignorado] {member}: não parece tabela de votos por seção. Faltando: {essentials - fields}')
                    continue
                if 'DS_CARGO' not in fields and 'CD_CARGO' not in fields:
                    raise ValueError('Sem coluna cargo; interrompido para não misturar eleições')
                batch = []
                for line in data:
                    r = header_dict(line)
                    cargo = col(r,'DS_CARGO')
                    cargo_id = col(r,'CD_CARGO')
                    president = norm(cargo) == 'PRESIDENTE' or str(cargo_id).strip() == '1'
                    # O arquivo BR é a referência presidencial, os por UF são a referência de outros cargos.
                    # Assim não se somam votos presidenciais duplicados entre BR e UFs.
                    if (national and not president) or (not national and president):
                        continue
                    qtd = number(col(r,'QT_VOTOS'))
                    if qtd is None:
                        raise ValueError(f'Voto inválido em {member}: {r.get("QT_VOTOS")}')
                    batch.append((identifier, member, number(col(r,'ANO_ELEICAO'),2026), number(col(r,'NR_TURNO')),
                                  col(r,'CD_ELEICAO'), col(r,'SG_UF'), col(r,'CD_MUNICIPIO'), col(r,'NM_MUNICIPIO'),
                                  col(r,'NR_ZONA'), col(r,'NR_SECAO'), cargo or cargo_id, cargo_id,
                                  col(r,'NR_VOTAVEL'), col(r,'NM_VOTAVEL'), qtd))
                    if len(batch) >= 20000:
                        conn.executemany('INSERT INTO votos VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', batch)
                        count += len(batch)
                        batch.clear()
                if batch:
                    conn.executemany('INSERT INTO votos VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', batch)
                    count += len(batch)
            conn.execute('INSERT OR REPLACE INTO importacoes (identificador,sha256,tipo,linhas) VALUES (?,?,?,?)',
                         (identifier,digest,'votos',count))
        print(f'[OK] {identifier}: {count:,} linhas')
        total += count
    conn.close()
    return total


def safe_table(value):
    stem = re.sub('[^a-z0-9_]+','_', unicodedata.normalize('NFKD', value.lower()).encode('ascii','ignore').decode()).strip('_')[:44]
    return 'raw_' + stem + '_' + hashlib.sha1(value.encode()).hexdigest()[:8]


def importar_generico(db_path, root, termo=None, limite_arquivos=None, forcar=False, originais=None):
    """Importa qualquer CSV/TXT do TSE como texto para SQL independente do layout, sem conversão silenciosa."""
    orig = pasta_originais(root, originais)
    sources = [p for p in orig.rglob('*') if p.is_file() and
               p.suffix.lower() in ('.zip','.csv','.txt') and not p.name.endswith('.origem.json')]
    if termo:
        sources = [p for p in sources if termo.casefold() in str(p).casefold()]
    if limite_arquivos:
        sources = sources[:limite_arquivos]
    conn = db_connect(db_path)
    total = 0
    for path in sorted(sources):
        path_id = identificador(path, orig)
        digest = sha256_file(path)
        count_file = 0
        for member, raw, _ in entries(path):
            identifier = path_id + '::' + member
            old = conn.execute('SELECT sha256 FROM importacoes WHERE identificador=? AND tipo=?', (identifier,'generico')).fetchone()
            if old and old[0] == digest and not forcar:
                continue
            reader = reader_text(raw)
            headers = reader.fieldnames or []
            if not headers or len(headers)>300:
                print('[ignorado] sem cabeçalho compatível:', member)
                continue
            # Uma tabela por membro evita misturar esquemas heterogêneos.
            table = safe_table(identifier)
            columns = ['c' + str(i) for i in range(len(headers))]
            defs = ','.join(f'"{c}" TEXT' for c in columns)
            placeholders = ','.join('?' for _ in columns)
            count = 0
            with conn:
                conn.execute(f'DROP TABLE IF EXISTS "{table}"')
                conn.execute(f'CREATE TABLE "{table}" ({defs})')
                batch = []
                for row in reader:
                    batch.append(tuple(row.get(h) for h in headers))
                    if len(batch) >= 10000:
                        conn.executemany(f'INSERT INTO "{table}" VALUES ({placeholders})',batch)
                        count += len(batch)
                        batch.clear()
                if batch:
                    conn.executemany(f'INSERT INTO "{table}" VALUES ({placeholders})',batch)
                    count += len(batch)
                conn.execute('INSERT OR REPLACE INTO importacoes (identificador,sha256,tipo,linhas) VALUES (?,?,?,?)',
                             (identifier,digest,'generico',count))
                conn.execute('CREATE TABLE IF NOT EXISTS colunas_raw (tabela TEXT, coluna TEXT, original TEXT, PRIMARY KEY(tabela,coluna))')
                conn.execute('DELETE FROM colunas_raw WHERE tabela=?',(table,))
                conn.executemany('INSERT INTO colunas_raw VALUES (?,?,?)',[(table,col,orig) for col,orig in zip(columns,headers)])
            count_file += count
            print(f'[OK RAW] {table}: {count:,} linhas ({member})')
        total += count_file
    conn.close()
    return total


def criar_indices(db):
    conn = db_connect(db)
    print('Criando índices (em bases nacionais pode levar alguns minutos)...')
    conn.executescript('''
    CREATE INDEX IF NOT EXISTS idx_votos_cargo_turno ON votos(cargo_codigo, turno, votavel);
    CREATE INDEX IF NOT EXISTS idx_votos_municipio ON votos(cargo_codigo, uf, municipio_codigo, turno, votavel);
    CREATE INDEX IF NOT EXISTS idx_votos_secao ON votos(uf, municipio_codigo, zona, secao, turno, cargo_codigo);
    ''')
    conn.close()
    print('Índices concluídos.')
