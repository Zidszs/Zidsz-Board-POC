#!/usr/bin/env bash
# Setup do N8Groker no Linux.
# Idempotente: se a ferramenta já existe, não reinstala.
# Pergunta antes de instalar qualquer coisa no sistema.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

confirm() {
  local answer
  read -r -p "$1 [s/N] " answer
  [[ "$answer" =~ ^[sSyY]$ ]]
}

have() { command -v "$1" >/dev/null 2>&1; }

python_ok() {
  local bin="$1"
  "$bin" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

find_python() {
  if have python3 && python_ok python3; then
    echo python3
    return 0
  fi
  if have python && python_ok python; then
    echo python
    return 0
  fi
  return 1
}

install_docker_debian() {
  echo "Vou usar o repositório apt oficial do Docker (docs.docker.com), com sudo."
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc
  local codename
  codename="$(. /etc/os-release && echo "${VERSION_CODENAME}")"
  local arch
  arch="$(dpkg --print-architecture)"
  echo "deb [arch=${arch} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${codename} stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}

echo "N8Groker — setup Linux"
echo "Pasta: $ROOT"
echo

if have docker && docker compose version >/dev/null 2>&1; then
  echo "[ok] Docker e o plugin compose já estão instalados."
else
  echo "[falta] Docker Engine com plugin compose."
  if confirm "Instalar Docker pelo repositório oficial?"; then
    if [[ -f /etc/os-release ]] && grep -Eq 'ubuntu|debian' /etc/os-release; then
      # Debian usa o mesmo método; o codename entra na linha do repo.
      # Em Debian puro a URL ubuntu falha. Detectar ID.
      . /etc/os-release
      if [[ "${ID}" == "debian" ]]; then
        echo "Debian detectado. Trocando a URL do repo para download.docker.com/linux/debian."
        sudo apt-get update
        sudo apt-get install -y ca-certificates curl
        sudo install -m 0755 -d /etc/apt/keyrings
        sudo curl -fsSL "https://download.docker.com/linux/debian/gpg" -o /etc/apt/keyrings/docker.asc
        sudo chmod a+r /etc/apt/keyrings/docker.asc
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
        sudo apt-get update
        sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
      else
        install_docker_debian
      fi
    else
      echo "Esta distro não é Ubuntu/Debian. Siga https://docs.docker.com/engine/install/ e rode o setup de novo."
    fi
  else
    echo "Instalação do Docker cancelada."
  fi
fi

PY=""
if PY="$(find_python)"; then
  echo "[ok] Python: $PY ($($PY -V 2>&1))"
else
  echo "[falta] Python 3.10+."
  if confirm "Instalar python3, venv e pip pelo apt?"; then
    if have apt-get; then
      sudo apt-get update
      sudo apt-get install -y python3 python3-venv python3-pip
      PY="$(find_python || true)"
    else
      echo "apt-get não encontrado. Instale Python 3.10+ pelo gerenciador da sua distro."
    fi
  else
    echo "Instalação do Python cancelada."
  fi
fi

if [[ -n "${PY}" ]]; then
  echo
  "$PY" "$ROOT/scripts/init_env.py" || echo "Não foi possível gerar o .env."
else
  echo "Sem Python, o .env não foi gerado. Rode de novo depois de instalar."
fi

VENV="$ROOT/control_plane/.venv"
if [[ -x "$VENV/bin/python" ]] && "$VENV/bin/python" -c "import streamlit" >/dev/null 2>&1; then
  echo "[ok] Dependências do Control Plane já estão no venv."
elif [[ -n "${PY}" ]]; then
  if confirm "Criar control_plane/.venv e instalar requirements do Control Plane?"; then
    "$PY" -m venv "$VENV"
    "$VENV/bin/python" -m pip install --upgrade pip
    "$VENV/bin/python" -m pip install --no-cache-dir -r "$ROOT/control_plane/requirements.txt"
  else
    echo "Instalação das dependências do Control Plane cancelada."
  fi
fi

if have docker && docker info >/dev/null 2>&1; then
  if docker network inspect rede_comunicacao >/dev/null 2>&1; then
    echo "[ok] Rede Docker rede_comunicacao."
  else
    echo "Criando a rede rede_comunicacao (não pede pacote novo)."
    docker network create rede_comunicacao
  fi
  if confirm "Baixar as imagens do Langfuse e do LiteLLM (docker compose pull)?"; then
    if [[ -f "$ROOT/.env" ]]; then
      docker compose -f "$ROOT/llm/docker-compose.yml" --env-file "$ROOT/.env" pull
    else
      echo "Sem .env não dá para interpolar o compose. Rode o setup de novo."
    fi
  else
    echo "Pull cancelado."
  fi
else
  echo "Docker não está no ar. Abra o serviço e rode o pull depois:"
  echo "  docker compose -f llm/docker-compose.yml --env-file .env pull"
fi

if ! have node; then
  echo
  echo "Node.js não encontrado. O Porteiro precisa de Node 16+."
  echo "No Ubuntu/Debian: https://nodejs.org/ ou o pacote nodejs da distro, se for 16+."
  if have apt-get && confirm "Tentar instalar o pacote nodejs da distro?"; then
    sudo apt-get install -y nodejs
  fi
fi

echo
echo "Control Plane:"
echo "  $VENV/bin/python -m streamlit run control_plane/app.py"
echo "Stack:"
echo "  docker compose -f llm/docker-compose.yml --env-file .env up -d"
echo "Preencha NGROK_AUTHTOKEN no .env antes do iniciar_servicos (no Windows) ou do ngrok."
