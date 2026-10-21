import os
import gc
import json
import time
import base64
import threading
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
    "inizializzato": False,
    "vapid_public": "",
    "vapid_private": "",
    "silenziato": False,
    "subscriptions": {},        # endpoint -> {"id_db": ..., "sub": ...}
    "notifiche_inviate": set(), # "YYYY-MM-DD|id_imp|tipo"
    "data_corrente": "",
    "residui_attuali": 0,
    "ultimo_residui_push": -1,
    "cache_famiglia": {},       # id -> nome
    "cache_categorie": {},      # id -> nome
    "ultimo_sync_anagrafica": 0,
    "scheduler_avviato": False,
    "evento_ricalcolo": threading.Event(),
    "pagine_attive": set()
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


def _pulisci_chiave_str(val: str) -> str:
    return (val or "").strip().strip('"').strip("'").strip().rstrip("=")


def _valida_o_deriva_coppia_vapid(pub_raw: str, priv_raw: str):
    """
    Verifica che la chiave privata VAPID sia valida e ricava matematicamente
    la chiave pubblica corrispondente (evitando qualsiasi disallineamento su Render).
    """
    priv_clean = _pulisci_chiave_str(priv_raw)
    if not priv_clean:
        return "", ""
    try:
        pad = "=" * ((4 - len(priv_clean) % 4) % 4)
        priv_bytes = base64.urlsafe_b64decode(priv_clean + pad)
        if len(priv_bytes) == 32:
            priv_int = int.from_bytes(priv_bytes, byteorder="big")
            priv_key = ec.derive_private_key(priv_int, ec.SECP256R1())
            pub_bytes = priv_key.public_key().public_bytes(
                encoding=serialization.Encoding.X962,
                format=serialization.PublicFormat.UncompressedPoint
            )
            pub_derived = base64.urlsafe_b64encode(pub_bytes).decode("utf-8").rstrip("=")
            return pub_derived, priv_clean
    except Exception as ex:
        print(f"[NOTIFICHE] Chiave VAPID_PRIVATE_KEY non standard ({ex}), uso valore grezzo.", flush=True)
    return _pulisci_chiave_str(pub_raw), priv_clean


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


def _salva_stato_vapid_db():
    sb = _STATO["supabase"]
    if not sb:
        return
    oggi_str = _ora_italia().strftime("%Y-%m-%d")
    inviate_oggi = [k for k in _STATO["notifiche_inviate"] if k.startswith(oggi_str + "|")]
    payload_vapid = json.dumps({
        "pub": _STATO["vapid_public"],
        "priv": _STATO["vapid_private"],
        "sent": inviate_oggi
    })
    try:
        res = sb.table("menu_giornalieri").select("id").eq("data_menu", "1970-01-01").eq("categoria", "SYS_VAPID").limit(1).execute()
        if res.data:
            sb.table("menu_giornalieri").update({
                "subcategoria": payload_vapid,
                "preso": bool(_STATO["silenziato"])
            }).eq("id", res.data[0]["id"]).execute()
        else:
            sb.table("menu_giornalieri").insert({
                "data_menu": "1970-01-01",
                "categoria": "SYS_VAPID",
                "subcategoria": payload_vapid,
                "quantita": 0,
                "unita_misura": "pz",
                "preso": bool(_STATO["silenziato"])
            }).execute()
    except Exception as ex:
        print(f"[NOTIFICHE] Errore salvataggio SYS_VAPID: {ex}", flush=True)


