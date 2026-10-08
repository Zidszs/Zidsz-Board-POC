import pytest

from control_plane.stats import ContainerStat, cached_stats, parse_docker_stats, stats_argv


def test_parse_e_argv_do_docker_stats():
    argv = stats_argv("docker")
    assert argv[:3] == ["docker", "stats", "--no-stream"]
    assert "{{.Name}}" in argv[-1]
    text = "n8n_app\t1.23%\t12MiB / 64MiB\nlangfuse-web\t0.50%\t40MiB / 128MiB\nNAME\tCPU\tMEM\n\nincompleto\n"
    rows = parse_docker_stats(text)
    assert [(row.name, row.cpu, row.memory) for row in rows] == [
        ("n8n_app", "1.23%", "12MiB / 64MiB"),
        ("langfuse-web", "0.50%", "40MiB / 128MiB"),
    ]


def test_cache_nao_chama_de_novo_e_falha_quieta():
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return [ContainerStat("n8n_app", "1%", "1MiB / 2MiB")]

    rows, cache = cached_stats(100, None, loader, ttl=20)
    assert calls["n"] == 1
    assert rows[0].name == "n8n_app"
    rows_again, cache_again = cached_stats(110, cache, loader, ttl=20)
    assert calls["n"] == 1
    assert rows_again[0].cpu == "1%"

    def boom():
        calls["n"] += 1
        raise OSError("docker parado")

    kept, refreshed = cached_stats(130, cache_again, boom, ttl=20)
    assert calls["n"] == 2
    assert kept[0].name == "n8n_app"
    kept_again, _cache = cached_stats(135, refreshed, boom, ttl=20)
    assert calls["n"] == 2
    assert kept_again[0].memory == "1MiB / 2MiB"


def test_argv_rejeita_executavel_vazio():
    with pytest.raises(ValueError):
        stats_argv("  ")
