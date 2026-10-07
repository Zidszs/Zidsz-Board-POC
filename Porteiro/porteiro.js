const http = require('http');
const fs = require('fs');
const path = require('path');

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

// Token compartilhado entre n8n e porteiro para rotas admin.
// Defina em ambiente: PORTEIRO_TOKEN=algumacoisalonga
const TOKEN_ADMIN = process.env.PORTEIRO_TOKEN || '';

// Limites de proteção contra DoS / poluição de banco
const MAX_VISITANTES = 1000;          // teto total no banco
const LIMITE_REQ_POR_MIN = 60;        // por IP de socket
const TIMEOUT_PROXY_MS = 30000;
const TIMEOUT_NGROK_MS = 3000;

let DOMINIO_BLOQUEADO = 'localhost';

if (!fs.existsSync(DATA_DIR)) {
    fs.mkdirSync(DATA_DIR, { recursive: true });
}

if (!TOKEN_ADMIN) {
    console.warn('[AVISO] PORTEIRO_TOKEN nao definido — rotas admin protegidas apenas pelo IP do socket.');
}

// ==========================================
// 2. UTILITÁRIOS DE IP E ESCAPE
// ==========================================
function normalizarIp(ip) {
    if (!ip) return '';
    // Node devolve "::ffff:127.0.0.1" em sockets IPv6 com cliente IPv4
    return ip.startsWith('::ffff:') ? ip.slice(7) : ip;
}

function ehLocalhost(ip) {
    const n = normalizarIp(ip);
    return n === '127.0.0.1' || n === '::1';
}

function ehDockerInterno(ip) {
    // Range padrão do Docker: 172.16.0.0/12
    const n = normalizarIp(ip);
    const m = n.match(/^172\.(\d+)\./);
    if (!m) return false;
    const b = +m[1];
    return b >= 16 && b <= 31;
}

function ehImune(ip) {
    return ehLocalhost(ip) || ehDockerInterno(ip);
}

function ehFormatoIpValido(ip) {
    if (!ip || ip.length > 45) return false;
    return /^[0-9a-fA-F:.]+$/.test(ip);
}