def _carica_o_inizializza_config_db():
    with _LOCK:
        if _STATO["inizializzato"] and _STATO["vapid_public"]:
            return
        sb = _STATO["supabase"]
        pub_env = _pulisci_chiave_str(os.environ.get("VAPID_PUBLIC_KEY", ""))
        priv_env = _pulisci_chiave_str(os.environ.get("VAPID_PRIVATE_KEY", ""))

        if priv_env:
            pub_v, priv_v = _valida_o_deriva_coppia_vapid(pub_env, priv_env)
            _STATO["vapid_public"] = pub_v
            _STATO["vapid_private"] = priv_v

        if sb:
            try:
                res = sb.table("menu_giornalieri").select("id, categoria, subcategoria, preso").eq("data_menu", "1970-01-01").in_("categoria", ["SYS_VAPID", "SYS_SUB"]).execute()
                righe = res.data or []
                oggi_str = _ora_italia().strftime("%Y-%m-%d")

                for r in righe:
                    cat = r.get("categoria")
                    sub_txt = r.get("subcategoria") or ""
                    if cat == "SYS_VAPID" and sub_txt:
                        _STATO["silenziato"] = bool(r.get("preso"))
                        try:
                            dati_v = json.loads(sub_txt)
                            for k_sent in (dati_v.get("sent") or []):
                                if str(k_sent).startswith(oggi_str + "|"):
                                    _STATO["notifiche_inviate"].add(str(k_sent))
                            if not (_STATO["vapid_public"] and _STATO["vapid_private"]):
                                pub_db, priv_db = _valida_o_deriva_coppia_vapid(dati_v.get("pub", ""), dati_v.get("priv", ""))
                                _STATO["vapid_public"] = pub_db
                                _STATO["vapid_private"] = priv_db
                        except Exception:
                            pass
                    elif cat == "SYS_SUB" and sub_txt:
                        try:
                            s_obj = json.loads(sub_txt)
                            ep = s_obj.get("endpoint")
                            if ep:
                                _STATO["subscriptions"][ep] = {"id_db": r["id"], "sub": s_obj}
                        except Exception:
                            pass
            except Exception as ex:
                print(f"[NOTIFICHE] Errore lettura config DB: {ex}", flush=True)

        if not (_STATO["vapid_public"] and _STATO["vapid_private"]):
            pub_k, priv_k = _genera_chiavi_vapid()
            _STATO["vapid_public"] = pub_k
            _STATO["vapid_private"] = priv_k

        _STATO["inizializzato"] = True
        print(f"[NOTIFICHE] Configurazione pronta. Dispositivi registrati: {len(_STATO['subscriptions'])}, Silenziato: {_STATO['silenziato']}", flush=True)

    threading.Thread(target=_salva_stato_vapid_db, daemon=True).start()


def registra_pagina(page):
    try:
        _STATO["pagine_attive"].add(page)
    except Exception:
        pass


def _aggiorna_ui_pagine():
    morti = set()
    for p in list(_STATO["pagine_attive"]):
        try:
            if hasattr(p, "aggiorna_menu_notifiche") and callable(p.aggiorna_menu_notifiche):
                p.aggiorna_menu_notifiche()
            p.update()
        except Exception:
            morti.add(p)
    for m in morti:
        _STATO["pagine_attive"].discard(m)


def is_notifiche_attive() -> bool:
    if not _STATO["inizializzato"]:
        _carica_o_inizializza_config_db()
    return (len(_STATO["subscriptions"]) > 0) and (not _STATO["silenziato"])


def ha_dispositivi_registrati() -> bool:
    if not _STATO["inizializzato"]:
        _carica_o_inizializza_config_db()
    return len(_STATO["subscriptions"]) > 0


def imposta_silenzioso(valore: bool):
    _STATO["silenziato"] = bool(valore)
    threading.Thread(target=_salva_stato_vapid_db, daemon=True).start()
    _aggiorna_ui_pagine()
    if not valore:
        forza_ricalcolo_impegni()


def forza_ricalcolo_impegni():
    _STATO["ultimo_sync_anagrafica"] = 0
    _STATO["evento_ricalcolo"].set()


def _invia_push_singolo(sub_info: dict, payload_dict: dict) -> int:
    if not webpush or not _STATO["vapid_private"]:
        return 0
    try:
        ep = sub_info.get("endpoint", "")
        parsed = __import__("urllib.parse").parse.urlparse(ep)
        aud = f"{parsed.scheme}://{parsed.netloc}" if (parsed.scheme and parsed.netloc) else None
        claims = {"sub": "mailto:notifiche@extralargefamily.it"}
        if aud:
            claims["aud"] = aud

        webpush(
            subscription_info=sub_info,
            data=json.dumps(payload_dict),
            vapid_private_key=_STATO["vapid_private"],
            vapid_claims=claims,
            ttl=3600,
            headers={"Urgency": "high"}
        )
        return 201
    except WebPushException as ex:
        code = ex.response.status_code if ex.response is not None else 500
        print(f"[NOTIFICHE] WebPushException ({code}): {ex}", flush=True)
        return code
    except Exception as ex:
        print(f"[NOTIFICHE] Errore invio push: {ex}", flush=True)
        return 500


