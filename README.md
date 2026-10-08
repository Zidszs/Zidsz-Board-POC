# N8Groker

Ambiente de automação que integra **n8n**, **Docker**, **Ngrok**, **Porteiro** e opcionalmente **Scout Gate** — para expor workflows na internet com controle de acesso por IP, aprovação via e-mail e observabilidade na borda.

Documentação técnica completa: [`DOCUMENTACAO.md`](DOCUMENTACAO.md)

## Mapa interativo

O grafo do repositório e o guia de leitura estão em [`docs/ENTENDA-O-PROJETO.md`](docs/ENTENDA-O-PROJETO.md). Esta foto inclui o Keeper, as duas trilhas, o cookie `n8groker_sessao` e a pasta `Arquivos-n8n`. No Windows, com Node.js 18 ou mais novo, na raiz do clone:

```powershell
npx https://github.com/Egonex-AI/Understand-Anything/releases/download/v2.9.0/understand-anything-viewer.tgz .
```

O comando imprime uma URL em `127.0.0.1:5173` com token. O painel é só leitura e não chama nenhum LLM. A URL fica no release `v2.9.0` (10/07/2026). Para trocar o viewer de propósito, substitua `v2.9.0` pela tag de um release que exista e cujo arquivo `understand-anything-viewer.tgz` responda. Não use `releases/latest/download`. Para atualizar o grafo depois de mudar o código, rode de novo `/understand` (a partir da segunda vez ele só reanalisa o que mudou).

O índice de `docs/` está em [`docs/README.md`](docs/README.md). O roteiro em vídeo da interface do Control Plane, com o que foi corrigido na tela e o que essa gravação não sobe, está em [`docs/apresentacao/TOUR-EM-VIDEO.md`](docs/apresentacao/TOUR-EM-VIDEO.md). O que cada tela, campo, botão e aviso faz está em [`docs/apresentacao/GUIA-DE-USO.md`](docs/apresentacao/GUIA-DE-USO.md).

## Esta versão

Esta cópia já é a linha Control Plane. O console fica em `http://localhost:8501`, com as seções Infraestrutura, Scout, Chat e, para o admin da máquina, a aba Admin. Há diagnóstico, backup, contas e JWT de admin. A tecla G abre `http://localhost:8501/?aba=scout`. Não há janela Tk do Scout.

Não há outro ramo para trocar dentro desta cópia.

---

## Visão geral
Propósito do Projeto: Este ambiente foi desenvolvido estritamente para estudo, treinamento e práticas de segurança em uma infraestrutura pessoal. Ele serve como um laboratório prático para aplicar conceitos de Zero Trust, proxy reverso, controle de acesso e observabilidade na borda, não sendo voltado para ambientes de produção corporativos.
O N8Groker elimina a configuração manual de roteador, DNS e reverse proxy. Um visitante externo chega via Ngrok, passa pelo Scout (opcional) e pelo Porteiro (proxy Node.js) antes de alcançar o n8n. IPs desconhecidos ficam em fila até aprovação humana via workflow n8n.

```text
Internet → Ngrok → Scout (:4050, opcional) → Porteiro (:5677) → n8n (:5678)
```

Modo legado (`USE_SCOUT=0`): Ngrok aponta directo para o Porteiro. Nesse modo a página 202 é a de `Porteiro/porteiro.js`. Com Scout, o 202 do túnel é o de `Scout_OSINT_Docker/scout/core/porta_apps.py`.

---

## Início rápido (máquina já configurada)

| Passo | Ação |
|-------|------|
| 1 | Rodar **Setup.bat** (ou `python scripts/init_env.py`) para criar `.env` a partir de `.env.example`, com segredos aleatórios. Preencher só o `NGROK_AUTHTOKEN` |
| 2 | Preencher só o `NGROK_AUTHTOKEN`. O Setup gera a `N8N_ENCRYPTION_KEY` e não troca uma chave que já exista. O token de aprovação não vai no `.env` |
| 3 | Executar **`Setup.bat`** — prepara a base (não sobe serviços) |
| 4 | Executar **`iniciar_servicos.ps1`** — na primeira vez pergunta, em português, o que falta (ferramenta, motor do Docker, `.env`, venv) e só então sobe o núcleo: Control Plane em `http://localhost:8501`, borda em `8502` (se o venv existir), Ollama local se `USE_OLLAMA_LOCAL=1` e o programa já estiver instalado, Porteiro, ngrok e Scout (se `USE_SCOUT=1`). n8n e Langfuse/LiteLLM ficam de fora, salvo `STACKS_BOOT` (exemplo `n8n,llm`) |
| 5 | Configurar o n8n (conta, workflow, SMTP) — ver checklist abaixo |
| 6 | Tecla **`G`** no HUD abre o painel na aba Scout (`http://localhost:8501/?aba=scout`). O login do painel continua valendo |

A rede Docker `rede_comunicacao` é criada pelo **Setup.bat** (opção auto-config) ou pelo **`iniciar_servicos.ps1`**, que cria a rede quando o motor Docker está no ar, sem perguntar. Só crie manualmente se os dois não rodaram:

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
| 3 | **`iniciar_servicos.ps1`** — sobe o núcleo (painéis, Porteiro, ngrok, Scout). n8n e a stack LLM sobem à parte | Script |
| 4 | **n8n** — checklist abaixo | Você |

### Checklist n8n (obrigatório para aprovação por IP)

1. Abrir `http://localhost:5678` e **criar a conta admin** (primeiro acesso após reset).
2. **Importar** `Workflows_para_Autenticação/Aprovacao de Acesso (Novo).json`.
3. No node **Configuracoes**:
   - `admin_email` → seu e-mail de aprovador (não deixe `voce@exemplo.com`).
   - `admin_token` → já vem `{{ $env.PORTEIRO_N8N_TOKEN }}`. É obrigatório. Se o n8n recusar `$env`, cole o conteúdo de `.n8groker/porteiro-n8n.token`. Campo vazio passa a responder 403.
   - `porteiro_url` → `http://host.docker.internal:5677` (já vem correto).
4. No node **Email e Espera Aprovacao** → configurar **credencial SMTP**.
5. **Ativar** o workflow (no JSON vem `"active": false`). Um workflow já importado nesta máquina não muda sozinho: edite o node ou importe de novo.

### Nodes bloqueados no n8n

Quem entra no n8n pelo túnel é um usuário da instância. A imagem lê `NODES_EXCLUDE` (um array JSON). `N8N_NODES_EXCLUDE` dentro do container é ignorado. O `.env` guarda a lista separada por vírgula em `N8N_NODES_EXCLUDE`; o `iniciar_servicos.ps1` converte, exporta `NODES_EXCLUDE` e, depois do `up`, grava no log a saída de `docker exec n8n_app printenv NODES_EXCLUDE`. A lista bloqueia Execute Command, SSH, Read/Write Files from Disk, Local File Trigger e os nodes antigos de arquivo binário (`readBinaryFile`, `readBinaryFiles`, `writeBinaryFile`). O padrão está em `.env.example`. Apague um nome e recrie o container para reativar. A lista vale para a instância inteira, inclusive `http://localhost:5678` e `http://127.0.0.1:5678`.

