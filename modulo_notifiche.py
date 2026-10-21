import os
import gc
import json
import time
import base64
import threading
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo
import fastapi
from fastapi.responses import HTMLResponse, Response, JSONResponse

try:
    from pywebpush import webpush, WebPushException
except ImportError:
    webpush = None
    WebPushException = Exception

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

TZ_ITALIA = ZoneInfo("Europe/Rome")

_STATO = {
    "supabase": None,
    "vapid_public": "",
    "vapid_private": "",
    "silenziato": False,
    "subscriptions": {},      # endpoint -> dict subscription
    "notifiche_inviate": set(), # (data_str, id_imp, tipo)
    "data_corrente": "",
    "residui_attuali": 0,
    "ultimo_residui_push": -1,
    "ultimo_messaggio_vocale": {"id": 0, "testo": ""},
    "cache_famiglia": {},     # id -> nome
    "cache_categorie": {},    # id -> nome
    "ultimo_sync_anagrafica": 0,
    "scheduler_avviato": False,
    "evento_ricalcolo": threading.Event()
}
_LOCK = threading.Lock()


def _ora_italia() -> datetime:
    return datetime.now(TZ_ITALIA)


def _calcola_minuti(orario_str) -> int:
    if not orario_str:
        return -1
    try:
        h, m = map(int, str(orario_str)[:5].split(":"))
        return h * 60 + m
    except Exception:
        return -1


def _genera_chiavi_vapid():
    priv_key = ec.generate_private_key(ec.SECP256R1())
    priv_val = priv_key.private_numbers().private_value
    priv_bytes = priv_val.to_bytes(32, byteorder="big")
    pub_bytes = priv_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint
    )
    priv_b64 = base64.urlsafe_b64encode(priv_bytes).decode("utf-8").rstrip("=")
    pub_b64 = base64.urlsafe_b64encode(pub_bytes).decode("utf-8").rstrip("=")
    return pub_b64, priv_b64


def _carica_o_inizializza_config_db():
    sb = _STATO["supabase"]
    if not sb:
        return
    try:
        res = sb.table("menu_giornalieri").select("id, categoria, subcategoria, preso").eq("data_menu", "1970-01-01").in_("categoria", ["SYS_VAPID", "SYS_SUB"]).execute()
        righe = res.data or []
        vapid_trovato = False
        for r in righe:
            cat = r.get("categoria")
            sub_txt = r.get("subcategoria") or ""
            if cat == "SYS_VAPID" and sub_txt:
                dati_v = json.loads(sub_txt)
                _STATO["vapid_public"] = dati_v.get("pub", "")
                _STATO["vapid_private"] = dati_v.get("priv", "")
                _STATO["silenziato"] = bool(r.get("preso"))
                vapid_trovato = bool(_STATO["vapid_public"] and _STATO["vapid_private"])
            elif cat == "SYS_SUB" and sub_txt:
                try:
                    s_obj = json.loads(sub_txt)
                    ep = s_obj.get("endpoint")
                    if ep:
                        _STATO["subscriptions"][ep] = {"id_db": r["id"], "sub": s_obj}
                except Exception:
                    pass

        if not vapid_trovato:
            pub_k = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
            priv_k = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
            if not (pub_k and priv_k):
                pub_k, priv_k = _genera_chiavi_vapid()
            _STATO["vapid_public"] = pub_k
            _STATO["vapid_private"] = priv_k
            sb.table("menu_giornalieri").insert({
                "data_menu": "1970-01-01",
                "categoria": "SYS_VAPID",
                "subcategoria": json.dumps({"pub": pub_k, "priv": priv_k}),
                "quantita": 0,
                "unita_misura": "pz",
                "preso": False
            }).execute()
    except Exception as ex:
        print(f"[NOTIFICHE] Errore init config DB: {ex}", flush=True)
        if not _STATO["vapid_public"]:
            _STATO["vapid_public"], _STATO["vapid_private"] = _genera_chiavi_vapid()


def is_notifiche_attive() -> bool:
    return (len(_STATO["subscriptions"]) > 0) and (not _STATO["silenziato"])


def ha_dispositivi_registrati() -> bool:
    return len(_STATO["subscriptions"]) > 0


def imposta_silenzioso(valore: bool):
    _STATO["silenziato"] = bool(valore)
    sb = _STATO["supabase"]
    if sb:
        def _w():
            try:
                sb.table("menu_giornalieri").update({"preso": bool(valore)}).eq("data_menu", "1970-01-01").eq("categoria", "SYS_VAPID").execute()
            except Exception:
                pass
        threading.Thread(target=_w, daemon=True).start()
    if not valore:
        forza_ricalcolo_impegni()


