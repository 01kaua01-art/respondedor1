"""
Testes unitários e de integração local para o projeto meu_agente_loop.
Valida schemas, parsing de JSON, orquestração do loop (aprovação, refino, poda e limite),
persistência e tratamento de erros sem depender de chamadas reais às APIs externas.
"""

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from schemas import (
    ConfiguracaoAgente,
    ConfiguracaoLoop,
    IteracaoLoop,
    Provedor,
    RegistroExecucao,
    ResultadoAvaliacao,
    StatusAvaliacao,
    StatusFinal,
)
from agentes import (
    ChaveAPINaoConfiguradaError,
    ErroValidacaoAvaliador,
    extrair_json_de_texto,
    obter_chave_api,
)
from main import (
    carregar_execucao_por_id,
    executar_loop,
    listar_historico_execucoes,
    salvar_execucao,
)


def test_schemas_validacao():
    print("Testando schemas...")
    # 1. Aprovado zera pontos_a_corrigir
    res = ResultadoAvaliacao(
        status=StatusAvaliacao.APROVADO,
        motivo="Excelente proposta, atende a todos os critérios.",
        pontos_a_corrigir=["ponto desnecessario"],
    )
    assert res.status == StatusAvaliacao.APROVADO
    assert res.pontos_a_corrigir == []

    # 2. Refinar com pontos vazios usa o motivo como ponto
    res_ref = ResultadoAvaliacao(
        status=StatusAvaliacao.REFINAR,
        motivo="Falta detalhar o custo.",
        pontos_a_corrigir=[],
    )
    assert res_ref.status == StatusAvaliacao.REFINAR
    assert len(res_ref.pontos_a_corrigir) == 1
    assert res_ref.pontos_a_corrigir[0] == "Falta detalhar o custo."

    # 3. Rejeitado
    res_rej = ResultadoAvaliacao(
        status=StatusAvaliacao.REJEITADO,
        motivo="Inviável e fora do escopo.",
        pontos_a_corrigir=[],
    )
    assert res_rej.status == StatusAvaliacao.REJEITADO
    print("  [OK] Schemas testados com sucesso.")


def test_extrair_json():
    print("Testando extração e parsing de JSON...")
    # JSON puro
    j1 = '{"status": "aprovado", "motivo": "Tudo certo", "pontos_a_corrigir": []}'
    d1 = extrair_json_de_texto(j1)
    assert d1["status"] == "aprovado"

    # Markdown com ```json
    j2 = '```json\n{"status": "refinar", "motivo": "Melhorar SLA", "pontos_a_corrigir": ["SLA"]}\n```'
    d2 = extrair_json_de_texto(j2)
    assert d2["status"] == "refinar"

    # Texto antes e depois do JSON
    j3 = 'Olá! Segue a análise:\n{"status": "rejeitado", "motivo": "Ramo podado", "pontos_a_corrigir": []}\nAtenciosamente.'
    d3 = extrair_json_de_texto(j3)
    assert d3["status"] == "rejeitado"

    # JSON inválido dispara ValueError
    try:
        extrair_json_de_texto("Isto não é um json")
        assert False, "Deveria ter lançado ValueError"
    except ValueError:
        pass

    print("  [OK] Extração de JSON testada com sucesso.")


def test_persistencia_historico():
    print("Testando persistência e recuperação de histórico...")
    pasta_temp = Path(tempfile.mkdtemp())
    try:
        config = ConfiguracaoLoop(
            objetivo="Testar persistência do histórico",
            criterios="Salvar e ler arquivo JSON corretamente",
            gerador=ConfiguracaoAgente(provedor=Provedor.GEMINI, modelo="gemini-2.5-flash"),
            avaliador=ConfiguracaoAgente(provedor=Provedor.GROQ, modelo="llama-3.3-70b-versatile"),
            max_refinamentos=3,
        )
        registro = RegistroExecucao(
            id="test-uuid-1234",
            configuracao=config,
            iteracoes=[
                IteracaoLoop(
                    numero_iteracao=1,
                    tipo="geracao_inicial",
                    proposta="Proposta inicial de teste",
                    avaliacao=ResultadoAvaliacao(
                        status=StatusAvaliacao.APROVADO,
                        motivo="Aprovada no teste",
                    ),
                )
            ],
            proposta_final="Proposta inicial de teste",
            estado_final=StatusFinal.APROVADO,
        )

        caminho = salvar_execucao(registro, pasta_temp)
        assert caminho.exists()

        lista = listar_historico_execucoes(pasta_temp)
        assert len(lista) == 1
        assert lista[0].id == "test-uuid-1234"
        assert lista[0].estado_final == StatusFinal.APROVADO

        recuperado = carregar_execucao_por_id("test-uuid-1234", pasta_temp)
        assert recuperado is not None
        assert recuperado.configuracao.objetivo == "Testar persistência do histórico"

        print("  [OK] Persistência e leitura validadas com sucesso.")
    finally:
        shutil.rmtree(pasta_temp, ignore_errors=True)


