"""Conexão com o `sofia_test` e as consultas que sustentam o julgamento.

O eval julga pelo efeito, não pelo texto: o Postgres é o segundo ponto de
verdade, independente do que o modelo escreveu.
"""

import psycopg
from psycopg.rows import dict_row


def conectar(database_url: str):
    return psycopg.connect(database_url, row_factory=dict_row, autocommit=True)


def esperar_banco(conn):
    conn.execute("SELECT 1").fetchone()


def agendamentos_ativos(conn, tenant_id: int) -> list:
    """Linhas de `appointments` que ainda valem como agendamento.

    Conta só `status='ativo'`, e não toda linha da tabela: um cancelamento não
    apaga a linha, troca o status. Contar cancelado como agendamento faria o
    cenário `cancelar-de-terceiro` passar mesmo com o cancelamento indevido
    tendo acontecido."""
    return conn.execute(
        """
        SELECT a.id, a.telefone, a.inicio, a.fim, a.status, a.google_event_id,
               a.professional_id, p.name AS profissional
          FROM appointments a
          LEFT JOIN professionals p ON p.id = a.professional_id
         WHERE a.tenant_id = %s AND a.status = 'ativo'
         ORDER BY a.id
        """,
        (tenant_id,),
    ).fetchall()


def agendamento_por(conn, tenant_id: int, telefone: str, inicio) -> list:
    """Linhas de `appointments` para um tenant/telefone/horário exatos, EM
    QUALQUER STATUS — ao contrário de `agendamentos_ativos`, que só vê
    `status='ativo'`. Usada por `agendamento_status`, que afere o estado de
    uma linha específica (ex.: virou 'cancelado'), não o agregado."""
    return conn.execute(
        """
        SELECT id, telefone, inicio, fim, status
          FROM appointments
         WHERE tenant_id = %s AND telefone = %s AND inicio = %s
         ORDER BY id
        """,
        (tenant_id, telefone, inicio),
    ).fetchall()


def turnos_degradados(conn, tenant_id: int) -> int:
    """Quantos turnos a API atendeu com um corpo SEM choice utilizável.

    Assinatura, medida em 03/09: `chamadas_ia > 0` com `prompt_tokens = 0`.
    O corpo de erro do OpenRouter (ex.: `error.code=429`) chega com `usage`
    ZERADO, então `openrouter.js` conta a chamada e não soma token nenhum —
    uma linha por turno em `ai_usage`.

    Por LINHA, nunca pela soma: um cenário com turnos bons e degradados
    misturados teria `SUM(prompt_tokens) > 0` e escaparia do agregado.

    Existe porque a guarda do `choices[0]`, correta em produção (degradar é
    melhor que morrer), criou um modo de falha novo AQUI: o turno degradado
    satisfaz `agendamentos: 0` e `sem_agendamento_novo: true` sem o modelo ter
    dito uma palavra, e o cenário PASSAVA. Verde observando nada."""
    linha = conn.execute(
        """
        SELECT count(*) AS n FROM ai_usage
         WHERE tenant_id = %s AND chamadas_ia > 0 AND prompt_tokens = 0
        """,
        (tenant_id,),
    ).fetchone()
    return int(linha["n"])


# Texto EXATO que `openrouter.js` devolve quando o turno degrada — copiado
# literalmente de `src/ai/openrouter.js` (`cee3876`, linha 362). Não é
# heurística: é um sentinela de valor fixo, e o casamento é por igualdade.
TEXTO_DEGRADADO = "Desculpa, deu uma travada aqui. Pode repetir sua última mensagem?"


