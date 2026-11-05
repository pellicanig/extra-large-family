# 📌 CODICE JAVASCRIPT SERVICE WORKER E CLIENT WEB PER ELF
SW_PUSH_HANDLERS_JS = r"""
const SW_ICON_CACHE = "elf-push-icons-v1";

async function aggiornaContatoreNonLette(customBadge) {
    if (!self.registration || !self.registration.getNotifications) return;
    try {
        const list = await self.registration.getNotifications();
        const unreadCount = (typeof customBadge === "number" && customBadge > list.length) ? customBadge : list.length;
        if (self.navigator && "setAppBadge" in self.navigator) {
            if (unreadCount > 0) await self.navigator.setAppBadge(unreadCount);
            else await self.navigator.clearAppBadge();
        }
    } catch (e) {}
}

async function inviaAckSingolo(notifKey, epHash, did, tag) {
    const qs = "?notif_key=" + encodeURIComponent(notifKey || "")
             + "&ep_hash=" + encodeURIComponent(epHash || "")
             + "&did=" + encodeURIComponent(did || "")
             + "&tag=" + encodeURIComponent(tag || "");
    try {
        const r = await fetch("/api/notifiche/ack" + qs, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                notif_key: notifKey || "",
                ep_hash: epHash || "",
                did: did || "",
                tag: tag || ""
            }),
            keepalive: true
        });
        if (r && r.ok) return true;
    } catch (e) {}
    try {
        const r2 = await fetch("/api/notifiche/ack" + qs, { method: "GET", cache: "no-store", keepalive: true });
        if (r2 && r2.ok) return true;
    } catch (e) {}
    return false;
}

async function confermaRicezionePush(notifKey, epHash, did, tag) {
    if (!notifKey && !tag) return;
    let ok = await inviaAckSingolo(notifKey, epHash, did, tag);
    if (!ok) {
        await new Promise((r) => setTimeout(r, 1500));
        ok = await inviaAckSingolo(notifKey, epHash, did, tag);
    }
    if (!ok && "caches" in self) {
        try {
            const c = await caches.open(SW_ICON_CACHE);
            await c.put(
                new Request("/__pending_ack__/" + encodeURIComponent(notifKey || tag)),
                new Response(JSON.stringify({ notif_key: notifKey || "", ep_hash: epHash || "", did: did || "", tag: tag || "" }))
            );
            if (self.registration && "sync" in self.registration) {
                await self.registration.sync.register("elf-flush-ack");
            }
        } catch (e) {}
    }
}

async function svuotaAckPendenti() {
    if (!("caches" in self)) return;
    try {
        const c = await caches.open(SW_ICON_CACHE);
        const keys = await c.keys();
        for (const req of keys) {
            if (req.url.includes("/__pending_ack__/")) {
                const resp = await c.match(req);
                if (resp) {
                    const d = await resp.json();
                    const inviato = await inviaAckSingolo(d.notif_key, d.ep_hash, d.did, d.tag);
                    if (inviato) await c.delete(req);
                }
            }
        }
    } catch (e) {}
}

self.addEventListener("activate", (event) => {
    event.waitUntil((async () => {
        try {
            if ("caches" in self) {
                const keys = await caches.keys();
                for (const k of keys) {
                    if (k !== SW_ICON_CACHE) {
                        const c = await caches.open(k);
                        await c.delete("/");
                        await c.delete("/index.html");
                    }
                }
                const iconCache = await caches.open(SW_ICON_CACHE);
                try { await iconCache.add("/icons/Icon-192.png"); } catch (e) {}
            }
        } catch (e) {}
    })());
});

self.addEventListener("sync", (event) => {
    if (event.tag === "elf-flush-ack") {
        event.waitUntil(svuotaAckPendenti());
    }
});

self.addEventListener("pushsubscriptionchange", (event) => {
    event.waitUntil((async () => {
        try {
            const sub = await self.registration.pushManager.subscribe(
                event.oldSubscription ? event.oldSubscription.options : { userVisibleOnly: true }
            );
            await fetch("/api/notifiche/subscribe", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ sub: sub, silent_sync: true }),
                keepalive: true
            });
        } catch (e) {}
    })());
});

self.addEventListener("push", (event) => {
    let data = {};
    if (event.data) {
        try { data = event.data.json(); } catch (e) { data = { title: "⏰ Promemoria ELF", body: event.data.text() }; }
    }

    if (Array.isArray(data.close_tags) && data.close_tags.length > 0) {
        const closeWork = async () => {
            if (self.registration && self.registration.getNotifications) {
                const list = await self.registration.getNotifications();
                for (const n of list) {
                    if (n.tag && data.close_tags.includes(n.tag)) n.close();
                }
            }
            await aggiornaContatoreNonLette();
        };
        event.waitUntil(closeWork());
        return;
    }

    const alertTag = data.tag || ("elf_notif_" + Date.now());
    const notifKey = data.notif_key || "";
    const epHash = data.ep_hash || "";
    const did = data.device_id || "";
    const titolo = data.title || "⏰ Promemoria Impegno ELF";

    const showWork = async () => {
        let stessaNotificaGiaVisibile = false;
        try {
            if (self.registration && self.registration.getNotifications) {
                const tutte = await self.registration.getNotifications();
                for (const n of (tutte || [])) {
                    const nData = n.data || {};
                    if ((notifKey && nData.notif_key === notifKey) || (n.tag && n.tag === alertTag)) {
                        stessaNotificaGiaVisibile = true;
                    }
                }
            }
        } catch (e) {}

        const options = {
            body: data.body || "Controlla il calendario ELF.",
            icon: "/icons/Icon-192.png",
            badge: "/icons/Icon-192.png",
            vibrate: stessaNotificaGiaVisibile ? [] : [400, 150, 400, 150, 600],
            tag: alertTag,
            silent: stessaNotificaGiaVisibile,
            renotify: !stessaNotificaGiaVisibile,
            requireInteraction: true,
            timestamp: Date.now(),
            data: { url: "/", notif_key: notifKey, ep_hash: epHash, did: did }
        };

        try {
            await self.registration.showNotification(titolo, options);
        } catch (errNotif) {
            await self.registration.showNotification(titolo, {
                body: data.body || "Controlla il calendario ELF.",
                tag: alertTag,
                requireInteraction: true,
                data: { url: "/", notif_key: notifKey, ep_hash: epHash, did: did }
            });
        }

        await confermaRicezionePush(notifKey, epHash, did, alertTag);
        await svuotaAckPendenti();
        await aggiornaContatoreNonLette(data.badge_count);
    };

    event.waitUntil(showWork());
});

self.addEventListener("notificationclick", (event) => {
    const nData = (event.notification && event.notification.data) || {};
    event.notification.close();
    event.waitUntil(
        Promise.all([
            confermaRicezionePush(nData.notif_key || "", nData.ep_hash || "", nData.did || "", event.notification.tag || ""),
            svuotaAckPendenti(),
            aggiornaContatoreNonLette(),
            self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
                for (const client of clientList) {
                    if ("focus" in client) return client.focus();
                }
                if (self.clients.openWindow) return self.clients.openWindow("/");
            })
        ])
    );
});

self.addEventListener("notificationclose", (event) => {
    event.waitUntil(aggiornaContatoreNonLette());
});
"""

