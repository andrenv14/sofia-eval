---
name: passada
description: Sequência completa de uma passada do sofia-eval — trava de recurso, subida do sofia-bot com o modelo declarado, execução, leitura do relatório e teardown. Use ao rodar cenários, medir teto, ou reproduzir um achado de comportamento.
---

# Rodar uma passada do sofia-eval

O eval **não sobe servidor** e **não escolhe modelo** — as duas coisas são
passos humanos, e é por isso que esta sequência existe: ela estava espalhada
pelo README, e em 02/09 duas rodadas se perderam por causa dela (uma medindo o
modelo errado sem ninguém notar, outra descobrindo credencial vencida só
depois de subir o servidor).

## 0. Antes de tudo: a janela é de outra sessão também

A suíte Vitest do `sofia-bot` e este eval dividem o `sofia_test`. Rodar os dois
juntos deu 4 ERROs por contenção em 29/08.

```bash
pgrep -af "[v]itest|node [s]rc/server.js"                       # processos

# A URL vive no .env do sofia-bot, não no ambiente desta shell:
DBURL=$(grep -E '^DATABASE_URL=' ~/sofia-bot/.env | cut -d= -f2- | tr -d "\"' \r")
psql "$DBURL" -At -c "select count(*) from pg_stat_activity
  where datname='sofia_test' and pid <> pg_backend_pid();"      # o recurso
```

**Os colchetes não são firula, e a versão sem eles mentia.** `pgrep -fa
"vitest|node src/server.js"` casa a PRÓPRIA linha de comando do shell que o
executa — o padrão está dentro dela. Ele devolve um processo com a janela
livre, e quem lê conclui "ocupado". Em 03/09 pegou duas sessões no mesmo dia,
esta e a da VPS. `[v]itest` casa a string `vitest`, mas a linha do shell contém
`[v]itest` com os colchetes, que o regex não casa: o auto-casamento some.

**E processo é PROXY; o recurso disputado é o BANCO.** A segunda linha responde
"alguém está conectado no `sofia_test`", não "existe um processo cujo nome
parece com o de quem usaria o banco" — mede o artefato, não o proxy. O
`pid <> pg_backend_pid()` exclui a própria consulta.

Se qualquer uma das duas acusar, **pare e espere**. Havendo sessão-guia coordenando
a máquina, **peça a janela e espere o OK** antes de seguir — inclusive para
teste de segundos, porque a suíte trunca as tabelas a cada teste.

Barato e vale sempre: `bash .claude/hooks/estado.sh` já responde janela,
modelo da última rodada e validade do token do Google numa tela só.

Os dois comandos acima foram exercidos NOS DOIS SENTIDOS em 03/09, que é o que
o `AGENTS.md` exige de qualquer varredura antes de ela valer como prova: sem
servidor, `pgrep` vazio e 0 conexões; com o `sofia-bot` de pé, `pgrep` acusando
dois PIDs e 1 conexão no `sofia_test`. Trava que só foi vista dizer "livre" não
provou que consegue dizer "ocupado".

## 1. Validar sem gastar token

```bash
.venv/bin/python -m sofia_eval --lista
```

Derruba YAML inválido antes da primeira chamada de LLM. Faça isto **sempre**
depois de mexer em cenário.

## 2. Subir o sofia-bot com o modelo DECLARADO

O modelo é ambiente, não é escolha do eval: sem `tenant.openrouter_model` no
YAML, o tenant nasce com a coluna nula e o `sofia-bot` usa o `OPENROUTER_MODEL`
do `.env` dele. Para uma passada comparativa, declare na subida — **nunca
editando o `.env`**, que é configuração da máquina do fundador:

```bash
cd ~/sofia-bot && OPENROUTER_MODEL=<modelo> npm start
```

Espere o health responder antes de seguir (não durma um tempo fixo):

```bash
until curl -fs --max-time 2 http://localhost:3000/health >/dev/null; do sleep 1; done
```

