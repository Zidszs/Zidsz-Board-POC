"use strict";

const assert = require("node:assert/strict");
const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");
const test = require("node:test");
const { conferirCliente } = require("./hmac_cliente");

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "porteiro-"));
const chave = crypto.randomBytes(32);
const arquivoChave = path.join(tmp, "hmac.key");
fs.writeFileSync(arquivoChave, chave);
process.env.PORTEIRO_DATA_DIR = path.join(tmp, "dados");
process.env.N8GROKER_AUDIT = path.join(tmp, "audit.jsonl");
process.env.PORTEIRO_HMAC_KEY = arquivoChave;
process.env.PORTEIRO_PAINEL_TOKEN = "token-teste";
delete process.env.PORTEIRO_TOKEN;
process.env.PORTEIRO_TRUSTED_PROXIES = "";

const bordaHits = [];
const borda = http.createServer((req, res) => {
    bordaHits.push({ url: req.url, cliente: req.headers["x-n8groker-client"] || "" });
    res.writeHead(200, { "Content-Type": "text/plain" });
    res.end("borda");
});
borda.on("upgrade", (req, socket) => {
    bordaHits.push({ url: req.url, cliente: req.headers["x-n8groker-client"] || "", upgrade: true });
    socket.write("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n");
    socket.end("ok");
});
const n8nStatus = [];
const n8n = http.createServer((req, res) => {
    n8nStatus.push(req.url);
    res.writeHead(404, { "Content-Type": "text/plain" });
    res.end("sem fluxo");
});

function ouvir(servidor) {
    return new Promise((resolve) => servidor.listen(0, "127.0.0.1", () => resolve(servidor.address().port)));
}

