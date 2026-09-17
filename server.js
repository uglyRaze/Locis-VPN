const express = require('express');
const sqlite3 = require('sqlite3').verbose();
const crypto = require('crypto');
const path = require('path');
const axios = require('axios');
const https = require('https');
const fs = require('fs');

process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0';

const app = express();
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// --- 1. CONFIGURATION ---
const MY_DB_PATH = path.join(__dirname, 'keys.db');
const MAX_DEVICES = process.env.MAX_DEVICES || 7;
const PROFILE_UPDATE_INTERVAL_HOURS = 2;

const XUI_CONFIG = {
    baseUrl: process.env.XUI_BASE_URL || 'https://127.0.0.1:54321',
    basePath: process.env.XUI_BASE_PATH || '/your_secret_path',
    username: process.env.XUI_USERNAME || 'admin',
    password: process.env.XUI_PASSWORD || 'securepassword'
};

const SERVER_DATA = {
    domain: process.env.SERVER_DOMAIN || 'example.com',
    ip: process.env.SERVER_IP || '127.0.0.1',
    port: parseInt(process.env.SERVER_PORT || '445'),
    sni: 'stream-1.example.com',
    pbk: 'PUBLIC_KEY_PLACEHOLDER',
    sid: 'SHORT_ID_PLACEHOLDER',
    flow: 'xtls-rprx-vision',
    name: '⚡️Locis VPN',
    inboundId: 1,
    telegramUrl: 'https://t.me/your_vpn_channel',
    displayServers: [
        { label: 'EU Central', flag: '🇪🇺' },
        { label: 'Germany', flag: '🇩🇪' },
        { label: 'Netherlands', flag: '🇳🇱' },
        { label: 'Poland', flag: '🇵🇱' },
        { label: 'France', flag: '🇫🇷' },
        { label: 'Belgium', flag: '🇧🇪' }
    ]
};

let xuiCookie = '';

const xuiClient = axios.create({
    httpsAgent: new https.Agent({ rejectUnauthorized: false }),
    timeout: 5000
});

// --- 2. DATABASE ---
const myDb = new sqlite3.Database(MY_DB_PATH);
myDb.serialize(() => {
    myDb.run(`CREATE TABLE IF NOT EXISTS keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT UNIQUE,
        uuid TEXT,
        devices TEXT DEFAULT '[]',
        expiry INTEGER
    )`);
});

// --- CRON: DAILY DEVICE RESET ---
function dailyResetDevices() {
    myDb.run("UPDATE keys SET devices = '[]'", (err) => {
        if (err) {
            console.error(`[CRON] Error resetting devices: ${err.message}`);
        } else {
            console.log('[CRON] Device list reset successfully.');
        }
    });
}

setInterval(() => {
    const now = new Date();
    if (now.getHours() === 3 && now.getMinutes() === 0) {
        dailyResetDevices();
    }
}, 60000);

// --- 3. X-UI SERVICE ENGINE ---
async function loginToXUI() {
    try {
        const params = new URLSearchParams();
        params.append('username', XUI_CONFIG.username);
        params.append('password', XUI_CONFIG.password);
        const res = await xuiClient.post(`${XUI_CONFIG.baseUrl}${XUI_CONFIG.basePath}/login`, params);
        if (res.data?.success && res.headers['set-cookie']) {
            xuiCookie = res.headers['set-cookie'][0].split(';')[0];
            return true;
        }
    } catch (e) { console.error("XUI Auth Failure"); }
    return false;
}

async function addClient(uuid, email, expiry) {
    if (!xuiCookie) await loginToXUI();
    try {
        const res = await xuiClient.post(`${XUI_CONFIG.baseUrl}${XUI_CONFIG.basePath}/panel/api/inbounds/addClient`, {
            id: SERVER_DATA.inboundId,
            settings: JSON.stringify({
                clients: [{ id: uuid, flow: SERVER_DATA.flow, email: email, expiryTime: expiry, enable: true }]
            })
        }, { headers: { 'Cookie': xuiCookie } });
        return res.data?.success;
    } catch (e) { return false; }
}

async function updateClientInXUI(uuid, email, newExpiry) {
    if (!xuiCookie) await loginToXUI();
    try {
        const res = await xuiClient.post(`${XUI_CONFIG.baseUrl}${XUI_CONFIG.basePath}/panel/api/inbounds/updateClient/${uuid}`, {
            id: SERVER_DATA.inboundId,
            settings: JSON.stringify({
                clients: [{ id: uuid, flow: SERVER_DATA.flow, email: email, expiryTime: newExpiry, enable: true }]
            })
        }, { headers: { 'Cookie': xuiCookie } });
        return res.data?.success;
    } catch (e) { return false; }
}

// --- 4. DEVICE LIMIT CONTROL ---
function handleDeviceCheck(row, incomingId) {
    let devices = [];
    try {
        devices = JSON.parse(row.devices || '[]');
    } catch (e) { devices = []; }

    if (devices.includes(incomingId)) {
        return { allowed: true, newList: devices };
    }

    if (devices.length >= MAX_DEVICES) {
        return { allowed: false };
    }

    devices.push(incomingId);
    return { allowed: true, newList: devices };
}

