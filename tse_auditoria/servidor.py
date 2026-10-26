"""Servidor localhost: painel de gráficos + API somente leitura sobre o SQLite."""
import json
import mimetypes
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import analise


def connect(db):
    conn = sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro', uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    return conn


def sql_readonly(db, query):
    q = query.strip()
    if not q.lower().startswith(('select ', 'with ', 'pragma table_info(')) or ';' in q or len(q) > 5000:
        raise ValueError('Apenas uma consulta SELECT/WITH, até 5.000 caracteres, sem ponto e vírgula')
    conn = connect(db)
    try:
        start = time.monotonic()
        conn.set_progress_handler(lambda: 1 if time.monotonic() - start > 30 else 0, 20000)
        cur = conn.execute('SELECT * FROM (' + q + ') LIMIT 1000')
        return {'colunas': [x[0] for x in cur.description], 'linhas': [list(r) for r in cur]}
    finally:
        conn.close()


def _turno(args):
    return int(args.get('turno', '1'))


ROTAS = {
    '/api/meta': lambda db, a: analise.meta(db),
    '/api/resumo': lambda db, a: analise.resumo(db, _turno(a), a.get('cargo', '1'), a.get('uf')),
    '/api/uf': lambda db, a: analise.por_uf(db, _turno(a), a.get('cargo', '1')),
    '/api/municipios': lambda db, a: analise.municipios(db, _turno(a), a.get('cargo', '1'), a['a'],
                                                        a.get('b'), a.get('uf')),
    '/api/secoes': lambda db, a: analise.histograma_secoes(db, _turno(a), a.get('cargo', '1'), a['partido'],
                                                           a.get('uf')),
    '/api/brancos_nulos': lambda db, a: analise.brancos_nulos_secoes(db, _turno(a), a.get('cargo', '1'),
                                                                     a.get('uf')),
    '/api/candidatos': lambda db, a: analise.candidatos(db, _turno(a), a.get('cargo', '6'), a.get('uf'),
                                                        a.get('limite', '300')),
    '/api/turnos': lambda db, a: analise.entre_turnos(db, a.get('cargo', '1'), a['a'], a['b']),
    '/api/conferencia': lambda db, a: analise.conferencia_1t(db),
    '/api/partido_cargos': lambda db, a: analise.partido_por_cargo(db, _turno(a),
                                                                   [p for p in a.get('partidos', '').split(',') if p]),
}


def handler_factory(db, html, static_dir):
    class Handler(BaseHTTPRequestHandler):
        def answer(self, obj, code=200, mime='application/json; charset=utf-8'):
            content = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False, default=str).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(content)

        def route(self, method):
            path = urlparse(self.path)
            args = {k: v[0] for k, v in parse_qs(path.query).items()}
            if path.path == '/':
                return self.answer(Path(html).read_bytes(), mime='text/html; charset=utf-8')
            if path.path.startswith('/static/'):
                arquivo = (Path(static_dir) / path.path[len('/static/'):]).resolve()
                if Path(static_dir).resolve() not in arquivo.parents or not arquivo.is_file():
                    return self.answer({'erro': 'não encontrado'}, 404)
                mime = mimetypes.guess_type(arquivo.name)[0] or 'application/octet-stream'
                return self.answer(arquivo.read_bytes(), mime=mime)
            if not Path(db).exists():
                return self.answer({'erro': 'Banco vazio. Use catalogar, baixar, importar e agregar.'}, 409)
            if path.path in ROTAS and method == 'GET':
                return self.answer(ROTAS[path.path](db, args))
            if path.path == '/api/sql' and method == 'POST':
                length = int(self.headers.get('Content-Length', '0'))
                if length > 10000:
                    return self.answer({'erro': 'Corpo excede limite'}, 413)
                payload = json.loads(self.rfile.read(length) or b'{}')
                return self.answer(sql_readonly(db, payload.get('query', '')))
            return self.answer({'erro': 'Rota não encontrada'}, 404)

        def do_GET(self):
            try:
                self.route('GET')
            except Exception as exc:
                self.answer({'erro': f'{type(exc).__name__}: {exc}'}, 500)

        def do_POST(self):
            try:
                self.route('POST')
            except Exception as exc:
                self.answer({'erro': f'{type(exc).__name__}: {exc}'}, 400)

        def log_message(self, fmt, *args):
            pass

    return Handler


def servir(db, root, html, host='127.0.0.1', port=8787):
    static_dir = Path(html).resolve().parent / 'static'
    print(f'Painel local: http://{host}:{port} (Ctrl+C para encerrar)')
    ThreadingHTTPServer((host, port), handler_factory(db, html, static_dir)).serve_forever()
