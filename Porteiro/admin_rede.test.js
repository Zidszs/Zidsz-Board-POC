"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { decidirAdmin } = require("./admin_rede");

const tokenNaoImporta = { "x-admin-token": "token-valido" };

test("loopback passa; a faixa docker so passa se for o IP do n8n", () => {
    assert.equal(decidirAdmin("127.0.0.1", { host: "127.0.0.1:5677", ...tokenNaoImporta }, "").ok, true);
    assert.equal(decidirAdmin("::ffff:127.0.0.1", { host: "host.docker.internal:5677" }, "").via, "loopback");
    const scout = decidirAdmin("172.18.0.4", { host: "host.docker.internal:5677" }, "localhost");
    assert.equal(scout.ok, false);
    const semLista = decidirAdmin("172.18.0.4", { host: "host.docker.internal:5677" }, "localhost", []);
    assert.equal(semLista.ok, false);
    const n8n = decidirAdmin("172.18.0.4", { host: "host.docker.internal:5677" }, "localhost", ["172.18.0.4"]);
    assert.equal(n8n.ok, true);
    assert.equal(n8n.via, "n8n");
    const outro = decidirAdmin("172.18.0.9", { host: "host.docker.internal:5677" }, "", ["172.18.0.4"]);
    assert.equal(outro.ok, false);
});

test("tunel com token e headers de proxy e recusado", () => {
    const peloXff = decidirAdmin(
        "127.0.0.1",
        { host: "127.0.0.1:5677", "x-forwarded-for": "203.0.113.40", "x-admin-token": "token-valido" },
        ""
    );
    assert.equal(peloXff.ok, false);
    assert.equal(peloXff.via, "x-forwarded-for");

    const peloHostFwd = decidirAdmin(
        "172.18.0.4",
        { host: "host.docker.internal:5677", "x-forwarded-host": "exemplo.ngrok-free.app", "x-admin-token": "token-valido" },
        ""
    );
    assert.equal(peloHostFwd.ok, false);
    assert.equal(peloHostFwd.via, "x-forwarded-host");

    const peloHost = decidirAdmin(
        "127.0.0.1",
        { host: "exemplo.ngrok-free.app", "x-admin-token": "token-valido" },
        "exemplo.ngrok-free.app"
    );
    assert.equal(peloHost.ok, false);
    assert.equal(peloHost.via, "host-ngrok");
});

test("gateway docker e IP de fora nao sao rede local de admin", () => {
    assert.equal(decidirAdmin("192.168.65.3", { host: "127.0.0.1:5677" }, "").ok, false);
    assert.equal(decidirAdmin("198.51.100.4", { host: "198.51.100.4:5677" }, "").ok, false);
});