// --- 5. ENDPOINTS ---
app.post('/create-key', async (req, res) => {
    const days = parseInt(req.body?.days) || 30;
    const newKey = crypto.randomBytes(16).toString('hex');
    const newUuid = crypto.randomUUID();
    const expiry = Date.now() + (days * 86400000);
    const email = `locis_${newKey.slice(0, 4)}`;

    const ok = await addClient(newUuid, email, expiry);
    if (!ok) return res.status(500).json({ ok: false });

    myDb.run("INSERT INTO keys (key, uuid, expiry) VALUES (?, ?, ?)", [newKey, newUuid, expiry], (err) => {
        if (err) return res.status(500).json({ ok: false });
        res.json({ ok: true, key: newKey, sub_link: `https://${SERVER_DATA.domain}:3001/sub/${newKey}` });
    });
});

app.post('/extend-subscription', (req, res) => {
    const { key, days } = req.body;
    if (!key || !days) return res.status(400).json({ ok: false, error: "Missing parameters" });

    myDb.get("SELECT * FROM keys WHERE key = ?", [key], async (err, row) => {
        if (err || !row) return res.status(404).json({ ok: false, error: "Key not found" });

        const currentExpiry = row.expiry || Date.now();
        const baseTime = currentExpiry > Date.now() ? currentExpiry : Date.now();
        const newExpiry = baseTime + (days * 86400000);

        const email = `locis_${key.slice(0, 4)}`;
        const ok = await updateClientInXUI(row.uuid, email, newExpiry);
        if (!ok) return res.status(500).json({ ok: false, error: "Failed to update in X-UI" });

        myDb.run("UPDATE keys SET expiry = ? WHERE key = ?", [newExpiry, key], (err) => {
            if (err) return res.status(500).json({ ok: false, error: "Database error" });
            res.json({ ok: true, new_expiry: newExpiry });
        });
    });
});

app.get('/sub/:key', (req, res) => {
    const key = req.params.key;
    const hwid = req.headers['x-hwid'];
    const ip = req.headers['x-forwarded-for'] || req.socket.remoteAddress || '0.0.0.0';
    const ua = req.headers['user-agent'] || 'no-ua';
    const fingerprint = hwid || crypto.createHash('md5').update(ip + ua).digest('hex').slice(0, 12);

    myDb.get("SELECT * FROM keys WHERE key = ?", [key], (err, row) => {
        if (!row || Date.now() > row.expiry) return res.status(404).send("Not Found");

        const result = handleDeviceCheck(row, fingerprint);

        if (!result.allowed) {
            return res.status(429).send(`<h1>Limit Reached</h1><p>${MAX_DEVICES} devices maximum.</p>`);
        }

        myDb.run("UPDATE keys SET devices = ? WHERE key = ?", [JSON.stringify(result.newList), key]);

        const daysLeft = Math.max(0, Math.ceil((row.expiry - Date.now()) / 86400000));
        const profileTitleText = `${SERVER_DATA.name} - ${daysLeft} Days Left`;
        const announceText = `⛔️ Connection issue? Tap Refresh\n\n⏱️ Active Subscription`;

        const vlessLinks = SERVER_DATA.displayServers.map(s =>
            `vless://${row.uuid}@${SERVER_DATA.domain}:${SERVER_DATA.port}?type=tcp&security=reality&sni=${SERVER_DATA.sni}&fp=qq&pbk=${SERVER_DATA.pbk}&sid=${SERVER_DATA.sid}&flow=${SERVER_DATA.flow}&encryption=none#${encodeURIComponent(s.flag + ' ' + s.label)}`
        ).join('\n');

        res.setHeader('Content-Type', 'text/plain; charset=utf-8');
        res.setHeader('Profile-Title', 'base64:' + Buffer.from(profileTitleText, 'utf-8').toString('base64'));
        res.setHeader('Profile-Update-Interval', PROFILE_UPDATE_INTERVAL_HOURS);
        res.setHeader('Support-Url', SERVER_DATA.telegramUrl);
        res.setHeader('Announce', 'base64:' + Buffer.from(announceText, 'utf-8').toString('base64'));
        res.setHeader('Subscription-Userinfo', `expire=${Math.floor(row.expiry / 1000)}`);

        res.send(Buffer.from(`${vlessLinks}\n`).toString('base64'));
    });
});

// --- 6. SERVER BOOTSTRAP ---
const sslOptions = {
    cert: fs.readFileSync(process.env.SSL_CERT_PATH || './certs/fullchain.pem'),
    key: fs.readFileSync(process.env.SSL_KEY_PATH || './certs/privkey.pem')
};

https.createServer(sslOptions, app).listen(3001, '0.0.0.0', () => {
    console.log(`🚀 API Server active on port 3001`);
    loginToXUI();
});