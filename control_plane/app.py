"""Control Plane do N8Groker.

Subir com:
    streamlit run control_plane/app.py
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st

st.set_page_config(
    page_title="Control Plane N8Groker",
    page_icon="🎛️",
    layout="wide",
    initial_sidebar_state="expanded",
)

from control_plane.deps import mensagem_dependencia, modulos_ausentes

_ausentes = modulos_ausentes()
if _ausentes:
    st.error(mensagem_dependencia(_ausentes))
    st.stop()

from control_plane.abas import sincronizar_aba
from control_plane.actor import AccessDenied, set_actor
from control_plane.admin_token import aceitar
from control_plane.auth import (
    AuthError,
    create_user,
    entrar_com_token,
    list_users,
    service_ids,
    sessao_do_token,
    update_lists,
)
from control_plane.edge_auth import client_ip_from_header, descrever_chave
from control_plane.binding import mensagem_espera
from control_plane.porteiro_admin import FilaError, aplicar, liberar, listar_fila, plano_da_fila
from control_plane.porteiro_token import token_painel
from control_plane.backup import (
    BackupError,
    backup_catalog,
    build_backup_plan,
    build_restore_plan,
    execute_plan,
    list_backups,
    stamp_now,
)
from control_plane.config import SERVICE_DESCRIPTIONS, ServiceSpec, Settings, load_settings
from control_plane.diagnostics import (
    gather_diagnostic_zip,
    load_series,
    note_marker,
    record_results,
    thermometer,
)
from control_plane.envfile import read_env_file
from control_plane.health import BADGES, check_services
from control_plane.operations import (
    OperationError,
    launch_orchestrator,
    restart_container,
    restart_stack,
    start_infrastructure,
    start_stack,
    stop_stack,
)
from control_plane.stacks import LLM_FORA_DO_AR
from control_plane.scout_client import api_base
from control_plane.scout_panel import render_scout_tab
from control_plane.stats import STATS_TTL_SECONDS, cached_stats, read_docker_stats
from control_plane.versions import ensure_version_check, read_cached_report
from control_plane.support_chat import (
    NeedsConfirmation,
    OllamaError,
    ToolDecision,
    apply_model_message,
    build_system_prompt,
    chat_config_from_env,
    check_ollama,
    ollama_chat,
    perform,
    pull_model,
    tool_schemas,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("control_plane.app")


def _button(label: str, **kwargs):
    try:
        return st.button(label, use_container_width=True, **kwargs)
    except TypeError:
        kwargs.pop("use_container_width", None)
        return st.button(label, width="stretch", **kwargs)


def ancora_abrir(url: str, rotulo: str = "Abrir") -> str:
    """Link no documento do Streamlit. link_button abre outra aba e o clique fica em /painel/."""
    from html import escape

    destino = escape(str(url or ""), quote=True)
    texto = escape(str(rotulo or "Abrir"))
    return f'<a href="{destino}" target="_top">{texto}</a>'


def _link(label: str, url: str):
    if str(url or "").startswith("/painel/escolher"):
        st.markdown(ancora_abrir(url, label), unsafe_allow_html=True)
        return
    try:
        st.link_button(label, url, use_container_width=True)
    except TypeError:
        try:
            st.link_button(label, url, width="stretch")
        except TypeError:
            st.markdown(f"[{label}]({url})")


def _guard(action):
    try:
        return action()
    except (OperationError, AccessDenied) as exc:
        st.error(exc.message)
    except Exception:
        logger.exception("operacao do control plane")
        st.error("Algo inesperado impediu a operação. Nenhum detalhe técnico foi exibido aqui.")
    return None


def _copy_block(service_id: str) -> None:
    copy = SERVICE_DESCRIPTIONS.get(service_id)
    if copy is None:
        return
    st.caption(copy.summary)
    with st.expander("Para que serve"):
        st.write(copy.detail)


def _avisar_chave_borda(settings: Settings) -> None:
    if _modo() != "edge":
        return
    texto = descrever_chave(settings.root)
    if not texto:
        return
    logger.error(texto)
    st.error(texto)


def _origem_no_navegador() -> None:
    if _modo() != "edge":
        return
    import streamlit.components.v1 as components

    # components.html usa srcdoc. Nesse iframe o WebCrypto não vê contexto
    # seguro, então a segunda origem no IP já aprovado não entra na fila.
    # A página /painel/origem.html é o mesmo documento do túnel, em HTTPS.
    components.iframe("/painel/origem.html", height=0, scrolling=False)


def _ticket_borda(settings: Settings) -> str:
    if _modo() != "edge" or not st.session_state.get("cp_authenticated"):
        return ""
    if st.session_state.get("cp_must_change") or st.session_state.get("cp_aguardando"):
        return ""
    from control_plane.sessao_borda import ticket_de_login
    from control_plane.auth import AuthError

    try:
        return ticket_de_login(
            settings.root,
            str(st.session_state.get("cp_user") or ""),
            list(st.session_state.get("cp_abrir") or []),
            str(st.session_state.get("cp_ip") or ""),
        )
    except (AuthError, OSError):
        return ""


def _sessao_no_navegador(settings: Settings) -> None:
    if _modo() != "edge":
        return
    ticket = _ticket_borda(settings)
    if not ticket:
        return
    import streamlit.components.v1 as components

    # O login do Streamlit não grava n8groker_sessao. Este iframe pede o cookie
    # ao Scout antes do clique em Abrir. O link também leva o ticket, para o
    # primeiro clique não depender desta resposta.
    components.iframe("/painel/sessao?t=" + ticket, height=0, scrolling=False)


def _url_atalho(shortcut, settings: Settings | None = None) -> str:
    if _modo() == "edge" and not shortcut.placeholder:
        base = "/painel/escolher?app=" + shortcut.id
        if settings is not None:
            ticket = _ticket_borda(settings)
            if ticket:
                return base + "&t=" + ticket
        return base
    return shortcut.url


def _render_shortcuts(settings: Settings) -> None:
    st.subheader("Acesso rápido")
    items = [item for item in settings.shortcuts if _admin_sessao() or _pode("abrir", item.id)]
    for offset in range(0, len(items), 4):
        chunk = items[offset : offset + 4]
        columns = st.columns(len(chunk))
        for column, shortcut in zip(columns, chunk):
            with column:
                with st.container(border=True):
                    st.markdown(f"**{shortcut.title}**")
                    _copy_block(shortcut.id)
                    _link("Abrir", _url_atalho(shortcut, settings))
                    if shortcut.placeholder:
                        st.caption("Não faz parte desta stack. A porta 9000 só abre se houver um Portainer seu.")
                    else:
                        for item in st.session_state.get("health_cache") or []:
                            if getattr(item, "service_id", None) == shortcut.id and getattr(item, "state", "") == "offline":
                                st.caption("Fora do ar. Abrir não carrega enquanto este programa estiver parado.")
                                break


def _service_card(spec: ServiceSpec, result, settings: Settings) -> None:
    with st.container(border=True):
        st.markdown(f"**{spec.title}**")
        _copy_block(spec.id)
        if not spec.monitored:
            st.caption("Sem checagem HTTP: não publica porta neste painel.")
        elif result is None:
            st.caption("Ainda sem checagem.")
        else:
            st.markdown(BADGES.get(result.state, result.state))
            st.caption(result.detail)
        if spec.container_name:
            st.caption(f"Container `{spec.container_name}`")
        elif spec.monitored:
            st.caption("Processo no host. Sem reinício por container.")
        else:
            st.caption("Peça interna ou programa no host. Sem botão de reinício.")
        if spec.critical and spec.container_name and spec.monitored and (_admin_sessao() or _pode("operar", spec.id)):
            if _button("Reiniciar container", key=f"restart_{spec.id}"):
                outcome = _guard(
                    lambda name=spec.container_name, sid=spec.id: restart_container(
                        name,
                        timeout=settings.restart_timeout_seconds,
                        service_id=sid,
                    )
                )
                if outcome is not None:
                    _note_lifecycle(settings, spec.id, "restart")
                    st.toast(f"{spec.title}: {outcome.detail}")
                    st.session_state.pop("health_cache", None)


def _render_status_grid(settings: Settings, results_by_id: dict) -> None:
    groups = (
        ("n8groker", "Stack N8Groker"),
        ("llm", "Langfuse, LiteLLM e MinIO"),
        ("dados", "Bancos internos, sem porta no host"),
    )
    for group_id, title in groups:
        specs = [
            spec
            for spec in settings.services
            if spec.group == group_id and (_admin_sessao() or _pode("ver", spec.id) or _pode("operar", spec.id))
        ]
        if not specs:
            continue
        st.markdown(f"**{title}**")
        for offset in range(0, len(specs), 4):
            chunk = specs[offset : offset + 4]
            columns = st.columns(len(chunk))
            for column, spec in zip(columns, chunk):
                with column:
                    _service_card(spec, results_by_id.get(spec.id), settings)


def _note_lifecycle(settings: Settings, service_id: str, kind: str) -> None:
    try:
        note_marker(settings.root, service_id, kind)
    except Exception:
        logger.warning("marcador de %s nao gravou para %s", kind, service_id)


def _record_health(settings: Settings, results) -> None:
    rows = [(item.service_id, item.state, item.latency_ms) for item in results]
    try:
        record_results(settings.root, rows)
    except Exception:
        logger.warning("historico de saude nao gravou")


def _probe(settings: Settings):
    results = check_services(
        settings.services,
        settings.health_timeout_seconds,
        settings.slow_threshold_ms,
    )
    _record_health(settings, results)
    return results


def _status_section(settings: Settings) -> None:
    # Esta seção é um fragmento: o clique não roda o script inteiro de novo.
    # Sem republicar o ator, Reiniciar container vê sessão vazia e recusa o admin.
    _publicar_ator(settings)
    slow = int(settings.slow_threshold_ms)
    timeout = settings.health_timeout_seconds
    st.caption(
        f"🟢 No ar: resposta esperada e até {slow} ms. "
        f"🟡 Resposta estranha: HTTP diferente ou acima de {slow} ms. "
        f"🔴 Fora do ar: o programa não está escutando, ou o tempo esgotou ({timeout:g}s). "
        "Fora do ar não é falha desta tela. n8n, LiteLLM e Langfuse ficam assim enquanto não foram iniciados. "
        "O botão abaixo refaz só esta seção e grava um ponto no histórico."
    )
    if "health_cache" not in st.session_state:
        with st.spinner("Consultando serviços..."):
            st.session_state["health_cache"] = _probe(settings)
    if _button("🔄 Atualizar Status", key="refresh_status"):
        with st.spinner("Consultando serviços..."):
            st.session_state["health_cache"] = _probe(settings)
    results = {item.service_id: item for item in st.session_state.get("health_cache", [])}
    _render_status_grid(settings, results)


def _mostrar_passos(report) -> None:
    for step in report.steps:
        if step.ok and "não é erro" in (step.detail or ""):
            st.info(step.detail)
        elif step.ok:
            st.success(f"{step.title}: concluído.")
        else:
            st.warning(f"{step.title}: {step.detail or 'não concluído.'}")


def _llm_fora_do_ar() -> bool:
    cache = st.session_state.get("health_cache") or []
    por_id = {getattr(item, "service_id", ""): item for item in cache}
    for service_id in ("litellm", "langfuse"):
        item = por_id.get(service_id)
        if item is None or getattr(item, "state", "") == "offline":
            return True
    return False


def _ve_operacoes() -> bool:
    if _modo() == "edge":
        return False
    if _admin_sessao():
        return True
    return (
        _pode("operar", "n8n")
        or _pode("operar", "langfuse")
        or _pode("operar", "litellm")
    )


def _operations(settings: Settings) -> None:
    _publicar_ator(settings)
    st.subheader("Operações")
    st.caption(
        "O núcleo é Porteiro, Scout (se USE_SCOUT=1), ngrok e a rede Docker `rede_comunicacao`. "
        "Os painéis 8501 e 8502 sobem com o HUD. n8n e Langfuse/LiteLLM são stacks à parte: "
        "cada uma liga, para e reinicia sozinha. A URL pública do ngrok continua no HUD."
    )

    st.markdown("**Núcleo**")
    st.caption(
        "Este console não desliga o núcleo. Parar daqui fecharia o painel ou o túnel "
        "e trancaria quem está administrando, inclusive de longe. "
        "A tecla Q no HUD encerra o núcleo e as stacks que estiverem no ar."
    )
    if _admin_sessao():
        if _button("Iniciar núcleo", key="iniciar_nucleo", type="primary"):
            report = _guard(
                lambda: start_infrastructure(
                    settings.root,
                    scout_build=settings.scout_build,
                    compose_timeout=settings.compose_timeout_seconds,
                )
            )
            if report is not None:
                st.session_state.pop("health_cache", None)
                if report.ok:
                    for spec in settings.services:
                        if spec.id in {"porteiro", "scout", "ngrok"}:
                            _note_lifecycle(settings, spec.id, "start")
                _mostrar_passos(report)
                if report.ok:
                    st.toast("Núcleo iniciado.")
    else:
        st.caption("Só o admin inicia o núcleo.")

    if _admin_sessao() or _pode("operar", "n8n"):
        st.markdown("**n8n**")
        if _llm_fora_do_ar():
            st.info(LLM_FORA_DO_AR)
        iniciar, parar, reiniciar = st.columns(3)
        with iniciar:
            if _button("Iniciar n8n", key="iniciar_n8n"):
                report = _guard(
                    lambda: start_stack(settings.root, "n8n", compose_timeout=settings.compose_timeout_seconds)
                )
                if report is not None:
                    st.session_state.pop("health_cache", None)
                    if report.ok:
                        _note_lifecycle(settings, "n8n", "start")
                    _mostrar_passos(report)
        with parar:
            if _button("Parar n8n", key="parar_n8n"):
                st.session_state["confirmar_parada_n8n"] = True
        with reiniciar:
            if _button("Reiniciar n8n", key="reiniciar_n8n"):
                report = _guard(
                    lambda: restart_stack(settings.root, "n8n", compose_timeout=settings.compose_timeout_seconds)
                )
                if report is not None:
                    st.session_state.pop("health_cache", None)
                    if report.ok:
                        _note_lifecycle(settings, "n8n", "restart")
                    _mostrar_passos(report)
        if st.session_state.get("confirmar_parada_n8n"):
            st.warning("Isso derruba o n8n (`docker compose down`, sem apagar volumes). A stack LLM não é mexida.")
            confirmar, cancelar = st.columns(2)
            with confirmar:
                if _button("Confirmar parada do n8n", key="confirmar_parada_n8n_ok", type="primary"):
                    st.session_state["confirmar_parada_n8n"] = False
                    report = _guard(
                        lambda: stop_stack(
                            settings.root,
                            "n8n",
                            confirmed=True,
                            compose_timeout=settings.compose_timeout_seconds,
                        )
                    )
                    if report is not None:
                        st.session_state.pop("health_cache", None)
                        if report.ok:
                            _note_lifecycle(settings, "n8n", "stop")
                        _mostrar_passos(report)
            with cancelar:
                if _button("Cancelar", key="confirmar_parada_n8n_cancelar"):
                    st.session_state["confirmar_parada_n8n"] = False
                    st.rerun()

    if _admin_sessao() or (_pode("operar", "langfuse") and _pode("operar", "litellm")):
        st.markdown("**Langfuse e LiteLLM**")
        st.caption("As duas peças sobem e descem juntas. Quem só pode operar uma delas não liga a stack.")
        iniciar, parar, reiniciar = st.columns(3)
        with iniciar:
            if _button("Iniciar Langfuse e LiteLLM", key="iniciar_llm"):
                report = _guard(
                    lambda: start_stack(settings.root, "llm", compose_timeout=settings.compose_timeout_seconds)
                )
                if report is not None:
                    st.session_state.pop("health_cache", None)
                    if report.ok:
                        _note_lifecycle(settings, "langfuse", "start")
                        _note_lifecycle(settings, "litellm", "start")
                    _mostrar_passos(report)
        with parar:
            if _button("Parar Langfuse e LiteLLM", key="parar_llm"):
                st.session_state["confirmar_parada_llm"] = True
        with reiniciar:
            if _button("Reiniciar Langfuse e LiteLLM", key="reiniciar_llm"):
                report = _guard(
                    lambda: restart_stack(settings.root, "llm", compose_timeout=settings.compose_timeout_seconds)
                )
                if report is not None:
                    st.session_state.pop("health_cache", None)
                    if report.ok:
                        _note_lifecycle(settings, "langfuse", "restart")
                        _note_lifecycle(settings, "litellm", "restart")
                    _mostrar_passos(report)
        if st.session_state.get("confirmar_parada_llm"):
            st.warning(
                "Isso derruba Langfuse e LiteLLM (`docker compose down`, sem apagar volumes). "
                "O n8n, se estiver no ar, continua de pé e a credencial deixa de responder."
            )
            confirmar, cancelar = st.columns(2)
            with confirmar:
                if _button("Confirmar parada da stack LLM", key="confirmar_parada_llm_ok", type="primary"):
                    st.session_state["confirmar_parada_llm"] = False
                    report = _guard(
                        lambda: stop_stack(
                            settings.root,
                            "llm",
                            confirmed=True,
                            compose_timeout=settings.compose_timeout_seconds,
                        )
                    )
                    if report is not None:
                        st.session_state.pop("health_cache", None)
                        if report.ok:
                            _note_lifecycle(settings, "langfuse", "stop")
                            _note_lifecycle(settings, "litellm", "stop")
                        _mostrar_passos(report)
            with cancelar:
                if _button("Cancelar", key="confirmar_parada_llm_cancelar"):
                    st.session_state["confirmar_parada_llm"] = False
                    st.rerun()
    elif _pode("operar", "langfuse") or _pode("operar", "litellm"):
        st.markdown("**Langfuse e LiteLLM**")
        st.caption("Para ligar ou desligar esta stack é preciso Pode operar em Langfuse e em LiteLLM.")

    if _admin_sessao():
        with st.expander("Abrir o HUD do núcleo (iniciar_servicos.ps1)"):
            st.caption(
                "Abre o orquestrador do núcleo numa janela nova, no Windows. "
                "Esse script sobe Porteiro, Scout, ngrok e os painéis em 8501 e 8502. "
                "n8n e Langfuse/LiteLLM só entram no boot se STACKS_BOOT pedir. "
                "Fechar a janela ou pressionar Q encerra o núcleo e as stacks que estiverem no ar. "
                "Ollama e o Docker Desktop só fecham se o script os abriu nesta sessão."
            )
            if _button("Abrir HUD do núcleo", key="abrir_hud"):
                result = _guard(lambda: launch_orchestrator(settings.root))
                if result is not None:
                    st.success(result.detail)


def _env_values(settings: Settings) -> dict[str, str]:
    path = settings.root / ".env"
    if not path.is_file():
        return {}
    try:
        return read_env_file(path)
    except Exception:
        logger.warning("nao consegui ler o .env para o chat")
        return {}


def _fresh_health(settings: Settings) -> dict:
    results = check_services(
        settings.services,
        settings.health_timeout_seconds,
        settings.slow_threshold_ms,
    )
    return {item.service_id: item for item in results}


def _support_chat(settings: Settings) -> None:
    st.subheader("Chat de suporte")
    env = _env_values(settings)
    config = chat_config_from_env(env)
    st.caption(
        f"Ollama em `{config.base_url}`, modelo `{config.model}`. "
        "Status e logs rodam na hora. Iniciar, parar e reiniciar só depois de Confirmar. "
        "Este chat não desliga o Control Plane. Um modelo de 7B pode errar um passo."
    )
    st.session_state.setdefault("support_messages", [])
    st.session_state.setdefault("support_pending", None)

    probe_error = None
    try:
        check_ollama(config, timeout=3)
    except OllamaError as exc:
        probe_error = exc.message
    if probe_error:
        st.warning(probe_error)
        if f"ollama pull {config.model}" in probe_error:
            st.code(f"ollama pull {config.model}")
            if st.session_state.get("support_pull_ask"):
                st.info("O download só começa se você confirmar. Não é um comando livre.")
                yes, no = st.columns(2)
                with yes:
                    if _button("Confirmar download", key="support_pull_ok"):
                        st.session_state["support_pull_ask"] = False
                        try:
                            st.success(pull_model(config.model))
                            st.rerun()
                        except OllamaError as exc:
                            st.error(exc.message)
                with no:
                    if _button("Cancelar download", key="support_pull_no"):
                        st.session_state["support_pull_ask"] = False
                        st.rerun()
            elif _button("Baixar modelo", key="support_pull"):
                st.session_state["support_pull_ask"] = True
                st.rerun()

    for msg in st.session_state["support_messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    pending = st.session_state.get("support_pending")
    if isinstance(pending, dict):
        st.warning(pending.get("message") or "Ação pendente.")
        yes, no = st.columns(2)
        with yes:
            if _button("Confirmar", key="support_confirm", type="primary"):
                decision = ToolDecision(
                    "confirm",
                    str(pending.get("message") or ""),
                    tool=str(pending.get("tool") or ""),
                    service_id=str(pending.get("service_id") or ""),
                )
                try:
                    text = perform(
                        decision,
                        confirmed=True,
                        services=settings.services,
                        health_by_id=_fresh_health(settings),
                        env=env,
                        root=settings.root,
                    )
                except NeedsConfirmation:
                    text = "A confirmação não chegou na execução. Nada foi feito."
                except Exception:
                    logger.exception("acao do chat")
                    text = "A ação não pôde ser concluída. Nenhum detalhe técnico foi exibido aqui."
                st.session_state["support_messages"].append(
                    {"role": "assistant", "content": f"Ação executada: {text}"}
                )
                st.session_state["support_pending"] = None
                st.session_state.pop("health_cache", None)
                st.rerun()
        with no:
            if _button("Cancelar", key="support_cancel"):
                st.session_state["support_messages"].append(
                    {"role": "assistant", "content": "Ação cancelada. Nada foi executado."}
                )
                st.session_state["support_pending"] = None
                st.rerun()

    if _button("Limpar conversa", key="support_clear"):
        st.session_state["support_messages"] = []
        st.session_state["support_pending"] = None
        st.rerun()

    prompt = st.chat_input(
        "Pergunte como usar a stack",
        disabled=bool(pending) or bool(probe_error),
    )
    if not prompt or pending or probe_error:
        return

    st.session_state["support_messages"].append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    health_by_id = _fresh_health(settings)
    history = [
        {"role": item["role"], "content": item["content"]}
        for item in st.session_state["support_messages"][-12:]
        if item.get("role") in {"user", "assistant"}
    ]
    messages = [{"role": "system", "content": build_system_prompt(settings.services, health_by_id)}, *history]
    with st.chat_message("assistant"):
        placeholder = st.empty()
        collected: list[str] = []

        def on_delta(piece: str) -> None:
            collected.append(piece)
            placeholder.markdown("".join(collected))

        try:
            reply = ollama_chat(config, messages, tool_schemas(settings.services), on_delta=on_delta)
            visible, pending_decision = apply_model_message(
                reply,
                services=settings.services,
                health_by_id=health_by_id,
                env=env,
                root=settings.root,
            )
        except OllamaError as exc:
            visible = exc.message
            pending_decision = None
        except Exception:
            logger.exception("chat de suporte")
            visible = "Não consegui falar com o Ollama. Nada foi executado."
            pending_decision = None
        placeholder.markdown(visible)
    st.session_state["support_messages"].append({"role": "assistant", "content": visible})
    if pending_decision is not None:
        st.session_state["support_pending"] = {
            "message": pending_decision.message,
            "tool": pending_decision.tool,
            "service_id": pending_decision.service_id,
        }
        st.rerun()


_METRIC_LABEL = {
    "latencia": "latência",
    "tempo_ate_saudavel": "tempo até ficar saudável",
}
_STATE_LEVEL = {"online": 2, "degraded": 1, "offline": 0}


def _diagnostics(settings: Settings) -> None:
    from control_plane.keeper_cliente import chamar, consultar_uma_vez, linha_diagnostico

    st.subheader("Diagnóstico")
    resposta_keeper = consultar_uma_vez({}, lambda: chamar(settings.root, "status"))
    linha_keeper = linha_diagnostico(resposta_keeper)
    if linha_keeper:
        st.warning(linha_keeper)
    st.caption(
        "Cada atualização de status grava hora, serviço, estado, latência e o tempo até voltar "
        "a responder depois de uma queda, de um início ou de um reinício. O arquivo fica em `.n8groker/` "
        "(fora do git) e amostras com mais de 7 dias são apagadas."
    )
    try:
        series = load_series(settings.root)
        flags = thermometer(series)
    except Exception:
        logger.warning("leitura do historico falhou")
        series = {}
        flags = []
    slow_ids = {flag.service_id for flag in flags}
    if flags:
        st.warning("Termômetro: estes serviços passaram do p90 recente (acima da mediana).")
        for flag in flags:
            label = _METRIC_LABEL.get(flag.metric, flag.metric)
            st.markdown(
                f"- **{flag.service_id}** · {label}: {flag.latest:.0f} ms "
                f"(mediana {flag.median:.0f} ms, p90 {flag.p90:.0f} ms)"
            )
    elif series:
        st.caption("Termômetro: nenhum serviço acima do próprio p90. Com menos de 5 pontos anteriores não há linha de base.")
    else:
        st.caption("Termômetro: ainda sem histórico. Use Atualizar Status para gravar o primeiro ponto.")

    titles = {spec.id: spec.title for spec in settings.services}
    for service_id, samples in series.items():
        title = titles.get(service_id, service_id)
        st.markdown(f"**{title}**")
        if service_id in slow_ids:
            st.caption("Mais lento que o normal deste serviço.")
        latencies = [sample.latency_ms if sample.latency_ms is not None else 0 for sample in samples]
        states = [_STATE_LEVEL.get(sample.status, 0) for sample in samples]
        st.line_chart({"latência (ms)": latencies, "estado (2 no ar, 1 estranho, 0 fora do ar)": states})

    if _button("Gerar diagnóstico", key="gerar_diag"):
        cache = st.session_state.get("health_cache") or []
        status_rows = [
            {
                "service_id": item.service_id,
                "status": item.state,
                "detail": item.detail,
                "latency_ms": item.latency_ms,
                "recorded_at": "",
            }
            for item in cache
        ]
        try:
            st.session_state["diag_zip"] = gather_diagnostic_zip(settings.root, status_rows, _env_values(settings))
        except Exception:
            logger.warning("zip de diagnostico falhou")
            st.error("Não consegui montar o zip de diagnóstico.")
    if st.session_state.get("diag_zip"):
        st.download_button(
            "Baixar diagnóstico (.zip)",
            data=st.session_state["diag_zip"],
            file_name="diagnostico-n8groker.zip",
            mime="application/zip",
            key="baixar_diag",
        )
        st.caption("O zip traz status, histórico, logs recentes, compose ps e versões. Segredo de chave sai como «redigido».")


def _container_stats() -> None:
    st.subheader("CPU e memória")
    st.caption("Leitura de `docker stats --no-stream`, reaproveitada por 20 segundos para não travar o painel.")
    now = time.time()

    def loader():
        return read_docker_stats(timeout=8)

    rows, cache = cached_stats(now, st.session_state.get("docker_stats"), loader, ttl=STATS_TTL_SECONDS)
    st.session_state["docker_stats"] = cache
    if not rows:
        st.caption(
            "Sem leitura de CPU e memória. O Docker não respondeu, ou não há container em execução. "
            "O restante do painel segue."
        )
        return
    lines = ["| Container | CPU | Memória |", "| --- | --- | --- |"]
    for row in rows:
        name = row.name.replace("|", "/")
        cpu = row.cpu.replace("|", "/")
        memory = row.memory.replace("|", "/")
        lines.append(f"| `{name}` | {cpu} | {memory} |")
    st.markdown("\n".join(lines))


def _backups(settings: Settings) -> None:
    st.subheader("Backup e restauração")
    st.caption(
        "Os arquivos ficam em `.n8groker/backups`, fora do git. Postgres usa `pg_dump` "
        "(a senha não vai na linha de comando). O n8n copia o SQLite. ClickHouse e MinIO "
        "arquivam o volume com tar. Restaurar pede confirmação e para os serviços que usam "
        "esse dado antes de gravar."
    )
    catalog = backup_catalog()
    labels = {item_id: label for item_id, label in catalog}
    target = st.selectbox(
        "O que guardar",
        options=[item_id for item_id, _label in catalog],
        format_func=lambda item_id: labels[item_id],
        key="backup_alvo",
    )
    if _button("Fazer backup", key="fazer_backup"):
        try:
            plan = build_backup_plan(target, settings.root, _env_values(settings), stamp=stamp_now())
            execute_plan(plan)
            st.success("Backup gravado em .n8groker/backups.")
        except BackupError as exc:
            st.error(exc.message)
        except Exception:
            logger.warning("backup falhou")
            st.error("Não consegui fazer o backup.")
    names = []
    try:
        names = list_backups(settings.root, target)
    except BackupError as exc:
        st.caption(exc.message)
    if not names:
        st.caption("Nenhum backup deste alvo nesta máquina.")
        return
    chosen = st.selectbox("Arquivo para restaurar", options=list(reversed(names)), key="backup_arquivo")
    confirmed = st.checkbox(
        "Eu confirmo a restauração. Os serviços afetados serão parados antes.",
        key=f"backup_confirma_{target}",
    )
    if _button("Restaurar", key="restaurar_backup"):
        if not confirmed:
            st.error("Marque a confirmação. Nada foi restaurado.")
        else:
            try:
                plan = build_restore_plan(
                    target,
                    settings.root,
                    _env_values(settings),
                    source_name=chosen,
                    confirmed=True,
                )
                execute_plan(plan)
                st.session_state.pop("health_cache", None)
                st.success("Restauração concluída. Os serviços que tinham sido parados foram iniciados de novo.")
            except BackupError as exc:
                st.error(exc.message)
            except Exception:
                logger.warning("restore falhou")
                st.error("Não consegui restaurar.")


def _sidebar(settings: Settings) -> None:
    with st.sidebar:
        st.header("Atalhos")
        for shortcut in settings.shortcuts:
            if _admin_sessao() or _pode("abrir", shortcut.id):
                _link(shortcut.title, _url_atalho(shortcut, settings))
        st.divider()
        st.caption("Stack local")
        for spec in settings.services:
            if spec.id in {"ngrok", "porteiro", "scout"} and (_admin_sessao() or _pode("abrir", spec.id)):
                _link(spec.title, spec.link_url)
        st.divider()
        if _button("Sair", key="auth_logout"):
            _limpar_sessao()
            st.rerun()
        st.caption(f"Raiz: `{settings.root}`")
        st.caption("Compose: n8n, ngrok, Scout_OSINT_Docker, llm")


def _version_notice(settings: Settings) -> None:
    if not st.session_state.get("version_check_started"):
        st.session_state["version_check_started"] = True
        ensure_version_check(settings.root)
    cached = read_cached_report(settings.root)
    if cached is None:
        return
    _checked, report = cached
    if not report.ok and not report.notices:
        return
    if report.notices:
        with st.expander(
            f"Versões mais novas no registro ({len(report.notices)}). Não é falha: o painel não atualiza sozinho."
        ):
            for notice in report.notices:
                st.caption(notice.message)
    if report.ok and not report.notices:
        st.caption("Imagens pinadas conferidas. Nenhuma tag mais nova. O painel não atualiza sozinho.")
    if report.notes:
        with st.expander("Tags sem comparação numérica"):
            for note in report.notes:
                st.caption(note)


def _modo() -> str:
    return os.environ.get("PANEL_MODE", "").strip()


def _admin_sessao() -> bool:
    return bool(st.session_state.get("cp_admin"))


def _pode(acao: str, service_id: str) -> bool:
    if _admin_sessao():
        return True
    chave = {"ver": "cp_ver", "operar": "cp_operar", "abrir": "cp_abrir"}.get(acao, "")
    return service_id in set(st.session_state.get(chave) or [])


def _limpar_sessao() -> None:
    for chave in (
        "cp_authenticated",
        "cp_admin",
        "cp_user",
        "cp_must_change",
        "cp_aguardando",
        "cp_acesso",
        "cp_ver",
        "cp_operar",
        "cp_abrir",
        "cp_solicitei",
        "cp_por_cookie",
        "cp_admin_geracao",
        "porteiro_fila",
    ):
        st.session_state.pop(chave, None)


def _gravar_sessao(sessao: dict) -> None:
    st.session_state["cp_authenticated"] = True
    st.session_state["cp_admin"] = bool(sessao.get("admin"))
    st.session_state["cp_user"] = sessao.get("username") or ""
    st.session_state["cp_must_change"] = bool(sessao.get("must_change"))
    st.session_state["cp_aguardando"] = bool(sessao.get("aguardando"))
    st.session_state["cp_ver"] = list(sessao.get("ver") or [])
    st.session_state["cp_operar"] = list(sessao.get("operar") or [])
    st.session_state["cp_abrir"] = list(sessao.get("abrir") or [])
    if "admin_geracao" in sessao:
        st.session_state["cp_admin_geracao"] = int(sessao["admin_geracao"])


def _publicar_ator(settings: Settings) -> None:
    set_actor(
        {
            "admin": _admin_sessao(),
            "must_change": bool(st.session_state.get("cp_must_change")),
            "aguardando": bool(st.session_state.get("cp_aguardando")),
            "ver": list(st.session_state.get("cp_ver") or []),
            "operar": list(st.session_state.get("cp_operar") or []),
            "abrir": list(st.session_state.get("cp_abrir") or []),
            "username": st.session_state.get("cp_user") or "",
            "ip": st.session_state.get("cp_ip") or "",
            "root": str(settings.root),
        }
    )


def _texto_origem_edge(settings: Settings) -> str:
    """Aviso do navegador novo. O cookie vem do documento do túnel, não do iframe."""
    if _modo() != "edge":
        return ""
    from control_plane.binding import aviso_para_painel

    return aviso_para_painel(
        settings.root,
        str(st.session_state.get("cp_ip") or ""),
        _origem_do_pedido(),
    )


def _origem_do_pedido() -> str:
    from control_plane.binding import origem_do_cookie

    try:
        headers = st.context.headers
    except Exception:
        return ""
    if headers is None:
        return ""
    for chave in ("Cookie", "cookie"):
        try:
            valor = headers[chave]
        except Exception:
            valor = ""
        if valor:
            return origem_do_cookie(str(valor))
    return ""


def _ler_header_cliente() -> str:
    try:
        headers = st.context.headers
    except Exception:
        return ""
    if headers is None:
        return ""
    for chave in ("X-N8groker-Client", "x-n8groker-client"):
        try:
            valor = headers[chave]
        except Exception:
            valor = ""
        if valor:
            return str(valor)
    return ""


def _exigir_borda(settings: Settings) -> bool:
    if _modo() != "edge":
        st.session_state["cp_ip"] = "127.0.0.1"
        return True
    ip = client_ip_from_header(settings.root, _ler_header_cliente())
    if not ip:
        st.error("O painel de borda só abre com o cabeçalho assinado pelo Porteiro.")
        return False
    st.session_state["cp_ip"] = ip
    return True


def sessao_admin_aberta(estado: dict) -> bool:
    """Sessão de admin já aberta não relê admin-jti.json."""
    return bool((estado or {}).get("cp_authenticated")) and bool((estado or {}).get("cp_admin"))


AVISO_GIRO_ADMIN = "A chave de admin girou. Cole um token novo. O anterior não entra."


def encerrar_admin_se_girou(estado: dict, root: Path) -> bool:
    """Fecha a sessão admin quando admin-geracao subiu. Não mexe em sessão de usuário."""
    from control_plane.admin_token import geracao_confere

    if not sessao_admin_aberta(estado):
        return False
    if geracao_confere(estado, root):
        return False
    for chave in (
        "cp_authenticated",
        "cp_admin",
        "cp_user",
        "cp_must_change",
        "cp_aguardando",
        "cp_acesso",
        "cp_ver",
        "cp_operar",
        "cp_abrir",
        "cp_solicitei",
        "cp_por_cookie",
        "cp_admin_geracao",
        "porteiro_fila",
    ):
        estado.pop(chave, None)
    return True


def preparar_campo(estado: dict, chave: str) -> None:
    """Esvazia o campo antes de desenhar o widget, no rerun depois do envio."""
    if estado.pop(f"_limpar_{chave}", None):
        estado[chave] = ""


def marcar_campo(estado: dict, chave: str) -> None:
    estado[f"_limpar_{chave}"] = True


def _cookie_do_pedido() -> str:
    try:
        headers = st.context.headers
    except Exception:
        return ""
    if not headers:
        return ""
    try:
        valor = headers.get("cookie")
        if not valor:
            valor = headers.get("Cookie")
    except Exception:
        valor = ""
    return str(valor or "")


def _retomar_cookie(settings: Settings):
    if _modo() != "edge":
        return None
    from control_plane.sessao_borda import retomar_do_cookie

    return retomar_do_cookie(
        settings.root,
        _cookie_do_pedido(),
        str(st.session_state.get("cp_ip") or ""),
    )


def _entrar_por_cookie(settings: Settings) -> bool:
    sessao = _retomar_cookie(settings)
    if sessao is None:
        return False
    _gravar_sessao(sessao)
    st.session_state["cp_por_cookie"] = True
    return True


def _require_login(settings: Settings) -> bool:
    root = settings.root
    ip = str(st.session_state.get("cp_ip") or "")
    if st.session_state.get("cp_must_change"):
        _limpar_sessao()
    if encerrar_admin_se_girou(st.session_state, root):
        st.session_state["_erro_auth_admin_token"] = AVISO_GIRO_ADMIN
    elif sessao_admin_aberta(st.session_state):
        return True
    if st.session_state.get("cp_por_cookie"):
        if _entrar_por_cookie(settings):
            return True
        _limpar_sessao()
    if st.session_state.get("cp_authenticated") and not st.session_state.get("cp_admin"):
        try:
            sessao = sessao_do_token(
                root,
                str(st.session_state.get("cp_acesso") or ""),
                ip=ip,
                modo=_modo() or "console",
                passo="auth.sessao",
            )
        except AuthError as exc:
            _limpar_sessao()
            st.error(exc.message)
        else:
            _gravar_sessao(sessao)
            if sessao.get("aguardando"):
                _pedir_vinculo(ip, str(sessao.get("username") or ""))
            return True
    if st.session_state.get("cp_authenticated") is True:
        return True
    if _entrar_por_cookie(settings):
        return True
    st.subheader("Entrar")
    preparar_campo(st.session_state, "auth_token")
    preparar_campo(st.session_state, "auth_admin_token")
    erro_token = st.session_state.pop("_erro_auth_token", "")
    erro_admin = st.session_state.pop("_erro_auth_admin_token", "")
    if erro_token:
        st.error(str(erro_token))
    if _modo() == "edge":
        aviso_entrada = _texto_origem_edge(settings)
        if aviso_entrada:
            st.warning(aviso_entrada)
        st.caption("Cole o token que o dono emitiu. Não há senha nem cadastro nesta tela.")
        rotulo_token = "Token"
    else:
        st.caption("Cole o token que o admin emitiu. Não há senha nem cadastro nesta tela.")
        rotulo_token = "Token de acesso"
    token = st.text_area(rotulo_token, key="auth_token", height=80)
    if _button("Entrar", key="auth_login", type="primary"):
        marcar_campo(st.session_state, "auth_token")
        try:
            sessao = entrar_com_token(root, token, ip=ip, modo=_modo() or "console")
        except AuthError as exc:
            st.session_state["_erro_auth_token"] = exc.message
            st.rerun()
        else:
            from control_plane.auth import normalizar_token

            st.session_state["cp_acesso"] = normalizar_token(token)
            _gravar_sessao(sessao)
            if sessao.get("aguardando"):
                _pedir_vinculo(ip, str(sessao.get("username") or ""))
            st.rerun()
    if _modo() != "edge":
        st.divider()
        if erro_admin:
            st.error(str(erro_admin))
        st.caption("Sessão de admin da máquina. Cole o JWT no campo Token de admin. Ele não vai para a URL.")
        with st.form("cp_form_admin", clear_on_submit=True):
            admin = st.text_area("Token de admin", key="auth_admin_token", height=80)
            try:
                enviar_admin = st.form_submit_button("Abrir sessão admin", use_container_width=True)
            except TypeError:
                enviar_admin = st.form_submit_button("Abrir sessão admin")
        if enviar_admin:
            marcar_campo(st.session_state, "auth_admin_token")
            try:
                aceitar(root, admin, modo="console", ip=ip)
            except AuthError as exc:
                st.session_state["_erro_auth_admin_token"] = exc.message
                st.rerun()
            else:
                from control_plane.admin_token import numero_para_sessao

                _gravar_sessao(
                    {
                        "admin": True,
                        "username": "admin",
                        "must_change": False,
                        "aguardando": False,
                        "ver": [],
                        "operar": [],
                        "abrir": [],
                        "admin_geracao": numero_para_sessao(root),
                    }
                )
                st.rerun()
        st.caption(
            "Com o console já aberto: `python -m control_plane.admin_token`. "
            "Vale 5 minutos. A primeira chave é `--init`."
        )
    return False


def _pedir_vinculo(ip: str, username: str) -> None:
    if st.session_state.get("cp_solicitei"):
        return
    st.session_state["cp_solicitei"] = True
    try:
        from control_plane.porteiro_admin import agir

        agir("solicitar", ip, username)
    except (FilaError, OSError):
        return


def _render_varredura(settings: Settings) -> None:
    import json
    import urllib.request

    st.subheader("Varredura")
    st.caption("Desbloquear aqui só tira o IP da janela do Scout. Alias e blocklist da aba Scout ficam.")
    try:
        with urllib.request.urlopen("http://127.0.0.1:8765/varredura", timeout=2) as resposta:
            ips = json.loads(resposta.read().decode("utf-8")).get("ips") or []
    except (OSError, ValueError, json.JSONDecodeError):
        st.caption("Scout não informou IPs de varredura.")
        return
    if not ips:
        st.caption("Nenhum IP bloqueado por varredura.")
        return
    from control_plane.audit import auditar

    for ip in ips:
        if _button("Desbloquear " + str(ip), key="varredura_" + str(ip)):
            pedido = urllib.request.Request(
                "http://127.0.0.1:8765/varredura/desbloquear",
                data=json.dumps({"ip": ip}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urllib.request.urlopen(pedido, timeout=2).read()
            except OSError as exc:
                st.error("Não consegui desbloquear no Scout.")
                return
            auditar(settings.root, "admin", str(ip), "desbloquear_varredura", str(ip), "ok")
            st.success("IP liberado da varredura.")


def _render_admin(settings: Settings) -> None:
    from control_plane.inventario import texto_inventario
    from control_plane.keeper_cliente import (
        BANNER_KEEPER_FORA,
        KeeperFora,
        chamar,
        chave_liberada,
        consultar_uma_vez,
        emitir_token,
        revogar_conta_keeper,
        revogar_jti_keeper,
        rotacionar_keeper,
    )
    from control_plane.user_token import listar

    resposta_keeper = consultar_uma_vez({}, lambda: chamar(settings.root, "status"))
    keeper_ok = chave_liberada(resposta_keeper)
    if not keeper_ok:
        st.warning(BANNER_KEEPER_FORA)
    st.subheader("Inventário")
    st.caption("Leitura do disco. Esta tela não lê a chave privada.")
    try:
        st.markdown(texto_inventario(settings.root))
    except OSError:
        st.caption("Não consegui ler o inventário.")
    st.subheader("Contas")
    st.caption(
        "Não há senha. Pode ver, Pode operar e Pode abrir entram no token, na hora de emitir. "
        "Gravar listas só guarda o modelo do próximo token: um token já emitido não muda. "
        "O token aparece uma vez para copiar e não fica gravado em claro."
    )
    conhecidos = list(service_ids())
    usuario = st.text_input("Usuário da nova conta", key="admin_user")
    ver = st.multiselect(
        "Pode ver",
        conhecidos,
        key="admin_ver",
        help="Apps que a conta enxerga no painel.",
        placeholder="Escolha os apps",
    )
    operar = st.multiselect(
        "Pode operar",
        conhecidos,
        key="admin_operar",
        help="Apps em que a conta pode iniciar, parar ou mudar rota.",
        placeholder="Escolha os apps",
    )
    abrir = st.multiselect(
        "Pode abrir",
        conhecidos,
        key="admin_abrir",
        help="Apps que aparecem em Acesso rápido.",
        placeholder="Escolha os apps",
    )
    if _button("Criar conta", key="admin_create", type="primary"):
        try:
            create_user(settings.root, usuario, ver=list(ver), operar=list(operar), abrir=list(abrir))
        except AuthError as exc:
            st.error(exc.message)
        else:
            st.success(f"Conta {usuario.strip()} criada. Emita um token para ela entrar.")
    ip_admin = str(st.session_state.get("cp_ip") or "")
    registro_ilegivel = False
    for conta in list_users(settings.root):
        nome = str(conta.get("username") or "")
        st.markdown(f"**{nome}**")
        st.caption(
            f"situação {conta.get('status') or 'ativo'} · "
            f"ver {', '.join(conta.get('ver') or []) or '—'} · "
            f"operar {', '.join(conta.get('operar') or []) or '—'} · "
            f"abrir {', '.join(conta.get('abrir') or []) or '—'}"
        )
        if conta.get("status") != "inativo" and _button(
            "Revogar conta", key=f"admin_off_{nome}", disabled=not keeper_ok
        ):
            if not keeper_ok:
                st.warning(BANNER_KEEPER_FORA)
            else:
                try:
                    revogar_conta_keeper(settings.root, nome, ip=ip_admin)
                except KeeperFora:
                    st.warning(BANNER_KEEPER_FORA)
                except AuthError as exc:
                    st.error(exc.message)
                except OSError:
                    st.error("Não consegui gravar a geração da sessão desse usuário. A sessão de admin continua aberta.")
                else:
                    st.success("Conta revogada. Os tokens dela deixam de entrar e o cookie cai na próxima requisição.")
        ver_edit = st.multiselect(
            f"Pode ver ({nome})",
            conhecidos,
            default=[item for item in (conta.get("ver") or []) if item in conhecidos],
            key=f"admin_ver_{nome}",
            placeholder="Escolha os apps",
        )
        operar_edit = st.multiselect(
            f"Pode operar ({nome})",
            conhecidos,
            default=[item for item in (conta.get("operar") or []) if item in conhecidos],
            key=f"admin_operar_{nome}",
            placeholder="Escolha os apps",
        )
        abrir_edit = st.multiselect(
            f"Pode abrir ({nome})",
            conhecidos,
            default=[item for item in (conta.get("abrir") or []) if item in conhecidos],
            key=f"admin_abrir_{nome}",
            placeholder="Escolha os apps",
        )
        if _button(f"Gravar listas de {nome}", key=f"admin_lists_{nome}"):
            try:
                update_lists(settings.root, nome, ver=list(ver_edit), operar=list(operar_edit), abrir=list(abrir_edit))
            except AuthError as exc:
                st.error(exc.message)
            else:
                st.success("Listas gravadas. Valem no próximo token. O token já emitido continua com as listas de quando saiu.")
        if conta.get("status") != "inativo" and _button(
            f"Emitir token para {nome}", key=f"admin_emit_{nome}", disabled=not keeper_ok
        ):
            if not keeper_ok:
                st.warning(BANNER_KEEPER_FORA)
            else:
                try:
                    update_lists(settings.root, nome, ver=list(ver_edit), operar=list(operar_edit), abrir=list(abrir_edit))
                    novo = emitir_token(
                        settings.root,
                        nome,
                        ver=list(ver_edit),
                        operar=list(operar_edit),
                        abrir=list(abrir_edit),
                        ip=ip_admin,
                    )
                except KeeperFora:
                    st.warning(BANNER_KEEPER_FORA)
                except AuthError as exc:
                    st.error(exc.message)
                except OSError:
                    st.error("Não consegui gravar a chave ou o registro do jti. Nenhum token foi mostrado.")
                else:
                    st.session_state[f"cp_token_copia_{nome}"] = novo
        copia = st.session_state.get(f"cp_token_copia_{nome}")
        if isinstance(copia, str) and copia:
            st.caption("Copie agora. Este token não fica em disco. Apague da tela quando terminar.")
            st.code(copia, language=None)
            if _button(f"Apagar token da tela ({nome})", key=f"admin_clear_{nome}"):
                st.session_state.pop(f"cp_token_copia_{nome}", None)
                st.rerun()
        try:
            emitidos = listar(settings.root, nome)
        except AuthError as exc:
            if not registro_ilegivel:
                st.error(exc.message)
                registro_ilegivel = True
            emitidos = []
        for item in emitidos:
            if item.get("revogado"):
                continue
            curto = str(item.get("jti") or "")[:8]
            hora = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(int(item.get("exp") or 0)))
            st.caption(f"jti {curto}… válido até {hora}")
            if _button(f"Revogar jti {curto}", key=f"admin_jti_{item.get('jti')}", disabled=not keeper_ok):
                if not keeper_ok:
                    st.warning(BANNER_KEEPER_FORA)
                else:
                    try:
                        revogar_jti_keeper(settings.root, str(item.get("jti") or ""), ip=ip_admin)
                    except KeeperFora:
                        st.warning(BANNER_KEEPER_FORA)
                    except AuthError as exc:
                        st.error(exc.message)
                    else:
                        st.success("Token revogado. A sessão ligada a ele cai na próxima requisição.")
                        st.rerun()
    st.divider()
    st.subheader("Chave dos tokens")
    st.caption(
        "A chave fica em .n8groker/usuario.key, separada da chave de admin, e não entra no git. "
        "O botão pede rotacionar ao Keeper. A privada não entra neste painel. "
        "Rotacionar troca a chave: todo token já emitido deixa de valer na hora e o cookie de sessão cai no próximo pedido. "
        "A sessão de admin não usa essa chave."
    )
    if _button("Rotacionar chave", key="admin_rotate", disabled=not keeper_ok):
        if keeper_ok:
            st.session_state["confirmar_rotacao_usuario"] = True
    if st.session_state.get("confirmar_rotacao_usuario"):
        st.warning("Todo token de usuário emitido antes deixa de valer agora. As sessões deles caem na próxima requisição.")
        confirmar, cancelar = st.columns(2)
        with confirmar:
            if _button("Confirmar rotação", key="admin_rotate_ok", type="primary", disabled=not keeper_ok):
                if not keeper_ok:
                    st.warning(BANNER_KEEPER_FORA)
                else:
                    st.session_state["confirmar_rotacao_usuario"] = False
                    try:
                        rotacionar_keeper(settings.root, "usuario", ip=ip_admin)
                    except KeeperFora:
                        st.warning(BANNER_KEEPER_FORA)
                    except AuthError as exc:
                        st.error(exc.message)
                    except OSError:
                        st.error(
                            "Não consegui concluir a troca da chave. "
                            "Na próxima leitura o par é concluído se os temporários já estavam prontos. "
                            "A sessão de admin continua."
                        )
                    else:
                        st.success("Chave trocada. Os tokens antigos não entram mais.")
        with cancelar:
            if _button("Cancelar rotação", key="admin_rotate_no"):
                st.session_state["confirmar_rotacao_usuario"] = False
                st.rerun()
    st.divider()
    _render_varredura(settings)
    st.subheader("Fila de IPs")
    st.caption(
        "Esta aba fala com http://127.0.0.1:5676. "
        "O webhook do n8n em 404 não impede Aprovar, Bloquear o IP inteiro nem Vincular."
    )
    if _button("Atualizar fila", key="porteiro_fila_refresh"):
        st.session_state.pop("porteiro_fila", None)
        st.session_state.pop("porteiro_fila_erro", None)
        st.rerun()
    if not token_painel(settings.root):
        st.error(
            "Não há .n8groker/porteiro-painel.token. "
            "O iniciar_servicos.ps1 cria esse arquivo. Sem ele a fila responde 403."
        )
    if "porteiro_fila" not in st.session_state:
        try:
            st.session_state["porteiro_fila"] = listar_fila(token=token_painel(settings.root))
        except FilaError as exc:
            st.session_state["porteiro_fila"] = None
            st.session_state["porteiro_fila_erro"] = exc.message
    if st.session_state.get("porteiro_fila") is None:
        st.error(st.session_state.get("porteiro_fila_erro") or "Fila indisponível.")
        st.caption("Sem o Porteiro em 127.0.0.1:5676 a fila não carrega. O veredito de quem já entrou não muda.")
    else:
        visiveis = [
            item
            for item in st.session_state["porteiro_fila"]
            if item.get("status") in {"pendente", "aprovado"}
        ]
        if not visiveis:
            st.caption("Nenhum IP pendente ou aprovado.")
        for item in visiveis:
            ip = str(item.get("ip") or "")
            st.markdown(f"**{ip}** · {item.get('status')}")
            st.caption(
                f"{item.get('data_primeiro_acesso') or 'sem data'} · "
                f"conta pedida {item.get('conta_solicitada') or '—'} · "
                f"vínculo {item.get('conta_vinculada') or '—'}"
            )
            origens = item.get("origens") if isinstance(item.get("origens"), list) else []
            if origens:
                texto = " · ".join(
                    f"{parte.get('origem')} ({parte.get('status')})"
                    for parte in origens
                    if isinstance(parte, dict)
                )
                st.caption("Origens: " + texto)
            st.caption(
                f"navegador {item.get('navegador') or '—'} · "
                f"sistema {item.get('sistema') or '—'} · "
                f"idioma {item.get('idioma') or '—'} · "
                f"horário {item.get('horario') or '—'} · "
                f"país {item.get('pais') or 'vazio'} · "
                f"dispositivo {item.get('dispositivo') or '—'}"
            )
            plano = plano_da_fila(item)
            if item.get("status") == "aprovado" and plano["navegadores"]:
                st.caption(
                    "Navegador novo neste IP. Aprovar este navegador libera só essa origem. "
                    "Bloquear o IP inteiro vale para o IP todo."
                )
            conta = st.text_input(
                "Conta para vincular",
                value=str(item.get("conta_solicitada") or item.get("conta_vinculada") or ""),
                key=f"fila_conta_{ip}",
            )
            colunas = st.columns(4)
            with colunas[0]:
                if plano["aprovar"] and _button("Aprovar", key=f"fila_ok_{ip}"):
                    _fila(settings, "aprovar", ip, origem=plano["aprovar"])
                elif item.get("status") == "pendente":
                    st.caption("Sem origem neste IP. Aprovar sem o id da origem não libera o navegador.")
            with colunas[1]:
                if _button("Bloquear o IP inteiro", key=f"fila_no_{ip}"):
                    _fila(settings, "bloquear", ip)
            with colunas[2]:
                if _button("Vincular", key=f"fila_vinc_{ip}"):
                    _fila(settings, "vincular", ip, conta, origem=plano["vincular"])
            with colunas[3]:
                if _button("Liberar este IP para esta conta", key=f"fila_liberar_{ip}"):
                    _liberar_ip(settings, ip, conta, plano["liberar"])
            for origem_nova in plano["navegadores"]:
                st.caption(f"Origem pendente {origem_nova}")
                if _button("Aprovar este navegador", key=f"fila_nav_{ip}_{origem_nova}"):
                    _fila(settings, "aprovar", ip, origem=origem_nova)


def _liberar_ip(settings: Settings, ip: str, conta: str, origem: str) -> None:
    if not str(conta or "").strip():
        st.error("Informe a conta para liberar este IP.")
        return
    if not origem:
        st.error("Sem origem registrada neste IP. Abra o endereço no navegador para a origem entrar na fila.")
        return
    try:
        resultado = liberar(
            st.session_state, ip, conta, origem, token=token_painel(settings.root)
        )
    except FilaError as exc:
        st.error(exc.message)
        return
    if not resultado["ok"]:
        from control_plane.porteiro_admin import mensagem_recusa

        st.error(mensagem_recusa(resultado.get("texto") or ""))
        return
    st.rerun()


def _fila(settings: Settings, acao: str, ip: str, conta: str = "", origem: str = "") -> None:
    try:
        resultado = aplicar(
            st.session_state, acao, ip, conta, token=token_painel(settings.root), origem=origem
        )
    except FilaError as exc:
        st.error(exc.message)
        return
    if not resultado["ok"]:
        from control_plane.porteiro_admin import mensagem_recusa

        st.error(mensagem_recusa(resultado.get("texto") or ""))
        return
    st.rerun()


_ABA_LABEL = {
    "infra": "Infraestrutura",
    "scout": "Scout",
    "chat": "Chat de suporte",
    "admin": "Admin",
}


def _abas_visiveis() -> tuple[str, ...]:
    if st.session_state.get("cp_must_change") or st.session_state.get("cp_aguardando"):
        return ()
    admin = _admin_sessao()
    ver = set(st.session_state.get("cp_ver") or [])
    operar = set(st.session_state.get("cp_operar") or [])
    abrir = set(st.session_state.get("cp_abrir") or [])
    abas = []
    if admin or ver or operar or abrir:
        abas.append("infra")
    if admin or "scout" in ver or "scout" in operar:
        abas.append("scout")
    if admin or ver or operar:
        abas.append("chat")
    if admin and _modo() != "edge":
        abas.append("admin")
    return tuple(abas)


def _aba_na_url(abas: tuple[str, ...]) -> str:
    raw = st.query_params.get("aba", abas[0] if abas else "infra")
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else ""
    text = str(raw)
    if text not in abas:
        return abas[0]
    return text


def _escolher_aba(abas: tuple[str, ...]) -> str:
    atual = _aba_na_url(abas)
    desejada = sincronizar_aba(
        st.session_state.get("cp_aba"),
        atual,
        st.session_state.get("cp_aba_aplicada"),
        abas,
    )
    if st.session_state.get("cp_aba") != desejada:
        st.session_state["cp_aba"] = desejada
    escolha = st.radio(
        "Seção",
        abas,
        format_func=lambda key: _ABA_LABEL[key],
        horizontal=True,
        label_visibility="collapsed",
        key="cp_aba",
    )
    st.session_state["cp_aba_aplicada"] = escolha
    if escolha != atual:
        st.query_params["aba"] = escolha
        st.rerun()
    return escolha


def main() -> None:
    try:
        settings = load_settings()
    except Exception:
        logger.exception("config")
        st.error("Não foi possível ler a configuração do Control Plane.")
        return

    for warning in settings.warnings:
        st.warning(warning)

    _avisar_chave_borda(settings)
    _origem_no_navegador()

    if not _exigir_borda(settings):
        return

    if not _require_login(settings):
        return

    _publicar_ator(settings)
    _sessao_no_navegador(settings)
    if _modo() == "edge":
        import time

        from control_plane.binding import espera_registro_origem

        origem_cookie = _origem_do_pedido()
        aviso_origem = _texto_origem_edge(settings)
        if aviso_origem:
            st.warning(aviso_origem)
        elif espera_registro_origem(
            origem_cookie,
            aviso_origem,
            int(st.session_state.get("cp_origem_tentativas") or 0),
        ):
            st.session_state["cp_origem_tentativas"] = int(st.session_state.get("cp_origem_tentativas") or 0) + 1
            time.sleep(0.8)
            st.rerun()
    if st.session_state.get("cp_aguardando"):
        _pedir_vinculo(str(st.session_state.get("cp_ip") or ""), str(st.session_state.get("cp_user") or ""))
        st.subheader("Aguardando aprovação")
        st.caption(
            mensagem_espera(
                settings.root,
                str(st.session_state.get("cp_ip") or ""),
                str(st.session_state.get("cp_user") or ""),
            )
        )
        st.caption("Nenhuma ação roda até esse passo. Várias pessoas atrás do mesmo NAT compartilham o IP.")
        _sidebar(settings)
        return

    st.title("Control Plane")
    painel = SERVICE_DESCRIPTIONS["control-plane"]
    st.caption(painel.summary)
    st.caption(painel.detail)

    _sidebar(settings)
    abas = _abas_visiveis()
    if not abas:
        st.info(
            "Este token não traz permissão em nenhum app. "
            "No console, a aba Admin emite outro token com Pode ver, Pode operar ou Pode abrir."
        )
        return
    aba = _escolher_aba(abas)
    if aba == "infra":
        _version_notice(settings)
        _render_shortcuts(settings)
        st.divider()
        st.subheader("Status da infraestrutura")

        fragment = getattr(st, "fragment", None)
        if fragment is not None:
            fragment(_status_section)(settings)
        else:
            _status_section(settings)

        if _admin_sessao():
            st.divider()
            _container_stats()
            st.divider()
            _diagnostics(settings)
        if _ve_operacoes():
            st.divider()
            _operations(settings)
        if _admin_sessao():
            st.divider()
            _backups(settings)

    elif aba == "scout":
        env = _env_values(settings)
        render_scout_tab(
            api_base(env.get("SCOUT_BACKEND_URL")),
            pode_operar=_admin_sessao() or _pode("operar", "scout"),
            admin=_admin_sessao() and _modo() != "edge",
            root=settings.root,
        )

    elif aba == "admin":
        _render_admin(settings)

    else:
        _support_chat(settings)

    with st.expander("Como os serviços se ligam"):
        st.markdown(
            """
- **LiteLLM → Langfuse:** callback `langfuse_otel` para `http://langfuse-web:3000`, com as chaves do headless init.
- **n8n → LiteLLM:** credencial OpenAI (`openAiApi`) aponta para `http://litellm:4000/v1`.
- **Modelos:** nenhum provider vem pronto. Cadastre na UI do LiteLLM.
- **Login Langfuse:** e-mail `admin@example.com` e a senha `LANGFUSE_INIT_USER_PASSWORD` do `.env`.
- **Login LiteLLM:** usuário `LITELLM_UI_USERNAME` ou a `LITELLM_MASTER_KEY` do `.env`.
            """
        )


main()