def forza_ricalcolo_impegni():
    _STATO["ultimo_sync_anagrafica"] = 0
    _STATO["evento_ricalcolo"].set()


def _invia_push_a_tutti(payload_dict: dict):
    if not webpush or not _STATO["vapid_private"] or not _STATO["subscriptions"]:
        return
    if _STATO["silenziato"] and not payload_dict.get("badge_only"):
        return

    data_str = json.dumps(payload_dict)
    da_rimuovere = []
    with _LOCK:
        items = list(_STATO["subscriptions"].items())

    for ep, info in items:
        try:
            webpush(
                subscription_info=info["sub"],
                data=data_str,
                vapid_private_key=_STATO["vapid_private"],
                vapid_claims={"sub": "mailto:notifiche@extralargefamily.it"},
                ttl=3600
            )
        except WebPushException as ex:
            if ex.response is not None and ex.response.status_code in [404, 410]:
                da_rimuovere.append((ep, info.get("id_db")))
        except Exception:
            pass

    if da_rimuovere:
        with _LOCK:
            for ep, id_db in da_rimuovere:
                _STATO["subscriptions"].pop(ep, None)
                if id_db and _STATO["supabase"]:
                    try:
                        _STATO["supabase"].table("menu_giornalieri").delete().eq("id", id_db).execute()
                    except Exception:
                        pass


def _controlla_impegni_oggi():
    sb = _STATO["supabase"]
    if not sb:
        return

    adesso = _ora_italia()
    oggi_str = adesso.strftime("%Y-%m-%d")
    oggi_date = adesso.date()
    minuti_ora = adesso.hour * 60 + adesso.minute

    if _STATO["data_corrente"] != oggi_str:
        _STATO["data_corrente"] = oggi_str
        _STATO["notifiche_inviate"].clear()
        _STATO["ultimo_residui_push"] = -1

    ora_ts = time.time()
    if ora_ts - _STATO["ultimo_sync_anagrafica"] > 1800 or not _STATO["cache_famiglia"]:
        try:
            rf = sb.table("famiglia").select("id, nome").execute()
            _STATO["cache_famiglia"] = {str(x["id"]): x["nome"] for x in (rf.data or [])}
            rc = sb.table("categorie").select("id, nome").execute()
            _STATO["cache_categorie"] = {str(x["id"]): x["nome"] for x in (rc.data or [])}
            _STATO["ultimo_sync_anagrafica"] = ora_ts
        except Exception:
            pass

    try:
        res = sb.table("impegni").select("*, impegni_familiari(id_familiare)").lte("data_evento", oggi_str).execute()
        tutti = res.data or []
    except Exception:
        return

    eventi_oggi = []
    for imp in tutti:
        val_p = imp.get("is_periodico")
        is_p = str(val_p).lower() in ["true", "1", "t", "y", "yes"]
        if is_p:
            try:
                d_start = datetime.strptime(imp["data_evento"], "%Y-%m-%d").date()
                d_stop = datetime.strptime(imp["data_fine_periodo"], "%Y-%m-%d").date() if imp.get("data_fine_periodo") else None
            except Exception:
                continue
            if oggi_date < d_start or (d_stop and oggi_date > d_stop):
                continue
            escluse = (imp.get("date_escluse") or "").split(",")
            if oggi_str in escluse:
                continue
            g_str = imp.get("giorni_periodici") or ""
            giorni_rep = [int(g) for g in g_str.split(",") if g.strip().isdigit()] if g_str else [d_start.weekday()]
            if oggi_date.weekday() in giorni_rep:
                eventi_oggi.append(imp)
        else:
            if imp.get("data_evento") == oggi_str:
                eventi_oggi.append(imp)

    residui = 0
    for ev in eventi_oggi:
        id_ev = str(ev.get("id"))
        titolo = ev.get("titolo") or "Impegno"
        ora_i_str = ev.get("ora_inizio")
        ora_f_str = ev.get("ora_fine")
        min_i = _calcola_minuti(ora_i_str)
        min_f = _calcola_minuti(ora_f_str)
        if min_i >= 0 and min_f < 0:
            min_f = min(min_i + 60, 1439)

        # Calcolo impegni residui rispetto all'orario attuale
        fine_effettiva = min_f if min_f >= 0 else 1439
        if fine_effettiva > minuti_ora:
            residui += 1

        if _STATO["silenziato"] or min_i < 0:
            continue

        ids_fam = [str(f["id_familiare"]) for f in (ev.get("impegni_familiari") or []) if f.get("id_familiare")]
        nomi_fam = ", ".join([_STATO["cache_famiglia"].get(fid, "") for fid in ids_fam if _STATO["cache_famiglia"].get(fid)]) or "Tutta la famiglia"
        nome_cat = _STATO["cache_categorie"].get(str(ev.get("id_categoria")), "Impegno")
        ora_i_vis = str(ora_i_str)[:5]
        ora_f_vis = str(ora_f_str)[:5] if ora_f_str else f"{(min_f // 60):02d}:{(min_f % 60):02d}"

        # 1. Notifica 15 minuti prima dell'INIZIO
        diff_start = minuti_ora - (min_i - 15)
        chiave_start = (oggi_str, id_ev, "start15")
        if 0 <= diff_start <= 2 and chiave_start not in _STATO["notifiche_inviate"]:
            _STATO["notifiche_inviate"].add(chiave_start)
            testo_voce = f"Attenzione: tra 15 minuti, alle {ora_i_vis}, inizia {titolo} per {nomi_fam}."
            _STATO["ultimo_messaggio_vocale"] = {"id": int(time.time()), "testo": testo_voce}
            _invia_push_a_tutti({
                "title": f"⏰ Tra 15 min inizia: {titolo}",
                "body": f"👥 {nomi_fam}\n🕒 Ore {ora_i_vis} - {ora_f_vis} ({nome_cat})",
                "voice": testo_voce,
                "badge_count": residui,
                "tag": f"start_{id_ev}_{oggi_str}"
            })

        # 2. Notifica 15 minuti prima della FINE
        if min_f >= 0:
            diff_end = minuti_ora - (min_f - 15)
            chiave_end = (oggi_str, id_ev, "end15")
            if 0 <= diff_end <= 2 and chiave_end not in _STATO["notifiche_inviate"]:
                _STATO["notifiche_inviate"].add(chiave_end)
                testo_voce = f"Avviso: tra 15 minuti, alle {ora_f_vis}, termina {titolo} di {nomi_fam}."
                _STATO["ultimo_messaggio_vocale"] = {"id": int(time.time()), "testo": testo_voce}
                _invia_push_a_tutti({
                    "title": f"🏁 Tra 15 min termina: {titolo}",
                    "body": f"👥 {nomi_fam}\n🕒 Fine prevista ore {ora_f_vis} ({nome_cat})",
                    "voice": testo_voce,
                    "badge_count": residui,
                    "tag": f"end_{id_ev}_{oggi_str}"
                })

    _STATO["residui_attuali"] = residui
    if residui != _STATO["ultimo_residui_push"]:
        _STATO["ultimo_residui_push"] = residui
        _invia_push_a_tutti({
            "badge_only": True,
            "badge_count": residui
        })


