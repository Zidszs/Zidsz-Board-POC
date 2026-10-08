# Control Plane

Painel Streamlit para ver se a infra local está no ar e disparar `docker compose` sem tirar o `iniciar_servicos.ps1` do lugar. O HUD do PowerShell continua dono do sync da URL do ngrok e do desligamento com a tecla **Q**.

## Rodar

O `iniciar_servicos.ps1` sobe este painel no mesmo boot, depois do venv, em segundo plano. O console abre em `http://localhost:8501` e escuta só em `127.0.0.1:8501`. A borda sobe no mesmo boot em `127.0.0.1:8502`, com `baseUrlPath=painel`, e só o Porteiro a alcança, depois do IP aprovado e com o HMAC. A tecla **Q** do HUD encerra os dois processos e os filhos, o PID em `.n8groker/keeper.pid` se ainda for o Keeper, e as portas 8501 e 8502 fecham junto com o resto. O boot também cria `.n8groker/maquina.key` se faltar. O Keeper não escuta porta. Se você recusou o venv, o HUD avisa e não tenta o Streamlit. Se a porta 8501 ou a 8502 já for este painel, o script não abre outro. Se for outro programa, ele avisa e não mata.

O admin da máquina não tem usuário nem senha. Na raiz do projeto:

```powershell
control_plane\.venv\Scripts\python -m control_plane.admin_token --init
control_plane\.venv\Scripts\python -m control_plane.admin_token
```

Cole o JWT no campo **Token de admin** de `http://localhost:8501`. Ele dura 5 minutos, não vai para a URL e a borda recusa. O admin não tem senha. As contas ficam em `.n8groker/users.json`, também sem senha. Na aba Admin, **Emitir token**, **Revogar** e **Rotacionar chave** chamam `python -m control_plane.keeper` (sem porta, 3 segundos). O painel não lê a privada. O JWT de usuário aparece uma vez (permissões no próprio token, prazo em `N8GROKER_TOKEN_USUARIO_HORAS`, padrão 8 horas). A chave é `.n8groker/usuario.key`, separada de `admin.key`. **Rotacionar chave** invalida os tokens já emitidos e derruba o cookie na próxima requisição. `python -m control_plane.admin_token --rotate` gira a chave de admin pelo Keeper, sem folga: a sessão admin cai quando `.n8groker/admin-geracao` sobe, também se o arquivo estava ausente ou ilegível, e o colar continua como entrada. Keeper fora desliga esses botões e não derruba quem já entrou. `python -m control_plane.auth --reset` não apaga usuários nem a auditoria.

O comando manual, na raiz do repositório, continua valendo quando o orquestrador não está no ar:

```powershell
python -m venv control_plane\.venv
control_plane\.venv\Scripts\python -m pip install -r control_plane\requirements.txt
$env:PANEL_MODE = "console"
control_plane\.venv\Scripts\python -m streamlit run control_plane\app.py --server.address 127.0.0.1 --server.port 8501
```

No Linux, o `setup.sh` cria o mesmo venv. O comando fica:

```bash
PANEL_MODE=console control_plane/.venv/bin/python -m streamlit run control_plane/app.py --server.address 127.0.0.1 --server.port 8501
```

O layout abre wide. As seções do topo são **Infraestrutura**, **Scout** e **Chat de suporte**. A aba **Admin** só aparece no console, com a sessão do JWT. O endereço `http://localhost:8501/?aba=scout` (ou `infra`, `chat`, `admin`) abre a seção depois do login. A tecla **G** do HUD usa esse link da aba Scout. Cada card tem uma linha do que a aplicação é e o expander **Para que serve**. **Atualizar Status** refaz só as checagens HTTP dos serviços com porta (fragmento do Streamlit), com timeout curto e em paralelo. Postgres, ClickHouse, Redis e o banco do LiteLLM aparecem como texto: não publicam porta no host, então não há sonda HTTP.

## Scout

