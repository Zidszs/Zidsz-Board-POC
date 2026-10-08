"use strict";

const CONTA = /^[A-Za-z0-9._-]{3,64}$/;

function contaValida(conta) {
    return typeof conta === "string" && CONTA.test(conta);
}

function aprovar(registro) {
    if (!registro) return { ok: false, status: 404, texto: "Erro: O IP nao esta na fila de espera." };
    registro.status = "aprovado";
    return { ok: true, status: 200, texto: `Sucesso! O IP ${registro.ip} agora esta aprovado.` };
}

function bloquear(registro) {
    if (!registro) return { ok: false, status: 404, texto: "Erro: O IP nao esta na fila de espera." };
    registro.status = "bloqueado";
    return { ok: true, status: 200, texto: `Sucesso! O IP ${registro.ip} agora esta bloqueado.` };
}

function vincular(registro, conta) {
    if (!contaValida(conta)) return { ok: false, status: 400, texto: "Conta invalida." };
    if (!registro) return { ok: false, status: 404, texto: "Erro: O IP nao esta na fila de espera." };
    if (registro.status !== "aprovado") {
        return { ok: false, status: 409, texto: "O IP precisa estar aprovado antes do vinculo." };
    }
    registro.conta_vinculada = conta;
    registro.vinculo = "ativo";
    return { ok: true, status: 200, texto: `IP ${registro.ip} vinculado a ${conta}.` };
}

function solicitar(registro, conta) {
    if (!contaValida(conta)) return { ok: false, status: 400, texto: "Conta invalida." };
    if (!registro) return { ok: false, status: 404, texto: "Erro: O IP nao esta na fila de espera." };
    registro.conta_solicitada = conta;
    return { ok: true, status: 200 };
}

module.exports = { contaValida, aprovar, bloquear, vincular, solicitar };
