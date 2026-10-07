# Scout Gate v5.2 — MITM + redireccionamentos

Ferramenta de **borda** separada do **Porteiro**: ngrok aponta para o Scout; Scout encaminha para o Porteiro (ou outras apps) conforme toggles na GUI.

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
Ngrok → Scout :4050 (MITM)
           ├── toggle ON  → Porteiro :5677 (pass-through tcp)
           ├── toggle ON  → outra rota
           └── toggle OFF → sem listener + iptables DROP (Linux)
Porteiro (inalterado) → n8n :5678
```

| Variável | Descrição |
|----------|-----------|
| `SCOUT_PUBLIC_PORT` | Porta onde ngrok aponta (Scout escuta) |
| `SCOUT_UPSTREAM_HOST/PORT` | Porteiro (seed inicial) |
| `SCOUT_NGROK_TUNNEL_URL` | URL ngrok (metadados GUI) |
| `SCOUT_ENFORCE_FIREWALL` | `1` = iptables DOCKER-USER |
| `SCOUT_LISTEN_PORT_START` | Pool para novas rotas Docker |

## GUI (3 abas)

1. **Redireccionamentos** — toggle ON/OFF por container/porta; sync Docker; add manual
2. **Tráfego** — sessões MITM + aliases IP + alertas
3. **Config** — health, ngrok, firewall

## API (:8765)

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

- **Container novo** → aparece na GUI com toggle **OFF**
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
  app.py                   # GUI
data/                      # redirections.json, aliases, blocklist
```

## Aviso

Uso em redes próprias. MITM + firewall requer Linux com Docker host network.