def _invia_push_a_tutti(payload_dict: dict):
    if not webpush or not _STATO["vapid_private"] or not _STATO["subscriptions"]:
        return
    if _STATO["silenziato"] and not payload_dict.get("badge_only"):
        return

    da_rimuovere = []
    with _LOCK:
        items = list(_STATO["subscriptions"].items())

    for ep, info in items:
        code = _invia_push_singolo(info["sub"], payload_dict)
        if code in [401, 403, 404, 410]:
            da_rimuovere.append((ep, info.get("id_db")))

    if da_rimuovere:
        with _LOCK:
            for ep, id_db in da_rimuovere:
                _STATO["subscriptions"].pop(ep, None)
                if id_db and _STATO["supabase"]:
                    try:
                        _STATO["supabase"].table("menu_giornalieri").delete().eq("id", id_db).execute()
                    except Exception:
                        pass
        _aggiorna_ui_pagine()


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
        _STATO["notifiche_inviate"] = {k for k in _STATO["notifiche_inviate"] if k.startswith(oggi_str + "|")}
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
    notifiche_da_inviare = []
    nuove_inviate = False

    for ev in eventi_oggi:
        id_ev = str(ev.get("id"))
        titolo = (ev.get("titolo") or "Impegno").strip()
        note = (ev.get("note") or "").strip()
        ora_i_str = ev.get("ora_inizio")
        ora_f_str = ev.get("ora_fine")
        min_i = _calcola_minuti(ora_i_str)
        min_f = _calcola_minuti(ora_f_str)
        if min_i >= 0 and min_f < 0:
            min_f = min(min_i + 60, 1439)

        # Impegno residuo rispetto all'orario attuale
        fine_effettiva = min_f if min_f >= 0 else 1439
        if fine_effettiva > minuti_ora:
            residui += 1

        if _STATO["silenziato"] or min_i < 0:
            continue

        ids_fam = [str(f["id_familiare"]) for f in (ev.get("impegni_familiari") or []) if f.get("id_familiare")]
        nomi_lista = [_STATO["cache_famiglia"].get(fid, "") for fid in ids_fam if _STATO["cache_famiglia"].get(fid)]
        chi_testo = ", ".join(nomi_lista) if nomi_lista else "Tutta la famiglia"
        nome_cat = _STATO["cache_categorie"].get(str(ev.get("id_categoria")), "IMPEGNO")
        ora_i_vis = str(ora_i_str)[:5]
        ora_f_vis = str(ora_f_str)[:5] if ora_f_str else f"{(min_f // 60):02d}:{(min_f % 60):02d}"
        riga_note = f"\n📝 {note}" if note else ""

        # 1. Notifica 15 minuti prima dell'INIZIO (scatta tra -15 e -1 min dall'inizio, una sola volta)
        min_mancanti_inizio = min_i - minuti_ora
        chiave_start = f"{oggi_str}|{id_ev}|start15"
        if 0 < min_mancanti_inizio <= 15 and chiave_start not in _STATO["notifiche_inviate"]:
            _STATO["notifiche_inviate"].add(chiave_start)
            nuove_inviate = True
            notifiche_da_inviare.append({
                "title": f"⏰ Tra {min_mancanti_inizio} min ({ora_i_vis}) • {chi_testo}",
                "body": f"📌 [{nome_cat}] {titolo}\n🕒 Orario: {ora_i_vis} - {ora_f_vis}{riga_note}",
                "tag": f"start_{id_ev}_{oggi_str}"
            })

        # 2. Notifica 15 minuti prima della FINE (scatta tra -15 e -1 min dalla fine, una sola volta)
        if min_f >= 0:
            min_mancanti_fine = min_f - minuti_ora
            chiave_end = f"{oggi_str}|{id_ev}|end15"
            if 0 < min_mancanti_fine <= 15 and chiave_end not in _STATO["notifiche_inviate"]:
                _STATO["notifiche_inviate"].add(chiave_end)
                nuove_inviate = True
                notifiche_da_inviare.append({
                    "title": f"🏁 Termina tra {min_mancanti_fine} min ({ora_f_vis}) • {chi_testo}",
                    "body": f"📌 [{nome_cat}] {titolo}\n🕒 Fine prevista: ore {ora_f_vis}{riga_note}",
                    "tag": f"end_{id_ev}_{oggi_str}"
                })

    _STATO["residui_attuali"] = residui

    if nuove_inviate:
        threading.Thread(target=_salva_stato_vapid_db, daemon=True).start()

    for notif in notifiche_da_inviare:
        notif["badge_count"] = residui
        _invia_push_a_tutti(notif)

    # Aggiornamento badge sull'icona quando cambia il numero di impegni residui
    if residui != _STATO["ultimo_residui_push"]:
        precedente = _STATO["ultimo_residui_push"]
        _STATO["ultimo_residui_push"] = residui
        if not notifiche_da_inviare and precedente != -1:
            _invia_push_a_tutti({
                "badge_only": True,
                "badge_count": residui,
                "title": f"📅 Impegni residui di oggi: {residui}" if residui > 0 else "✅ Tutti gli impegni di oggi completati",
                "body": "Contatore icona aggiornato." if residui > 0 else "Nessun altro impegno residuo per oggi.",
                "tag": "elf-badge-sync"
            })


