"""
╔══════════════════════════════════════════════════════════╗
║      DASHBOARD FINANCEIRO MEI — Copiloto de Negócios     ║
╚══════════════════════════════════════════════════════════╝

Dependências:
    pip install streamlit plotly pypdf google-genai pillow

Uso:
    streamlit run dashboard_mei.py
"""

import streamlit as st
import json
import os
import time
import pypdf
from datetime import datetime
from pathlib import Path
from google import genai
from PIL import Image
import plotly.graph_objects as go

# ──────────────────────────────────────────────────────────
# ⚙️  CONFIGURAÇÃO DA PÁGINA
# ──────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Copiloto MEI",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ──────────────────────────────────────────────────────────
# 🗂️  CONSTANTES E CAMINHOS
# ──────────────────────────────────────────────────────────
PASTA_BASE      = Path(__file__).parent
PASTA_EXTRATOS  = PASTA_BASE / "extratos"
CAMINHO_BANCO   = PASTA_BASE / "historico_faturamento.json"
LIMITE_MEI      = 81_000.00
PAUSA_SEGUNDOS  = 15
MODELO_GEMINI   = "gemini-2.5-flash"

MESES_ORDEM = {
    "Jan": 1, "Fev": 2, "Mar": 3, "Abr": 4,
    "Mai": 5, "Jun": 6, "Jul": 7, "Ago": 8,
    "Set": 9, "Out": 10, "Nov": 11, "Dez": 12,
}

PASTA_EXTRATOS.mkdir(exist_ok=True)

