"""Evidência de falha: o estado do banco salvo ANTES que a limpeza o apague.

O eval TRUNCA 11 tabelas na entrada de cada cenário, com autocommit — quando o
comando devolve o prompt, o estado do cenário que falhou já não existe, e não
há transação para voltar atrás. Ligar uma flag antes de rodar não resolveria:
ninguém sabe qual cenário vai falhar antes de ele falhar.

Então a captura é automática e só acontece no caminho de falha. É a mesma ideia
de `banco.resposta_da_assistente`, que já lê do banco com o tenant ainda de pé
para explicar a falha na saída — aqui com mais dados e em arquivo.

Nada neste módulo pode derrubar a execução nem mudar veredito: a captura é
conveniência de depuração, não parte do julgamento. Falhou, avisa e segue.
"""

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from . import relatorio

PASTA = Path("~/para-revisao")

# Colunas listadas uma a uma, nunca `SELECT *`: assim uma coluna de segredo
# acrescentada ao schema do sofia-bot amanhã não entra no arquivo sozinha.
#
# `professionals.google_refresh_token` EXISTE na tabela e fica de fora de
# propósito — é credencial. `google_calendar_id` também fica de fora: não é
# segredo, mas é o endereço da conta dedicada do eval, e não diz nada sobre a
# falha (todo profissional recebe o mesmo valor, o do .env).
COLUNAS_APPOINTMENTS = (
    "id", "professional_id", "telefone", "inicio", "fim", "status",
    "google_event_id", "google_event_link", "criado_em",
)
COLUNAS_PROFESSIONALS = (
    "id", "name", "service_duration_minutes", "active", "sort_order",
    "prompt_extra", "created_at",
)
COLUNAS_MESSAGES = ("id", "contact_phone", "role", "content", "created_at")

# Grade do profissional: nenhuma das duas tabelas tem `tenant_id`, então a
# captura passa por `professionals`. Entram porque um cenário de grade
# (`grade-do-profissional`) tem o veredito inteiro decidido por elas — sem
# isso, a evidência mostraria "nenhum agendamento" sem mostrar a regra que
# recusou, que é justamente o que se quer ler quando esse cenário falha.
COLUNAS_GRADE = ("id", "professional_id", "dia_semana", "hora_inicio", "hora_fim", "criado_em")
COLUNAS_EXCECOES = (
    "id", "professional_id", "data", "hora_inicio", "hora_fim", "tipo", "criado_em",
)


def capturar(conn, tenant, cenario, resultado) -> str:
    """Grava o estado do tenant em JSON e devolve o caminho, ou None se não deu.

    Chamada só quando o veredito não é PASSOU, e sempre antes da limpeza."""
    try:
        dados = _coletar(conn, tenant, cenario, resultado)
        destino = _caminho(cenario.id)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(
            json.dumps(dados, ensure_ascii=False, indent=2, default=_serializavel),
            encoding="utf-8",
        )
        return _amigavel(destino)
    except Exception as err:
        # Nunca fatal: o veredito do cenário já está decidido, e a execução
        # continua. Só avisa que a evidência não foi salva.
        print(
            relatorio.amarelo(f"    (não consegui salvar a evidência de {cenario.id}: {err})"),
            flush=True,
        )
        return None


def _caminho(id_cenario: str) -> Path:
    carimbo = datetime.now().strftime("%Y%m%d-%H%M%S")
    return PASTA.expanduser() / f"eval-{id_cenario}-{carimbo}.json"


def _amigavel(destino: Path) -> str:
    """Caminho com ~ quando está sob o home — é o que a linha do relatório mostra."""
    try:
        return f"~/{destino.relative_to(Path.home())}"
    except ValueError:
        return str(destino)


def _coletar(conn, tenant, cenario, resultado) -> dict:
    tid = tenant["id"]
    return {
        "cenario": {
            "id": cenario.id,
            "descricao": (cenario.descricao or "").strip() or None,
            "arquivo": str(cenario.caminho),
            "contato": cenario.contato,
            "timezone": cenario.timezone,
            "turnos_enviados": list(cenario.turnos),
            "verificacoes_esperadas": cenario.verificacoes,
        },
        "veredito": resultado.veredito,
        "motivos": [str(m) for m in resultado.motivos],
        "capturado_em": datetime.now().astimezone().isoformat(timespec="seconds"),
        "segundos": round(resultado.segundos, 1),
        # Do tenant só o que identifica a execução. A linha inteira de `tenants`
        # carrega whatsapp_access_token, google_client_secret e
        # google_refresh_token — nunca vai para arquivo.
        "tenant": {
            "id": tid,
            "slug": tenant["slug"],
            "timezone": tenant["timezone"],
            "service_duration_minutes": tenant["service_duration_minutes"],
        },
        "appointments": _tabela(conn, "appointments", COLUNAS_APPOINTMENTS, tid),
        "professionals": _tabela(conn, "professionals", COLUNAS_PROFESSIONALS, tid),
        "messages": _tabela(conn, "messages", COLUNAS_MESSAGES, tid),
        "ai_usage": _uso_de_ia(conn, tid),
        "professional_availability": _tabela_por_profissional(
            conn, "professional_availability", COLUNAS_GRADE, tid
        ),
        "professional_availability_exceptions": _tabela_por_profissional(
            conn, "professional_availability_exceptions", COLUNAS_EXCECOES, tid
        ),
    }


def _tabela(conn, nome: str, colunas, tenant_id: int) -> list:
    """Todas as linhas do tenant, em ordem de id — cronológica, é serial."""
    return conn.execute(
        f"SELECT {', '.join(colunas)} FROM {nome} WHERE tenant_id = %s ORDER BY id",
        (tenant_id,),
    ).fetchall()


def _tabela_por_profissional(conn, nome: str, colunas, tenant_id: int) -> list:
    """Igual a `_tabela`, para as tabelas que se ligam ao tenant pelo
    profissional — `professional_availability` e as exceções não têm
    `tenant_id` próprio."""
    campos = ", ".join(f"t.{c}" for c in colunas)
    return conn.execute(
        f"""
        SELECT {campos}
          FROM {nome} t
          JOIN professionals p ON p.id = t.professional_id
         WHERE p.tenant_id = %s
         ORDER BY t.id
        """,
        (tenant_id,),
    ).fetchall()


def _uso_de_ia(conn, tenant_id: int) -> dict:
    """Agregado: o total que o relatório mostra, mais a quebra por modelo."""
    campos = """
        COALESCE(SUM(chamadas_ia), 0)       AS chamadas_ia,
        COALESCE(SUM(prompt_tokens), 0)     AS prompt_tokens,
        COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
        COALESCE(SUM(total_tokens), 0)      AS total_tokens
    """
    total = conn.execute(
        f"SELECT COUNT(*) AS linhas, {campos} FROM ai_usage WHERE tenant_id = %s",
        (tenant_id,),
    ).fetchone()
    por_modelo = conn.execute(
        f"""
        SELECT model, COUNT(*) AS linhas, {campos}
          FROM ai_usage WHERE tenant_id = %s
         GROUP BY model ORDER BY model
        """,
        (tenant_id,),
    ).fetchall()
    return {"total": total, "por_modelo": por_modelo}


def _serializavel(valor):
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, Path):
        return str(valor)
    return str(valor)