## 3. Rodar

```bash
cd ~/sofia-eval
.venv/bin/python -m sofia_eval                          # tudo
.venv/bin/python -m sofia_eval --cenario <id> --cenario <id>
```

Leva minutos e custa dinheiro de verdade (chave de teste, teto US$5). Rode em
segundo plano e espere a notificação, em vez de ficar consultando.

## 4. Ler o resultado — os três vereditos não são dois

- **PASSOU / FALHOU / ERRO.** ERRO não é veredito sobre o modelo: é infra
  (contenção, credencial, latência, exceção do servidor). Não recalibra teto e
  não prova bug de comportamento.
- Se reprovou, separe **comportamento** (o banco não bateu) de **teto**
  (`chamadas_ia_max`/`tokens_prompt_max` estourados). Teto velho reprova
  cenário por calibração, não por comportamento — são coisas diferentes no
  relato.
- **Confira o modelo efetivo** no cabeçalho do relatório HTML
  (`~/para-revisao/relatorio-eval-*.html`). Ele vem de `ai_usage.model`, o que
  o servidor gravou — não o que a config dizia. É a única prova de que o
  override do passo 2 chegou.
- Cenário que falhou deixa o estado do banco em
  `~/para-revisao/eval-<cenario>-*.json`. **Nada disso se commita** — carrega
  conversa de terceiro, que não entra em repositório em nenhuma visibilidade.
- ERRO com o servidor de pé: leia o log dele antes de culpar o modelo. Em
  02/09, três ERROs eram um `TypeError` em `openrouter.js` (`handleUserMessage`),
  não comportamento.

## 5. Derrubar o servidor

Terminada a passada, derrube — deixar de pé com override de modelo confunde a
próxima sessão:

```bash
kill $(ps -eo pid,cmd | awk '/node src\/server\.js/ && !/awk/ {print $1}')
curl -s -o /dev/null -w "%{http_code}\n" --max-time 2 http://localhost:3000/health   # 000 = caiu
```

E avise a sessão-guia que a janela está livre.

## Se for CALIBRAR teto (não é uma passada, são três)

Teto é **2 × o máximo observado em 3 passadas consecutivas**, contra o modelo
declarado. Nunca arredonde para número redondo: número redondo esconde de onde
veio. O comentário ao lado do teto no YAML tem de dizer data, modelo e os três
números medidos.

**Passada com turno DEGRADADO não conta para as três — repete, e a repetição
fica registrada.** Turno degradado é a assinatura `chamadas_ia > 0` com
`prompt_tokens = 0`: o modelo não respondeu (429 — ver passo 2), a chamada foi
contada e nenhum token de prompt somou. Confira **por linha, nunca pela soma**:
cenário com turnos bons e degradados misturados escapa do agregado. Desde
`eefc598` o eval detecta a assinatura sozinho e o cenário sai **ERRO** antes de
qualquer verificação rodar — a regra existe para o que vem DEPOIS disso: três
passadas com uma degradada no meio dão um teto envenenado sem ninguém notar.

**São DOIS detectores, e um não substitui o outro** — porque a assinatura
sozinha não bastava, e isso foi medido, não previsto. O corpo do 429 chega SEM
`usage`, e o `+= 1` do `openrouter.js` está dentro de `if (completion?.usage)`;
então o que fica em `ai_usage` depende de QUANDO o 429 chegou:

| onde o 429 bate | linha em `ai_usage` | quem pega |
|---|---|---|
| iteração 1 (turno todo degradado) | `(1 chamada, 0 tokens)` — o `chamadas \|\| 1` do `usage.js:37` converte o zero | `turnos_degradados` |
| iteração ≥2 (turno PARCIAL) | `(1 chamada, 7.482 tokens)` — a iteração 1 já somou de verdade | `turnos_degradados_por_texto` |

A segunda forma produziu um **verde falso medido** em 03/09: o
`grade-do-profissional`, com a condição quebrada de propósito, PASSOU — as duas
respostas eram *"Desculpa, deu uma travada aqui"*. A assinatura não viu.

