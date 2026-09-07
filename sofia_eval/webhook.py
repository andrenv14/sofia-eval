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
                        # `field` é obrigatório desde a fatia coex-webhooks do
                        # sofia-bot: `processarWebhook`, em `src/server.js` do
                        # sofia-bot, só processa change do campo `messages` e descarta as outras com log, sem
                        # responder. Sem esta chave o webhook devolve 200 e não
                        # processa nada — o eval esperaria uma resposta que
                        # nunca vem, e cenário que assere ausência ficaria VERDE
                        # com o bot nunca tendo rodado.
                        "field": "messages",
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


# Número do NEGÓCIO nos echos. Fictício e no padrão do repositório
# (`5511999990NNN`), como manda o `AGENTS.md`: número de verdade não entra aqui
# nem como exemplo.
DONO = "5511999990000"


def montar_payload_echo(phone_number_id: str, para: str, texto: str, wamid: str) -> dict:
    """Espelha `montarPayloadEcho` do sofia-bot (tests/helpers/webhookPayload.js).

    É a fala do DONO pelo app dele, que a Meta devolve como echo. Duas coisas
    a separam do payload de mensagem, e as duas importam:

    - `field` é `smb_message_echoes`, NÃO `messages` e NÃO `history`. O
      `history` carrega o mesmo array `message_echoes[]`, mas de conversa
      antiga — o sofia-bot ignora-o de propósito, senão o primeiro sync
      calaria a Sofia por causa de mensagens de meses atrás. Com o campo
      errado o webhook devolve 200 e não processa NADA.
    - o corpo vai em `message_echoes[]`, com `from` = negócio e `to` = contacto.

    O echo NÃO gera turno da assistente. Quem espera resposta aqui espera para
    sempre — ver `turnos.enviar_echo_do_dono`."""
    return {
        "entry": [
            {
                "changes": [
                    {
                        "field": "smb_message_echoes",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": phone_number_id},
                            "contacts": [{"wa_id": para}],
                            "message_echoes": [
                                {
                                    "from": DONO,
                                    "to": para,
                                    "id": wamid,
                                    "type": "text",
                                    "text": {"body": texto},
                                }
                            ],
                        },
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

    # O `phone_number_id` vem de QUEM CHAMA, e não de `cfg`, porque ele varia
    # por cenário (`tenant.phone_number_id_do_cenario`). Quem chama passa o que
    # está na linha do tenant recém-criada, então as duas pontas — a que o
    # servidor procura no banco e a que chega no payload — não podem divergir.
    def enviar(self, phone_number_id: str, de: str, texto: str, wamid: str) -> None:
        self._postar(montar_payload(phone_number_id, de, texto, wamid))

    def enviar_echo_do_dono(self, phone_number_id: str, para: str, texto: str, wamid: str) -> None:
        """Entrega a fala do dono como echo. Não devolve nada e não espera nada."""
        self._postar(montar_payload_echo(phone_number_id, para, texto, wamid))

    def _postar(self, payload: dict) -> None:
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
