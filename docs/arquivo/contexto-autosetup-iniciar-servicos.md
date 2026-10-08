# Contexto para o agente implementador

Este arquivo é o único artefato que sai desta máquina. Esta pasta (`D:\New_N8groker_test`) é campo de teste. Nada de código de produto é commitado nem enviado daqui.

Quem implementa é o Grok, depois, na branch `cursor/control-plane-llm-stack-e9b5` (pull request [#1](https://github.com/Zidszs/N8Groker-POC/pull/1)). O ponto de partida commitado é `6e0b29ce33808c4530995a6092f5469348621d5e` (`6e0b29c`, "Remove bytecode do Python e ignora __pycache__.").

Merge deste markdown não é o trabalho pronto. O Grok aplica o comportamento e as correções na branch de feature. Não commitar `.env`, segredo, nem `venv`. Não subir a stack numa VM de nuvem só para provar pull de imagem.

Há três seções, com papéis diferentes:

1. Caderno do teste — evidência do que esta máquina tinha e do que foi visto. Caminhos `C:\Users\<usuario>\...` ficam aqui como evidência. Não são requisito de código.
2. Patch candidato — o diff local não commitado que deixou Langfuse e LiteLLM saudáveis. É candidato verificado, não um commit desta máquina.
3. Critério de aceite do auto-setup — o que a primeira execução de `iniciar_servicos.ps1` tem de fazer em qualquer máquina, e o ciclo de reteste.

---

## 1. Caderno do teste

Evidência desta sessão. Não copiar estes caminhos para o código como se fossem o contrato do produto.

### Máquina e ponto de partida

- Windows 10, PowerShell, pasta `D:\New_N8groker_test`.
- Commit de partida: `6e0b29ce33808c4530995a6092f5469348621d5e`.
- Já presentes: Docker Desktop, Git, Node v22, npm, Docker CLI 29, docker-compose, winget, WSL2.
- `python` no PATH era o alias da Microsoft Store, não um Python real. Nesta sessão o alias ainda responde com o texto da Store e código 9009 quando se chama `python` sem o executável instalado.

### O que foi instalado ou preparado aqui

- Python 3.12.10 via winget, pacote `Python.Python.3.12`, escopo de usuário.
  - `python.exe`: `C:\Users\<usuario>\AppData\Local\Programs\Python\Python312\python.exe`
  - `py.exe`: `C:\Users\<usuario>\AppData\Local\Programs\Python\Launcher\py.exe`
- O PATH de usuário precisou, antes de `WindowsApps`:
  - `%LOCALAPPDATA%\Programs\Python\Python312\Scripts\`
  - `%LOCALAPPDATA%\Programs\Python\Python312\`
  - `%LOCALAPPDATA%\Programs\Python\Launcher\`
- Terminal já aberto não enxerga atualização de PATH. O Cursor herda o PATH do momento em que o processo do Cursor subiu. Por isso `py` pode continuar invisível depois do winget.
- `Scout_OSINT_Docker\.venv` com `requests` e `websocket-client` (`Scout_OSINT_Docker\requirements.txt`).
- `control_plane\.venv` com Streamlit e pytest (`control_plane\requirements.txt`: `streamlit>=1.39`, `pytest>=8.0`).
- `.env` copiado de `.env.example`. `scripts\init_env.py` chama `control_plane.envfile.ensure_env`, troca marcadores `__GENERATE_*__` e não rotaciona valor que já saiu do placeholder (rotacionar `ENCRYPTION_KEY` do Langfuse ou do n8n inutiliza o banco). `NGROK_AUTHTOKEN` não é gerado.

### O que o script faz hoje (HEAD `6e0b29c`)

`iniciar_servicos.ps1` não instala ferramenta, não sobe o Docker Desktop e não cria a rede.

- `Invoke-Compose` (por volta das linhas 117–131) força `ErrorActionPreference = SilentlyContinue` e descarta o stderr: `docker-compose ... 2>$null | Out-Null`. Só devolve `$LASTEXITCODE`.
- `Stop-Tudo` (por volta da linha 340) começa com `Clear-Host`. O `trap` e o `finally` chamam `Stop-Tudo`. Uma falha de pull parece desligamento limpo: a tela apaga o erro e o texto final é "Desligamento concluido com sucesso!".
- Checagem de Node: se `node -v` falha, o script pede para instalar em https://nodejs.org e sai. Não instala.
- Checagem de Docker: `docker info`. Se o motor está parado, pede para abrir o Docker Desktop e sai. Não espera.
- Sem `.env`, pede Setup ou `python scripts/init_env.py` e sai. Não copia o exemplo.
- `Find-ScoutPython` / `Find-HostPython` em `setup_projeto.ps1` olham `py` e `python` no PATH. Não olham `%LOCALAPPDATA%\Programs\Python\...`. O `python` da Store passa no `Get-Command` e falha na execução.
- `Test-LlmConfigured` só sobe Langfuse/LiteLLM se estas chaves existem e não são `__GENERATE_*__`: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LITELLM_MASTER_KEY`, `LANGFUSE_DB_PASSWORD`, `ENCRYPTION_KEY`, `N8N_CREDENTIALS_OVERWRITE_DATA`.
- Ordem atual de subida: Porteiro (`node porteiro.js` em `Porteiro\`), Scout se `USE_SCOUT=1`, Langfuse/LiteLLM, n8n, ngrok. HUD permanece. `Q` chama `Stop-Tudo`, que inclui `docker-compose down` de `llm\docker-compose.yml` quando a stack LLM foi marcada como em uso.
- A rede `rede_comunicacao` está `external: true` em `llm\docker-compose.yml`. O Setup (`setup_projeto.ps1`) sabe criá-la. O `iniciar_servicos.ps1` não cria.

Comando que subiu a stack LLM nesta máquina, com o compose já corrigido no worktree (correção não commitada):

```text
docker-compose -f llm\docker-compose.yml --env-file .env up -d
```

O motor Docker precisa estar no ar antes desse comando. A rede `rede_comunicacao` precisa existir antes do compose.

### Por que Langfuse e LiteLLM não subiram

Três falhas em sequência no `llm\docker-compose.yml` do commit `6e0b29c`. O stderr escondido e o `Clear-Host` do desligamento fizeram o pull falho parecer shutdown limpo.

1. MinIO `quay.io/minio/minio:RELEASE.2025-10-15T17-29-55Z` devolve 401 UNAUTHORIZED no pull anônimo. A mesma tag no Docker Hub também exige login. O compose aborta o pull inteiro. O ClickHouse não termina.
2. Com o MinIO trocado, o `langfuse-web` roda as migrações e sai com `Invalid environment variables: LANGFUSE_INIT_USER_EMAIL`. O Langfuse 4.30 rejeita `admin@localhost`. O default que passou foi `admin@example.com`.
3. Healthcheck: o Docker define `HOSTNAME` com o id do container. O Next escuta só nesse IP. O healthcheck faz fetch em `127.0.0.1` e recebe `ECONNREFUSED`. O `langfuse-web` nunca fica healthy. O serviço `litellm` tem `depends_on.langfuse-web.condition: service_healthy` e permanece `Created`.

O healthcheck do web está em `fetch('http://127.0.0.1:3000/api/public/health')`. O do worker está em `fetch('http://127.0.0.1:3030/api/health')`. Os dois serviços herdam o âncora YAML `x-langfuse-env`.

### O que ficou saudável depois do patch local (não commitado)

`docker inspect langfuse-minio --format "{{.Config.Image}}"` devolveu a mesma referência do worktree:

```text
cgr.dev/chainguard/minio@sha256:4692462f35d97d7e82c30371d82f057703c5d9489bcae726010594c812f2d285
```

Containers saudáveis nesta máquina após o patch: `langfuse-web` (`:3000`), `langfuse-worker` (`127.0.0.1:3030`), `langfuse-minio` (`127.0.0.1:9090`), `langfuse-postgres`, `langfuse-clickhouse`, `langfuse-redis`, `litellm` (`:4000`), `litellm-db`.

HTTP 200 observado:

- `http://localhost:3000/`
- `http://localhost:3000/api/public/health`
- `http://localhost:4000/ui`
- `http://localhost:4000/health/liveliness`

Login do Langfuse: `admin@example.com` mais o valor local de `LANGFUSE_INIT_USER_PASSWORD`. Login do LiteLLM: `LITELLM_UI_USERNAME` / `LITELLM_UI_PASSWORD`, ou a master key. Os valores não entram neste arquivo.

n8n, ngrok, Scout e Porteiro ficaram parados nesse ensaio. `Q` no HUD também derruba Langfuse e LiteLLM.

Modelo, host do Ollama e chaves de provider não estão no compose. Entram depois na UI do LiteLLM em `http://localhost:4000/ui`. A credencial OpenAI do n8n aponta para `http://litellm:4000/v1` (`control_plane.envfile.n8n_overwrite`).

### Estado do `.env` local (só nomes)

Comparação do `.env` desta máquina com o `.env.example` do worktree. Nenhum valor foi copiado para cá.

Já saíram do placeholder (não rotacionar): `PORTEIRO_USER`, `PORTEIRO_PASS`, `N8N_ENCRYPTION_KEY`, `NGROK_AUTHTOKEN`, `PORTEIRO_TOKEN`, `NEXTAUTH_SECRET`, `SALT`, `ENCRYPTION_KEY`, `LANGFUSE_DB_PASSWORD`, `CLICKHOUSE_PASSWORD`, `REDIS_AUTH`, `MINIO_ROOT_PASSWORD`, `LANGFUSE_INIT_USER_PASSWORD`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LITELLM_DB_PASSWORD`, `LITELLM_MASTER_KEY`, `LITELLM_SALT_KEY`, `LITELLM_UI_PASSWORD`, `N8N_CREDENTIALS_OVERWRITE_DATA`.

Iguais ao default do exemplo do worktree (não são segredo inventado nesta lista): `USE_SCOUT`, `SCOUT_PUBLIC_PORT`, `SCOUT_ADMIN_PORT`, `SCOUT_UPSTREAM_HOST`, `SCOUT_UPSTREAM_PORT`, `SCOUT_DEFAULT_ROUTE_MODE`, `SCOUT_LISTEN_PORT_START`, `SCOUT_ENFORCE_FIREWALL`, `SCOUT_BACKEND_URL`, `NEXTAUTH_URL`, `TELEMETRY_ENABLED`, `LANGFUSE_DB_USER`, `LANGFUSE_DB_NAME`, `CLICKHOUSE_USER`, `MINIO_ROOT_USER`, `LANGFUSE_INIT_ORG_ID`, `LANGFUSE_INIT_ORG_NAME`, `LANGFUSE_INIT_PROJECT_ID`, `LANGFUSE_INIT_PROJECT_NAME`, `LANGFUSE_INIT_USER_EMAIL` (já `admin@example.com` no worktree local), `LANGFUSE_INIT_USER_NAME`, `LITELLM_DB_USER`, `LITELLM_DB_NAME`, `LITELLM_UI_USERNAME`.

Vazio: `SCOUT_NGROK_TUNNEL_URL`. O HUD do `iniciar_servicos.ps1` grava essa chave quando o túnel em `http://localhost:4040/api/tunnels` aparece. Não pedir uma URL pública no primeiro boot.

No commit `6e0b29c`, `.env.example` e `.env_template` ainda dizem `admin@localhost`. A troca para `admin@example.com` está só no worktree, dentro do patch da seção 2.

---

## 2. Patch candidato

Isto não é um commit desta máquina. É o diff não commitado do worktree, conferido contra o HEAD `6e0b29c`, que deixou Langfuse e LiteLLM saudáveis. O Grok aplica o equivalente na branch `cursor/control-plane-llm-stack-e9b5`. Não trazer este worktree como se já estivesse no Git.

O diff não contém segredo. `.env`, senhas, tokens e `venv` ficam de fora. A troca de `entrypoint` de `/bin/sh` para `sh` faz parte do mesmo diff verificado: a imagem Chainguard não usa `/bin/sh`.

`HOSTNAME: "0.0.0.0"` entra uma vez no âncora `x-langfuse-env`. `langfuse-web` e `langfuse-worker` herdam esse âncora (`<<: *langfuse-env`). Os dois passam a escutar em todas as interfaces, e o healthcheck em `127.0.0.1` consegue conectar.

Fora deste diff: a tabela do README (serviço Langfuse) ainda cita MinIO `RELEASE.2025-10-15T17-29-55Z`. Essa frase não foi editada no ensaio. Ao aplicar o patch, atualizar essa frase para a imagem com digest, senão a documentação rastreada continua apontando a tag que devolve 401.

```diff
diff --git a/.env.example b/.env.example
index ac78ce5..4d44128 100644
--- a/.env.example
+++ b/.env.example
@@ -67,7 +67,7 @@ LANGFUSE_INIT_ORG_ID=n8groker
 LANGFUSE_INIT_ORG_NAME=N8Groker
 LANGFUSE_INIT_PROJECT_ID=n8groker
 LANGFUSE_INIT_PROJECT_NAME=N8Groker
-LANGFUSE_INIT_USER_EMAIL=admin@localhost
+LANGFUSE_INIT_USER_EMAIL=admin@example.com
 LANGFUSE_INIT_USER_NAME=Admin
 LANGFUSE_INIT_USER_PASSWORD=__GENERATE_ALNUM_24__
 
diff --git a/.env_template b/.env_template
index ac78ce5..4d44128 100644
--- a/.env_template
+++ b/.env_template
@@ -67,7 +67,7 @@ LANGFUSE_INIT_ORG_ID=n8groker
 LANGFUSE_INIT_ORG_NAME=N8Groker
 LANGFUSE_INIT_PROJECT_ID=n8groker
 LANGFUSE_INIT_PROJECT_NAME=N8Groker
-LANGFUSE_INIT_USER_EMAIL=admin@localhost
+LANGFUSE_INIT_USER_EMAIL=admin@example.com
 LANGFUSE_INIT_USER_NAME=Admin
 LANGFUSE_INIT_USER_PASSWORD=__GENERATE_ALNUM_24__
 
diff --git a/README.md b/README.md
index a431156..462a57e 100644
--- a/README.md
+++ b/README.md
@@ -116,7 +116,7 @@ Como eles se falam, sem modelo pré-cadastrado:
 - O n8n recebe `CREDENTIALS_OVERWRITE_DATA` para a credencial do tipo `openAiApi`: base `http://litellm:4000/v1` e a master key do proxy. No editor, crie uma credencial OpenAI; a URL e a chave vêm desse overwrite.
 - Provider, host do Ollama e chave de LLM **não** vão no compose nem no `.env`. Cadastre na UI do LiteLLM (`http://localhost:4000/ui`).
 
-Login inicial do Langfuse: `admin@localhost` e `LANGFUSE_INIT_USER_PASSWORD` do `.env`. Login do LiteLLM: `LITELLM_UI_USERNAME` / `LITELLM_UI_PASSWORD`, ou a `LITELLM_MASTER_KEY`.
+Login inicial do Langfuse: `admin@example.com` e `LANGFUSE_INIT_USER_PASSWORD` do `.env`. Login do LiteLLM: `LITELLM_UI_USERNAME` / `LITELLM_UI_PASSWORD`, ou a `LITELLM_MASTER_KEY`.
 
 O `iniciar_servicos.ps1` sobe essa stack junto com o resto quando as chaves existem. Se o `.env` for antigo e não tiver as chaves, o script avisa e segue com n8n, ngrok, Scout e Porteiro. `docker compose down` dessa stack **não** apaga volume; o factory reset usa `down -v` só em `llm/docker-compose.yml`.
 
diff --git a/control_plane/app.py b/control_plane/app.py
index 89e0980..dfb7a96 100644
--- a/control_plane/app.py
+++ b/control_plane/app.py
@@ -274,7 +274,7 @@ def main() -> None:
 - **LiteLLM → Langfuse:** callback `langfuse_otel` para `http://langfuse-web:3000`, com as chaves do headless init.
 - **n8n → LiteLLM:** credencial OpenAI (`openAiApi`) aponta para `http://litellm:4000/v1`.
 - **Modelos:** nenhum provider vem pronto. Cadastre na UI do LiteLLM.
-- **Login Langfuse:** e-mail `admin@localhost` e a senha `LANGFUSE_INIT_USER_PASSWORD` do `.env`.
+- **Login Langfuse:** e-mail `admin@example.com` e a senha `LANGFUSE_INIT_USER_PASSWORD` do `.env`.
 - **Login LiteLLM:** usuário `LITELLM_UI_USERNAME` ou a `LITELLM_MASTER_KEY` do `.env`.
             """
         )
diff --git a/llm/docker-compose.yml b/llm/docker-compose.yml
index a619934..ef71af9 100644
--- a/llm/docker-compose.yml
+++ b/llm/docker-compose.yml
@@ -5,7 +5,9 @@
 #   Langfuse 4.30.0 — docker.io/langfuse/langfuse e langfuse-worker
 #     (o compose oficial usa a tag flutuante :4; aqui fica o patch 4.30.0)
 #   ClickHouse 25.12.11, Postgres 17.11, Redis 7.4.11
-#   MinIO RELEASE.2025-10-15T17-29-55Z
+#   MinIO: a tag pinada quay.io/minio/minio:RELEASE.2025-10-15T17-29-55Z
+#   devolve 401 (Quay e Docker Hub exigem login). Imagem pública usada pelo
+#   compose atual do Langfuse: cgr.dev/chainguard/minio, digest fixo.
 #   LiteLLM v1.103.1 — ghcr.io/berriai/litellm
 #     (a doc de deploy pede tag fixa; o exemplo antigo v1.90.2 não é o stable atual)
 #
@@ -27,6 +29,9 @@ x-langfuse-env: &langfuse-env
   SALT: ${SALT:?SALT_ausente}
   ENCRYPTION_KEY: ${ENCRYPTION_KEY:?ENCRYPTION_KEY_ausente}
   TELEMETRY_ENABLED: ${TELEMETRY_ENABLED:-false}
+  # Docker define HOSTNAME com o id do container. O Next usa isso e escuta só
+  # nesse IP, então o healthcheck em 127.0.0.1 falha e o LiteLLM não sobe.
+  HOSTNAME: "0.0.0.0"
   # Ingestão OTLP em tempo real no self-host (senão o trace pode atrasar).
   LANGFUSE_MIGRATION_V4_NATIVE_OTEL_BEHAVIOUR: direct
   CLICKHOUSE_MIGRATION_URL: clickhouse://langfuse-clickhouse:9000
@@ -129,10 +134,10 @@ services:
       - rede_comunicacao
 
   langfuse-minio:
-    image: quay.io/minio/minio:RELEASE.2025-10-15T17-29-55Z
+    image: cgr.dev/chainguard/minio@sha256:4692462f35d97d7e82c30371d82f057703c5d9489bcae726010594c812f2d285
     container_name: langfuse-minio
     restart: unless-stopped
-    entrypoint: ["/bin/sh", "-c"]
+    entrypoint: ["sh", "-c"]
     command: ["mkdir -p /data/langfuse && exec minio server --address :9000 --console-address :9001 /data"]
     environment:
       MINIO_ROOT_USER: ${MINIO_ROOT_USER:-langfuse}
@@ -198,7 +203,7 @@ services:
       LANGFUSE_INIT_PROJECT_NAME: ${LANGFUSE_INIT_PROJECT_NAME:-N8Groker}
       LANGFUSE_INIT_PROJECT_PUBLIC_KEY: ${LANGFUSE_PUBLIC_KEY:?LANGFUSE_PUBLIC_KEY_ausente}
       LANGFUSE_INIT_PROJECT_SECRET_KEY: ${LANGFUSE_SECRET_KEY:?LANGFUSE_SECRET_KEY_ausente}
-      LANGFUSE_INIT_USER_EMAIL: ${LANGFUSE_INIT_USER_EMAIL:-admin@localhost}
+      LANGFUSE_INIT_USER_EMAIL: ${LANGFUSE_INIT_USER_EMAIL:-admin@example.com}
       LANGFUSE_INIT_USER_NAME: ${LANGFUSE_INIT_USER_NAME:-Admin}
       LANGFUSE_INIT_USER_PASSWORD: ${LANGFUSE_INIT_USER_PASSWORD:?LANGFUSE_INIT_USER_PASSWORD_ausente}
     extra_hosts:
```

Não gravar senha real em arquivo rastreado. O default de e-mail nos arquivos versionados é `admin@example.com`. A senha continua o marcador `__GENERATE_ALNUM_24__` no exemplo e o valor já gerado só no `.env` local.

`tests\test_llm_stack.py` exige imagens pinadas sem `:latest` no compose LLM e não cita a tag antiga do MinIO. O digest satisfaz "sem tag flutuante". Vale um assert da referência `cgr.dev/chainguard/minio@sha256:4692462f35d97d7e82c30371d82f057703c5d9489bcae726010594c812f2d285` para o Quay não voltar. Não há assert de `admin@localhost` nesse teste; mesmo assim, nenhum default rastreado pode continuar `admin@localhost`.

---

## 3. Critério de aceite do auto-setup

A primeira execução de `iniciar_servicos.ps1` prepara a estrutura atual e segue até a stack estar no ar. O requisito que não pode ser reduzido: não hardcodar só o que faltou nesta máquina de teste. Antes de subir serviço, descobrir cada ferramenta que o script, o Porteiro, o Scout, o control plane e os compose realmente invocam, testar se ela existe e roda nesta máquina, e, se faltar, oferecer a instalação. Outra máquina pode não ter nenhuma de Git, winget, WSL, Docker Desktop, Docker CLI, compose, Node, npm, Python 3.12, o launcher `py` ou ngrok. A lista do caderno é evidência, não o catálogo fechado.

Varredura mínima para achar o que o boot chama de verdade (reler o código; não parar nesta lista):

- `iniciar_servicos.ps1`: `node` (`Porteiro\porteiro.js`), `docker`, `docker-compose`, `py` / `python` para o venv do Scout.
- `Porteiro\porteiro.js` usa só módulos nativos (`http`, `fs`, `path`). Não há `package.json` nesse diretório. npm só entra se a varredura achar uma invocação real.
- Scout: `docker-compose up -d --build` em `Scout_OSINT_Docker\docker-compose.yml`; GUI com o Python do `.venv` e `requests` / `websocket-client`.
- Control plane: `streamlit run control_plane/app.py` no venv; `control_plane\operations.py` chama `docker-compose` (ou `docker compose`) e `node`.
- Compose: motor Docker, rede externa `rede_comunicacao`, imagens. O ngrok do projeto é o container `ngrok/ngrok`, não um binário no host, salvo se algum script chamar `ngrok` fora do compose.
- `scripts\init_env.py` precisa de um Python real, não do alias da Store.

Prompts em português. Dizer o nome da ferramenta e por que a subida precisa dela.

- Sim: instalar (winget em escopo de usuário quando o pacote existe; caminho oficial quando não existe), atualizar o PATH da sessão atual e o PATH do usuário, testar o comando de novo, continuar.
- Não: parar e listar o que ainda falta, com o motivo.

Não esconder o stderr do compose. Pull ou `up` que falha mostra o erro. Não pode parecer desligamento limpo. O `Clear-Host` de `Stop-Tudo` não pode apagar a causa antes de a pessoa ler.

Estados que não são "executável ausente" e mesmo assim travam a subida. Perguntar em português e, no sim, resolver e seguir:

- Docker Desktop instalado e motor parado: pedir para abrir, iniciar o Docker Desktop, esperar até `docker info` funcionar, continuar.
- Rede `rede_comunicacao` ausente: pedir para criar (`docker network create rede_comunicacao`). O compose marca a rede como external.
- Venv do Scout ou do control plane ausente quando aquele fluxo vai usá-lo: pedir para criar e instalar as dependências daquele projeto (`requirements.txt` correspondente).
- `.env` ausente: copiar de `.env.example` e rodar `scripts\init_env.py` para os `__GENERATE_*__`.
- Valor ainda igual ao default de exemplo, vazio ou placeholder (`NGROK_AUTHTOKEN`, `PORTEIRO_USER`, `PORTEIRO_PASS` e qualquer outro nessa situação): perguntar e gravar no `.env` local o que a pessoa informar. Não inventar token. Não imprimir o segredo de volta. Não rotacionar chave que já saiu do placeholder. Marcador com gerador em `control_plane\envfile.py` continua passando por `scripts\init_env.py`, não por um segredo digitado à mão. `LANGFUSE_INIT_USER_EMAIL` default é `admin@example.com`, não `admin@localhost`. `SCOUT_NGROK_TUNNEL_URL` vazio espera o HUD; não pedir URL pública no primeiro boot.

Ordem de subida que permanece: Porteiro, Scout se `USE_SCOUT=1`, Langfuse/LiteLLM, n8n, ngrok. O HUD fica. `Q` para tudo, inclusive Langfuse e LiteLLM. Modelo, host do Ollama e chaves de provider ficam fora do compose; configuram-se depois na UI do LiteLLM em `http://localhost:4000/ui`. A credencial OpenAI do n8n aponta para `http://litellm:4000/v1`.

O resultado tem de permitir que a primeira execução local de `iniciar_servicos.ps1` ache uma ferramenta faltante, ofereça instalar e, se o compose falhar, mostre a causa. Não provar isso subindo a stack numa VM de nuvem.

### Ciclo de reteste

Depois que o Grok implementar na branch `cursor/control-plane-llm-stack-e9b5` / PR #1, esta máquina puxa essa branch e testa de novo. O merge deste arquivo de documentação não encerra o trabalho. O aceite é o script, nesta máquina de teste, cobrindo ferramenta ausente, os bloqueios que não são executável, o patch da stack LLM e um erro de compose visível.
