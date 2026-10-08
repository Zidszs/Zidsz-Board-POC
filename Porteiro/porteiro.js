const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { decidirIp, ehLocalhost, ehFormatoIpValido, normalizarIp, caminhoWebhook, lerAgente, lerIdioma } = require('./identidade');
const { decidirAdmin } = require('./admin_rede');
const { assinarCliente, carregarChave } = require('./hmac_cliente');
const { anexarAuditoria } = require('./auditoria');
const fila = require('./fila');
const origens = require('./origens');

// ==========================================
// 1. CONFIGURAÇÕES E CAMINHOS
// ==========================================
// Portátil: n8n/storage/Porteiro relativo à raiz do projeto (../ a partir de Porteiro/).
// Sobrescrever com PORTEIRO_DATA_DIR no ambiente, se necessário.
const DATA_DIR = process.env.PORTEIRO_DATA_DIR
    ? path.resolve(process.env.PORTEIRO_DATA_DIR)
    : path.resolve(__dirname, '..', 'n8n', 'storage', 'Porteiro');
const ARQUIVO_CONTROLE = path.join(DATA_DIR, 'controle_acesso.json');
const ARQUIVO_LOG = path.join(DATA_DIR, 'registro_portaria.log');
const PORTA_DO_PORTEIRO = 5677;
const PORTA_ADMIN = Number(process.env.PORTEIRO_ADMIN_PORT || 5676);

// Dois tokens, lidos em cada rota. Não vêm do .env: o Scout monta esse arquivo.
// porteiro-painel.token = aba Admin. porteiro-n8n.token = workflow do n8n.
// PORTEIRO_TOKEN no ambiente é ignorado de propósito.

// Limites de proteção contra DoS / poluição de banco
const MAX_VISITANTES = 1000;          // teto total no banco
const LIMITE_REQ_POR_MIN = 60;        // por IP de socket
const TIMEOUT_PROXY_MS = 30000;
const TIMEOUT_NGROK_MS = 3000;

let DOMINIO_BLOQUEADO = 'localhost';

if (!fs.existsSync(DATA_DIR)) {
    fs.mkdirSync(DATA_DIR, { recursive: true });
}

function pastaSegredos() {
    if (process.env.N8GROKER_DIR) return path.resolve(process.env.N8GROKER_DIR);
    return path.resolve(__dirname, '..', '.n8groker');
}

function lerTokenArquivo(nome) {
    try {
        const texto = fs.readFileSync(path.join(pastaSegredos(), nome), 'utf8').replace(/^\uFEFF/, '').trim();
        if (!texto || /[\r\n]/.test(texto)) return '';
        return texto;
    } catch (erro) {
        return '';
    }
}

function tokensDeAprovacao() {
    const lista = [];
    const painel = String(process.env.PORTEIRO_PAINEL_TOKEN || '').trim() || lerTokenArquivo('porteiro-painel.token');
    const n8n = String(process.env.PORTEIRO_N8N_TOKEN || '').trim() || lerTokenArquivo('porteiro-n8n.token');
    if (painel) lista.push(painel);
    if (n8n && n8n !== painel) lista.push(n8n);
    return lista;
}

function tokenConfere(apresentado, validos) {
    if (!apresentado || !validos.length) return false;
    const recebido = Buffer.from(String(apresentado));
    for (let i = 0; i < validos.length; i++) {
        const alvo = Buffer.from(validos[i]);
        if (recebido.length === alvo.length && crypto.timingSafeEqual(recebido, alvo)) return true;
    }
    return false;
}

if (!tokensDeAprovacao().length) {
    console.error('[PORTEIRO] Sem token de aprovacao. As rotas /n8n/* ficam fechadas (403).');
}

// ==========================================
// 2. UTILITÁRIOS DE IP E ESCAPE
// ==========================================
function proxiesConfiaveis() {
    return (process.env.PORTEIRO_TRUSTED_PROXIES || '')
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean);
}

