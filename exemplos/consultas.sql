-- Consultas na tabela normalizada. O código de presidente em dados TSE é 1.
-- Votos por candidato/turno, sem classificar ideologia de forma presumida:
SELECT turno, votavel, MAX(nome_votavel) AS candidato, SUM(quantidade) AS votos
FROM votos WHERE cargo_codigo='1'
GROUP BY turno, votavel ORDER BY turno,votos DESC;

-- Comparação 1º x 2º turno por UF para dois números de candidato:
SELECT uf,
 SUM(CASE WHEN turno=1 AND votavel='13' THEN quantidade ELSE 0 END) votos_13_primeiro,
 SUM(CASE WHEN turno=2 AND votavel='13' THEN quantidade ELSE 0 END) votos_13_segundo,
 SUM(CASE WHEN turno=1 AND votavel='22' THEN quantidade ELSE 0 END) votos_22_primeiro,
 SUM(CASE WHEN turno=2 AND votavel='22' THEN quantidade ELSE 0 END) votos_22_segundo
FROM votos WHERE cargo_codigo='1' AND votavel IN ('13','22')
GROUP BY uf ORDER BY uf;

-- Ranking de municípios por aumento ABSOLUTO de um candidato, códigos TSE:
SELECT uf, municipio_codigo, municipio_nome,
 SUM(CASE WHEN turno=1 THEN quantidade ELSE 0 END) as primeiro,
 SUM(CASE WHEN turno=2 THEN quantidade ELSE 0 END) as segundo,
 SUM(CASE WHEN turno=2 THEN quantidade ELSE 0 END) -
 SUM(CASE WHEN turno=1 THEN quantidade ELSE 0 END) as aumento
FROM votos WHERE cargo_codigo='1' AND votavel='13'
GROUP BY uf, municipio_codigo, municipio_nome
ORDER BY aumento DESC LIMIT 100;

-- Votação detalhada de cada candidato dentro de uma seção específica:
SELECT * FROM votos WHERE uf='GO' AND municipio_codigo='9000'
  AND zona='12' AND secao='33' AND cargo_codigo='1'
ORDER BY turno,votavel;

-- Número de linhas por fonte para examinar duplicações e escopo:
SELECT fonte,cargo_codigo,turno,COUNT(*) linhas,SUM(quantidade) votos
FROM votos GROUP BY fonte,cargo_codigo,turno ORDER BY fonte,cargo_codigo,turno;

-- Antes de somar dados brutos de outros cargos, verifique o dicionário de
-- dados de cada CSV (em RAW todas as colunas são texto) e regras de cada cargo.
