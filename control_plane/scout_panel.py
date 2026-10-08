"""Aba Scout do Control Plane. Confirmação antes de mudar rota, alias, bloqueio ou firewall."""

from __future__ import annotations

import logging

import streamlit as st

from control_plane.actor import AccessDenied
from control_plane.scout_client import GateError, apply_action, make_gate

logger = logging.getLogger("control_plane.scout")

_N8N_WARNING = (
    "Ativar n8n_app expõe o n8n direto e ignora a fila do Porteiro. "
    "Para o acesso normal, deixe só a rota do Porteiro. Nada foi alterado ainda."
)
_NGROK_WARNING = "Não é recomendado ativar o proxy para ngrok_service. A rota continua desligada."


def _append_log(session: dict, message: str) -> None:
    lines = list(session.get("scout_log") or [])
    lines.insert(0, message)
    session["scout_log"] = lines[:30]


def _log(message: str) -> None:
    _append_log(st.session_state, message)


def commit_scout_action(gate, pending: dict, session: dict) -> str:
    """Executa a ação e descarta o snapshot. A próxima leitura busca a lista de novo."""
    from control_plane.actor import concluir, exigir

    exigir("operar", "scout")
    action = pending
    if pending.get("kind") == "patch_form":
        action = _prepare_patch(pending)
    message = apply_action(gate, action)
    _append_log(session, message)
    session.pop("scout_snap", None)
    session.pop("scout_down", None)
    session.pop("scout_health_text", None)
    concluir("operar", "scout")
    return message


def _arm(action: dict) -> None:
    st.session_state["scout_pending"] = action


def _route_card(gate, entry: dict, *, managed: bool, pode_operar: bool = True) -> None:
    entry_id = str(entry.get("id") or "")
    if not entry_id:
        return
    enabled = bool(entry.get("enabled"))
    source = str(entry.get("source") or "")
    manual = source == "manual"
    status = "NOVO" if entry.get("is_new") else ("ATIVO" if enabled else "INATIVO")
    upstream = f"{entry.get('upstream_host')}:{entry.get('upstream_port')}"
    title = f"{entry.get('name') or entry_id} · {status}"
    with st.container(border=True):
        st.markdown(f"**{title}**")
        st.caption(f"Origem {source or '—'} · porta Scout {entry.get('listen_port')} · upstream {upstream} · modo {entry.get('mode', 'tcp')}")
        if pode_operar and enabled and source in {"docker", "manual"}:
            mode = st.selectbox(
                "Modo",
                ("tcp", "http", "https"),
                index=("tcp", "http", "https").index(entry.get("mode") if entry.get("mode") in {"tcp", "http", "https"} else "tcp"),
                key=f"scout_mode_{entry_id}",
            )
            if st.button("Gravar modo", key=f"scout_mode_btn_{entry_id}"):
                _arm({"kind": "patch", "id": entry_id, "fields": {"mode": mode}, "message": f"Gravar o modo {mode} na rota {entry_id}?"})
                st.rerun()
        if pode_operar and st.button("Desativar" if enabled else "Ativar", key=f"scout_toggle_{entry_id}"):
            nxt = not enabled
            decision = "allow"
            from control_plane.scout_client import toggle_decision

            decision = toggle_decision(entry_id, nxt, managed=managed)
            if decision == "reject_ngrok":
                st.error(_NGROK_WARNING)
                return
            message = _N8N_WARNING if decision == "confirm_n8n" else f"{'Ativar' if nxt else 'Desativar'} a rota {entry_id}?"
            _arm({"kind": "toggle", "id": entry_id, "enabled": nxt, "message": message})
            st.rerun()
        if manual:
            name = st.text_input("Nome", value=str(entry.get("name") or ""), key=f"scout_name_{entry_id}")
            listen = st.text_input("Porta Scout", value=str(entry.get("listen_port") or ""), key=f"scout_listen_{entry_id}")
            upstream_text = st.text_input("Upstream (host:porta)", value=upstream, key=f"scout_up_{entry_id}")
            left, right = st.columns(2)
            with left:
                if st.button("Salvar rota manual", key=f"scout_save_{entry_id}"):
                    _arm(
                        {
                            "kind": "patch_form",
                            "id": entry_id,
                            "name": name,
                            "listen": listen,
                            "upstream": upstream_text,
                            "managed": managed,
                            "message": f"Gravar as alterações da rota manual {entry_id}?",
                        }
                    )
                    st.rerun()
            with right:
                if st.button("Apagar rota manual", key=f"scout_del_{entry_id}"):
                    _arm({"kind": "delete", "id": entry_id, "message": f"Apagar a rota manual {entry_id}? Isso não tem desfazer automático."})
                    st.rerun()