function arquivoIpsN8n() {
    if (process.env.PORTEIRO_N8N_IP_FILE) return path.resolve(process.env.PORTEIRO_N8N_IP_FILE);
    return path.resolve(__dirname, '..', '.n8groker', 'n8n-container-ip');
}

// Lido em cada rota admin: o Porteiro sobe antes do n8n, e o IP do container muda.
function ipsDoN8n() {
    const deEnv = String(process.env.PORTEIRO_N8N_IPS || '').split(/[\s,]+/).filter(Boolean);
    let deArquivo = [];
    try {
        const texto = fs.readFileSync(arquivoIpsN8n(), 'utf8').replace(/^\uFEFF/, '');
        deArquivo = texto.split(/[\s,]+/).filter(Boolean);
    } catch (erro) {
        deArquivo = [];
    }
    return deEnv.concat(deArquivo);
}

function escHtml(s) {
    return String(s).replace(/[&<>"']/g, c =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function idPublico(valor) {
    return !!valor && valor.length <= 128 && /^[A-Za-z0-9_-]+$/.test(valor);
}

const CSP_ANALISE = "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'";

// Caminho antigo. Só vale quando o túnel cai direto na 5677 (Scout desligado).
// No uso diário o ngrok entrega no Scout, e o 202 sai de porta_apps.py.
function paginaAnalise(ip) {
    return '<!DOCTYPE html><html><head><meta http-equiv="Content-Security-Policy" content="' + CSP_ANALISE + '"></head>'
        + '<body style="font-family:sans-serif; text-align:center; padding:50px; background-color:#1e1e1e; color:#fff;">'
        + '<h2>Acesso em Analise</h2>'
        + '<p>Seu IP (<b>' + escHtml(ip) + '</b>) foi enviado para aprovacao do administrador.</p>'
        + '<p>Por favor, aguarde alguns instantes. Esta pagina atualiza sozinha.</p>'
        + '<p id="origem-aviso" data-recarregar="1">Se o navegador bloquear JavaScript ou a chave do dispositivo, a origem nao e registrada e o administrador nao consegue aprovar. Abra este endereco em HTTPS, libere JavaScript e recarregue.</p>'
        + '<noscript><p>JavaScript esta desligado. Sem ele a origem nao e registrada e a aprovacao nao acontece.</p></noscript>'
        + '<script src="/origem.js"></script>'
        + '</body></html>';
}

function servirOrigem(res) {
    const arquivo = path.join(__dirname, '..', 'Scout_OSINT_Docker', 'scout', 'static', 'origem.js');
    let corpo;
    try {
        corpo = fs.readFileSync(arquivo);
    } catch (e) {
        res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
        return res.end('script ausente');
    }
    res.writeHead(200, {
        'Content-Type': 'text/javascript; charset=utf-8',
        'X-Content-Type-Options': 'nosniff',
        'Cache-Control': 'no-store',
        'Content-Security-Policy': "default-src 'none'",
    });
    return res.end(corpo);
}

function criarPendente(ip, headers) {
    const agente = lerAgente((headers || {})['user-agent']);
    const agora = new Date().toISOString();
    return {
        ip: ip,
        status: 'pendente',
        data_primeiro_acesso: agora,
        tentativas: 1,
        navegador: agente.navegador,
        sistema: agente.sistema,
        idioma: lerIdioma((headers || {})['accept-language']),
        horario: agora,
        ultima_vista: agora,
        pais: '',
        dispositivo: '',
        origem: '',
        origem_aprovada: '',
        origens: []
    };
}

function anotarOrigemNoRegistro(registro, origem, dispositivo) {
    const anotado = origens.anotar(registro, origem, dispositivo);
    registro.dispositivo = dispositivo;
    registro.origem = origem;
    registro.origem_aprovada = anotado.origem_aprovada || '';
    registro.pais = '';
    registro.ultima_vista = new Date().toISOString();
    if (!registro.horario) registro.horario = registro.ultima_vista;
    return anotado;
}

function avisarOrigem(ipAlvo, registro, anotado, origem, dispositivo) {
    if (anotado.nova) {
        const foto = camposWebhook(registro);
        const contaAviso = registro.conta_vinculada || registro.conta_solicitada || '';
        setImmediate(() => acionarN8n(ipAlvo, contaAviso, foto));
    }
    if (anotado.nova && anotado.origem_aprovada) {
        auditar(
            'origem_nova',
            ipAlvo,
            anotado.origem_aprovada + '|' + origem + '|' + dispositivo,
            'aguardando'
        );
    }
}

function registrarPeloVisitante(res, ipReal, urlVisita) {
    const origem = urlVisita.searchParams.get('origem') || '';
    const dispositivo = urlVisita.searchParams.get('dispositivo') || '';
    if (!idPublico(origem) || !idPublico(dispositivo)) {
        res.writeHead(400, { 'Content-Type': 'text/plain; charset=utf-8' });
        return res.end('Origem invalida.');
    }
    let registro = visitantes.get(ipReal);
    if (!registro) {
        if (visitantes.size >= MAX_VISITANTES) podarVisitantes();
        registro = criarPendente(ipReal, {});
        visitantes.set(ipReal, registro);
        const foto = camposWebhook(registro);
        setImmediate(() => acionarN8n(ipReal, '', foto));
    }
    const anotado = anotarOrigemNoRegistro(registro, origem, dispositivo);
    agendarPersist();
    avisarOrigem(ipReal, registro, anotado, origem, dispositivo);
    return responderJson(res, 200, {
        ok: true,
        origem: origem,
        origem_aprovada: anotado.origem_aprovada,
        status: anotado.item.status,
    });
}

// Socket é a verdade: vem da pilha TCP do Node, não é falsificável pelo cliente.
function obterIpSocket(req) {
    return normalizarIp(req.socket.remoteAddress || '');
}

function classificar(req) {
    return decidirIp(obterIpSocket(req), req.headers['x-forwarded-for'], proxiesConfiaveis());
}

// ==========================================
// 3. ESTADO EM MEMÓRIA + PERSISTÊNCIA ATÔMICA
// ==========================================
const visitantes = new Map();         // ip -> registro
const rateLimit = new Map();          // ipSocket -> { count, janela }

function carregarBancoInicial() {
    try {
        if (!fs.existsSync(ARQUIVO_CONTROLE)) return;
        const dados = JSON.parse(fs.readFileSync(ARQUIVO_CONTROLE, 'utf8'));
        for (const v of dados.visitantes || []) {
            if (v && ehFormatoIpValido(v.ip)) visitantes.set(v.ip, v);
        }
        console.log(`[BANCO] ${visitantes.size} visitante(s) carregado(s).`);
    } catch (e) {
        console.error(`[ERRO] Falha ao carregar banco: ${e.message}`);
    }
}

let persistTimer = null;
let persistChain = Promise.resolve();

// Debounce: várias mudanças em sequência viram uma única gravação.
function agendarPersist() {
    if (persistTimer) return;
    persistTimer = setTimeout(() => {
        persistTimer = null;
        persistChain = persistChain.then(persistirAgora);
    }, 200);
}

// Gravação atômica: escreve em .tmp e renomeia. Evita arquivo corrompido se cair no meio.
async function persistirAgora() {
    const tmp = ARQUIVO_CONTROLE + '.tmp';
    try {
        const dados = { visitantes: Array.from(visitantes.values()) };
        await fs.promises.writeFile(tmp, JSON.stringify(dados, null, 2));
        await fs.promises.rename(tmp, ARQUIVO_CONTROLE);
    } catch (e) {
        console.error(`[ERRO] Falha ao persistir banco: ${e.message}`);
        try { await fs.promises.unlink(tmp); } catch { /* ignorar */ }
    }
}

function podarVisitantes() {
    if (visitantes.size <= MAX_VISITANTES) return;
    // Descarta os pendentes mais antigos primeiro
    const pendentes = Array.from(visitantes.values())
        .filter(v => v.status === 'pendente')
        .sort((a, b) => (a.data_primeiro_acesso || '').localeCompare(b.data_primeiro_acesso || ''));
    let remover = visitantes.size - MAX_VISITANTES;
    for (const v of pendentes) {
        if (remover <= 0) break;
        visitantes.delete(v.ip);
        remover--;
    }
}

// ==========================================
// 4. RATE LIMIT (por IP de socket — único que não é falsificável)
// ==========================================
function dentroDoLimite(ipSocket) {
    if (ehLocalhost(ipSocket)) return true; // tráfego puramente local sem limite
    const agora = Date.now();
    const reg = rateLimit.get(ipSocket);
    if (!reg || agora - reg.janela > 60000) {
        rateLimit.set(ipSocket, { count: 1, janela: agora });
        return true;
    }
    reg.count++;
    return reg.count <= LIMITE_REQ_POR_MIN;
}

setInterval(() => {
    const agora = Date.now();
    for (const [ip, reg] of rateLimit) {
        if (agora - reg.janela > 120000) rateLimit.delete(ip);
    }
}, 60000).unref();

// ==========================================
// 5. LOG (assíncrono, com write stream em append)
// ==========================================
const logStream = fs.createWriteStream(ARQUIVO_LOG, { flags: 'a' });
logStream.on('error', err => console.error(`[ERRO] Log stream: ${err.message}`));

function registrarLog(msg) {
    const data = new Date().toLocaleString('pt-BR');
    const linha = `[${data}] ${msg}`;
    console.log(linha);
    logStream.write(linha + '\n');
}

// ==========================================
// 6. INTEGRAÇÃO NGROK E N8N
// ==========================================
async function configurarDominio() {
    const ac = new AbortController();
    const t = setTimeout(() => ac.abort(), TIMEOUT_NGROK_MS);
    try {
        const response = await fetch('http://localhost:4040/api/tunnels', { signal: ac.signal });
        const data = await response.json();
        const urlPublica = data.tunnels[0].public_url;
        DOMINIO_BLOQUEADO = urlPublica.replace(/^https?:\/\//, '');
        registrarLog(`[SISTEMA] Alvo de bloqueio dinamico definido: ${DOMINIO_BLOQUEADO}`);
    } catch (e) {
        registrarLog(`[AVISO] Ngrok offline ou inacessivel. Firewall operando como 'localhost'.`);
        DOMINIO_BLOQUEADO = 'localhost';
    } finally {
        clearTimeout(t);
    }
}

function portaBorda() {
    const porta = Number(process.env.PAINEL_BORDA_PORT || 8502);
    return Number.isInteger(porta) && porta > 0 ? porta : 8502;
}

function chaveHmac() {
    return carregarChave(process.env.PORTEIRO_HMAC_KEY || '');
}

function ehCaminhoPainel(url) {
    const caminho = String(url || '').split('?')[0];
    return caminho === '/painel' || caminho.startsWith('/painel/');
}

function camposWebhook(registro) {
    const base = registro || {};
    return {
        dispositivo: base.dispositivo || '',
        origem: base.origem || '',
        origem_aprovada: base.origem_aprovada || '',
        navegador: base.navegador || '',
        sistema: base.sistema || '',
        idioma: base.idioma || '',
        horario: base.horario || new Date().toISOString(),
        pais: ''
    };
}

function acionarN8n(ipAlvo, conta, extra, cb) {
    if (typeof conta === 'function') {
        cb = conta;
        conta = '';
        extra = null;
    } else if (typeof extra === 'function') {
        cb = extra;
        extra = null;
    }
    const dados = camposWebhook(extra);
    dados.pais = '';
    if (!dados.horario) dados.horario = new Date().toISOString();
    const options = {
        hostname: '127.0.0.1',
        port: Number(process.env.N8N_LOCAL_PORT || 5678),
        path: caminhoWebhook(ipAlvo, conta || '', dados),
        method: 'GET'
    };
    let terminou = false;
    const fim = (err, status) => {
        if (terminou) return;
        terminou = true;
        if (cb) cb(err, status);
    };
    const reqN8n = http.request(options, (res) => {
        res.resume();
        registrarLog(`[SISTEMA] Interfone tocado para ${ipAlvo}. n8n respondeu: ${res.statusCode}`);
        fim(null, res.statusCode);
    });
    reqN8n.on('error', (err) => {
        registrarLog(`[ERRO] Falha ao acionar n8n: ${err.message}`);
        fim(err, 0);
    });
    reqN8n.setTimeout(5000, () => reqN8n.destroy());
    reqN8n.end();
}

function responderJson(res, status, corpo) {
    res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify(corpo));
}

function auditar(acao, ip, alvo, resultado) {
    try {
        anexarAuditoria({ usuario: 'admin', ip, acao, alvo, resultado });
    } catch (err) {
        registrarLog(`[ERRO] Auditoria: ${err.message}`);
    }
}

// ==========================================
// 7. MOTOR PRINCIPAL (SERVIDOR)
// ==========================================
async function atenderPedido(req, res) {
    if (req.url === '/favicon.ico') {
        res.writeHead(204);
        return res.end();
    }

    const ipSocket = obterIpSocket(req);
    const decisao = classificar(req);
    const caminhoAcessado = req.url;

    // Rate limit por IP de socket — antes de qualquer trabalho
    if (!dentroDoLimite(ipSocket)) {
        res.writeHead(429, { 'Content-Type': 'text/plain; charset=utf-8' });
        return res.end('Muitas requisicoes. Aguarde.');
    }

    // --------------------------------------------------
    // A. FIREWALL DE ROTA (ZERO TRUST)
    // --------------------------------------------------
    if (caminhoAcessado.startsWith('/webhook/solicitar-verificacao-acesso')) {
        registrarLog(`[FIREWALL] Bloqueio! IP ${decisao.ip || ipSocket} tentou acessar o gatilho interno via proxy.`);
        res.writeHead(403, { 'Content-Type': 'text/plain; charset=utf-8' });
        return res.end('Acesso Negado: Esta rota e bloqueada para acessos externos.');
    }

    // --------------------------------------------------
    // B. ROTAS ADMIN
    //    Token valido e obrigatorio, em qualquer porta e de qualquer IP.
    //    Rede local e ausencia de tunel sao condicoes a mais, nao substituem o token.
    //    No Docker Desktop, host.docker.internal na 5676 aparece como loopback.
    //    O bind 127.0.0.1 nao separa esse container. A protecao e o token.
    // --------------------------------------------------
    const pedido = new URL(caminhoAcessado, 'http://127.0.0.1');
    if (pedido.pathname.startsWith('/n8n/')) {
        const validos = tokensDeAprovacao();
        const apresentado = req.headers['x-admin-token'] || '';
        if (!tokenConfere(apresentado, validos)) {
            registrarLog(`[FIREWALL] Token de aprovacao ausente ou invalido (socket=${ipSocket} rota=${pedido.pathname}).`);
            res.writeHead(403, { 'Content-Type': 'text/plain; charset=utf-8' });
            return res.end(validos.length ? 'Token invalido.' : 'Token de aprovacao nao configurado.');
        }
        const rede = decidirAdmin(ipSocket, req.headers, DOMINIO_BLOQUEADO, ipsDoN8n());
        if (!rede.ok) {
            registrarLog(`[FIREWALL] ${rede.motivo} via=${rede.via} socket=${ipSocket} rota=${pedido.pathname}`);
            res.writeHead(403, { 'Content-Type': 'text/plain; charset=utf-8' });
            return res.end(rede.motivo);
        }
        return tratarAdmin(pedido, res);
    }

    // --------------------------------------------------
    // C. LÓGICA NORMAL DE VISITANTES
    // --------------------------------------------------
    if (decisao.acao === 'negar') {
        registrarLog(`[FIREWALL] ${decisao.motivo} socket=${ipSocket}`);
        res.writeHead(403, { 'Content-Type': 'text/plain; charset=utf-8' });
        return res.end(decisao.motivo || 'Acesso negado.');
    }
    if (decisao.acao === 'local') {
        if (ehCaminhoPainel(caminhoAcessado)) return fazerProxyPainel(req, res, '127.0.0.1');
        return fazerProxy(req, res);
    }

    const ipReal = decisao.ip;
    if (!ehFormatoIpValido(ipReal)) {
        res.writeHead(400);
        return res.end('IP de origem invalido.');
    }

    let registro = visitantes.get(ipReal);
    let urlVisita;
    try {
        urlVisita = new URL(caminhoAcessado, 'http://127.0.0.1');
    } catch (e) {
        urlVisita = new URL('http://127.0.0.1/');
    }
    if (urlVisita.pathname === '/origem.js' || urlVisita.pathname === '/painel/origem.js') {
        return servirOrigem(res);
    }
    if (urlVisita.pathname === '/painel/registrar-origem') {
        return registrarPeloVisitante(res, ipReal, urlVisita);
    }

    if (!registro) {
        if (visitantes.size >= MAX_VISITANTES) podarVisitantes();
        registro = criarPendente(ipReal, req.headers);
        visitantes.set(ipReal, registro);
        agendarPersist();
        registrarLog(`[NOVO] IP ${ipReal} registrado como PENDENTE.`);
        const foto = camposWebhook(registro);
        setImmediate(() => acionarN8n(ipReal, '', foto));
    } else {
        registro.tentativas = (registro.tentativas || 1) + 1;
        registro.ultima_vista = new Date().toISOString();
        agendarPersist();
    }

    if (registro.status === 'aprovado') {
        if (ehCaminhoPainel(caminhoAcessado)) return fazerProxyPainel(req, res, ipReal);
        return fazerProxy(req, res);
    } else if (registro.status === 'bloqueado') {
        res.writeHead(403, { 'Content-Type': 'text/plain; charset=utf-8' });
        return res.end('Acesso Negado Permanentemente.');
    } else {
        // Caminho antigo: esta resposta 202 não é a do túnel com Scout.
        res.writeHead(202, {
            'Content-Type': 'text/html; charset=utf-8',
            'Content-Security-Policy': CSP_ANALISE,
            'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer',
            'Cache-Control': 'no-store',
        });
        return res.end(paginaAnalise(ipReal));
    }
}

const server = http.createServer(atenderPedido);

// Porta que o tunel nao alcanca. O Scout encaminha so a 5677.
const adminServer = http.createServer((req, res) => {
    let caminho = '/';
    try {
        caminho = new URL(req.url || '/', 'http://127.0.0.1').pathname;
    } catch {
        caminho = '/';
    }
    if (!caminho.startsWith('/n8n/')) {
        res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
        return res.end('Esta porta so atende as rotas locais de aprovacao.');
    }
    return atenderPedido(req, res);
});

// ==========================================
// 8. PROXY REVERSO
// ==========================================
function cabecalhosPainel(req, ip) {
    const headers = Object.assign({}, req.headers);
    delete headers['x-n8groker-client'];
    headers['x-n8groker-client'] = assinarCliente(ip, chaveHmac());
    return headers;
}

function fazerProxy(req, res) {
    const proxyReq = http.request({
        hostname: '127.0.0.1',
        port: 5678,
        path: req.url,
        method: req.method,
        headers: req.headers
    }, (proxyRes) => {
        res.writeHead(proxyRes.statusCode, proxyRes.headers);
        proxyRes.pipe(res);
    });

    proxyReq.setTimeout(TIMEOUT_PROXY_MS, () => proxyReq.destroy(new Error('Timeout no proxy')));

    proxyReq.on('error', () => {
        if (!res.headersSent) res.writeHead(502).end('n8n offline ou reiniciando.');
    });

    req.pipe(proxyReq);
}

function fazerProxyPainel(req, res, ip) {
    const proxyReq = http.request({
        hostname: '127.0.0.1',
        port: portaBorda(),
        path: req.url,
        method: req.method,
        headers: cabecalhosPainel(req, ip)
    }, (proxyRes) => {
        res.writeHead(proxyRes.statusCode, proxyRes.headers);
        proxyRes.pipe(res);
    });
    proxyReq.setTimeout(TIMEOUT_PROXY_MS, () => proxyReq.destroy(new Error('Timeout no painel')));
    proxyReq.on('error', () => {
        if (!res.headersSent) res.writeHead(502).end('Painel de borda offline.');
    });
    req.pipe(proxyReq);
}

function tratarAdmin(pedido, res) {
    if (pedido.pathname === '/n8n/fila') {
        return responderJson(res, 200, { visitantes: Array.from(visitantes.values()) });
    }
    const ipAlvo = pedido.searchParams.get('ip') || '';
    const conta = pedido.searchParams.get('conta') || '';
    const origem = pedido.searchParams.get('origem') || '';
    if (!ehFormatoIpValido(ipAlvo)) {
        res.writeHead(400);
        return res.end('IP invalido.');
    }
    let registro = visitantes.get(ipAlvo);
    if (pedido.pathname === '/n8n/tocar') {
        const agora = new Date().toISOString();
        let criado = false;
        if (!registro) {
            if (visitantes.size >= MAX_VISITANTES) podarVisitantes();
            registro = criarPendente(ipAlvo, {});
            registro.navegador = pedido.searchParams.get('navegador') || '';
            registro.sistema = pedido.searchParams.get('sistema') || '';
            registro.idioma = pedido.searchParams.get('idioma') || '';
            visitantes.set(ipAlvo, registro);
            criado = true;
            registrarLog(`[NOVO] IP ${ipAlvo} registrado como PENDENTE.`);
        } else {
            registro.tentativas = (registro.tentativas || 1) + 1;
            registro.ultima_vista = agora;
        }
        agendarPersist();
        if (criado) {
            const foto = camposWebhook(registro);
            setImmediate(() => acionarN8n(ipAlvo, '', foto));
        }
        return responderJson(res, 200, { ok: true, visitante: registro });
    }
    if (pedido.pathname === '/n8n/registrar-origem') {
        if (!registro) {
            res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
            return res.end('Erro: O IP nao esta na fila de espera.');
        }
        const dispositivo = pedido.searchParams.get('dispositivo') || '';
        const anotado = anotarOrigemNoRegistro(registro, origem, dispositivo);
        agendarPersist();
        avisarOrigem(ipAlvo, registro, anotado, origem, dispositivo);
        return responderJson(res, 200, {
            ok: true,
            origem: origem,
            origem_aprovada: anotado.origem_aprovada,
            status: anotado.item.status,
        });
    }
    if (pedido.pathname === '/n8n/aprovar' || pedido.pathname === '/n8n/bloquear') {
        let resultado;
        if (pedido.pathname === '/n8n/aprovar') {
            resultado = origens.aprovar(registro, origem);
        } else if (origem) {
            resultado = origens.bloquearUma(registro, origem);
        } else {
            resultado = fila.bloquear(registro);
        }
        if (resultado.ok) {
            agendarPersist();
            registrarLog(`[COMANDO] IP ${ipAlvo} marcado como ${registro.status.toUpperCase()}`);
            auditar(pedido.pathname === '/n8n/aprovar' ? 'aprovar' : 'bloquear', ipAlvo, ipAlvo, 'ok');
        }
        res.writeHead(resultado.status, { 'Content-Type': 'text/plain; charset=utf-8' });
        return res.end(resultado.texto);
    }
    if (pedido.pathname === '/n8n/vincular') {
        if (!origem) {
            res.writeHead(400, { 'Content-Type': 'text/plain; charset=utf-8' });
            return res.end('Origem obrigatoria.');
        }
        const par = registro && Array.isArray(registro.origens)
            ? registro.origens.find((candidato) => candidato && candidato.origem === origem)
            : null;
        if (!par || par.status !== 'aprovado') {
            res.writeHead(409, { 'Content-Type': 'text/plain; charset=utf-8' });
            return res.end('O par IP e origem precisa estar aprovado antes do vinculo.');
        }
        const antes = registro ? registro.conta_vinculada : undefined;
        const resultado = fila.vincular(registro, conta);
        if (resultado.ok) {
            agendarPersist();
            registrarLog(`[COMANDO] IP ${ipAlvo} vinculado a ${conta}`);
            auditar('vincular', ipAlvo, conta, 'ok');
        }
        if (registro && antes !== undefined && !resultado.ok) registro.conta_vinculada = antes;
        res.writeHead(resultado.status, { 'Content-Type': 'text/plain; charset=utf-8' });
        return res.end(resultado.texto);
    }
    if (pedido.pathname === '/n8n/solicitar') {
        const resultado = fila.solicitar(registro, conta);
        if (!resultado.ok) {
            res.writeHead(resultado.status, { 'Content-Type': 'text/plain; charset=utf-8' });
            return res.end(resultado.texto);
        }
        agendarPersist();
        acionarN8n(ipAlvo, conta, camposWebhook(registro), (_err, status) => {
            auditar('solicitar', ipAlvo, conta, 'ok');
            responderJson(res, 200, { ok: true, n8n_status: status || 0, conta_solicitada: conta });
        });
        return;
    }
    res.writeHead(404);
    res.end('Rota admin desconhecida.');
}

server.on('upgrade', (req, socket, head) => {
    if (!ehCaminhoPainel(req.url || '')) {
        socket.destroy();
        return;
    }
    const decisao = classificar(req);
    let ip = '';
    if (decisao.acao === 'local') {
        ip = '127.0.0.1';
    } else if (decisao.acao === 'visitante') {
        const registro = visitantes.get(decisao.ip);
        if (!registro || registro.status !== 'aprovado') {
            socket.destroy();
            return;
        }
        ip = decisao.ip;
    } else {
        socket.destroy();
        return;
    }
    const proxy = http.request({
        hostname: '127.0.0.1',
        port: portaBorda(),
        path: req.url,
        method: 'GET',
        headers: cabecalhosPainel(req, ip)
    });
    proxy.on('upgrade', (proxyRes, proxySocket, proxyHead) => {
        const linhas = ['HTTP/1.1 101 Switching Protocols'];
        for (const [chave, valor] of Object.entries(proxyRes.headers)) {
            if (valor == null) continue;
            const texto = Array.isArray(valor) ? valor.join(', ') : String(valor);
            linhas.push(`${chave}: ${texto}`);
        }
        socket.write(linhas.join('\r\n') + '\r\n\r\n');
        if (proxyHead && proxyHead.length) socket.write(proxyHead);
        if (head && head.length) proxySocket.write(head);
        proxySocket.pipe(socket);
        socket.pipe(proxySocket);
    });
    proxy.on('response', (resposta) => {
        resposta.resume();
        socket.destroy();
    });
    proxy.on('error', () => socket.destroy());
    proxy.end();
});

// ==========================================
// 9. INICIALIZAÇÃO (BOOT)
// ==========================================
async function boot() {
    console.log('======================================');
    console.log('Iniciando sequencia de boot do Porteiro...');
    console.log(`[PORTEIRO] Dados: ${DATA_DIR}`);

    carregarBancoInicial();
    await configurarDominio();

    server.listen(PORTA_DO_PORTEIRO, '127.0.0.1', () => {
        console.log(`[PORTEIRO] Ativo em http://127.0.0.1:${PORTA_DO_PORTEIRO}`);
        console.log('======================================');
    });
    adminServer.listen(PORTA_ADMIN, '127.0.0.1', () => {
        console.log(`[PORTEIRO] Aprovacao local em http://127.0.0.1:${PORTA_ADMIN}`);
    });
}

// Shutdown limpo: garante que mudanças pendentes sejam gravadas.
for (const sig of ['SIGINT', 'SIGTERM']) {
    process.on(sig, async () => {
        registrarLog(`[SISTEMA] Recebido ${sig}, encerrando...`);
        try { await persistChain; } catch { /* ignorar */ }
        try { await persistirAgora(); } catch { /* ignorar */ }
        process.exit(0);
    });
}

if (require.main === module) {
    boot();
}

module.exports = { server, adminServer, classificar, visitantes, portaBorda, PORTA_ADMIN };
