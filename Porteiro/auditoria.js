"use strict";

const fs = require("fs");
const path = require("path");

function arquivoAuditoria() {
    if (process.env.N8GROKER_AUDIT) return path.resolve(process.env.N8GROKER_AUDIT);
    return path.resolve(__dirname, "..", ".n8groker", "audit.jsonl");
}

function anexarAuditoria(evento) {
    const arquivo = arquivoAuditoria();
    fs.mkdirSync(path.dirname(arquivo), { recursive: true });
    const linha = {
        hora: evento.hora || new Date().toISOString(),
        usuario: evento.usuario || "admin",
        ip: evento.ip || "",
        acao: evento.acao,
        alvo: evento.alvo || "",
        resultado: evento.resultado || "ok",
    };
    fs.appendFileSync(arquivo, JSON.stringify(linha) + "\n", { encoding: "utf8" });
    return linha;
}

module.exports = { anexarAuditoria, arquivoAuditoria };
