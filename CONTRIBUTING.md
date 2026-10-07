# Contribuindo ao N8Groker

Repositório: **https://github.com/Zidszs/N8Groker-POC**

Obrigado por contribuir. Este guia resume o fluxo para quem clona o repositório pela primeira vez ou envia melhorias via pull request.

---

## Clone → primeira execução

```text
git clone https://github.com/Zidszs/N8Groker-POC.git N8Groker
cd N8Groker
Setup.bat                  # dependências, .env, rede Docker, pastas
iniciar_servicos.ps1       # sobe Porteiro, n8n, ngrok, Scout
```

Numa **cópia de teste**, pode rodar `factory_reset.bat` antes (confirme com `Excluir`) para simular instalação limpa.

Depois configure no n8n (uma vez):

1. Criar conta admin em `http://localhost:5678`
2. Importar `Workflows_para_Autenticação/Aprovacao de Acesso (Novo).json`
3. Configurar SMTP no node **Email e Espera Aprovacao**
4. Editar `admin_email` no node **Configuracoes**
5. **Ativar** o workflow

Documentação completa: [`README.md`](README.md) e [`DOCUMENTACAO.md`](DOCUMENTACAO.md) (seção 16).

---

## O que **não** commitar

- `.env` (raiz) — copie de `.env_template`
- `n8n/data/*` e `n8n/n8n/data/*` (SQLite n8n)
- `n8n/storage/Porteiro/*` (estado do Porteiro)
- `Scout_OSINT_Docker/data/`, `.venv/`, logs, `*.sqlite*`, `.porteiro.pid`

O `.gitignore` já cobre estes arquivos. Antes de push:

```powershell
git status
git add -n .
```

---

## Factory reset

Use `factory_reset.bat` **apenas numa cópia** do projeto para voltar ao estado “repo limpo”. Apaga `.env`, bases de dados, storage Porteiro, dados Scout e recria pastas com `.gitkeep`. **Não** use na pasta principal de desenvolvimento.

---

## Pull requests

1. Faça fork de https://github.com/Zidszs/N8Groker-POC
2. Crie uma branch para a sua alteração
3. Descreva o problema ou melhoria no PR
4. Confirme que **não há segredos** no diff (`.env`, SQLite, tokens)
5. Indique como testou: `Setup.bat` → `iniciar_servicos.ps1` → n8n (se aplicável)

Issues e sugestões também são bem-vindas no repositório.
