"""
Módulo de comunicação com provedores de IA (Gemini, OpenRouter e Groq).
Gerencia clientes, prompts, chamadas de API, tratamento de erros e validação estruturada.
"""

import json
import os
import re
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv

from schemas import (
    ConfiguracaoAgente,
    Provedor,
    ResultadoAvaliacao,
    StatusAvaliacao,
)

# Carrega variáveis do arquivo .env
load_dotenv()

# Mapeamento oficial de variáveis de ambiente por provedor
VARIAVEIS_ENV = {
    Provedor.GEMINI: "GEMINI_API_KEY",
    Provedor.OPENROUTER: "OPENROUTER_API_KEY",
    Provedor.GROQ: "GROQ_API_KEY",
}


class AgenteErro(Exception):
    """Exceção base para erros relacionados aos agentes."""
    pass


class ChaveAPINaoConfiguradaError(AgenteErro):
    """Lançada quando a chave de API requerida não está presente no ambiente."""
    pass


class ErroRespostaVazia(AgenteErro):
    """Lançada quando o modelo de IA retorna uma resposta em branco."""
    pass


class ErroChamadaAPI(AgenteErro):
    """Lançada em falhas de comunicação com a API (limite de taxa, timeout, auth, etc.)."""
    pass


class ErroValidacaoAvaliador(AgenteErro):
    """Lançada quando a resposta estruturada do avaliador permanece inválida após retentativas."""
    pass


def obter_chave_api(provedor: Provedor | str) -> str:
    """
    Obtém a chave de API correspondente ao provedor.
    Garante que a chave existe e não está em branco. Nunca exibe a chave em mensagens de erro.
    """
    # Recarrega para capturar alterações recentes no .env
    load_dotenv(override=False)

    if isinstance(provedor, str):
        try:
            provedor = Provedor(provedor)
        except ValueError:
            raise AgenteErro(f"Provedor '{provedor}' não é suportado.")

    var_nome = VARIAVEIS_ENV.get(provedor)
    if not var_nome:
        raise AgenteErro(f"Variável de ambiente desconhecida para o provedor '{provedor}'.")

    chave = os.getenv(var_nome, "").strip()
    if not chave:
        raise ChaveAPINaoConfiguradaError(
            f"A chave de API para o provedor '{provedor.value.upper()}' ({var_nome}) não está configurada. "
            f"Adicione-a ao seu arquivo .env ou às variáveis de ambiente antes de iniciar a execução."
        )
    return chave


def verificar_status_chaves() -> Dict[str, bool]:
    """
    Verifica quais provedores possuem chaves de API configuradas no ambiente.
    Retorna apenas indicadores booleanos sem expor valores sensíveis.
    """
    load_dotenv(override=False)
    return {
        prov.value: bool(os.getenv(var_name, "").strip())
        for prov, var_name in VARIAVEIS_ENV.items()
    }