def _loop_scheduler():
    _carica_o_inizializza_config_db()
    while True:
        try:
            _controlla_impegni_oggi()
            gc.collect()
        except Exception as ex:
            print(f"[NOTIFICHE] Errore loop: {ex}", flush=True)
        _STATO["evento_ricalcolo"].wait(timeout=30)
        _STATO["evento_ricalcolo"].clear()


SW_JS_CODE = """
// ELF Push & Badge Service Worker v3
self.addEventListener('install', (e) => { self.skipWaiting(); });

self.addEventListener('activate', (e) => {
    e.waitUntil((async () => {
        await self.clients.claim();
        try {
            const keys = await caches.keys();
            for (const k of keys) {
                const c = await caches.open(k);
                await c.delete('/');
                await c.delete('/index.html');
                await c.delete(self.location.origin + '/');
                await c.delete(self.location.origin + '/index.html');
            }
        } catch (err) {}
    })());
});

self.addEventListener('push', (event) => {
    if (!event.data) return;
    let data = {};
    try { data = event.data.json(); } catch (e) { return; }

    const badgeCount = (typeof data.badge_count === 'number') ? data.badge_count : null;
    const badgePromise = (badgeCount !== null && 'setAppBadge' in self.navigator)
        ? (badgeCount > 0 ? self.navigator.setAppBadge(badgeCount).catch(()=>{}) : self.navigator.clearAppBadge().catch(()=>{}))
        : Promise.resolve();

    const notifyClients = self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
        clients.forEach((c) => c.postMessage({ type: 'ELF_BADGE', badge_count: badgeCount }));
    });

    if (data.badge_only) {
        const silentOpts = {
            body: data.body || '',
            icon: '/icons/icon-192.png',
            badge: '/icons/icon-192.png',
            tag: 'elf-badge-sync',
            silent: true,
            renotify: false,
            data: { url: '/' }
        };
        event.waitUntil(Promise.all([
            badgePromise,
            notifyClients,
            self.registration.showNotification(data.title || 'ELF', silentOpts).then(() => {
                if (badgeCount === 0) {
                    return self.registration.getNotifications({ tag: 'elf-badge-sync' }).then((ns) => ns.forEach((n) => n.close()));
                }
            }).catch(()=>{})
        ]));
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
        data: { url: '/' }
    };

    event.waitUntil(Promise.all([
        badgePromise,
        notifyClients,
        self.registration.showNotification(data.title || 'Impegno ELF', options)
    ]));
});

self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    event.waitUntil(
        self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
            for (const client of clientList) {
                if ('focus' in client) return client.focus();
            }
            if (self.clients.openWindow) return self.clients.openWindow('/');
        })
    );
});
"""