test("caminho do painel, vinculo e websocket", async (t) => {
    const portaBorda = await ouvir(borda);
    const portaN8n = await ouvir(n8n);
    process.env.PAINEL_BORDA_PORT = String(portaBorda);
    process.env.N8N_LOCAL_PORT = String(portaN8n);
    const porteiro = require("./porteiro");
    await ouvir(porteiro.server);
    const base = "http://127.0.0.1:" + porteiro.server.address().port;
    t.after(() => {
        porteiro.server.close();
        borda.close();
        n8n.close();
    });

    function pedir(url, headers) {
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

    // 202 deste processo é o caminho antigo (Porteiro na 5677), não o do túnel com Scout.
    const pendente = await pedir("/painel", { "x-forwarded-for": "203.0.113.40" });
    assert.equal(pendente.status, 202);
    assert.match(pendente.body, /Acesso em Analise/);
    assert.equal(bordaHits.length, 0);

    const semToken = await pedir("/n8n/aprovar?ip=203.0.113.40");
    assert.equal(semToken.status, 403);
    const peloTunel = await pedir("/n8n/aprovar?ip=203.0.113.40", {
        "x-admin-token": "token-teste",
        "x-forwarded-for": "203.0.113.40",
        "x-forwarded-host": "exemplo.ngrok-free.app",
        host: "exemplo.ngrok-free.app",
    });
    assert.equal(peloTunel.status, 403);
    assert.match(peloTunel.body, /tunel/);
    const soHost = await pedir("/n8n/bloquear?ip=203.0.113.40", {
        "x-admin-token": "token-teste",
        host: "exemplo.ngrok-free.app",
    });
    assert.equal(soHost.status, 403);
    const filaTunel = await pedir("/n8n/fila", {
        "x-admin-token": "token-teste",
        "x-forwarded-for": "203.0.113.40",
    });
    assert.equal(filaTunel.status, 403);
    const semOrigem = await pedir("/n8n/aprovar?ip=203.0.113.40", { "x-admin-token": "token-teste" });
    assert.equal(semOrigem.status, 400);
    await pedir("/n8n/registrar-origem?ip=203.0.113.40&origem=orig-a&dispositivo=abc", {
        "x-admin-token": "token-teste",
    });
    const aprovado = await pedir("/n8n/aprovar?ip=203.0.113.40&origem=orig-a", { "x-admin-token": "token-teste" });
    assert.equal(aprovado.status, 200);
    const filaAntes = JSON.parse((await pedir("/n8n/fila", { "x-admin-token": "token-teste" })).body);
    const registroAntes = filaAntes.visitantes.find((item) => item.ip === "203.0.113.40");
    assert.equal(registroAntes.status, "aprovado");
    assert.equal(registroAntes.conta_vinculada, undefined);

    const pagina = await pedir("/painel", { "x-forwarded-for": "203.0.113.40" });
    assert.equal(pagina.status, 200);
    assert.equal(pagina.body, "borda");
    assert.equal(conferirCliente(bordaHits.at(-1).cliente, chave), "203.0.113.40");

    const bloqueado = await new Promise((resolve) => {
        const req = http.request(base + "/painel/_stcore/stream", {
            headers: {
                "x-forwarded-for": "203.0.113.77",
                Connection: "Upgrade",
                Upgrade: "websocket",
                "Sec-WebSocket-Key": crypto.randomBytes(16).toString("base64"),
                "Sec-WebSocket-Version": "13",
            },
        });
        req.setTimeout(1500, () => resolve("timeout"));
        req.on("upgrade", () => resolve("upgrade"));
        req.on("error", () => resolve("erro"));
        req.on("response", () => resolve("http"));
        req.end();
    });
    assert.equal(bloqueado, "erro");

    await pedir("/", { "x-forwarded-for": "203.0.113.77" });
    await pedir("/n8n/registrar-origem?ip=203.0.113.77&origem=orig-b&dispositivo=abc", {
        "x-admin-token": "token-teste",
    });
    assert.equal(
        (await pedir("/n8n/aprovar?ip=203.0.113.77&origem=orig-b", { "x-admin-token": "token-teste" })).status,
        200
    );
    const aceito = await new Promise((resolve, reject) => {
        const req = http.request(base + "/painel/_stcore/stream", {
            headers: {
                "x-forwarded-for": "203.0.113.77",
                Connection: "Upgrade",
                Upgrade: "websocket",
                "Sec-WebSocket-Key": crypto.randomBytes(16).toString("base64"),
                "Sec-WebSocket-Version": "13",
            },
        });
        req.setTimeout(1500, () => reject(new Error("timeout")));
        req.on("upgrade", (_res, socket) => {
            socket.end();
            resolve("websocket");
        });
        req.on("error", reject);
        req.end();
    });
    assert.equal(aceito, "websocket");
    const up = bordaHits.filter((item) => item.upgrade).at(-1);
    assert.equal(conferirCliente(up.cliente, chave), "203.0.113.77");

    assert.equal(
        (await pedir("/n8n/vincular?ip=203.0.113.40&conta=ana&origem=orig-a", { "x-admin-token": "token-teste" })).status,
        200
    );
    const pedido = await pedir("/n8n/solicitar?ip=203.0.113.40&conta=ana", { "x-admin-token": "token-teste" });
    assert.equal(pedido.status, 200);
    assert.equal(JSON.parse(pedido.body).n8n_status, 404);
    assert.ok(n8nStatus.some((url) => url.includes("conta=ana")));
    const filaDepois = JSON.parse((await pedir("/n8n/fila", { "x-admin-token": "token-teste" })).body);
    const registro = filaDepois.visitantes.find((item) => item.ip === "203.0.113.40");
    assert.equal(registro.conta_vinculada, "ana");
    assert.equal(registro.conta_solicitada, "ana");
    const audit = fs.readFileSync(process.env.N8GROKER_AUDIT, "utf8");
    assert.match(audit, /"acao":"aprovar"/);
    assert.match(audit, /"acao":"vincular"/);
    assert.doesNotMatch(audit, /token-teste/);

    porteiro.visitantes.set("203.0.113.68", {
        ip: "203.0.113.68",
        status: "pendente",
        tentativas: 68,
        origens: [],
        pais: "",
    });
    const toque = JSON.parse((await pedir("/n8n/tocar?ip=203.0.113.68", { "x-admin-token": "token-teste" })).body);
    assert.equal(toque.visitante.tentativas, 69);
    assert.ok(toque.visitante.ultima_vista);
    const visto = toque.visitante.ultima_vista;
    const deNovo = JSON.parse((await pedir("/n8n/tocar?ip=203.0.113.68", { "x-admin-token": "token-teste" })).body);
    assert.equal(deNovo.visitante.tentativas, 70);
    const regOrigem = await pedir(
        "/n8n/registrar-origem?ip=203.0.113.68&origem=orig-68&dispositivo=abcdef12",
        { "x-admin-token": "token-teste" }
    );
    assert.equal(regOrigem.status, 200);
    const depoisOrigem = JSON.parse((await pedir("/n8n/fila", { "x-admin-token": "token-teste" })).body)
        .visitantes.find((item) => item.ip === "203.0.113.68");
    assert.equal(depoisOrigem.tentativas, 70);
    assert.ok(depoisOrigem.ultima_vista >= visto);
    assert.equal(depoisOrigem.origens[0].origem, "orig-68");
    assert.equal(depoisOrigem.origens[0].status, "pendente");

    const fresco = "203.0.113.81";
    const criado = JSON.parse((await pedir("/n8n/tocar?ip=" + fresco, { "x-admin-token": "token-teste" })).body);
    assert.equal(criado.visitante.tentativas, 1);
    assert.equal(criado.visitante.status, "pendente");
    assert.equal((criado.visitante.origens || []).length, 0);
    const cedo = await pedir("/n8n/aprovar?ip=" + fresco, { "x-admin-token": "token-teste" });
    assert.equal(cedo.status, 400);
    assert.match(cedo.body, /Origem obrigatoria/);
    const reg = await pedir(
        "/n8n/registrar-origem?ip=" + fresco + "&origem=orig-flow&dispositivo=abcdef0123456789",
        { "x-admin-token": "token-teste" }
    );
    assert.equal(reg.status, 200);
    const filaMeio = JSON.parse((await pedir("/n8n/fila", { "x-admin-token": "token-teste" })).body)
        .visitantes.find((item) => item.ip === fresco);
    assert.equal(filaMeio.tentativas, 1);
    const okFluxo = await pedir("/n8n/aprovar?ip=" + fresco + "&origem=orig-flow", { "x-admin-token": "token-teste" });
    assert.equal(okFluxo.status, 200);
    const segundo = await pedir(
        "/n8n/registrar-origem?ip=" + fresco + "&origem=orig-dois&dispositivo=bbbbbbbbbbbbbbbb",
        { "x-admin-token": "token-teste" }
    );
    assert.equal(segundo.status, 200);
    const filaFim = JSON.parse((await pedir("/n8n/fila", { "x-admin-token": "token-teste" })).body)
        .visitantes.find((item) => item.ip === fresco);
    assert.equal(filaFim.status, "aprovado");
    assert.equal(filaFim.origens.find((item) => item.origem === "orig-flow").status, "aprovado");
    assert.equal(filaFim.origens.find((item) => item.origem === "orig-dois").status, "pendente");

    const analise = await pedir("/painel", { "x-forwarded-for": "203.0.113.82" });
    assert.equal(analise.status, 202);
    assert.match(analise.body, /\/origem\.js/);
    assert.match(analise.body, /origem-aviso/);
    assert.match(analise.body, /noscript/);
    assert.match(analise.body, /JavaScript esta desligado/);
    assert.doesNotMatch(analise.body, /painel/i);
    const script = await pedir("/origem.js", { "x-forwarded-for": "203.0.113.82" });
    assert.equal(script.status, 200);
    assert.match(script.body, /P-256/);
    const filaScript = JSON.parse((await pedir("/n8n/fila", { "x-admin-token": "token-teste" })).body)
        .visitantes.find((item) => item.ip === "203.0.113.82");
    assert.equal(filaScript.tentativas, 1);
    const publico = await pedir(
        "/painel/registrar-origem?origem=orig-pub&dispositivo=abcdef12&ip=198.51.100.9",
        { "x-forwarded-for": "203.0.113.82" }
    );
    assert.equal(publico.status, 200);
    const filaPub = JSON.parse((await pedir("/n8n/fila", { "x-admin-token": "token-teste" })).body);
    const dono = filaPub.visitantes.find((item) => item.ip === "203.0.113.82");
    assert.equal(dono.tentativas, 1);
    assert.ok(dono.origens.some((item) => item.origem === "orig-pub" && item.status === "pendente"));
    assert.equal(filaPub.visitantes.find((item) => item.ip === "198.51.100.9"), undefined);
});

test("chave hmac em pasta nao e lida como arquivo", () => {
    const { carregarChave } = require("./hmac_cliente");
    const pasta = path.join(tmp, "chave-pasta");
    fs.mkdirSync(pasta);
    assert.throws(() => carregarChave(pasta), /pasta/);
    const vazia = path.join(tmp, "chave-vazia");
    fs.mkdirSync(vazia);
    assert.throws(() => carregarChave(vazia), /porteiro-hmac\.key e uma pasta/);
});
