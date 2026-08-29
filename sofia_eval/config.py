"""Configuração do eval, lida de .env — nunca de argumento de linha de comando.

Segredos moram no .env local (que está no .gitignore). Como as credenciais do
Google já vivem no .env do sofia-bot, este módulo aceita apontar pra lá em vez
de duplicar segredo em dois arquivos: o .env do sofia-eval vem primeiro, e o
que faltar é preenchido pelo .env do sofia-bot.
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

RAIZ = Path(__file__).resolve().parent.parent

# Regra dura de isolamento (docs/features/sofia-eval.md, "Isolamento"): o eval
# TRUNCA tabelas antes de cada cenário. Apontar pra produção seria destrutivo,
# então o nome do banco é conferido antes de qualquer conexão.
BANCO_EXIGIDO = "sofia_test"


class ErroDeConfig(Exception):
    """Falta variável, ou uma variável tem valor proibido."""


def _ler_env(caminho: Path) -> dict:
    """Parser mínimo de .env — evita depender de python-dotenv (a spec fecha as
    dependências em requests, PyYAML e psycopg)."""
    if not caminho.is_file():
        return {}
    valores = {}
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#"):
            continue
        if linha.startswith("export "):
            linha = linha[len("export "):].strip()
        if "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        chave = chave.strip()
        valor = valor.strip()
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
            valor = valor[1:-1]
        valores[chave] = valor
    return valores


@dataclass(frozen=True)
class Config:
    database_url: str
    sofia_url: str
    whatsapp_app_secret: str
    phone_number_id: str
    google_client_id: str
    google_client_secret: str
    google_refresh_token: str
    google_calendar_id: str
    google_account: str
    timeout_turno_s: float
    poll_intervalo_s: float
    carencia_status_s: float
    janela_limpeza_dias: int
    timeout_http_s: float
    # Diretório do sofia-bot (pai do .env apontado por SOFIA_BOT_ENV) — usado
    # só pelo relatório HTML, para ler o SHA do sofia-bot no cabeçalho.
    sofia_bot_dir: Path


def carregar() -> Config:
    # Precedência: ambiente do processo > .env do sofia-eval > .env do sofia-bot.
    do_eval = _ler_env(RAIZ / ".env")
    caminho_bot = os.environ.get("SOFIA_BOT_ENV") or do_eval.get("SOFIA_BOT_ENV") or "~/sofia-bot/.env"
    do_bot = _ler_env(Path(caminho_bot).expanduser())

    def pega(nome, padrao=None):
        for fonte in (os.environ, do_eval, do_bot):
            valor = fonte.get(nome)
            if valor is not None and valor.strip() != "":
                return valor.strip()
        return padrao

    faltando = []

    def obrigatoria(nome):
        valor = pega(nome)
        if valor is None:
            faltando.append(nome)
        return valor or ""

    database_url = obrigatoria("DATABASE_URL")
    whatsapp_app_secret = obrigatoria("WHATSAPP_APP_SECRET")
    google_client_id = obrigatoria("GOOGLE_CLIENT_ID")
    google_client_secret = obrigatoria("GOOGLE_CLIENT_SECRET")
    google_refresh_token = obrigatoria("GOOGLE_REFRESH_TOKEN")
    google_account = obrigatoria("EVAL_GOOGLE_ACCOUNT")

    if faltando:
        raise ErroDeConfig(
            "Faltam variáveis no .env: "
            + ", ".join(faltando)
            + f"\nCopie .env.example para .env, ou aponte SOFIA_BOT_ENV para o .env do sofia-bot"
            + f" (hoje: {caminho_bot})."
        )

    _conferir_banco(database_url)

    return Config(
        database_url=database_url,
        sofia_url=pega("SOFIA_URL", "http://localhost:3000").rstrip("/"),
        whatsapp_app_secret=whatsapp_app_secret,
        # Fictício de propósito: nenhuma mensagem sai pra Meta, e este número
        # não pode colidir com nenhum tenant real.
        phone_number_id=pega("EVAL_PHONE_NUMBER_ID", "999000999000999"),
        google_client_id=google_client_id,
        google_client_secret=google_client_secret,
        google_refresh_token=google_refresh_token,
        google_calendar_id=pega("EVAL_GOOGLE_CALENDAR_ID", "primary"),
        google_account=google_account,
        timeout_turno_s=float(pega("EVAL_TIMEOUT_TURNO_S", "120")),
        poll_intervalo_s=float(pega("EVAL_POLL_INTERVALO_S", "0.3")),
        carencia_status_s=float(pega("EVAL_CARENCIA_STATUS_S", "8")),
        janela_limpeza_dias=int(pega("EVAL_JANELA_LIMPEZA_DIAS", "45")),
        timeout_http_s=float(pega("EVAL_TIMEOUT_HTTP_S", "30")),
        sofia_bot_dir=Path(caminho_bot).expanduser().parent,
    )


def _conferir_banco(database_url: str) -> None:
    """Recusa qualquer DATABASE_URL que não seja o banco de teste.

    Não é paranoia: o eval roda TRUNCATE. Um .env copiado da VPS por engano
    apagaria a base de um cliente pagante."""
    try:
        nome = urlparse(database_url).path.lstrip("/")
    except ValueError as err:
        raise ErroDeConfig(f"DATABASE_URL não é uma URL válida: {err}") from None
    if nome != BANCO_EXIGIDO:
        raise ErroDeConfig(
            f"DATABASE_URL aponta para o banco {nome!r}, e o eval só roda contra "
            f"{BANCO_EXIGIDO!r}.\nO eval TRUNCA tabelas antes de cada cenário — "
            "apontar para outro banco seria destrutivo. Recusando."
        )


def erro_fatal(mensagem: str) -> None:
    print(f"\n\033[31m{mensagem}\033[0m\n", file=sys.stderr)
    sys.exit(2)
