from control_plane.deps import mensagem_dependencia, modulos_ausentes


def test_mensagem_quando_falta_cryptography():
    texto = mensagem_dependencia(["cryptography"])
    assert "cryptography" in texto
    assert "Streamlit" in texto
    assert "pip install -r control_plane/requirements.txt" in texto
    assert "venv antigo" in texto


def test_painel_checa_cryptography_antes_de_importar_o_jwt():
    from pathlib import Path

    app = Path("control_plane/app.py").read_text(encoding="utf-8")
    assert app.index("modulos_ausentes") < app.index("from control_plane.admin_token import aceitar")
    assert "mensagem_dependencia" in app


def test_modulos_ausentes_nao_inclui_o_que_esta_instalado():
    assert "cryptography" not in modulos_ausentes()
