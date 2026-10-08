# Tour em vídeo da interface

> **A regravar depois da reforma** (lote 7).

Gravação de 39 segundos, 1920×1200, 8,4 MB: `tour_interface_control_plane.mp4`. O arquivo não entrou no git. Oito megabytes passam do que cabe como anexo leve em `docs/`. A tela gravada é a do desktop da VM; o pedido pedia algo perto de 1280×800, e o tamanho ficou abaixo de 15 MB.

A gravação usa o console em `http://127.0.0.1:8501` e o `/painel` pelo Porteiro em `http://127.0.0.1:5677/painel`. Essa gravação ainda mostra login com senha. A tela atual não tem senha: o admin emite um JWT e a pessoa cola em **Token de acesso**. Nenhum segredo real entra aqui, e nenhuma conta de demonstração fica gravada.

O que cada rótulo da tela faz, com as capturas tela a tela, está em [`GUIA-DE-USO.md`](GUIA-DE-USO.md).

## Roteiro

| Trecho | O que a tela mostra |
| --- | --- |
| Console, Infraestrutura | Legenda 🟢 No ar, 🟡 Resposta estranha, 🔴 Fora do ar. Porteiro no ar. n8n, ngrok, Scout, Langfuse, LiteLLM e MinIO fora do ar. |
| Reiniciar container | No cartão do n8n, a frase `Docker não está no PATH. Abra o Docker Desktop e tente de novo.` |
| Acesso rápido | Atalhos de Langfuse, LiteLLM e n8n com a frase de que Abrir não carrega enquanto o programa estiver parado. Portainer avisa que não faz parte desta stack. |
| Iniciar infraestrutura | A gravação ainda mostra o botão único e a frase do Docker. A tela atual tem **Iniciar núcleo**, **Iniciar n8n** e **Iniciar Langfuse e LiteLLM**. O núcleo não tem botão de parar. |
| Scout | Sessão, trilha e um alerta de exemplo. Em seguida, o aviso de que o Scout não respondeu em `127.0.0.1:8765`. |
| Admin | A gravação mostra uma conta com Pode ver, Pode operar e Pode abrir. A tela atual emite token, sem senha. A fila diz `Nenhum IP pendente ou aprovado.` |
| Chat de suporte | O Ollama não respondeu em `http://localhost:11434`. O resto do painel continua. |
| Sair e `/painel` | A gravação mostra troca de senha e `Aguardando aprovação`. A tela atual pede **Token de acesso**. Sem vínculo de IP, o token válido ainda cai em `Aguardando aprovação`. |

## O que a navegação achou e o que mudou

| Tela | Problema | Correção |
| --- | --- | --- |
| Seções do console | O clique em Scout, Admin ou Chat voltava para Infraestrutura. A URL antiga ganhava do rádio. | O clique fica. O link `?aba=` ainda abre a seção na primeira visita. |
| Cartões | Selo `Offline` / `Online` em inglês. Serviço parado parecia falha da tela. | `No ar`, `Fora do ar`, `Resposta estranha`. A legenda diz que fora do ar é programa que não está em execução. |
| Atalho | A palavra `Placeholder`. | O Portainer diz que não faz parte desta stack. Se o cartão está fora do ar, Abrir avisa que não carrega. |
| n8n | O botão `Reiniciar` não dizia que mexe no container. | O rótulo é `Reiniciar container`. Sem Docker, a frase é a do PATH, não um traceback. |
| Versões | Vários avisos azuis empurravam o status e pareciam erro. | Ficam num expansor: não é falha e o painel não atualiza sozinho. |
| Parar infraestrutura | Passo com falha seria mostrado como concluído. | Passo sem `ok` aparece como aviso, com o detalhe. |
| Diagnóstico | Eixo `online` / `offline` e o texto `start/restart`. | Eixo em português. O histórico fala em início ou reinício. |
| CPU | Tabela vazia sem dizer por quê. | Diz que o Docker não respondeu ou não há container. |
| Admin | `Ver`, `Operar` e `Abrir` vazios ao editar uma conta. `Gravar listas` apagaria o que a legenda mostrava. O placeholder do widget era `Choose options`. | Os três campos carregam as listas já gravadas. Rótulos `Pode ver`, `Pode operar` e `Pode abrir`. Placeholder `Escolha os apps`. |
| Fila | `Nenhum IP pendente ou aprovado sem esta leitura.` Sem botão para tentar de novo. Corpo JSON de recusa iria cru para a tela. | Frase direta, botão `Atualizar fila`, e motivo em `reason` / `error` / `detail` / `texto`. |
| Scout, ação recusada | HTTP de erro com `ok: false` caía no texto de “não respondeu”, porque `HTTPError` é `URLError`. | O corpo é lido e o `reason` aparece. |
| Trilha | `Revogar sessão` não tirava a linha na hora. | A lista é lida de novo e o aviso fica. |
| Conta sem lista | A frase não dizia quem marca a permissão. | Aponta a aba Admin. |

O item `Select all` do multiselect continua em inglês. É texto do Streamlit, não um rótulo nosso. Trocar o widget inteiro por caixas não entrou neste passe.

Depois dessa gravação, a seção Operações deixou de ter um único **Iniciar infraestrutura** / **Parar infraestrutura**. O `iniciar_servicos.ps1` sobe só o núcleo (Porteiro, Scout, ngrok e os painéis). `STACKS_BOOT` lista o que sobe junto; vazio deixa só o núcleo. n8n e Langfuse/LiteLLM ligam e desligam por stack, no console ou com `.\iniciar_servicos.ps1 -Stack n8n` (e `llm`). O console não desliga o núcleo: a tecla Q do HUD é quem encerra o núcleo e as stacks que estiverem no ar.

O login também mudou depois do vídeo. Não há senha nem troca de senha. O admin emite um JWT na aba Admin (**Emitir token**). A pessoa cola em **Token de acesso**, no console e no `/painel`. **Rotacionar chave** invalida os tokens já emitidos e derruba o cookie na próxima requisição. O vídeo não foi regravado. O guia tem a tela nova.

## O que foi simulado

- n8n, LiteLLM e Langfuse ficaram desligados de propósito. Não houve `docker compose`.
- O Scout de verdade não subiu. A aba mostra o aviso de backend fora. A sessão, a trilha `LIBERADO` e o alerta `pulou_etapas` da captura foram gravados nesta máquina por `scout.core.trilha.Trilha`, para a tela não ficar vazia, e não são uma conta persistida. Não houve tráfego no túnel. O webhook de alerta não foi chamado: a URL não estava definida.
- O Porteiro (`node porteiro.js`, portas 5677 e 5676) e os dois Streamlit (8501 console, 8502 borda) rodaram de verdade. A chave HMAC e os tokens de teste nasceram em `.n8groker/`, fora do git.
- O chat não fala com modelo. O Ollama não está nesta VM.

## Limitações

- Sem túnel ngrok, sem Docker Desktop e sem os três apps pesados.
- No `/painel`, um token válido com o IP `127.0.0.1` sem vínculo cai em `Aguardando aprovação`. A escolha de app (Acesso rápido) aparece no console, para quem tem `Pode abrir` no token.
- Sem Scout na frente, o atalho Abrir da borda não é o `/painel/escolher` do portal.
- Reteste no Windows: as frases novas são as da tela. O boot com Docker, o ngrok e o vínculo de um IP público continuam no checklist que já existia. Não há passo novo obrigatório além de olhar a legenda `Fora do ar` com a stack parada e conferir que Scout, Admin e Chat trocam de seção.
