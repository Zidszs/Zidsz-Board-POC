# N8Groker

Ambiente de automação que integra **n8n**, **Docker**, **Ngrok**, **Porteiro** e opcionalmente **Scout Gate** — para expor workflows na internet com controle de acesso por IP, aprovação via e-mail e observabilidade na borda.

Documentação técnica completa: [`DOCUMENTACAO.md`](DOCUMENTACAO.md)

---

## Visão geral
Propósito do Projeto: Este ambiente foi desenvolvido estritamente para estudo, treinamento e práticas de segurança em uma infraestrutura pessoal. Ele serve como um laboratório prático para aplicar conceitos de Zero Trust, proxy reverso, controle de acesso e observabilidade na borda, não sendo voltado para ambientes de produção corporativos.
O N8Groker elimina a configuração manual de roteador, DNS e reverse proxy. Um visitante externo chega via Ngrok, passa pelo Scout (opcional) e pelo Porteiro (proxy Node.js) antes de alcançar o n8n. IPs desconhecidos ficam em fila até aprovação humana via workflow n8n.

```text
Internet → Ngrok → Scout (:4050, opcional) → Porteiro (:5677) → n8n (:5678)
```

Modo legado (`USE_SCOUT=0`): Ngrok aponta directo para o Porteiro.

---

## Início rápido (máquina já configurada)

| Passo | Ação |
|-------|------|
| 1 | Copiar `.env_template` → `.env` (ou deixar o **Setup.bat** / **factory_reset.bat** criar) |
| 2 | Preencher `NGROK_AUTHTOKEN`, `N8N_ENCRYPTION_KEY` e, se quiser, `PORTEIRO_TOKEN` |
| 3 | Executar **`Setup.bat`** — prepara a base (não sobe serviços) |
| 4 | Executar **`iniciar_servicos.ps1`** — sobe Porteiro, n8n, ngrok, Scout |
| 5 | Configurar o n8n (conta, workflow, SMTP) — ver checklist abaixo |
| 6 | Com Scout: tecla **`G`** no HUD abre a GUI |

A rede Docker `rede_comunicacao` é criada automaticamente pelo **Setup.bat** (opção auto-config). Só crie manualmente se o Setup não rodou:

```powershell
docker network create rede_comunicacao
```

---

## Primeira instalação (após factory reset)

Use esta ordem numa **cópia limpa** do projeto:

```text
factory_reset.bat  →  Setup.bat  →  iniciar_servicos.ps1  →  n8n (manual)
```

| # | O quê | Quem faz |
|---|--------|----------|
| 1 | **`factory_reset.bat`** — digite `Excluir`; opcional **S** para criar `.env` | Você (só na cópia de teste) |
| 2 | **`Setup.bat`** — dependências, `.env`, rede Docker, pastas | Script + você (tokens no `.env`) |
| 3 | **`iniciar_servicos.ps1`** — sobe toda a stack | Script |
| 4 | **n8n** — checklist abaixo | Você |

### Checklist n8n (obrigatório para aprovação por IP)

1. Abrir `http://localhost:5678` e **criar a conta admin** (primeiro acesso após reset).
2. **Importar** `Workflows_para_Autenticação/Aprovacao de Acesso (Novo).json`.
3. No node **Configuracoes**:
   - `admin_email` → seu e-mail de aprovador (não deixe `voce@exemplo.com`).
   - `admin_token` → vazio **ou** igual ao `PORTEIRO_TOKEN` do `.env` (ver nota abaixo).
   - `porteiro_url` → `http://host.docker.internal:5677` (já vem correto).
4. No node **Email e Espera Aprovacao** → configurar **credencial SMTP**.
5. **Ativar** o workflow (no JSON vem `"active": false`).

### `PORTEIRO_TOKEN` (opcional)

- No `.env` serve de referência e para copiar para o workflow.
- O Porteiro lê `process.env.PORTEIRO_TOKEN` — **não carrega o `.env` sozinho**.
- Com `admin_token` **vazio** no workflow e sem token no processo Node, as rotas `/n8n/aprovar` e `/n8n/bloquear` funcionam para chamadas vindas do container n8n (IP interno Docker).

---

## O que o Setup faz e o que não faz

