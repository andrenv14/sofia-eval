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
