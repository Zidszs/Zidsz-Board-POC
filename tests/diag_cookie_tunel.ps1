#requires -Version 5.1
<#
.SYNOPSIS
    Diagnostico do cookie n8groker_sessao pelo tunel, antes do reteste.
.DESCRIPTION
    Abre o /painel/ no Edge ou no Chromium, cola o token, clica Entrar e
    lista status, Set-Cookie mascarado e Location de /painel/sessao e
    /painel/escolher. O token e os valores de cookie saem no maximo 4+4.
.PARAMETER UrlTunel
    URL publica do tunel, sem caminho. Exemplo: https://tunel.exemplo
.PARAMETER Token
    Token de entrada. Nao e impresso inteiro.
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$UrlTunel,
    [Parameter(Mandatory = $true)]
    [string]$Token
)

$ErrorActionPreference = 'Stop'

function Get-ValorMascarado {
    param([string]$Valor)
    if ([string]::IsNullOrEmpty($Valor)) {
        return ''
    }
    if ($Valor.Length -le 8) {
        return '****'
    }
    return $Valor.Substring(0, 4) + '...' + $Valor.Substring($Valor.Length - 4)
}

Write-Host ('token ' + (Get-ValorMascarado -Valor $Token))
Write-Host ('tunel ' + $UrlTunel)

$py = Join-Path $env:TEMP ('diag-cookie-' + [guid]::NewGuid().ToString('n') + '.py')
$env:DIAG_URL = $UrlTunel
$env:DIAG_TOKEN = $Token

$codigo = @'
import os
import sys
from urllib.parse import parse_qsl, urlsplit, urlunsplit

def mascarar(valor):
    texto = str(valor or "")
    if len(texto) <= 8:
        return "****" if texto else ""
    return texto[:4] + "..." + texto[-4:]

def redigir(texto):
    token = os.environ.get("DIAG_TOKEN") or ""
    saida = str(texto or "")
    if token and token in saida:
        saida = saida.replace(token, mascarar(token))
    return saida

def caminho(url):
    partes = urlsplit(url)
    pares = []
    for chave, valor in parse_qsl(partes.query, keep_blank_values=True):
        pares.append(chave + "=" + mascarar(valor))
    consulta = ("?" + "&".join(pares)) if pares else ""
    return partes.path + consulta

def mascarar_cookie(linha):
    bruto = str(linha or "").strip()
    if not bruto:
        return ""
    primeiro, sep, resto = bruto.partition(";")
    nome, eq, valor = primeiro.partition("=")
    if not eq:
        return mascarar(primeiro)
    cauda = (";" + resto) if sep else ""
    return nome + "=" + mascarar(valor) + cauda

def interessante(url):
    rota = urlsplit(url).path
    return rota.endswith("/painel/sessao") or rota.endswith("/sessao") or "/escolher" in rota

def main():
    url = (os.environ.get("DIAG_URL") or "").strip().rstrip("/")
    token = os.environ.get("DIAG_TOKEN") or ""
    if not url.startswith("https://") or not token:
        print("informe -UrlTunel https://... e -Token")
        return 1
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        print("playwright ausente. Instale com: python -m pip install playwright")
        return 2
    destino = url + "/painel/"
    vistos = []
    with sync_playwright() as p:
        browser = None
        erros = []
        for kwargs in (
            {"channel": "msedge", "headless": True},
            {"headless": True},
        ):
            try:
                browser = p.chromium.launch(**kwargs)
                break
            except Exception as exc:
                erros.append(type(exc).__name__)
        if browser is None:
            print("navegador ausente " + ",".join(erros))
            return 2
        context = browser.new_context(
            ignore_https_errors=True,
            extra_http_headers={"ngrok-skip-browser-warning": "1"},
        )
        page = context.new_page()

        def ao_responder(resp):
            if not interessante(resp.url):
                return
            try:
                linhas = resp.header_values("set-cookie")
            except Exception:
                unico = resp.headers.get("set-cookie") or ""
                linhas = [unico] if unico else []
            location = ""
            try:
                location = resp.header_value("location") or ""
            except Exception:
                location = resp.headers.get("location") or ""
            corpo = "sem-corpo"
            try:
                texto = resp.text()[:180].lower()
                if "streamlit" in texto:
                    corpo = "html-streamlit"
                elif "ok" == texto.strip() or "<body>ok</body>" in texto:
                    corpo = "html-curto"
                elif texto.startswith("<!doctype") or texto.startswith("<html"):
                    corpo = "html"
                else:
                    corpo = "outro"
            except Exception:
                corpo = "sem-corpo"
            vistos.append((resp.request.method, caminho(resp.url), resp.status, linhas, location, corpo))

        page.on("response", ao_responder)
        try:
            page.goto(destino, wait_until="domcontentloaded", timeout=30000)
            page.get_by_role("button", name="Entrar").wait_for(timeout=20000)
            page.get_by_label("Token").fill(token)
            page.get_by_role("button", name="Entrar").click()
            page.wait_for_timeout(5000)
        except Exception as exc:
            print("falha " + type(exc).__name__ + " " + redigir(exc)[:240])
            browser.close()
            return 1
        print("depois-de-entrar")
        if not vistos:
            print("pedido /painel/sessao (nenhum)")
            print("pedido /painel/escolher (nenhum)")
        for metodo, rota, status, linhas, location, corpo in vistos:
            print("pedido " + metodo + " " + rota)
            print("status " + str(status))
            print("corpo " + corpo)
            if linhas:
                for linha in linhas:
                    print("set-cookie " + mascarar_cookie(linha))
            else:
                print("set-cookie (nenhum)")
            if location:
                partes = urlsplit(location)
                print("location " + urlunsplit(("", "", partes.path or "/", "", "")))
            else:
                print("location (nenhum)")
        print("cookies-do-contexto")
        algum = False
        for item in context.cookies():
            algum = True
            print(
                "cookie "
                + str(item.get("name") or "")
                + " httpOnly="
                + str(bool(item.get("httpOnly")))
                + " secure="
                + str(bool(item.get("secure")))
                + " sameSite="
                + str(item.get("sameSite") or "")
                + " path="
                + str(item.get("path") or "")
                + " valor="
                + mascarar(item.get("value") or "")
            )
        if not algum:
            print("cookie (nenhum)")
        nomes = [item.get("name") for item in context.cookies()]
        if "n8groker_sessao" in nomes:
            print("resultado cookie-presente")
        else:
            print("resultado cookie-ausente")
        browser.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())
'@

Set-Content -LiteralPath $py -Value $codigo -Encoding UTF8
$codigoSaida = 0
try {
    $exe = $null
    foreach ($nome in @('py', 'python', 'python3')) {
        $cmd = Get-Command $nome -ErrorAction SilentlyContinue
        if ($cmd) {
            $exe = $cmd.Source
            break
        }
    }
    if (-not $exe) {
        Write-Host 'Python nao encontrado. Instale Python 3 e o pacote playwright.'
        $codigoSaida = 2
    }
    else {
        & $exe $py
        $codigoSaida = $LASTEXITCODE
    }
}
finally {
    Remove-Item Env:\DIAG_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:\DIAG_URL -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $py) {
        Remove-Item -LiteralPath $py -Force
    }
}
exit $codigoSaida
