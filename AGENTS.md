# sofia-eval — guia para sessões de agente

Este arquivo é para quem vai TRABALHAR no código deste repositório, não para
quem quer entender o que ele faz — isso é o `README.md`. Audiência diferente:
o README descreve o que a ferramenta É; isto aqui descreve o que uma sessão
NÃO pode fazer, e os modos de falha que já aconteceram.

## O que é, em uma frase

Ferramenta que avalia o **comportamento** do modelo de IA do `sofia-bot`
(repositório irmão, privado), não o código dele. Se você
está pensando em consertar um bug de comportamento sem escrever antes o
cenário que o prova (e o vê nascer vermelho), pare — é o protocolo central
deste projeto, não um detalhe de estilo.

## Antes de qualquer coisa

- **A visibilidade deste repositório MUDA — não escreva regra que dependa
  dela.** Foi público de 25/08 a 03/09/2026 e é privado desde então, na mesma
  noite em que várias decisões daqui foram tomadas supondo "público". Não
  confie nem no que esta linha diz: confira se importar
  (`gh repo view --json visibility`, ou a API do GitHub sem autenticação —
  404 é privado, 200 é público).

  **A regra vale nos dois estados: nunca telefone real, nome de cliente, IP
  de servidor, nome de tenant real ou credencial em commit, comentário, YAML
  ou mensagem de commit.** Vale privado porque repo privado é clonado para
  laptop, compartilhado com quem entra no projeto, e — como 03/09 provou nas
  duas direções — está a UM clique de mudar de estado. Dado de terceiro não
  entra aqui em nenhum dos dois casos. Telefone sintético segue o padrão já usado em
  `cenarios/` (`5511999990NNN`) — nunca um número de verdade, nem "só de
  exemplo".

  **Antes de todo commit aqui, uma pergunta — não uma varredura:**

  > Alguma linha disto descreve operação, configuração ou estado de um
  > cliente real?

  O motivo de ser pergunta e não `grep`: em 03/09 três coisas passaram por
  descuido — um telefone parcial mascarado, uma frase descrevendo a
  configuração viva de um tenant, e uma varredura minha cega ao próprio caso
  que buscava. **Nenhuma das três tinha padrão.** Grep não pegaria nenhuma;
  a pergunta pegaria as três.

  A justificativa original desta pergunta era outra, e ela **enfraqueceu no
  mesmo dia** — fica registrada porque como ela caiu importa mais que ela.
  Era: "em repositório público, commit + push é publicação irreversível, então
  cuidar antes custa segundos e cuidar depois não resolve". Verdadeiro, e forte
  — enquanto o repositório era público. Ficou privado horas depois, e o
  argumento perdeu o essencial.

  O que sobra não depende de visibilidade nenhuma, e é o que sustenta a
  pergunta sozinho: **as três não tinham padrão**. Grep não pega o que não tem
  padrão; pergunta pega. E o custo continua assimétrico mesmo privado, só que
  menos: remover de commit já feito não apaga do histórico, aqui nem lá.
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
em `cenarios/07` a `15` neste momento) é intencionalmente incompleto: omitir
é honesto, fabricar número não é.

## Cenário que nasce verde precisa de prova de sensibilidade

Nem todo cenário nasce vermelho. Guarda de regressão (bug já fechado, cenário
existe para ele não voltar) nasce verde, e isso é legítimo. O que NÃO é
legítimo é chamá-la de guarda sem nunca a ter visto falhar: **cenário que
nunca falhou não provou que consegue falhar**. Uma verificação negativa —
`agendamentos: 0`, `sem_agendamento_novo: true` — fica verde por várias causas
diferentes, e só uma delas é a que o cenário afirma medir; as outras são
recusa por outro motivo, ou o modelo nem tentando.

Então, antes de um cenário verde contar como guarda: **quebre de propósito a
condição que ele guarda, rode, e veja o vermelho**. Depois restaure e
registre o resultado. Ex.: em `grade-do-profissional`, remover a exceção de
bloqueio tem de fazer o agendamento acontecer e o cenário reprovar. Se ficar
verde dos dois jeitos, a asserção é decoração — e aí o achado é maior que o
cenário. Regra da guia, 03/09 (os rótulos de dia deste projeto seguem UTC),
depois de dois bloqueadores custarem caro por previsão tratada como medição.

Existe ferramenta para isso do lado do eval: `python -m sofia_eval.autoteste`
exerce cada chave do vocabulário nos dois sentidos contra o banco, sem gastar
token. **Rode-a depois de mexer em `verificacoes.py`** — é o lugar onde um bug
não aparece como erro, aparece como veredito errado.

**A regra não é sobre cenários — é sobre qualquer verificação, inclusive as
suas próprias.** Um `grep` de auditoria, um filtro, uma varredura: se você só
o viu dizer "limpo", ele não provou que consegue dizer "sujo". Caso concreto,
03/09, os dois lados da mesa no mesmo dia: para conferir que nenhum telefone
real tinha sobrado, rodei `55[0-9]{9,13}` e li "limpo" como prova. O padrão
exige 9 dígitos seguidos; o número em questão estava mascarado
(`5561xxxx` + `xxxx`) e tem 8 — a varredura era **estruturalmente incapaz** de
achar justamente o que eu tinha acabado de remover. A guia rodou um pente
equivalente no histórico e também voltou "limpo", pelo mesmo motivo, e só
descobriu porque testou contra um positivo conhecido. **Toda varredura roda
com controle positivo: primeiro faça-a acusar algo que você sabe que está
lá.** Sem isso, "limpo" não é resultado, é ausência de resultado.

**E varredura de árvore de trabalho não fala sobre o histórico.** `git grep`
no checkout responde "o que está aqui agora"; um dado que já foi commitado e
publicado continua legível por `git log -p` e pela página do commit, e
removê-lo num commit posterior **não o despublica**. Para essa pergunta, o
instrumento é `git log --all -S` / `git grep <padrão> $(git rev-list --all)`.

## Escreva o que o verde NÃO diz, junto da asserção

Parente da regra acima, e distinta dela: lá o risco é a asserção não conseguir
falhar; aqui é ela conseguir falhar, passar honestamente, e ainda assim ser
**lida como uma afirmação mais larga do que sustenta**.

O eval roda sempre com a SUA configuração — sua agenda conectada, seu tenant,
seu banco. Então todo verde é da forma "sob esta configuração, o
comportamento X vale". Quase nunca é da forma "o cliente Y está protegido",
que é o que quem lê o relatório quer que ele signifique, especialmente quando
o cenário é portão de lançamento. Exemplo vivo: `agenda-unica-um-por-vez`
prova que a agenda compartilhada recusa dois profissionais no mesmo horário —
com a agenda conectada, porque é assim que o eval roda. Num tenant cuja agenda
NÃO está conectada, essa barreira não existe, e o verde daqui não diz nada
sobre ele.

**Então: ao lado de toda asserção que possa ser lida de forma mais larga,
escreva o limite.** Uma linha dizendo o que o verde não afirma vale mais que
um parágrafo dizendo o que ele afirma. E desconfie de verificação de
configuração por campo preenchido: campo pode conter marcador que parece valor
e não é — só chamada real responde "está conectado?".

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
