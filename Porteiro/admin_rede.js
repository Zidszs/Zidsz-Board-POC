"use strict";

const { ehLocalhost, ehDockerInterno, normalizarIp } = require("./identidade");

const SUFIXOS_NGROK = [".ngrok.io", ".ngrok.app", ".ngrok.dev", ".ngrok-free.app", ".ngrok-free.dev"];

function headerPreenchido(valor) {
    if (valor == null) return false;
    if (Array.isArray(valor)) return valor.some((item) => headerPreenchido(item));
    return String(valor).trim() !== "";
}

function nomeHost(host) {
    let texto = String(host || "").trim().toLowerCase();
    if (!texto) return "";
    if (texto.startsWith("[")) {
        const fim = texto.indexOf("]");
        return fim > 1 ? texto.slice(1, fim) : texto;
    }
    return texto.split(":")[0];
}

function hostEhNgrok(host, dominioNgrok) {
    const nome = nomeHost(host);
    if (!nome) return false;
    if (nome === "localhost" || nome === "127.0.0.1" || nome === "::1" || nome === "host.docker.internal") {
        return false;
    }
    const dominio = nomeHost(dominioNgrok);
    if (dominio && dominio !== "localhost" && nome === dominio) return true;
    return SUFIXOS_NGROK.some((sufixo) => nome.endsWith(sufixo));
}

function sinalDeTunel(headers, dominioNgrok) {
    const h = headers || {};
    if (headerPreenchido(h["x-forwarded-for"])) return "x-forwarded-for";
    if (headerPreenchido(h["x-forwarded-host"])) return "x-forwarded-host";
    if (hostEhNgrok(h.host, dominioNgrok)) return "host-ngrok";
    return "";
}

function ipsN8nDe(lista) {
    const itens = Array.isArray(lista) ? lista : String(lista || "").split(/[\s,]+/);
    const conjunto = new Set();
    for (const item of itens) {
        const ip = normalizarIp(String(item).trim());
        if (ip) conjunto.add(ip);
    }
    return conjunto;
}

// A faixa 172.16/12 inteira não é admin: o Scout também está nela.
// Só o IP do container n8n, descoberto na subida, passa. Lista vazia recusa.
function socketAdminLocal(socketIp, ipsN8n) {
    const socket = normalizarIp(socketIp);
    if (ehLocalhost(socket)) return "loopback";
    if (ehDockerInterno(socket) && ipsN8nDe(ipsN8n).has(socket)) return "n8n";
    return "";
}

// Token não entra aqui: túnel com token válido continua recusado.
function decidirAdmin(socketIp, headers, dominioNgrok, ipsN8n) {
    const sinal = sinalDeTunel(headers, dominioNgrok);
    if (sinal) {
        return {
            ok: false,
            via: sinal,
            motivo: "As rotas de aprovacao nao aceitam acesso pelo tunel.",
        };
    }
    const via = socketAdminLocal(socketIp, ipsN8n);
    if (!via) {
        return {
            ok: false,
            via: "socket",
            motivo: "As rotas de aprovacao so aceitam a rede local da maquina.",
        };
    }
    return { ok: true, via, motivo: "" };
}

module.exports = { decidirAdmin, sinalDeTunel, socketAdminLocal, hostEhNgrok };