CLIENT_JS_CODE = """
(function() {
    if (window.__elfNotifInit) return;
    window.__elfNotifInit = true;

    function urlBase64ToUint8Array(base64String) {
        const padding = '='.repeat((4 - base64String.length % 4) % 4);
        const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
        const rawData = window.atob(base64);
        return Uint8Array.from([...rawData].map((c) => c.charCodeAt(0)));
    }

    function arrayBufferToBase64Url(buffer) {
        if (!buffer) return '';
        const bytes = new Uint8Array(buffer);
        let binary = '';
        for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
        return window.btoa(binary).replace(/\\+/g, '-').replace(/\\//g, '_').replace(/=+$/, '');
    }

    function aggiornaBadgeLocale(n) {
        if (typeof n !== 'number') return;
        if ('setAppBadge' in navigator) {
            if (n > 0) navigator.setAppBadge(n).catch(()=>{});
            else navigator.clearAppBadge().catch(()=>{});
        }
    }

    async function sincronizzaSottoscrizione EBadge() {
        try {
            const r = await fetch('/api/notifiche/status', { cache: 'no-store' });
            const cfg = await r.json();
            aggiornaBadgeLocale(cfg.residui);

            if (!('serviceWorker' in navigator) || !('PushManager' in window) || !cfg.vapid_public) return;

            // Registra SW dedicato sullo scope /notifiche-push/ così Flet non lo sovrascrive mai
            const reg = await navigator.serviceWorker.register('/sw-notifiche.js', { scope: '/notifiche-push/' });
            await navigator.serviceWorker.ready;

            // Se l'app sul telefono ha già il permesso notifiche attivo, si auto-registra in background!
            if ('Notification' in window && Notification.permission === 'granted') {
                let sub = await reg.pushManager.getSubscription();
                if (sub && sub.options && sub.options.applicationServerKey) {
                    const currentKey = arrayBufferToBase64Url(sub.options.applicationServerKey);
                    if (currentKey && currentKey !== cfg.vapid_public.replace(/=+$/, '')) {
                        try { await sub.unsubscribe(); } catch (e) {}
                        sub = null;
                    }
                }
                if (!sub) {
                    sub = await reg.pushManager.subscribe({
                        userVisibleOnly: true,
                        applicationServerKey: urlBase64ToUint8Array(cfg.vapid_public)
                    });
                }
                if (sub && (!cfg.attive || cfg.dispositivi === 0)) {
                    await fetch('/api/notifiche/subscribe', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ sub: sub, silent_sync: true })
                    });
                }
            }
        } catch (e) {}
    }

    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.addEventListener('message', (ev) => {
            if (ev.data && ev.data.type === 'ELF_BADGE') {
                aggiornaBadgeLocale(ev.data.badge_count);
            }
        });
    }

    sincronizzaSottoscrizioneEBadge();
    setInterval(sincronizzaSottoscrizioneEBadge, 30000);
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) sincronizzaSottoscrizioneEBadge();
    });
})();
"""

# Rimuovo lo spazio nel nome funzione JS sopra:
CLIENT_JS_CODE = CLIENT_JS_CODE.replace("sincronizzaSottoscrizione EBadge", "sincronizzaSottoscrizioneEBadge")

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
.err { color: #C62828; font-weight: bold; }
</style>
</head>
<body>
<div class="card">
    <h3 style="margin-top:0;color:#0D47A1;">🔔 Notifiche & Badge ELF</h3>
    <p id="status" style="font-size:14px;color:#444;"> Attivazione automatica in corso...</p>
    <button id="btn" onclick="attivaTutto()">📲 ATTIVA NOTIFICHE E BADGE</button>
</div>
<script>
function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const rawData = window.atob(base64);
    return Uint8Array.from([...rawData].map((c) => c.charCodeAt(0)));
}

