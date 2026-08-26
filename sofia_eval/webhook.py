"""Monta, assina e entrega o payload que a Meta enviaria.

A assinatura tem de bater BYTE A BYTE com a de tests/helpers/webhookPayload.js
do sofia-bot: lá o corpo assinado é `Buffer.from(JSON.stringify(bodyObj))`.
`JSON.stringify` não põe espaço nenhum e não escapa não-ASCII — o equivalente
exato em Python é `json.dumps(..., separators=(',', ':'), ensure_ascii=False)`
em UTF-8. E os MESMOS bytes vão no corpo do POST: assinar uma serialização e
mandar outra devolve 401.
"""

import hashlib
import hmac
import json

import requests


class ErroDeWebhook(Exception):
    pass


class ServidorForaDoAr(ErroDeWebhook):
    pass


def montar_payload(phone_number_id: str, de: str, texto: str, wamid: str) -> dict:
    """Espelha montarPayloadMensagem({ type: 'text' }) do sofia-bot."""
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": phone_number_id},
                            "messages": [
                                {"id": wamid, "from": de, "type": "text", "text": {"body": texto}}
                            ],
                        }
                    }
                ]
            }
        ]
    }


def serializar(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def assinar(corpo: bytes, app_secret: str) -> str:
    """Espelha assinar() do sofia-bot."""
    return "sha256=" + hmac.new(app_secret.encode("utf-8"), corpo, hashlib.sha256).hexdigest()


COMO_SUBIR = (
    "O servidor do sofia-bot não respondeu em {url}.\n\n"
    "Suba ele numa outra aba, você mesmo — o eval não sobe servidor:\n"
    "    cd ~/sofia-bot && npm start\n\n"
    "Confira também que o .env do sofia-bot aponta para o banco sofia_test e\n"
    "usa a chave de teste da OpenRouter."
)


class Cliente:
    def __init__(self, cfg):
        self._cfg = cfg
        self._sessao = requests.Session()

    def conferir_servidor(self) -> None:
        url = f"{self._cfg.sofia_url}/health"
        try:
            resp = self._sessao.get(url, timeout=5)
        except requests.RequestException:
            raise ServidorForaDoAr(COMO_SUBIR.format(url=url)) from None
        if resp.status_code != 200:
            raise ServidorForaDoAr(
                f"{url} respondeu HTTP {resp.status_code} em vez de 200.\n\n"
                + COMO_SUBIR.format(url=url)
            )

    def enviar(self, de: str, texto: str, wamid: str) -> None:
        payload = montar_payload(self._cfg.phone_number_id, de, texto, wamid)
        corpo = serializar(payload)
        url = f"{self._cfg.sofia_url}/webhook"
        try:
            resp = self._sessao.post(
                url,
                data=corpo,
                headers={
                    "Content-Type": "application/json",
                    "X-Hub-Signature-256": assinar(corpo, self._cfg.whatsapp_app_secret),
                },
                timeout=self._cfg.timeout_http_s,
            )
        except requests.RequestException as err:
            raise ServidorForaDoAr(f"{err}\n\n" + COMO_SUBIR.format(url=url)) from None

        if resp.status_code == 401:
            raise ErroDeWebhook(
                "o webhook devolveu 401 (assinatura inválida).\n"
                "O WHATSAPP_APP_SECRET que o eval usa é diferente do que o servidor\n"
                "do sofia-bot carregou. Compare os dois .env e reinicie o servidor."
            )
        if resp.status_code != 200:
            raise ErroDeWebhook(f"o webhook devolveu HTTP {resp.status_code}: {resp.text[:200]}")