**O segundo detector é CONTORNO e tem data para morrer.** Ele casa uma string
LITERAL de `openrouter.js` (`banco.TEXTO_DEGRADADO`). Morre quando o
`sofia-bot` passar a registrar a iteração fracassada como dado próprio em
`ai_usage` — item de fila de lá, decisão da guia em 03/09; a opção estruturada
não foi feita agora porque é código de produção com migration, e a produção
estava com incidente de 429 em curso.

**Risco enquanto ele viver, e ele é SILENCIOSO:** se alguém mudar aquele texto
no `openrouter.js`, o eval para de acusar e não avisa — o verde falso volta.
Quem mexer no texto tem de mexer em `banco.TEXTO_DEGRADADO`. O `autoteste`
exerce os dois sentidos E o quase-acerto (mesma frase sem um acento não pode
acusar), para a fragilidade ser executável em vez de só escrita.

Cenário que nasce VERDE precisa, antes de contar como guarda, do **controle de
sensibilidade**: quebre de propósito a condição que ele guarda, rode, e veja o
vermelho. Verde dos dois jeitos = a asserção não mede o que afirma medir. Ver
`AGENTS.md`.

### A calibração pendente da leva 2 — ordem pronta para disparar

> **ESTADO EM 03/09: PAUSADA — não é fracasso, e os 9 NÃO estão declarados sem
> teto.** Motivo: o 429 deixou de ser intermitente e virou persistente no fim
> da tarde — **6 passadas seguidas degradaram**, sem nenhuma conversa saudável
> para medir (8 degradações no total: 4 na iteração 1, 3 na 2, 1 na 3).
> Declarar "sem teto, 429 em 03/09" carimbaria uma medição feita sob um código
> que está prestes a mudar.
>
> **Condição de retomada:** a fatia de RETENTATIVA de erro transitório entrar
> em produção no `sofia-bot` (autorizada em 03/09; decisão da guia). O eval
> roda contra o servidor real, então turno que degrada hoje é candidato a
> completar depois dela — e teto medido sob o código que VAI para produção
> vale mais que teto medido sob o que está saindo.
>
> **Ao retomar, comece no passo 3** (o passo 2 está concluído). Os passos 1 e 2
> não precisam ser refeitos; o passo 1 é barato e vale rodar mesmo assim.

**Destravada em 03/09.** Estava bloqueada até a guarda de
`completion.choices[0]` (`openrouter.js`, `handleUserMessage`) entrar em
produção — passada que morre em `TypeError` não mede nada. Ela entrou: merge
`cee3876` no `sofia-bot`, 03/09 04:45 UTC.

Para quem for procurá-la: a guarda é a **condição** `!choice?.message`, logo
depois de `const choice = completion?.choices?.[0]`. Não é a ausência de
`choices` — a forma real `choices: [{ finish_reason: 'error' }]` TEM `choices` —
e não é a função `resumirRespostaSemChoices`, que só formata o resumo dentro do
`console.error`.

Esta é a ordem, sem nada a decidir na hora.

A ordem é barata→cara de propósito: cada passo elimina uma hipótese antes que
a seguinte custe janela ou token. (Renumerado em 03/09; onde as mensagens
antigas dizem "Passo −1" leia passo 2, e "Passo 0", passo 3.)

**Passo 1 — verificar o VERIFICADOR, antes de qualquer coisa.**

```bash
.venv/bin/python -m sofia_eval.autoteste
```

Segundos, zero token. Exerce cada chave do vocabulário nos dois sentidos
contra o banco. Se algo aqui reprovar, **pare**: quer dizer que a camada que
decide PASSOU/FALHOU está quebrada, e toda medição feita em cima dela seria
confiança falsa com carimbo de número. Vindo antes, ele elimina de graça a
hipótese "minha verificação está errada" — sem isso, uma reprovação nas
passadas obriga a voltar e checar, gastando janela.