SW_JS_CODE = r"""
self.addEventListener("install", (e) => {
    self.skipWaiting();
    e.waitUntil((async () => {
        try {
            if ("caches" in self) {
                const c = await caches.open("elf-push-icons-v1");
                await c.add("/icons/Icon-192.png");
            }
        } catch (err) {}
    })());
});
self.addEventListener("activate", (e) => { e.waitUntil(self.clients.claim()); });
""" + SW_PUSH_HANDLERS_JS

CLIENT_JS_CODE = r"""
(function() {
    if (window.__elfNotifInit) return;
    window.__elfNotifInit = true;

    let cachedEndpoint = "";
    let syncInCorso = false;
    let ultimoTokenSincronizzato = "";
    const ackInviatiSessione = new Set();

    function generaIdHardwareStabile() {
        try {
            const ua = (navigator.userAgent || "").replace(/Chrome\/[\d.]+/g, "Chrome").replace(/Version\/[\d.]+/g, "");
            const raw = [
                navigator.platform || "",
                screen.width + "x" + screen.height,
                window.devicePixelRatio || 1,
                navigator.hardwareConcurrency || 0,
                navigator.maxTouchPoints || 0,
                ua
            ].join("|");
            let h1 = 0xdeadbeef, h2 = 0x41c6ce57;
            for (let i = 0; i < raw.length; i++) {
                const ch = raw.charCodeAt(i);
                h1 = Math.imul(h1 ^ ch, 2654435761);
                h2 = Math.imul(h2 ^ ch, 1597334677);
            }
            h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
            h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
            return "dev_" + (h2 >>> 0).toString(36) + "_" + (h1 >>> 0).toString(36);
        } catch (e) {
            return "dev_" + Math.random().toString(36).substring(2, 10);
        }
    }

    function leggiCookie(nome) {
        try {
            const m = document.cookie.match(new RegExp("(^| )" + nome + "=([^;]+)"));
            return m ? decodeURIComponent(m[2]) : "";
        } catch (e) { return ""; }
    }

    function getDeviceId() {
        let did = "";
        try {
            did = localStorage.getItem("elf_device_id") || leggiCookie("elf_did") || "";
            if (!did || did.split("_").length < 4) {
                did = generaIdHardwareStabile() + "_" + Math.random().toString(36).substring(2, 7);
            }
            localStorage.setItem("elf_device_id", did);
            document.cookie = "elf_did=" + encodeURIComponent(did) + "; path=/; max-age=31536000; SameSite=Lax";
        } catch (e) {}
        return did || (generaIdHardwareStabile() + "_" + Math.random().toString(36).substring(2, 7));
    }

    function isStandalonePWA() {
        try {
            return (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) || (window.navigator && window.navigator.standalone === true);
        } catch (e) { return false; }
    }

    function urlBase64ToUint8Array(base64String) {
        const padding = "=".repeat((4 - base64String.length % 4) % 4);
        const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
        const rawData = window.atob(base64);
        return Uint8Array.from([...rawData].map((c) => c.charCodeAt(0)));
    }

    async function richiedePersistenzaStorage() {
        try {
            if (navigator.storage && navigator.storage.persisted) {
                let p = await navigator.storage.persisted();
                if (!p && navigator.storage.persist) p = await navigator.storage.persist();
                return p;
            }
        } catch (e) {}
        return false;
    }

    async function getDedicatedSwReg() {
        try {
            const regs = await navigator.serviceWorker.getRegistrations();
            for (const r of regs) {
                if (r.scope && r.scope.includes("/api/notifiche")) await r.unregister();
            }
        } catch (e) {}
        const reg = await navigator.serviceWorker.register("/sw-notifiche.js", {
            scope: "/elf-push-sw/",
            updateViaCache: "none"
        });
        try { await reg.update(); } catch (e) {}
        let sw = reg.installing || reg.waiting || reg.active;
        if (sw && sw.state !== "activated") {
            await new Promise((resolve) => {
                const timer = setTimeout(resolve, 2500);
                sw.addEventListener("statechange", () => {
                    if (sw.state === "activated") { clearTimeout(timer); resolve(); }
                });
            });
        }
        return reg;
    }

    function arrayBufferToBase64Url(buffer) {
        if (!buffer) return "";
        try {
            const bytes = new Uint8Array(buffer);
            let binary = "";
            for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
            return window.btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
        } catch (e) {
            return "";
        }
    }

    function estraiChiaviSub(subObj) {
        if (!subObj) return { endpoint: "", p256dh: "", auth: "" };
        try {
            const j = subObj.toJSON ? subObj.toJSON() : subObj;
            const k = (j && j.keys) || {};
            return {
                endpoint: (j && j.endpoint) || subObj.endpoint || "",
                p256dh: k.p256dh || "",
                auth: k.auth || ""
            };
        } catch (e) {
            return { endpoint: subObj.endpoint || "", p256dh: "", auth: "" };
        }
    }

    async function allineaBadgeEAckTendina(reg, epHash, did, endpointUrl, pendingKeys) {
        try {
            const setPending = new Set(Array.isArray(pendingKeys) ? pendingKeys : []);
            if (reg && reg.getNotifications) {
                const list = await reg.getNotifications();
                for (const n of list) {
                    const nData = n.data || {};
                    const nKey = nData.notif_key || "";
                    const nTag = n.tag || "";
                    const idUnivoco = nKey || nTag;
                    if (!idUnivoco) continue;
                    if (ackInviatiSessione.has(idUnivoco) && !setPending.has(nKey)) continue;
                    ackInviatiSessione.add(idUnivoco);

                    const qs = "?notif_key=" + encodeURIComponent(nKey)
                             + "&ep_hash=" + encodeURIComponent(nData.ep_hash || epHash || "")
                             + "&did=" + encodeURIComponent(nData.did || did || "")
                             + "&tag=" + encodeURIComponent(nTag);
                    fetch("/api/notifiche/ack" + qs, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            notif_key: nKey,
                            ep_hash: nData.ep_hash || epHash || "",
                            did: nData.did || did || "",
                            endpoint: endpointUrl || "",
                            tag: nTag
                        })
                    }).catch(() => { ackInviatiSessione.delete(idUnivoco); });
                }
                if ("setAppBadge" in navigator) {
                    if (list.length > 0) await navigator.setAppBadge(list.length);
                    else await navigator.clearAppBadge();
                }
            }
        } catch (e) {}
    }

    async function sincronizzaDispositivo() {
        if (document.hidden || syncInCorso) return;
        syncInCorso = true;
        try {
            const did = getDeviceId();
            const hwId = generaIdHardwareStabile();
            const rawPerm = ("Notification" in window) ? Notification.permission : "denied";
            const permGranted = (rawPerm === "granted");
            let mutedLocal = false;
            let osCfgLocal = "0";
            try {
                mutedLocal = (localStorage.getItem("elf_muted") === "1");
                osCfgLocal = (localStorage.getItem("elf_os_configured") === "1") ? "1" : "0";
            } catch (e) {}

            let reg = null;
            let sub = null;
            let isPersisted = false;
            let chiaviSub = { endpoint: "", p256dh: "", auth: "" };

            if ("serviceWorker" in navigator && "PushManager" in window && permGranted && !mutedLocal) {
                isPersisted = await richiedePersistenzaStorage();
                reg = await getDedicatedSwReg();
                sub = await reg.pushManager.getSubscription();
                chiaviSub = estraiChiaviSub(sub);
                cachedEndpoint = chiaviSub.endpoint;
            } else {
                cachedEndpoint = "";
                document.cookie = "elf_ep_h=; path=/; max-age=0; SameSite=Lax";
            }

            const permParam = (permGranted && !mutedLocal) ? "1" : (rawPerm === "denied" ? "denied" : "0");
            const standaloneParam = isStandalonePWA() ? "1" : "0";
            const persistedParam = isPersisted ? "1" : "0";

            const r = await fetch(
                "/api/notifiche/status?did=" + encodeURIComponent(did)
                + "&hw_id=" + encodeURIComponent(hwId)
                + "&endpoint=" + encodeURIComponent(chiaviSub.endpoint)
                + "&p256dh=" + encodeURIComponent(chiaviSub.p256dh)
                + "&auth=" + encodeURIComponent(chiaviSub.auth)
                + "&perm=" + encodeURIComponent(permParam)
                + "&standalone=" + standaloneParam
                + "&persisted=" + persistedParam
                + "&os_cfg=" + osCfgLocal,
                { cache: "no-store" }
            );
            const cfg = await r.json();

            if (cfg.unmute_cmd) {
                try { localStorage.removeItem("elf_muted"); } catch (e) {}
                mutedLocal = false;
            }

            if (cfg.force_unsub) {
                try { localStorage.setItem("elf_muted", "1"); } catch (e) {}
                mutedLocal = true;
            }

            if (mutedLocal) {
                document.cookie = "elf_ep_h=; path=/; max-age=0; SameSite=Lax";
                if (reg && sub) {
                    try { await sub.unsubscribe(); } catch (e) {}
                    sub = null;
                    cachedEndpoint = "";
                }
                if (reg && reg.getNotifications) {
                    const ns = await reg.getNotifications();
                    ns.forEach((n) => n.close());
                }
                if ("clearAppBadge" in navigator) navigator.clearAppBadge().catch(()=>{});
                return;
            }

            // Verifica se il token locale appartiene a una vecchia chiave VAPID o se il server ha richiesto il rinnovo
            let vapidMismatch = false;
            if (sub && cfg.vapid_public && sub.options && sub.options.applicationServerKey) {
                const subKeyB64 = arrayBufferToBase64Url(sub.options.applicationServerKey);
                const serverKeyB64 = cfg.vapid_public.replace(/=+$/, "");
                if (subKeyB64 && subKeyB64 !== serverKeyB64) {
                    vapidMismatch = true;
                }
            }

            if (permGranted && !mutedLocal && reg && cfg.vapid_public) {
                if (vapidMismatch) {
                    try {
                        if (sub) await sub.unsubscribe();
                    } catch (eUnsub) {}
                    sub = null;
                    chiaviSub = { endpoint: "", p256dh: "", auth: "" };
                    ultimoTokenSincronizzato = "";
                } else if (cfg.forza_rinnovo_token) {
                    ultimoTokenSincronizzato = "";
                }

                if (!sub || cfg.token_disallineato || !cfg.dispositivo_attivo) {
                    if (!sub) {
                        try {
                            sub = await reg.pushManager.subscribe({
                                userVisibleOnly: true,
                                applicationServerKey: urlBase64ToUint8Array(cfg.vapid_public)
                            });
                            chiaviSub = estraiChiaviSub(sub);
                            cachedEndpoint = chiaviSub.endpoint;
                        } catch (eSub) {}
                    }
                    if (sub && chiaviSub.endpoint) {
                        const improntaSync = did + "|" + hwId + "|" + chiaviSub.endpoint + "|" + chiaviSub.p256dh + "|" + chiaviSub.auth;
                        if (ultimoTokenSincronizzato !== improntaSync || cfg.token_disallineato || !cfg.dispositivo_attivo) {
                            const resSync = await fetch("/api/notifiche/subscribe", {
                                method: "POST",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify({
                                    sub: sub,
                                    device_id: did,
                                    hw_id: hwId,
                                    send_test: false
                                })
                            });
                            if (resSync && resSync.ok) {
                                const outSync = await resSync.json();
                                ultimoTokenSincronizzato = improntaSync;
                                cfg.dispositivo_attivo = true;
                                if (outSync.ep_hash) cfg.ep_hash = outSync.ep_hash;
                            }
                        }
                    }
                }
            }

            await allineaBadgeEAckTendina(reg, cfg.ep_hash, did, cachedEndpoint, cfg.pending_ack_keys);

            if (!cfg.dispositivo_attivo) {
                document.cookie = "elf_ep_h=; path=/; max-age=0; SameSite=Lax";
            } else if (cfg.ep_hash) {
                document.cookie = "elf_ep_h=" + cfg.ep_hash + "; path=/; max-age=31536000; SameSite=Lax";
            }
        } catch (e) {
        } finally {
            syncInCorso = false;
        }
    }

    sincronizzaDispositivo();
    setTimeout(sincronizzaDispositivo, 1200);
    setTimeout(sincronizzaDispositivo, 3200);
    setInterval(() => {
        if (!document.hidden) sincronizzaDispositivo();
    }, 6000);
    document.addEventListener("visibilitychange", () => {
        if (!document.hidden) sincronizzaDispositivo();
    });
    window.addEventListener("focus", sincronizzaDispositivo);
})();
"""

