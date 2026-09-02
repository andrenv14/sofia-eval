# sofia-eval — guia para sessões de agente

Este arquivo é para quem vai TRABALHAR no código deste repositório, não para
quem quer entender o que ele faz — isso é o `README.md`. Audiência diferente:
o README descreve o que a ferramenta É; isto aqui descreve o que uma sessão
NÃO pode fazer, e os modos de falha que já aconteceram.

## O que é, em uma frase

Ferramenta **pública** que avalia o **comportamento** do modelo de IA do
`sofia-bot` (repositório privado, irmão deste), não o código dele. Se você
está pensando em consertar um bug de comportamento sem escrever antes o
cenário que o prova (e o vê nascer vermelho), pare — é o protocolo central
deste projeto, não um detalhe de estilo.

## Antes de qualquer coisa

- **Repositório é PÚBLICO.** Nunca telefone real, nome de cliente, IP de
  servidor, nome de tenant real ou credencial em commit, comentário, YAML ou
  mensagem de commit. Telefone sintético segue o padrão já usado em
  `cenarios/` (`5511999990NNN`) — nunca um número de verdade, nem "só de
  exemplo".
- **Banco: só `sofia_test`, nunca produção.** A guarda em
  `sofia_eval/config.py` (`_conferir_banco`) recusa qualquer `DATABASE_URL`
  cujo nome de banco não seja exatamente `sofia_test`. Ela existe porque o
  eval **TRUNCA tabelas antes de cada cenário** — um `.env` apontado para o
  lugar errado apagaria cliente pagante. Não afrouxe essa guarda para prefixo
  ou regex. Se um dia existir um banco `sofia_eval` próprio, ele entra como
  MAIS um valor aceito num conjunto fechado (`{"sofia_test", "sofia_eval"}`),
  nunca como enfraquecimento da checagem.
- **Trava de recurso com o `sofia-bot`: nunca ao mesmo tempo.** A suíte
  Vitest do `sofia-bot` e este eval dividem o `sofia_test` na mesma máquina —
  a suíte trunca a cada teste, o eval semeia e lê. Rodar os dois juntos já
  produziu 4 ERROs por contenção (29/08). Antes de QUALQUER passada:
  `pgrep -fa "vitest|node src/server.js"` — se algo aparecer, espere. Se
  houver outra sessão trabalhando o `sofia-bot` na mesma máquina, avise-a
  antes de começar e depois de terminar.
- **IA real custa dinheiro.** Cada turno de cada cenário é uma chamada real à
  OpenRouter, na chave de teste (teto US$5). `--lista` valida todo o
  esquema sem gastar um token — use-a para checar YAML novo. Só uma passada
  de verdade (`--cenario ...` ou sem filtro) chama o modelo.

## O vocabulário de verificação é fechado — por design

**Chave desconhecida no YAML é ERRO, não é ignorada** — vale para `tenant`,
`agenda_ocupada` e `verificacoes` (esquemas `TENANT`/`OCUPADO`/`VERIFICACOES`
em `sofia_eval/cenario.py`). Verificação escrita errado que passa em silêncio
é pior que verificação nenhuma. Consequência prática: **cenário com chave que
ainda não existe no esquema derruba a validação inteira antes do primeiro
token gasto** — isso não é bug, é a trava funcionando. Chave nova é mudança
em `cenario.py` (esquema) + `verificacoes.py` (aplicação), nunca workaround
dentro do YAML.

## ERRO ≠ FALHOU

Três vereditos, não dois: **passou**, **FALHOU** (o modelo fez a coisa
errada — veredito sobre comportamento) e **ERRO** (o turno não processou —
infra, contenção de banco, latência, credencial vencida). ERRO não recalibra
teto, não conta como falha de comportamento e não prova bug de modelo. Antes
de propor mudança de prompt ou de código a partir de uma passada, confira
qual dos três realmente aconteceu — confundir ERRO com FALHOU corrige o
problema errado.

## Tetos de custo: sempre medidos, nunca chutados

