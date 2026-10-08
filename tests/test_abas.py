from control_plane.abas import sincronizar_aba

ABAS = ("infra", "scout", "chat", "admin")


def test_primeira_abertura_segue_a_url():
    assert sincronizar_aba(None, "scout", None, ABAS) == "scout"


def test_clique_nao_volta_para_a_url_antiga():
    assert sincronizar_aba("scout", "infra", "infra", ABAS) == "scout"


def test_link_novo_vence_a_secao_que_ja_estava_aplicada():
    assert sincronizar_aba("infra", "admin", "infra", ABAS) == "admin"
