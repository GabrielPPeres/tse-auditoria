import io
import json
import sqlite3
import zipfile
from pathlib import Path
from tse_auditoria.catalogo import official_url
from tse_auditoria.banco import importar_votos, importar_generico
from tse_auditoria.servidor import totais, comparacao, por_secao, sql_readonly

COLUMNS=['ANO_ELEICAO','NR_TURNO','CD_ELEICAO','SG_UF','CD_MUNICIPIO',
         'NM_MUNICIPIO','NR_ZONA','NR_SECAO','CD_CARGO','DS_CARGO',
         'NR_VOTAVEL','NM_VOTAVEL','QT_VOTOS']

def fixture(tmp_path,region='BR'):
    folder=tmp_path/'originais'/'resultados-2026'
    folder.mkdir(parents=True,exist_ok=True)
    path=folder/('teste_votacao_secao_2026_'+region+'.zip')
    rows=[
      ('2026','1','1','GO','9000','FICTICIA','12','33','1','Presidente','13','Candidato A','100'),
      ('2026','1','1','GO','9000','FICTICIA','12','33','1','Presidente','22','Candidato B','110'),
      ('2026','2','2','GO','9000','FICTICIA','12','33','1','Presidente','13','Candidato A','170'),
      ('2026','2','2','GO','9000','FICTICIA','12','33','1','Presidente','22','Candidato B','125'),
      ('2026','1','1','GO','9000','FICTICIA','12','33','6','Deputado Federal','888','Deputado','50'),
    ]
    text=';'.join(COLUMNS)+'\n'+'\n'.join(';'.join(x) for x in rows)+'\n'
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('votacao_secao_2026_'+region+'.csv',text.encode('iso-8859-1'))
    return path

def test_official_urls():
    assert official_url('https://cdn.tse.jus.br/estatistica/x.zip')
    assert not official_url('https://cdn.tse.jus.br.evil.com/x.zip')
    assert not official_url('http://cdn.tse.jus.br/x.zip')

def test_import_and_compare(tmp_path):
    fixture(tmp_path,'BR')
    fixture(tmp_path,'GO')
    db=tmp_path/'tse.sqlite3'
    rows=importar_votos(db,tmp_path)
    assert rows==5 # 4 BR president + 1 GO deputado, sem duplicar presidente.
    r=totais(db,cargo='1')
    assert len(r)==4
    assert sum(x['votos'] for x in r if x['turno']==2)==295
    comparison=comparacao(db,'13','22',uf='GO')
    assert len(comparison)==1
    assert comparison[0]['movimento']=='B PARA A'
    assert comparison[0]['aumento_a']==70
    assert len(por_secao(db,'GO','9000','12','33',cargo='1'))==4
    assert sql_readonly(db,'SELECT COUNT(*) n FROM votos')['linhas']==[[5]]
    assert importar_votos(db,tmp_path)==0 # idempotente

def test_generic(tmp_path):
    fixture(tmp_path,'GO')
    db=tmp_path/'db.sqlite'
    rows=importar_generico(db,tmp_path)
    assert rows==5
    assert importar_generico(db,tmp_path)==0
    with sqlite3.connect(db) as cx:
        tables=[x[0] for x in cx.execute("SELECT name FROM sqlite_master WHERE name LIKE 'raw_%'")]
        assert tables
        assert cx.execute(f'SELECT COUNT(*) FROM "{tables[0]}"').fetchone()[0]==5
