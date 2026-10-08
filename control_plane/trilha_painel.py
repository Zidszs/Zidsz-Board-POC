"""Sessões, trilha e alertas na aba Scout. Só o admin do console."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import streamlit as st

_RAIZ = Path(__file__).resolve().parents[1] / "Scout_OSINT_Docker"
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from scout.core.trilha import Trilha, pasta_de, texto_duracao  # noqa: E402

from control_plane.trilha_auth import (
    avisos_de_tela,
    ler_auth,
    linhas_auth,
    mensagens_da_revogacao,
    revogar_sessao,
)


def render_trilha_admin(root: Path) -> None:
    st.markdown("**Sessões, trilha e alertas**")
    st.caption(
        "Só esta sessão admin, no console. A trilha fica em `.n8groker/trilha/trilha.jsonl`. "
        "Não guarda senha, token nem cookie. Alerta não bloqueia o acesso. "
        "A faixa amarela de rajada (5 auth.sessao do mesmo IP em 60 s, "
        "N8GROKER_AUTH_RAJADA e N8GROKER_AUTH_RAJADA_SEG) e a de motivo origem "
        "ficam só nesta tela: não vão para alertas.jsonl e não chamam o webhook."
    )
    aviso = st.session_state.pop("trilha_aviso", "")
    if aviso:
        st.success(aviso)
    erro = st.session_state.pop("trilha_erro", "")
    if erro:
        st.error(erro)
    for faixa in avisos_de_tela(ler_auth(root)):
        st.warning(faixa)
    grade = Trilha(pasta_de(root))
    agora = int(time.time())
    sessoes = grade.sessoes()
    if not sessoes:
        st.caption("Nenhuma sessão ativa.")
    for item in sessoes:
        sid = str(item.get("sid") or "")
        st.markdown(f"**{item.get('conta') or '—'}** · {item.get('ip') or '—'}")
        st.caption(
            f"sessão {sid} · origem {item.get('origem') or '—'} · "
            f"app {item.get('app') or '—'} · início {item.get('inicio_hora') or '—'} · "
            f"ativa há {texto_duracao(int(item.get('inicio') or agora), agora)}"
        )
        if st.button("Revogar sessão", key="trilha_revogar_" + sid):
            aviso_revogacao, erro_revogacao = mensagens_da_revogacao(
                revogar_sessao(
                    root,
                    sid,
                    conta=str(item.get("conta") or ""),
                    ip=str(item.get("ip") or ""),
                )
            )
            if aviso_revogacao:
                st.session_state["trilha_aviso"] = aviso_revogacao
            if erro_revogacao:
                st.session_state["trilha_erro"] = erro_revogacao
            st.rerun()
    st.markdown("**Trilha**")
    filtro = st.text_input("IP ou sessão", key="trilha_filtro")
    texto = str(filtro or "").strip()
    if len(texto) == 16 and all(c in "0123456789abcdef" for c in texto):
        linhas = grade.legiveis(sid=texto)
    else:
        linhas = grade.legiveis(ip=texto)
    if not linhas:
        st.caption("Nenhum checkpoint nessa leitura.")
    for linha in linhas[-30:]:
        st.code(linha, language=None)
    st.markdown("**Autenticação**")
    st.caption(
        "Passos de auth em `.n8groker/trilha/auth.jsonl`, no máximo as 30 linhas do fim do arquivo. "
        "O código do motivo fica neste arquivo. A tela de entrar mostra só o próximo passo. "
        "Recusa de sessão no túnel entra aqui como auth.sessao, com o sid de 16 hex. "
        "Filtrar pela sessão mostra essas linhas. "
        "A janela de 20 do alerta e o webhook não leem este arquivo."
    )
    if len(texto) == 16 and all(c in "0123456789abcdef" for c in texto):
        autenticacao = linhas_auth(root, sid=texto)
    else:
        autenticacao = linhas_auth(root, ip=texto)
    if not autenticacao:
        st.caption("Nenhum passo de auth nessa leitura.")
    for linha in autenticacao[-30:]:
        st.code(linha, language=None)
    st.markdown("**Alertas**")
    st.caption("O alerta não muda o veredito. Um acesso liberado continua liberado.")
    alertas = grade.alertas()
    if not alertas:
        st.caption("Nenhum alerta.")
    for alerta in reversed(alertas[-40:]):
        frase = (
            f"{alerta.get('hora') or ''} · {alerta.get('regra') or ''} · "
            f"{alerta.get('conta') or '—'} · {alerta.get('ip') or '—'} · "
            f"{alerta.get('detalhe') or ''}"
        )
        if alerta.get("visto"):
            st.caption("visto · " + frase)
            continue
        st.warning(frase)
        identificador = str(alerta.get("id") or "")
        if identificador and st.button("Visto", key="trilha_visto_" + identificador):
            grade.marcar_visto(identificador)
            st.rerun()
