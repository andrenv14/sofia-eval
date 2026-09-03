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
  conversa, e o repositório é público.
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

**Passo 0 — controle de sensibilidade do `grade-do-profissional`.** Antes das
passadas de medição, e não depois. Remova (ou desloque de hora) a exceção
`bloqueio` do YAML e rode só esse cenário: sem o bloqueio das 9h a marcação
DEVE acontecer, `agendamentos` vira 1, e o cenário **tem de ficar VERMELHO**.
Se ficar verde, ele não entra na leva e o achado vale mais que a calibração.
Restaure o YAML e registre o resultado no relato.

**Passo 1 — 3 passadas dos 8 cenários sem teto**, com o modelo declarado na
subida. São eles: `remarcacao`, `cancelamento-correto`,
`horario-de-outra-pessoa`, `bot-a-bot-desengajar`, `duplicidade`,
`configuracao-multiprofissional`, `precisa-verificar-novamente`,
`grade-do-profissional`. Fonte da verdade sobre quem falta é o YAML, não esta
lista: cenário sem `chamadas_ia_max` é cenário sem teto.

Dimensionamento, para decidir com número em vez de susto — a chave de teste
tem teto de US$5. Base medida da v1 sob `google/gemini-3.7-flash`: 17 turnos
custaram 50 chamadas e 199.338 tokens de prompt por passada, ou seja ~2,9
chamadas e ~11,7 mil tokens por turno. Os 8 cenários da leva 2 somam 29
turnos, o que projeta **~85 chamadas e ~340 mil tokens de prompt por passada**,
~1,0 milhão nas três. É um PISO, não um teto: três desses cenários têm 5 ou 6
turnos, e conversa longa carrega histórico maior por turno.

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
