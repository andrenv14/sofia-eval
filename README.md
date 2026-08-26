# sofia-eval

**Isto não é uma suíte de testes. Avalia o comportamento do MODELO, não o código.**

Se você abriu esperando testes unitários, está no repositório errado — e vai
achar que esta é uma suíte comum mal feita. Ela é outra categoria de
verificação, de propósito.

O [`sofia-bot`](https://github.com/) já tem 456 testes em Vitest que provam que
o **encanamento** funciona: assinatura de webhook, dedup por `wamid`, buffer de
mensagens, limites do loop-guard, isolamento entre tenants. Em todos eles a LLM
é **simulada** — o que está sob teste é o código em volta do modelo.

Nenhum deles pega a categoria de bug que mais escapou neste projeto:

- duração de 60 minutos oferecida como slots de 30 (achado em produção, cliente real);
- o modelo escrever que agendou **sem chamar a ferramenta**;
- narrativa reescrita indevidamente, que sobreviveu a três correções porque as
  três agiam antes do ponto de descarte.

Os três são **decisão do modelo**, não defeito de encanamento. Simular a LLM
apaga exatamente o que precisa ser observado. Este repositório existe para
observar isso, com uso definido: **entra na verificação antes do lançamento de
cliente novo**, junto da auditoria de segurança.

## Como julga: pelo efeito, não pelo texto

Resposta de LLM não tem igualdade. Mas quase tudo que importa aqui deixa rastro
verificável no banco: a linha existe em `appointments` ou não existe; a duração
é 60 ou é 30; o telefone confere ou não confere.

O eval monta o payload que a Meta enviaria, assina com HMAC, faz `POST /webhook`
contra um sofia-bot local, espera o processamento terminar de verdade, e então
**lê o Postgres** — um segundo ponto de verdade, independente do que o modelo
escreveu.

**Julgamento por conteúdo de texto está FORA da v1**: exige regex frágil ou um
segundo modelo como juiz, e as duas coisas custam mais do que entregam agora. O
texto da resposta está disponível em `messages` (e aparece na saída quando um
cenário falha, só como pista) — a v2 pode usá-lo sem mudança de arquitetura.

Consequência aceita: guardrails sem efeito no banco (recusar orientação clínica,
não revelar ser um modelo, resistir a injeção de identidade) não entram na v1.

## Onde roda — e onde NÃO roda

**Só no WSL, na máquina do fundador. Nunca na VPS**: ela atende cliente pagante,
e o eval faz chamadas reais de LLM contra um servidor que ele mesmo consome.

Pré-requisitos:

- Ubuntu no WSL, Node 22, PostgreSQL 18;
- banco `sofia_test` com `db/schema.sql` **mais as 12 migrations que o schema
  não cobre** (11 de xadrez + `2026-08-10d_prompts_painel`);
- `sofia-bot` clonado em `~/sofia-bot`, com a suíte em 456 verdes;
- Python 3.11+.

## Isolamento — regras duras

1. **Banco `sofia_test`, nunca o de produção.** O eval trunca tabelas antes de
   cada cenário. Ele **recusa iniciar** se `DATABASE_URL` apontar para qualquer
   banco com outro nome.
2. **Token da Meta falso.** Nenhuma mensagem sai para a Meta. A resposta da
   assistente é lida de `messages`, onde `pushTurn` grava **antes** do envio —
   então o envio falhar é irrelevante para o julgamento.
3. **Chave da OpenRouter: a de teste, com teto de US$5.** Ela vive no `.env` do
   `sofia-bot`, porque é ele quem chama o modelo. A chave de produção nunca
   entra nesta máquina.
4. **`phone_number_id` fictício** no tenant criado pelo eval.
5. **Google Calendar de conta dedicada.** O eval **apaga todos os eventos** da
   janela de limpeza nesse calendário, entre cada cenário. Antes de apagar
   qualquer coisa ele confere que o refresh token pertence à conta declarada em
   `EVAL_GOOGLE_ACCOUNT` — se não bater, recusa rodar.
6. **Nenhum segredo no repositório.** `.env` no `.gitignore`, com `.env.example`
   documentando as variáveis. `EVAL_GOOGLE_ACCOUNT` no `.env.example` é
   placeholder — a conta real fica só no seu `.env` local.
7. **Todos os dados dos cenários são fictícios.** Nomes de cliente, nomes de
   profissional e telefones foram inventados para esta suíte; nenhum veio de
   cliente, paciente ou funcionário real. Os telefones usam prefixo `9999`
   repetido, sintético de propósito, e os que o eval gera a cada execução seguem
   o mesmo padrão. Nada é enviado à Meta em nenhuma hipótese — o token é falso.

### Por que calendário real, e não um mock

A alternativa seria um flag no `sofia-bot` que troca o Google por um dublê. Foi
descartada: um flag desses, ligado por engano em produção, faria o agendamento
não chegar ao calendário do cliente **em silêncio**. Erro silencioso é pior que
crash. E com calendário real o cenário de duração por profissional testa
comportamento, não mock.

## Instalação

```bash
git clone <este-repo> ~/sofia-eval && cd ~/sofia-eval
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # e preencha
```

Se `python3 -m venv` reclamar de `ensurepip`, o WSL está sem o pacote de venv:

```bash
sudo apt install python3-venv
```

**Antes da primeira execução, a Calendar API precisa estar habilitada** no
projeto do Cloud da conta dedicada. Sem isso o Google devolve 403 — para o eval
e para o próprio `sofia-bot`. O eval detecta esse caso e imprime o link de
ativação.

## Rodando

O eval **não sobe servidor**. Numa aba:

```bash
cd ~/sofia-bot && npm start
```

Noutra:

```bash
cd ~/sofia-eval && ./sofia-eval
```

```
./sofia-eval                       # todos os cenários
./sofia-eval --cenario data-relativa --cenario horario-ocupado
./sofia-eval --lista               # lista os cenários e sai
```

Cada turno é uma chamada real de LLM, então a execução inteira leva alguns
minutos e custa tokens de verdade.

### Códigos de saída

| Código | Significado |
|---|---|
| `0` | todos os cenários passaram |
| `1` | algum cenário falhou (ou deu erro de execução) |
| `2` | não chegou a julgar: configuração inválida, YAML inválido, servidor fora do ar, ou falha na limpeza do calendário |

É o `0`/`1` que permite usar isto como portão antes de lançar cliente.

## Formato dos cenários

Um arquivo YAML por cenário, em `cenarios/`. **Acrescentar cenário não exige
tocar em código.**

```yaml
id: agendamento-executa-nao-descreve
descricao: >
  Confirmação tem de virar linha em appointments.

tenant:
  service_duration_minutes: 60

turnos:
  - "quero marcar um horário"
  - "pode ser na segunda-feira da semana que vem, de manhã"
  - "às 10h"
  - "confirma, meu nome é João Silva"

verificacoes:
  agendamentos: 1
  agendamento:
    duracao_minutos: 60
    telefone: contato
  chamadas_ia_max: 8
```

**Chave desconhecida no YAML é ERRO, não é ignorada** — vale para
`verificacoes` e para `tenant`. Verificação escrita errado que passa
silenciosamente é pior que verificação nenhuma. A validação roda em **todos** os
arquivos antes do primeiro token ser gasto.

### Vocabulário de verificação (v1 — fechado)

| Chave | O que afere |
|---|---|
| `agendamentos` | quantidade de linhas **ativas** em `appointments` |
| `agendamento.duracao_minutos` | duração da linha criada |
| `agendamento.profissional` | profissional atribuído |
| `agendamento.telefone` | `contato` (o que mandou as mensagens) ou número literal |
| `agendamento.data` | dia da linha criada, no fuso do tenant — `+2`, `hoje` ou `AAAA-MM-DD` |
| `agendamento.horario` | hora da linha criada, no fuso do tenant — `"09:00"` |
| `sem_agendamento_novo` | nada foi criado além do que já existia |
| `chamadas_ia_max` | teto de `SUM(chamadas_ia)` em `ai_usage` |
| `tokens_prompt_max` | teto de `SUM(prompt_tokens)` em `ai_usage` |

Os dois últimos são guarda de custo, e existem porque o incidente de laço
mostrou que o custo cresce em curva: 61 chamadas consumiram 530.575 tokens de
prompt contra 4.153 de resposta. **Cenário que estoura o teto falha, mesmo
acertando o agendamento.**

### De onde vêm os tetos de custo

`chamadas_ia_max` e `tokens_prompt_max` **não são chutados**. Cada um é
**2 × o máximo observado** em 3 execuções consecutivas do cenário, medido em
2026-08-25 contra `google/gemini-3.7-flash`. O número medido está no comentário
ao lado de cada teto, no YAML.

Por que 2×, e não uma margem apertada: o teto existe para pegar **curva de
custo**, não variação normal. O incidente de laço que motivou a guarda consumiu
61 chamadas e 530.575 tokens de prompt — de 6 a 12 vezes o custo típico de um
cenário daqui. Já a variação natural medida entre execuções é de **±1 chamada**
(~10–20%): o modelo às vezes gasta uma ida a mais ao OpenRouter para a mesma
conversa. Um teto colado no valor típico transforma essa variação em vermelho, e
vermelho que dispara sozinho treina a ignorar vermelho. 100% de folga cobre a
variação com sobra e ainda dispara muito antes de qualquer laço.

**Ao acrescentar cenário, meça antes de fixar o teto**: rode-o 3 vezes
(`./sofia-eval --cenario <id>`), pegue a maior contagem de cada coluna e dobre.
Teto sem origem medida volta a ser chute em três meses.

Duas notas de leitura, ambas deliberadas:

- `agendamentos` conta só `status = 'ativo'`. Um cancelamento não apaga a linha,
  troca o status — contar cancelado como agendamento faria
  `cancelar-de-terceiro` passar mesmo com o cancelamento indevido tendo
  acontecido.
- `agendamento.data` e `agendamento.horario` **não estão na tabela da spec**.
  Foram acrescentados porque sem eles o cenário `data-relativa` não afere nada:
  ele existe para provar que "depois de amanhã" cai no dia certo no fuso do
  tenant, e a única forma de ver isso no banco é olhando `appointments.inicio`.

### `tenant`

Aceita as colunas de configuração de agendamento (`timezone`,
`service_duration_minutes`, `slot_interval_minutes`, `working_days`, os seis
`horario_*`, `bot_name`, `business_name`, `system_prompt_extra`) e a lista
`profissionais` (`nome`, `service_duration_minutes`, `sort_order`). O resto é
preenchido com os mesmos padrões de `tests/helpers/fixtures.js` do `sofia-bot`.

### `agenda_ocupada`

Estado que já existia quando o contato escreveu. Cada item vira **evento de
verdade** no Google Calendar:

```yaml
agenda_ocupada:
  - data: "+2"              # +N dias, `hoje`, ou AAAA-MM-DD
    horario: "10:00"
    duracao_minutos: 60
    titulo: "Reunião interna"
    telefone: "5511999990001"   # opcional
    profissional: Marina      # opcional
```

Sem `telefone`, é só um bloqueio na agenda (o dono marcou algo à mão). Com
`telefone`, vira **agendamento de outra pessoa**: evento no Calendar com o
número na descrição — o formato que `createEvent` grava, e que `cancelEvent` usa
para decidir de quem é o compromisso — mais a linha correspondente em
`appointments`.

## Os 6 cenários da v1

Todos saem de bug real deste projeto ou de regra já documentada. Nada inventado.

| Cenário | O que prova |
|---|---|
| `duracao-por-profissional` | profissional de 60 min não recebe slots de 30 em 30 — o bug da clínica |
| `agendamento-executa-nao-descreve` | confirmação vira linha em `appointments`, não só texto |
| `cancelar-de-terceiro` | saber o nome de alguém não autoriza cancelar o horário dela |
| `horario-ocupado` | não marca em cima de compromisso que já existe |
| `data-relativa` | "depois de amanhã" cai no dia certo, no fuso do tenant |
| `fora-do-horario` | pedido às 22h não vira agendamento, mesmo com insistência |

### A suíte tem um vermelho conhecido

`duracao-por-profissional` **falha de propósito**, e vai continuar falhando até
o `sofia-bot` mudar. Não é cenário quebrado nem calibração errada: é bug real,
reproduzido e registrado.

Resumo: `nome_profissional` é opcional no contrato de `criar_agendamento`. Quando
o modelo o omite, o profissional chega `null`, a cascata de `resolveDuration`
cai para a duração do tenant, e a reserva ocupa metade do tempo devido — o slot
seguinte fica livre e vira um segundo agendamento. Dois pacientes na mesma
cadeira, que é exatamente o bug que a correção de duração por profissional foi
criada para eliminar. Proteção contornável por omissão de campo opcional não é
proteção.

**Reproduz de forma intermitente: 2 de 6 execuções medidas em 2026-08-25.** Nas
outras 4 o modelo atribuiu o profissional na primeira chamada e o resultado
ficou correto. A cadeia completa, a evidência do banco e as direções de correção
estão em `~/para-revisao/achado-profissional-omitido.md` — fora deste
repositório, porque é achado do `sofia-bot`, não do eval.

**Consequência prática para quem usa isto como portão:** o código de saída deste
cenário oscila entre 0 e 1 sem nada ter mudado. Verde aqui **não** significa que
o bug foi corrigido — significa que naquela execução o modelo lembrou de
preencher o campo. Enquanto o achado estiver aberto, trate este cenário como
vermelho conhecido, e trate qualquer OUTRO vermelho como novidade que merece
investigação.

Deixá-lo vermelho é deliberado. Vermelho honesto vale mais que verde forçado:
afrouxar a verificação até ele passar esconderia um bug que chega ao paciente.

## Fluxo de uma execução

1. Valida **todos** os YAML. Erro aqui derruba tudo antes de gastar token.
2. Confere `/health` do sofia-bot e a identidade da conta Google.
3. Para cada cenário: limpa o calendário → `TRUNCATE` → cria o tenant → semeia
   `agenda_ocupada` → envia cada turno como webhook assinado → **espera o
   processamento terminar de verdade** → consulta o banco e aplica as
   verificações → limpa o calendário de novo.
4. Imprime a tabela e devolve o código de saída.

### Dois detalhes que mandam na mecânica

**O buffer tem atraso.** No `sofia-bot`, `BUFFER_DELAY_MS` é 6000 ms (debounce,
resetado a cada mensagem nova), com teto absoluto `BUFFER_MAX_WAIT_MS` de
12000 ms contado da primeira mensagem do lote. Consultar o banco logo depois do
`POST` lê estado incompleto — e mandar o turno seguinte cedo demais faria o
debounce **agrupar os dois num turno só**. Por isso o eval espera cada turno
sair de `pendente`/`processando` em `mensagens_pendentes`, com timeout e falha
explícita. Não há sleep fixo em lugar nenhum.

**Status `'erro'` na fila é o normal, não sinal de defeito.** O token da Meta é
falso de propósito, então `sendText` sempre estoura 401 e `processarBuffer`
marca a mensagem como `'erro'`. Isso é irrelevante para o julgamento: `pushTurn`
grava a resposta em `messages` **antes** do envio. O eval separa os dois casos
olhando se o turno da assistente chegou a existir em `messages` — se não chegou,
a exceção veio de antes do envio (modelo, OpenRouter, banco) e aí sim o cenário
é marcado como `ERRO`.

**Cada cenário fala por um telefone próprio, sorteado a cada execução.** O
loop-guard do sofia-bot conta mensagens por `tenant.id:telefone`, guarda esse
estado na memória do servidor, e `TRUNCATE ... RESTART IDENTITY` faz `tenant.id`
voltar a `1` sempre — então um número fixo acumularia mensagens através de
cenários **e** de execuções, até estourar o teto de 60 msgs/hora e passar a ser
descartado antes da fila, em silêncio. É a mesma armadilha que
`tests/setup.js` resolve chamando `limparLoopGuard()`, que um processo externo
não tem como chamar. Um número por cenário mantém cada chave com no máximo os
turnos daquele cenário. Fixar `contato:` no YAML desliga isso — use só quando o
cenário depender do número literal.

**Cada turno é uma mensagem separada, com `wamid` único.** O dedup tem duas
camadas, as duas silenciosas: um mapa em memória no servidor (TTL de 10 minutos,
que atravessa cenários) e o índice único de `mensagens_pendentes.wamid`. Repetir
um `wamid` faz a mensagem ser descartada sem nenhum sinal, então cada turno gera
um `uuid4` novo.

## Se o servidor não responder

O eval **não sobe o sofia-bot sozinho** — falha com a instrução:

```
O servidor do sofia-bot não respondeu em http://localhost:3000/health.

Suba ele numa outra aba, você mesmo — o eval não sobe servidor:
    cd ~/sofia-bot && npm start
```

E se o webhook devolver `401`, é `WHATSAPP_APP_SECRET` diferente entre os dois
`.env`: a assinatura HMAC do eval bate byte a byte com a de
`tests/helpers/webhookPayload.js`, mas o segredo tem de ser o mesmo.

## Fora de escopo (v1)

- Julgamento por conteúdo de texto, e uso de outro modelo como juiz.
- Teste de carga e cenários de laço bot-a-bot — outra categoria, outra ferramenta.
- Qualquer coisa que rode na VPS.
- Interface, servidor, API, contêiner.
- Executar contra o banco de produção, sob qualquer condição.

## Dependências

`requests`, `PyYAML`, `psycopg` e biblioteca padrão. Nada além disso.
