"use strict";

const assert = require("node:assert/strict");
const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");
const test = require("node:test");

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "porteiro-token-"));
const chave = crypto.randomBytes(32);
fs.writeFileSync(path.join(tmp, "hmac.key"), chave);
process.env.PORTEIRO_DATA_DIR = path.join(tmp, "dados");
process.env.N8GROKER_AUDIT = path.join(tmp, "audit.jsonl");
process.env.PORTEIRO_HMAC_KEY = path.join(tmp, "hmac.key");
process.env.N8GROKER_DIR = tmp;
process.env.PORTEIRO_TRUSTED_PROXIES = "";
delete process.env.PORTEIRO_TOKEN;
delete process.env.PORTEIRO_PAINEL_TOKEN;
delete process.env.PORTEIRO_N8N_TOKEN;

const porteiro = require("./porteiro");

function ouvir(servidor) {
    return new Promise((resolve) => servidor.listen(0, "127.0.0.1", () => resolve(servidor.address().port)));
}

function pedir(base, url, headers) {
    return new Promise((resolve, reject) => {
        const req = http.request(base + url, { headers: headers || {}, method: "GET" }, (res) => {
            const partes = [];
            res.on("data", (parte) => partes.push(parte));
            res.on("end", () => resolve({ status: res.statusCode, body: Buffer.concat(partes).toString("utf8") }));
        });
        req.on("error", reject);
        req.end();
    });
}

test("aprovacao exige token em qualquer porta", async (t) => {
    const portaPublica = await ouvir(porteiro.server);
    const portaAdmin = await ouvir(porteiro.adminServer);
    const bases = [
        "http://127.0.0.1:" + portaPublica,
        "http://127.0.0.1:" + portaAdmin,
    ];
    t.after(() => {
        porteiro.server.close();
        porteiro.adminServer.close();
    });

    for (const base of bases) {
        const semHeader = await pedir(base, "/n8n/aprovar?ip=203.0.113.9");
        assert.equal(semHeader.status, 403);
        assert.doesNotMatch(semHeader.body, /fila de espera/);
        assert.match(semHeader.body, /nao configurado/);
    }

    fs.writeFileSync(path.join(tmp, "porteiro-painel.token"), "token-do-painel\n");
    fs.writeFileSync(path.join(tmp, "porteiro-n8n.token"), "token-do-n8n\n");

    for (const base of bases) {
        const errado = await pedir(base, "/n8n/aprovar?ip=203.0.113.9", { "x-admin-token": "outro" });
        assert.equal(errado.status, 403);
        assert.match(errado.body, /invalido/);

        const tunel = await pedir(base, "/n8n/aprovar?ip=203.0.113.9", {
            "x-admin-token": "token-do-painel",
            "x-forwarded-for": "203.0.113.9",
            "x-forwarded-host": "exemplo.ngrok-free.app",
            host: "exemplo.ngrok-free.app",
        });
        assert.equal(tunel.status, 403);
        assert.match(tunel.body, /tunel/);

        await pedir(base, "/", { "x-forwarded-for": "203.0.113.9" });
        const semOrigem = await pedir(base, "/n8n/aprovar?ip=203.0.113.9", { "x-admin-token": "token-do-painel" });
        assert.equal(semOrigem.status, 400);
        assert.match(semOrigem.body, /nao aprovado/);
        await pedir(base, "/n8n/registrar-origem?ip=203.0.113.9&origem=orig-1&dispositivo=abc", {
            "x-admin-token": "token-do-painel",
        });
        const certo = await pedir(base, "/n8n/aprovar?ip=203.0.113.9&origem=orig-1", {
            "x-admin-token": "token-do-painel",
        });
        assert.equal(certo.status, 200);
        await pedir(base, "/n8n/registrar-origem?ip=203.0.113.9&origem=orig-2&dispositivo=def", {
            "x-admin-token": "token-do-painel",
        });
        const fila = JSON.parse((await pedir(base, "/n8n/fila", { "x-admin-token": "token-do-painel" })).body);
        const registro = fila.visitantes.find((item) => item.ip === "203.0.113.9");
        assert.equal(registro.origens.find((item) => item.origem === "orig-1").status, "aprovado");
        assert.equal(registro.origens.find((item) => item.origem === "orig-2").status, "pendente");
        const audit = fs.readFileSync(process.env.N8GROKER_AUDIT, "utf8");
        assert.match(audit, /origem_nova/);
        assert.doesNotMatch(audit, /mac/i);

        const peloN8n = await pedir(base, "/n8n/bloquear?ip=203.0.113.9", { "x-admin-token": "token-do-n8n" });
        assert.equal(peloN8n.status, 200);
    }
});
