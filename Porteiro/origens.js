"use strict";

function lista(registro) {
    if (!registro.origens || !Array.isArray(registro.origens)) registro.origens = [];
    return registro.origens;
}

function anotar(registro, origem, dispositivo) {
    const origens = lista(registro);
    let atual = null;
    let aprovada = "";
    for (const item of origens) {
        if (!item) continue;
        if (item.origem === origem) atual = item;
        else if (item.status === "aprovado" && !aprovada) aprovada = item.origem;
    }
    if (!atual) {
        atual = { origem: origem, dispositivo: dispositivo, status: "pendente" };
        origens.push(atual);
        return { nova: true, item: atual, origem_aprovada: aprovada };
    }
    if (atual.dispositivo !== dispositivo) {
        atual.dispositivo = dispositivo;
        atual.status = "pendente";
        return { nova: true, item: atual, origem_aprovada: aprovada };
    }
    return { nova: false, item: atual, origem_aprovada: aprovada };
}

function aprovar(registro, origem) {
    if (!origem) return { ok: false, status: 400, texto: "Origem obrigatoria. Dispositivo nao aprovado." };
    if (!registro) return { ok: false, status: 404, texto: "Erro: O IP nao esta na fila de espera." };
    const item = lista(registro).find((candidato) => candidato && candidato.origem === origem);
    if (!item) return { ok: false, status: 404, texto: "Origem nao registrada." };
    item.status = "aprovado";
    registro.status = "aprovado";
    return { ok: true, status: 200, texto: `Sucesso! A origem ${origem} do IP ${registro.ip} esta aprovada.` };
}

function bloquearUma(registro, origem) {
    if (!registro) return { ok: false, status: 404, texto: "Erro: O IP nao esta na fila de espera." };
    const item = lista(registro).find((candidato) => candidato && candidato.origem === origem);
    if (!item) return { ok: false, status: 404, texto: "Origem nao registrada." };
    item.status = "bloqueado";
    return { ok: true, status: 200, texto: `Sucesso! A origem ${origem} esta bloqueada.` };
}

module.exports = { anotar, aprovar, bloquearUma };