O volume `../Arquivos-n8n` (a partir de `n8n/docker-compose.yml`) monta a pasta da raiz em `/home/node/Arquivos-n8n`. `N8N_RESTRICT_FILE_ACCESS_TO` aponta só para esse caminho e `N8N_BLOCK_FILE_ACCESS_TO_N8N_FILES=true` mantém `/home/node/.n8n` bloqueado. Com o node de arquivo de volta na lista, o caminho no nó é `/home/node/Arquivos-n8n/entrada.csv`. Na imagem n8n `2.42.5` (2.0+) o padrão sem a variável seria `~/.n8n-files`.

O node **Code** fica de fora. Os fluxos do dia a dia, e o editor nesta máquina, usam JavaScript dentro do processo do n8n, não um shell nem o disco do host. Para bloquear o Code também, acrescente `n8n-nodes-base.code` em `N8N_NODES_EXCLUDE`.

`N8N_BLOCK_ENV_ACCESS_IN_NODE` não é definido. O workflow `Aprovacao de Acesso (Novo).json` lê `{{ $env.PORTEIRO_N8N_TOKEN }}`. Ligar o bloqueio faria essa expressão falhar. O compose também não força `false`: se a imagem do n8n já recusar `$env`, cole o conteúdo de `.n8groker/porteiro-n8n.token` no campo `admin_token`, como o checklist diz. O workflow `Alerta de trilha` não usa `$env`.

### Tokens de aprovação (obrigatórios)

O `.env` não guarda mais esse segredo: o compose do Scout monta o `.env` dentro do container. O `iniciar_servicos.ps1` cria dois arquivos em `.n8groker/` (e o Porteiro relê os dois em cada rota):

| Arquivo | Quem usa |
| --- | --- |
| `porteiro-painel.token` | Aba Admin do console, no header `X-Admin-Token` para `http://127.0.0.1:5676` |
| `porteiro-n8n.token` | Workflow do n8n. O compose do n8n lê só `.n8groker/porteiro-n8n.env` |

`PORTEIRO_TOKEN` no ambiente ou num `.env` antigo é ignorado. Sem os dois arquivos, ou com token errado, `/n8n/*` responde 403 antes de olhar a fila. Não é o 404 «O IP nao esta na fila de espera». Token certo não atravessa o túnel.

### Chave HMAC e montagens de arquivo

O Scout não monta a pasta `.n8groker` inteira. `admin.key` e `usuario.key` ficam de fora do container. Cada segredo entra sozinho, só leitura: `porteiro-painel.token`, `sessao.key`, `sessoes-geracao.json` e `porteiro-hmac.key`. `audit.jsonl` entra como arquivo (pode nascer vazio). A trilha entra como pasta.

O `iniciar_servicos.ps1` grava esses arquivos **antes** de qualquer `docker compose`. Se o arquivo não existe na hora do `up`, o Docker cria uma **pasta** no lugar. Foi isso que deixou `.n8groker/porteiro-hmac.key` vazia: o Scout não lia chave, não injetava `x-n8groker-client`, e o painel respondia que só abre com o cabeçalho assinado. Pasta vazia o script remove, anota `[REPARO]` e grava o arquivo (32 bytes aleatórios, ACL do usuário, sem apagar um arquivo que já tem conteúdo). Pasta com conteúdo interrompe o boot e não apaga nada. Arquivo vazio de chave ou token é trocado; `audit.jsonl` vazio continua vazio. Depois que o `scout-backend` sobe, o script confere dentro do container (`docker exec scout-backend test -f` e, na chave, arquivo não vazio). Se a montagem for pasta, o boot para.

Sem chave legível, o Scout não encaminha `/painel`. A trilha grava `BLOQUEIO (cabeçalho do Porteiro ausente)` e não fecha a cadeia em `ok`. Quando um app abre de verdade, a linha termina em `LIBERADO`, com `sid` e `conta`. O Scout e o painel de borda também registram o erro na subida se a chave falta, está vazia ou é pasta.

Arquivo que o Docker já montou não é trocado com `os.replace`. No Docker Desktop do Windows isso devolve `WinError 5` e o container fica no inode antigo. `sessoes-geracao.json`, `sessao.key` e `porteiro-painel.token` são regravados no mesmo inode (trunca, escreve, `fsync`), com nova tentativa se o arquivo estiver aberto. `usuario.key` nasce no boot do núcleo, no mesmo cuidado (arquivo, não pasta; gravação no lugar), e não é montada no Scout: quem assina o JWT de usuário é o Keeper, na máquina. O painel confere com `usuario.pub` e não chama o Keeper nesse login. Rotacionar essa chave derruba o cookie no pedido seguinte, porque a geração em `sessoes-geracao.json` sobe. O JWT de admin da máquina não entra nesse arquivo. Girar a chave de admin sobe o número em `.n8groker/admin-geracao` e a sessão admin cai na atualização seguinte.

A página 202 registra a primeira origem. Depois que o IP já está aprovado, o segundo navegador não vê essa página: cai no painel. O painel carrega `/painel/origem.html` num iframe de URL mesma origem (não `srcdoc`). O `srcdoc` do Streamlit não é contexto seguro, o WebCrypto não roda e a origem nova não entra na fila. O script é `/origem.js`. O Scout também entrega `/painel/origem.js` e qualquer outro prefixo que termine nesse nome (`/painel/painel/origem.js`, o que o `baseUrlPath` do Streamlit pede). Se isso fosse para o Streamlit, o navegador receberia HTML e o console mostraria `Unexpected identifier 'Streamlit'`. A origem nova fica pendente; a já aprovada continua.

O login do Streamlit não criava o cookie `n8groker_sessao`. O `/painel/escolher` exigia esse cookie para gravá-lo, então o clique em Abrir n8n respondia `Acesso negado.` e a trilha nem registrava a URL. Agora o Scout entrega o cookie nesse clique (o link leva um ticket curto assinado com a chave HMAC, mais a prova da origem) e também em `/painel/sessao`. A trilha grava `login`, `escolher` e `LIBERADO` com `sid` e `conta`. Sem cookie e sem ticket, a negação entra na trilha como `BLOQUEIO (sem sessao)`. A raiz sem sessão continua 403, sem a palavra painel no corpo.

No Docker Desktop, outro container que chama `host.docker.internal:5676` aparece como loopback. O bind `127.0.0.1` não separa esse container, e a lista de IP do n8n também não. A proteção é o token. A porta continua em loopback para a LAN não chegar direto.

### Rotas de aprovação: só rede local

`/n8n/aprovar`, `/n8n/bloquear`, `/n8n/vincular`, `/n8n/fila` e `/n8n/solicitar` não aceitam o domínio do ngrok. Token válido não muda isso. O Porteiro recusa a chamada quando vê `X-Forwarded-For`, `X-Forwarded-Host` ou um `Host` do ngrok. O socket tem de ser loopback, ou o IP do container `n8n_app` gravado em `.n8groker/n8n-container-ip` na subida. A faixa `172.16/12` inteira não passa: o Scout está nessa rede. O `iniciar_servicos.ps1` descobre esse IP com `docker inspect` depois que o n8n sobe; não há IP fixo no script. O Porteiro relê o arquivo em cada rota admin, porque ele sobe antes do n8n.

O ngrok em modo `http` grava `X-Forwarded-For` na conexão que ele abre para o upstream. O visitante não consegue tirar esse header: ele é escrito pelo agente, depois do TLS. O pipe do Scout copia os bytes e não remove o header. O que não dá para garantir é outra conexão, vinda do container do Scout sem passar pelo ngrok. Por isso uma rota manual, um patch ou um sync do Docker não pode escutar nem apontar para `5676` ou `5677`. A rota `porteiro-manual` continua levando o visitante ao `5677`. Um alias não abre rota.