def _loop_scheduler():
    time.sleep(3)
    _carica_o_inizializza_config_db()
    while True:
        try:
            _controlla_impegni_oggi()
            gc.collect()
        except Exception as ex:
            print(f"[NOTIFICHE] Errore loop: {ex}", flush=True)
        _STATO["evento_ricalcolo"].wait(timeout=45)
        _STATO["evento_ricalcolo"].clear()


SW_JS_CODE = """
self.addEventListener('install', (e) => { self.skipWaiting(); });
self.addEventListener('activate', (e) => { e.waitUntil(self.clients.claim()); });

self.addEventListener('push', (event) => {
    if (!event.data) return;
    let data = {};
    try { data = event.data.json(); } catch (e) { return; }

    const badgeCount = (typeof data.badge_count === 'number') ? data.badge_count : null;
    const badgePromise = (badgeCount !== null && 'setAppBadge' in self.navigator)
        ? (badgeCount > 0 ? self.navigator.setAppBadge(badgeCount) : self.navigator.clearAppBadge())
        : Promise.resolve();

    if (data.badge_only || !data.title) {
        event.waitUntil(badgePromise);
        return;
    }

    const options = {
        body: data.body || '',
        icon: '/icons/icon-192.png',
        badge: '/icons/icon-192.png',
        vibrate: [250, 120, 250, 120, 400],
        tag: data.tag || 'elf-notif',
        renotify: true,
        requireInteraction: true,
        data: { voice: data.voice || data.body || '', url: '/?speak=' + encodeURIComponent(data.voice || data.title) }
    };

    const notifyClients = self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
        clients.forEach((c) => c.postMessage({ type: 'ELF_SPEAK', voice: data.voice || data.title, badge_count: badgeCount }));
    });

    event.waitUntil(Promise.all([
        badgePromise,
        notifyClients,
        self.registration.showNotification(data.title, options)
    ]));
});

self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    const targetUrl = (event.notification.data && event.notification.data.url) ? event.notification.data.url : '/';
    event.waitUntil(
        self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
            for (const client of clientList) {
                if ('focus' in client) {
                    client.postMessage({ type: 'ELF_SPEAK', voice: event.notification.data.voice || '' });
                    return client.focus();
                }
            }
            if (self.clients.openWindow) return self.clients.openWindow(targetUrl);
        })
    );
});
"""