(Usa o `sofia_test`, então vale a trava de recurso do passo 0.)

**Passo 2 — ler o corpo que a guarda registrou. ✔ CONCLUÍDO em 03/09; a
sessão que retomar começa no passo 3.**

O que este passo existia para descobrir, e descobriu: **o corpo sem choice
utilizável é `error.code=429`, limite de requisição.** Não é moderação, apesar
de o comentário do próprio `openrouter.js` levantar esse caminho como "real"; e
não é saldo da chave, que não é free tier e não foi debitada pelas chamadas 429.
Bug aberto desde 15/08, fechado aqui.

**E o 429 é INTERMITENTE** — este é o achado que muda a calibração, não o nome
do erro. Mesma chave, mesmo modelo: uma passada degradada e as duas seguintes
normais (3 chamadas/11.310 tokens, e 2/3.754). Intermitente é PIOR que
permanente, porque envenena algumas passadas em silêncio em vez de derrubar
todas. É de onde vem a regra da passada degradada, lá em cima.

Por que o passo existia — continua valendo se o caso voltar: a guarda **não
conserta a causa**, ela converte um `TypeError` que mata o turno numa degradação
que REGISTRA o que a API devolveu. O cenário passa a rodar até o fim, e o
veredito vira uma afirmação sobre o SILÊNCIO do modelo, não sobre
comportamento — calibrar em cima disso mede o custo do FRACASSO com cara de
número medido. Para reproduzir: rode **um** dos cenários que reproduziam o
`TypeError` (`agendamento-executa-nao-descreve`, `cancelar-de-terceiro` ou
`fora-do-horario`) e procure no log do servidor a linha
`resposta sem choice utilizável`.

Decisão tomada, com o corpo na mão: **causa com contorno** — o contorno é
repetir a passada degradada, justamente porque o 429 é intermitente; a
calibração roda com conversa real. A alternativa continua escrita para o dia em
que a causa não tiver contorno: calibrar só os cenários que COMPLETAM e declarar
os outros sem teto, com o motivo escrito. Isso é honesto; teto medido sobre
conversa quebrada não é.

Regra da guia, 03/09. É a mesma distinção de ERRO ≠ FALHOU do `AGENTS.md`,
aplicada um nível acima: um cenário pode COMPLETAR e ainda assim não estar
medindo o que se pensa.

**Passo 3 — controle de sensibilidade dos cenários que nascem VERDES.**

*Estado em 03/09, para quem retomar não repetir trabalho nem herdar conclusão
que não existe:*

- **`grade-do-profissional`: INCONCLUSIVO — não reprovado.** Quebrado de
  propósito e rodado 6 vezes; nenhuma passada produziu conversa saudável.
  Nunca foi visto VERMELHO, e também nunca foi visto verde de forma limpa. A
  primeira tentativa deu PASSOU, mas era o verde falso do turno parcialmente
  degradado — hoje ela sairia ERRO. **Refazer do zero ao retomar.**
- **`agenda-unica-um-por-vez`: NÃO TENTADO.** Não cheguei a rodá-lo: com tudo
  degradando, gastar turno nele mediria o 429, não o cenário.

São dois: `grade-do-profissional` e `agenda-unica-um-por-vez`. Cada um traz
no próprio YAML a instrução exata de como quebrá-lo; em resumo, no
`agenda-unica-um-por-vez` é remover o item de `agenda_ocupada`, e sem ele a
marcação DEVE acontecer e o cenário ficar vermelho.

No `grade-do-profissional`, e antes das passadas de medição, não depois:
remova (ou desloque de hora) a exceção `bloqueio` do YAML e rode só esse
cenário. Sem o bloqueio das 9h a marcação DEVE acontecer, `agendamentos` vira 1, e o cenário **tem de ficar VERMELHO**.
Se ficar verde, ele não entra na leva e o achado vale mais que a calibração.
Restaure o YAML e registre o resultado no relato.

