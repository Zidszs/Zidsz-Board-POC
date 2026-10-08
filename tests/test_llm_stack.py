from pathlib import Path

from control_plane.config import SERVICE_DESCRIPTIONS, default_services, default_shortcuts, load_settings
from control_plane.health import check_services
from control_plane.envfile import parse_env

ROOT = Path(__file__).resolve().parents[1]


def test_compose_pins_images_and_integration():
    text = (ROOT / "llm" / "docker-compose.yml").read_text(encoding="utf-8")
    images = [line for line in text.splitlines() if line.strip().startswith("image:")]
    assert images
    assert all(":latest" not in line for line in images)
    assert "docker.io/langfuse/langfuse:4.30.0" in text
    assert "docker.io/langfuse/langfuse-worker:4.30.0" in text
    assert "ghcr.io/berriai/litellm:v1.103.1" in text
    assert "docker.io/postgres:17.11" in text
    assert "docker.io/redis:7.4.11" in text
    assert "clickhouse/clickhouse-server:25.12.11" in text
    assert (
        "cgr.dev/chainguard/minio@sha256:4692462f35d97d7e82c30371d82f057703c5d9489bcae726010594c812f2d285"
        in text
    )
    assert "image: quay.io/minio" not in text
    assert "image: cgr.dev/chainguard/minio@" in text
    assert 'HOSTNAME: "0.0.0.0"' in text
    assert 'entrypoint: ["sh", "-c"]' in text
    assert 'entrypoint: ["/bin/sh", "-c"]' not in text
    assert "admin@localhost" not in text
    assert "admin@example.com" in text
    assert 'name: rede_comunicacao' in text
    assert '"127.0.0.1:3000:3000"' in text
    assert '"127.0.0.1:4000:4000"' in text
    assert '"3000:3000"' not in text
    assert '"4000:4000"' not in text
    assert "127.0.0.1:3030:3030" in text
    ngrok = (ROOT / "ngrok" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "127.0.0.1:4040:4040" in ngrok
    assert "${NGROK_TUNNEL_HOST:-host.docker.internal}:${NGROK_TUNNEL_TARGET:-5677}" in ngrok
    assert "LANGFUSE_OTEL_HOST: http://langfuse-web:3000" in text
    assert "OPENAI_API_KEY" not in text
    assert "ollama" not in text.lower()
    assert "5432:5432" not in text
    for forbidden in ("down", "-v", "latest"):
        assert f"image: {forbidden}" not in text


def test_litellm_config_has_no_models():
    text = (ROOT / "llm" / "litellm_config.yaml").read_text(encoding="utf-8")
    assert "model_list: []" in text
    assert 'callbacks: ["langfuse_otel"]' in text
    assert "store_model_in_db: true" in text
    lowered = text.lower()
    assert "ollama" not in lowered
    assert "openai_api_key" not in lowered
    assert "anthropic" not in lowered
    assert "gpt-" not in lowered


def test_n8n_points_at_litellm_without_breaking_when_unset():
    text = (ROOT / "n8n" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "CREDENTIALS_OVERWRITE_DATA=${N8N_CREDENTIALS_OVERWRITE_DATA-}" in text
    assert "rede_comunicacao" in text


def test_env_example_has_markers_not_real_secrets():
    example = parse_env((ROOT / ".env.example").read_text(encoding="utf-8"))
    template = parse_env((ROOT / ".env_template").read_text(encoding="utf-8"))
    assert set(example) == set(template)
    assert example["NGROK_AUTHTOKEN"] == "seu_token_do_ngrok_aqui"
    assert example["LITELLM_MASTER_KEY"].startswith("__GENERATE_")
    assert example["LANGFUSE_PUBLIC_KEY"].startswith("__GENERATE_")
    assert "sk-" not in example["LITELLM_MASTER_KEY"]
    example_text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "OLLAMA_BASE_URL=http://localhost:11434" in example_text
    assert "SUPPORT_CHAT_MODEL=qwen2.5:7b-instruct" in example_text
    assert "11434" not in (ROOT / "llm" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "11434" not in (ROOT / "llm" / "litellm_config.yaml").read_text(encoding="utf-8")
    assert example["LANGFUSE_INIT_USER_EMAIL"] == "admin@example.com"
    assert template["LANGFUSE_INIT_USER_EMAIL"] == "admin@example.com"
    for relative in (
        ".env.example",
        ".env_template",
        "README.md",
        "control_plane/app.py",
        "llm/docker-compose.yml",
    ):
        tracked = (ROOT / relative).read_text(encoding="utf-8")
        assert "admin@localhost" not in tracked, relative


def test_control_plane_defaults_match_real_services():
    by_id = {spec.id: spec for spec in default_services()}
    assert by_id["n8n"].container_name == "n8n_app"
    assert by_id["n8n"].health_url.endswith("/healthz")
    assert by_id["porteiro"].expected_statuses == (204,)
    assert by_id["scout"].health_url.endswith("/health")
    assert by_id["ngrok"].container_name == "ngrok_service"
    assert by_id["langfuse"].container_name == "langfuse-web"
    assert "failIfDatabaseUnavailable=true" in by_id["langfuse"].health_url
    assert by_id["langfuse-worker"].container_name == "langfuse-worker"
    assert by_id["langfuse-worker"].health_url.endswith("/api/health")
    assert by_id["litellm"].container_name == "litellm"
    assert by_id["litellm"].health_url.endswith("/health/readiness")
    assert by_id["litellm"].link_url.endswith("/ui")
    assert by_id["minio"].container_name == "langfuse-minio"
    assert by_id["langfuse-postgres"].monitored is False
    assert by_id["litellm-db"].container_name == "litellm-db"


def test_todo_servico_registrado_tem_descricao():
    ids = {spec.id for spec in default_services()}
    atalhos = {item.id for item in default_shortcuts()}
    esperados = {
        "n8n",
        "porteiro",
        "ngrok",
        "scout",
        "scout-gui",
        "langfuse",
        "langfuse-worker",
        "litellm",
        "minio",
        "langfuse-postgres",
        "langfuse-clickhouse",
        "langfuse-redis",
        "litellm-db",
        "portainer",
        "control-plane",
    }
    assert ids | atalhos | {"control-plane"} == esperados
    assert set(SERVICE_DESCRIPTIONS) == esperados
    for key in esperados:
        copy = SERVICE_DESCRIPTIONS[key]
        assert copy.summary.strip()
        assert "\n" not in copy.summary
        assert copy.detail.strip()
        assert copy.summary != copy.detail
    internos = [spec for spec in default_services() if not spec.monitored]
    assert {spec.id for spec in internos} == {
        "scout-gui",
        "langfuse-postgres",
        "langfuse-clickhouse",
        "langfuse-redis",
        "litellm-db",
    }
    assert check_services(internos, timeout=0.2, slow_ms=1000) == []
    assert all(not spec.critical for spec in internos)


def test_config_env_override_ignores_bad_url(monkeypatch, tmp_path):
    monkeypatch.delenv("CP_CONFIG_FILE", raising=False)
    settings = load_settings(
        root=tmp_path,
        environ={
            "N8GROKER_ROOT": str(tmp_path),
            "CP_LANGFUSE_URL": "http://127.0.0.1:3999",
            "CP_N8N_URL": "javascript:alert(1)",
            "CP_SLOW_MS": "250",
        },
    )
    urls = {item.id: item.url for item in settings.shortcuts}
    assert urls["langfuse"] == "http://127.0.0.1:3999"
    assert urls["n8n"] == "http://localhost:5678"
    assert settings.slow_threshold_ms == 250
    assert settings.warnings