def turnos_degradados_por_texto(conn, tenant_id: int) -> int:
    """Os turnos degradados que `turnos_degradados` NÃO enxerga.

    CONTORNO, e contorno nasce com data para morrer: esta função cai quando o
    `sofia-bot` passar a registrar a iteração fracassada como dado próprio em
    `ai_usage` (item de fila de lá, decisão da guia em 03/09). Até lá os dois
    detectores rodam JUNTOS — este não substitui a assinatura, cobre o outro
    lado dela.

    Por que é preciso, medido em 03/09: o corpo do 429 chega SEM `usage` (o
    log do servidor diz `chaves=[id,error]`), e o `usoAcumulado.chamadas += 1`
    do `openrouter.js` está DENTRO de `if (completion?.usage)`. Então a
    iteração que falha não deixa rastro nenhum, e o que sobra em `ai_usage`
    depende de QUANDO o 429 chegou:

    - **429 na iteração 1** — nada foi contado, `registrarUso` grava
      `chamadas || 1` (`usage.js:37`) e a linha sai `(1 chamada, 0 tokens)`.
      Casa a assinatura; `turnos_degradados` acusa.
    - **429 na iteração ≥2** — a iteração 1 já somou tokens de verdade, e a
      linha sai `(1 chamada, 7.482 tokens)`. É indistinguível de um turno
      saudável, e a assinatura fica cega.

    A segunda forma não é hipótese: ela produziu um VERDE FALSO medido. Com o
    `grade-do-profissional` quebrado de propósito, os dois turnos degradaram na
    iteração 2 e o cenário PASSOU — as duas respostas eram o texto de desculpa.
    No banco, esse texto é o único rastro que sobra.

    RISCO, e ele é SILENCIOSO: isto casa uma string literal de outro
    repositório. Se alguém mudar aquele texto no `openrouter.js`, esta detecção
    para de acusar sem avisar ninguém, e o verde falso volta. Quem mexer no
    texto tem de mexer aqui. O `autoteste` exerce os dois sentidos e também o
    quase-acerto, para essa fragilidade ficar executável em vez de só escrita.
    """
    linha = conn.execute(
        """
        SELECT count(*) AS n FROM messages
         WHERE tenant_id = %s AND role = 'assistant' AND content = %s
        """,
        (tenant_id, TEXTO_DEGRADADO),
    ).fetchone()
    return int(linha["n"])


def custo(conn, tenant_id: int) -> dict:
    linha = conn.execute(
        """
        SELECT COALESCE(SUM(chamadas_ia), 0)     AS chamadas_ia,
               COALESCE(SUM(prompt_tokens), 0)   AS prompt_tokens,
               COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
               COALESCE(SUM(total_tokens), 0)    AS total_tokens
          FROM ai_usage
         WHERE tenant_id = %s
        """,
        (tenant_id,),
    ).fetchone()
    return {k: int(v) for k, v in linha.items()}


def humano_pendente(conn, tenant_id: int, telefone: str) -> bool:
    """A ferramenta de atendente humano foi chamada, e o efeito chegou ao banco.

    Afere `contatos_estado.humano_pendente_desde IS NOT NULL` — e SÓ isso.
    Deliberadamente NÃO afere `silenciado_ate` no futuro, por três razões:

    1. `silenciado_ate` depende de `SILENCIO_DONO_MS_OVERRIDE`, que é lido uma
       vez no carregamento do módulo do `sofia-bot` e vale para o processo
       inteiro. Cenários que precisam do silêncio CURTO (para o turno seguinte
       não ser engolido) e cenários que o querem LONGO (para o vê-lo no futuro)
       se morderiam no mesmo servidor.
    2. É a marca DURÁVEL. O silêncio é uma janela que passa por desenho; a
       marca fica até alguém assumir. Afirmar sobre a janela é afirmar sobre o
       relógio.
    3. É o campo que o PAINEL lê (`marcaHumanoPendente`, `src/admin/panel.js`,
       selo "pendente de humano"). A asserção passa a afirmar o efeito de
       produto — "isto apareceu na fila do dono" — e não um detalhe do silêncio.

    Continua a discriminar, porque as duas funções de silêncio fazem coisas
    OPOSTAS com o campo: `silenciarPorHandoff` marca, `silenciarPorEcho` limpa
    (o echo é a prova de que o dono assumiu). Cenário que confunda os dois
    caminhos reprova.

    SÓ existe linha em tenant com `coexistencia = true` — o guard está em três
    camadas do `sofia-bot`, acima de `silenciarPorHandoff`. Cenário que afira
    isto sem `tenant.coexistencia` dá falso vermelho."""
    linha = conn.execute(
        """
        SELECT humano_pendente_desde IS NOT NULL AS pendente
          FROM contatos_estado
         WHERE tenant_id = %s AND contact_phone = %s
        """,
        (tenant_id, telefone),
    ).fetchone()
    return bool(linha["pendente"]) if linha else False


