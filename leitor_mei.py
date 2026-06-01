"""
╔══════════════════════════════════════════════════════════╗
║       COPILOTO FINANCEIRO MEI — Leitor de Extratos       ║
║  Transforma prints/PDFs de vendas em inteligência fiscal ║
╚══════════════════════════════════════════════════════════╝

Dependências:
    pip install pypdf google-genai pillow rich

Uso:
    1. Coloque seus extratos (PDF, PNG, JPG, JPEG) na pasta ./extratos
    2. Execute: python leitor_mei.py
    3. O histórico fica salvo em historico_faturamento.json
"""

import time
import pypdf
import os
import json
from datetime import datetime
from google import genai
from PIL import Image
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

# ──────────────────────────────────────────────────────────
# ⚙️  CONFIGURAÇÕES
# ──────────────────────────────────────────────────────────
GEMINI_API_KEY        = "***"   # Chave começa com AIza...
PAUSA_ENTRE_ARQUIVOS  = 15                        # Segundos entre chamadas (evita erro 429)
LIMITE_MEI_ANUAL      = 81_000.00                 # Limite anual MEI em R$
MODELO_GEMINI         = "gemini-2.5-flash"

client  = genai.Client(api_key=GEMINI_API_KEY)
console = Console()

# ──────────────────────────────────────────────────────────
# 🧠  PROMPT MESTRE — Instruções para a IA
# ──────────────────────────────────────────────────────────
PROMPT_MESTRE = """
Você é um sistema contábil especialista para MEI (Microempreendedor Individual) brasileiro.
Analise cuidadosamente o extrato/comprovante/print enviado e extraia os dados de faturamento bruto.

REGRAS OBRIGATÓRIAS:
1. Identifique cada plataforma separadamente: iFood, 99Food, Rappi, Uber Eats,
   Stone, PagSeguro, Cielo, Rede, Getnet, Mercado Pago, PIX, Dinheiro, Outros.
2. Classifique cada receita:
   - "comercio"  → venda de produtos físicos ou digitais
   - "servico"   → prestação de serviços (mão de obra, delivery próprio, etc.)
3. Detecte o período exato (mês e ano) a partir das datas no documento.
4. faturamento_total deve ser EXATAMENTE a soma de faturamento_comercio + faturamento_servicos.
5. Use APENAS valores brutos (antes de descontos/taxas da plataforma).
6. Retorne SOMENTE JSON puro, sem markdown, sem ```json, sem texto antes ou depois.

Estrutura JSON obrigatória:
{
  "periodo": "Jan/2026",
  "plataformas": [
    { "nome": "iFood",    "tipo": "comercio", "valor": 1500.00 },
    { "nome": "Stone",    "tipo": "comercio", "valor": 800.00  },
    { "nome": "PIX",      "tipo": "comercio", "valor": 300.00  }
  ],
  "faturamento_comercio": 2600.00,
  "faturamento_servicos": 0.00,
  "faturamento_total": 2600.00
}
"""

# ──────────────────────────────────────────────────────────
# 📄  EXTRAÇÃO DE TEXTO DE PDF
# ──────────────────────────────────────────────────────────
def extrair_texto_pdf(caminho: str) -> str | None:
    try:
        leitor = pypdf.PdfReader(caminho)
        texto = "".join(pagina.extract_text() or "" for pagina in leitor.pages)
        return texto if texto.strip() else None
    except Exception as e:
        console.print(f"  [red]❌ Erro ao ler PDF: {e}[/red]")
        return None


# ──────────────────────────────────────────────────────────
# 🤖  ANÁLISE COM GEMINI
# ──────────────────────────────────────────────────────────
def analisar_com_gemini(conteudo, nome_arquivo: str, eh_imagem: bool = False) -> dict | None:
    console.print(f"  [cyan]🧠 Enviando para análise IA ({MODELO_GEMINI})...[/cyan]")
    try:
        if eh_imagem:
            img = Image.open(conteudo)
            resposta = client.models.generate_content(
                model=MODELO_GEMINI,
                contents=[img, PROMPT_MESTRE]
            )
        else:
            resposta = client.models.generate_content(
                model=MODELO_GEMINI,
                contents=f"{PROMPT_MESTRE}\n\nEXTRATO PARA ANÁLISE:\n{conteudo}"
            )

        texto_bruto = resposta.text.replace("```json", "").replace("```", "").strip()
        dados = json.loads(texto_bruto)

        # Garante campos essenciais
        dados.setdefault("plataformas", [])
        dados.setdefault("faturamento_comercio", 0.0)
        dados.setdefault("faturamento_servicos", 0.0)
        dados.setdefault("faturamento_total", 0.0)
        dados["nome_arquivo"]        = nome_arquivo
        dados["data_processamento"]  = datetime.now().strftime("%d/%m/%Y %H:%M")
        return dados

    except json.JSONDecodeError as e:
        console.print(f"  [red]❌ A IA não retornou JSON válido: {e}[/red]")
        console.print(f"  [dim]Resposta recebida: {resposta.text[:200]}[/dim]")
        return None
    except Exception as e:
        console.print(f"  [red]❌ Erro na chamada à IA: {e}[/red]")
        return None


