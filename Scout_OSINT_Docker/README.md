# Scout Gate v5.2 — MITM + redireccionamentos

Ferramenta de **borda** separada do **Porteiro**: ngrok aponta para o Scout; Scout encaminha para o Porteiro (ou outras apps) conforme as rotas ligadas na aba Scout do Control Plane.

## Início rápido

Integrado ao N8Groker via `iniciar_servicos.ps1` quando `USE_SCOUT=1` no `.env` raiz.

Para uso standalone (desenvolvimento):

```bat
copy .env.example .env
# Preferir variáveis no ../.env da raiz do N8Groker
setup.bat
```

**Linux recomendado** para MITM + firewall (`network_mode: host` no compose).

## Arquitectura (com N8Groker)

```
Ngrok → scout-backend:4050 (rede Docker, não o gateway do host)
           ├── portal HTTP/WebSocket → painel em host.docker.internal:8502
           │                         → n8n_app:5678, langfuse-web:3000, litellm:4000
           ├── toggle ON  → outra rota
           └── toggle OFF → sem listener + iptables DROP (Linux)
```

| Variável | Descrição |
|----------|-----------|
| `SCOUT_PUBLIC_PORT` | Porta onde ngrok aponta (Scout escuta) |
| `SCOUT_UPSTREAM_HOST/PORT` | Porteiro (seed inicial) |
| `SCOUT_NGROK_TUNNEL_URL` | URL ngrok (metadado mostrado na aba Scout) |
| `SCOUT_ENFORCE_FIREWALL` | `1` = iptables DOCKER-USER |
| `SCOUT_LISTEN_PORT_START` | Pool para novas rotas Docker |

## Gestão

Neste ramo não há janela Tk. A tecla **G** do `iniciar_servicos.ps1`, e a tecla **G** deste `setup.ps1`, abrem `http://localhost:8501/?aba=scout`. O `setup.ps1` só sobe o container Docker e o standby. A janela clássica continua no branch `master`.

A aba cobre redirecionamentos (toggle, modo, porta manual, sync Docker), tráfego, clientes, alias, bloqueio, desbloqueio e o health. A paridade com a janela antiga está no README da raiz. O cliente HTTP é `scout/gate.py` (biblioteca padrão, sem Tk).

## API (`127.0.0.1:8765`)

A porta de gestão e a `4050` saem no compose só em `127.0.0.1`. No Docker Desktop o ngrok chega na `4050` por `host.docker.internal`. A aba Scout usa HTTP. O WebSocket `/ws` continua na API; a aba não o importa. Rota manual, patch ou sync não escuta nem aponta para `5676` ou `5677`. A rota `porteiro-manual` segue para o `5677`.

| Endpoint | Função |
|----------|--------|
| `GET /health` | Estado |
| `GET /redirections` | Lista + toggles |
| `POST /redirections/toggle` | `{id, enabled}` |
| `POST /redirections/manual` | Nova rota manual |
| `POST /redirections/sync` | Discovery Docker |
| `GET /traffic` | Log sessões |
| `POST /aliases` | Alias IP |
| `POST /blocklist` | Bloquear IP na borda |
| `POST /firewall/sync` | Reaplicar iptables |
| `WS /ws` | Stream ticks + comandos |

## Comportamento

- **Container novo** → aparece na aba Scout com a rota **desligada**
- **Toggle ON** → Scout escuta `listen_port` e encaminha para upstream
- **Toggle OFF** → sem listener; firewall bloqueia porta Docker publicada (Linux)
- **Porteiro** → nunca modificado; Scout só faz pass-through TCP

## Estrutura

```
scout/
  core/
    mitm_proxy.py          # MITM TCP
    redirection_registry.py
    firewall_linux.py
    docker_resolver.py
    traffic_log.py
    ip_aliases.py
    blocklist.py
  server/                  # FastAPI backend
  gate.py                  # cliente HTTP da aba Scout (sem janela)
  client/ws_client.py      # protocolo WebSocket da API; a aba não importa
data/                      # redirections.json, aliases, blocklist
```

## Aviso

Uso em redes próprias. MITM + firewall requer Linux com Docker host network.
