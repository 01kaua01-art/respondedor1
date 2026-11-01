"""
Modelos Pydantic para validação de entradas, propostas, avaliações e histórico de execuções.
"""

from datetime import datetime
from enum import Enum
from typing import List, Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


class Provedor(str, Enum):
    """Provedores de LLM suportados no projeto."""
    GEMINI = "gemini"
    OPENROUTER = "openrouter"
    GROQ = "groq"


class StatusAvaliacao(str, Enum):
    """Decisão estruturada emitida pelo agente avaliador."""
    APROVADO = "aprovado"
    REFINAR = "refinar"
    REJEITADO = "rejeitado"


class StatusFinal(str, Enum):
    """Estado final resultante da orquestração do loop."""
    APROVADO = "aprovado"
    REJEITADO = "rejeitado"
    LIMITE_ATINGIDO = "limite_atingido"
    ERRO = "erro"


class ConfiguracaoAgente(BaseModel):
    """Configuração de provedor e modelo para um agente específico."""
    provedor: Provedor
    modelo: str = Field(..., min_length=1, description="Nome ou identificador do modelo no provedor")

    @field_validator("modelo")
    @classmethod
    def validar_modelo(cls, v: str) -> str:
        v_limpo = v.strip()
        if not v_limpo:
            raise ValueError("O identificador do modelo não pode ser vazio.")
        return v_limpo


class ConfiguracaoLoop(BaseModel):
    """Parâmetros e configurações gerais da execução do loop."""
    objetivo: str = Field(..., min_length=5, description="Objetivo principal da tarefa a ser cumprido")
    criterios: str = Field(..., min_length=5, description="Critérios claros e objetivos que regem a avaliação")
    gerador: ConfiguracaoAgente
    avaliador: ConfiguracaoAgente
    max_refinamentos: int = Field(default=3, ge=1, le=20, description="Número máximo de iterações de refinamento permitidas")

    @field_validator("objetivo", "criterios")
    @classmethod
    def validar_textos(cls, v: str) -> str:
        v_limpo = v.strip()
        if len(v_limpo) < 5:
            raise ValueError("O campo deve conter ao menos 5 caracteres úteis.")
        return v_limpo


class ResultadoAvaliacao(BaseModel):
    """
    Estrutura obrigatória retornada e validada para a decisão do avaliador.
    """
    status: StatusAvaliacao = Field(..., description="Estado da avaliação: aprovado, refinar ou rejeitado")
    motivo: str = Field(..., min_length=3, description="Justificativa textual detalhada da decisão")
    pontos_a_corrigir: List[str] = Field(default_factory=list, description="Lista de pontos a corrigir se status for refinar")

    @field_validator("motivo")
    @classmethod
    def validar_motivo(cls, v: str) -> str:
        v_limpo = v.strip()
        if not v_limpo:
            raise ValueError("O motivo da avaliação não pode ser vazio.")
        return v_limpo

    @field_validator("pontos_a_corrigir")
    @classmethod
    def normalizar_pontos(cls, v: List[str]) -> List[str]:
        # Remove itens vazios ou espaços residuais
        return [item.strip() for item in v if item and item.strip()]

    @model_validator(mode="after")
    def ajustar_coerencia_status(self) -> "ResultadoAvaliacao":
        if self.status == StatusAvaliacao.APROVADO:
            # Em aprovação, não deve haver pontos de correção pendentes
            self.pontos_a_corrigir = []
        elif self.status == StatusAvaliacao.REFINAR and not self.pontos_a_corrigir:
            # Se for pedido refinamento mas o LLM não listou itens individuais,
            # cria um item a partir do motivo para fornecer feedback acionável
            self.pontos_a_corrigir = [self.motivo]
        return self


class IteracaoLoop(BaseModel):
    """Representa um ciclo completo de proposta gerada e sua respectiva avaliação."""
    numero_iteracao: int = Field(..., ge=1, description="Número sequencial da iteração no loop")
    tipo: Literal["geracao_inicial", "refinamento"] = Field(..., description="Tipo da etapa")
    proposta: str = Field(..., min_length=1, description="Texto da proposta produzida pelo gerador")
    avaliacao: ResultadoAvaliacao = Field(..., description="Avaliação estruturada retornada pelo avaliador")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat(), description="Registro de data e hora da iteração")


class RegistroExecucao(BaseModel):
    """Registro completo de uma execução para auditoria, histórico e persistência em JSON."""
    id: str = Field(..., description="Identificador único da execução (UUID)")
    data_hora: str = Field(default_factory=lambda: datetime.now().isoformat(), description="Data e hora de início")
    configuracao: ConfiguracaoLoop = Field(..., description="Configurações utilizadas na execução")
    iteracoes: List[IteracaoLoop] = Field(default_factory=list, description="Histórico de todas as versões e avaliações")
    proposta_final: Optional[str] = Field(None, description="Proposta aceita no caso de aprovação ou última versão gerada")
    estado_final: StatusFinal = Field(..., description="Estado terminal da execução")
    erro: Optional[str] = Field(None, description="Descrição do erro caso a execução tenha sido interrompida por falha")