# ──────────────────────────────────────────────────────────
# 💾  BANCO DE DADOS LOCAL (JSON)
# ──────────────────────────────────────────────────────────
def carregar_banco(caminho: str) -> list:
    if not os.path.exists(caminho):
        return []
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            conteudo = f.read().strip()
            if not conteudo:
                return []
            dados = json.loads(conteudo)
            return dados if isinstance(dados, list) else [dados]
    except Exception as e:
        console.print(f"[yellow]⚠️  Histórico corrompido, iniciando banco novo: {e}[/yellow]")
        return []


def salvar_banco(caminho: str, dados: list) -> None:
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f, indent=4, ensure_ascii=False)


# ──────────────────────────────────────────────────────────
# 📊  EXIBIÇÃO — TABELA DINÂMICA + RESUMO ANUAL
# ──────────────────────────────────────────────────────────
def cor_percentual(pct: float) -> str:
    if pct >= 90:  return "bold red"
    if pct >= 75:  return "red"
    if pct >= 50:  return "yellow"
    return "green"


def exibir_relatorio(banco: list) -> None:

    # ── Tabela principal ─────────────────────────────────
    tabela = Table(
        title="📊  HISTÓRICO DE FATURAMENTO BRUTO — MEI",
        box=box.ROUNDED,
        header_style="bold white on dark_blue",
        show_lines=True,
        expand=True,
        padding=(0, 1),
    )
    tabela.add_column("Período",       style="bold cyan",    justify="center", min_width=10)
    tabela.add_column("Arquivo",       style="dim",          max_width=22)
    tabela.add_column("Plataformas detectadas",              min_width=30)
    tabela.add_column("Comércio",      style="green",        justify="right",  min_width=13)
    tabela.add_column("Serviços",      style="blue",         justify="right",  min_width=13)
    tabela.add_column("Total Bruto",   style="bold magenta", justify="right",  min_width=14)

    resumo_anual: dict[str, dict] = {}
    total_geral = {"comercio": 0.0, "servicos": 0.0, "total": 0.0}

    for r in sorted(banco, key=lambda x: x.get("periodo", "")):
        comercio = float(r.get("faturamento_comercio", 0))
        servicos = float(r.get("faturamento_servicos", 0))
        total    = float(r.get("faturamento_total",    0))

        # Monta descrição de plataformas
        plats = r.get("plataformas", [])
        if plats:
            linhas_plat = []
            for p in plats:
                tipo_abrev = "COM" if p.get("tipo", "").lower() == "comercio" else "SVC"
                linhas_plat.append(
                    f"[yellow]{p['nome']}[/yellow] "
                    f"[dim]({tipo_abrev})[/dim] "
                    f"[white]R$ {float(p['valor']):,.2f}[/white]"
                )
            plats_str = "\n".join(linhas_plat)
        else:
            nomes = r.get("plataformas_detectadas", ["N/A"])
            plats_str = "[dim]" + ", ".join(nomes) + "[/dim]"

        tabela.add_row(
            r.get("periodo", "N/I"),
            r.get("nome_arquivo", "")[:22],
            plats_str,
            f"R$ {comercio:,.2f}",
            f"R$ {servicos:,.2f}",
            f"R$ {total:,.2f}",
        )

        # Acumula totais
        total_geral["comercio"] += comercio
        total_geral["servicos"] += servicos
        total_geral["total"]    += total

        # Agrupa por ano
        periodo = r.get("periodo", "")
        ano = periodo[-4:] if len(periodo) >= 4 else "N/A"
        if ano not in resumo_anual:
            resumo_anual[ano] = {"comercio": 0.0, "servicos": 0.0, "total": 0.0, "meses": 0}
        resumo_anual[ano]["comercio"] += comercio
        resumo_anual[ano]["servicos"] += servicos
        resumo_anual[ano]["total"]    += total
        resumo_anual[ano]["meses"]    += 1

    console.print(tabela)
    console.print()

    # ── Resumo anual ─────────────────────────────────────
    tab_anual = Table(
        title="📅  RESUMO ANUAL",
        box=box.SIMPLE_HEAVY,
        header_style="bold white on dark_green",
        show_lines=False,
        padding=(0, 1),
    )
    tab_anual.add_column("Ano",          style="bold",         justify="center")
    tab_anual.add_column("Meses reg.",                         justify="center")
    tab_anual.add_column("Comércio",     style="green",        justify="right")
    tab_anual.add_column("Serviços",     style="blue",         justify="right")
    tab_anual.add_column("Total Bruto",  style="bold magenta", justify="right")
    tab_anual.add_column(f"Limite MEI  R$ {LIMITE_MEI_ANUAL:,.0f}",
                                                                justify="right")

    for ano, vals in sorted(resumo_anual.items()):
        pct  = (vals["total"] / LIMITE_MEI_ANUAL) * 100
        cor  = cor_percentual(pct)
        alerta = " ⚠️" if pct >= 75 else (" 🔴" if pct >= 90 else "")
        tab_anual.add_row(
            ano,
            str(vals["meses"]),
            f"R$ {vals['comercio']:,.2f}",
            f"R$ {vals['servicos']:,.2f}",
            f"R$ {vals['total']:,.2f}",
            f"[{cor}]{pct:.1f}% utilizado{alerta}[/{cor}]",
        )

    console.print(tab_anual)
    console.print()

    # ── Painel de totais ──────────────────────────────────
    pct_acum = (total_geral["total"] / LIMITE_MEI_ANUAL) * 100
    cor_acum = cor_percentual(pct_acum)
    aviso = ""
    if pct_acum >= 90:
        aviso = "\n[bold red]🚨 ATENÇÃO: Você está próximo do limite MEI! Considere formalizar como ME.[/bold red]"
    elif pct_acum >= 75:
        aviso = "\n[yellow]⚠️  Atenção: Faturamento acima de 75% do limite anual. Monitore de perto![/yellow]"

    console.print(Panel(
        f"[bold white]💰 FATURAMENTO BRUTO TOTAL ACUMULADO:[/bold white] "
        f"[bold green]R$ {total_geral['total']:,.2f}[/bold green]\n"
        f"[dim]  Comércio: R$ {total_geral['comercio']:,.2f}   |   "
        f"Serviços: R$ {total_geral['servicos']:,.2f}[/dim]\n"
        f"[{cor_acum}]  Limite MEI anual: R$ {LIMITE_MEI_ANUAL:,.2f} — "
        f"{pct_acum:.1f}% do limite utilizado[/{cor_acum}]"
        f"{aviso}",
        title="[bold] RESUMO GERAL [/bold]",
        border_style="blue",
        padding=(1, 2),
    ))