`chamadas_ia_max` e `tokens_prompt_max` são **2× o máximo observado em 3
passadas consecutivas**, contra o modelo declarado do cenário (ver seção
seguinte). Teto sem essa origem é chute, e chute em teto reprova cenário por
calibração, não por comportamento — os dois viram vermelho igual na tabela,
mas significam coisas diferentes. Ao fechar cenário novo, o comentário ao
lado do teto no YAML tem de dizer data, modelo e os três números medidos —
é o padrão já usado em `cenarios/01` a `04`. Cenário sem teto medido (leva 2,
em `cenarios/07` a `10` neste momento) é intencionalmente incompleto: omitir
é honesto, fabricar número não é.

## O modelo sob avaliação é declarado, não ambiente

Achado de 02/09: até essa data, `tenant.py` não gravava `openrouter_model` no
`INSERT` do tenant — a coluna nascia NULA e o `sofia-bot` caía no
`OPENROUTER_MODEL` do `.env` local, sem nada registrar QUAL modelo respondeu
uma passada específica. Já corrigido — `tenant.openrouter_model` é
declarável em `tenant:` no YAML. Mesmo assim:

- Cenário com teto calibrado contra um modelo específico **deve** declarar
  `tenant.openrouter_model`, senão o teto vale para "o que estiver no `.env`
  hoje", que muda sem avisar quem lê o cenário depois.
- O modelo que efetivamente respondeu sempre aparece no relatório HTML
  (cabeçalho "modelo(s)", lido de `ai_usage.model` — o que o servidor
  gravou, nunca o que a config dizia). Confira ali antes de tirar conclusão
  de uma passada, principalmente comparando modelos diferentes.
- Para trocar o modelo de TODOS os cenários de uma vez (ex.: passada
  comparativa) sem editar YAML nenhum: variável de ambiente na subida do
  `sofia-bot` (`OPENROUTER_MODEL=... npm start`, um processo só, uma vez).
  **Nunca edite o `.env` do `sofia-bot`** para isso — é configuração da
  máquina do fundador, não deste repositório.

## O modo de falha que uma sessão nova vai encontrar: token do Google

A conta do Google Calendar dedicada ao eval (`EVAL_GOOGLE_ACCOUNT` no
`.env`) usa OAuth2 com refresh token — e se a tela de consentimento do
projeto no Google Cloud estiver em modo "Testing", o Google **expira esse
token em 7 dias**, sem aviso prévio. Sintoma, visto pela primeira vez em
02/09: a passada inteira derruba ANTES do primeiro cenário, na checagem de
identidade da conta (`google_calendar.Calendario.conferir_conta()`), com

```
invalid_grant: Token has been expired or revoked.
```

**Isso não é bug de modelo nem de código — é infraestrutura**, mesma
categoria de ERRO da seção acima, só que acontece antes mesmo do primeiro
`TRUNCATE`. Não tente reautorizar sozinho: o fluxo OAuth exige login
interativo na conta Google dedicada, que só o fundador tem. Pare, reporte o
erro completo (ele não expõe o token — só o corpo HTTP que o Google devolve)
e espere. Se isso se repetir toda semana, a causa provável é a tela de
consentimento em "Testing"; a cura é publicá-la, não reautorizar de novo.

## Antes de propor cenário novo

Leia a spec (`docs/features/sofia-eval.md`) e o item do eval na fila do
`sofia-bot` (`docs/contexto/fila.md`) — os dois vivem no repositório privado,
fora daqui; é achado de lá, não deste repo. Não escreva cenário para um
comportamento que o vocabulário fechado não consegue expressar sem primeiro
decidir a extensão do vocabulário — proponha a chave nova (nome, semântica,
o que quebra se escrita errada) antes do YAML. Achado de tráfego real sem
rastro possível no banco (ex.: a assistente afirma uma capacidade que não
corresponde a nenhuma ferramenta existente) é candidato a julgamento de
texto — que é a fronteira v1/v2 e decisão do fundador, nunca implementação
por conta própria de uma sessão.

## Não crie agentes de propósito geral

Os agentes do `sofia-bot` nasceram cada um de um incidente concreto e
repetido, não de simetria com outro projeto. Este repositório ainda não tem
nenhum incidente que justifique um. Quando um MODO DE FALHA se repetir (não
uma tarefa isolada), aí sim nasce um, com nome e motivo — como este arquivo
está fazendo com o token do Google.
