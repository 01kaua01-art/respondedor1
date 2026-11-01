"""
Interface Web interativa construída com Streamlit para o Orquestrador de Agentes de IA em Loop.
Apresenta dados e recolhe configurações, mantendo o desacoplamento da lógica de negócio.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import streamlit as st

from schemas import (
    ConfiguracaoAgente,
    ConfiguracaoLoop,
    IteracaoLoop,
    Provedor,
    RegistroExecucao,
    StatusAvaliacao,
    StatusFinal,
)
from agentes import (
    VARIAVEIS_ENV,
    verificar_status_chaves,
)
from main import (
    carregar_execucao_por_id,
    executar_loop,
    listar_historico_execucoes,
)

# Exemplos de modelos identificados explicitamente como exemplos
EXEMPLOS_MODELOS = {
    Provedor.GEMINI: [
        "gemini-2.5-flash (Exemplo)",
        "gemini-2.5-pro (Exemplo)",
        "gemini-1.5-flash (Exemplo)",
        "gemini-1.5-pro (Exemplo)",
        "Outro modelo (digitar manualmente)",
    ],
    Provedor.OPENROUTER: [
        "meta-llama/llama-3.3-70b-instruct (Exemplo)",
        "deepseek/deepseek-chat (Exemplo)",
        "anthropic/claude-3.5-sonnet (Exemplo)",
        "google/gemini-2.0-flash-001 (Exemplo)",
        "mistralai/mistral-large-2411 (Exemplo)",
        "Outro modelo (digitar manualmente)",
    ],
    Provedor.GROQ: [
        "llama-3.3-70b-versatile (Exemplo)",
        "llama-3.1-8b-instant (Exemplo)",
        "mixtral-8x7b-32768 (Exemplo)",
        "gemma2-9b-it (Exemplo)",
        "Outro modelo (digitar manualmente)",
    ],
}


def extrair_identificador_modelo(selecao: str) -> str:
    """Extrai apenas o nome técnico do modelo, removendo sufixos informativos como '(Exemplo)'."""
    return selecao.replace(" (Exemplo)", "").strip()


def configurar_pagina() -> None:
    """Define metadados visuais da página Streamlit."""
    st.set_page_config(
        page_title="Agente em Loop: Geração, Avaliação & Poda",
        page_icon="🔄",
        layout="wide",
        initial_sidebar_state="expanded",
    )


def renderizar_barra_lateral() -> Optional[str]:
    """
    Renderiza status de credenciais e lista de execuções anteriores na barra lateral.
    Retorna o ID de uma execução selecionada para consulta, se houver.
    """
    st.sidebar.title("⚙️ Painel de Controle")

    # Status das chaves de API
    st.sidebar.subheader("Credenciais das APIs")
    chaves_status = verificar_status_chaves()

    for prov, var_name in VARIAVEIS_ENV.items():
        esta_ok = chaves_status.get(prov.value, False)
        if esta_ok:
            st.sidebar.success(f"**{prov.value.upper()}**: Configurada ✅", icon="🔑")
        else:
            st.sidebar.warning(f"**{prov.value.upper()}**: Ausente (`{var_name}`) ⚠️", icon="🔒")

    with st.sidebar.expander("ℹ️ Como configurar chaves", expanded=False):
        st.markdown(
            "Crie um arquivo `.env` na raiz do projeto com as chaves necessárias:\n"
            "```env\n"
            "GEMINI_API_KEY=...\n"
            "OPENROUTER_API_KEY=...\n"
            "GROQ_API_KEY=...\n"
            "```\n"
            "*Nenhuma chamada é enviada caso a chave requerida não esteja configurada.*"
        )

    st.sidebar.markdown("---")
    st.sidebar.subheader("📜 Histórico de Execuções")

    historico = listar_historico_execucoes()

    if not historico:
        st.sidebar.caption("Nenhuma execução registrada até o momento.")
        return None

    opcoes = {"nova": "➕ Nova Execução"}
    for reg in historico:
        data_str = reg.data_hora[:16].replace("T", " ")
        status_icone = {
            StatusFinal.APROVADO: "✅",
            StatusFinal.REJEITADO: "✂️",
            StatusFinal.LIMITE_ATINGIDO: "⏳",
            StatusFinal.ERRO: "❌",
        }.get(reg.estado_final, "📄")
        resumo_obj = reg.configuracao.objetivo[:28] + ("..." if len(reg.configuracao.objetivo) > 28 else "")
        label = f"{status_icone} {data_str} - {resumo_obj}"
        opcoes[reg.id] = label

    escolha = st.sidebar.selectbox(
        "Selecione uma execução para consultar:",
        options=list(opcoes.keys()),
        format_func=lambda x: opcoes[x],
        index=0,
    )

    if escolha == "nova":
        return None
    return escolha


def renderizar_cabecalho() -> None:
    """Exibe título e fluxo didático do sistema."""
    st.title("🔄 Orquestrador de Agentes de IA em Loop")
    st.markdown(
        "Sistema autônomo com **geração**, **avaliação estruturada**, **refinamento iterativo** e **poda de propostas**."
    )

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.info("**1. Geração**\n\nCria a proposta a partir do objetivo e critérios.")
    with col2:
        st.info("**2. Avaliação**\n\nAnalisa rigorosamente e gera decisão JSON estruturada.")
    with col3:
        st.info("**3. Refinamento ou Poda**\n\nCorrige se 'refinar', encerra se 'aprovado' ou 'rejeitado'.")
    with col4:
        st.info("**4. Histórico**\n\nPersiste cada versão e justificativa em JSON.")


def renderizar_detalhes_avaliacao(avaliacao: Any) -> None:
    """Renderiza a caixa visual com o resultado da avaliação."""
    if avaliacao.status == StatusAvaliacao.APROVADO:
        st.success(f"**Decisão:** APROVADO ✅\n\n**Justificativa:** {avaliacao.motivo}")
    elif avaliacao.status == StatusAvaliacao.REJEITADO:
        st.error(f"**Decisão:** REJEITADO (Ramo Podado) ✂️\n\n**Justificativa da Rejeição:** {avaliacao.motivo}")
    elif avaliacao.status == StatusAvaliacao.REFINAR:
        st.warning(f"**Decisão:** REFINAR 🔄\n\n**Motivo:** {avaliacao.motivo}")
        if avaliacao.pontos_a_corrigir:
            st.markdown("**Pontos específicos a corrigir:**")
            for p in avaliacao.pontos_a_corrigir:
                st.markdown(f"- ⚠️ {p}")


def renderizar_iteracao(iteracao: IteracaoLoop) -> None:
    """Renderiza uma iteração contendo a versão da proposta e sua avaliação."""
    rotulo = f"Iteração {iteracao.numero_iteracao} - {iteracao.tipo.replace('_', ' ').title()}"
    status_emoji = {
        StatusAvaliacao.APROVADO: "✅ Aprovado",
        StatusAvaliacao.REJEITADO: "✂️ Rejeitado",
        StatusAvaliacao.REFINAR: "🔄 Refinar",
    }.get(iteracao.avaliacao.status, "")

    with st.expander(f"📌 {rotulo} ({status_emoji})", expanded=True):
        st.markdown(f"**Horário:** `{iteracao.timestamp}`")

        col_prop, col_aval = st.columns([1.2, 1.0])
        with col_prop:
            st.subheader("📝 Proposta Gerada")
            st.markdown(iteracao.proposta)

        with col_aval:
            st.subheader("🧐 Avaliação do Agente")
            renderizar_detalhes_avaliacao(iteracao.avaliacao)


def renderizar_execucao_completa(registro: RegistroExecucao) -> None:
    """Apresenta o relatório visual completo de uma execução."""
    # Banner de desfecho
    if registro.estado_final == StatusFinal.APROVADO:
        st.success(
            f"### 🎉 Proposta Aprovada com Sucesso!\n"
            f"O avaliador aprovou plenamente a proposta após {len(registro.iteracoes)} iteração(ões)."
        )
    elif registro.estado_final == StatusFinal.REJEITADO:
        st.error(
            f"### ✂️ Ramo Podado: Proposta Rejeitada\n"
            f"O avaliador rejeitou a proposta. O ramo de refinamento foi interrompido imediatamente."
        )
    elif registro.estado_final == StatusFinal.LIMITE_ATINGIDO:
        st.warning(
            f"### ⏳ Limite de Refinamentos Atingido\n"
            f"Foram realizadas {len(registro.iteracoes)} iterações sem que houvesse aprovação ou rejeição definitiva."
        )
    elif registro.estado_final == StatusFinal.ERRO:
        st.error(
            f"### ❌ Execução Interrompida por Erro\n"
            f"Detalhes: {registro.erro or 'Erro não especificado'}"
        )

    # Informações gerais da execução
    with st.expander("ℹ️ Metadados da Execução", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**ID da Execução:** `{registro.id}`")
            st.markdown(f"**Data e Hora:** `{registro.data_hora}`")
            st.markdown(f"**Objetivo:** {registro.configuracao.objetivo}")
            st.markdown(f"**Critérios:** {registro.configuracao.criterios}")
        with c2:
            st.markdown(
                f"**Agente Gerador:** `{registro.configuracao.gerador.provedor.value.upper()}` "
                f"(`{registro.configuracao.gerador.modelo}`)"
            )
            st.markdown(
                f"**Agente Avaliador:** `{registro.configuracao.avaliador.provedor.value.upper()}` "
                f"(`{registro.configuracao.avaliador.modelo}`)"
            )
            st.markdown(f"**Refinamentos Máximos:** `{registro.configuracao.max_refinamentos}`")
            st.markdown(f"**Total de Iterações:** `{len(registro.iteracoes)}`")

    # Exibição de cada versão e avaliação
    st.markdown("### 📋 Ciclo de Iterações e Versões")
    for it in registro.iteracoes:
        renderizar_iteracao(it)

    # Proposta final em destaque se houver
    if registro.proposta_final:
        st.markdown("### 🏆 Proposta Final Aceita / Resultante")
        st.info(registro.proposta_final)


def renderizar_configurador_agente(titulo: str, prefixo_key: str) -> ConfiguracaoAgente:
    """Renderiza os seletores de provedor e modelo de um agente."""
    st.subheader(titulo)
    provedor_selecionado = st.selectbox(
        "Provedor:",
        options=[Provedor.GEMINI, Provedor.OPENROUTER, Provedor.GROQ],
        format_func=lambda p: {
            Provedor.GEMINI: "Google Gemini (google-genai)",
            Provedor.OPENROUTER: "OpenRouter (openai)",
            Provedor.GROQ: "Groq Cloud (openai)",
        }.get(p, p.value),
        key=f"{prefixo_key}_provedor",
    )

    exemplos = EXEMPLOS_MODELOS[provedor_selecionado]
    modelo_escolhido_exemplo = st.selectbox(
        "Modelo (Exemplos sugeridos):",
        options=exemplos,
        index=0,
        key=f"{prefixo_key}_modelo_exemplo",
        help="Modelos sugeridos como exemplos. Selecione 'Outro' para digitar qualquer outro modelo desejado.",
    )

    if "Outro" in modelo_escolhido_exemplo:
        modelo_final = st.text_input(
            "Digite o identificador do modelo:",
            value="",
            placeholder="ex: deepseek/deepseek-r1 ou gpt-4o",
            key=f"{prefixo_key}_modelo_custom",
        ).strip()
    else:
        modelo_final = extrair_identificador_modelo(modelo_escolhido_exemplo)

    return ConfiguracaoAgente(provedor=provedor_selecionado, modelo=modelo_final)


def main() -> None:
    """Ponto de entrada da aplicação Streamlit."""
    configurar_pagina()
    id_selecionado = renderizar_barra_lateral()

    # Se uma execução passada foi selecionada na barra lateral, exibe os dados salvos
    if id_selecionado:
        st.header("🔍 Consulta de Execução Anterior")
        registro_recuperado = carregar_execucao_por_id(id_selecionado)
        if registro_recuperado:
            renderizar_execucao_completa(registro_recuperado)
        else:
            st.error("Não foi possível carregar os dados desta execução.")
        return

    # Tela principal para nova execução
    renderizar_cabecalho()
    st.markdown("---")

    st.header("🛠️ Configuração da Nova Execução")

    with st.form("form_execucao_loop"):
        objetivo = st.text_area(
            "🎯 Objetivo da Tarefa:",
            value="",
            placeholder="Exemplo: Desenvolver uma estratégia de precificação dinâmica para uma startup SaaS B2B.",
            height=100,
            help="Descreva detalhadamente o que a proposta deve resolver ou entregar.",
        )

        criterios = st.text_area(
            "📐 Critérios de Avaliação:",
            value="",
            placeholder=(
                "Exemplo:\n"
                "1. Deve conter ao menos 3 planos de assinatura claramente diferenciados.\n"
                "2. Deve incluir análise de custos marginais e métrica de expansão (upsell).\n"
                "3. Deve prever política contra churn e período de teste gratuito.\n"
                "4. Texto coeso, claro e sem jargões desnecessários."
            ),
            height=130,
            help="Critérios objetivos que o avaliador usará para aprovar, pedir refinamento ou rejeitar.",
        )

        col_gerador, col_avaliador = st.columns(2)
        with col_gerador:
            config_gerador = renderizar_configurador_agente("🤖 Agente Gerador", "gerador")

        with col_avaliador:
            config_avaliador = renderizar_configurador_agente("🧐 Agente Avaliador", "avaliador")

        max_refinamentos = st.number_input(
            "🔄 Número Máximo de Refinamentos Permitidos:",
            min_value=1,
            max_value=10,
            value=3,
            step=1,
            help="Valor padrão: 3. Limite de ciclos de correção para evitar loops infinitos caso a proposta não atinja aprovação.",
        )

        submetido = st.form_submit_button(
            "🚀 Iniciar Loop Autônomo de Agentes",
            use_container_width=True,
            type="primary",
        )

    if submetido:
        # Validação 1: Campos de texto preenchidos
        if not objetivo.strip() or not criterios.strip():
            st.error("❌ Por favor, preencha o objetivo da tarefa e os critérios de avaliação antes de prosseguir.")
            return

        # Validação 2: Modelos válidos
        if not config_gerador.modelo or not config_avaliador.modelo:
            st.error("❌ Por favor, informe um modelo válido para ambos os agentes (gerador e avaliador).")
            return

        # Validação 3: Chaves de API configuradas para os provedores escolhidos
        chaves_status = verificar_status_chaves()
        provedores_usados = {config_gerador.provedor, config_avaliador.provedor}
        chaves_faltando = []

        for prov in provedores_usados:
            if not chaves_status.get(prov.value, False):
                var_necessaria = VARIAVEIS_ENV.get(prov, "")
                chaves_faltando.append(f"**{prov.value.upper()}** (variável `{var_necessaria}`)")

        if chaves_faltando:
            st.error(
                "❌ **Execução não iniciada**: Faltam chaves de API necessárias no arquivo `.env`:\n\n"
                + "\n".join(f"- {c}" for c in chaves_faltando)
                + "\n\nConfigure-as no arquivo `.env` para poder prosseguir."
            )
            return

        # Montagem do objeto de configuração Pydantic
        try:
            config_loop = ConfiguracaoLoop(
                objetivo=objetivo,
                criterios=criterios,
                gerador=config_gerador,
                avaliador=config_avaliador,
                max_refinamentos=int(max_refinamentos),
            )
        except Exception as e_config:
            st.error(f"Erro nos parâmetros de configuração: {str(e_config)}")
            return

        # Container visual para acompanhamento em tempo real
        st.markdown("---")
        st.subheader("⚡ Acompanhamento do Loop em Tempo Real")

        status_container = st.status("Iniciando orquestração...", expanded=True)
        placeholder_iteracoes = st.empty()
        iteracoes_em_tempo_real: List[IteracaoLoop] = []

        def callback_progresso(evento: str, dados: Dict[str, Any]) -> None:
            if evento == "inicio_validacao_chaves":
                status_container.write("🔑 Validando credenciais das APIs...")
            elif evento == "inicio_geracao":
                status_container.write(f"📝 Gerando proposta inicial com o modelo `{config_gerador.modelo}`...")
            elif evento == "inicio_avaliacao":
                it_num = dados.get("iteracao", 1)
                status_container.write(f"🧐 Avaliador analisando a proposta da iteração {it_num} com `{config_avaliador.modelo}`...")
            elif evento == "iteracao_concluida":
                iteracao: IteracaoLoop = dados.get("iteracao")
                iteracoes_em_tempo_real.append(iteracao)
                with placeholder_iteracoes.container():
                    st.write(f"**Iterações processadas até agora: {len(iteracoes_em_tempo_real)}**")
                    for it_local in iteracoes_em_tempo_real:
                        renderizar_iteracao(it_local)
            elif evento == "inicio_refinamento":
                ref_num = dados.get("refinamento_numero", 1)
                status_container.write(f"🔄 Refinando proposta (Refinamento #{ref_num} de {config_loop.max_refinamentos})...")
            elif evento == "finalizado_aprovado":
                status_container.update(label="✅ Loop concluído: Proposta aprovada!", state="complete", expanded=False)
            elif evento == "finalizado_rejeitado":
                status_container.update(label="✂️ Loop encerrado: Ramo rejeitado e podado.", state="error", expanded=False)
            elif evento == "finalizado_limite":
                status_container.update(label="⏳ Loop concluído: Limite de refinamentos atingido.", state="complete", expanded=False)
            elif evento == "erro":
                status_container.update(label=f"❌ Erro na execução: {dados.get('mensagem', '')}", state="error", expanded=True)

        # Execução do loop delegada ao main.py
        with st.spinner("Agentes em execução..."):
            registro_final = executar_loop(
                config=config_loop,
                callback_evento=callback_progresso,
            )

        # Apresentação do resultado completo
        st.markdown("---")
        st.subheader("🏁 Relatório da Execução Concluída")
        renderizar_execucao_completa(registro_final)


if __name__ == "__main__":
    main()
