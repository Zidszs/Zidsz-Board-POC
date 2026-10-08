"use strict";

// Decide o IP do visitante a partir do socket e dos headers desta conexão.
// Não há IP de cliente padrão: se o proxy confiável não trouxer um, a resposta é negar.

function normalizarIp(ip) {
    if (!ip) return "";
    return ip.startsWith("::ffff:") ? ip.slice(7) : ip;
}

function ehLocalhost(ip) {
    const n = normalizarIp(ip);
    return n === "127.0.0.1" || n === "::1";
}

function ehDockerInterno(ip) {
    const n = normalizarIp(ip);
    const m = n.match(/^172\.(\d+)\./);
    if (!m) return false;
    const segundo = +m[1];
    return segundo >= 16 && segundo <= 31;
}

function ehGatewayDockerDesktop(ip) {
    const n = normalizarIp(ip);
    return /^192\.168\.65\.\d{1,3}$/.test(n);
}

function ehRedeDocker(ip) {
    return ehDockerInterno(ip) || ehGatewayDockerDesktop(ip);
}

function ehFormatoIpValido(ip) {
    if (!ip || ip.length > 45) return false;
    return /^[0-9a-fA-F:.]+$/.test(ip);
}

function primeiroEncaminhado(xff) {
    if (!xff || typeof xff !== "string") return "";
    const primeiro = xff.split(",")[0].trim();
    if (!ehFormatoIpValido(primeiro)) return "";
    return normalizarIp(primeiro);
}

function listaProxies(lista) {
    const itens = Array.isArray(lista) ? lista : String(lista || "").split(",");
    return new Set(itens.map((item) => normalizarIp(String(item).trim())).filter(Boolean));
}

function decidirIp(socketIp, xff, proxies) {
    const socket = normalizarIp(socketIp);
    const conhecidos = listaProxies(proxies);
    if (ehLocalhost(socket)) {
        const hop = primeiroEncaminhado(xff);
        if (!hop || ehLocalhost(hop)) {
            return { acao: "local", ip: socket, via: "loopback", socket };
        }
        if (ehRedeDocker(hop)) {
            return {
                acao: "negar",
                ip: "",
                via: "rede-docker",
                socket,
                motivo: "A rede do Docker não conta como localhost.",
            };
        }
        return { acao: "visitante", ip: hop, via: "x-forwarded-for", socket };
    }
    if (conhecidos.has(socket)) {
        const hop = primeiroEncaminhado(xff);
        if (!hop || ehLocalhost(hop) || ehRedeDocker(hop)) {
            return {
                acao: "negar",
                ip: "",
                via: "proxy-sem-cliente",
                socket,
                motivo: "Proxy confiável sem IP de cliente. Acesso negado.",
            };
        }
        return { acao: "visitante", ip: hop, via: "x-forwarded-for", socket };
    }
    if (ehRedeDocker(socket)) {
        return {
            acao: "negar",
            ip: "",
            via: "rede-docker",
            socket,
            motivo: "A rede do Docker não conta como localhost.",
        };
    }
    if (!ehFormatoIpValido(socket)) {
        return { acao: "negar", ip: "", via: "socket-invalido", socket, motivo: "IP de origem inválido." };
    }
    return { acao: "visitante", ip: socket, via: "socket", socket };
}

// Rota admin: o container do n8n continua podendo falar. Isso não libera visitante.
function ehPeerAdmin(socketIp) {
    const socket = normalizarIp(socketIp);
    return ehLocalhost(socket) || ehRedeDocker(socket);
}

function tokenCurto(texto) {
    const pedaco = String(texto || "").trim().split(/[\s/;()]+/)[0] || "";
    return pedaco.slice(0, 16);
}

function lerAgente(ua) {
    const texto = String(ua || "");
    let navegador = "";
    let sistema = "";
    if (/Edg\//.test(texto)) navegador = "Edge";
    else if (/Firefox\//.test(texto)) navegador = "Firefox";
    else if (/Chrome\//.test(texto)) navegador = "Chrome";
    else if (/Safari\//.test(texto)) navegador = "Safari";
    else navegador = tokenCurto(texto);

    if (/Android/.test(texto)) sistema = "Android";
    else if (/iPhone|iPad|iPod/.test(texto)) sistema = "iOS";
    else if (/Windows/.test(texto)) sistema = "Windows";
    else if (/Mac OS X|Macintosh/.test(texto)) sistema = "macOS";
    else if (/Linux/.test(texto)) sistema = "Linux";
    else sistema = tokenCurto(texto);
    return { navegador: navegador, sistema: sistema };
}

function lerIdioma(acceptLanguage) {
    const texto = String(acceptLanguage || "").split(",")[0].split(";")[0].trim();
    return texto.slice(0, 32);
}

function horarioUtc(marca) {
    const data = marca instanceof Date ? marca : new Date();
    return data.toISOString();
}

function caminhoWebhook(ip, conta, extra) {
    const dados = extra || {};
    const params = new URLSearchParams();
    params.set("ip", String(ip || ""));
    if (conta) params.set("conta", String(conta));
    params.set("dispositivo", String(dados.dispositivo || ""));
    params.set("origem", String(dados.origem || ""));
    params.set("origem_aprovada", String(dados.origem_aprovada || ""));
    params.set("navegador", String(dados.navegador || ""));
    params.set("sistema", String(dados.sistema || ""));
    params.set("idioma", String(dados.idioma || ""));
    params.set("horario", String(dados.horario || horarioUtc()));
    params.set("pais", "");
    return "/webhook/solicitar-verificacao-acesso?" + params.toString();
}

module.exports = {
    normalizarIp,
    ehLocalhost,
    ehDockerInterno,
    ehRedeDocker,
    ehFormatoIpValido,
    decidirIp,
    ehPeerAdmin,
    lerAgente,
    lerIdioma,
    horarioUtc,
    caminhoWebhook,
};