function escHtml(s) {
    return String(s).replace(/[&<>"']/g, c =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// Socket é a verdade: vem da pilha TCP do Node, não é falsificável pelo cliente.
function obterIpSocket(req) {
    return normalizarIp(req.socket.remoteAddress || '');
}

// X-Forwarded-For só é confiável se a conexão TCP veio de um proxy local
// (ex.: agente ngrok rodando em 127.0.0.1). Caso contrário, ignora o header.
function obterIpReal(req) {
    const socket = obterIpSocket(req);
    if (ehLocalhost(socket)) {
        const xff = req.headers['x-forwarded-for'];
        if (xff) {
            const primeiro = xff.split(',')[0].trim();
            if (primeiro) return normalizarIp(primeiro);
        }
    }
    return socket;
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

function acionarN8n(ipAlvo) {
    const options = {
        hostname: '127.0.0.1',
        port: 5678,
        path: '/webhook/solicitar-verificacao-acesso?ip=' + encodeURIComponent(ipAlvo),
        method: 'GET'
    };
    const reqN8n = http.request(options, (res) => {
        registrarLog(`[SISTEMA] Interfone tocado para ${ipAlvo}. n8n respondeu: ${res.statusCode}`);
    });
    reqN8n.on('error', (err) => {
        registrarLog(`[ERRO] Falha ao acionar n8n: ${err.message}`);
    });
    reqN8n.setTimeout(5000, () => reqN8n.destroy());
    reqN8n.end();
}

// ==========================================
// 7. MOTOR PRINCIPAL (SERVIDOR)
// ==========================================
const server = http.createServer(async (req, res) => {
    if (req.url === '/favicon.ico') {
        res.writeHead(204);
        return res.end();
    }

    const ipSocket = obterIpSocket(req);
    const ipReal = obterIpReal(req);
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
        registrarLog(`[FIREWALL] Bloqueio! IP ${ipReal} tentou acessar o gatilho interno via proxy.`);
        res.writeHead(403, { 'Content-Type': 'text/plain; charset=utf-8' });
        return res.end('Acesso Negado: Esta rota e bloqueada para acessos externos.');
    }

    // --------------------------------------------------
    // B. ROTAS ADMIN (comandos do n8n)
    //    Usa SOMENTE ipSocket (não falsificável) + token compartilhado.
    // --------------------------------------------------
    if (caminhoAcessado.startsWith('/n8n/aprovar?ip=') || caminhoAcessado.startsWith('/n8n/bloquear?ip=')) {
        if (!ehImune(ipSocket)) {
            registrarLog(`[FIREWALL] Tentativa admin de fora: socket=${ipSocket} ipReal=${ipReal} rota=${caminhoAcessado}`);
            res.writeHead(403);
            return res.end('Acesso Negado.');
        }
        if (TOKEN_ADMIN && req.headers['x-admin-token'] !== TOKEN_ADMIN) {
            registrarLog(`[FIREWALL] Token admin invalido (socket=${ipSocket}).`);
            res.writeHead(403);
            return res.end('Token invalido.');
        }

        const [rota, qs] = caminhoAcessado.split('?ip=');
        const acao = rota === '/n8n/aprovar' ? 'aprovado' : 'bloqueado';
        const ipAlvo = decodeURIComponent(qs || '');

        if (!ehFormatoIpValido(ipAlvo)) {
            res.writeHead(400);
            return res.end('IP invalido.');
        }

        const registro = visitantes.get(ipAlvo);
        if (registro) {
            registro.status = acao;
            agendarPersist();
            registrarLog(`[COMANDO] n8n marcou o IP ${ipAlvo} como ${acao.toUpperCase()}`);
            res.writeHead(200);
            return res.end(`Sucesso! O IP ${ipAlvo} agora esta ${acao}.`);
        } else {
            res.writeHead(404);
            return res.end(`Erro: O IP ${ipAlvo} nao esta na fila de espera.`);
        }
    }

    // --------------------------------------------------
    // C. LÓGICA NORMAL DE VISITANTES
    // --------------------------------------------------
    if (ehImune(ipReal)) {
        return fazerProxy(req, res);
    }

    if (!ehFormatoIpValido(ipReal)) {
        res.writeHead(400);
        return res.end('IP de origem invalido.');
    }

    let registro = visitantes.get(ipReal);

    if (!registro) {
        if (visitantes.size >= MAX_VISITANTES) podarVisitantes();
        registro = {
            ip: ipReal,
            status: 'pendente',
            data_primeiro_acesso: new Date().toISOString(),
            tentativas: 1
        };
        visitantes.set(ipReal, registro);
        agendarPersist();
        registrarLog(`[NOVO] IP ${ipReal} registrado como PENDENTE.`);
        // Dispara o n8n sem segurar a resposta do visitante
        setImmediate(() => acionarN8n(ipReal));
    } else {
        registro.tentativas = (registro.tentativas || 1) + 1;
        agendarPersist();
    }

    if (registro.status === 'aprovado') {
        return fazerProxy(req, res);
    } else if (registro.status === 'bloqueado') {
        res.writeHead(403, { 'Content-Type': 'text/plain; charset=utf-8' });
        return res.end('Acesso Negado Permanentemente.');
    } else {
        res.writeHead(202, { 'Content-Type': 'text/html; charset=utf-8' });
        return res.end(`
            <html><body style="font-family:sans-serif; text-align:center; padding:50px; background-color:#1e1e1e; color:#fff;">
                <h2>Acesso em Analise 🛡️</h2>
                <p>Seu IP (<b>${escHtml(ipReal)}</b>) foi enviado para aprovacao do administrador.</p>
                <p>Por favor, aguarde alguns instantes. Esta pagina atualiza sozinha.</p>
                <script>setTimeout(() => { location.reload(); }, 5000);</script>
            </body></html>
        `);
    }
});

// ==========================================
// 8. PROXY REVERSO
// ==========================================
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

// ==========================================
// 9. INICIALIZAÇÃO (BOOT)
// ==========================================
async function boot() {
    console.log('======================================');
    console.log('Iniciando sequencia de boot do Porteiro...');
    console.log(`[PORTEIRO] Dados: ${DATA_DIR}`);

    carregarBancoInicial();
    await configurarDominio();

    server.listen(PORTA_DO_PORTEIRO, '0.0.0.0', () => {
        console.log(`[PORTEIRO] Ativo na porta ${PORTA_DO_PORTEIRO}`);
        console.log('======================================');
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

boot();
