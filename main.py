"""
Orquestrador central do loop de agentes de IA.
Controla a geração, avaliação, refinamento, poda de ramos e persistência do histórico em JSON.
Mantém a lógica de negócio desacoplada da interface e da comunicação com APIs.
"""

import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

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
    AgenteErro,
    ChaveAPINaoConfiguradaError,
    ErroChamadaAPI,
    ErroRespostaVazia,
    ErroValidacaoAvaliador,
    avaliar_proposta,
    gerar_proposta_inicial,
    obter_chave_api,
    refinar_proposta,
    verificar_status_chaves,
)

# Diretório padrão para persistência de histórico
DIRETORIO_RESULTADOS = Path(__file__).parent / "resultados"


def garantir_diretorio_resultados(diretorio: Optional[Path] = None) -> Path:
    """Garante a existência do diretório de resultados sem sobrescrever arquivos existentes."""
    pasta = diretorio or DIRETORIO_RESULTADOS
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def salvar_execucao(registro: RegistroExecucao, diretorio: Optional[Path] = None) -> Path:
    """
    Salva o registro completo da execução em formato JSON dentro de resultados/.
    Usa identificador único no nome para nunca sobrescrever execuções anteriores.
    """
    pasta = garantir_diretorio_resultados(diretorio)
    timestamp_formatado = datetime.now().strftime("%Y%m%d_%H%M%S")
    nome_arquivo = f"execucao_{timestamp_formatado}_{registro.id[:8]}.json"
    caminho = pasta / nome_arquivo

    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(registro.model_dump(), f, ensure_ascii=False, indent=2)

    return caminho


def listar_historico_execucoes(diretorio: Optional[Path] = None) -> List[RegistroExecucao]:
    """
    Carrega o histórico de todas as execuções salvas em resultados/,
    ordenadas da mais recente para a mais antiga.
    """
    pasta = garantir_diretorio_resultados(diretorio)
    arquivos = sorted(pasta.glob("execucao_*.json"), key=os.path.getmtime, reverse=True)

    registros: List[RegistroExecucao] = []
    for arq in arquivos:
        try:
            with open(arq, "r", encoding="utf-8") as f:
                dados = json.load(f)
                registro = RegistroExecucao.model_validate(dados)
                registros.append(registro)
        except Exception:
            # Ignora arquivos corrompidos ou com formato antigo para manter estabilidade
            continue
    return registros


def carregar_execucao_por_id(id_execucao: str, diretorio: Optional[Path] = None) -> Optional[RegistroExecucao]:
    """Recupera uma execução específica a partir do seu ID."""
    pasta = garantir_diretorio_resultados(diretorio)
    for arq in pasta.glob("execucao_*.json"):
        try:
            with open(arq, "r", encoding="utf-8") as f:
                dados = json.load(f)
                if dados.get("id") == id_execucao:
                    return RegistroExecucao.model_validate(dados)
        except Exception:
            continue
    return None


TipoCallback = Optional[Callable[[str, Dict[str, Any]], None]]