| Quem chama | URL |
| --- | --- |
| Console, aba Admin, ou um comando nesta máquina | `http://127.0.0.1:5676/n8n/aprovar?ip=` (porta só em loopback; o túnel não chega nela). O mesmo caminho vale para `bloquear`, `vincular`, `fila` e `solicitar`. |
| n8n, de dentro do container | `http://host.docker.internal:5677/n8n/aprovar?ip=` — é o `porteiro_url` do workflow. Sem header de proxy. |

Não use a URL pública do ngrok nessas rotas. O visitante continua entrando pelo túnel na porta 5677; o veredito volta pela rede local.

### Contrato do webhook

O Porteiro, ao ver IP novo e de novo quando nasce uma origem, chama em loopback:

`GET http://127.0.0.1:5678/webhook/solicitar-verificacao-acesso?ip=<ip>&conta=<usuário>&dispositivo=<sha256>&origem=<id>&origem_aprovada=<id>&navegador=<nome>&sistema=<nome>&idioma=<tag>&horario=<UTC>&pais=`

`conta` só entra quando existe. `pais` vai sempre vazio: não há GeoIP e o idioma não é copiado para esse campo. `horario` é UTC ISO-8601 gerado no Porteiro. Navegador e sistema saem do User-Agent, na máquina. Quem só lê `ip` continua vendo o IP. Se o n8n responder 404, a fila local segue: `/n8n/solicitar` devolve HTTP 200 com `n8n_status`.

O JSON em `Workflows_para_Autenticação/Aprovacao de Acesso (Novo).json` mostra esses campos e devolve `origem` em aprovar e bloquear. Um workflow já importado no n8n não muda sozinho: é preciso editar o node ou importar de novo. `/n8n/aprovar` não grava `conta_vinculada`. Vincular continua sendo outro passo, e só com o par já aprovado.

O veredito volta sempre com `X-Admin-Token`:

| Ação | Rota | Efeito |
| --- | --- | --- |
| Aprovar | `GET /n8n/aprovar?ip=&origem=` | o par fica `aprovado`. Não grava `conta_vinculada`. Sem `origem`, não aprova dispositivo. |
| Reprovar | `GET /n8n/bloquear?ip=&origem=` | bloqueia essa origem. Sem `origem`, bloqueia o IP inteiro. |
| Vincular | `GET /n8n/vincular?ip=&conta=&origem=` | só com o par já `aprovado`. Grava `conta_vinculada` e `vinculo: ativo`. |
| Fila | `GET /n8n/fila` | JSON `{visitantes: [...]}`. |

Da máquina, o prefixo é `http://127.0.0.1:5676`. Do container n8n, `http://host.docker.internal:5677`. A aba Admin do console usa a porta 5676 e atualiza a lista na hora. Cada veredito entra em `.n8groker/audit.jsonl`.

A aprovação é o par IP e origem. Uma segunda origem no mesmo IP fica pendente. A já aprovada continua.

### Contas do painel e JWT de admin

Não há tela de criar administrador. Na raiz do projeto, com o venv do Control Plane:

```text
python -m control_plane.admin_token --init
python -m control_plane.admin_token
```

O segundo comando imprime um JWT Ed25519 de 5 minutos no stdout. Cole só essa linha no campo **Token de admin** em `http://localhost:8501`, com o console já aberto. Se o boot demorou mais que isso, gere de novo: prazo vencido, assinatura errada e chave ausente aparecem na tela e em `.n8groker/trilha/auth.jsonl` (motivo `prazo`, `assinatura` ou `chave`, sem o JWT). O comando usa a pasta do código, a mesma do painel, e não o diretório em que o terminal estava. Não coloque o token na URL. A borda (`/painel`) recusa esse JWT. A chave privada fica em `.n8groker/admin.key` (modo 0600) e não entra no `.env`. O admin não tem senha. `--init` continua criando essa chave. `--rotate` não gira no próprio processo: chama o Keeper.

O Keeper é `python -m control_plane.keeper <pedido>`. Não escuta porta. Cada pedido é um processo, com no máximo 3 segundos; se estourar, o HUD trata como Keeper fora e não tenta de novo na mesma atualização. A saída é uma linha JSON, sem chave privada. Os pedidos deste lote são `status`, `emitir`, `revogar-jti`, `revogar-conta` e `rotacionar` (`admin` ou `usuario`). O boot cria `.n8groker/maquina.key` se faltar (32 bytes, fora do `.env` e de qualquer volume Docker). Sem esse arquivo, `status` responde `motivo` `chave`. O filho grava `.n8groker/keeper.pid` ao nascer e apaga ao sair. A tecla Q, e o pedido de shutdown, matam esse PID se o arquivo ainda existir e a linha de comando for do Keeper.

Com a sessão admin aberta, a aba Admin cria contas em `.n8groker/users.json`, sem senha. **Emitir token**, **Revogar** e **Rotacionar chave** pedem ao Keeper. O painel não lê a privada. **Emitir token** mostra uma vez um JWT Ed25519 de usuário (conta, Pode ver, Pode operar, Pode abrir, prazo e jti). O prazo padrão é 8 horas, em `N8GROKER_TOKEN_USUARIO_HORAS`. A chave que assina esse JWT é `.n8groker/usuario.key`, criada no boot se faltar. O token não é gravado em claro: no disco ficam o jti e a validade, para revogar e para a trilha. As permissões que valem são as do token. Mudar Pode ver, Pode operar ou Pode abrir e gravar a lista não altera um token já emitido; é preciso emitir de novo. O inventário da aba é só leitura: públicas, kid curto, número de `admin-geracao`, jti no prazo, `maquina.key` como sim ou não, e a frase de recuperação de `sessao.key`, da chave HMAC e dos dois tokens do Porteiro. Stack, backup, diagnóstico e a aba Admin não são um papel: só a sessão admin do console. Na borda, token válido com IP ainda sem vínculo fica em **aguardando aprovação**, com as listas vazias, até `/n8n/vincular` daquele IP e daquela conta. Origem do navegador, vínculo de IP e trilha continuam por cima do token.

**Rotacionar chave** (com confirmação) pede `rotacionar` com alvo `usuario`. Todo token de usuário emitido antes deixa de valer na hora, e o cookie de sessão cai no pedido seguinte. A sessão de admin não usa essa chave. `python -m control_plane.admin_token --rotate` pede o alvo `admin`. A pública nova vale na hora. A anterior fica em `admin-prev.pub` só para o motivo `rotacionado` e não abre sessão: não há folga de 5 minutos. O Keeper grava em `.n8groker/admin-geracao` um número maior do que o da sessão aberta. Se esse arquivo falta ou não é um inteiro, o próximo número passa da maior geração já entregue, em `.n8groker/admin-geracao-max`, e continua até 1000000000. O console lê esse número em toda atualização, sem chamar o Keeper. Se o número da sessão for menor, a sessão admin fecha e o campo **Token de admin** continua na tela para colar um token novo. Keeper fora mostra o aviso «Keeper fora, não dá para emitir nem girar chave», desliga emitir, revogar e girar, e a aba Diagnóstico ganha essa linha. Quem já entrou não cai por isso. Revogar um jti na aba Admin invalida aquele token e sobe a geração da conta. Revogar uma sessão na aba Scout continua derrubando só aquele sid.

