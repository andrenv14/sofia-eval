"""Datas relativas ao dia da execução.

O eval roda em dia qualquer, então cenário não pode ter data fixa: "depois de
amanhã" no turno tem de casar com o que a verificação espera. A forma relativa
`+N` resolve pra hoje+N dias no fuso do tenant.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


def hoje(timezone: str) -> date:
    return datetime.now(ZoneInfo(timezone)).date()


def resolver(valor, timezone: str) -> date:
    """Aceita 'AAAA-MM-DD', '+N' (dias a partir de hoje) ou 'hoje'."""
    if isinstance(valor, date):
        return valor
    texto = str(valor).strip()
    if texto == "hoje":
        return hoje(timezone)
    if texto.startswith("+") or texto.startswith("-"):
        try:
            return hoje(timezone) + timedelta(days=int(texto))
        except ValueError:
            raise ValueError(f"data relativa inválida: {valor!r} (use '+2', 'hoje' ou 'AAAA-MM-DD')") from None
    try:
        return date.fromisoformat(texto)
    except ValueError:
        raise ValueError(f"data inválida: {valor!r} (use '+2', 'hoje' ou 'AAAA-MM-DD')") from None


def instante(data: date, horario: str, timezone: str) -> datetime:
    hora, _, minuto = horario.partition(":")
    return datetime(data.year, data.month, data.day, int(hora), int(minuto or 0), tzinfo=ZoneInfo(timezone))