async function attivaTutto() {
    const st = document.getElementById('status');
    const btn = document.getElementById('btn');
    btn.disabled = true;
    st.className = "";
    st.innerText = "⏳ Registrazione notifiche push e badge sul telefono...";
    try {
        const perm = await Notification.requestPermission();
        if (perm !== 'granted') {
            st.className = "err";
            st.innerText = "⚠️ Permesso notifiche negato nelle impostazioni del telefono.";
            btn.disabled = false;
            return;
        }
        const reg = await navigator.serviceWorker.register('/sw-notifiche.js', { scope: '/notifiche-push/' });
        await navigator.serviceWorker.ready;

        const cfgResp = await fetch('/api/notifiche/status', { cache: 'no-store' });
        const cfg = await cfgResp.json();

        // Rimuove eventuale iscrizione precedente con chiave diversa
        const oldSub = await reg.pushManager.getSubscription();
        if (oldSub) {
            try { await oldSub.unsubscribe(); } catch (e) {}
        }

        const sub = await reg.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: urlBase64ToUint8Array(cfg.vapid_public)
        });

        const resSub = await fetch('/api/notifiche/subscribe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ sub: sub, send_test: true })
        });
        const out = await resSub.json();

        if ('setAppBadge' in navigator) {
            if (out.residui > 0) await navigator.setAppBadge(out.residui).catch(()=>{});
            else await navigator.clearAppBadge().catch(()=>{});
        }
        st.className = "ok";
        st.innerText = "✅ Notifiche e Badge attivati! Ritorno all'app...";
        setTimeout(() => { window.location.replace("/"); }, 900);
    } catch (e) {
        st.className = "err";
        st.innerText = "Errore attivazione: " + e.message;
        btn.disabled = false;
    }
}

