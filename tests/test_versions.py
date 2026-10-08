from datetime import datetime, timedelta, timezone
from pathlib import Path

from control_plane.versions import (
    check_versions,
    extract_images,
    is_newer,
    parse_image,
    select_newer_tag,
    version_tuple,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def test_comparacao_de_tags():
    assert version_tuple("v1.103.1") == (1, 103, 1)
    assert version_tuple("17.11") == (17, 11)
    assert version_tuple("latest") is None
    assert version_tuple("4.31.0-alpine") is None
    assert is_newer("4.31.0", "4.30.0")
    assert is_newer("v1.104.0", "v1.103.1")
    assert is_newer("17.12", "17.11")
    assert is_newer("17.11.1", "17.11")
    assert not is_newer("4.30.0", "4.30.0")
    assert not is_newer("4.29.9", "4.30.0")
    assert not is_newer("latest", "4.30.0")
    assert not is_newer("18", "17.11")
    assert select_newer_tag("4.30.0", ["4.29.0", "latest", "4.31.0-rc1", "4.30.1", "4.31.0"]) == "4.31.0"
    assert select_newer_tag("4.30.0", ["4.30.0", "latest"]) is None


def test_digest_e_latest_nao_viram_atualizacao(tmp_path):
    calls = []

    def fetch(pin):
        calls.append(pin.raw)
        return ["9.9.9"]

    pins = [
        parse_image("cgr.dev/chainguard/minio@sha256:abc"),
        parse_image("docker.n8n.io/n8nio/n8n:latest"),
        parse_image("docker.io/langfuse/langfuse:4.30.0"),
    ]
    report = check_versions(pins, fetch_tags=fetch, now=NOW, cache_file=tmp_path / "version-cache.json")
    assert calls == ["docker.io/langfuse/langfuse:4.30.0"]
    assert len(report.notices) == 1
    assert report.notices[0].newer == "9.9.9"
    assert "não atualiza" in report.notices[0].message
    assert any("digest" in note for note in report.notes)
    assert any("latest" in note for note in report.notes)
    assert report.notices[0].image.startswith("docker.io/langfuse/langfuse")


def test_offline_nao_quebra_e_o_cache_segura(tmp_path):
    cache = tmp_path / "version-cache.json"
    calls = {"n": 0}

    def fetch(pin):
        calls["n"] += 1
        if calls["n"] == 1:
            return ["4.31.0", "latest"]
        raise OSError("offline")

    pin = parse_image("docker.io/langfuse/langfuse:4.30.0")
    first = check_versions([pin], fetch_tags=fetch, now=NOW, cache_file=cache)
    assert first.ok is True
    assert first.notices[0].newer == "4.31.0"
    second = check_versions([pin], fetch_tags=fetch, now=NOW + timedelta(hours=1), cache_file=cache)
    assert calls["n"] == 1
    assert second.notices[0].newer == "4.31.0"
    third = check_versions([pin], fetch_tags=fetch, now=NOW + timedelta(hours=13), cache_file=cache)
    assert calls["n"] == 2
    assert third.notices[0].newer == "4.31.0"


def test_offline_sem_cache_fica_vazio(tmp_path):
    def fetch(pin):
        raise OSError("offline")

    report = check_versions(
        [parse_image("ghcr.io/berriai/litellm:v1.103.1")],
        fetch_tags=fetch,
        now=NOW,
        cache_file=tmp_path / "version-cache.json",
    )
    assert report.ok is False
    assert report.notices == ()


def test_compose_real_separa_pin_numerico_digest_e_tag_sem_semver():
    llm = [parse_image(raw) for raw in extract_images((ROOT / "llm" / "docker-compose.yml").read_text(encoding="utf-8"))]
    tags = {ref.tag for ref in llm}
    assert "4.30.0" in tags
    assert "v1.103.1" in tags
    assert "17.11" in tags
    assert any(ref.digest and "minio" in ref.raw for ref in llm)
    n8n = extract_images((ROOT / "n8n" / "docker-compose.yml").read_text(encoding="utf-8"))
    assert parse_image(n8n[0]).tag == "2.42.5"
    assert version_tuple("2.42.5") == (2, 42, 5)
    ngrok = extract_images((ROOT / "ngrok" / "docker-compose.yml").read_text(encoding="utf-8"))
    assert parse_image(ngrok[0]).tag == "3.39.8-debian"
    assert version_tuple("3.39.8-debian") is None
    text = (ROOT / "control_plane" / "versions.py").read_text(encoding="utf-8")
    assert "docker pull" not in text
    assert "não atualiza" in text