Quem já tinha hash de senha em `users.json` ou em `controle_acesso.json` não entra com essa senha. O hash é ignorado (e tirado do arquivo na próxima leitura). A conta continua, e o admin emite um token novo.

`python -m control_plane.auth --reset` não apaga usuários nem `audit.jsonl` e não cria senha de admin. Um `auth.json` antigo é renomeado para `auth.json.legado` e deixa de autenticar.

---

### Roteiro no Windows

No PowerShell, na raiz do projeto, com a stack no ar:

1. `python -m control_plane.admin_token --init` e, em seguida, `python -m control_plane.admin_token`. Copie o JWT.
2. Abra `http://localhost:8501`, cole o token no campo **Token de admin** e abra a sessão. Na aba Admin, crie um usuário e clique em **Emitir token**. Copie o JWT. Ele não fica gravado.
3. Saia e entre com esse token no campo **Token de acesso**. Não há senha. Na borda, o mesmo campo está em `/painel`.
4. De outro IP, abra a URL pública do ngrok em `/painel`. Sem IP aprovado a resposta é a página de análise (HTTP 202), não o Streamlit.
5. No console, aba Admin, o IP aparece pendente, com data e conta pedida. **Aprovar** e **Reprovar** chamam `http://127.0.0.1:5676` e a lista atualiza na hora, mesmo se o webhook do n8n estiver em 404. **Vincular** só depois do IP aprovado.
6. `http://localhost:5678` e `http://127.0.0.1:5678` continuam o n8n direto, sem Porteiro e sem login do painel.

## O que o Setup faz e o que não faz

| Setup **faz** | Setup **não faz** |
|---------------|-------------------|
| Verifica Docker, Node, Python (Control Plane e `.env`), portas | Instalar Docker / Node / Python (só abre links) |
| Cria `.env`, rede `rede_comunicacao`, pastas de dados | Subir containers (isso é o `iniciar_servicos.ps1`) |
| venv + pip do Control Plane | Criar conta no n8n |
| Abre `.env` para você preencher tokens | Importar ou ativar workflows |
| Pode perguntar se inicia o `iniciar_servicos.ps1` | Configurar SMTP no n8n |

**Resumo:** o Setup deixa a **base técnica pronta**; o fluxo de aprovação exige configuração **dentro do n8n**.

---

## Scripts operacionais

| Script | Função |
|--------|--------|
| **`Setup.bat`** | Checklist interactivo de dependências: auto-config (`.env`, rede Docker, pastas, venv do Control Plane), links de instalação ou ignorar por item. Não sobe serviços por defeito (pode perguntar no fim se inicia o orquestrador). |
| **`iniciar_servicos.ps1`** | Orquestrador do núcleo: na primeira execução prepara ferramenta, `.env` e venv (perguntando antes) e cria a rede `rede_comunicacao` se o motor estiver no ar. Depois boot do núcleo, HUD e shutdown (**Q**, que também derruba n8n e a stack LLM se estiverem no ar). `-Stack n8n` ou `-Stack llm` inicia, para ou reinicia uma stack sem abrir o HUD. `STACKS_BOOT` vazio deixa as apps de fora do boot. |
| **`factory_reset.bat`** | Apaga dados voláteis (n8n DB, Porteiro JSON, Scout data, `.env`). Confirmação digitando `Excluir`; opcional **S** recria `.env` do template. Usar **só numa cópia** do projeto. |
| **`Scout_OSINT_Docker/setup.bat`** | Scout isolado: sobe o container e fica em standby. A tecla G abre o painel. Não abre janela Tk. |
| **`setup.sh`** | Equivalente Linux do setup: Docker (repo oficial apt), Python, venv do Control Plane, `.env`, pull das imagens. Pergunta antes de instalar. |
| **`control_plane/app.py`** | Painel Streamlit: Infraestrutura, Scout, Chat de suporte e, no console com sessão admin, a aba Admin (não substitui o HUD). |

---

## Control Plane e stack de LLM

O `iniciar_servicos.ps1` abre o painel sozinho, em segundo plano, depois que o venv do Control Plane está pronto. Um venv antigo, que só importava o Streamlit, não passa: o script compara o hash de `control_plane/requirements.txt` com `control_plane/.venv/.n8groker-requirements.sha256` e também executa `import streamlit; import cryptography`. Se o carimbo ou o import falhar, ele roda `pip install -r` de novo, sem perguntar outra vez. Se ainda faltar o módulo, a página mostra o aviso em português em vez do traceback. O endereço é `http://localhost:8501`. O Streamlit sobe headless (não abre o navegador nem pede e-mail), escuta só em `127.0.0.1` e o log fica em `control_plane/streamlit.log`. O console não cria conta sozinho: o admin cola o JWT (`python -m control_plane.admin_token`) e, na aba Admin, cria os usuários. A borda sobe em `127.0.0.1:8502` e só aparece em `/painel`, com o cabeçalho HMAC do Porteiro. Se o registro tiver uma tag mais nova de uma imagem pinada, o painel avisa e não atualiza sozinho; sem rede essa consulta falha em silêncio. Se a porta 8501 já for este painel, o script reutiliza. Se for outro programa, avisa e não mata esse processo. Recusar o venv (marcador `.n8groker.skip-control-plane-venv`) deixa o HUD sem Streamlit e diz isso na tela. A tecla **Q**, o pedido de shutdown e a falha de subida encerram o processo que este script iniciou, com os filhos, para as portas 8501 e 8502 fecharem junto com o núcleo e com as stacks de app que estiverem no ar. No console, **Iniciar núcleo** sobe Porteiro, Scout e ngrok; não há botão para desligar o núcleo, porque isso trancaria o admin fora. n8n e Langfuse/LiteLLM ligam e desligam por stack, e só quem tem **Pode operar** naquele app (ou o admin) aciona. Se a 8502 já for este painel, o script reutiliza; se for outro programa, avisa e não mata. Cada card do painel tem uma linha do que a peça é e um texto curto do para que ela serve nesta stack. A aba **Chat de suporte** usa o Ollama local (`OLLAMA_BASE_URL`, modelo `SUPPORT_CHAT_MODEL`, padrão `qwen2.5:7b-instruct`) para explicar a stack com o status ao vivo. Status e logs rodam na hora; iniciar, parar e reiniciar só depois de **Confirmar**. O chat não desliga o próprio painel e não puxa o modelo sozinho. Um modelo de 7B cabe em máquina modesta e erra passo com facilidade: confira a ação no card antes de confirmar. Se o Ollama estiver fora, a aba mostra o comando (`ollama serve` ou `ollama pull qwen2.5:7b-instruct`) em vez de quebrar. O painel também grava um histórico local de saúde (linha do tempo e termômetro de lentidão), gera um zip de diagnóstico com logs já redigidos, mostra CPU e memória por container (`docker stats --no-stream`, com cache de 20 segundos) e faz backup/restauração (pg_dump do Postgres, SQLite do n8n, volumes de ClickHouse e MinIO). Restaurar pede confirmação e para os serviços afetados antes. Esses arquivos ficam em `.n8groker/`, fora do git. Detalhes de botões, allowlist e variáveis: [`control_plane/README.md`](control_plane/README.md).

