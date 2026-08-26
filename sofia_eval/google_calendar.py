"""Cliente mínimo do Google Calendar, via REST, só com `requests`.

Por que existe: a decisão foi usar um calendário real (conta dedicada) em vez
de um flag de mock no sofia-bot — um flag desses, ligado por engano em
produção, faria agendamento não chegar ao calendário do cliente EM SILÊNCIO.
Com calendário real, o cenário de duração por profissional testa comportamento,
não mock.

O eval precisa do Calendar para duas coisas: semear `agenda_ocupada` como
evento de verdade, e LIMPAR tudo entre cenários — cenário não pode herdar
evento do anterior.
"""

import time
from datetime import timedelta

import requests

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/calendar/v3"


class ErroDeCalendario(Exception):
    pass


def _explicar(metodo: str, caminho: str, resp) -> str:
    """Traduz os dois 403 que travam a primeira execução numa conta nova.

    Sem isso, o operador vê um JSON de 300 caracteres e não sabe que a correção
    é um clique no console do Google."""
    corpo = resp.text[:400]
    base = f"{metodo} {caminho} devolveu HTTP {resp.status_code}: {corpo}"
    if resp.status_code == 403 and "has not been used in project" in corpo:
        return (
            base
            + "\n\nA Calendar API está DESABILITADA no projeto do Cloud desta conta.\n"
            "Abra o link acima, clique em Ativar, espere alguns minutos e rode de novo.\n"
            "O mesmo projeto precisa disso para o próprio sofia-bot agendar."
        )
    if resp.status_code == 403 and "insufficient" in corpo.lower():
        return (
            base
            + "\n\nO refresh token não tem o escopo do Calendar "
            "(https://www.googleapis.com/auth/calendar).\n"
            "Gere o token de novo pedindo esse escopo."
        )
    return base


class Calendario:
    def __init__(self, cfg):
        self._cfg = cfg
        self._token = None
        self._expira_em = 0.0
        self._sessao = requests.Session()

    # ---- autenticação --------------------------------------------------
    def _access_token(self) -> str:
        if self._token and time.monotonic() < self._expira_em:
            return self._token
        try:
            resp = self._sessao.post(
                TOKEN_URL,
                data={
                    "client_id": self._cfg.google_client_id,
                    "client_secret": self._cfg.google_client_secret,
                    "refresh_token": self._cfg.google_refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=self._cfg.timeout_http_s,
            )
        except requests.RequestException as err:
            raise ErroDeCalendario(f"não consegui falar com o Google para renovar o token: {err}") from None
        if resp.status_code != 200:
            raise ErroDeCalendario(
                "o Google recusou o refresh token "
                f"(HTTP {resp.status_code}): {resp.text[:300]}\n"
                "Confira GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN."
            )
        dados = resp.json()
        self._token = dados["access_token"]
        # Margem de 60s pra não usar um token que expira no meio da chamada.
        self._expira_em = time.monotonic() + int(dados.get("expires_in", 3600)) - 60
        return self._token

    def _chamar(self, metodo: str, caminho: str, **kwargs):
        url = f"{API}{caminho}"
        try:
            resp = self._sessao.request(
                metodo,
                url,
                headers={"Authorization": f"Bearer {self._access_token()}"},
                timeout=self._cfg.timeout_http_s,
                **kwargs,
            )
        except requests.RequestException as err:
            raise ErroDeCalendario(f"{metodo} {caminho} falhou: {err}") from None
        if resp.status_code >= 400:
            raise ErroDeCalendario(_explicar(metodo, caminho, resp))
        return resp.json() if resp.content else {}

    # ---- guarda de conta ----------------------------------------------
    def conferir_conta(self) -> str:
        """Recusa rodar contra um calendário que não seja o da conta dedicada.

        O eval APAGA todos os eventos da janela de limpeza. Apontar para a
        agenda de um cliente seria destrutivo — mesma lógica da trava de
        DATABASE_URL."""
        meta = self._chamar("GET", "/calendars/primary")
        conta = (meta.get("id") or "").strip()
        esperado = self._cfg.google_account.strip()
        if conta.lower() != esperado.lower():
            raise ErroDeCalendario(
                f"o refresh token é da conta {conta!r}, e EVAL_GOOGLE_ACCOUNT diz {esperado!r}.\n"
                "O eval apaga eventos desse calendário — recusando por segurança."
            )
        return conta

    # ---- eventos -------------------------------------------------------
    def listar(self, inicio, fim) -> list:
        eventos = []
        pagina = None
        while True:
            params = {
                "timeMin": inicio.isoformat(),
                "timeMax": fim.isoformat(),
                "maxResults": 2500,
                "showDeleted": "false",
            }
            if pagina:
                params["pageToken"] = pagina
            dados = self._chamar(
                "GET", f"/calendars/{self._cfg.google_calendar_id}/events", params=params
            )
            eventos.extend(dados.get("items") or [])
            pagina = dados.get("nextPageToken")
            if not pagina:
                return eventos

    def criar(self, inicio, fim, titulo: str, timezone: str, descricao: str = "") -> dict:
        return self._chamar(
            "POST",
            f"/calendars/{self._cfg.google_calendar_id}/events",
            json={
                "summary": titulo,
                "description": descricao,
                "start": {"dateTime": inicio.isoformat(), "timeZone": timezone},
                "end": {"dateTime": fim.isoformat(), "timeZone": timezone},
            },
        )

    def apagar(self, event_id: str) -> None:
        url = f"{API}/calendars/{self._cfg.google_calendar_id}/events/{event_id}"
        try:
            resp = self._sessao.delete(
                url,
                headers={"Authorization": f"Bearer {self._access_token()}"},
                timeout=self._cfg.timeout_http_s,
            )
        except requests.RequestException as err:
            raise ErroDeCalendario(f"falha ao apagar o evento {event_id}: {err}") from None
        # 410 = já estava apagado. Não é erro.
        if resp.status_code not in (200, 204, 404, 410):
            raise ErroDeCalendario(
                f"falha ao apagar o evento {event_id}: HTTP {resp.status_code}: {resp.text[:200]}"
            )

    def limpar(self, hoje, dias: int) -> int:
        """Apaga TODOS os eventos da janela. Erro aqui é fatal para a execução:
        cenário que herda evento do anterior produz veredito mentiroso."""
        from datetime import datetime, time as _time
        from zoneinfo import ZoneInfo

        utc = ZoneInfo("UTC")
        inicio = datetime.combine(hoje - timedelta(days=1), _time.min, tzinfo=utc)
        fim = datetime.combine(hoje + timedelta(days=dias), _time.max, tzinfo=utc)
        apagados = 0
        for evento in self.listar(inicio, fim):
            if evento.get("id"):
                self.apagar(evento["id"])
                apagados += 1
        return apagados