def extrair_json_de_texto(texto: str) -> Dict[str, Any]:
    """
    Extrai um dicionário JSON a partir da resposta de texto do LLM.
    Suporta JSON puro, blocos markdown (```json ... ```) e objetos embutidos no texto.
    """
    texto_limpo = texto.strip()

    # Tentativa 1: parsing direto
    try:
        return json.loads(texto_limpo)
    except json.JSONDecodeError:
        pass

    # Tentativa 2: remover delimitadores de código markdown
    padrao_markdown = r"```(?:json)?\s*([\s\S]*?)\s*```"
    match_md = re.search(padrao_markdown, texto_limpo)
    if match_md:
        try:
            return json.loads(match_md.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Tentativa 3: localizar o objeto JSON mais externo delimitado por { ... }
    inicio = texto_limpo.find("{")
    fim = texto_limpo.rfind("}")
    if inicio != -1 and fim != -1 and fim > inicio:
        trecho = texto_limpo[inicio : fim + 1]
        try:
            return json.loads(trecho)
        except json.JSONDecodeError:
            pass

    raise ValueError("Nenhum formato JSON válido foi encontrado na resposta do modelo.")


def _chamar_gemini(modelo: str, prompt: str, modo_json: bool = False) -> str:
    """Executa chamada ao Gemini usando o SDK oficial google-genai."""
    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        raise AgenteErro(
            "Biblioteca 'google-genai' não encontrada. Instale os requisitos com 'pip install -r requirements.txt'."
        ) from e

    chave = obter_chave_api(Provedor.GEMINI)

    try:
        client = genai.Client(api_key=chave)
        config = None
        if modo_json:
            config = types.GenerateContentConfig(
                response_mime_type="application/json"
            )

        resposta = client.models.generate_content(
            model=modelo,
            contents=prompt,
            config=config,
        )

        texto = resposta.text if resposta and hasattr(resposta, "text") else ""
        if not texto or not texto.strip():
            raise ErroRespostaVazia("O modelo Gemini retornou uma resposta vazia.")
        return texto.strip()

    except ChaveAPINaoConfiguradaError:
        raise
    except ErroRespostaVazia:
        raise
    except Exception as e:
        msg = str(e)
        # Sanitizar eventuais chaves que a biblioteca possa refletir
        if "403" in msg or "API_KEY_INVALID" in msg:
            raise ErroChamadaAPI("Erro no Gemini: Chave de API inválida ou sem permissão para o modelo solicitado.")
        if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
            raise ErroChamadaAPI("Erro no Gemini: Limite de taxa ou cota excedida. Aguarde um momento antes de tentar novamente.")
        if "404" in msg or "NOT_FOUND" in msg:
            raise ErroChamadaAPI(f"Erro no Gemini: Modelo '{modelo}' não foi encontrado ou não está disponível para sua chave.")
        raise ErroChamadaAPI(f"Erro ao comunicar com a API do Gemini: {msg}")


def _chamar_openai_compativel(
    provedor: Provedor,
    base_url: str,
    modelo: str,
    mensagens: List[Dict[str, str]],
    modo_json: bool = False,
) -> str:
    """Executa chamada para APIs compatíveis com OpenAI (OpenRouter ou Groq)."""
    try:
        from openai import OpenAI, AuthenticationError, RateLimitError, APITimeoutError, APIConnectionError, APIError
    except ImportError as e:
        raise AgenteErro(
            "Biblioteca 'openai' não encontrada. Instale os requisitos com 'pip install -r requirements.txt'."
        ) from e

    chave = obter_chave_api(provedor)

    headers = {}
    if provedor == Provedor.OPENROUTER:
        headers = {
            "HTTP-Referer": "http://localhost:8501",
            "X-Title": "Meu Agente em Loop",
        }

    try:
        client = OpenAI(
            base_url=base_url,
            api_key=chave,
            default_headers=headers,
            timeout=90.0,
        )

        kwargs: Dict[str, Any] = {
            "model": modelo,
            "messages": mensagens,
            "temperature": 0.2 if modo_json else 0.7,
        }

        # Adiciona response_format se solicitado (suportado por modelos modernos)
        if modo_json:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            resposta = client.chat.completions.create(**kwargs)
        except APIError as api_err:
            # Caso o modelo específico não suporte o parâmetro response_format json_object,
            # tenta novamente sem esse parâmetro relyando nas instruções de prompt
            if modo_json and "response_format" in str(api_err).lower():
                kwargs.pop("response_format", None)
                resposta = client.chat.completions.create(**kwargs)
            else:
                raise

        if not resposta.choices or not resposta.choices[0].message:
            raise ErroRespostaVazia(f"O modelo retornou uma estrutura de resposta vazia no provedor {provedor.value.upper()}.")

        conteudo = resposta.choices[0].message.content or ""
        if not conteudo.strip():
            raise ErroRespostaVazia(f"O provedor {provedor.value.upper()} retornou texto vazio.")

        return conteudo.strip()

    except ChaveAPINaoConfiguradaError:
        raise
    except ErroRespostaVazia:
        raise
    except AuthenticationError:
        raise ErroChamadaAPI(
            f"Erro de autenticação no {provedor.value.upper()}: chave de API inválida ou expirada."
        )
    except RateLimitError:
        raise ErroChamadaAPI(
            f"Limite de requisições excedido (rate limit) no provedor {provedor.value.upper()}. Aguarde alguns instantes."
        )
    except APITimeoutError:
        raise ErroChamadaAPI(
            f"Tempo limite de requisição esgotado (timeout) ao consultar {provedor.value.upper()}."
        )
    except APIConnectionError:
        raise ErroChamadaAPI(
            f"Falha de conexão com a API do {provedor.value.upper()}. Verifique sua conexão com a internet."
        )
    except APIError as e:
        raise ErroChamadaAPI(f"Erro na API do {provedor.value.upper()}: {e.message if hasattr(e, 'message') else str(e)}")
    except Exception as e:
        raise ErroChamadaAPI(f"Falha inesperada na chamada ao provedor {provedor.value.upper()}: {str(e)}")


def executar_chamada_agente(
    config: ConfiguracaoAgente,
    prompt_sistema: str,
    prompt_usuario: str,
    modo_json: bool = False,
) -> str:
    """
    Roteia a chamada para o provedor adequado (Gemini, OpenRouter ou Groq).
    """
    if config.provedor == Provedor.GEMINI:
        # Gemini funciona combinando sistema e usuário no prompt de conteúdo
        prompt_completo = f"{prompt_sistema}\n\n---\n\n{prompt_usuario}"
        return _chamar_gemini(modelo=config.modelo, prompt=prompt_completo, modo_json=modo_json)

    elif config.provedor == Provedor.OPENROUTER:
        mensagens = [
            {"role": "system", "content": prompt_sistema},
            {"role": "user", "content": prompt_usuario},
        ]
        return _chamar_openai_compativel(
            provedor=Provedor.OPENROUTER,
            base_url="https://openrouter.ai/api/v1",
            modelo=config.modelo,
            mensagens=mensagens,
            modo_json=modo_json,
        )

    elif config.provedor == Provedor.GROQ:
        mensagens = [
            {"role": "system", "content": prompt_sistema},
            {"role": "user", "content": prompt_usuario},
        ]
        return _chamar_openai_compativel(
            provedor=Provedor.GROQ,
            base_url="https://api.groq.com/openai/v1",
            modelo=config.modelo,
            mensagens=mensagens,
            modo_json=modo_json,
        )

    else:
        raise AgenteErro(f"Provedor '{config.provedor}' não é reconhecido.")


def gerar_proposta_inicial(
    config: ConfiguracaoAgente,
    objetivo: str,
    criterios: str,
) -> str:
    """Gera a primeira versão da proposta a partir do objetivo e critérios."""
    prompt_sistema = (
        "Você é um agente especialista em concepção, planejamento e redação de propostas de alta qualidade. "
        "Sua meta é atender rigorosamente ao objetivo proposto e aos critérios estabelecidos pelo usuário."
    )
    prompt_usuario = (
        f"OBJETIVO DA TAREFA:\n{objetivo}\n\n"
        f"CRITÉRIOS DE AVALIAÇÃO:\n{criterios}\n\n"
        "INSTRUÇÕES:\n"
        "- Crie uma proposta inicial detalhada, fundamentada e bem estruturada.\n"
        "- Aborde cada aspecto necessário para cumprir o objetivo.\n"
        "- Apresente o texto final diretamente, com títulos claros e argumentos sólidos."
    )
    return executar_chamada_agente(
        config=config,
        prompt_sistema=prompt_sistema,
        prompt_usuario=prompt_usuario,
        modo_json=False,
    )


def refinar_proposta(
    config: ConfiguracaoAgente,
    objetivo: str,
    criterios: str,
    proposta_anterior: str,
    motivo_refinamento: str,
    pontos_a_corrigir: List[str],
) -> str:
    """Gera uma versão refinada da proposta corrigindo os pontos apontados pelo avaliador."""
    prompt_sistema = (
        "Você é um agente especialista em aprimoramento e refinamento iterativo de propostas. "
        "Sua função é incorporar as críticas do avaliador, solucionar todas as pendências identificadas "
        "e entregar uma versão substancialmente superior e completa."
    )

    pontos_formatados = "\n".join(f"- {p}" for p in pontos_a_corrigir) if pontos_a_corrigir else "- Ajustes gerais indicados no motivo."

    prompt_usuario = (
        f"OBJETIVO DA TAREFA:\n{objetivo}\n\n"
        f"CRITÉRIOS DE AVALIAÇÃO:\n{criterios}\n\n"
        f"PROPOSTA ANTERIOR:\n{proposta_anterior}\n\n"
        f"FEEDBACK DO AVALIADOR (MOTIVO DO REFINAMENTO):\n{motivo_refinamento}\n\n"
        f"PONTOS ESPECÍFICOS QUE DEVEM SER CORRIGIDOS:\n{pontos_formatados}\n\n"
        "INSTRUÇÕES DE REFINAMENTO:\n"
        "1. Corrija pontualmente todos os problemas assinalados pelo avaliador.\n"
        "2. Preserve e aprimore as partes que já estavam adequadas.\n"
        "3. Não apresente apenas as correções isoladas: entregue a PROPOSTA COMPLETA REVISADA pronta para nova avaliação."
    )

    return executar_chamada_agente(
        config=config,
        prompt_sistema=prompt_sistema,
        prompt_usuario=prompt_usuario,
        modo_json=False,
    )


def avaliar_proposta(
    config: ConfiguracaoAgente,
    objetivo: str,
    criterios: str,
    proposta: str,
    max_tentativas_validacao: int = 2,
) -> ResultadoAvaliacao:
    """
    Avalia criticamente uma proposta em relação ao objetivo e aos critérios.
    Exige resposta estruturada validada via Pydantic.
    Se a resposta for inválida, retenta até `max_tentativas_validacao` vezes (máximo de 2 repetições).
    Em caso de falha persistente, encerra com exceção sem nunca aprovar automaticamente.
    """
    prompt_sistema = (
        "Você é um avaliador rigoroso, imparcial e analítico de soluções e propostas.\n"
        "Sua responsabilidade é conferir se a proposta satisfaz plenamente os critérios e o objetivo.\n\n"
        "REGRAS DE DECISÃO:\n"
        "- 'aprovado': A proposta atende plenamente e com alto rigor a todos os critérios e ao objetivo.\n"
        "- 'refinar': A proposta tem mérito e está no rumo correto, mas faltam elementos ou há correções necessárias.\n"
        "- 'rejeitado': A proposta viola premissas essenciais, é inviável, foge completamente do escopo ou é irrecuperável (poda de ramo).\n\n"
        "FORMATO OBRIGATÓRIO DE RESPOSTA:\n"
        "Você DEVE responder exclusivamente um objeto JSON válido, sem texto antes ou depois, com o seguinte esquema:\n"
        "{\n"
        '  "status": "aprovado" | "refinar" | "rejeitado",\n'
        '  "motivo": "Justificativa clara e fundamentada da decisão tomada",\n'
        '  "pontos_a_corrigir": ["item 1", "item 2"]\n'
        "}\n\n"
        "Atenção:\n"
        "- Se o status for 'aprovado', o campo 'pontos_a_corrigir' deve ser uma lista vazia: [].\n"
        "- Se o status for 'refinar', você DEVE listar em 'pontos_a_corrigir' os pontos específicos que exigem correção.\n"
        "- Se o status for 'rejeitado', explique minuciosamente o motivo da poda em 'motivo'."
    )

    prompt_usuario = (
        f"OBJETIVO:\n{objetivo}\n\n"
        f"CRITÉRIOS DE AVALIAÇÃO:\n{criterios}\n\n"
        f"PROPOSTA A SER AVALIADA:\n{proposta}\n\n"
        "Avalie a proposta acima e retorne estritamente o JSON com 'status', 'motivo' e 'pontos_a_corrigir'."
    )

    ultimo_erro: Optional[Exception] = None
    prompt_atual = prompt_usuario

    # Total de tentativas: 1 inicial + até max_tentativas_validacao repetições (total 3 chamadas máx)
    total_tentativas = 1 + max_tentativas_validacao

    for tentativa in range(1, total_tentativas + 1):
        try:
            resposta_texto = executar_chamada_agente(
                config=config,
                prompt_sistema=prompt_sistema,
                prompt_usuario=prompt_atual,
                modo_json=True,
            )

            # Extração de JSON
            dados_dict = extrair_json_de_texto(resposta_texto)

            # Validação estrita via Pydantic
            avaliacao = ResultadoAvaliacao.model_validate(dados_dict)
            return avaliacao

        except (ValueError, json.JSONDecodeError) as e:
            ultimo_erro = e
            motivo_falha = f"Formato JSON inválido: {str(e)}"
        except Exception as e:
            # Erros de API ou chaves interrompem imediatamente
            if isinstance(e, (ChaveAPINaoConfiguradaError, ErroChamadaAPI)):
                raise
            ultimo_erro = e
            motivo_falha = f"Validação falhou: {str(e)}"

        # Se ainda houver tentativas de repetição, monta instrução de correção
        if tentativa < total_tentativas:
            prompt_atual = (
                f"{prompt_usuario}\n\n"
                f"[AVISO IMPORTANTE - TENTATIVA ANTERIOR INVÁLIDA]:\n"
                f"Sua resposta anterior não pôde ser validada. Motivo do erro: {motivo_falha}.\n"
                "Por favor, responda APENAS com o JSON estritamente válido nos campos: "
                '{"status": "aprovado"|"refinar"|"rejeitado", "motivo": "...", "pontos_a_corrigir": [...]}.'
            )

    # Esgotou tentativas sem obter resposta válida: encerra com erro visível (NUNCA aprova automaticamente)
    raise ErroValidacaoAvaliador(
        f"O avaliador não retornou uma estrutura JSON válida após {max_tentativas_validacao} repetições. "
        f"Detalhe do último erro: {str(ultimo_erro)}"
    )