Langfuse e LiteLLM formam a stack `llm` em `llm/docker-compose.yml`, na mesma rede Docker `rede_comunicacao` que o núcleo cria. Não sobem no boot, salvo `STACKS_BOOT=llm` (ou `n8n,llm`).

| Serviço | Imagem pinada | No host | Para que serve |
| --- | --- | --- | --- |
| Langfuse | `langfuse/langfuse:4.30.0` e `langfuse/langfuse-worker:4.30.0` | `:3000` (UI), `:3030` só em localhost (health do worker) | Observabilidade. Postgres 17.11, ClickHouse 25.12.11, Redis 7.4.11 e MinIO `cgr.dev/chainguard/minio@sha256:4692462f35d97d7e82c30371d82f057703c5d9489bcae726010594c812f2d285` ficam na rede interna. MinIO do browser em `127.0.0.1:9090`. |
| LiteLLM | `ghcr.io/berriai/litellm:v1.103.1` | `:4000` (UI em `/ui`) | Proxy compatível com OpenAI, com Postgres próprio. |

Como eles se falam, sem modelo pré-cadastrado:

- O setup grava chaves `pk-lf-` / `sk-lf-` no `.env`. O Langfuse cria org, projeto, usuário e essas chaves no primeiro boot (`LANGFUSE_INIT_*`).
- O LiteLLM usa as mesmas chaves e o callback `langfuse_otel` em `http://langfuse-web:3000`.
- O n8n recebe `CREDENTIALS_OVERWRITE_DATA`, gerado no boot a partir da `LITELLM_MASTER_KEY`, para a credencial do tipo `openAiApi`: base `http://litellm:4000/v1`. No editor, crie uma credencial OpenAI; a URL e a chave vêm desse overwrite. Isso não cadastra provider no `litellm_config.yaml`.
- Provider, host de modelo e chave de LLM **não** vão no compose nem na credencial do LiteLLM. O `.env` só guarda `OLLAMA_BASE_URL` para o chat de suporte do painel. Cadastre o modelo na UI do LiteLLM (`http://localhost:4000/ui`).

Login inicial do Langfuse: `admin@example.com` e `LANGFUSE_INIT_USER_PASSWORD` do `.env`. Login do LiteLLM: `LITELLM_UI_USERNAME` / `LITELLM_UI_PASSWORD`, ou a `LITELLM_MASTER_KEY`.

O `iniciar_servicos.ps1` sobe essa stack junto com o resto quando as chaves existem. A ordem é Control Plane (fundo: console em `127.0.0.1:8501` e borda em `127.0.0.1:8502`), Ollama local (se `USE_OLLAMA_LOCAL=1`), Porteiro, Scout (se `USE_SCOUT=1`), ngrok, Langfuse/LiteLLM e por último o n8n. Depois do n8n estabilizar, o script grava o IP do container `n8n_app` em `.n8groker/n8n-container-ip` (sem IP fixo no código) e o HUD repete essa leitura enquanto o n8n está `Up`. `USE_OLLAMA_LOCAL=1` é o padrão: se o Ollama está instalado e `http://localhost:11434` não responde, o script sobe `ollama serve` (ou o aplicativo, se só ele existir), espera um tempo e mostra o estado no HUD. Se já responde, reutiliza e não mata no Q. Se não está instalado, avisa em português e só instala com sim. `USE_OLLAMA_LOCAL=0` não procura nem encerra o programa (Ollama em outra máquina). Nenhum modelo e nenhum endereço entram no LiteLLM por aqui. O Scout sobe antes do ngrok para o nome `scout-backend` existir na rede. O ngrok, com `USE_SCOUT=1`, aponta para `scout-backend:4050`, não para `host.docker.internal`. A URL pública (`:4040/api/tunnels`) é gravada em `SCOUT_NGROK_TUNNEL_URL` e em `NGROK_REMOTE_URL` antes do primeiro `up` do n8n. O Scout é recriado uma vez depois dessa URL, para a aba ver o endereço. Se a URL mudar de verdade mais tarde, o HUD recria n8n e Scout de propósito; `health: starting` nessa hora é reinício esperado, não falha. O botão Iniciar do Control Plane não faz esse sync. Se o `.env` for antigo e não tiver as chaves, o script avisa e segue com n8n, ngrok, Scout e Porteiro. Na primeira execução ele descobre ferramenta, motor Docker, rede, `.env` e venv que faltam, pergunta em português e só instala no sim. Comentário com `__GENERATE_*__` não dispara o `init_env.py`; só um valor `KEY=VALUE` ainda marcado. Segredo que já saiu do placeholder não é rotacionado. Falha de compose aparece na tela; desligamento por erro não se apresenta como sucesso. `docker compose down` dessa stack **não** apaga volume; o factory reset usa `down -v` só em `llm/docker-compose.yml`.

Primeira subida do Langfuse leva alguns minutos (ClickHouse e migrações). A máquina precisa de folga de RAM: a doc oficial do Langfuse sugere algo na faixa de 4 núcleos e 16 GB para uso folgado; localmente dá para experimentar com menos, mas o ClickHouse é o que mais pesa.

---

## Componentes da stack

### n8n (Docker)
Motor de automação. Workflows de aprovação por e-mail, webhooks e credenciais. Dados em `n8n/n8n/data/` (SQLite).

### Ngrok (Docker)
Túnel HTTPS público. Dashboard local em `:4040`. Upstream configurado pelo script (`4050` com Scout ou `5677` sem Scout).

### Porteiro (Node.js, host)
Proxy reverso na porta **5677**. Firewall Zero Trust por IP, fila de acesso, rate limit, rotas admin protegidas. Persistência em `n8n/storage/Porteiro/` (paths **relativos** ao projeto — portátil).

### Scout Gate (Docker, opcional)
Camada OSINT/MITM na porta **4050**. API admin **8765**, publicada só em `127.0.0.1`.

| Recurso | Descrição |
|---------|-----------|
| Redireccionamentos | Toggles por rota/container; modo tcp/http/https (persistido) |
| Tráfego | Log de sessões em tempo real |
| **Clientes** | Lista persistente de IPs vistos (`known_clients.json`) — alias sem vigiar o log |
| Aliases IP | Nome manual → IP confiável (sem alertas nem blocklist na borda) |
| Blocklist | Bloqueio na borda MITM |
| Alertas | Tráfego elevado, bloqueios (ignorados para IPs com alias) |

A gestão desses controles, neste ramo, é a seção **Scout** do painel (`http://localhost:8501/?aba=scout`). A tecla **G** do HUD abre esse endereço. A janela Tk continua só no `master`. A lista de paridade está mais abaixo.

### Orquestrador (PowerShell)
`iniciar_servicos.ps1`: valida Node/Docker/`.env`, sobe o Control Plane em segundo plano (`http://localhost:8501` e a borda em `8502`), o Ollama local quando `USE_OLLAMA_LOCAL=1` e o programa já está instalado, Porteiro, Scout e ngrok (grava a URL pública). n8n e Langfuse/LiteLLM só entram nesse boot se `STACKS_BOOT` pedir; senão sobem pelo console ou por `-Stack n8n` / `-Stack llm`. O script não grava modelo nem endereço de Ollama no LiteLLM: isso continua na UI. Se o Ollama local já responde, ele é reutilizado e o **Q** não o fecha. Se não está instalado, o HUD avisa e só instala no sim (winget `Ollama.Ollama`); recusar não impede a stack. O mesmo vale para o Docker Desktop: o **Q** só o encerra se este script abriu o motor nesta sessão, e só depois do `compose down`. HUD com fila Porteiro, status do painel, do Ollama e de quem é dono do Docker, sync de `WEBHOOK_URL` só quando a URL muda, teclas **G** (abre o painel na aba Scout) e **Q**. Se o painel não estiver no ar, **G** avisa em português e tenta o `Start-ControlPlane`; se a saúde não subir, mostra o caminho de `control_plane\streamlit.log` e não abre o navegador. O **Q** encerra o núcleo e as stacks que estiverem no ar. O console não desliga o núcleo. Não há janela para fechar.

