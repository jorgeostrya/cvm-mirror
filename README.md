# cvm-mirror

Espelho dos dados da CVM (DFP+ITR) em formato Parquet, atualizado
automaticamente. Pensado para ser consumido por LLMs/notebooks que precisam
acessar demonstrações financeiras de companhias brasileiras de capital aberto
sem depender de download direto do portal `dados.cvm.gov.br`.

## Como rodar (uma vez só)

1. **Fork ou clone este repo** para sua conta no GitHub. Mantenha como repo
   público (necessário para `raw.githubusercontent.com`).
2. **Habilite GitHub Actions** em Settings → Actions → General → "Allow all
   actions" e em "Workflow permissions" marque **Read and write permissions**.
3. **Vá em Actions** → escolha o workflow "Atualizar mirror CVM" → clique
   **Run workflow**. Demora de 15 a 30 min na primeira execução.
4. Verifique que apareceram arquivos em `data/dfp/` (uns 700 parquets, um por
   empresa). Pronto.

Depois disso, o workflow roda sozinho dias 1, 5 e 15 de cada mês para pegar
reapresentações.

## Estrutura do mirror

```
data/
├── companies.json        # ticker → CD_CVM, lista de empresas, setor, situação
├── metadata.json         # data da última atualização, contagem de linhas
├── dfp/
│   ├── 8133.parquet      # LREN3 — Lojas Renner (anual, todos os anos)
│   ├── 9512.parquet      # PETR4/PETR3 — Petrobras
│   ├── 9342.parquet      # VALE3 — Vale
│   └── ...
└── itr/
    └── ...               # mesma estrutura, dados trimestrais (LTM)
```

Cada `{CD_CVM}.parquet` contém:
- BPA (Balanço Patrimonial Ativo) — CON e IND
- BPP (Balanço Patrimonial Passivo) — CON e IND
- DRE (Demonstração do Resultado) — CON e IND
- DFC-MI (Fluxo de Caixa indireto) — CON e IND
- Todos os anos disponíveis (default: 2010 em diante)

## Como consumir

Em Python:

```python
import pandas as pd
URL = "https://raw.githubusercontent.com/SEU_USUARIO/cvm-mirror/main/data/dfp/8133.parquet"
df = pd.read_parquet(URL)
# filtrar consolidado, ULTIMO exercicio
con = df[(df["TP_DF"] == "CON") & (df["ORDEM_EXERC"] == "ÚLTIMO")]
```

Em qualquer chat com Claude, basta passar o ticker, período, tipo e o nome do
seu repo no GitHub.

## Por que não é só baixar direto da CVM?

Porque ferramentas executando em sandboxes restritos (Claude, alguns Jupyter
Hubs, ambientes corporativos) frequentemente não conseguem alcançar
`dados.cvm.gov.br`, mas conseguem `raw.githubusercontent.com`. Esse mirror
resolve isso sem custo.

## Como funciona o build

`scripts/build_mirror.py` faz, em ordem:
1. Baixa `cad_cia_aberta.csv` (cadastro de companhias) — para resolver
   ticker → CD_CVM via FCA.
2. Para cada ano de `ANO_INICIAL` até hoje:
   - Baixa `dfp_cia_aberta_AAAA.zip` e `itr_cia_aberta_AAAA.zip`.
   - Extrai BPA, BPP, DRE, DFC-MI (versões CON e IND).
   - Concatena em um DataFrame único.
3. Particiona por empresa e salva como `data/dfp/{CD_CVM}.parquet` com
   compressão zstd nível 9.

O workflow consome cerca de 8 minutos de GitHub Actions e gera ~80–150 MB
de parquets (cabe folgadamente no limite gratuito de 5 GB por repo público).

## Customizar

- Para limitar o período: edite o workflow e adicione
  `env: ANO_INICIAL: "2015"` no step "Rodar build".
- Para incluir mais demonstrativos (DFC-MD, DMPL, DVA): edite a lista
  `DEMONSTRATIVOS` em `scripts/build_mirror.py`.

## Licença

Os dados são da CVM (Open Data Brasil — licença CC-BY). Este repo é apenas um
espelho técnico em formato mais conveniente. Atribua a CVM como fonte original.
