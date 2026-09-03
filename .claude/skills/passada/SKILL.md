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
pgrep -fa "vitest|node src/server.js"
```

Se aparecer qualquer coisa, **pare e espere**. Havendo sessão-guia coordenando
a máquina, **peça a janela e espere o OK** antes de seguir — inclusive para
teste de segundos, porque a suíte trunca as tabelas a cada teste.

Barato e vale sempre: `bash .claude/hooks/estado.sh` já responde janela,
modelo da última rodada e validade do token do Google numa tela só.

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

Cenário que nasce VERDE precisa, antes de contar como guarda, do **controle de
sensibilidade**: quebre de propósito a condição que ele guarda, rode, e veja o
vermelho. Verde dos dois jeitos = a asserção não mede o que afirma medir. Ver
`AGENTS.md`.

### A calibração pendente da leva 2 — ordem pronta para disparar

Bloqueada até a guarda de `completion.choices[0]` (`openrouter.js`,
`handleUserMessage`) entrar em produção: passada que morre em `TypeError` não
mede nada. Quando entrar, esta é a ordem, sem nada a decidir na hora.

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

**Passo 2 — ler o corpo que a guarda registrou. NÃO pule para o passo 3.**
A guarda **não conserta a causa**; ela converte um `TypeError` que mata o turno
numa degradação que REGISTRA o que a API devolveu no lugar de `choices`. Ou
seja: depois do deploy, os cenários que erravam provavelmente vão rodar até o
fim e **ainda assim reprovar** — só que com uma resposta de desculpa em vez de
um stack trace. Calibrar em cima disso mede o custo do FRACASSO, não o
comportamento do cenário: o teto sairia lixo, com cara de número medido.

Então, assim que a guarda entrar: rode **um** dos cenários que reproduziam o
`TypeError` (`agendamento-executa-nao-descreve`, `cancelar-de-terceiro` ou
`fora-do-horario`) e leia no log do servidor o corpo registrado. Ele deve
dizer o que está chegando: erro de provedor, filtro de conteúdo, limite, ou
resposta vazia. Um cenário, custo desprezível — e é a primeira vez que alguém
vai ver a causa, que está escondida atrás do erro desde que apareceu.

Com o corpo na mão, decida:
- **causa com contorno** → aplica-se, e a calibração roda com conversa real;
- **sem contorno** → a calibração roda só nos cenários que COMPLETAM, e os
  outros ficam declarados sem teto, com o motivo escrito. Isso é honesto;
  teto medido sobre conversa quebrada não é.

Regra da guia, 03/09. É a mesma distinção de ERRO ≠ FALHOU do `AGENTS.md`,
aplicada um nível acima: um cenário pode COMPLETAR e ainda assim não estar
medindo o que se pensa.

**Passo 3 — controle de sensibilidade dos cenários que nascem VERDES.**
São dois: `grade-do-profissional` e `agenda-unica-um-por-vez`. Cada um traz
no próprio YAML a instrução exata de como quebrá-lo; em resumo, no
`agenda-unica-um-por-vez` é remover o item de `agenda_ocupada`, e sem ele a
marcação DEVE acontecer e o cenário ficar vermelho.

No `grade-do-profissional`: Antes das
passadas de medição, e não depois. Remova (ou desloque de hora) a exceção
`bloqueio` do YAML e rode só esse cenário: sem o bloqueio das 9h a marcação
DEVE acontecer, `agendamentos` vira 1, e o cenário **tem de ficar VERMELHO**.
Se ficar verde, ele não entra na leva e o achado vale mais que a calibração.
Restaure o YAML e registre o resultado no relato.

**Passo 4 — 3 passadas dos 9 cenários sem teto**, com o modelo declarado na
subida. Fonte da verdade sobre quem falta é o YAML, não uma lista escrita
aqui, que envelhece: cenário sem `chamadas_ia_max` é cenário sem teto.
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