| Setup **faz** | Setup **não faz** |
|---------------|-------------------|
| Verifica Docker, Node, Python (Scout), portas | Instalar Docker / Node / Python (só abre links) |
| Cria `.env`, rede `rede_comunicacao`, pastas de dados | Subir containers (isso é o `iniciar_servicos.ps1`) |
| venv + pip do Scout se `USE_SCOUT=1` | Criar conta no n8n |
| Abre `.env` para você preencher tokens | Importar ou ativar workflows |
| Pode perguntar se inicia o `iniciar_servicos.ps1` | Configurar SMTP no n8n |

**Resumo:** o Setup deixa a **base técnica pronta**; o fluxo de aprovação exige configuração **dentro do n8n**.

---

## Scripts operacionais

| Script | Função |
|--------|--------|
| **`Setup.bat`** | Checklist interactivo de dependências: auto-config (`.env`, rede Docker, pastas, venv Scout), links de instalação ou ignorar por item. Não sobe serviços por defeito (pode perguntar no fim se inicia o orquestrador). |
| **`iniciar_servicos.ps1`** | Orquestrador principal: boot, HUD, sync URL ngrok, Scout, shutdown (**Q**). |
| **`factory_reset.bat`** | Apaga dados voláteis (n8n DB, Porteiro JSON, Scout data, `.env`). Confirmação digitando `Excluir`; opcional **S** recria `.env` do template. Usar **só numa cópia** do projeto. |
| **`Scout_OSINT_Docker/setup.bat`** | Setup isolado do Scout (venv + container + GUI standby). |

---

## Componentes da stack

### n8n (Docker)
Motor de automação. Workflows de aprovação por e-mail, webhooks e credenciais. Dados em `n8n/n8n/data/` (SQLite).

### Ngrok (Docker)
Túnel HTTPS público. Dashboard local em `:4040`. Upstream configurado pelo script (`4050` com Scout ou `5677` sem Scout).

### Porteiro (Node.js, host)
Proxy reverso na porta **5677**. Firewall Zero Trust por IP, fila de acesso, rate limit, rotas admin protegidas. Persistência em `n8n/storage/Porteiro/` (paths **relativos** ao projeto — portátil).

### Scout Gate (Docker + GUI, opcional)
Camada OSINT/MITM na porta **4050**. API admin **8765**.

| Recurso | Descrição |
|---------|-----------|
| Redireccionamentos | Toggles por rota/container; modo tcp/http/https (persistido) |
| Tráfego | Log de sessões em tempo real |
| **Clientes** | Lista persistente de IPs vistos (`known_clients.json`) — alias sem vigiar o log |
| Aliases IP | Nome manual → IP confiável (sem alertas nem blocklist na borda) |
| Blocklist | Bloqueio na borda MITM |
| Alertas | Tráfego elevado, bloqueios (ignorados para IPs com alias) |

### Orquestrador (PowerShell)
`iniciar_servicos.ps1`: valida Node/Docker/`.env`, sobe Porteiro + containers, HUD com fila Porteiro, sync `WEBHOOK_URL`, teclas **G** (GUI Scout) e **Q** (shutdown). Fechar GUI Scout com **Sim** encerra toda a infra via `.n8groker.shutdown.request`.

---

## Portas

| Porta | Serviço |
|-------|---------|
| `4050` | Scout MITM (tráfego público via Ngrok, com `USE_SCOUT=1`) |
| `4040` | Ngrok dashboard/API |
| `8765` | Scout API + WebSocket GUI |
| `5677` | Porteiro |
| `5678` | n8n |

---

## Workflow n8n (aprovação por IP)

Importe **apenas** este arquivo:

`Workflows_para_Autenticação/Aprovacao de Acesso (Novo).json`

Fluxo: Porteiro detecta IP novo → webhook → e-mail com Aprovar/Bloquear → Porteiro libera ou bloqueia o acesso.

Detalhes de configuração: seção **Primeira instalação** acima e [`DOCUMENTACAO.md`](DOCUMENTACAO.md) seções 7 e 16.

---

## Dados e persistência