---

## Portas

| Porta | Serviço |
|-------|---------|
| `4050` | Scout, portal do túnel, só em `127.0.0.1` no host. O container do ngrok fala com `scout-backend:4050` na rede Docker |
| `4040` | Ngrok dashboard/API, só em `127.0.0.1` |
| `8765` | Scout API de gestão, só em `127.0.0.1` (o painel usa esta porta) |
| `5676` | Porteiro, rotas de aprovação, só em `127.0.0.1` |
| `5677` | Porteiro, visitante, só em `127.0.0.1`. No Docker Desktop o container ainda chega por `host.docker.internal` |
| `3000` | Langfuse (UI), só em `127.0.0.1` |
| `3030` | Langfuse worker (health, só localhost) |
| `4000` | LiteLLM (UI em `/ui`), só em `127.0.0.1` |
| `9090` | MinIO do Langfuse (mídia, só localhost) |
| `5678` | n8n, só em `127.0.0.1` |
| `8501` | Control Plane, console, só em `127.0.0.1`. O browser abre `http://localhost:8501` |
| `8502` | Control Plane, borda, só em `127.0.0.1`. O visitante aprovado chega por `/painel` no Porteiro |
| `11434` | Ollama local, se `USE_OLLAMA_LOCAL=1` e o programa estiver instalado. O LiteLLM não recebe esse endereço pelo script |

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

`N8N_RESTRICT_FILE_ACCESS_TO` no compose aponta só para `/home/node/Arquivos-n8n`. `N8N_BLOCK_FILE_ACCESS_TO_N8N_FILES=true` mantém `/home/node/.n8n` bloqueado. Na imagem n8n `2.42.5` (2.0+) o padrão seria `~/.n8n-files`; aqui o caminho é explícito. Os nodes de arquivo continuam em `NODES_EXCLUDE` até o nome sair da lista.

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
| Python 3.10+ | Sim | Control Plane e geração do `.env`. Nenhum módulo exige 3.11; a imagem Docker do Scout já é 3.11. O Scout sobe no Docker |
| PowerShell 5.1+ | Sim | Scripts `.ps1` |
| Conta Ngrok (Authtoken) | Sim | `.env` |

**Primeira vez (cópia limpa):** `factory_reset.bat` → **`Setup.bat`** → `iniciar_servicos.ps1` → checklist n8n (secção **Primeira instalação**). Máquina já configurada: Setup + `iniciar_servicos.ps1` basta.

ExecutionPolicy (se necessário):

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Rede
- Rede Docker `rede_comunicacao` — o **Setup.bat** cria automaticamente; manual só se necessário
- Portas livres: 5676, 5677, 5678, 4040, 4050, 8765, 3000, 3030, 4000, 9090, 8501, 8502

---

## Estrutura do repositório

```text
N8Groker/
├── Setup.bat / setup_projeto.ps1    # Preparar ambiente
├── factory_reset.bat / .ps1         # Reset de dados (cópia do projeto)
├── iniciar_servicos.ps1             # Orquestrador + HUD
├── setup.sh                         # Setup Linux (Docker, Python, pull)
├── .env.example                     # Modelo; segredos nascem no Setup
├── .env_template                    # Cópia do modelo (scripts antigos)
├── Arquivos-n8n/                    # Arquivos dos fluxos n8n
├── scripts/init_env.py              # Gera segredos no .env
├── control_plane/                   # Painel Streamlit
├── llm/                             # Langfuse + LiteLLM
├── Porteiro/porteiro.js
├── n8n/docker-compose.yml
├── ngrok/docker-compose.yml
├── Scout_OSINT_Docker/              # Scout Gate (Docker; gestão na aba Scout)
├── Workflows_para_Autenticação/
├── README.md
├── CONTRIBUTING.md
├── DOCUMENTACAO.md
└── docs/                        # Desenho de acesso multiusuário
```

---

## Encerramento

| Acção | Efeito |
|-------|--------|
| **Q** no HUD PowerShell | Shutdown seguro (Ngrok, n8n, Langfuse/LiteLLM, Scout, Porteiro e o Control Plane que este script abriu). Ollama e Docker Desktop só se este script os abriu nesta sessão. O Docker Desktop sai depois do `compose down` |
| **G** no HUD | Abre o navegador em `http://localhost:8501/?aba=scout`. Se o painel estiver fora, tenta iniciá-lo e avisa se não subir |
| **Parar n8n** ou **Parar Langfuse e LiteLLM** | `docker compose down` daquela stack, sem `-v`, com confirmação. O núcleo não tem botão de parar |

---

## Paridade da aba Scout

A janela Tk (`Scout_network.py` e `scout/app.py`) existe no `master`. Neste ramo ela foi removida. A seção Scout chama os mesmos caminhos HTTP da API em `8765`, pelo módulo `Scout_OSINT_Docker/scout/gate.py`. O login do painel protege a seção. Ação que muda estado pede **Confirmar**.

| Controle da janela (master) | Neste ramo | API |
| --- | --- | --- |
| Cabeçalho ONLINE/OFFLINE e resumo Activos/Total/Novos | Estado e resumo no topo | `GET /health` |
| Sync Docker | Sincronizar Docker | `POST /redirections/sync` |
| Porta manual (host `host.docker.internal`, começa desligada) | Incluir porta manual | `POST /redirections/manual` |
| Sync Firewall | Sincronizar firewall | `POST /firewall/sync` |
| Tabela de rotas (modo, nome, origem, porta, upstream, ativo) | Cartão de cada rota | `GET /redirections` |
| Combobox de modo com a rota docker ou manual ativa | Gravar modo | `PATCH /redirections/{id}` |
| Checkbox Activo | Ativar ou Desativar, com confirmação | `POST /redirections/toggle` |
| Aviso antes de ativar `n8n_app` | O mesmo texto; sem Confirmar a API não é chamada | `POST /redirections/toggle` |
| Recusa ativar `ngrok_service` | Recusa igual, sem chamar a API | — |
| Edição de rota manual e troca de `127.0.0.1:5677/5678` por `host.docker.internal` | Salvar rota manual | `PATCH /redirections/{id}` |
| Apagar rota manual | Apagar rota manual | `DELETE /redirections/{id}` |
| Tráfego, alertas, alias e bloquear IP | As mesmas tabelas e botões | `GET /traffic`, `POST /aliases`, `POST /blocklist` |
| Clientes: alias, bloquear, remover | Os três botões na linha | `POST /aliases`, `POST /blocklist`, `DELETE /clients/{ip}` |
| Config: URL, health, ngrok, firewall, docker | Endereço HTTP, Health Check e o mesmo texto | `GET /health` |
| Log curto no rodapé | Registro da sessão do navegador | — |
| Desbloquear não estava no menu; a API já existia | Desbloquear quando o IP já está bloqueado | `DELETE /blocklist/{ip}` |

