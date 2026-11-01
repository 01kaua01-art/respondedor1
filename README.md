# 🔄 Meu Agente em Loop

Sistema autônomo e extensível em Python para executar agentes de inteligência artificial em loop fechado, contemplando **geração**, **avaliação estruturada**, **refinamento iterativo** e **poda de propostas inviáveis (branch pruning)**.

O projeto utiliza diretamente as APIs do **Google Gemini**, **OpenRouter** e **Groq**, sem depender de frameworks pesados ou serviços pagos de orquestração.

---

## 🚀 Tecnologias

- **Python 3.10+**
- **`google-genai`**: SDK oficial para integração com o Google Gemini.
- **`openai`**: Cliente compatível com a API da OpenAI para conexões com OpenRouter e Groq Cloud.
- **`pydantic`**: Validação estrita de tipos, entradas e respostas estruturadas em JSON.
- **`python-dotenv`**: Gerenciamento seguro de variáveis de ambiente a partir do arquivo `.env`.
- **`streamlit`**: Interface web local responsiva e interativa com visualização em tempo real.

---

## 📁 Estrutura do Projeto

```text
meu_agente_loop/
├── .env                  # Chaves de API (criado localmente, ignorado no Git)
├── .env.example          # Modelo de configuração sem chaves reais
├── .gitignore            # Ignora .env, ambientes virtuais, caches e histórico local
├── requirements.txt      # Dependências do projeto
├── schemas.py            # Modelos Pydantic para configurações, propostas e avaliações
├── agentes.py            # Clientes, prompts e chamadas resilientes aos provedores
├── main.py               # Orquestrador central do loop e persistência
├── interface.py          # Interface gráfica Streamlit
├── test_loop_unitario.py # Bateria de testes unitários locais
├── README.md             # Instruções e documentação completa
└── resultados/           # Histórico de execuções persistido em arquivos JSON
```

---

## ⚙️ Configuração das Chaves de API

As chaves de API são lidas a partir de variáveis de ambiente:

- `GEMINI_API_KEY` (obtenha em [Google AI Studio](https://aistudio.google.com/))
- `OPENROUTER_API_KEY` (obtenha em [OpenRouter](https://openrouter.ai/keys))
- `GROQ_API_KEY` (obtenha em [Groq Console](https://console.groq.com/keys))

### Como configurar:

1. Faça uma cópia do arquivo `.env.example` e renomeie para `.env`:
   ```bash
   cp .env.example .env
   # No Windows PowerShell:
   Copy-Item .env.example .env
   ```
2. Abra o arquivo `.env` e insira as chaves dos provedores que deseja utilizar:
   ```ini
   GEMINI_API_KEY=sua_chave_gemini_aqui
   OPENROUTER_API_KEY=sua_chave_openrouter_aqui
   GROQ_API_KEY=sua_chave_groq_aqui
   ```
3. **Segurança:** O arquivo `.env` está explicitamente incluído no `.gitignore`. Nunca envie chaves reais para o Git nem compartilhe em logs.
4. **Verificação prévia:** Se uma chave necessária para a execução não estiver configurada, o sistema bloqueia a chamada antes de iniciar e exibe uma mensagem clara.

---

## 📦 Instalação

Recomenda-se criar um ambiente virtual Python:

```bash
# 1. Navegue até o diretório do projeto
cd meu_agente_loop

# 2. Crie um ambiente virtual (opcional, mas recomendado)
python -m venv venv

# Ative o ambiente virtual:
# No Windows PowerShell:
.\venv\Scripts\Activate.ps1
# No Linux/macOS:
source venv/bin/activate

# 3. Instale as dependências
pip install -r requirements.txt
```

---

## 🖥️ Como Executar

### 1. Interface Web (Streamlit)

Para abrir a interface gráfica no navegador:

```bash
streamlit run interface.py
```

A interface permite:
- Definir o **objetivo da tarefa** e os **critérios de avaliação**.
- Selecionar independentemente o provedor e modelo do **Gerador** e do **Avaliador** (com sugestões identificadas como exemplos ou campo livre para digitar qualquer modelo).
- Definir o **número máximo de refinamentos** (predefinido como `3`).
- Acompanhar visualmente as etapas do loop em tempo real.
- Analisar a proposta gerada e a avaliação detalhada (justificativa e pontos a corrigir) de cada versão.
- Consultar o histórico completo de execuções anteriores armazenadas em `resultados/`.

### 2. Verificação via Linha de Comando (CLI)

Para verificar o status das chaves configuradas via terminal:

```bash
python main.py
```

### 3. Bateria de Testes Locais

Para validar schemas, parsing resiliente de JSON, orquestração, regras de poda e persistência:

```bash
python test_loop_unitario.py
```

---

## 🔁 Fluxo de Funcionamento e Regras de Negócio

1. **Geração Inicial:** O agente gerador produz a primeira proposta orientando-se pelo objetivo e critérios fornecidos.
2. **Avaliação Estruturada:** O agente avaliador analisa criticamente a proposta e emite uma resposta estruturada em JSON com o seguinte formato:
   ```json
   {
     "status": "aprovado" | "refinar" | "rejeitado",
     "motivo": "Justificativa textual clara da decisão",
     "pontos_a_corrigir": ["item 1", "item 2"]
   }
   ```
3. **Decisão e Roteamento:**
   - **`aprovado`**: A proposta atende plenamente aos critérios. O ramo é concluído com sucesso e a proposta final é salva.
   - **`refinar`**: O gerador recebe a justificativa e os pontos a corrigir para produzir uma nova versão revisada. O ciclo se repete.
   - **`rejeitado`**: A proposta é considerada inviável, perigosa ou irrecuperável. O ramo é **podado imediatamente** e a execução é encerrada sem refinamentos subsequentes.
4. **Controle de Limites:**
   - Se o número máximo de refinamentos for atingido sem aprovação ou rejeição, o sistema registra o estado **`limite_atingido`**, prevenindo loops infinitos e custos desnecessários.
5. **Tolerância a Falhas e Validação:**
   - Todas as respostas do avaliador são validadas via Pydantic.
   - Respostas mal formatadas são tratadas com até **2 tentativas de repetição** com feedback corretivo.
   - Respostas inválidas persistentes resultam em **`erro`** visível; **nunca há aprovação automática**.

---

## 📜 Histórico e Persistência

Todas as execuções são salvas automaticamente em `resultados/` com nomes únicos baseados em timestamp e UUID (`execucao_YYYYMMDD_HHMMSS_<id>.json`), contendo:
- Identificador único e data/hora;
- Objetivo e critérios definidos;
- Provedores e modelos utilizados;
- Histórico de todas as versões de propostas e respectivas avaliações;
- Estado final (`aprovado`, `rejeitado`, `limite_atingido` ou `erro`);
- Mensagens de erro e justificativas detalhadas.
