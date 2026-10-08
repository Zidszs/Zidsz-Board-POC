import zipfile
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest

from control_plane.diagnostics import (
    DiagnosticsError,
    Sample,
    assemble_diagnostic_zip,
    flags_for_series,
    is_slower_than_baseline,
    load_series,
    note_marker,
    record_results,
    rolling_baseline,
)


def _at(seconds: int) -> datetime:
    return datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=seconds)


def test_tempo_ate_saudavel_na_transicao_e_depois_do_restart(tmp_path):
    record_results(tmp_path, [("n8n", "offline", None)], now=_at(0))
    record_results(tmp_path, [("n8n", "offline", None)], now=_at(2))
    healthy = record_results(tmp_path, [("n8n", "online", 40.0)], now=_at(5))
    assert healthy == [5000]
    again = record_results(tmp_path, [("n8n", "online", 42.0)], now=_at(8))
    assert again == [None]
    note_marker(tmp_path, "n8n", "restart", now=_at(12))
    after = record_results(tmp_path, [("n8n", "online", 55.0)], now=_at(15))
    assert after == [3000]
    series = load_series(tmp_path)["n8n"]
    assert [item.time_to_healthy_ms for item in series] == [None, None, 5000, None, 3000]


def test_retencao_apaga_amostra_antiga(tmp_path):
    record_results(tmp_path, [("n8n", "online", 10.0)], now=_at(0) - timedelta(days=8))
    record_results(tmp_path, [("n8n", "online", 12.0)], now=_at(0))
    series = load_series(tmp_path)["n8n"]
    assert len(series) == 1
    assert series[0].latency_ms == 12.0


def test_termometro_marca_acima_do_p90_e_ignora_amostra_curta():
    flat = [10.0, 10.0, 10.0, 10.0, 10.0]
    assert rolling_baseline(flat) is None
    assert is_slower_than_baseline(11.0, flat[:4]) is False
    assert is_slower_than_baseline(11.0, flat) is True
    assert is_slower_than_baseline(10.0, flat) is False
    samples = [
        Sample("t", "n8n", "online", value, None)
        for value in (10.0, 10.0, 10.0, 10.0, 10.0, 80.0)
    ]
    flags = flags_for_series(samples)
    assert len(flags) == 1
    assert flags[0].metric == "latencia"
    assert flags[0].latest == 80.0
    assert flags[0].p90 == 10.0
    calm = [
        Sample("t", "n8n", "online", value, None)
        for value in (10.0, 10.0, 10.0, 10.0, 10.0, 10.0)
    ]
    assert flags_for_series(calm) == []


def test_termometro_de_tempo_ate_saudavel():
    samples = []
    for index, recovery in enumerate((1000.0, 1100.0, 900.0, 1000.0, 1050.0, 8000.0)):
        samples.append(Sample(f"t{index}", "litellm", "online", 20.0, recovery if True else None))
    flags = [flag for flag in flags_for_series(samples) if flag.metric == "tempo_ate_saudavel"]
    assert len(flags) == 1
    assert flags[0].latest == 8000.0
    assert flags[0].latest > flags[0].p90
    assert flags[0].latest > flags[0].median


def test_zip_redige_segredo_e_nao_leva_env():
    secret = "super-segredo-do-painel"
    env = {"LITELLM_MASTER_KEY": secret, "N8N_PORT": "5678"}
    blob = assemble_diagnostic_zip(
        status_rows=[{"service_id": "n8n", "status": "online", "detail": f"token {secret}", "latency_ms": 12, "recorded_at": "t"}],
        history_rows=[{"recorded_at": "t", "service_id": "n8n", "status": "online", "latency_ms": 12, "time_to_healthy_ms": None}],
        logs={"n8n_app": f"linha com {secret} porta 5678\n", "../.env": secret, "mau nome": secret},
        compose_ps=f"n8n_app Up {secret}",
        docker_version=f"Docker 27 {secret}",
        ollama_version="ollama 0.5",
        env=env,
    )
    assert secret.encode() not in blob
    with zipfile.ZipFile(BytesIO(blob)) as archive:
        names = archive.namelist()
        assert "status.json" in names
        assert "historico.json" in names
        assert "compose-ps.txt" in names
        assert "versoes.txt" in names
        assert "logs/n8n_app.log" in names
        assert all(".env" not in name for name in names)
        assert "logs/../.env" not in names
        joined = "\n".join(archive.read(name).decode("utf-8") for name in names)
    assert secret not in joined
    assert "«redigido»" in joined
    assert "5678" in joined


def test_gitignore_cobre_a_pasta_de_diagnostico():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")
    assert ".n8groker/" in text


def test_servico_invalido_nao_grava(tmp_path):
    with pytest.raises(DiagnosticsError):
        record_results(tmp_path, [("n8n; rm", "online", 1.0)], now=_at(0))