CLIENT_JS_CODE = """
(function() {
    if (window.__elfNotifInit) return;
    window.__elfNotifInit = true;
    let lastVocalId = 0;

    function parlaItaliano(testo) {
        if (!testo || !('speechSynthesis' in window)) return;
        try {
            window.speechSynthesis.cancel();
            const u = new SpeechSynthesisUtterance(testo);
            u.lang = 'it-IT';
            u.rate = 1.0;
            window.speechSynthesis.speak(u);
        } catch (e) {}
    }

    function aggiornaBadgeLocale(n) {
        if (typeof n !== 'number') return;
        if ('setAppBadge' in navigator) {
            if (n > 0) navigator.setAppBadge(n).catch(()=>{});
            else navigator.clearAppBadge().catch(()=>{});
        }
    }

    const params = new URLSearchParams(window.location.search);
    if (params.has('speak')) {
        const msg = params.get('speak');
        setTimeout(() => parlaItaliano(msg), 600);
        window.history.replaceState({}, document.title, '/');
    }

    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/sw-notifiche.js').catch(()=>{});
        navigator.serviceWorker.addEventListener('message', (event) => {
            if (event.data && event.data.type === 'ELF_SPEAK') {
                if (typeof event.data.badge_count === 'number') aggiornaBadgeLocale(event.data.badge_count);
                parlaItaliano(event.data.voice);
            }
        });
    }

    async function syncStatoServer() {
        try {
            const r = await fetch('/api/notifiche/status', { cache: 'no-store' });
            const d = await r.json();
            aggiornaBadgeLocale(d.residui);
            if (!d.silenziato && d.vocal && d.vocal.id && d.vocal.id !== lastVocalId) {
                if (lastVocalId !== 0) parlaItaliano(d.vocal.testo);
                lastVocalId = d.vocal.id;
            }
        } catch (e) {}
    }
    syncStatoServer();
    setInterval(syncStatoServer, 30000);
})();
"""

PAGINA_ATTIVA_HTML = """<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Attivazione Notifiche ELF</title>
<style>
body { font-family: system-ui, sans-serif; background: #EEF2F6; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; padding: 15px; box-sizing: border-box; }
.card { background: white; padding: 24px; border-radius: 14px; box-shadow: 0 4px 16px rgba(0,0,0,0.12); max-width: 360px; width: 100%; text-align: center; }
button { background: #0D47A1; color: white; border: none; padding: 14px 20px; border-radius: 8px; font-size: 15px; font-weight: bold; width: 100%; cursor: pointer; margin-top: 12px; }
.ok { color: #2E7D32; font-weight: bold; }
</style>
</head>
<body>
<div class="card">
    <h3 style="margin-top:0;color:#0D47A1;">🔔 Notifiche Vocali & Badge ELF</h3>
    <p id="status" style="font-size:14px;color:#444;">Premi il pulsante qui sotto per attivare su questo telefono le notifiche 15 min prima di inizio/fine impegno e il contatore sull'icona.</p>
    <button id="btn" onclick="attivaTutto()">📲 CONFERMA E ATTIVA ORA</button>
</div>
<script>
function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/\\-/g, '+').replace(/_/g, '/');
    const rawData = window.atob(base64);
    return Uint8Array.from([...rawData].map((c) => c.charCodeAt(0)));
}

async function attivaTutto() {
    const st = document.getElementById('status');
    const btn = document.getElementById('btn');
    btn.disabled = true;
    st.innerText = "⏳ Configurazione notifiche e permessi in corso...";
    try {
        if ('speechSynthesis' in window) {
            const u = new SpeechSynthesisUtterance("Notifiche vocali Extra Large Family attivate.");
            u.lang = 'it-IT';
            window.speechSynthesis.speak(u);
        }
        const perm = await Notification.requestPermission();
        if (perm !== 'granted') {
            st.innerText = "⚠️ Permesso notifiche negato nelle impostazioni del telefono/browser.";
            btn.disabled = false;
            return;
        }
        const reg = await navigator.serviceWorker.register('/sw-notifiche.js');
        await navigator.serviceWorker.ready;
        const cfgResp = await fetch('/api/notifiche/status');
        const cfg = await cfgResp.json();
        const sub = await reg.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: urlBase64ToUint8Array(cfg.vapid_public)
        });
        await fetch('/api/notifiche/subscribe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(sub)
        });
        if ('setAppBadge' in navigator) {
            if (cfg.residui > 0) await navigator.setAppBadge(cfg.residui);
            else await navigator.clearAppBadge();
        }
        st.className = "ok";
        st.innerText = "✅ Notifiche Push, Voce e Badge attivate con successo! Ritorno all'app...";
        setTimeout(() => { window.location.href = "/"; }, 1200);
    } catch (e) {
        st.innerText = "Errore attivazione: " + e.message;
        btn.disabled = false;
    }
}
</script>
</body>
</html>
"""