| Local | Conteúdo |
|-------|----------|
| `n8n/n8n/data/` | Base SQLite n8n e credenciais. O compose monta isto em `/home/node/.n8n`. O caminho `n8n/n8n/` é estranho de propósito: o ficheiro está em `n8n/` e o volume é `./n8n/data`. |
| `n8n/storage/Porteiro/` | `controle_acesso.json` e logs do Porteiro, no host. O n8n não lê esta pasta. |
| `Arquivos-n8n/` | Arquivos que os fluxos leem e gravam. No nó Read/Write Files from Disk: `/home/node/Arquivos-n8n/entrada.csv`. |
| `Scout_OSINT_Docker/data/` | Rotas, aliases, clientes, blocklist |
| `.env` | Segredos (não vai para GitHub) |

`N8N_RESTRICT_FILE_ACCESS_TO` no compose aponta só para `/home/node/Arquivos-n8n`. `N8N_BLOCK_FILE_ACCESS_TO_N8N_FILES=true` mantém `/home/node/.n8n` bloqueado. Na imagem `n8n:latest` (2.0+) o padrão seria `~/.n8n-files`; aqui o caminho é explícito.

**Factory reset:** `factory_reset.bat` limpa a base n8n, o estado do Porteiro e os dados do Scout, e recria essas pastas vazias. **Não** apaga `Arquivos-n8n/` (são arquivos do usuário). Apagar só imagens Docker **não** reseta a base n8n (dados ficam no disco).

**Portabilidade:** mover/copiar a pasta do projeto funciona; caminhos são relativos (`$PSScriptRoot`, `__dirname`). Copiar a pasta inteira incluindo dados se quiser manter IPs aprovados.

---

## Scout — configuração correcta (resumo)

- Ngrok → `:4050` (Scout)
- Rota **porteiro** ON: listen `:4050` → upstream `host.docker.internal:5677`, modo **tcp**
- Só **uma** rota activa na porta pública (não activar `n8n_app` directo — bypassa Porteiro)
- Modo tcp/http/https guardado em `redirections.json` (não reinicia no boot)

Detalhes: [`DOCUMENTACAO.md`](DOCUMENTACAO.md) secções 1 e 7.

---

## Requisitos

### Hardware
Virtualização habilitada na BIOS (Intel VT-x / AMD SVM) — necessária para Docker/WSL2.

### Software

| Dependência | Obrigatório | Notas |
|-------------|-------------|-------|
| Docker Desktop + WSL2 | Sim | n8n, Ngrok, Scout |
| Node.js LTS (16+) | Sim | Porteiro |
| Python 3.10+ | Se `USE_SCOUT=1` | GUI Scout. Nenhum módulo exige 3.11; a imagem Docker do Scout já é 3.11 |
| PowerShell 5.1+ | Sim | Scripts `.ps1` |
| Conta Ngrok (Authtoken) | Sim | `.env` |

**Primeira vez (cópia limpa):** `factory_reset.bat` → **`Setup.bat`** → `iniciar_servicos.ps1` → checklist n8n (secção **Primeira instalação**). Máquina já configurada: Setup + `iniciar_servicos.ps1` basta.

ExecutionPolicy (se necessário):

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Rede
- Rede Docker `rede_comunicacao` — o **Setup.bat** cria automaticamente; manual só se necessário
- Portas livres: 5677, 5678, 4040, 4050, 8765

---

## Estrutura do repositório

```text
N8Groker/
├── Setup.bat / setup_projeto.ps1    # Preparar ambiente
├── factory_reset.bat / .ps1         # Reset de dados (cópia do projeto)
├── iniciar_servicos.ps1             # Orquestrador + HUD
├── .env_template                    # Modelo de configuração
├── Arquivos-n8n/                    # Arquivos dos fluxos n8n
├── Porteiro/porteiro.js
├── n8n/docker-compose.yml
├── ngrok/docker-compose.yml
├── Scout_OSINT_Docker/              # Scout Gate (Docker + GUI)
├── Workflows_para_Autenticação/
├── README.md
└── DOCUMENTACAO.md
```

---

## Encerramento

| Acção | Efeito |
|-------|--------|
| **Q** no HUD PowerShell | Shutdown seguro (Ngrok, n8n, Scout, Porteiro) |
| Fechar GUI Scout → **Sim** | Mesmo shutdown via ficheiro de pedido |
| Fechar GUI Scout → **Não** | Só fecha a janela |

---

## Licença e suporte

Consulte [`DOCUMENTACAO.md`](DOCUMENTACAO.md) para diagramas, fluxos de aprovação, variáveis de ambiente, segurança Zero Trust e recomendações operacionais.
