# Contribuindo ao N8Groker

Repositório: **https://github.com/Zidszs/N8Groker-POC**

## Duas branches

| Branch | O que é |
| --- | --- |
| `master` | Stack clássica: n8n, Porteiro, ngrok e Scout. A tecla G abre a janela Tk. |
| `control-plane-plus` | A mesma stack com o Control Plane (Infraestrutura, Scout, Chat e, no console, a aba Admin), diagnóstico, backup, contas e JWT de admin. A tecla G abre `http://localhost:8501/?aba=scout`. Não há janela Tk. |

O `master` não recebe merge deste ramo. Cada um segue o seu histórico. Trabalho novo do painel entra em `control-plane-plus`.

## Primeira execução

```text
git clone https://github.com/Zidszs/N8Groker-POC.git N8Groker
cd N8Groker
Setup.bat                  # dependências, .env, rede Docker, pastas
iniciar_servicos.ps1       # console 8501, borda 8502, Porteiro, ngrok, Scout, LLM, n8n
```

Numa cópia de teste, `factory_reset.bat` (confirme com `Excluir`) simula instalação limpa. Use só numa cópia.

A conta do produto n8n, em `http://127.0.0.1:5678`, é outra coisa que a sessão admin do painel. O painel não tem senha de admin:

```text
python -m control_plane.admin_token --init
python -m control_plane.admin_token
```

Cole o JWT no campo **Token de admin** de `http://localhost:8501`. A aba Admin cria os usuários, sem senha, e emite o token de acesso.

No n8n, uma vez: importe `Workflows_para_Autenticação/Aprovacao de Acesso (Novo).json`, configure o SMTP, edite `admin_email` e ative o workflow. `porteiro_url` fica `http://host.docker.internal:5677`. As rotas `/n8n/*` não atendem pelo domínio do ngrok.

Guia de entrada: [`README.md`](README.md). Diagramas: [`DOCUMENTACAO.md`](DOCUMENTACAO.md), seção 16.

## Testes

Na raiz, com as dependências de `control_plane/requirements.txt`:

```bash
python -m pytest tests
```

A CI (`.github/workflows/ci.yml`) faz isso no Ubuntu. No Windows ela confere só `iniciar_servicos.ps1`, com PowerShell 7:

1. O Parser (`[System.Management.Automation.Language.Parser]::ParseFile`) tem de voltar sem erro.
2. O PSScriptAnalyzer 1.25.0, só com `-Severity Error`. Aviso e informação não falham o job. Se a PSGallery já existe, o job não registra de novo. Se falta, `Register-PSRepository -Default` e o `Install-Module` repetem, e o módulo entra em cache. Falha de instalação falha o job, com a mensagem de que a análise não foi executada. A análise não é pulada.

## Licença

O código deste repositório é 0BSD (Zero-Clause BSD). O texto oficial está em [`LICENSE`](LICENSE), com copyright `2026 Gabriel Antunes`. O uso é livre e não exige atribuição. A nota de comunidade no README não muda essa licença.

n8n, Ollama, ngrok, Langfuse e LiteLLM não são redistribuídos aqui. Entram como imagem oficial baixada na subida, ou, no caso do Ollama, como o programa oficial na máquina. A licença de cada um está na seção Licença do [`README.md`](README.md). Não copie esses textos para o `LICENSE` deste projeto.

## O que não commitar

Nunca commitar:

- `.env` e qualquer segredo (token, senha, chave de API)
- chaves (`.n8groker/admin.key`, `admin.pub`, `porteiro-hmac.key` e o par anterior)
- venv (`control_plane/.venv/`, `.venv/`)
- `.n8groker/` (contas, auditoria, backups, diagnóstico, PID, IP do container)

Também ficam de fora `n8n/data/`, `n8n/n8n/data/*`, `n8n/n8n/storage/`, `n8n/storage/Porteiro/*`, o conteúdo de `Arquivos-n8n/` (ficam o `.gitkeep` e o `README.md`), `Scout_OSINT_Docker/data/`, logs, `*.sqlite*`, `*.key`, `*.pem` e `.env.*`. O `.gitignore` já cobre esses caminhos. Antes do push: `git status`.

## Toda mudança atualiza o README

Qualquer alteração de comportamento, porta, comando ou fluxo de boot atualiza o `README.md` no mesmo commit. Não deixe o guia apontar para uma porta ou um comando que o código já não usa.

## Pull requests

1. Faça fork de https://github.com/Zidszs/N8Groker-POC
2. Crie uma branch a partir da versão que você está mudando (`master` ou `control-plane-plus`)
3. Descreva o problema ou a melhoria no PR
4. Confirme que o diff não tem `.env`, segredo, chave, venv nem `.n8groker/`
5. Diga como testou: `python -m pytest tests` e, se mexeu em `iniciar_servicos.ps1`, o Parser e o ScriptAnalyzer