A seção Scout fala com a API em `127.0.0.1:8765` (o mesmo contrato da antiga janela Tk, agora só HTTP, em `Scout_OSINT_Docker/scout/gate.py`). O compose publica essa porta só em loopback. Se o `scout-backend` estiver fora, a seção mostra um aviso em português e o resto do painel continua. Rotas, modo, porta manual, sync Docker, firewall, alias, bloqueio, desbloqueio e remoção de cliente pedem **Confirmar** antes do POST, PATCH ou DELETE. Ativar `n8n_app` repete o aviso de que o Porteiro fica de fora. Ativar `ngrok_service` é recusado e a API não é chamada. Upstream `127.0.0.1` ou `localhost` nas portas 5677 e 5678 vira `host.docker.internal` antes de gravar. Uma rota nova não pode escutar nem apontar para `5676` ou `5677`. A `porteiro-manual` continua no `5677`. Um alias não abre rota. Quem só tem `ver` em `scout` vê a seção; quem mexe na rota precisa de `operar`.

Não há tick de um em um segundo, nem bipe, nem “fechar a janela encerra a stack”. A lista completa está no README da raiz, na seção **Paridade da aba Scout**. Neste ramo não existe janela Tk; ela continua no `master`.

## Chat de suporte

A aba fala com o Ollama desta máquina. `OLLAMA_BASE_URL` (padrão `http://localhost:11434`) e `SUPPORT_CHAT_MODEL` (padrão `qwen2.5:7b-instruct`) vêm do `.env`. Esse endereço não entra no LiteLLM.

Antes de cada resposta o painel monta um prompt em português com a descrição das peças, as URLs e o status HTTP daquele momento. O modelo pode pedir ferramenta, no formato de function calling do Ollama:

| Ferramenta | Efeito | Confirmação |
| --- | --- | --- |
| status | checagem já usada nos cards | não |
| logs | `docker logs --tail N` (no máximo 200 linhas) só de container da lista de restart | não |
| scout_resumo, scout_rotas, scout_trafego, scout_clientes | leitura da API do Scout, sem argumentos livres | não |
| iniciar, parar, reiniciar | o mesmo `docker start`/`stop`/`restart` dos botões, ou o Porteiro que o próprio painel subiu | sim, card com Confirmar/Cancelar |

Toggle, alias, bloqueio, firewall e porta manual do Scout não entram no chat. Reiniciar o container `scout-backend` usa `reiniciar_servico`, que já pede confirmação.

Nome fora da lista, comando livre e o próprio Control Plane são recusados. Segredo de chave cujo nome contém `KEY`, `SECRET`, `TOKEN` ou `PASS` sai como `«redigido»` nos logs. A conversa fica na sessão do navegador até **Limpar conversa**. A resposta em texto chega em fluxo, quando o Ollama manda os pedaços.

Se o Ollama não responde, a aba pede `ollama serve`. Se o modelo não está baixado, mostra `ollama pull qwen2.5:7b-instruct` e só baixa no botão de confirmação.

Limite do 7B: ele explica o caminho, mas troca nome de porta e inventa passo. Leia o card de confirmação. Não trate a resposta como procedimento automático. A primeira carga do modelo usa alguns GB de RAM.

## O que os botões fazem

| Botão | Comando | Confirmação |
| --- | --- | --- |
| Iniciar núcleo | Rede `rede_comunicacao`, `node porteiro.js`, Scout (se `USE_SCOUT=1`) e ngrok. Não sobe n8n nem Langfuse/LiteLLM. Só o admin. Não há botão para parar o núcleo: a tecla Q do HUD é quem encerra | não |
| Iniciar / Parar / Reiniciar n8n | `docker compose` de `n8n/docker-compose.yml` (`up -d`, `down` sem `-v`, `restart`). Exige **Pode operar** em n8n, ou admin | Parar pede confirmação |
| Iniciar / Parar / Reiniciar Langfuse e LiteLLM | `docker compose` de `llm/docker-compose.yml`. Exige **Pode operar** em Langfuse e em LiteLLM, ou admin | Parar pede confirmação |
| Abrir HUD do núcleo | Abre `iniciar_servicos.ps1` numa janela nova, no Windows. Sem `-Stack`, sobe só o núcleo | não |
| Reiniciar | `docker restart` só de `n8n_app`, `ngrok_service`, `scout-backend`, `langfuse-web`, `langfuse-worker`, `litellm` | não |