def collega_api_fastapi(app: fastapi.FastAPI, supabase_client):
    if supabase_client is not None:
        _STATO["supabase"] = supabase_client

    with _LOCK:
        if not _STATO["scheduler_avviato"] and _STATO["supabase"] is not None:
            _STATO["scheduler_avviato"] = True
            threading.Thread(target=_loop_scheduler, daemon=True).start()

    @app.get("/sw-notifiche.js")
    async def get_sw():
        return Response(content=SW_JS_CODE, media_type="application/javascript", headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})

    @app.get("/api/notifiche/client.js")
    async def get_client_js():
        return Response(content=CLIENT_JS_CODE, media_type="application/javascript", headers={"Cache-Control": "no-cache"})

    @app.get("/api/notifiche/pagina-attiva")
    async def get_pagina_attiva():
        return HTMLResponse(content=PAGINA_ATTIVA_HTML, headers={"Cache-Control": "no-cache"})

    @app.get("/api/notifiche/status")
    async def get_status():
        return JSONResponse({
            "attive": is_notifiche_attive(),
            "silenziato": _STATO["silenziato"],
            "dispositivi": len(_STATO["subscriptions"]),
            "residui": _STATO["residui_attuali"],
            "vapid_public": _STATO["vapid_public"],
            "vocal": _STATO["ultimo_messaggio_vocale"]
        })

    @app.post("/api/notifiche/subscribe")
    async def post_subscribe(req: fastapi.Request):
        sub_obj = await req.json()
        ep = sub_obj.get("endpoint")
        if ep:
            with _LOCK:
                esistente = _STATO["subscriptions"].get(ep)
                id_db = esistente.get("id_db") if esistente else None
                if not id_db and _STATO["supabase"]:
                    try:
                        res = _STATO["supabase"].table("menu_giornalieri").insert({
                            "data_menu": "1970-01-01",
                            "categoria": "SYS_SUB",
                            "subcategoria": json.dumps(sub_obj),
                            "quantita": 0,
                            "unita_misura": "pz",
                            "preso": False
                        }).execute()
                        if res.data:
                            id_db = res.data[0]["id"]
                    except Exception:
                        pass
                _STATO["subscriptions"][ep] = {"id_db": id_db, "sub": sub_obj}
            imposta_silenzioso(False)
        return JSONResponse({"ok": True, "residui": _STATO["residui_attuali"]})

    @app.middleware("http")
    async def inietta_script_notifiche(request: fastapi.Request, call_next):
         path = request.url.path
         response = await call_next(request)
         if path == "/flutter_service_worker.js" and response.status_code == 200:
             body = b""
             async for chunk in response.body_iterator:
                 body += chunk
             prefisso = b"try { importScripts('/sw-notifiche.js'); } catch(e) {}\n"
             nuovo_body = prefisso + body
             headers = dict(response.headers)
             headers["content-length"] = str(len(nuovo_body))
             headers["cache-control"] = "no-cache"
             return Response(content=nuovo_body, status_code=200, headers=headers, media_type="application/javascript")
         elif path == "/" and "text/html" in response.headers.get("content-type", ""):
             body = b""
             async for chunk in response.body_iterator:
                 body += chunk
             tag_script = b'<script src="/api/notifiche/client.js" defer></script></body>'
             if b"/api/notifiche/client.js" not in body and b"</body>" in body:
                 body = body.replace(b"</body>", tag_script, 1)
             headers = dict(response.headers)
             headers["content-length"] = str(len(body))
             return Response(content=body, status_code=response.status_code, headers=headers, media_type="text/html")
         return response