**Passo 4 — 3 passadas dos 9 cenários sem teto**, com o modelo declarado na
subida. Fonte da verdade sobre quem falta é o YAML, não uma lista escrita
aqui, que envelhece: cenário sem `chamadas_ia_max` é cenário sem teto.
Passada que voltar com ERRO por turno degradado **não conta** — repete, e a
repetição fica registrada (regra no começo desta seção).
Levantar a lista sem gastar nada — casando a CHAVE, não a string:

```bash
grep -L "^ *chamadas_ia_max:" cenarios/*.yaml
```

O `^ *` não é firula. `grep -L "chamadas_ia_max"` sem âncora devolve 8 em vez
de 9: o `bot-a-bot-desengajar` cita o nome da chave num COMENTÁRIO explicando
por que NÃO tem teto, e o padrão ingênuo conta isso como se tivesse. Achado ao
rodar o controle positivo neste próprio comando — que é a regra do
`AGENTS.md` aplicada à ferramenta antes de ela entrar aqui. Controle: os 6
cenários da v1 têm de ficar FORA da lista.

Dimensionamento **em dólares**, que é a moeda da decisão — token não é
dólar, e o US$5 da chave é teto, não saldo. Medido em 03/09 pela API do
OpenRouter: a chave tem limite de US$5, **já consumiu US$2,20** (desde 25/08,
quase tudo sob gemini) e **restam US$2,80**. O preço do
`openai/gpt-5.6-luna` é US$0,20 por milhão de tokens de prompt e US$1,20 por
milhão de completion. Base medida da v1 sob gemini: 17 turnos custaram 50
chamadas e 199.338 tokens de prompt por passada (~2,9 chamadas e ~11,7 mil
tokens por turno). Os 9 cenários da leva 2 somam 31 turnos → ~90 chamadas e
~363 mil tokens de prompt por passada, ~1,09 milhão nas três →
**US$ 0,25 na rodada inteira**, contra US$2,80 disponíveis. Cabe com ~11× de
folga; a projeção de tokens é piso (três cenários têm 5-6 turnos), mas
precisaria errar por uma ordem de grandeza para apertar.

Colateral que vale saber: o luna custa **3,75× menos por token de prompt** que
o gemini, e nos três cenários medidos em 02/09 também gastou MENOS tokens. A
troca de modelo barateou a operação nas duas pontas.

Refaça esta medição antes de disparar (`GET /api/v1/auth/key` com a chave do
`.env`, e `GET /api/v1/models` para o preço) — saldo é estado, não constante.

**Ordem dos 9, por valor e não por número**, para que uma rodada interrompida
deixe medido o que mais importa: `grade-do-profissional` e
`configuracao-multiprofissional` primeiro (o segundo é pré-requisito de
lançamento), depois `agenda-unica-um-por-vez`, `bot-a-bot-desengajar`,
`cancelamento-correto`, `remarcacao`, `horario-de-outra-pessoa`,
`duplicidade`, `precisa-verificar-novamente`. São nove — confira contra o
`grep` acima antes de rodar, e não contra esta lista.

**O que NÃO precisa entrar nesta rodada: remedir os 6 cenários da v1.** Os
tetos deles foram calibrados sob gemini, e a troca para `openai/gpt-5.6-luna`
poderia tê-los invalidado — mas a passada de 02/09 mediu os três que
produziram resultado, e nos três o luna custou MENOS que a base gemini
(`duracao-por-profissional` 10/38.907 contra 13/56.657;
`data-relativa` 6/22.833 contra 8/30.716; `horario-ocupado` 4/15.134 contra
7/27.277). Então os tetos da v1 estão **folgados sob o modelo novo, não
apertados**: não vão reprovar cenário por calibração. Remedi-los aperta a
guarda, o que é bom, mas não destrava nada e dobraria o custo da rodada.
Vale como fatia própria, depois — não como pré-requisito da leva 2.
