from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_ci_roda_pytest_parser_e_analyzer_com_action_pinada():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "python -m pytest tests" in text
    assert "Parser]::ParseFile" in text
    assert "-Severity Error" in text
    assert "shell: pwsh" in text
    assert "push:" in text
    assert "pull_request:" in text
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in text
    assert "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97" in text
    assert "RequiredVersion 1.25.0" in text
    assert "uses: actions/checkout@v" not in text
    assert "uses: actions/setup-python@v" not in text
    assert "uses: actions/cache@v" not in text
    assert "actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9" in text
    assert "Register-PSRepository -Default" in text
    assert "PSGallery ja registrada" in text
    assert "nao foi executada" in text
    assert "Invoke-ScriptAnalyzer" in text
    assert text.index("Register-PSRepository -Default") < text.index("Invoke-ScriptAnalyzer")
