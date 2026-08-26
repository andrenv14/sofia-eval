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
