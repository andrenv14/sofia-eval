# sofia-eval

**Isto não é uma suíte de testes. Avalia o comportamento do MODELO, não o código.**

Se você abriu esperando testes unitários, está no repositório errado — e vai
achar que esta é uma suíte comum mal feita. Ela é outra categoria de
verificação, de propósito.

O `sofia-bot` (código privado — a arquitetura e os trechos que valem leitura
estão no repositório irmão
[`sofia-vitrine`](https://github.com/andrenv14/sofia-vitrine)) tem 368 testes
em Vitest que provam que o **encanamento** funciona: assinatura de webhook,
dedup por `wamid`, buffer de mensagens, limites do loop-guard, fila
persistente. Em todos eles a LLM
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
  não cobre** (11 de um módulo desativado + `2026-08-10d_prompts_painel`);
- `sofia-bot` clonado em `~/sofia-bot`, com a suíte verde;
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

### Evidência de falha

Cenário com veredito `FALHOU` ou `ERRO` tem o estado do banco salvo em
`~/para-revisao/eval-<cenario>-<timestamp>.json` **antes de qualquer limpeza** —
o `TRUNCATE` da entrada do cenário seguinte é autocommit, e sem isto não sobra
nada para inspecionar. Vão para o arquivo as linhas de `appointments` e
`professionals` do tenant, o histórico de `messages` em ordem cronológica e o
`ai_usage` agregado. O relatório imprime o caminho junto do motivo da falha.

Cenário que passa **não escreve nada**. Falha na captura só avisa e segue: não
muda veredito nem código de saída. O arquivo **não carrega credencial nenhuma** —
as consultas listam coluna a coluna, nunca `SELECT *`, então
`professionals.google_refresh_token` e a linha de `tenants` (que guarda
`whatsapp_access_token`, `google_client_secret` e `google_refresh_token`) ficam
de fora.

> **Não commite esses JSON.** Eles contêm o conteúdo das conversas do cenário
> — conversa de terceiro não entra em repositório, seja ele público ou
> privado (a visibilidade deste já mudou uma vez, em 03/09/2026). Por isso o
> destino é `~/para-revisao/`, fora do repositório — a mesma pasta dos achados
> que não moram aqui.

## Relatório HTML

Toda execução escreve, além da tabela, um arquivo HTML estático e
auto-contido em `~/para-revisao/relatorio-eval-<timestamp>.html` — mesma
pasta e mesmo motivo do JSON de evidência acima: o relatório carrega o
conteúdo das conversas, que não entra em repositório em nenhuma
visibilidade. **Não commite esse arquivo.**

Isto é o degrau 1 do item 8 de `docs/contexto/fila.md` no `sofia-bot`
("saída visual do eval"): a tabela do terminal só mostra o veredito, e o eval
julga pelo efeito no banco — não se vê o que a Sofia respondeu, mesmo com o
texto gravado em `messages`. O relatório é **arquivo escrito em disco**, não
servidor nem interface: zero dependência nova (só `html.escape` e f-strings
da biblioteca padrão), zero JS obrigatório, e não muda o julgamento de
nenhum cenário — só lê o que a tabela do terminal já calculou.

Por cenário, o relatório traz: id e descrição; a conversa turno a turno
(cliente × Sofia, lida de `messages`); cada verificação com esperado ×
obtido × veredito, **inclusive as que passaram** (a tabela do terminal só
grava as que falham); chamadas de IA e tokens; e o veredito. No topo: SHA do
`sofia-eval` e do `sofia-bot`, data/hora, modelo(s) e os totais da rodada.

**Ferramentas chamadas ficam fora desta v1 do relatório.** O banco não
guarda essa informação — `pushTurn` grava só as mensagens finais de
user/assistant, `ai_usage` não tem coluna de nome de ferramenta, e a lista
existe apenas em memória durante o processamento no `sofia-bot`. Sem fonte
no banco, o relatório diz isso explicitamente em vez de inventar uma.

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

### Vocabulário de verificação (fechado)

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
| `respostas_assistente_max` | teto de linhas `messages` com `role='assistant'`, do contato do cenário |
| `agendamento_status` | estado (`ativo`/`cancelado`) de uma linha **específica**, por `telefone`+`data`+`horario` |

Os dois últimos da tabela original (`chamadas_ia_max`, `tokens_prompt_max`) são
guarda de **custo**, e existem porque o incidente de laço mostrou que o custo
cresce em curva: 61 chamadas consumiram 530.575 tokens de prompt contra 4.153
de resposta. **Cenário que estoura o teto falha, mesmo acertando o
agendamento.**

`respostas_assistente_max` é guarda de **comportamento** (desengajar), não de
custo — entrou na leva 2 para o cenário `bot-a-bot-desengajar` (ver abaixo). É
lista, não igualdade: "desengajar" é "no máximo N respostas", nunca zero (o
primeiro turno legítimo já grava uma).

`agendamento_status` também entrou na leva 2, para cenários com **mais de uma
linha em jogo** (ex.: `cancelamento-correto`, que precisa provar que a linha
CERTA virou `cancelado`, e não só que o total caiu — `agendamentos` sozinho não
distingue as duas coisas). Falha alta por desenho: 0 linhas encontradas para o
`telefone`+`data`+`horario` pedido é FALHA (não passa em branco), e mais de 1
também é FALHA, por ambiguidade — nunca escolhe uma linha sozinho.

### De onde vêm os tetos de custo

`chamadas_ia_max` e `tokens_prompt_max` **não são chutados**. Cada um é
**2 × o máximo observado** em 3 execuções consecutivas do cenário, contra
`google/gemini-3.7-flash`. Cinco cenários foram medidos em 2026-08-25;
`duracao-por-profissional` é o único com base posterior, remedido em
2026-08-28. A data e os números de cada medição estão no comentário ao lado do
teto correspondente, no YAML — é lá que se confere de onde veio, não aqui.

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

Cada profissional aceita ainda `ativo` (booleano, padrão `true`), `grade`
(regra recorrente semanal) e `excecoes` (pontuais). `ativo: false` cria o
profissional cadastrado mas **fora da oferta** — `carregarProfissionaisAtivos`
(`sofia-bot`, `professionals/professionals.js`) filtra por `active`, e a
diferença importa: um tenant com "4 cadastrados, 3 agendáveis" não é o mesmo
negócio que um com 4 agendáveis, e escrever 4 mede outra coisa.

`grade` e `excecoes` semeiam `professional_availability` e
`professional_availability_exceptions`:

```yaml
profissionais:
  - nome: Helena
    grade:
      - dia_semana: 1          # 0=domingo .. 6=sábado, igual a working_days
        hora_inicio: "08:00"
        hora_fim: "18:00"
    excecoes:
      - data: "+2"             # +N, hoje, ou AAAA-MM-DD — sempre entre aspas
        hora_inicio: "09:00"
        hora_fim: "10:00"
        tipo: bloqueio         # bloqueio | liberacao
```

Três coisas que mordem, todas conferidas na validação antes de gastar token:

- **Semear QUALQUER linha liga a restrição.** `temGradeConfigurada`
  (`sofia-bot`, `availability.js`) só olha se existe alguma linha, em qualquer
  das duas tabelas. Uma regra só de segunda deixa o profissional indisponível
  de terça a domingo. Profissional sem grade nenhuma segue irrestrito — é o
  caso de todos os cenários que não usam estas chaves.
- **Exceção vence regra recorrente**, sempre: `bloqueio` que sobrepõe o slot
  fecha; `liberacao` que cobre o slot inteiro abre, mesmo sem regra nenhuma
  naquele dia.
- **Data relativa precisa de aspas.** `data: +2` sem aspas o YAML lê como o
  número 2, que não é data nenhuma — a validação recusa, nomeando arquivo e
  índice. Já `data: -2` sem aspas **passa**, porque vira o número -2 e o texto
  `-2` é uma data relativa válida (dois dias atrás). A armadilha é
  assimétrica, e a validação não tem como desfazer isso: quando o YAML chega
  aqui, `+2` já virou `2` e a informação do sinal se perdeu. Escreva sempre
  entre aspas.

`openrouter_model` (opcional) declara o modelo sob avaliação **no cenário**, em
vez de deixá-lo implícito no `.env` do `sofia-bot`. Sem declarar, o tenant nasce
com a coluna NULA e herda o `OPENROUTER_MODEL` de lá — mesmo comportamento de
sempre. Declare quando o cenário tiver teto (`chamadas_ia_max`,
`tokens_prompt_max`) calibrado contra um modelo específico: o modelo que
efetivamente respondeu sempre aparece no relatório HTML (cabeçalho "modelo(s)"
e por cenário), lido de `ai_usage.model` — o que o servidor gravou, não o que a
config dizia.

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

## Os cenários

Todos saem de bug real deste projeto, de regra já documentada, ou de achado de
tráfego real. Nada inventado.

### Os 6 da v1 — tetos medidos, 3 passadas cada

| Cenário | O que prova |
|---|---|
| `duracao-por-profissional` | profissional de 60 min não recebe slots de 30 em 30 — o bug da clínica |
| `agendamento-executa-nao-descreve` | confirmação vira linha em `appointments`, não só texto |
| `cancelar-de-terceiro` | saber o nome de alguém não autoriza cancelar o horário dela |
| `horario-ocupado` | não marca em cima de compromisso que já existe |
| `data-relativa` | "depois de amanhã" cai no dia certo, no fuso do tenant |
| `fora-do-horario` | pedido às 22h não vira agendamento, mesmo com insistência |

### Leva 2 — vocabulário fechado, tetos de custo AINDA NÃO medidos

Os nove cenários abaixo passam na validação de esquema (`--lista`) e usam só
chaves já existentes ou as duas novas desta leva (`respostas_assistente_max`,
`agendamento_status`). Nenhum deles fixou `chamadas_ia_max`/`tokens_prompt_max`
ainda — falta a passada de 3 execuções que a seção "De onde vêm os tetos de
custo" exige, e falta primeiro a guarda em torno de `completion.choices[0]`
entrar no `sofia-bot` (achado de 02/09 — `openrouter.js`, `handleUserMessage`; ver `AGENTS.md`) — calibrar contra um
bug de infra gastaria token medindo o número errado.

| Cenário | O que prova |
|---|---|
| `remarcacao` | trocar de horário cancela o antigo em vez de duplicar a linha |
| `cancelamento-correto` | cancela o agendamento do contato sem tocar no de outra pessoa marcado no mesmo dia |
| `horario-de-outra-pessoa` | não marca em cima de um agendamento real de outra pessoa (variante de `horario-ocupado` com dono) |
| `bot-a-bot-desengajar` | detectado robô de menu de outra empresa, responde uma vez e para — achado de tráfego real de 31/08, nasce vermelho até o prompt do `sofia-bot` mudar |
| `duplicidade` | pedir o mesmo horário duas vezes não vira duas linhas, robusto ao caminho que o modelo escolher |
| `configuracao-multiprofissional` | com 4 profissionais no ar, o nome resolve pro profissional certo e pra duração DELE (dados fictícios, anonimizado) |
| `precisa-verificar-novamente` | modelo não inventa confirmação em cima do "tenta de novo" ambíguo de `createEvent` — intermitente por natureza, ver descrição do YAML |
| `grade-do-profissional` | a grade do profissional vale, e exceção pontual vence a regra recorrente — guarda de regressão, nasce VERDE de propósito |
| `agenda-unica-um-por-vez` | com agenda única, dois profissionais não podem ocupar o mesmo horário — e a proteção mora no Google, não no sistema. Pré-requisito de lançamento (dados fictícios, anonimizado) |

**Sobre o item `grade ignorada sem profissional` da fila:** virou
`grade-do-profissional`, e mudou de natureza no caminho. O bug original
(profissional não resolvido ⇒ grade ignorada) parece **fechado**:
`resolverProfissionalObrigatorio` (`sofia-bot`, `src/ai/tools.js`) recusa a
ferramenta de agenda quando falta `nome_profissional` em tenant com
profissional cadastrado, então `professional` nulo só sobra em tenant SEM
profissional nenhum — onde ignorar a grade é o comportamento correto, não o
bug. Isso é leitura de código, não veredito, e a fila mantém o item aberto até
a investigação original ser reaberta. O cenário entrou pelo lado que ainda dá
para provar: que a grade É consultada quando o profissional está resolvido, e
que exceção vence regra recorrente. Nasce verde — guarda de regressão, não
achado.

### O vermelho conhecido fechou — e virou regressão

`duracao-por-profissional` **passava a ser vermelho de propósito**. Não é mais:
o bug que ele demonstrava foi corrigido, e desde 2026-08-28 o cenário passa.
Ele muda de papel — deixa de exibir um bug aberto e passa a **guardar um bug
fechado contra volta**.

O bug era este: `nome_profissional` era opcional no contrato de
`criar_agendamento`. Quando o modelo o omitia, o profissional chegava `null`, a
cascata de `resolveDuration` caía para a duração do tenant, e a reserva ocupava
metade do tempo devido — o slot seguinte ficava livre e virava um segundo
agendamento. Dois pacientes na mesma cadeira, que é exatamente o bug que a
correção de duração por profissional foi criada para eliminar. Proteção
contornável por omissão de campo opcional não era proteção.

Reproduzia de forma intermitente: **2 de 6 execuções medidas em 2026-08-25** —
nas outras 4 o modelo atribuía o profissional por conta própria e o resultado
saía correto. Essa intermitência é o motivo de a correção não ter sido
confiada ao modelo.

**O que fechou:** as quatro camadas de
`docs/features/nome-profissional-obrigatorio.md`, no `sofia-bot`, em produção
desde 2026-08-28 (merge `cac042b`). As camadas 1 e 2 instruem, a 3 valida no
`executeTool`, e a 4 é uma trigger `BEFORE INSERT` em `appointments` que recusa
`professional_id` nulo em tenant com profissional ativo — a única que não
depende de o modelo cooperar. A cadeia completa e a evidência do banco estão em
`~/para-revisao/achado-profissional-omitido.md`, fora deste repositório, porque
é achado do `sofia-bot`, não do eval.

**Consequência prática para quem usa isto como portão:** o cenário agora é
verde estável, e não mais um código de saída que oscila entre 0 e 1 sem nada ter
mudado. Vermelho aqui voltou a significar o que significa nos outros cinco —
**novidade que merece investigação**. Não há mais exceção a memorizar ao ler a
tabela.

O que continua valendo do princípio antigo: vermelho honesto vale mais que verde
forçado. O cenário ficou verde porque o `sofia-bot` mudou, não porque a
verificação foi afrouxada — `agendamentos: 1` e `duracao_minutos: 60` seguem
exatamente como estavam quando o cenário falhava.

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

## Fora de escopo

- Julgamento por conteúdo de texto, e uso de outro modelo como juiz. Decisão do
  fundador mantida na leva 2: falso negativo de regex é silencioso (parafraseia
  e o cenário passa sem provar nada) e modelo juiz é o mesmo tipo de artefato
  sob teste — vira decisão própria, não implícita numa leva de cenários. Em
  consequência, `capacidade inventada` (achado de tráfego real de 01/09: a
  Sofia oferece encaminhar recado e escrever texto — nenhuma ferramenta faz
  isso, então zero rastro no banco) fica **bloqueado até a v2**.
- Teste de carga — simular volume para estressar o loop-guard é outra
  categoria, outra ferramenta. **Não inclui** verificar que a Sofia se
  desengaja de um robô de menu (`bot-a-bot-desengajar`, leva 2): isso é
  comportamento de MODELO em 4 turnos determinísticos, aferido por
  `respostas_assistente_max` — não é laço nem carga.
- Qualquer coisa que rode na VPS.
- Servidor, API, contêiner. (O relatório HTML é **arquivo escrito em disco**,
  não interface — não contraria este limite. Ver "Relatório HTML" acima.)
- Executar contra o banco de produção, sob qualquer condição.
- Ferramentas chamadas pelo modelo, no relatório HTML — o banco não guarda
  essa informação (ver "Relatório HTML" acima).

## Dependências

`requests`, `PyYAML`, `psycopg` e biblioteca padrão. Nada além disso — o
relatório HTML usa só `html.escape` e f-strings da biblioteca padrão.