# ──────────────────────────────────────────────────────────
# 🚀  EXECUÇÃO PRINCIPAL
# ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    pasta_atual     = os.path.dirname(os.path.abspath(__file__))
    pasta_extratos  = os.path.join(pasta_atual, "extratos")
    caminho_banco   = os.path.join(pasta_atual, "historico_faturamento.json")
    extensoes_ok    = (".pdf", ".png", ".jpg", ".jpeg")

    os.makedirs(pasta_extratos, exist_ok=True)

    console.print(Panel(
        "[bold cyan]📈  COPILOTO FINANCEIRO MEI[/bold cyan]\n"
        "[dim]Transformando extratos em inteligência fiscal[/dim]",
        border_style="cyan",
        padding=(1, 4),
    ))

    # Carrega histórico salvo
    banco = carregar_banco(caminho_banco)
    arquivos_ja_processados = {item.get("nome_arquivo") for item in banco}

    # Descobre arquivos novos na pasta extratos/
    todos_arquivos = [
        a for a in os.listdir(pasta_extratos)
        if a.lower().endswith(extensoes_ok)
    ]
    novos = [a for a in todos_arquivos if a not in arquivos_ja_processados]

    if not novos:
        console.print("[green]✅  Nenhum arquivo novo encontrado. Exibindo histórico completo.[/green]\n")
    else:
        console.print(f"[cyan]🔍  {len(novos)} novo(s) arquivo(s) para processar.[/cyan]\n")

        for idx, nome_arquivo in enumerate(novos):
            caminho = os.path.join(pasta_extratos, nome_arquivo)
            console.print(f"[bold]📂  [{idx + 1}/{len(novos)}] {nome_arquivo}[/bold]")

            dados = None
            if nome_arquivo.lower().endswith(".pdf"):
                texto = extrair_texto_pdf(caminho)
                if texto:
                    dados = analisar_com_gemini(texto, nome_arquivo, eh_imagem=False)
                else:
                    console.print("  [yellow]⚠️  PDF sem texto extraível. Tente converter para imagem.[/yellow]")
            else:
                dados = analisar_com_gemini(caminho, nome_arquivo, eh_imagem=True)

            if dados:
                banco.append(dados)
                salvar_banco(caminho_banco, banco)   # Salva imediatamente após cada arquivo
                console.print(f"  [green]✅  Salvo! Período: {dados.get('periodo', 'N/I')} — "
                               f"Total: R$ {float(dados.get('faturamento_total', 0)):,.2f}[/green]")
            else:
                console.print(f"  [red]❌  Falha ao processar {nome_arquivo}. Arquivo pulado.[/red]")

            # Pausa de segurança entre requisições (evita erro 429)
            if idx < len(novos) - 1:
                console.print(f"  [dim]⏱️  Aguardando {PAUSA_ENTRE_ARQUIVOS}s antes do próximo arquivo...[/dim]")
                time.sleep(PAUSA_ENTRE_ARQUIVOS)

        console.print()

    # Exibe relatório completo
    if banco:
        exibir_relatorio(banco)
    else:
        console.print(
            Panel(
                "[yellow]📭  Nenhum dado no histórico.\n"
                "Adicione seus extratos (PDF/PNG/JPG) na pasta [bold]./extratos[/bold] e execute novamente.[/yellow]",
                border_style="yellow",
            )
        )
