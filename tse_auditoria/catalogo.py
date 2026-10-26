"""Catálogo CKAN TSE com fallback HTML, sem usar fontes de terceiros."""
import json
import re
import ssl
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

BASE = 'https://dadosabertos.tse.jus.br'
API = BASE + '/api/3/action/'


def official_url(url):
    parsed = urlparse(url)
    return parsed.scheme == 'https' and (
        parsed.hostname == 'tse.jus.br' or (parsed.hostname or '').endswith('.tse.jus.br')
    ) and not parsed.username and not parsed.password


def get_bytes(url, timeout=50):
    if not official_url(url):
        raise ValueError(f'URL não oficial (recusada): {url}')
    req = Request(url, headers={'User-Agent': 'AuditoriaTSE2026/1.0 (+uso pessoal; dados publicos)', 'Accept': 'application/json,text/html,*/*'})
    with urlopen(req, timeout=timeout) as res:
        if not official_url(res.url):
            raise ValueError('Redirecionamento para domínio não oficial')
        return res.read()


def ckan(action, **params):
    url = API + action + '?' + urlencode(params)
    d = json.loads(get_bytes(url).decode('utf-8-sig'))
    if not d.get('success'):
        raise RuntimeError(f'Erro na API CKAN: {d.get("error")}')
    return d['result']


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            d = dict(attrs)
            if d.get('href'):
                self.hrefs.append(d['href'])


def html_links(url):
    page = get_bytes(url).decode('utf-8', errors='replace')
    parser = LinkParser()
    parser.feed(page)
    return [urljoin(url, href) for href in parser.hrefs]


def fallback_html():
    """Resgate oficial caso o proxy bloqueie a Action API; sem token/raspagem de resultados."""
    url = BASE + '/dataset/?tags=Ano+2026'
    out = []
    seen = set()
    for page in range(1, 8):
        links = html_links(url + '&page=' + str(page))
        slugs = []
        for link in links:
            p = urlparse(link)
            if p.hostname != 'dadosabertos.tse.jus.br':
                continue
            match = re.fullmatch(r'/dataset/([\w-]+)', p.path)
            if match and match.group(1) not in seen:
                slugs.append(match.group(1))
                seen.add(match.group(1))
        if not slugs:
            break
        for slug in slugs:
            detail = BASE + '/dataset/' + slug
            files = []
            for link in html_links(detail):
                if official_url(link) and 'cdn.tse.jus.br' in urlparse(link).netloc:
                    file = Path(urlparse(link).path).name
                    if file and not any(v['url'] == link for v in files):
                        files.append({'id': file, 'name': file, 'url': link, 'format': file.split('.')[-1].upper()})
            out.append({'name': slug, 'title': slug, 'url': detail, 'resources': files})
    return out


def descobrir():
    try:
        results = ckan('package_search', q='2026', rows=500, start=0)
        packages = results.get('results', [])
        # Evita 2022/2024 que mencionem 2026 apenas na descrição.
        packages = [p for p in packages if '2026' in (p.get('name', '') + ' ' + p.get('title', ''))]
        if not packages:
            raise RuntimeError('Busca retornou zero conjuntos de 2026')
        origem = 'CKAN Action API'
    except Exception as ex:
        print(f'[aviso] API CKAN indisponível ({type(ex).__name__}). Tentando páginas HTML oficiais...')
        packages = fallback_html()
        origem = 'HTML oficial (fallback)'
    if not packages:
        raise RuntimeError('Não foi possível descobrir recursos oficiais; tente novamente mais tarde')
    normalized = []
    for p in packages:
        normalized.append({
            'slug': p.get('name'), 'titulo': p.get('title', p.get('name')),
            'pagina': p.get('url') or BASE + '/dataset/' + p.get('name', ''),
            'modificado': p.get('metadata_modified'),
            'recursos': [
                {'id': r.get('id'), 'nome': r.get('name') or r.get('description') or Path(urlparse(r.get('url', '')).path).name,
                 'url': r.get('url'), 'formato': r.get('format'), 'modificado': r.get('last_modified'),
                 'tamanho_catalogo': r.get('size')}
                for r in p.get('resources', []) if r.get('url') and official_url(r['url'])
            ]
        })
    return {'ano': 2026, 'origem': origem, 'portal': BASE, 'conjuntos': normalized}


def salvar_catalogo(dest):
    data = descobrir()
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    Path(dest).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'Catálogo salvo: {dest} ({len(data["conjuntos"])} conjuntos, '
          f'{sum(len(x["recursos"]) for x in data["conjuntos"])} recursos)')
    return data