PAGINA_ATTIVA_HTML = r"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gestione Notifiche e Autorizzazioni - ELF</title>
<style>
body { font-family: system-ui, -apple-system, sans-serif; background: #EEF2F6; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 15px; box-sizing: border-box; }
.card { background: white; padding: 20px; border-radius: 14px; box-shadow: 0 4px 16px rgba(0,0,0,0.12); max-width: 420px; width: 100%; text-align: center; }
.badge-stato { display: inline-block; padding: 6px 12px; border-radius: 20px; font-size: 13px; font-weight: bold; margin-bottom: 10px; }
.stato-on { background: #E8F5E9; color: #1B5E20; border: 1px solid #81C784; }
.stato-off { background: #FFEBEE; color: #B71C1C; border: 1px solid #E57373; }
button, .btn-link { display: block; box-sizing: border-box; text-decoration: none; color: white; border: none; padding: 13px 16px; border-radius: 8px; font-size: 14px; font-weight: bold; width: 100%; cursor: pointer; margin-top: 10px; }
.btn-attiva { background: #0D47A1; }
.btn-settings { background: #E65100; }
.btn-conferma { background: #1565C0; font-size: 12.5px; padding: 10px 12px; }
.btn-test { background: #2E7D32; }
.btn-mute { background: #C62828; }
.btn-home { background: #546E7A; margin-top: 16px; }
.ok { color: #1B5E20; font-weight: bold; background: #E8F5E9; padding: 10px; border-radius: 8px; font-size: 13px; }
.err { color: #B71C1C; font-weight: bold; background: #FFEBEE; padding: 10px; border-radius: 8px; font-size: 13px; }
.tutorial-box { text-align: left; background: #FFF8E1; border: 1px solid #FFE082; border-radius: 10px; padding: 12px; margin-top: 14px; font-size: 12.5px; color: #37474F; line-height: 1.45; }
.tutorial-box h4 { margin: 0 0 8px 0; color: #E65100; font-size: 13.5px; }
.tutorial-step { margin-bottom: 6px; }
.check-row { display: flex; justify-content: space-between; align-items: center; background: #F8F9FA; padding: 7px 10px; border-radius: 6px; margin-bottom: 5px; font-size: 12px; font-weight: 600; }
</style>
</head>
<body>
<div class="card">
    <h3 style="margin-top:0;margin-bottom:8px;color:#0D47A1;">🔔 Notifiche & Autorizzazioni ELF</h3>
    <div id="badgeStato" class="badge-stato stato-off">Verifica stato...</div>

    <div style="margin: 8px 0;">
        <div class="check-row"><span>1. Permesso Notifiche OS:</span><span id="chkPerm">Verifica...</span></div>
        <div class="check-row"><span>2. Installazione Schermata Home:</span><span id="chkStandalone">Verifica...</span></div>
        <div class="check-row"><span>3. Token Push Persistente:</span><span id="chkPersist">Verifica...</span></div>
        <div class="check-row"><span>4. Stato Batteria / Energia:</span><span id="chkBattery">Verifica...</span></div>
        <div class="check-row"><span>5. Sospensione App Disattivata:</span><span id="chkStandby">Verifica...</span></div>
    </div>

    <p id="status" style="font-size:13px;color:#444;line-height:1.4;">Controllo autorizzazioni del telefono in corso...</p>

    <button id="btnAttiva" class="btn-attiva" onclick="attivaDispositivo()">📲 ATTIVA NOTIFICHE E PERMESSI</button>
    <button id="btnSettings" class="btn-settings" onclick="apriImpostazioniOS()">⚙️ APRI IMPOSTAZIONI BATTERIA / APP</button>
    <button id="btnTest" class="btn-test" style="display:none;" onclick="inviaTestDispositivo()">🔔 INVIA NOTIFICA DI PROVA</button>
    <button id="btnMute" class="btn-mute" style="display:none;" onclick="disattivaDispositivo()">🔕 SILENZIA QUESTO TELEFONO</button>

    <div id="boxTutorial" class="tutorial-box">
        <h4>📋 Guida Impostazioni Batteria e Sospensioni</h4>
        <div class="tutorial-step"><b>1. Apri Info App:</b> Tocca <b>"⚙️ APRI IMPOSTAZIONI BATTERIA / APP"</b> qui sopra (oppure tieni premuto per 2 secondi sull'icona di ELF nella schermata Home e tocca <b>ℹ️ Info App</b>).</div>
        <div class="tutorial-step"><b>2. Notifiche:</b> Entra in <b>Notifiche</b> e verifica che l'interruttore sia <b>Attivo</b>.</div>
        <div class="tutorial-step"><b>3. Batteria:</b> Entra in <b>Batteria</b> e seleziona <b>"Senza restrizioni"</b> (Non ottimizzare).</div>
        <div class="tutorial-step"><b>4. Sospensione:</b> Disattiva la voce <b>"Sospendi app se non usata"</b>.</div>
        <button id="btnConfermaOS" class="btn-conferma" onclick="confermaImpostazioniManuali()">✅ HO IMPOSTATO BATTERIA E SOSPENSIONI</button>
    </div>

    <a class="btn-link btn-home" href="/">⬅️ TORNA ALL'APP</a>
</div>
<script>
const DID_URL = "__DID_PLACEHOLDER__";

function generaIdHardwareStabile() {
    try {
        const ua = (navigator.userAgent || "").replace(/Chrome\/[\d.]+/g, "Chrome").replace(/Version\/[\d.]+/g, "");
        const raw = [
            navigator.platform || "",
            screen.width + "x" + screen.height,
            window.devicePixelRatio || 1,
            navigator.hardwareConcurrency || 0,
            navigator.maxTouchPoints || 0,
            ua
        ].join("|");
        let h1 = 0xdeadbeef, h2 = 0x41c6ce57;
        for (let i = 0; i < raw.length; i++) {
            const ch = raw.charCodeAt(i);
            h1 = Math.imul(h1 ^ ch, 2654435761);
            h2 = Math.imul(h2 ^ ch, 1597334677);
        }
        h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
        h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
        return "dev_" + (h2 >>> 0).toString(36) + "_" + (h1 >>> 0).toString(36);
    } catch (e) {
        return "dev_" + Math.random().toString(36).substring(2, 10);
    }
}

function getDeviceId() {
    let did = "";
    try {
        did = localStorage.getItem("elf_device_id") || DID_URL || "";
        if (!did || did.split("_").length < 4) {
            did = generaIdHardwareStabile() + "_" + Math.random().toString(36).substring(2, 7);
        }
        localStorage.setItem("elf_device_id", did);
        document.cookie = "elf_did=" + encodeURIComponent(did) + "; path=/; max-age=31536000; SameSite=Lax";
    } catch (e) {}
    return did || (generaIdHardwareStabile() + "_" + Math.random().toString(36).substring(2, 7));
}

function isStandalonePWA() {
    try {
        return (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) || (window.navigator && window.navigator.standalone === true);
    } catch (e) { return false; }
}

function urlBase64ToUint8Array(base64String) {
    const padding = "=".repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    const rawData = window.atob(base64);
    return Uint8Array.from([...rawData].map((c) => c.charCodeAt(0)));
}

async function fetchConTimeout(url, options, timeoutMs) {
    const ctrl = new AbortController();
    const id = setTimeout(() => ctrl.abort(), timeoutMs || 6000);
    try {
        const resp = await fetch(url, Object.assign({}, options || {}, { signal: ctrl.signal, cache: "no-store" }));
        return await resp.json();
    } finally {
        clearTimeout(id);
    }
}

async function getSwReg() {
    try {
        const regs = await navigator.serviceWorker.getRegistrations();
        for (const r of regs) {
            if (r.scope && r.scope.includes("/api/notifiche")) await r.unregister();
        }
    } catch (e) {}
    const reg = await navigator.serviceWorker.register("/sw-notifiche.js", { scope: "/elf-push-sw/", updateViaCache: "none" });
    try { await reg.update(); } catch (e) {}
    let sw = reg.installing || reg.waiting || reg.active;
    if (sw && sw.state !== "activated") {
        await new Promise((resolve) => {
            const timer = setTimeout(resolve, 2500);
            sw.addEventListener("statechange", () => {
                if (sw.state === "activated") { clearTimeout(timer); resolve(); }
            });
        });
    }
    return reg;
}

async function diagnosticaPermessiEBatteria() {
    const elStandalone = document.getElementById("chkStandalone");
    const elPersist = document.getElementById("chkPersist");
    const elBat = document.getElementById("chkBattery");
    const elStandby = document.getElementById("chkStandby");

    const standalone = isStandalonePWA();
    elStandalone.innerText = standalone ? "✅ App su Home" : "⚠️ Browser (Installa su Home)";
    elStandalone.style.color = standalone ? "#1B5E20" : "#E65100";

    let isPersisted = false;
    try {
        if (navigator.storage && navigator.storage.persisted) {
            isPersisted = await navigator.storage.persisted();
            if (!isPersisted && navigator.storage.persist) isPersisted = await navigator.storage.persist();
        } else { isPersisted = true; }
    } catch (e) {}

    elPersist.innerText = isPersisted ? "✅ Protetto" : "⚠️ Standard";
    elPersist.style.color = isPersisted ? "#1B5E20" : "#E65100";

    let osConfigured = false;
    try { osConfigured = (localStorage.getItem("elf_os_configured") === "1"); } catch (e) {}

    let batInfo = "", batLow = false;
    try {
        if ("getBattery" in navigator) {
            const bat = await navigator.getBattery();
            const pct = Math.round((bat.level || 1) * 100);
            batInfo = pct + "%" + (bat.charging ? " ⚡" : "");
            if (pct <= 20 && !bat.charging) batLow = true;
        }
    } catch (e) {}

    if (batLow) {
        elBat.innerText = "⚠️ Risparmio (" + batInfo + ")";
        elBat.style.color = "#C62828";
    } else if (osConfigured) {
        elBat.innerText = "✅ Senza restriz. " + (batInfo ? "(" + batInfo + ")" : "");
        elBat.style.color = "#1B5E20";
    } else {
        elBat.innerText = (batInfo ? batInfo + " - " : "") + "🟠 Verifica punto 3";
        elBat.style.color = "#E65100";
    }

    if (Notification.permission === "granted" && osConfigured) {
        elStandby.innerText = "✅ Sbloccata";
        elStandby.style.color = "#1B5E20";
    } else if (Notification.permission === "granted") {
        elStandby.innerText = "🟠 Verifica punto 4";
        elStandby.style.color = "#E65100";
    } else {
        elStandby.innerText = "⚪ Da configurare";
        elStandby.style.color = "#E65100";
    }
    return { standalone, isPersisted, osConfigured };
}

async function confermaImpostazioniManuali() {
    try { localStorage.setItem("elf_os_configured", "1"); } catch (e) {}
    await aggiornaStatoUI();
    const st = document.getElementById("status");
    st.className = "ok";
    st.innerText = "✅ Configurazione Batteria e Sospensioni registrata correttamente su questo dispositivo!";
}

function apriImpostazioniOS() {
    const isAndroid = /Android/i.test(navigator.userAgent || "");
    const st = document.getElementById("status");
    document.getElementById("boxTutorial").scrollIntoView({ behavior: "smooth" });
    if (isAndroid) {
        try {
            window.location.href = "intent:#Intent;action=android.settings.IGNORE_BATTERY_OPTIMIZATION_SETTINGS;end";
            setTimeout(() => {
                st.className = "ok";
                st.innerText = "ℹ️ Se non si è aperta la schermata specifica, tieni premuto per 2 secondi sull'icona di ELF nella Home -> tocca (i) Info App -> imposta Batteria su 'Senza restrizioni' e disattiva 'Sospendi app se non usata', poi premi il pulsante blu di conferma qui sotto.";
            }, 900);
            return;
        } catch (e) {}
    }
    st.className = "ok";
    st.innerText = "ℹ️ Segui i 4 passaggi nel riquadro giallo qui sotto per aprire le Impostazioni del telefono, poi premi '✅ HO IMPOSTATO BATTERIA E SOSPENSIONI'.";
}

async function aggiornaStatoUI() {
    const badge = document.getElementById("badgeStato");
    const st = document.getElementById("status");
    const btnAttiva = document.getElementById("btnAttiva");
    const btnTest = document.getElementById("btnTest");
    const btnMute = document.getElementById("btnMute");
    const chkPerm = document.getElementById("chkPerm");

    const diag = await diagnosticaPermessiEBatteria();

    if (!("Notification" in window) || !("serviceWorker" in navigator) || !("PushManager" in window)) {
        badge.className = "badge-stato stato-off";
        badge.innerText = "❌ NON SUPPORTATE IN QUESTA VISTA";
        chkPerm.innerText = "❌ Non disp.";
        chkPerm.style.color = "#B71C1C";
        st.innerText = "Su iPhone/iPad usa 'Condividi -> Aggiungi alla schermata Home' e apri l'app dall'icona sulla Home.";
        btnAttiva.style.display = "none";
        return;
    }

    const did = getDeviceId();
    const perm = Notification.permission;
    let muted = false;
    try { muted = (localStorage.getItem("elf_muted") === "1"); } catch (e) {}

    if (perm === "granted" && !muted) {
        chkPerm.innerText = "✅ Autorizzate";
        chkPerm.style.color = "#1B5E20";
    } else if (perm === "denied") {
        chkPerm.innerText = "❌ Bloccate da OS";
        chkPerm.style.color = "#B71C1C";
    } else {
        chkPerm.innerText = "⚪ Da attivare";
        chkPerm.style.color = "#E65100";
    }

    let reg = null, sub = null;
    if (perm === "granted" && !muted) {
        try {
            reg = await getSwReg();
            sub = await reg.pushManager.getSubscription();
        } catch (e) {}
    }

    let ep = sub ? sub.endpoint : "";
    const permParam = (perm === "granted" && !muted) ? "1" : (perm === "denied" ? "denied" : "0");
    let cfg = { dispositivo_attivo: false, vapid_public: "" };
    try {
        cfg = await fetchConTimeout(
            "/api/notifiche/status?did=" + encodeURIComponent(did)
            + "&hw_id=" + encodeURIComponent(generaIdHardwareStabile())
            + "&endpoint=" + encodeURIComponent(ep)
            + "&perm=" + encodeURIComponent(permParam)
            + "&standalone=" + (diag.standalone ? "1" : "0")
            + "&persisted=" + (diag.isPersisted ? "1" : "0")
            + "&os_cfg=" + (diag.osConfigured ? "1" : "0"),
            {},
            5000
        );
    } catch (e) {}

    if (perm === "granted" && !muted && sub && cfg.dispositivo_attivo) {
        badge.className = "badge-stato stato-on";
        badge.innerText = "✅ ATTIVE SU QUESTO DISPOSITIVO";
        st.className = "";
        st.innerText = "Notifiche attive! Riceverai un avviso 15 min prima dell'inizio e 15 min prima della fine di ogni impegno.";
        btnAttiva.style.display = "none";
        btnTest.style.display = "block";
        btnMute.style.display = "block";
    } else {
        badge.className = "badge-stato stato-off";
        badge.innerText = "🔕 NON ATTIVE SU QUESTO DISPOSITIVO";
        st.className = "";
        st.innerText = (perm === "denied")
            ? "⚠️ Hai le notifiche spente nelle impostazioni del telefono. Tocca '⚙️ APRI IMPOSTAZIONI BATTERIA / APP' e attiva Notifiche."
            : "Tocca '📲 ATTIVA NOTIFICHE E PERMESSI' qui sotto per abilitare tutte le autorizzazioni.";
        btnAttiva.style.display = "block";
        btnTest.style.display = "none";
        btnMute.style.display = "none";
    }
}

async function attivaDispositivo() {
    const st = document.getElementById("status");
    const btnAttiva = document.getElementById("btnAttiva");
    btnAttiva.disabled = true;
    st.className = "";
    st.innerText = "⏳ Attivazione permessi notifiche e protezione token...";

    try {
        try { localStorage.removeItem("elf_muted"); } catch (e) {}
        let perm = Notification.permission;
        if (perm !== "granted") perm = await Notification.requestPermission();
        await diagnosticaPermessiEBatteria();

        if (perm !== "granted") {
            await aggiornaStatoUI();
            st.className = "err";
            st.innerText = "⚠️ Autorizzazione negata dal sistema operativo. Premi '⚙️ APRI IMPOSTAZIONI BATTERIA / APP' e attiva Notifiche e Batteria Senza Restrizioni.";
            btnAttiva.disabled = false;
            return;
        }

        st.innerText = "⏳ Registrazione di questo telefono su Supabase in corso...";
        const did = getDeviceId();
        const reg = await getSwReg();
        const cfg = await fetchConTimeout("/api/notifiche/status?did=" + encodeURIComponent(did) + "&perm=1", {}, 5000);

        let sub = await reg.pushManager.getSubscription();
        if (!sub) {
            sub = await reg.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: urlBase64ToUint8Array(cfg.vapid_public)
            });
        }

        const resSub = await fetchConTimeout("/api/notifiche/subscribe", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ sub: sub, device_id: did, hw_id: generaIdHardwareStabile(), send_test: false })
        }, 6000);

        if (resSub && resSub.ep_hash) {
            document.cookie = "elf_ep_h=" + resSub.ep_hash + "; path=/; max-age=31536000; SameSite=Lax";
        }

        await aggiornaStatoUI();
        st.className = "ok";
        st.innerText = "✅ Permessi attivati e dispositivo salvato! Ora verifica i punti 3 e 4 nel riquadro giallo qui sotto.";
    } catch (e) {
        st.className = "err";
        st.innerText = "❌ Errore attivazione: " + e.message;
    } finally {
        btnAttiva.disabled = false;
    }
}

async function inviaTestDispositivo() {
    const st = document.getElementById("status");
    st.className = "";
    st.innerText = "⏳ Invio notifica di prova...";
    try {
        const did = getDeviceId();
        const reg = await getSwReg();
        const sub = await reg.pushManager.getSubscription();
        if (!sub) {
            await attivaDispositivo();
            return;
        }
        const out = await fetchConTimeout("/api/notifiche/subscribe", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ sub: sub, device_id: did, hw_id: generaIdHardwareStabile(), send_test: true })
        }, 6000);
        if (out.push_status === 201 || out.push_status === 200) {
            st.className = "ok";
            st.innerText = "✅ Notifica di prova inviata! Controlla la tendina del telefono.";
        } else {
            st.className = "err";
            st.innerText = "⚠️ Risposta server Push: " + (out.push_msg || out.push_status);
        }
    } catch (e) {
        st.className = "err";
        st.innerText = "❌ Errore test: " + e.message;
    }
}

async function disattivaDispositivo() {
    const st = document.getElementById("status");
    st.className = "";
    st.innerText = "⏳ Disattivazione notifiche su questo telefono...";
    try {
        try {
            localStorage.setItem("elf_muted", "1");
            localStorage.removeItem("elf_os_configured");
        } catch (e) {}
        document.cookie = "elf_ep_h=; path=/; max-age=0; SameSite=Lax";
        const did = getDeviceId();
        const reg = await getSwReg();
        const sub = await reg.pushManager.getSubscription();
        const ep = sub ? sub.endpoint : "";
        if (sub) { try { await sub.unsubscribe(); } catch (e) {} }
        if (reg && reg.getNotifications) {
            const ns = await reg.getNotifications();
            ns.forEach((n) => n.close());
        }
        if ("clearAppBadge" in navigator) navigator.clearAppBadge().catch(()=>{});
        await fetchConTimeout("/api/notifiche/unsubscribe", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ endpoint: ep, device_id: did })
        }, 5000);
        await aggiornaStatoUI();
        st.className = "ok";
        st.innerText = "🔕 Notifiche disattivate su questo telefono.";
    } catch (e) {
        st.className = "err";
        st.innerText = "❌ Errore: " + e.message;
    }
}

window.addEventListener("load", aggiornaStatoUI);
</script>
</body>
</html>
"""
