"""CLI. Python 3.10+, somente bibliotecas padrão."""
import argparse
import json
import sys
from pathlib import Path
from tse_auditoria.catalogo import salvar_catalogo
from tse_auditoria.coleta import executar, verificar
from tse_auditoria import banco
from tse_auditoria.banco import importar_votos, importar_generico, criar_indices
from tse_auditoria.servidor import servir
from tse_auditoria.analise import agregar
from tse_auditoria import resultados_web

HERE=Path(__file__).resolve().parent
DATA=HERE/'dados'
CONFIG=HERE/'config.json'


def config_padrao():
    """config.json opcional, ex.: {"originais": "G:\\\\My Drive\\\\tse2026\\\\originais"}."""
    try:
        return json.loads(CONFIG.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}


def parser():
    p=argparse.ArgumentParser(prog='python main.py',description='Coleta auditável dos dados abertos do TSE 2026')
    cfg=config_padrao()
    p.add_argument('--pasta',default=cfg.get('pasta',str(DATA)),help='Diretório para catálogo e SQLite')
    p.add_argument('--originais',default=cfg.get('originais'),
                   help='Diretório dos downloads originais (padrão: <pasta>/originais). Pode ser o Google Drive.')
    sub=p.add_subparsers(dest='cmd',required=True)
    sub.add_parser('catalogar',help='Descobre os datasets 2026 no portal oficial')
    r=sub.add_parser('recursos',help='Lista os links oficiais descobertos')
    r.add_argument('--termo',default='',help='Filtra por parte do título/URL')
    d=sub.add_parser('baixar',help='Baixa originais sem editar, com SHA256')
    d.add_argument('--perfil',choices=['presidencial','resultados','todos'],default='presidencial')
    d.add_argument('--dataset',help='Filtra slug de conjunto (parcial)')
    d.add_argument('--uf',help='UF do arquivo, ex. GO,MG,SP (vazio para nacional)')
    d.add_argument('--termo',help='Filtra título ou nome do recurso')
    d.add_argument('--limite',type=int,help='Limita arquivos para experimentar')
    d.add_argument('--atualizar',action='store_true',help='Baixa novamente mesmo havendo hash local igual')
    sub.add_parser('verificar',help='Confere SHA256 dos arquivos locais')
    i=sub.add_parser('importar',help='Importa votacao_secao_2026 (presidente BR + cargos das UFs)')
    i.add_argument('--limite',type=int,help='Máximo de ZIPs')
    i.add_argument('--forcar',action='store_true')
    g=sub.add_parser('importar-generico',help='Importa qualquer CSV/TXT oficial em tabelas RAW')
    g.add_argument('--termo',help='Filtra caminho de arquivo')
    g.add_argument('--limite',type=int)
    g.add_argument('--forcar',action='store_true')
    sub.add_parser('indices',help='Otimiza consultas SQLite')
    sub.add_parser('agregar',help='Gera as tabelas resumidas usadas pelos gráficos do painel')
    w=sub.add_parser('resultados-web',help='Baixa a totalização oficial por município (resultados.tse.jus.br) e atualiza o painel')
    w.add_argument('--atualizar',action='store_true',help='Baixa de novo os arquivos já salvos')
    s=sub.add_parser('servir',help='Painel web local')
    s.add_argument('--porta',type=int,default=8787)
    return p


def main():
    args=parser().parse_args()
    modo=str(config_padrao().get('journal_mode','WAL')).upper()
    if modo not in ('WAL','DELETE'):
        raise SystemExit('ERRO: journal_mode em config.json deve ser WAL ou DELETE')
    banco.JOURNAL_MODE=modo
    root=Path(args.pasta).resolve()
    root.mkdir(parents=True,exist_ok=True)
    db=root/'tse_2026.sqlite3'
    catalog=root/'catalogo.json'
    orig=Path(args.originais).resolve() if args.originais else root/'originais'
    if args.cmd in ('baixar','verificar','importar','importar-generico'):
        print(f'Originais em: {orig}')
        if not Path(orig.anchor).exists():
            raise SystemExit(f'ERRO: unidade da pasta de originais indisponível: {orig.anchor}. O Google Drive está aberto?')
    try:
        if args.cmd=='catalogar': salvar_catalogo(catalog)
        elif args.cmd in ('recursos','baixar'):
            if not catalog.exists():
                print('Catálogo ausente: descobrindo...')
                salvar_catalogo(catalog)
            data=json.loads(catalog.read_text(encoding='utf-8'))
            if args.cmd=='recursos':
                for pkg in data['conjuntos']:
                    print(f'\n[{pkg["slug"]}] {pkg["titulo"]}')
                    for res in pkg['recursos']:
                        if args.termo.casefold() in (res['nome']+' '+res['url']).casefold():
                            print('  ',res['nome'],'\n   ',res['url'])
            else:
                ok,errors=executar(root,data,args.perfil,args.dataset,args.uf,args.termo,args.limite,args.atualizar,originais=orig)
                print(f'Concluído: {ok} arquivo(s). {len(errors)} falha(s).')
                if errors:return 2
        elif args.cmd=='verificar':
            return 0 if verificar(root,originais=orig) else 2
        elif args.cmd=='importar':
            imported=importar_votos(db,root,args.limite,args.forcar,originais=orig)
            print(f'Importadas {imported:,} linhas novas. Rode "indices" antes das análises pesadas.')
        elif args.cmd=='importar-generico':
            imported=importar_generico(db,root,args.termo,args.limite,args.forcar,originais=orig)
            print(f'Importadas {imported:,} linhas RAW (todas as colunas como TEXTO).')
        elif args.cmd=='indices': criar_indices(db)
        elif args.cmd=='agregar': agregar(db)
        elif args.cmd=='resultados-web':
            resultados_web.coletar(root,orig,args.atualizar)
            resultados_web.importar(db,orig)
            agregar(db,somente_web=True)
        elif args.cmd=='servir': servir(db,root,HERE/'painel.html',port=args.porta)
    except (KeyboardInterrupt, BrokenPipeError):
        return 130
    except Exception as ex:
        print(f'ERRO: {ex}',file=sys.stderr)
        return 1
    return 0

if __name__=='__main__': sys.exit(main())