# ──────────────────────────────────────────────────────────
# 🎨  ESTILOS
# ──────────────────────────────────────────────────────────
st.markdown("""
<style>
    /* Header principal */
    .hero {
        background: #0f0f1a;
        border: 1px solid #2a2a4a;
        border-radius: 14px;
        padding: 1.8rem 2rem;
        margin-bottom: 1.5rem;
        text-align: center;
    }
    .hero h1 { color: #e0e0ff; margin: 0; font-size: 2rem; }
    .hero p  { color: #6666aa; margin: 0.3rem 0 0; }

    /* Cards de KPI */
    .kpi-row { display: flex; gap: 1rem; margin-bottom: 1.2rem; }
    .kpi {
        flex: 1;
        background: #12122a;
        border: 1px solid #2a2a4a;
        border-radius: 12px;
        padding: 1rem 1.2rem;
    }
    .kpi .label { font-size: 0.75rem; color: #8888bb; text-transform: uppercase; letter-spacing: .05em; }
    .kpi .value { font-size: 1.5rem; font-weight: 600; color: #e0e0ff; margin-top: 2px; }
    .kpi .sub   { font-size: 0.78rem; color: #6666aa; margin-top: 2px; }

    /* Secção de upload */
    .upload-info {
        background: #12122a;
        border: 1px solid #2a2a4a;
        border-radius: 10px;
        padding: 1rem 1.2rem;
        font-size: 0.85rem;
        color: #8888bb;
    }

    /* Bloco de arquivo na lista */
    .file-item {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 4px 0;
        font-size: 0.82rem;
        color: #aaaacc;
    }

    div[data-testid="stMetricValue"] { color: #00d4ff !important; }
</style>
""", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────
# 🛠️  FUNÇÕES UTILITÁRIAS
# ──────────────────────────────────────────────────────────
PROMPT_EXTRATOR = """
Você é um sistema contábil especialista para MEI brasileiro.
Analise o extrato/comprovante e extraia o faturamento bruto com precisão.

REGRAS:
1. Identifique cada plataforma: iFood, 99Food, Rappi, Uber Eats, Stone,
   PagSeguro, Cielo, Rede, Getnet, Mercado Pago, PIX, Dinheiro, Outros.
2. Classifique: "comercio" (venda de produtos) ou "servico" (prestação de serviços).
3. Detecte o período (mês/ano) automaticamente.
4. faturamento_total = faturamento_comercio + faturamento_servicos.
5. Retorne SOMENTE JSON puro — sem markdown, sem texto extra.

Estrutura:
{
  "periodo": "Jan/2026",
  "plataformas": [
    { "nome": "iFood", "tipo": "comercio", "valor": 1500.00 },
    { "nome": "PIX",   "tipo": "comercio", "valor": 300.00  }
  ],
  "faturamento_comercio": 1800.00,
  "faturamento_servicos": 0.00,
  "faturamento_total": 1800.00
}
"""


def periodo_key(periodo: str) -> int:
    """Converte 'Jan/2026' em número ordenável (202601)."""
    try:
        mes_str, ano_str = periodo.split("/")
        return int(ano_str) * 100 + MESES_ORDEM.get(mes_str, 0)
    except Exception:
        return 0


@st.cache_data(ttl=3)
def carregar_banco() -> list:
    if not CAMINHO_BANCO.exists():
        return []
    try:
        conteudo = CAMINHO_BANCO.read_text(encoding="utf-8").strip()
        if not conteudo:
            return []
        dados = json.loads(conteudo)
        return dados if isinstance(dados, list) else [dados]
    except Exception:
        return []


def salvar_banco(dados: list) -> None:
    CAMINHO_BANCO.write_text(
        json.dumps(dados, indent=4, ensure_ascii=False),
        encoding="utf-8",
    )


def get_anos(banco: list) -> list[int]:
    anos = {int(r["periodo"].split("/")[1]) for r in banco if "/" in r.get("periodo", "")}
    ano_atual = datetime.now().year
    anos.add(ano_atual)
    return sorted(anos, reverse=True)


def filtrar_ano(banco: list, ano: int) -> list:
    return sorted(
        [r for r in banco if r.get("periodo", "").endswith(f"/{ano}")],
        key=lambda x: periodo_key(x.get("periodo", "")),
    )


def extrair_texto_pdf(caminho) -> str | None:
    try:
        leitor = pypdf.PdfReader(caminho)
        texto = "".join(p.extract_text() or "" for p in leitor.pages)
        return texto if texto.strip() else None
    except Exception:
        return None


def analisar_arquivo(caminho: Path, nome: str, client) -> dict | None:
    try:
        if nome.lower().endswith(".pdf"):
            texto = extrair_texto_pdf(str(caminho))
            if not texto:
                return None
            resp = client.models.generate_content(
                model=MODELO_GEMINI,
                contents=f"{PROMPT_EXTRATOR}\n\nEXTRATO:\n{texto}",
            )
        else:
            img = Image.open(str(caminho))
            resp = client.models.generate_content(
                model=MODELO_GEMINI,
                contents=[img, PROMPT_EXTRATOR],
            )

        texto_resp = resp.text.replace("```json", "").replace("```", "").strip()
        dados = json.loads(texto_resp)
        dados.setdefault("plataformas", [])
        dados.setdefault("faturamento_comercio", 0.0)
        dados.setdefault("faturamento_servicos", 0.0)
        dados.setdefault("faturamento_total", 0.0)
        dados["nome_arquivo"]       = nome
        dados["data_processamento"] = datetime.now().strftime("%d/%m/%Y %H:%M")
        return dados
    except Exception as e:
        st.error(f"Erro ao processar **{nome}**: {e}")
        return None


def cor_pct(pct: float) -> str:
    if pct >= 90: return "#ff4444"
    if pct >= 75: return "#ffaa00"
    return "#00dd88"


# ──────────────────────────────────────────────────────────
# 🔧  SIDEBAR
# ──────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️ Configurações")

    api_key = st.text_input(
        "🔑 Chave API Gemini",
        type="password",
        placeholder="Sua Chave API",
        help="Gerada em aistudio.google.com/app/apikey",
    )

    st.divider()

    banco_side = carregar_banco()
    anos_disp  = get_anos(banco_side)

    ano_selecionado = st.selectbox(
        "📅 Ano base",
        options=anos_disp,
        index=0,
    )

    st.divider()

    dados_ano_side = filtrar_ano(banco_side, ano_selecionado)
    total_ano_side = sum(float(r.get("faturamento_total", 0)) for r in dados_ano_side)
    pct_side       = (total_ano_side / LIMITE_MEI) * 100
    cor_side       = cor_pct(pct_side)

    st.markdown(f"**Limite MEI {ano_selecionado}**")
    st.progress(min(pct_side / 100, 1.0))
    st.markdown(
        f"<span style='color:{cor_side};font-size:.85rem;font-weight:600'>"
        f"R$ {total_ano_side:,.2f} / R$ {LIMITE_MEI:,.2f} &nbsp;({pct_side:.1f}%)"
        f"</span>",
        unsafe_allow_html=True,
    )

    if pct_side >= 90:
        st.error("🚨 Atenção: acima de 90% do limite MEI!")
    elif pct_side >= 75:
        st.warning("⚠️ Acima de 75% — monitore de perto.")

    st.divider()
    st.caption(f"📁 Banco: `{CAMINHO_BANCO.name}`")
    st.caption(f"📂 Extratos: `./extratos/`")

# ──────────────────────────────────────────────────────────
# 🏠  HEADER
# ──────────────────────────────────────────────────────────
st.markdown("""
<div class="hero">
    <h1>📈 Copiloto Financeiro MEI</h1>
    <p>Transformando extratos em inteligência fiscal — tudo em um só lugar</p>
</div>
""", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────
# 📑  ABAS PRINCIPAIS
# ──────────────────────────────────────────────────────────
tab_upload, tab_dash, tab_ia = st.tabs([
    "📤  Upload de Extratos",
    "📊  Dashboard",
    "🤖  Previsão com IA",
])

# ══════════════════════════════════════════════════════════
# TAB 1 — UPLOAD
# ══════════════════════════════════════════════════════════
with tab_upload:
    st.markdown(f"### 📁 Enviar extratos · Ano base **{ano_selecionado}**")

    col_up, col_lista = st.columns([3, 2], gap="large")

    # ── Área de upload ────────────────────────────────────
    with col_up:
        arquivos_enviados = st.file_uploader(
            "Arraste ou clique para selecionar",
            type=["pdf", "png", "jpg", "jpeg"],
            accept_multiple_files=True,
            label_visibility="collapsed",
        )

        if arquivos_enviados:
            st.markdown(f"**{len(arquivos_enviados)} arquivo(s) selecionado(s):**")
            for arq in arquivos_enviados:
                st.caption(f"• {arq.name}  —  {arq.size / 1024:.1f} KB")

            if st.button("💾 Salvar na pasta extratos", type="primary", use_container_width=True):
                salvos = 0
                for arq in arquivos_enviados:
                    destino = PASTA_EXTRATOS / arq.name
                    destino.write_bytes(arq.getbuffer())
                    salvos += 1
                st.success(f"✅ {salvos} arquivo(s) salvo(s) com sucesso!")
                st.cache_data.clear()
                st.rerun()

        st.markdown("""
        <div class="upload-info">
            <b>Formatos aceitos:</b> PDF, PNG, JPG, JPEG<br>
            <b>Exemplos:</b> print do iFood, extrato Stone, comprovante PIX, relatório Mercado Pago
        </div>
        """, unsafe_allow_html=True)

    # ── Lista de arquivos ─────────────────────────────────
    with col_lista:
        st.markdown("**Arquivos na pasta extratos:**")
        exts = (".pdf", ".png", ".jpg", ".jpeg")
        banco_up    = carregar_banco()
        processados = {r.get("nome_arquivo") for r in banco_up}
        arqs_pasta  = [a for a in os.listdir(PASTA_EXTRATOS) if a.lower().endswith(exts)]

        if arqs_pasta:
            for nome in sorted(arqs_pasta):
                if nome in processados:
                    st.markdown(f"✅ `{nome}`")
                else:
                    st.markdown(f"⏳ `{nome}`")
        else:
            st.caption("Nenhum arquivo encontrado")

    st.divider()

    # ── Processar novos arquivos ──────────────────────────
    st.markdown("### 🧠 Processar arquivos com IA")

    banco_proc   = carregar_banco()
    processados2 = {r.get("nome_arquivo") for r in banco_proc}
    novos        = [a for a in arqs_pasta if a not in processados2]

    if not novos:
        st.success("✅ Todos os arquivos já foram processados!")
    else:
        st.info(f"🔍  **{len(novos)}** arquivo(s) novo(s) aguardando análise.")

        if not api_key:
            st.warning("⚠️ Insira sua chave API na barra lateral para processar os arquivos.")
        else:
            if st.button(f"🚀 Analisar {len(novos)} arquivo(s)", type="primary"):
                gemini_client = genai.Client(api_key=api_key)
                barra  = st.progress(0.0, text="Iniciando...")
                status = st.empty()

                for idx, nome in enumerate(novos):
                    caminho = PASTA_EXTRATOS / nome
                    barra.progress(idx / len(novos), text=f"Processando {idx + 1}/{len(novos)}: {nome}")
                    status.info(f"🧠 Analisando **{nome}**...")

                    dados = analisar_arquivo(caminho, nome, gemini_client)

                    if dados:
                        banco_proc.append(dados)
                        salvar_banco(banco_proc)
                        status.success(
                            f"✅ **{nome}** → {dados.get('periodo')} | "
                            f"R$ {float(dados.get('faturamento_total', 0)):,.2f}"
                        )
                    else:
                        status.error(f"❌ Falha em **{nome}** — arquivo pulado.")

                    if idx < len(novos) - 1:
                        for seg in range(PAUSA_SEGUNDOS, 0, -1):
                            barra.progress(
                                (idx + 1) / len(novos),
                                text=f"⏱️ Aguardando {seg}s antes do próximo...",
                            )
                            time.sleep(1)

                barra.progress(1.0, text="Concluído!")
                st.success("🎉 Todos os arquivos foram processados!")
                st.cache_data.clear()
                st.rerun()

# ══════════════════════════════════════════════════════════
# TAB 2 — DASHBOARD
# ══════════════════════════════════════════════════════════
with tab_dash:
    banco_dash     = carregar_banco()
    dados_ano_dash = filtrar_ano(banco_dash, ano_selecionado)

    if not dados_ano_dash:
        st.info(f"📭 Nenhum dado para **{ano_selecionado}**. Envie extratos na aba Upload.")
    else:
        total    = sum(float(r.get("faturamento_total", 0))    for r in dados_ano_dash)
        comercio = sum(float(r.get("faturamento_comercio", 0)) for r in dados_ano_dash)
        servicos = sum(float(r.get("faturamento_servicos", 0)) for r in dados_ano_dash)
        media    = total / len(dados_ano_dash)
        pct_lim  = (total / LIMITE_MEI) * 100

        # ── KPIs ─────────────────────────────────────────
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("💰 Total bruto",    f"R$ {total:,.2f}")
        c2.metric("🛒 Comércio",        f"R$ {comercio:,.2f}")
        c3.metric("🔧 Serviços",        f"R$ {servicos:,.2f}")
        c4.metric("📊 Média mensal",    f"R$ {media:,.2f}")
        c5.metric("📏 Limite MEI",      f"{pct_lim:.1f}%",
                  delta=f"R$ {LIMITE_MEI - total:,.2f} restante",
                  delta_color="normal")

        st.divider()

        # ── Dados para gráficos ───────────────────────────
        meses       = [r.get("periodo", "") for r in dados_ano_dash]
        vals_com    = [float(r.get("faturamento_comercio", 0)) for r in dados_ano_dash]
        vals_svc    = [float(r.get("faturamento_servicos", 0)) for r in dados_ano_dash]
        vals_tot    = [float(r.get("faturamento_total",    0)) for r in dados_ano_dash]

        # ── Gráfico de barras mensais ─────────────────────
        fig_bar = go.Figure()
        fig_bar.add_trace(go.Bar(
            name="Comércio", x=meses, y=vals_com,
            marker_color="#00b4d8",
            text=[f"R$ {v:,.0f}" for v in vals_com],
            textposition="auto",
        ))
        if any(v > 0 for v in vals_svc):
            fig_bar.add_trace(go.Bar(
                name="Serviços", x=meses, y=vals_svc,
                marker_color="#7b2d8b",
                text=[f"R$ {v:,.0f}" for v in vals_svc],
                textposition="auto",
            ))
        fig_bar.update_layout(
            title=f"Faturamento Mensal — {ano_selecionado}",
            barmode="stack",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font_color="#cccccc",
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            height=400,
            yaxis=dict(tickprefix="R$ "),
        )
        st.plotly_chart(fig_bar, use_container_width=True)

        col_gauge, col_pizza = st.columns(2)

        # ── Gauge limite MEI ──────────────────────────────
        with col_gauge:
            fig_gauge = go.Figure(go.Indicator(
                mode="gauge+number+delta",
                value=total,
                number={"prefix": "R$ ", "valueformat": ",.2f"},
                delta={"reference": LIMITE_MEI, "valueformat": ",.2f", "suffix": " restante"},
                title={"text": f"Limite MEI {ano_selecionado}  (R$ {LIMITE_MEI:,.0f})"},
                gauge={
                    "axis":  {"range": [0, LIMITE_MEI], "tickformat": ",.0f"},
                    "bar":   {"color": "#00b4d8"},
                    "steps": [
                        {"range": [0,                LIMITE_MEI * .50], "color": "#0d2b0d"},
                        {"range": [LIMITE_MEI * .50, LIMITE_MEI * .75], "color": "#2b2b0d"},
                        {"range": [LIMITE_MEI * .75, LIMITE_MEI],       "color": "#2b0d0d"},
                    ],
                    "threshold": {
                        "line":      {"color": "red", "width": 3},
                        "thickness": 0.75,
                        "value":     LIMITE_MEI,
                    },
                },
            ))
            fig_gauge.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                font_color="#cccccc",
                height=310,
            )
            st.plotly_chart(fig_gauge, use_container_width=True)

        # ── Pizza por plataforma ──────────────────────────
        with col_pizza:
            plat_totais: dict[str, float] = {}
            for r in dados_ano_dash:
                for p in r.get("plataformas", []):
                    n = p.get("nome", "Outros")
                    plat_totais[n] = plat_totais.get(n, 0) + float(p.get("valor", 0))

            if plat_totais:
                fig_pie = go.Figure(go.Pie(
                    labels=list(plat_totais.keys()),
                    values=list(plat_totais.values()),
                    hole=0.42,
                    textinfo="label+percent",
                ))
                fig_pie.update_layout(
                    title="Receita por Plataforma",
                    paper_bgcolor="rgba(0,0,0,0)",
                    font_color="#cccccc",
                    height=310,
                    showlegend=True,
                )
                st.plotly_chart(fig_pie, use_container_width=True)

        # ── Linha acumulada vs limite ─────────────────────
        acumulado = []
        soma = 0.0
        for v in vals_tot:
            soma += v
            acumulado.append(round(soma, 2))

        fig_acum = go.Figure()
        fig_acum.add_trace(go.Scatter(
            x=meses, y=acumulado,
            mode="lines+markers+text",
            name="Acumulado",
            line=dict(color="#00b4d8", width=3),
            text=[f"R$ {v:,.0f}" for v in acumulado],
            textposition="top center",
            fill="tozeroy",
            fillcolor="rgba(0,180,216,0.08)",
        ))
        fig_acum.add_hline(
            y=LIMITE_MEI,
            line_dash="dash",
            line_color="red",
            annotation_text=f"Limite MEI  R$ {LIMITE_MEI:,.0f}",
            annotation_position="top left",
            annotation_font_color="#ff6666",
        )
        fig_acum.update_layout(
            title="Faturamento Acumulado no Ano",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font_color="#cccccc",
            height=360,
            yaxis=dict(tickprefix="R$ "),
        )
        st.plotly_chart(fig_acum, use_container_width=True)

        # ── Comparativo entre anos (se houver mais de 1) ──
        todos_anos = get_anos(banco_dash)
        if len(todos_anos) > 1:
            st.divider()
            st.markdown("### 📅 Comparativo entre anos")

            fig_comp = go.Figure()
            cores = ["#00b4d8", "#7b2d8b", "#00dd88", "#ffaa00", "#ff6666"]
            for i, ano in enumerate(sorted(todos_anos)):
                dados_a = filtrar_ano(banco_dash, ano)
                if not dados_a:
                    continue
                meses_a = [r.get("periodo", "").split("/")[0] for r in dados_a]
                vals_a  = [float(r.get("faturamento_total", 0)) for r in dados_a]
                fig_comp.add_trace(go.Scatter(
                    x=meses_a, y=vals_a,
                    mode="lines+markers",
                    name=str(ano),
                    line=dict(color=cores[i % len(cores)], width=2),
                ))
            fig_comp.update_layout(
                title="Evolução mensal por ano",
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                font_color="#cccccc",
                height=350,
                yaxis=dict(tickprefix="R$ "),
            )
            st.plotly_chart(fig_comp, use_container_width=True)

# ══════════════════════════════════════════════════════════
# TAB 3 — PREVISÃO COM IA
# ══════════════════════════════════════════════════════════
with tab_ia:
    st.markdown("### 🤖 Prospecção de faturamento com IA")
    st.caption(
        "A IA analisa todo o histórico acumulado e projeta os próximos meses "
        "com base em tendências, sazonalidade e crescimento."
    )

    banco_ia = carregar_banco()

    if len(banco_ia) < 2:
        st.info("📊 São necessários pelo menos **2 meses** de dados para gerar uma previsão confiável.")
    elif not api_key:
        st.warning("⚠️ Insira sua chave API na barra lateral para usar a previsão.")
    else:
        # Monta resumo do histórico
        hist_ord = sorted(banco_ia, key=lambda x: periodo_key(x.get("periodo", "")))
        linhas_hist = []
        for r in hist_ord:
            plats_str = " | ".join(
                f"{p['nome']}: R$ {float(p['valor']):,.2f}"
                for p in r.get("plataformas", [])
            )
            linhas_hist.append(
                f"- {r.get('periodo')}: "
                f"Total R$ {float(r.get('faturamento_total', 0)):,.2f} "
                f"| Comércio R$ {float(r.get('faturamento_comercio', 0)):,.2f} "
                f"| Serviços R$ {float(r.get('faturamento_servicos', 0)):,.2f}"
                + (f"\n  Plataformas: {plats_str}" if plats_str else "")
            )
        historico_texto = "\n".join(linhas_hist)
        total_hist      = sum(float(r.get("faturamento_total", 0)) for r in banco_ia)

        # KPIs do histórico
        c1, c2, c3 = st.columns(3)
        c1.metric("📅 Meses registrados",    len(banco_ia))
        c2.metric("💰 Total histórico",       f"R$ {total_hist:,.2f}")
        c3.metric("📈 Média histórica/mês",   f"R$ {total_hist / len(banco_ia):,.2f}")

        st.divider()

        meses_prev = st.slider(
            "Quantos meses deseja projetar?",
            min_value=1, max_value=6, value=3,
        )

        if st.button("🔮 Gerar previsão", type="primary", use_container_width=True):
            prompt_prev = f"""
Você é um consultor financeiro especialista em MEI brasileiro.

HISTÓRICO DE FATURAMENTO:
{historico_texto}

LIMITE ANUAL MEI: R$ {LIMITE_MEI:,.2f}

Analise os dados e projete os próximos {meses_prev} meses a partir do último período registrado.
Considere sazonalidade, tendência de crescimento e comportamento por plataforma.

Retorne SOMENTE JSON puro (sem markdown):
{{
  "analise_tendencia": "Descrição objetiva da tendência observada",
  "sazonalidade": "Meses altos e baixos identificados no histórico",
  "previsoes": [
    {{
      "mes": "Ago/2026",
      "valor_minimo": 0.0,
      "valor_esperado": 0.0,
      "valor_maximo": 0.0,
      "justificativa": "Razão da projeção"
    }}
  ],
  "alerta_mei": "Aviso sobre risco de ultrapassar o limite MEI",
  "recomendacao": "Recomendação estratégica prática para o negócio",
  "confianca": 82
}}
"""
            with st.spinner("🧠 Analisando histórico e calculando projeções..."):
                try:
                    cli_ia  = genai.Client(api_key=api_key)
                    resp_ia = cli_ia.models.generate_content(
                        model=MODELO_GEMINI,
                        contents=prompt_prev,
                    )
                    texto_ia = resp_ia.text.replace("```json", "").replace("```", "").strip()
                    previsao = json.loads(texto_ia)

                    # ── Análise de tendência ──────────────
                    st.markdown("#### 📊 Análise de tendência")
                    st.info(previsao.get("analise_tendencia", ""))

                    col_saz, col_conf = st.columns(2)
                    with col_saz:
                        st.markdown("**📅 Sazonalidade identificada**")
                        st.write(previsao.get("sazonalidade", ""))
                    with col_conf:
                        score = int(previsao.get("confianca", 0))
                        cor_c = "🟢" if score >= 75 else "🟡" if score >= 50 else "🔴"
                        st.metric("🎯 Confiança da previsão", f"{cor_c} {score}%")

                    st.divider()

                    # ── Gráfico histórico + projeção ──────
                    st.markdown("#### 🔮 Projeção dos próximos meses")

                    previsoes   = previsao.get("previsoes", [])
                    meses_p     = [p["mes"]                      for p in previsoes]
                    esperados   = [float(p["valor_esperado"])     for p in previsoes]
                    minimos     = [float(p["valor_minimo"])       for p in previsoes]
                    maximos     = [float(p["valor_maximo"])       for p in previsoes]

                    ultimos_hist = hist_ord[-6:]
                    meses_h      = [r.get("periodo") for r in ultimos_hist]
                    vals_h       = [float(r.get("faturamento_total", 0)) for r in ultimos_hist]

                    fig_f = go.Figure()

                    # Faixa de incerteza
                    fig_f.add_trace(go.Scatter(
                        x=meses_p + meses_p[::-1],
                        y=maximos + minimos[::-1],
                        fill="toself",
                        fillcolor="rgba(123,45,139,0.18)",
                        line=dict(color="rgba(0,0,0,0)"),
                        name="Intervalo previsto",
                        hoverinfo="skip",
                    ))

                    # Histórico real
                    fig_f.add_trace(go.Scatter(
                        x=meses_h, y=vals_h,
                        mode="lines+markers",
                        name="Histórico real",
                        line=dict(color="#00b4d8", width=2.5),
                        marker=dict(size=7),
                    ))

                    # Linha de previsão
                    fig_f.add_trace(go.Scatter(
                        x=meses_p, y=esperados,
                        mode="lines+markers+text",
                        name="Previsão",
                        line=dict(color="#cc88ff", width=2.5, dash="dash"),
                        marker=dict(size=9),
                        text=[f"R$ {v:,.0f}" for v in esperados],
                        textposition="top center",
                    ))

                    fig_f.update_layout(
                        plot_bgcolor="rgba(0,0,0,0)",
                        paper_bgcolor="rgba(0,0,0,0)",
                        font_color="#cccccc",
                        height=400,
                        legend=dict(orientation="h", yanchor="bottom", y=1.02),
                        yaxis=dict(tickprefix="R$ "),
                    )
                    st.plotly_chart(fig_f, use_container_width=True)

                    # ── Cards mensais ─────────────────────
                    cols_p = st.columns(len(previsoes))
                    for col_p, p in zip(cols_p, previsoes):
                        with col_p:
                            amplitude = (float(p["valor_maximo"]) - float(p["valor_minimo"])) / 2
                            st.metric(
                                label=p["mes"],
                                value=f"R$ {float(p['valor_esperado']):,.0f}",
                                delta=f"± R$ {amplitude:,.0f}",
                            )
                            st.caption(p.get("justificativa", ""))

                    st.divider()

                    # ── Alertas e recomendação ────────────
                    col_al, col_rec = st.columns(2)
                    with col_al:
                        st.markdown("#### ⚠️ Alerta Limite MEI")
                        st.warning(previsao.get("alerta_mei", ""))
                    with col_rec:
                        st.markdown("#### 💡 Recomendação estratégica")
                        st.success(previsao.get("recomendacao", ""))

                except json.JSONDecodeError:
                    st.error("A IA não retornou JSON válido. Tente novamente.")
                    with st.expander("Ver resposta bruta"):
                        st.code(resp_ia.text)
                except Exception as e:
                    st.error(f"Erro ao gerar previsão: {e}")