def _prepare_patch(action: dict) -> dict:
    from control_plane.scout_client import coerce_managed_upstream, parse_upstream

    fields: dict = {}
    name = str(action.get("name") or "").strip()
    if name:
        fields["name"] = name
    listen = str(action.get("listen") or "").strip()
    try:
        port = int(listen)
    except ValueError as exc:
        raise GateError("Porta Scout inválida. Use 1–65535.") from exc
    fields["listen_port"] = port
    host, up_port = parse_upstream(str(action.get("upstream") or ""))
    host, up_port, warning = coerce_managed_upstream(host, up_port, managed=bool(action.get("managed")))
    fields["upstream_host"] = host
    fields["upstream_port"] = up_port
    if warning:
        _log(warning)
    return {"kind": "patch", "id": action["id"], "fields": fields}


def _run_pending(gate, pending: dict) -> None:
    commit_scout_action(gate, pending, st.session_state)


def render_scout_tab(base_url: str, *, managed: bool = True, pode_operar: bool = True, admin: bool = False, root=None) -> None:
    st.subheader("Scout")
    st.caption(
        "As mesmas operações da antiga gestão do Scout, agora nesta aba. "
        "O backend é o scout-backend na porta 8765. Mudança de rota, alias, bloqueio e firewall pede confirmação."
    )
    if admin and root is not None:
        from control_plane.trilha_painel import render_trilha_admin

        render_trilha_admin(root)
    gate = make_gate(base_url, timeout=4)
    pending = st.session_state.get("scout_pending")
    if pode_operar and isinstance(pending, dict):
        st.warning(str(pending.get("message") or "Confirme a ação do Scout."))
        yes, no = st.columns(2)
        with yes:
            if st.button("Confirmar", key="scout_confirm", type="primary"):
                try:
                    _run_pending(gate, pending)
                except GateError as exc:
                    _log(exc.message)
                    st.error(exc.message)
                except AccessDenied as exc:
                    _log(exc.message)
                    st.error(exc.message)
                except Exception:
                    logger.warning("acao scout falhou")
                    _log("A ação do Scout não pôde ser concluída.")
                st.session_state.pop("scout_pending", None)
                st.rerun()
        with no:
            if st.button("Cancelar", key="scout_cancel"):
                st.session_state.pop("scout_pending", None)
                _log("Ação cancelada. Nada foi enviado ao Scout.")
                st.rerun()

    if st.button("Atualizar Scout", key="scout_refresh"):
        st.session_state.pop("scout_snap", None)
    if "scout_snap" not in st.session_state:
        try:
            st.session_state["scout_snap"] = gate.snapshot()
            st.session_state["scout_down"] = ""
        except GateError as exc:
            st.session_state["scout_snap"] = None
            st.session_state["scout_down"] = exc.message
    if st.session_state.get("scout_down"):
        st.error(st.session_state["scout_down"])
        st.caption("O restante do painel continua. Quando o Scout voltar, use Atualizar Scout.")
        return

    snap = st.session_state.get("scout_snap") or {}
    health = snap.get("health") or {}
    summary = snap.get("summary") or health.get("summary") or {}
    st.markdown("**Backend: no ar**")
    st.caption(
        f"Ativos {summary.get('active', 0)} · total {summary.get('total', 0)} · novos {summary.get('new', 0)}"
    )

    if pode_operar:
        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button("Sincronizar Docker", key="scout_sync"):
                _arm({"kind": "sync_docker", "message": "Pedir ao Scout para reler os containers? Rotas novas entram desligadas."})
                st.rerun()
        with c2:
            if st.button("Sincronizar firewall", key="scout_fw"):
                _arm({"kind": "firewall", "message": "Reaplicar o firewall do Scout com as portas atuais?"})
                st.rerun()
        with c3:
            st.caption("Porta manual começa desligada.")

        with st.expander("Incluir porta manual"):
            name = st.text_input("Nome", key="scout_new_name")
            port = st.number_input("Porta upstream", min_value=1, max_value=65535, value=5680, step=1, key="scout_new_port")
            if st.button("Incluir porta", key="scout_new_btn"):
                _arm(
                    {
                        "kind": "manual",
                        "name": name,
                        "upstream_port": int(port),
                        "upstream_host": "host.docker.internal",
                        "message": f"Incluir a porta manual «{name}» para host.docker.internal:{int(port)}, desligada?",
                    }
                )
                st.rerun()

    st.markdown("**Redirecionamentos**")
    entries = snap.get("entries") or []
    if not entries:
        st.caption("Nenhuma rota devolvida pelo Scout.")
    for entry in entries:
        if isinstance(entry, dict):
            _route_card(gate, entry, managed=managed, pode_operar=pode_operar)

    st.markdown("**Tráfego**")
    traffic = snap.get("traffic") or []
    if not traffic:
        st.caption("Sem sessões recentes.")
    for index, row in enumerate(traffic[:80]):
        if not isinstance(row, dict):
            continue
        ip = str(row.get("client_ip") or "")
        label = row.get("client_label") or ip
        blocked = "sim" if row.get("blocked") else "não"
        st.caption(
            f"{row.get('time', '')} · {label} · {row.get('route_name', '')} · {row.get('mode', '')} · "
            f"{row.get('upstream', '')} · {row.get('bytes_total', 0)} bytes · bloqueado {blocked}"
        )
        if ip:
            if pode_operar:
                _ip_actions(ip, allow_remove=False, suffix=f"t{index}")

    st.markdown("**Alertas**")
    alerts = snap.get("alerts") or []
    if not alerts:
        st.caption("Sem alertas.")
    for alert in alerts[:15]:
        if isinstance(alert, dict):
            st.caption(f"{alert.get('time', '')} {alert.get('msg', '')}")

    st.markdown("**Clientes**")
    st.caption("IPs vistos na borda. Alias, bloqueio e remoção pedem confirmação.")
    clients = snap.get("clients") or []
    if not clients:
        st.caption("Nenhum cliente registrado.")
    for row in clients:
        if not isinstance(row, dict) or not row.get("ip"):
            continue
        ip = str(row["ip"])
        st.markdown(
            f"**{ip}** · alias {row.get('alias') or '—'} · {row.get('last_seen', '')} · "
            f"modo {row.get('last_mode', '')} · rota {row.get('last_route', '')} · "
            f"sessões {row.get('session_count', 0)} · {row.get('bytes_total', 0)} bytes · "
            f"bloqueado {'sim' if row.get('blocked') else 'não'}"
        )
        if pode_operar:
            _ip_actions(ip, allow_remove=True, suffix="c" + ip.replace(":", "_").replace(".", "_"), blocked=bool(row.get("blocked")))

    st.markdown("**Configuração**")
    st.caption(f"HTTP da gestão: `{base_url}`")
    if st.button("Health Check", key="scout_health_btn"):
        try:
            health = gate.health()
            st.session_state["scout_health_text"] = _config_text(health)
        except GateError as exc:
            st.session_state["scout_health_text"] = exc.message
    st.text(st.session_state.get("scout_health_text") or _config_text(health))

    st.markdown("**Registro**")
    for line in st.session_state.get("scout_log") or []:
        st.caption(line)


