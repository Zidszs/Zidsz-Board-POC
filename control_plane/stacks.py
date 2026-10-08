"""Stacks de app fora do núcleo de segurança.

O núcleo (Porteiro, Scout, ngrok, painéis e a rede Docker) sobe no
`iniciar_servicos.ps1`. n8n e a stack LLM sobem por botão no console ou por
`iniciar_servicos.ps1 -Stack n8n|llm`. `STACKS_BOOT` lista o que sobe
junto com o núcleo; vazio deixa só o núcleo. O botão «Iniciar núcleo»
não lê essa variável.
"""

from __future__ import annotations

from dataclasses import dataclass

KNOWN_STACKS = ("n8n", "llm")

# A mesma lista padrão do compose e do iniciar_servicos.ps1. Não é um lugar
# novo para alargar ou encolher o bloqueio: só converte N8N_NODES_EXCLUDE.
_NODES_PADRAO = (
    "n8n-nodes-base.executeCommand,n8n-nodes-base.ssh,"
    "n8n-nodes-base.readWriteFile,n8n-nodes-base.localFileTrigger,"
    "n8n-nodes-base.readBinaryFile,n8n-nodes-base.readBinaryFiles,"
    "n8n-nodes-base.writeBinaryFile"
)

LLM_FORA_DO_AR = (
    "A stack LLM está fora do ar. O n8n sobe mesmo assim. "
    "A credencial OpenAI aponta para http://litellm:4000/v1 e só responde quando "
    "Langfuse e LiteLLM estiverem no ar. Isso não é erro."
)


@dataclass(frozen=True)
class StackSpec:
    id: str
    title: str
    compose: str
    operar: tuple[str, ...]


STACKS: dict[str, StackSpec] = {
    "n8n": StackSpec("n8n", "n8n", "n8n/docker-compose.yml", ("n8n",)),
    "llm": StackSpec(
        "llm",
        "Langfuse e LiteLLM",
        "llm/docker-compose.yml",
        ("langfuse", "litellm"),
    ),
}


def stack_spec(stack_id: str) -> StackSpec:
    spec = STACKS.get(stack_id)
    if spec is None:
        raise KeyError(stack_id)
    return spec


def stacks_boot(raw: str | None) -> tuple[str, ...]:
    """Ids conhecidos, na ordem escrita, sem duplicata. Token desconhecido sai."""
    vistos: list[str] = []
    for token in _tokens(raw):
        if token not in KNOWN_STACKS or token in vistos:
            continue
        vistos.append(token)
    return tuple(vistos)


def stacks_desconhecidas(raw: str | None) -> tuple[str, ...]:
    vistos: list[str] = []
    for token in _tokens(raw):
        if token in KNOWN_STACKS or token in vistos:
            continue
        vistos.append(token)
    return tuple(vistos)


def nodes_exclude_json(lista: str | None) -> str:
    texto = (lista or "").strip() or _NODES_PADRAO
    if texto.startswith("["):
        return texto
    itens = []
    for parte in texto.split(","):
        nome = parte.strip().replace('"', "").replace("\\", "")
        if nome:
            itens.append('"' + nome + '"')
    return "[" + ",".join(itens) + "]"


def _tokens(raw: str | None) -> list[str]:
    if not raw:
        return []
    saida = []
    for parte in str(raw).split(","):
        token = parte.strip().lower()
        if token:
            saida.append(token)
    return saida
