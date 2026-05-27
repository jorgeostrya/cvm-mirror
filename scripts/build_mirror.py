#!/usr/bin/env python3
"""
build_mirror.py — Mirror dos dados CVM em formato Parquet

Roda no GitHub Actions (gratuito para repos publicos). Baixa todos os ZIPs
DFP+ITR da CVM, consolida em parquet por empresa e commita no repo.

Resultado:
  data/companies.json           # mapa ticker/cnpj -> CD_CVM
  data/dfp/{cd_cvm}.parquet     # uma empresa, todos os anos, todas as contas
  data/itr/{cd_cvm}.parquet     # idem trimestral
  data/metadata.json            # info da ultima atualizacao
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

# ---------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DFP = DATA_DIR / "dfp"
DATA_ITR = DATA_DIR / "itr"
DATA_DFP.mkdir(parents=True, exist_ok=True)
DATA_ITR.mkdir(parents=True, exist_ok=True)

# Variaveis de ambiente para customizar no Actions
ANO_INICIAL = int(os.environ.get("ANO_INICIAL", "2010"))
ANO_FINAL   = int(os.environ.get("ANO_FINAL",   datetime.now().year))
# Demonstrativos que vamos processar (os principais)
DEMONSTRATIVOS = ["BPA", "BPP", "DRE", "DFC_MI"]

URL_DFP = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/dfp_cia_aberta_{ano}.zip"
URL_ITR = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/ITR/DADOS/itr_cia_aberta_{ano}.zip"
URL_CAD = "https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv"
# FCA tem o ticker B3 (Codigo_Negociacao)
URL_FCA_VM_TPL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FCA/DADOS/fca_cia_aberta_valor_mobiliario_{ano}.csv"

UA = {"User-Agent": "cvm-mirror/1.0 (+github.com/seu-usuario/cvm-mirror)"}

# ---------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------
def baixar_zip(url: str) -> bytes | None:
    """Baixa um ZIP. Retorna None se 404 (ano sem dados)."""
    for tentativa in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=180)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.content
        except requests.RequestException as e:
            print(f"  retry {tentativa+1}: {e}", file=sys.stderr)
            time.sleep(5 * (tentativa + 1))
    raise RuntimeError(f"Falha persistente em {url}")


def ler_csv_de_zip(zip_bytes: bytes, nome_csv: str) -> pd.DataFrame | None:
    """Le um CSV de dentro de um ZIP em memoria. Retorna None se ausente."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        if nome_csv not in z.namelist():
            return None
        with z.open(nome_csv) as fh:
            return pd.read_csv(fh, sep=";", encoding="latin-1", decimal=",",
                               dtype={"CD_CVM": "int32"})


def carregar_demonstrativos_ano(ano: int, modo: str) -> pd.DataFrame:
    """
    Carrega TODOS os demonstrativos (BPA, BPP, DRE, DFC_MI) de um ano,
    para CON e IND, concatenados em um unico dataframe.
    modo: 'DFP' (anual) ou 'ITR' (trimestral)
    """
    if modo == "DFP":
        url = URL_DFP.format(ano=ano)
        prefix = "dfp_cia_aberta"
    else:
        url = URL_ITR.format(ano=ano)
        prefix = "itr_cia_aberta"

    zip_bytes = baixar_zip(url)
    if zip_bytes is None:
        print(f"  {ano} {modo}: nao disponivel (404)", file=sys.stderr)
        return pd.DataFrame()

    frames = []
    for demo in DEMONSTRATIVOS:
        for tipo in ["con", "ind"]:
            nome = f"{prefix}_{demo}_{tipo}_{ano}.csv"
            df = ler_csv_de_zip(zip_bytes, nome)
            if df is None or df.empty:
                continue
            df["DEMO"] = demo
            df["TP_DF"] = tipo.upper()
            df["MODO"] = modo
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def construir_cadastro() -> pd.DataFrame:
    """Constroi o cadastro empresa<->ticker.

    O cadastro CVM (cad_cia_aberta.csv) NAO tem ticker. O ticker (Codigo_Negociacao)
    vem do FCA (Formulario Cadastral) - secao Valor Mobiliario. Pega o ano corrente
    e fallback nos anos anteriores.
    """
    print("Baixando cadastro CVM...", file=sys.stderr)
    r = requests.get(URL_CAD, headers=UA, timeout=120)
    r.raise_for_status()
    cad = pd.read_csv(io.BytesIO(r.content), sep=";", encoding="latin-1",
                      dtype={"CD_CVM": "int32"})

    # FCA Valor Mobiliario
    print("Baixando FCA valor mobiliario...", file=sys.stderr)
    fca = None
    for ano in range(datetime.now().year, datetime.now().year - 5, -1):
        url = URL_FCA_VM_TPL.format(ano=ano)
        try:
            r = requests.get(url, headers=UA, timeout=120)
            if r.status_code == 200:
                fca = pd.read_csv(io.BytesIO(r.content), sep=";",
                                  encoding="latin-1",
                                  dtype={"CD_CVM": "int32"})
                print(f"  FCA {ano} obtido ({len(fca)} linhas)", file=sys.stderr)
                break
        except Exception:
            continue

    # Construir mapa ticker -> CD_CVM, CNPJ
    if fca is not None and "Codigo_Negociacao" in fca.columns:
        tickers = (fca[["CD_CVM", "Codigo_Negociacao"]]
                   .dropna()
                   .drop_duplicates())
        tickers["Codigo_Negociacao"] = tickers["Codigo_Negociacao"].str.upper()
    else:
        tickers = pd.DataFrame(columns=["CD_CVM", "Codigo_Negociacao"])

    # Tabela final
    out = cad[["CD_CVM", "DENOM_SOCIAL", "CNPJ_CIA",
               "SETOR_ATIV", "SIT", "CD_CVM"]].copy()
    out.columns = ["CD_CVM", "denom", "cnpj", "setor", "situacao", "_dup"]
    out = out.drop(columns=["_dup"]).drop_duplicates("CD_CVM")
    return out, tickers


# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------
def main() -> int:
    print(f"=== Mirror CVM: {ANO_INICIAL} a {ANO_FINAL} ===", file=sys.stderr)

    cadastro, tickers = construir_cadastro()
    print(f"  cadastro: {len(cadastro)} empresas; tickers: {len(tickers)}",
          file=sys.stderr)

    # Salvar tickers/cadastro
    companies = {
        "tickers": dict(zip(tickers["Codigo_Negociacao"], tickers["CD_CVM"].astype(int))),
        "empresas": cadastro.to_dict(orient="records"),
        "atualizado_em": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    (DATA_DIR / "companies.json").write_text(
        json.dumps(companies, ensure_ascii=False, indent=2)
    )

    # Construir frames consolidados (todos os anos em memoria)
    todos_dfp = []
    todos_itr = []
    for ano in range(ANO_INICIAL, ANO_FINAL + 1):
        print(f"Processando DFP {ano}...", file=sys.stderr)
        df = carregar_demonstrativos_ano(ano, "DFP")
        if not df.empty:
            todos_dfp.append(df)
        print(f"Processando ITR {ano}...", file=sys.stderr)
        df = carregar_demonstrativos_ano(ano, "ITR")
        if not df.empty:
            todos_itr.append(df)

    dfp = pd.concat(todos_dfp, ignore_index=True) if todos_dfp else pd.DataFrame()
    itr = pd.concat(todos_itr, ignore_index=True) if todos_itr else pd.DataFrame()

    print(f"  total DFP: {len(dfp):,} linhas", file=sys.stderr)
    print(f"  total ITR: {len(itr):,} linhas", file=sys.stderr)

    # Particionar por empresa e salvar parquet
    # Mantemos apenas colunas essenciais para compactar
    cols_essenciais = [
        "CD_CVM", "DENOM_CIA", "CNPJ_CIA", "DT_REFER",
        "VERSAO", "GRUPO_DFP", "MOEDA", "ESCALA_MOEDA",
        "ORDEM_EXERC", "DT_INI_EXERC", "DT_FIM_EXERC",
        "CD_CONTA", "DS_CONTA", "VL_CONTA",
        "DEMO", "TP_DF", "MODO",
    ]
    cols_existentes_dfp = [c for c in cols_essenciais if c in dfp.columns]
    cols_existentes_itr = [c for c in cols_essenciais if c in itr.columns]

    # Cleanup das pastas antigas
    for p in DATA_DFP.glob("*.parquet"):
        p.unlink()
    for p in DATA_ITR.glob("*.parquet"):
        p.unlink()

    def salvar_por_empresa(df: pd.DataFrame, cols: list, pasta: Path) -> int:
        if df.empty:
            return 0
        df = df[cols].copy()
        # Tipos enxutos
        for c in ["CD_CONTA", "DS_CONTA", "DENOM_CIA", "CNPJ_CIA",
                  "GRUPO_DFP", "ORDEM_EXERC", "MOEDA", "ESCALA_MOEDA",
                  "DEMO", "TP_DF", "MODO"]:
            if c in df.columns:
                df[c] = df[c].astype("category")
        # Dates como string para compactar
        for c in ["DT_REFER", "DT_INI_EXERC", "DT_FIM_EXERC"]:
            if c in df.columns:
                df[c] = pd.to_datetime(df[c], errors="coerce")

        n_empresas = 0
        for cd_cvm, sub in df.groupby("CD_CVM", observed=True):
            cd_cvm = int(cd_cvm)
            out = pasta / f"{cd_cvm}.parquet"
            sub.to_parquet(out, index=False, compression="zstd",
                           compression_level=9)
            n_empresas += 1
        return n_empresas

    n_dfp = salvar_por_empresa(dfp, cols_existentes_dfp, DATA_DFP)
    n_itr = salvar_por_empresa(itr, cols_existentes_itr, DATA_ITR)
    print(f"  DFP: {n_dfp} empresas; ITR: {n_itr} empresas", file=sys.stderr)

    # Metadata
    meta = {
        "atualizado_em": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "ano_inicial": ANO_INICIAL,
        "ano_final": ANO_FINAL,
        "n_empresas_dfp": n_dfp,
        "n_empresas_itr": n_itr,
        "n_linhas_dfp": int(len(dfp)),
        "n_linhas_itr": int(len(itr)),
        "demonstrativos": DEMONSTRATIVOS,
    }
    (DATA_DIR / "metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2)
    )
    print("OK", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