def test_orquestracao_aprovado():
    print("Testando fluxo do loop: aprovação direta...")
    pasta_temp = Path(tempfile.mkdtemp())
    try:
        config = ConfiguracaoLoop(
            objetivo="Criar proposta de teste",
            criterios="Critérios atendidos na primeira",
            gerador=ConfiguracaoAgente(provedor=Provedor.GEMINI, modelo="gemini-2.5-flash"),
            avaliador=ConfiguracaoAgente(provedor=Provedor.OPENROUTER, modelo="meta-llama/llama-3.3-70b-instruct"),
            max_refinamentos=3,
        )

        with patch("main.obter_chave_api", return_value="chave-mock"), \
             patch("main.gerar_proposta_inicial", return_value="Proposta inicial sólida"), \
             patch("main.avaliar_proposta", return_value=ResultadoAvaliacao(
                 status=StatusAvaliacao.APROVADO,
                 motivo="Excelente na primeira tentativa."
             )):
            resultado = executar_loop(config, diretorio_resultados=pasta_temp)

            assert resultado.estado_final == StatusFinal.APROVADO
            assert len(resultado.iteracoes) == 1
            assert resultado.proposta_final == "Proposta inicial sólida"
            assert resultado.erro is None

        print("  [OK] Fluxo de aprovação validado com sucesso.")
    finally:
        shutil.rmtree(pasta_temp, ignore_errors=True)


def test_orquestracao_poda_rejeitado():
    print("Testando fluxo do loop: poda imediata quando rejeitado...")
    pasta_temp = Path(tempfile.mkdtemp())
    try:
        config = ConfiguracaoLoop(
            objetivo="Proposta com violação de critério essencial",
            criterios="Critérios que serão violados",
            gerador=ConfiguracaoAgente(provedor=Provedor.GROQ, modelo="llama-3.3-70b-versatile"),
            avaliador=ConfiguracaoAgente(provedor=Provedor.GEMINI, modelo="gemini-2.5-flash"),
            max_refinamentos=3,
        )

        with patch("main.obter_chave_api", return_value="chave-mock"), \
             patch("main.gerar_proposta_inicial", return_value="Proposta inadequada"), \
             patch("main.avaliar_proposta", return_value=ResultadoAvaliacao(
                 status=StatusAvaliacao.REJEITADO,
                 motivo="Proposta fora de conformidade. Ramo podado."
             )), \
             patch("main.refinar_proposta") as mock_refinar:
            
            resultado = executar_loop(config, diretorio_resultados=pasta_temp)

            assert resultado.estado_final == StatusFinal.REJEITADO
            assert len(resultado.iteracoes) == 1
            # Garante que NUNCA refinou ramo rejeitado
            mock_refinar.assert_not_called()

        print("  [OK] Fluxo de poda (rejeição) validado com sucesso.")
    finally:
        shutil.rmtree(pasta_temp, ignore_errors=True)