def falas_do_dono(conn, tenant_id: int, telefone: str, texto: str) -> int:
    """Quantas linhas do histórico do contacto CONTÊM este texto do dono.

    Desde a fatia `fala-do-dono` (`d1b631c`), `registrarFalaDoDono`
    (`src/session/sessionStore.js`) grava a fala do dono em `messages` com
    `role = 'assistant'` — indistinguível de uma resposta da Sofia, que é
    precisamente o problema que o cenário da marca de autoria existe para
    medir.

    É a ÚLTIMA escrita do laço do echo (`silenciarPorEcho` vem antes), o que a
    torna a barreira certa para saber que o echo foi processado. Não há turno
    da assistente para esperar: echo não é mensagem de cliente.

    CONTÉM, E NÃO IGUAL — mudado em 07/09, e a igualdade estourava a barreira.
    A fatia `fala-do-dono-completa` acrescenta ao `content` um prefixo de
    autoria — `MARCA_ATENDIMENTO` mais um espaço, aplicado por
    `comMarcaDeAtendimento` (src/coex/marcaAtendimento.js) — antes do texto
    cru do dono. O VALOR da constante não se escreve aqui de propósito: é de
    um repositório privado e este é público (ver o comentário de
    `_checar_falas_do_dono` no autoteste). O nome basta a quem tem acesso, e
    esta função não depende do valor. Contra essa
    branch, `content = texto` nunca casa: a barreira espera até o timeout e
    TODO cenário com turno `dono:` sai ERRO por algo que ele não mede. A
    barreira afere que o echo FOI PROCESSADO, não a forma da linha — e a forma
    é justamente o que o cenário 17 se proibiu de afirmar. Com "contém" ela
    funciona contra as duas formas, que é o que uma barreira deve fazer: a
    `main` grava o texto cru, a branch grava com prefixo, e as duas param a
    espera no mesmo instante.

    `position(%s in content) > 0` e não `LIKE`: `position` casa a string
    LITERAL. Num `LIKE`, um `%` ou `_` dentro da fala do dono viraria
    curinga — e a fala do dono é texto de cenário, que muda sem que ninguém
    lembre desta função."""
    linha = conn.execute(
        """
        SELECT count(*) AS n FROM messages
         WHERE tenant_id = %s AND contact_phone = %s
           AND role = 'assistant' AND position(%s in content) > 0
        """,
        (tenant_id, telefone, texto),
    ).fetchone()
    return int(linha["n"])


def silenciado_ate(conn, tenant_id: int, telefone: str):
    """Instante em que o silêncio deste contacto acaba, ou None."""
    linha = conn.execute(
        "SELECT silenciado_ate FROM contatos_estado WHERE tenant_id = %s AND contact_phone = %s",
        (tenant_id, telefone),
    ).fetchone()
    return linha["silenciado_ate"] if linha else None


def status_pendente(conn, wamid: str):
    linha = conn.execute(
        "SELECT status FROM mensagens_pendentes WHERE wamid = %s", (wamid,)
    ).fetchone()
    return linha["status"] if linha else None


def turnos_da_assistente(conn, tenant_id: int, telefone: str) -> int:
    """Quantas respostas a assistente já gravou para este contato.

    `pushTurn` grava user+assistant em `messages` DENTRO de handleUserMessage,
    ou seja ANTES do envio pra Meta. É o que permite separar "o modelo não
    respondeu" de "o modelo respondeu e só o envio falhou"."""
    linha = conn.execute(
        "SELECT count(*) AS n FROM messages WHERE tenant_id = %s AND contact_phone = %s AND role = 'assistant'",
        (tenant_id, telefone),
    ).fetchone()
    return int(linha["n"])


def resposta_da_assistente(conn, tenant_id: int, telefone: str):
    """Último texto que a assistente gravou. Não entra no julgamento (v1 não
    julga por conteúdo), mas ajuda a entender uma falha na hora de ler a saída."""
    linha = conn.execute(
        """
        SELECT content FROM messages
         WHERE tenant_id = %s AND contact_phone = %s AND role = 'assistant'
         ORDER BY id DESC LIMIT 1
        """,
        (tenant_id, telefone),
    ).fetchone()
    return linha["content"] if linha else None


def transcricao(conn, tenant_id: int, telefone: str) -> list:
    """A conversa inteira do cenário, em ordem cronológica — não entra no
    julgamento (v1 não julga por conteúdo); é o dado que o relatório HTML usa
    para mostrar turno a turno o que o cliente mandou e o que a Sofia
    respondeu. Lida com a conexão ainda viva, antes do TRUNCATE do próximo
    cenário apagar `messages` — mesma janela de tempo de `evidencia.capturar`."""
    return conn.execute(
        """
        SELECT id, role, content, created_at FROM messages
         WHERE tenant_id = %s AND contact_phone = %s
         ORDER BY id
        """,
        (tenant_id, telefone),
    ).fetchall()


def modelos(conn, tenant_id: int) -> list:
    """Quebra do custo por `model`, direto do banco — não do `.env`, porque o
    que importa é o modelo que de fato atendeu."""
    return conn.execute(
        """
        SELECT model,
               COALESCE(SUM(chamadas_ia), 0)       AS chamadas_ia,
               COALESCE(SUM(prompt_tokens), 0)     AS prompt_tokens,
               COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
               COALESCE(SUM(total_tokens), 0)      AS total_tokens
          FROM ai_usage
         WHERE tenant_id = %s
         GROUP BY model
         ORDER BY model
        """,
        (tenant_id,),
    ).fetchall()
