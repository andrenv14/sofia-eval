#!/usr/bin/env bash
#
# estado.sh — estado de abertura de sessão, entregue pelo hook SessionStart.
#
# POR QUE ISTO EXISTE: em 02/09, uma sessão só descobriu que a rodada estava
# medindo o modelo errado (Gemini, não o Luna trocado em produção) porque o
# fundador olhou o painel da OpenRouter por acaso — o modelo era AMBIENTE, não
# declarado em lugar nenhum que aparecesse na abertura. Na mesma sessão, uma
# passada inteira travou no primeiro cenário porque o refresh token do Google
# da conta dedicada tinha expirado — e isso só apareceu depois de subir o
# servidor e gastar o tempo da passada. As duas linhas abaixo (modelo da
# última rodada, validade do token do Google) existem para que a PRÓXIMA
# sessão veja isso na abertura, sem precisar tropeçar de novo. Molde:
# `estado.sh` do `sofia-bot` (irmão privado deste repositório) — mesmo
# contrato, adaptado à trava de recurso e ao vocabulário deste repo.
#
# CONTRATO: somente leitura, saída curta, NENHUM segredo impresso (lê .env só
# para autenticar a checagem do Google — nunca imprime token, client secret ou
# a DATABASE_URL inteira). Falha de qualquer bloco degrada com mensagem
# própria e NUNCA aborta — o script sai sempre com 0, porque travar a abertura
# da sessão seria pior que a informação faltar.

cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null || true

echo "=== estado do repositório (sofia-eval) ==="
echo "máquina: $(hostname 2>/dev/null || echo '(hostname indisponível)')"

branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')
sha=$(git rev-parse --short HEAD 2>/dev/null || echo '?')
echo "branch: ${branch} @ ${sha}"

main_local=$(git rev-parse main 2>/dev/null || echo '')
if remoto=$(timeout 5 git ls-remote origin main 2>/dev/null) && [ -n "$remoto" ]; then
  main_remoto=$(echo "$remoto" | awk '{print $1}')
  origem="origin/main (consultado agora)"
else
  main_remoto=$(git rev-parse origin/main 2>/dev/null || echo '')
  origem="origin/main LOCAL — remoto não consultado (sem rede?)"
fi
if [ -z "$main_local" ] || [ -z "$main_remoto" ]; then
  echo "main: não foi possível comparar com o remoto"
elif [ "$main_local" = "$main_remoto" ]; then
  echo "main: ${main_local:0:7} == ${origem}"
else
  echo "main: ${main_local:0:7} != ${main_remoto:0:7} — DIVERGENTE de ${origem}"
fi

sujo=$(git status --short 2>/dev/null)
if [ -z "$sujo" ]; then
  echo "working tree limpo"
else
  echo "working tree SUJO:"
  echo "$sujo" | sed 's/^/  /'
fi

# Trava de recurso: suíte do sofia-bot e servidor local dividem o sofia_test
# com este eval — nunca ao mesmo tempo (AGENTS.md, "Antes de qualquer coisa").
procs=$(pgrep -fa -- 'vitest|node src/server\.js|sofia_eval' 2>/dev/null | grep -v 'estado\.sh')
if [ -z "$procs" ]; then
  echo "trava de recurso: nenhum vitest/servidor/eval rodando agora — janela livre"
else
  echo "trava de recurso: ALGO RODANDO — NÃO inicie passada nem suíte agora:"
  echo "$procs" | sed 's/^/  /'
fi

# Modelo da ÚLTIMA rodada registrada — lido do relatório HTML, não do .env:
# é o que o servidor de fato gravou em ai_usage.model naquela execução.
ultimo=$(ls -1t ~/para-revisao/relatorio-eval-*.html 2>/dev/null | head -1)
if [ -n "$ultimo" ]; then
  modelo=$(grep -o '<dt>modelo(s)</dt><dd[^>]*>[^<]*</dd>' "$ultimo" 2>/dev/null \
           | sed -E 's#.*<dd[^>]*>([^<]*)</dd>#\1#')
  quando=$(basename "$ultimo" | sed -E 's/relatorio-eval-([0-9]{4})([0-9]{2})([0-9]{2})-([0-9]{2})([0-9]{2})([0-9]{2})\.html/\1-\2-\3 \4:\5:\6/')
  echo "última rodada (${quando}): modelo ${modelo:-?}"
else
  echo "última rodada: nenhum relatório em ~/para-revisao"
fi

# Validade do refresh token do Google da conta dedicada do eval — best-effort,
# timeout curto, nunca imprime o token. Achado de 02/09: este é o primeiro
# jeito de a passada travar, e travava só depois de já ter subido o servidor.
if [ -x .venv/bin/python ]; then
  google=$(timeout 8 .venv/bin/python -c '
import sys
try:
    from sofia_eval import config, google_calendar
    cfg = config.carregar()
    calendario = google_calendar.Calendario(cfg)
    conta = calendario.conferir_conta()
    print(f"token válido, conta {conta}")
except config.ErroDeConfig:
    print("config incompleta (.env) — não verificado")
except google_calendar.ErroDeCalendario as err:
    linha = " ".join(str(err).split())[:180]
    print(f"FALHA — {linha}")
except Exception as err:
    print(f"não verificado ({type(err).__name__})")
' 2>/dev/null)
  if [ -z "$google" ]; then
    echo "Google Calendar (conta do eval): não verificado (timeout ou .venv ausente)"
  else
    echo "Google Calendar (conta do eval): ${google}"
  fi
else
  echo "Google Calendar (conta do eval): não verificado (.venv/bin/python ausente — rode 'python -m venv .venv')"
fi

if [ -f .env ]; then
  banco=$(grep -E '^DATABASE_URL=' .env 2>/dev/null | sed -E 's#.*/([^/?]+)(\?.*)?$#\1#')
  echo "banco (sofia-eval/.env): ${banco:-(DATABASE_URL não encontrada; cai no .env do sofia-bot)}"
else
  echo "banco: sofia-eval/.env ausente — config cai inteira no .env do sofia-bot (config.py, SOFIA_BOT_ENV)"
fi