def test_orquestracao_refinamento_ate_aprovacao():
    print("Testando fluxo do loop: refinamento até aprovação...")
    pasta_temp = Path(tempfile.mkdtemp())
    try:
        config = ConfiguracaoLoop(
            objetivo="Proposta que precisa de um ajuste",
            criterios="Critérios atendidos na segunda versão",
            gerador=ConfiguracaoAgente(provedor=Provedor.OPENROUTER, modelo="deepseek/deepseek-chat"),
            avaliador=ConfiguracaoAgente(provedor=Provedor.GROQ, modelo="llama-3.3-70b-versatile"),
            max_refinamentos=3,
        )

        avaliacoes = [
            ResultadoAvaliacao(
                status=StatusAvaliacao.REFINAR,
                motivo="Falta detalhar o cronograma.",
                pontos_a_corrigir=["Adicionar cronograma de 3 meses"],
            ),
            ResultadoAvaliacao(
                status=StatusAvaliacao.APROVADO,
                motivo="Cronograma adicionado com sucesso. Aprovado!",
            ),
        ]

        with patch("main.obter_chave_api", return_value="chave-mock"), \
             patch("main.gerar_proposta_inicial", return_value="Versão 1"), \
             patch("main.refinar_proposta", return_value="Versão 2 (com cronograma)"), \
             patch("main.avaliar_proposta", side_effect=avaliacoes):

            resultado = executar_loop(config, diretorio_resultados=pasta_temp)

            assert resultado.estado_final == StatusFinal.APROVADO
            assert len(resultado.iteracoes) == 2
            assert resultado.iteracoes[0].tipo == "geracao_inicial"
            assert resultado.iteracoes[1].tipo == "refinamento"
            assert resultado.proposta_final == "Versão 2 (com cronograma)"

        print("  [OK] Fluxo de refinamento até aprovação validado com sucesso.")
    finally:
        shutil.rmtree(pasta_temp, ignore_errors=True)


def test_orquestracao_limite_atingido():
    print("Testando fluxo do loop: atingir limite de refinamentos...")
    pasta_temp = Path(tempfile.mkdtemp())
    try:
        config = ConfiguracaoLoop(
            objetivo="Proposta que nunca satisfaz o avaliador",
            criterios="Critérios muito exigentes",
            gerador=ConfiguracaoAgente(provedor=Provedor.GEMINI, modelo="gemini-2.5-flash"),
            avaliador=ConfiguracaoAgente(provedor=Provedor.GEMINI, modelo="gemini-2.5-flash"),
            max_refinamentos=2,
        )

        avaliacao_refinar = ResultadoAvaliacao(
            status=StatusAvaliacao.REFINAR,
            motivo="Ainda incompleto.",
            pontos_a_corrigir=["Continuar melhorando"],
        )

        with patch("main.obter_chave_api", return_value="chave-mock"), \
             patch("main.gerar_proposta_inicial", return_value="V1"), \
             patch("main.refinar_proposta", side_effect=["V2", "V3"]), \
             patch("main.avaliar_proposta", return_value=avaliacao_refinar):

            resultado = executar_loop(config, diretorio_resultados=pasta_temp)

            # Inicial + 2 refinamentos = 3 iterações avaliadas
            assert resultado.estado_final == StatusFinal.LIMITE_ATINGIDO
            assert len(resultado.iteracoes) == 3
            assert resultado.erro is None

        print("  [OK] Limite de refinamentos validado com sucesso.")
    finally:
        shutil.rmtree(pasta_temp, ignore_errors=True)


def test_chave_ausente_bloqueia_chamada():
    print("Testando bloqueio quando chave de API não configurada...")
    pasta_temp = Path(tempfile.mkdtemp())
    try:
        config = ConfiguracaoLoop(
            objetivo="Teste de chave ausente",
            criterios="Não deve executar sem chaves",
            gerador=ConfiguracaoAgente(provedor=Provedor.GEMINI, modelo="gemini-2.5-flash"),
            avaliador=ConfiguracaoAgente(provedor=Provedor.GROQ, modelo="llama-3.3-70b-versatile"),
            max_refinamentos=3,
        )

        with patch("main.obter_chave_api", side_effect=ChaveAPINaoConfiguradaError("Chave GEMINI não configurada")), \
             patch("main.gerar_proposta_inicial") as mock_gerar:

            resultado = executar_loop(config, diretorio_resultados=pasta_temp)

            assert resultado.estado_final == StatusFinal.ERRO
            assert "Chave GEMINI não configurada" in (resultado.erro or "")
            mock_gerar.assert_not_called()

        print("  [OK] Bloqueio de chamada sem chave de API validado com sucesso.")
    finally:
        shutil.rmtree(pasta_temp, ignore_errors=True)


if __name__ == "__main__":
    print("\nIniciando bateria de testes do projeto meu_agente_loop...\n")
    test_schemas_validacao()
    test_extrair_json()
    test_persistencia_historico()
    test_orquestracao_aprovado()
    test_orquestracao_poda_rejeitado()
    test_orquestracao_refinamento_ate_aprovacao()
    test_orquestracao_limite_atingido()
    test_chave_ausente_bloqueia_chamada()
    print("\nTODOS OS TESTES FORAM EXECUTADOS E PASSARAM COM SUCESSO!\n")