Não há `shell=True`. Container fora dessa lista é recusado.

O console não encerra o Porteiro do núcleo. Se algum fluxo encerrar o processo, é só o PID que o próprio painel gravou em `control_plane/.porteiro.cp.pid`.

## Configurar

Padrões estão em `control_plane/config.py` (portas e rotas reais deste repo). Para trocar URL sem editar código:

- copie `control_plane.local.json.example` para `control_plane.local.json`, ou
- use `CP_LANGFUSE_URL`, `CP_LITELLM_URL`, `CP_N8N_URL`, `CP_HEALTH_TIMEOUT`, `CP_SLOW_MS`, `N8GROKER_ROOT`.

`CP_SCOUT_BUILD=1` faz o Iniciar núcleo passar `--build` no Scout. O `iniciar_servicos.ps1` sempre usa `--build`; o painel não, para não reconstruir a imagem em todo clique.

Critério de status: resposta com o HTTP esperado e até `CP_SLOW_MS` (padrão 1000) é online; HTTP diferente ou mais lento é degradado; timeout ou conexão recusada é offline.

## Diagnóstico

**Atualizar Status** grava hora, serviço, estado, latência e o tempo até ficar saudável depois de uma queda ou de um start/restart. O SQLite fica em `.n8groker/diagnostics.sqlite` (a pasta inteira está no `.gitignore`) e amostras com mais de 7 dias saem. O painel desenha a linha de latência e de estado por serviço. O termômetro marca o serviço quando a latência, ou esse tempo até ficar saudável, passa do p90 recente e da mediana — e só depois de 5 pontos anteriores.

**Gerar diagnóstico** monta um zip com o status atual, o histórico, `docker logs` recente dos containers da lista, `docker compose ps`, `docker version` e `ollama --version`. O mesmo filtro do chat troca segredo de chave por `«redigido»`. O `.env` não entra no arquivo.

## Versões das imagens

O painel consulta o registro das imagens pinadas (Langfuse, LiteLLM, Postgres, ClickHouse, Redis e as outras do compose) e avisa se houver uma tag numérica mais nova. Não faz pull e não edita o compose. A consulta fica em `.n8groker/version-cache.json` por 12 horas. Sem rede, o aviso some ou continua o último resultado, sem mensagem de erro. Tag `latest` (n8n, ngrok) e o MinIO pinado por digest aparecem só como nota, sem proposta de troca.

## CPU e memória

A aba Infraestrutura mostra CPU e RAM de cada container com `docker stats --no-stream`. O resultado fica na sessão por 20 segundos. Se o Docker não responder, a tabela some e o resto do painel continua.

## Backup

Na aba Infraestrutura, o backup grava em `.n8groker/backups` (gitignored), com data e hora no nome:

| Alvo | Como |
| --- | --- |
| Postgres do Langfuse e do LiteLLM | `docker exec … pg_dump` em formato custom. A senha não entra no comando. |
| n8n | cópia de `n8n/n8n/data/database.sqlite` |
| ClickHouse e MinIO | arquivo tar do volume nomeado do compose (`n8groker-llm_…`), usando a imagem `postgres:17.11` só como utilitário |

Restaurar exige a caixa de confirmação. O plano para os containers que usam aquele dado, grava, e sobe de novo. Sem a confirmação o módulo recusa.

## Testes

```bash
python -m pip install -r control_plane/requirements.txt
python -m pytest tests
```