def _config_text(health: dict) -> str:
    return (
        f"Ngrok: {health.get('ngrok_url') or '(não definido)'}\n"
        f"Porta pública Scout: {health.get('public_port')}\n"
        f"Admin API: {health.get('admin_port')}\n"
        f"Firewall: {'ativo' if health.get('enforce_firewall') else 'inativo'}\n"
        f"Docker: {'ok' if health.get('docker_ok') else 'indisponível'}\n"
        f"Containers: {health.get('containers', 0)}"
    )


def _ip_actions(ip: str, *, allow_remove: bool, suffix: str, blocked: bool = False) -> None:
    alias = st.text_input("Alias", key=f"scout_alias_{suffix}", label_visibility="collapsed", placeholder=f"Alias para {ip}")
    cols = st.columns(3 if allow_remove else 2)
    with cols[0]:
        if st.button("Gravar alias", key=f"scout_alias_btn_{suffix}"):
            _arm({"kind": "alias", "ip": ip, "alias": alias, "message": f"Gravar o alias de {ip}?"})
            st.rerun()
    with cols[1]:
        if blocked:
            if st.button("Desbloquear", key=f"scout_unblock_{suffix}"):
                _arm({"kind": "unblock", "ip": ip, "message": f"Tirar {ip} do bloqueio da borda?"})
                st.rerun()
        elif st.button("Bloquear IP", key=f"scout_block_{suffix}"):
            _arm({"kind": "block", "ip": ip, "message": f"Bloquear {ip} na borda do Scout?"})
            st.rerun()
    if allow_remove:
        with cols[2]:
            if st.button("Remover da lista", key=f"scout_rm_{suffix}"):
                _arm({"kind": "remove_client", "ip": ip, "message": f"Remover {ip} da lista de clientes?"})
                st.rerun()