Não portado, de propósito:

| Controle da janela | Motivo |
| --- | --- |
| Tick WebSocket a cada segundo | O Streamlit não segura esse socket. **Atualizar Scout** lê os mesmos dados por HTTP. |
| Bipe do Windows quando chega alerta | O som era da janela Tk. O alerta aparece na lista. |
| Fechar a janela e perguntar se encerra ngrok, n8n, Scout e Porteiro | Não há janela. O desligamento do núcleo é **Q** no HUD. As stacks param pelos botões da seção Operações. |

O chat de suporte só lê Scout (`scout_resumo`, `scout_rotas`, `scout_trafego`, `scout_clientes`). Toggle, alias, bloqueio, firewall e porta manual ficam na aba, com confirmação. Reiniciar o container `scout-backend` continua na ferramenta já confirmada `reiniciar_servico`.

## CI

O workflow `.github/workflows/ci.yml` roda em push e em pull request. No Ubuntu: `python -m pytest tests`. No Windows, com `pwsh`: o Parser do `iniciar_servicos.ps1` e o PSScriptAnalyzer só em severidade Error (aviso de `Write-Host` não falha o job). Se a PSGallery já está registrada, o job não chama `Register-PSRepository` de novo. Se não está, usa `Register-PSRepository -Default` com nova tentativa. A instalação do PSScriptAnalyzer 1.25.0 também repete, e o módulo fica em cache. Se a galeria ou a instalação falhar, o job falha e diz que a análise não foi executada. As actions ficam presas no commit da release, não numa tag móvel.

---

## Borda (ngrok, Scout, Streamlit)

O desenho aprovado está na seção 12 de [`docs/desenho-acesso-multiusuario.md`](docs/desenho-acesso-multiusuario.md). O que já está no código vem descrito abaixo sem a palavra «proposto». O que ainda falta continua marcado.

O desenho seguinte (HUD-admin, HUD-user e Keeper) está em [`docs/desenho/desenho-hud-admin-user-keeper.md`](docs/desenho/desenho-hud-admin-user-keeper.md).

Já no código: as portas `5677`, `4040`, `3000`, `4000` e `4050` escutam em `127.0.0.1`. Com Scout, o ngrok usa `scout-backend` e a porta do portal na rede Docker. Na `4050` o Scout lê o pedido HTTP, inclusive `Upgrade: websocket`, e só então copia os dois lados. O IP do visitante é o último valor de `X-Forwarded-For`, e só quando o peer TCP é o endereço que o DNS de `ngrok_service` devolve naquela hora. O gateway `172.18.0.1` e os outros containers não entram nessa confiança: o header que eles mandam é ignorado. Esse tráfego não vira localhost nem admin. As outras rotas MITM continuam copiando bytes e não são o alvo do ngrok. Antes de abrir esse pipe, o Scout consulta a fila em `http://host.docker.internal:5676/n8n/fila` com o token do painel, montado só em `/run/porteiro-painel.token`. IP pendente recebe 202 e IP bloqueado recebe 403, sem upstream. A função `exige_porteiro` é a única que entrega host e porta de n8n (`n8n_app:5678`), LiteLLM (`litellm:4000`), Langfuse (`langfuse-web:3000`) e de qualquer outro id registrado: ela consulta o Porteiro e só liga se conta, IP e origem estiverem aprovados e o vínculo estiver ativo. Sem essa tríade não há socket. O cookie `n8groker_sessao` já vale em cada pedido e em cada WebSocket. É HttpOnly, Secure, SameSite Lax, Path `/`, Ed25519. A chave privada fica em `.n8groker/sessao.key` e entra no Scout só para a renovação deslizante (`/run/sessao.key`, leitura). `admin.key` e o hash das senhas não entram. Cada uso aceito empurra o prazo (`N8GROKER_SESSAO_SEG`, padrão 900). Reset de senha, troca de listas ou revogação na aba Admin sobem a geração em `.n8groker/sessoes-geracao.json` e o quadro seguinte do WebSocket cai. Sem cookie, a raiz responde 403 e o texto não cita o painel. `/painel` continua sendo o login. Cookie com IP diferente do lido na hora é 403. Se a conexão cai, o mesmo cookie, o mesmo IP e a mesma origem religam no mesmo app, sem voltar a `/painel`. No HUD da borda, Abrir aponta para `/painel/escolher?app=`. O Scout só grava o app na sessão e responde 303 para `/` depois que `exige_porteiro` confirma conta, IP e origem. A raiz com esse cookie é o app, no caminho original, sem subpath. App fora de `abrir`, ou raiz sem app escolhido, é 403 e o Porteiro não é consultado. `/painel` em si, com o IP aprovado na fila, vai para `host.docker.internal:8502` (o Streamlit da borda é processo do host). O script `Scout_OSINT_Docker/scout/static/origem.js` é servido em `/origem.js` e em `/painel/origem.js`, no mesmo origem, sem sessão. A página 202 «Acesso em Analise» (Scout, e o Porteiro no caminho direto) carrega esse script. O navegador gera a chave ECDSA P-256 não exportável e o ID e chama `/painel/registrar-origem` antes do Aprovar. O Scout grava a origem na fila com o token de admin; o navegador não vê o token e o IP da query é ignorado. Sem JavaScript ou sem WebCrypto a página diz que a origem não foi registrada. Cada GET `/painel` chama `/n8n/tocar`: cria o pendente se não existir, senão soma `tentativas` e grava `ultima_vista`. O script não soma de novo. Aprovar sem `origem` não marca dispositivo. Duas origens no mesmo IP: a nova fica pendente, a já aprovada continua, e a aba Admin mostra os dois IDs. Um segundo navegador no IP já aprovado entra como origem pendente: o painel carrega `/painel/origem.html`, não um srcdoc. Sem a chave HMAC em arquivo, `/painel` responde 503 e a trilha grava `BLOQUEIO (cabeçalho do Porteiro ausente)`. App que abre grava `LIBERADO`. Não há campo MAC. Nove prefixos negados em dez segundos bloqueiam o IP no Scout (`SCOUT_SCAN_CAMINHOS`, padrão 8, janela 10s, bloqueio 900s). Assets de `/painel` não contam. A linha `varredura` entra em `.n8groker/audit.jsonl`. A aba Admin desbloqueia só esse IP, em `127.0.0.1:8765`, e grava `desbloquear_varredura`. Alias e blocklist da aba Scout não são apagados.

O que o Gabriel fechou e as fases seguintes implementam:

