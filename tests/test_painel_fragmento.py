"""O fragmento de status não repete o script. O ator da sessão precisa voltar no clique."""

from control_plane.actor import set_actor


class _Bloco:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _Tela:
    def __init__(self):
        self.session_state = {
            "cp_admin": True,
            "cp_user": "admin",
            "cp_must_change": False,
            "cp_aguardando": False,
            "cp_ver": [],
            "cp_operar": [],
            "cp_abrir": [],
            "cp_ip": "127.0.0.1",
            "health_cache": [],
        }
        self.erros = []

    def caption(self, *_args, **_kwargs):
        return None

    def markdown(self, *_args, **_kwargs):
        return None

    def write(self, *_args, **_kwargs):
        return None

    def toast(self, *_args, **_kwargs):
        return None

    def error(self, mensagem):
        self.erros.append(mensagem)

    def button(self, _label, **kwargs):
        return kwargs.get("key") == "restart_n8n"

    def spinner(self, *_args, **_kwargs):
        return _Bloco()

    def container(self, *_args, **_kwargs):
        return _Bloco()

    def expander(self, *_args, **_kwargs):
        return _Bloco()

    def columns(self, spec):
        quantidade = spec if isinstance(spec, int) else len(spec)
        return [_Bloco() for _ in range(quantidade)]


def test_reiniciar_no_fragmento_nao_recusa_o_admin(monkeypatch):
    monkeypatch.setenv("PANEL_MODE", "console")
    import control_plane.app as painel
    from control_plane.actor import exigir
    from control_plane.operations import OperationError

    def reinicio_falso(name, *, timeout=60, service_id=None):
        exigir("operar", service_id)
        raise OperationError("parado no teste")

    tela = _Tela()
    monkeypatch.setattr(painel, "st", tela)
    monkeypatch.setattr(painel, "restart_container", reinicio_falso)
    set_actor(None)
    painel._status_section(painel.load_settings())
    assert "Sem permissão para esta ação." not in tela.erros
    assert "parado no teste" in tela.erros