def executar_loop(
    config: ConfiguracaoLoop,
    callback_evento: TipoCallback = None,
    diretorio_resultados: Optional[Path] = None,
) -> RegistroExecucao:
    """
    Orquestra o ciclo completo de agentes de IA:
    1. Valida chaves necessárias antes de disparar chamadas.
    2. Gera a proposta inicial.
    3. Avalia com resposta estruturada estritamente validada.
    4. Conforme o status:
       - 'aprovado': ramo aceito, finaliza com sucesso.
       - 'rejeitado': poda imediata do ramo, finaliza sem prosseguir.
       - 'refinar': gera nova versão corrigindo pontos específicos até atingir max_refinamentos.
    5. Se atingir o limite de refinamentos sem aprovação/rejeição, estado é 'limite_atingido'.
    6. Em caso de erro irrecuperável, encerra com 'erro'.
    7. Persiste a execução em JSON em resultados/.
    """

    def notificar(evento: str, **dados: Any) -> None:
        if callback_evento:
            try:
                callback_evento(evento, dados)
            except Exception:
                pass

    id_execucao = str(uuid.uuid4())
    data_inicio = datetime.now().isoformat()

    registro = RegistroExecucao(
        id=id_execucao,
        data_hora=data_inicio,
        configuracao=config,
        iteracoes=[],
        proposta_final=None,
        estado_final=StatusFinal.ERRO,
        erro=None,
    )

    try:
        # Passo 0: Validação prévia das chaves de API necessárias
        notificar("inicio_validacao_chaves", mensagem="Verificando chaves de API necessárias...")
        obter_chave_api(config.gerador.provedor)
        obter_chave_api(config.avaliador.provedor)

        # Passo 1: Geração da proposta inicial
        notificar(
            "inicio_geracao",
            iteracao=1,
            tipo="geracao_inicial",
            mensagem="Gerando proposta inicial a partir do objetivo e critérios...",
        )
        proposta_atual = gerar_proposta_inicial(
            config=config.gerador,
            objetivo=config.objetivo,
            criterios=config.criterios,
        )

        numero_iteracao = 1
        refinamentos_realizados = 0

        # Loop de avaliação e refinamento
        while True:
            notificar(
                "inicio_avaliacao",
                iteracao=numero_iteracao,
                mensagem=f"Avaliando proposta da iteração {numero_iteracao}...",
            )

            # Avaliação com validação estruturada Pydantic e até 2 repetições
            avaliacao = avaliar_proposta(
                config=config.avaliador,
                objetivo=config.objetivo,
                criterios=config.criterios,
                proposta=proposta_atual,
            )

            # Registra iteração
            tipo_etapa = "geracao_inicial" if numero_iteracao == 1 else "refinamento"
            iteracao_atual = IteracaoLoop(
                numero_iteracao=numero_iteracao,
                tipo=tipo_etapa,
                proposta=proposta_atual,
                avaliacao=avaliacao,
                timestamp=datetime.now().isoformat(),
            )
            registro.iteracoes.append(iteracao_atual)

            notificar(
                "iteracao_concluida",
                iteracao=iteracao_atual,
                refinamentos_realizados=refinamentos_realizados,
            )

            # Verificação das decisões do avaliador
            if avaliacao.status == StatusAvaliacao.APROVADO:
                # Proposta aprovada com sucesso
                registro.proposta_final = proposta_atual
                registro.estado_final = StatusFinal.APROVADO
                notificar(
                    "finalizado_aprovado",
                    mensagem="Proposta aprovada com sucesso pelo avaliador!",
                    iteracoes=numero_iteracao,
                )
                break

            elif avaliacao.status == StatusAvaliacao.REJEITADO:
                # Ramo rejeitado e podado imediatamente
                registro.proposta_final = proposta_atual
                registro.estado_final = StatusFinal.REJEITADO
                notificar(
                    "finalizado_rejeitado",
                    mensagem="Ramo de proposta rejeitado pelo avaliador. Execução podada.",
                    motivo=avaliacao.motivo,
                )
                break

            elif avaliacao.status == StatusAvaliacao.REFINAR:
                # Verifica se ainda há créditos de refinamento
                if refinamentos_realizados >= config.max_refinamentos:
                    # Limite atingido sem aprovação nem rejeição
                    registro.proposta_final = proposta_atual
                    registro.estado_final = StatusFinal.LIMITE_ATINGIDO
                    notificar(
                        "finalizado_limite",
                        mensagem=f"Limite de {config.max_refinamentos} refinamentos atingido sem aprovação final.",
                    )
                    break

                # Prossegue para o refinamento
                refinamentos_realizados += 1
                numero_iteracao += 1

                notificar(
                    "inicio_refinamento",
                    iteracao=numero_iteracao,
                    refinamento_numero=refinamentos_realizados,
                    motivo=avaliacao.motivo,
                    pontos=avaliacao.pontos_a_corrigir,
                    mensagem=f"Iniciando refinamento #{refinamentos_realizados} com base nos pontos a corrigir...",
                )

                proposta_atual = refinar_proposta(
                    config=config.gerador,
                    objetivo=config.objetivo,
                    criterios=config.criterios,
                    proposta_anterior=proposta_atual,
                    motivo_refinamento=avaliacao.motivo,
                    pontos_a_corrigir=avaliacao.pontos_a_corrigir,
                )

    except (ChaveAPINaoConfiguradaError, ErroValidacaoAvaliador, ErroChamadaAPI, ErroRespostaVazia, AgenteErro) as e:
        registro.estado_final = StatusFinal.ERRO
        registro.erro = str(e)
        notificar("erro", mensagem=str(e))

    except Exception as e:
        registro.estado_final = StatusFinal.ERRO
        registro.erro = f"Erro inesperado durante a orquestração: {str(e)}"
        notificar("erro", mensagem=registro.erro)

    finally:
        # Salva o arquivo de histórico independentemente do desfecho
        try:
            caminho_salvo = salvar_execucao(registro, diretorio_resultados)
            notificar("execucao_salva", caminho=str(caminho_salvo))
        except Exception as e_salvar:
            notificar("erro_salvamento", mensagem=f"Não foi possível salvar o histórico: {str(e_salvar)}")

    return registro


def main_cli() -> None:
    """Execução via terminal para diagnóstico ou uso em linha de comando."""
    print("=" * 70)
    print("Orquestrador de Agentes de IA em Loop")
    print("=" * 70)

    status_chaves = verificar_status_chaves()
    print("\nStatus das chaves de API configuradas no ambiente:")
    for prov, configurada in status_chaves.items():
        marcador = "[OK] Configurada" if configurada else "[X] Não configurada"
        print(f"  - {prov.upper():12}: {marcador}")

    print("\nPara iniciar a interface gráfica interativa, execute:")
    print("  streamlit run interface.py")
    print("=" * 70)


if __name__ == "__main__":
    main_cli()