Há um domínio só. Não há subpath por app (`/painel/n8n` não existe). O n8n avisa que `N8N_PATH` junto de proxy reverso quebra a navegação de pastas e manda usar subdomínio ([variáveis de deployment](https://docs.n8n.io/deploy/host-n8n/configure-n8n/basic-configuration/use-environment-variables/deployment)). `N8N_BASE_PATH` não está nessa tabela oficial. O Langfuse grava `NEXT_PUBLIC_BASE_PATH` na imagem e não aceita a imagem pronta `langfuse/langfuse:4.30.0` para isso ([custom base path](https://langfuse.com/self-hosting/configuration/custom-base-path)). O LiteLLM tem `SERVER_ROOT_PATH` desde a 1.72.3 ([custom root](https://docs.litellm.ai/docs/proxy/custom_root_ui)), mas um subpath só nele deixaria os três apps com regras diferentes. O túnel deste repositório é um só.

O usuário entra em `/painel`, faz login e escolhe o app no HUD. O Scout guarda o id na sessão (`n8n`, `litellm`, `langfuse` ou outro que esteja em `abrir`) e, na saída do painel, serve esse app na raiz, com o caminho original. Trocar de app é voltar a `/painel` e escolher de novo. Sessão nova também passa pelo painel.

Antes de ligar o upstream de qualquer app liberado, o Scout consulta o Porteiro. A consulta vale para n8n, LiteLLM, Langfuse e qualquer outro id. Confere conta, IP e origem. Sem essa confirmação o upstream não abre. Não há atalho que pule o Porteiro. O editor da máquina continua direto em `http://127.0.0.1:5678` e `http://localhost:5678`, fora do túnel.

Quem chega na raiz sem sessão recebe 403, sem redirect e sem texto que revele o painel. O login só existe em `/painel`.

Se a conexão cair, o cookie de sessão e o ID de origem continuam. O WebSocket e os pedidos seguintes ligam de novo no mesmo app, sem voltar a `/painel`, enquanto o cookie estiver no prazo, o IP for o mesmo e a origem for a mesma. O usuário só volta ao `/painel` se o cookie expirar, se ele sair, se o admin revogar a conta ou se o IP mudar. IP novo vai para a aprovação. A renovação é deslizante: cada uso aceito empurra o prazo.

Não há MAC. O webhook local leva impressão, origem, origem já aprovada, navegador, sistema, idioma e horário. País fica vazio.

A aba Scout do console (`http://localhost:8501`, só a sessão admin) lista as sessões ativas: id da sessão, id de origem, conta, IP, app atual, início e há quanto tempo está ativa. **Revogar sessão** derruba aquele cookie no pedido seguinte. O Scout grava cada checkpoint em `.n8groker/trilha/trilha.jsonl`, com o resultado, inclusive o bloqueio de rede Docker antes de `/painel`. A rotação usa `N8GROKER_TRILHA_MAX_BYTES` (padrão 1048576), `N8GROKER_TRILHA_COPIAS` (padrão 3) e `N8GROKER_TRILHA_DIAS` (padrão 7). A pasta é a única montada no Scout para isso; `admin.key` não entra. A linha legível junta os passos, por exemplo `203.0.113.8 > /painel > login ok > Porteiro ok > n8n > LIBERADO` e `203.0.113.8 > /n8n direto > BLOQUEIO (rota sem painel)`. Senha, token e cookie não são gravados.

Alertas não bloqueiam, mesmo quando o acesso foi liberado. As regras, com os padrões entre parênteses: pulou `/painel` e o Porteiro; trocas de app (`N8GROKER_ALERTA_TROCAS` 4 em `N8GROKER_ALERTA_TROCAS_SEG` 60); hora UTC fora da faixa da conta em `.n8groker/trilha/horarios.json` (`{"ana": {"inicio": 8, "fim": 18}}`); a mesma conta em dois IPs ao mesmo tempo; bloqueios seguidos de um liberado (`N8GROKER_ALERTA_BLOQUEIOS` 3). O alerta aparece em destaque na aba, com **Visto**, e vai para `audit.jsonl` como `alerta_trilha`. A mesma conta no mesmo IP com outra origem não dispara o alerta de dois IPs.

### Webhook opcional de alerta

No container do Scout o padrão já vai no ambiente: `N8GROKER_ALERTA_WEBHOOK=http://n8n_app:5678/webhook/alerta-trilha`. O alerta faz um GET, sem corpo e sem token:

`GET http://n8n_app:5678/webhook/alerta-trilha?regra=&ip=&conta=&sid=&origem=&app=&detalhe=&hora=`

O host aceito é `n8n_app`, `host.docker.internal`, `127.0.0.1` ou `localhost`, caminho começando em `/webhook/`, só `http`. De dentro do container, `127.0.0.1:5678` é reescrito para `n8n_app`, porque o loopback ali é o próprio Scout. Outro host é ignorado. O editor da máquina continua em `http://127.0.0.1:5678`.

O fluxo para importar está em [`Workflows_para_Autenticação/Alerta de trilha.json`](Workflows_para_Autenticação/Alerta%20de%20trilha.json). No n8n da máquina (`http://127.0.0.1:5678`): menu de workflows, Import from File, escolha esse JSON e ative. Enquanto não estiver importado e ativo, `http://127.0.0.1:5678/webhook/alerta-trilha` responde 404. O webhook é GET, sem corpo e sem token, com `regra`, `ip`, `conta`, `sid`, `origem`, `app`, `detalhe` e `hora`. O nó de email vem desligado; é só exemplo. Um workflow já importado não muda sozinho quando o JSON do repositório muda: apague o antigo ou importe de novo e ative. Do container do Scout o mesmo caminho é `http://n8n_app:5678/webhook/alerta-trilha`.

---

## Licença

O código deste projeto é [Zero-Clause BSD (0BSD)](LICENSE): livre para qualquer uso, sem exigência de atribuição. O texto oficial do SPDX, com copyright `2026 Gabriel Antunes`, está em [`LICENSE`](LICENSE) e permanece sem alteração:

```text
Copyright (C) 2026 Gabriel Antunes

Permission to use, copy, modify, and/or distribute this software for any purpose with or without fee is hereby granted.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
```

Nota de comunidade, não vinculante: dar crédito ao projeto original é opcional e fica a critério de quem usa. Se o projeto virar outra coisa, não há necessidade nenhuma de crédito. Esta nota não altera a licença e não é condição de uso.

Os componentes de terceiros não são redistribuídos neste repositório. As imagens oficiais são baixadas na subida, e o Ollama, quando `USE_OLLAMA_LOCAL=1`, é o programa oficial instalado na máquina. Cada um mantém a licença própria, conferida na fonte do projeto:

| Componente | O que este repositório usa | Licença | Fonte |
| --- | --- | --- | --- |
| n8n | imagem `docker.n8n.io/n8nio/n8n` | Sustainable Use License no código geral. Arquivos com `.ee.` no nome exigem a n8n Enterprise License | [LICENSE.md](https://github.com/n8n-io/n8n/blob/master/LICENSE.md) |
| Ollama | programa oficial na máquina | MIT | [LICENSE](https://github.com/ollama/ollama/blob/main/LICENSE) |
| ngrok | imagem `ngrok/ngrok` | Termos de Serviço da ngrok, licença de uso do agente | [Terms of Service](https://ngrok.com/tos) |
| Langfuse | imagens `langfuse/langfuse:4.30.0` e `langfuse/langfuse-worker:4.30.0` | MIT Expat fora de `ee/`. O que está em `ee/` é a Langfuse Enterprise license | [LICENSE em v4.30.0](https://github.com/langfuse/langfuse/blob/v4.30.0/LICENSE) |
| LiteLLM | imagem `ghcr.io/berriai/litellm:v1.103.1` | MIT fora de `enterprise/`. O diretório `enterprise/` é a BerriAI Enterprise license | [LICENSE em v1.103.1](https://github.com/BerriAI/litellm/blob/v1.103.1/LICENSE) |

## Suporte

Consulte [`DOCUMENTACAO.md`](DOCUMENTACAO.md) para diagramas, fluxos de aprovação, variáveis de ambiente, segurança Zero Trust e recomendações operacionais.
