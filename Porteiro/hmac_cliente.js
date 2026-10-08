"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

function assinarCliente(ip, chave) {
    const mac = crypto.createHmac("sha256", chave).update(String(ip), "utf8").digest("hex");
    return String(ip) + "|" + mac;
}

function conferirCliente(valor, chave) {
    if (!valor || !chave) return "";
    const texto = String(valor);
    const corte = texto.lastIndexOf("|");
    if (corte <= 0) return "";
    const ip = texto.slice(0, corte);
    const mac = texto.slice(corte + 1);
    const esperado = crypto.createHmac("sha256", chave).update(ip, "utf8").digest("hex");
    const a = Buffer.from(mac);
    const b = Buffer.from(esperado);
    if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) return "";
    return ip;
}

function carregarChave(arquivo) {
    const destino = arquivo || path.resolve(__dirname, "..", ".n8groker", "porteiro-hmac.key");
    if (fs.existsSync(destino)) {
        const info = fs.statSync(destino);
        if (info.isDirectory()) {
            throw new Error(
                "porteiro-hmac.key e uma pasta, nao um arquivo. " +
                "O Docker cria essa pasta quando o arquivo nao existe no compose. " +
                "Rode iniciar_servicos.ps1 de novo para reparar a pasta vazia."
            );
        }
        return fs.readFileSync(destino);
    }
    fs.mkdirSync(path.dirname(destino), { recursive: true });
    const chave = crypto.randomBytes(32);
    fs.writeFileSync(destino, chave, { mode: 0o600 });
    try { fs.chmodSync(destino, 0o600); } catch { /* Windows */ }
    return chave;
}

module.exports = { assinarCliente, conferirCliente, carregarChave };
