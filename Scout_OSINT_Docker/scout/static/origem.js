/* Primeiro acesso em /painel, em contexto seguro (HTTPS do ngrok).
   A chave privada não sai do navegador. */
(function () {
  var NOME = "n8groker-origem";

  function cookie(nome, valor) {
    document.cookie = nome + "=" + valor + "; Path=/; Secure; SameSite=Lax";
  }

  function ler(nome) {
    var partes = document.cookie ? document.cookie.split("; ") : [];
    for (var i = 0; i < partes.length; i++) {
      if (partes[i].indexOf(nome + "=") === 0) return partes[i].slice(nome.length + 1);
    }
    return "";
  }

  function abrir() {
    return new Promise(function (resolve, reject) {
      var pedido = indexedDB.open(NOME, 1);
      pedido.onupgradeneeded = function () {
        pedido.result.createObjectStore("chave");
      };
      pedido.onerror = function () { reject(pedido.error); };
      pedido.onsuccess = function () { resolve(pedido.result); };
    });
  }

  function guardar(db, chave, origem) {
    return new Promise(function (resolve, reject) {
      var tx = db.transaction("chave", "readwrite");
      tx.objectStore("chave").put({ chave: chave, origem: origem }, "unica");
      tx.oncomplete = function () { resolve(); };
      tx.onerror = function () { reject(tx.error); };
    });
  }

  function lerGuardado(db) {
    return new Promise(function (resolve, reject) {
      var tx = db.transaction("chave", "readonly");
      var get = tx.objectStore("chave").get("unica");
      get.onsuccess = function () { resolve(get.result || null); };
      get.onerror = function () { reject(get.error); };
    });
  }

  function b64(buf) {
    var bytes = new Uint8Array(buf);
    var texto = "";
    for (var i = 0; i < bytes.length; i++) texto += String.fromCharCode(bytes[i]);
    return btoa(texto).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
  }

  function hex(buf) {
    var bytes = new Uint8Array(buf);
    var texto = "";
    for (var i = 0; i < bytes.length; i++) {
      var parte = bytes[i].toString(16);
      texto += parte.length === 1 ? "0" + parte : parte;
    }
    return texto;
  }

  function avisoEl() {
    return document.getElementById("origem-aviso");
  }

  function dizer(texto) {
    var el = avisoEl();
    if (!el) {
      el = document.createElement("p");
      el.id = "origem-aviso";
      if (document.body) document.body.appendChild(el);
    }
    el.textContent = texto;
  }

  function falhaRegistro(texto) {
    dizer(texto);
    var el = avisoEl();
    if (!el || !el.parentNode || document.getElementById("origem-tentar")) return;
    var botao = document.createElement("button");
    botao.type = "button";
    botao.id = "origem-tentar";
    botao.textContent = "Tentar de novo";
    botao.addEventListener("click", function () { location.reload(); });
    el.parentNode.insertBefore(botao, el.nextSibling);
  }

  function recarregar(entrou) {
    var el = avisoEl();
    if (!el) return;
    var modo = el.getAttribute("data-recarregar");
    if (modo === "1") {
      setTimeout(function () { location.reload(); }, 5000);
      return;
    }
    if (!(modo === "agora" && entrou)) return;
    if (document.cookie.indexOf("n8groker_origem=") < 0) {
      dizer("Este navegador recusou o cookie. Ative cookies para este site");
      return;
    }
    setTimeout(function () { location.reload(); }, 400);
  }

  async function garantir() {
    if (!window.isSecureContext || !window.crypto || !window.crypto.subtle) {
      dizer("Este navegador bloqueou a chave do dispositivo. A origem nao foi registrada. Abra o endereco em HTTPS e permita JavaScript e WebCrypto, depois recarregue.");
      return;
    }
    var db = await abrir();
    var guardado = await lerGuardado(db);
    var par = guardado;
    if (!par) {
      var chave = await crypto.subtle.generateKey(
        { name: "ECDSA", namedCurve: "P-256" },
        false,
        ["sign"]
      );
      var origem = crypto.randomUUID();
      await guardar(db, chave, origem);
      par = { chave: chave, origem: origem };
    }
    var spki = await crypto.subtle.exportKey("spki", par.chave.publicKey);
    var digesto = await crypto.subtle.digest("SHA-256", spki);
    var resposta = await fetch(
      "/painel/registrar-origem?origem=" + encodeURIComponent(par.origem) + "&dispositivo=" + encodeURIComponent(hex(digesto)),
      { method: "GET", credentials: "same-origin", cache: "no-store" }
    );
    if (!resposta || !resposta.ok) {
      falhaRegistro("A origem nao entrou na fila. Recarregue a pagina em HTTPS.");
      return false;
    }
    cookie("n8groker_origem", par.origem);
    cookie("n8groker_spki", b64(spki));
    var el = avisoEl();
    if (el && el.getAttribute("data-recarregar")) dizer("Origem enviada. Aguarde a aprovacao.");
    var nonce = ler("n8groker_desafio");
    if (nonce) {
      var horario = new Date().toISOString().replace(/\.\d+Z$/, "Z");
      var texto = new TextEncoder().encode(nonce + "|" + par.origem + "|" + horario);
      var assinatura = await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, par.chave.privateKey, texto);
      cookie("n8groker_horario", horario);
      cookie("n8groker_assinatura", b64(assinatura));
    }
    return true;
  }

  garantir().then(function (entrou) { recarregar(entrou); }, function () {
    falhaRegistro("Nao foi possivel registrar a origem. Recarregue a pagina em HTTPS.");
    recarregar(false);
  });
})();
