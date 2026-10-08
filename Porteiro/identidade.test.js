"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const { decidirIp, ehPeerAdmin, caminhoWebhook, lerAgente, lerIdioma } = require("./identidade");

test("loopback com X-Forwarded-For usa o primeiro salto", () => {
    const visto = decidirIp("127.0.0.1", "203.0.113.10, 10.1.1.1", []);
    assert.equal(visto.acao, "visitante");
    assert.equal(visto.ip, "203.0.113.10");
    assert.equal(visto.via, "x-forwarded-for");
});

test("loopback em IPv4 mapeado também lê o header", () => {
    const visto = decidirIp("::ffff:127.0.0.1", "2001:db8::10", []);
    assert.equal(visto.ip, "2001:db8::10");
    assert.equal(visto.via, "x-forwarded-for");
});

test("loopback sem header continua local", () => {
    const visto = decidirIp("::1", "", []);
    assert.equal(visto.acao, "local");
    assert.equal(visto.ip, "::1");
});

test("faixa 172.16/12 não é localhost nem imune", () => {
    const visto = decidirIp("172.18.0.5", "203.0.113.10", []);
    assert.equal(visto.acao, "negar");
    assert.equal(visto.via, "rede-docker");
    assert.equal(visto.ip, "");
});

test("gateway 192.168.65 sem estar na lista não vira cliente", () => {
    const visto = decidirIp("192.168.65.3", "203.0.113.10", []);
    assert.equal(visto.acao, "negar");
    assert.equal(visto.via, "rede-docker");
});

test("proxy na lista lê o header e sem header nega", () => {
    const com = decidirIp("172.18.0.8", "198.51.100.20", ["172.18.0.8"]);
    assert.equal(com.acao, "visitante");
    assert.equal(com.ip, "198.51.100.20");
    assert.equal(com.via, "x-forwarded-for");
    const sem = decidirIp("172.18.0.8", "", ["172.18.0.8"]);
    assert.equal(sem.acao, "negar");
    assert.equal(sem.via, "proxy-sem-cliente");
});

test("LAN usa o socket e ignora o header", () => {
    const visto = decidirIp("198.51.100.4", "203.0.113.9", []);
    assert.equal(visto.acao, "visitante");
    assert.equal(visto.ip, "198.51.100.4");
    assert.equal(visto.via, "socket");
});

test("aprovar a partir de 172.18 continua sendo peer admin", () => {
    assert.equal(ehPeerAdmin("172.18.0.4"), true);
    assert.equal(ehPeerAdmin("127.0.0.1"), true);
    assert.equal(ehPeerAdmin("198.51.100.4"), false);
});

test("webhook leva os campos novos e o pais fica vazio", () => {
    const caminho = caminhoWebhook("198.51.100.4", "ana", {
        dispositivo: "abc",
        origem: "orig-1",
        origem_aprovada: "orig-0",
        navegador: "Firefox",
        sistema: "Windows",
        idioma: "pt-BR",
        horario: "2026-10-01T19:00:00.000Z",
        pais: "BR",
    });
    const q = new URL(caminho, "http://127.0.0.1").searchParams;
    assert.equal(q.get("ip"), "198.51.100.4");
    assert.equal(q.get("conta"), "ana");
    assert.equal(q.get("dispositivo"), "abc");
    assert.equal(q.get("origem"), "orig-1");
    assert.equal(q.get("origem_aprovada"), "orig-0");
    assert.equal(q.get("navegador"), "Firefox");
    assert.equal(q.get("sistema"), "Windows");
    assert.equal(q.get("idioma"), "pt-BR");
    assert.equal(q.get("horario"), "2026-10-01T19:00:00.000Z");
    assert.equal(q.get("pais"), "");
    assert.notEqual(q.get("pais"), "pt-BR");
});

test("webhook sem conta omite o parametro e nao copia o idioma para o pais", () => {
    const caminho = caminhoWebhook("198.51.100.4", "", {
        idioma: "en-US",
        horario: "2026-10-01T19:00:00.000Z",
    });
    const q = new URL(caminho, "http://127.0.0.1").searchParams;
    assert.equal(q.get("ip"), "198.51.100.4");
    assert.equal(q.has("conta"), false);
    assert.equal(q.get("idioma"), "en-US");
    assert.equal(q.get("pais"), "");
    assert.equal(q.get("horario"), "2026-10-01T19:00:00.000Z");
});

test("user-agent e lido na maquina", () => {
    const edge = lerAgente(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
    );
    assert.equal(edge.navegador, "Edge");
    assert.equal(edge.sistema, "Windows");
    const android = lerAgente("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36");
    assert.equal(android.navegador, "Chrome");
    assert.equal(android.sistema, "Android");
    const curto = lerAgente("FooBar/1.0");
    assert.equal(curto.navegador, "FooBar");
    assert.equal(curto.navegador.length <= 16, true);
    assert.equal(lerIdioma("pt-BR,pt;q=0.9"), "pt-BR");
    assert.equal(lerIdioma(""), "");
});

test("nao ha cliente de geoip nem servico de user-agent", () => {
    const nomes = ["identidade.js", "porteiro.js", "origens.js", "fila.js"];
    const texto = nomes.map((nome) => fs.readFileSync(path.join(__dirname, nome), "utf8")).join("\n");
    assert.equal(/geoip|ip-api|ipinfo|ipwho|ipstack|ipgeolocation|whatismybrowser|userstack/i.test(texto), false);
    assert.equal(texto.includes("mac_address"), false);
});