window.addEventListener('load', () => {
    if ('Notification' in window && Notification.permission === 'granted') {
        attivaTutto();
    } else {
        document.getElementById('status').innerText = "Premi il pulsante qui sotto per attivare le notifiche 15 min prima di inizio e fine impegno e il contatore sull'icona.";
    }
});
</script>
</body>
</html>
"""


def collega_api_fastapi(app: fastapi.FastAPI, supabase_client):
    if supabase_client is not None:
        _STATO["supabase"] = supabase_client

    # Carica SUBITO chiavi VAPID e sottoscrizioni da Supabase prima ancora che la pagina venga aperta
    _carica_o_inizializza_config_db()

    with _LOCK:
        if not _STATO["scheduler_avviato"] and _STATO["supabase"] is not None:
            _STATO["scheduler_avviato"] = True
            threading.Thread(target=_loop_scheduler, daemon=True).start()

    @app.get("/sw-notifiche.js")
    async def get_sw():
        return Response(
            content=SW_JS_CODE,
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-store, no-cache, must-revalidate"}
        )

    @app.get("/api/notifiche/client.js")
    async def get_client_js():
        return Response(
            content=CLIENT_JS_CODE,
            media_type="application/javascript",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"}
        )

    @app.get("/api/notifiche/pagina-attiva")
    async def get_pagina_attiva():
        return HTMLResponse(
            content=PAGINA_ATTIVA_HTML,
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"}
        )

    @app.get("/api/notifiche/status")
    async def get_status():
        return JSONResponse({
            "attive": is_notifiche_attive(),
            "silenziato": _STATO["silenziato"],
            "dispositivi": len(_STATO["subscriptions"]),
            "residui": _STATO["residui_attuali"],
            "vapid_public": _STATO["vapid_public"]
        })

    @app.post("/api/notifiche/subscribe")
    async def post_subscribe(req: fastapi.Request):
        payload = await req.json()
        sub_obj = payload.get("sub") if isinstance(payload, dict) and "sub" in payload else payload
        send_test = bool(payload.get("send_test")) if isinstance(payload, dict) else False

        ep = sub_obj.get("endpoint") if isinstance(sub_obj, dict) else None
        if ep:
            with _LOCK:
                esistente = _STATO["subscriptions"].get(ep)
                id_db = esistente.get("id_db") if esistente else None
                if not id_db and _STATO["supabase"]:
                    try:
                        # Verifica se l'endpoint è già presente su Supabase
                        res_ex = _STATO["supabase"].table("menu_giornalieri").select("id, subcategoria").eq("data_menu", "1970-01-01").eq("categoria", "SYS_SUB").execute()
                        for r_s in (res_ex.data or []):
                            try:
                                if json.loads(r_s.get("subcategoria") or "{}").get("endpoint") == ep:
                                    id_db = r_s["id"]
                                    break
                            except Exception:
                                pass
                        if id_db:
                            _STATO["supabase"].table("menu_giornalieri").update({"subcategoria": json.dumps(sub_obj)}).eq("id", id_db).execute()
                        else:
                            res_ins = _STATO["supabase"].table("menu_giornalieri").insert({
                                "data_menu": "1970-01-01",
                                "categoria": "SYS_SUB",
                                "subcategoria": json.dumps(sub_obj),
                                "quantita": 0,
                                "unita_misura": "pz",
                                "preso": False
                            }).execute()
                            if res_ins.data:
                                id_db = res_ins.data[0]["id"]
                        print(f"[NOTIFICHE] Dispositivo salvato su Supabase (ID: {id_db})", flush=True)
                    except Exception as ex:
                        print(f"[NOTIFICHE] Errore salvataggio SYS_SUB su Supabase: {ex}", flush=True)
                _STATO["subscriptions"][ep] = {"id_db": id_db, "sub": sub_obj}

            imposta_silenzioso(False)
            _controlla_impegni_oggi()

            if send_test:
                residui = _STATO["residui_attuali"]
                threading.Thread(
                    target=_invia_push_singolo,
                    args=(sub_obj, {
                        "title": "✅ Notifiche ELF Attive",
                        "body": f"📅 Impegni residui di oggi: {residui}\nRiceverai un avviso 15 min prima di inizio e fine impegno.",
                        "badge_count": residui,
                        "tag": "elf-welcome-test"
                    }),
                    daemon=True
                ).start()

        return JSONResponse({"ok": True, "residui": _STATO["residui_attuali"]})

    @app.middleware("http")
    async def inietta_script_notifiche(request: fastapi.Request, call_next):
        path = request.url.path
        if path in ["/", "/index.html", "/flutter_service_worker.js"]:
            # Rimuove gli header di cache condizionale così i telefoni con l'app già installata
            # ricevono sempre il file aggiornato (200 OK) invece di 304 Not Modified
             request.scope["headers"] = [
                (k, v) for k, v in request.scope.get("headers", [])
                if k.lower() not in (b"if-none-match", b"if-modified-since", b"accept-encoding")
            ]

        response = await call_next(request)

        if path == "/flutter_service_worker.js" and response.status_code == 200:
            body = b""
            async for chunk in response.body_iterator:
                body += chunk
            prefisso = b"// v-elf-push-3\ntry { importScripts('/sw-notifiche.js'); } catch(e) {}\n"
            nuovo_body = prefisso + body
            headers = {k: v for k, v in response.headers.items() if k.lower() not in ("content-length", "etag", "last-modified", "content-encoding")}
            headers["content-length"] = str(len(nuovo_body))
            headers["cache-control"] = "no-store, no-cache, must-revalidate"
            return Response(content=nuovo_body, status_code=200, headers=headers, media_type="application/javascript")

        if path in ["/", "/index.html"] and response.status_code == 200 and "text/html" in response.headers.get("content-type", ""):
            body = b""
            async for chunk in response.body_iterator:
                body += chunk
            tag_script = b'<script src="/api/notifiche/client.js" defer></script></body>'
            if b"/api/notifiche/client.js" not in body and b"</body>" in body:
                body = body.replace(b"</body>", tag_script, 1)
            headers = {k: v for k, v in response.headers.items() if k.lower() not in ("content-length", "etag", "last-modified", "content-encoding")}
            headers["content-length"] = str(len(body))
            headers["cache-control"] = "no-store, no-cache, must-revalidate"
            return Response(content=body, status_code=200, headers=headers, media_type="text/html")

        return response
