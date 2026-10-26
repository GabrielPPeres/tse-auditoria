# Laboratório TSE 2026 — base para auditoria independente

Projeto local, sem dependências externas de Python, para **descobrir, baixar, preservar e consultar dados oficiais publicados** no [Portal de Dados Abertos do Tribunal Superior Eleitoral](https://dadosabertos.tse.jus.br/).

> **Importante:** o ZIP distribuído deste projeto contém código, NÃO contém os resultados reais das eleições. O programa baixa os resultados diretamente dos domínios `dadosabertos.tse.jus.br` e `cdn.tse.jus.br` na sua máquina. Dados simulados existem apenas nos testes automatizados e nunca são importados pelo programa por padrão.

## 1. Ambiente

- Windows 10/11; Python **3.10 ou posterior** (`py --version` no PowerShell).
- Rede para acessar domínios oficiais do TSE.
- Para conjuntos nacionais completos, SSD com espaço disponível considerável: arquivos transmitidos de urna e registros de log podem ocupar **muito** armazenamento. O tamanho exato depende dos recursos publicados.
- Não precisa de chave de API nem login para ler datasets públicos. O servidor é apenas `http://127.0.0.1:8787` na sua máquina.
- Nenhum `pip install` necessário: usa apenas bibliotecas padrão de Python.

## 2. Primeira execução no PowerShell

Abra o PowerShell dentro desta pasta e execute:

```powershell
py -3 main.py catalogar
py -3 main.py recursos --termo votacao_secao_2026_BR
py -3 main.py baixar --perfil presidencial
py -3 main.py verificar
py -3 main.py importar
py -3 main.py indices
py -3 main.py agregar
py -3 main.py servir
```

### 2º turno antes do CSV por seção

O TSE publica a votação por seção alguns dias depois de cada turno. Até lá, `py -3 main.py resultados-web` baixa a totalização oficial por município de `resultados.tse.jus.br` (Presidente 1º e 2º turno; Governador 2º turno), guarda cada JSON com SHA256 em `originais/resultados-web/` e atualiza o painel em segundos. Quando o CSV por seção daquele turno for importado, `agregar` passa a usá-lo e a totalização fica só para conferência (aba "1º × 2º turno", quadro "Conferência").

`baixar --atualizar` consulta a versão no servidor (ETag) e só baixa o que mudou; a versão anterior é guardada em `dados/versoes_anteriores/` para comparar retificações.

O passo `agregar` gera as tabelas resumidas (`ag_*`) que alimentam os gráficos do painel. Rode-o de novo depois de cada importação; na base nacional completa leva cerca de 40 minutos.

Abra `http://127.0.0.1:8787` no navegador, ou use `iniciar_windows.bat` para iniciar o painel depois da importação. Use `Ctrl+C` para encerrar o servidor.

### Comandos principais

```powershell
# Refaz consulta ao catálogo oficial (não altera arquivos já baixados)
py -3 main.py catalogar

# Exibe todos os recursos de 2026 descobertos e URL oficial
py -3 main.py recursos

# Baixa o CSV/ZIP nacional de votação por seção para PRESIDENTE
py -3 main.py baixar --perfil presidencial

# Baixa TODOS os recursos do conjunto "Resultados - 2026" (inclui presidentes/estados/PDFs)
py -3 main.py baixar --perfil resultados --dataset resultados-2026

# Baixa apenas arquivos de uma UF do grupo Resultados 2026 (ex.: MG)
py -3 main.py baixar --perfil resultados --uf MG

# Baixa tudo que o catálogo encontrar para 2026 (ATENÇÃO: armazenamento e tempo)
py -3 main.py baixar --perfil todos

# Pode filtrar quantidade/nome/dataset para testar antes de baixar em massa
py -3 main.py baixar --perfil todos --termo comparecimento --limite 3

# Atualiza arquivos para novas versões publicadas pelo TSE; preserva metadados/hash atual
py -3 main.py catalogar
py -3 main.py baixar --perfil presidencial --atualizar
py -3 main.py importar

# Importação estruturada: votação_secao_2026_BR e 2026_XX (candidatos e votos)
py -3 main.py importar
py -3 main.py indices

# Importação genérica: quaisquer CSV/TXT já baixados, com TODAS as colunas como TEXT
# (para não inferir tipos erroneamente; exige seus próprios SELECT/CAST SQL)
py -3 main.py importar-generico --termo comparecimento
py -3 main.py importar-generico --termo boletim

# Ative painel local
py -3 main.py servir
```

**Atenção ao filtro por UF**: filtra pelo nome dos arquivos oficiais (por exemplo, `GO - Boletim`); arquivos nacionais nomeados `BR` não são incluídos quando o filtro exige `GO`. Para presidente nacional, use o perfil `presidencial` sem filtro de UF e selecione a UF no painel.

## 3. Fontes oficiais

O programa descobre conjuntos e URLs via API pública do catálogo CKAN (`package_search`) e, se ela estiver temporariamente indisponível, tenta recuperar os links diretamente das páginas HTML oficiais do portal. Não consulta Github, imprensa ou bancos de terceiros para obter números.

Exemplos de datasets atualmente publicados no portal:

- [Resultados - 2026](https://dadosabertos.tse.jus.br/dataset/resultados-2026): votação por seção, votos nominais/partido por município/zona, relatórios e detalhes de totalização.
- [Resultados - 2026 - Boletim de Urna](https://dadosabertos.tse.jus.br/dataset/resultados-2026-boletim-de-urna): BUs por UF/turno e hashes disponibilizados pelo TSE.
- [Resultados - 2026 - arquivos transmitidos](https://dadosabertos.tse.jus.br/dataset/?groups=resultados&tags=Ano+2026): bu/log/rdv/vota utilizados na totalização.
- [Catálogo de 2026](https://dadosabertos.tse.jus.br/dataset/?tags=Ano+2026): eleitorado, candidatos, abstenção, mesários, financiamento, denúncias etc.

**Nota de periodicidade:** as bases de votação por seção são publicadas/atualizadas pelo TSE ao fim dos turnos. Elas não são um histórico minuto a minuto da página de apuração. Para estudar o processo de transmissão, analise separadamente os logs, correspondências e registros próprios (alguns binários requerem ferramentas oficiais).

## 4. Estrutura dos dados

```
tse-2026-auditoria/
  main.py                    # CLI
  painel.html                # interface local responsiva
  iniciar_windows.bat        # abre servidor no Windows
  tse_auditoria/             # catálogo, download, ETL e HTTP
  exemplos/consultas.sql     # consultas auditáveis
  tests/                     # dados exclusivamente artificiais
  dados/                     # criada na primeira execução
    catalogo.json            # lista de datasets e URLs oficiais
    originais/<dataset>/     # downloads originais, sem edição
      <hash_url>_<nome>.zip
      <hash_url>_<nome>.zip.origem.json # URL, hora, SHA256, ETag
    tse_2026.sqlite3         # tabelas importadas
```

**Tabela principal `votos`:** `fonte, arquivo_interno, ano, turno, eleicao, uf, municipio_codigo, municipio_nome, zona, secao, cargo, cargo_codigo, votavel, nome_votavel, quantidade`.

- O arquivo nacional `votacao_secao_2026_BR.zip` alimenta apenas os registros de **presidente**.
- Os arquivos `votacao_secao_2026_UF.zip` alimentam apenas os registros dos **demais cargos**. Se contiverem resultados presidenciais, esses registros são ignorados na importação estruturada para **não duplicar os votos**. Os dados originais permanecem armazenados integralmente.
- `votavel` e campos de código são armazenados como texto; somente `quantidade` é somada. O programa **não** soma `QT_APTOS`, `QT_COMPARECIMENTO` ou `QT_ABSTENCOES` a partir de linhas de candidatos, pois isso duplicaria contagens.
- O importador é idempotente pelo hash do arquivo; se houver nova versão oficial, ele substitui as linhas anteriores daquela fonte.
- Não classifica candidatos/partidos como direita, centro ou esquerda; isso não é atributo oficial da votação. Você pode construir seus próprios agrupamentos de forma explícita e reproduzível em tabelas de análise.
- `importar-generico` importa qualquer CSV/TXT (inclusive de ZIP) em tabela `raw_*`, com colunas `c0,c1,..`. A tabela `colunas_raw` guarda o cabeçalho original. Isso preserva diferenças de layout sem atribuir significados equivocados aos campos.

### Limites importantes

- **SHA256 local**: comprova que a sua cópia não mudou desde o download; por si só **não autentica o arquivo como um BU assinado pelo TSE**. Onde o TSE publicar hashes oficiais, baixe-os e faça a verificação oficial separadamente. As assinaturas e estruturas binárias `*.bu`, `*.rdv`, `*.log`, `*.vota` não são automaticamente validadas por este projeto.
- **Sem dados individuais**: o sigilo do voto impossibilita acompanhar a troca de voto de uma pessoa entre turnos. Municípios e seções mostram apenas agregados.
- **Viradas no painel**: compara exclusivamente os números de candidato digitados (ex. 13 e 22) no 1º e 2º turnos, não assegura que o candidato liderava contra todos os demais no 1º turno. Para obter o vencedor real do 1º turno é necessário considerar todos os presidenciáveis.
- **Dados oficiais podem ser retificados**: execute `catalogar`, depois `baixar --atualizar`, `verificar` e `importar`; observe mudanças de SHA e metadados.
- **Não há auditoria oficial atestada neste software**: ele é um ambiente independente de exploração e contabilização de arquivos publicados, não uma certificação da votação.
- **Eleitorado apto**: a própria descrição do dataset Resultados 2026 avisa sobre 39 registros duplicados por transferência temporária; não some indiscriminadamente a coluna de aptos em todos os CSVs.

## 5. Como verificar todos os dados por conta própria

O painel tem sete abas de gráficos (Visão geral, Estados, Municípios, Seções, Partidos e deputados, Entre cargos e SQL livre), com filtros de turno, cargo, UF e candidato/partido A e B. Cada gráfico tem um botão "Ver tabela" com os mesmos números. A aba pode ser aberta direto pelo endereço, por exemplo `http://127.0.0.1:8787/#estados`. Todos os resultados vêm do SQLite gerado pelos arquivos oficiais; não há números pré-preenchidos. A biblioteca de gráficos (ECharts 5.5.1) fica em `static/`, então o painel funciona sem internet.

Para consultar outro cargo, informe o código `cargo_codigo` conforme a tabela importada; `1` é presidente. Para outra eleição, adaptação do código é necessária: o programa está propositalmente restrito a 2026.

Para explorar o SQLite diretamente no VS Code, use extensão SQLite ou um programa como DB Browser for SQLite. Veja `exemplos/consultas.sql` para começar.

## 6. Testes

O projeto usa só biblioteca padrão para executar, mas os testes podem ser rodados com `pytest` se estiver instalado:

```powershell
py -3 -m pytest -q
```

Os testes utilizam nomes de municípios explicitamente fictícios e **não** verificam o download oficial, que exige acesso à internet e aos serviços do TSE. A leitura em tempo real dos endpoints não foi possível no ambiente de geração deste pacote; por isso a compatibilidade real com respostas atuais precisa ser comprovada na primeira execução na sua máquina.

## 7. Próximas camadas de auditoria

Este é um coletor + base de cálculos. Para auditoria criptográfica integral de logs e boletins eletrônicos seriam necessários interpretadores dos formatos oficiais e conferência de assinaturas e hashes independentes. Uma segunda camada pode integrar fontes TSE por chaves `UF/município/zona/seção/turno/cargo` e produzir relatórios de divergências explicitamente tipificadas: diferenças de totalização, ausência de seção, troca de seção, voto sem correspondência, etc. Não se deve considerar duas fontes 'iguais' sem conferir campos e regras de contabilização (seções agregadas e transferências).

**Licença da fonte:** Consulte a licença Creative Commons Attribution aplicável aos datasets oficiais do TSE. O código deste exemplo pode ser usado e modificado livremente para uso próprio, preservando os créditos à fonte oficial dos dados.
