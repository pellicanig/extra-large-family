import os
import gc
import json
import time
import base64
import hashlib
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
    "inizializzato": False,
    "vapid_public": "",
    "vapid_private": "",
    "subscriptions": {},        # endpoint -> {"id_db": ..., "sub": ..., "attivo": True, "ua_hash": "..."}
    "stato_client_ua": {},      # ua_hash -> bool (True se questo specifico dispositivo ha notifiche attive)
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


def _hash_ua(user_agent: str) -> str:
    ua_clean = (user_agent or "").strip().lower()
    if not ua_clean:
        return ""
    return hashlib.sha256(ua_clean.encode("utf-8")).hexdigest()[:16]


def _pulisci_chiave_str(val: str) -> str:
    return (val or "").strip().strip('"').strip("'").strip().rstrip("=")


def _valida_o_deriva_coppia_vapid(pub_raw: str, priv_raw: str):
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
        print(f"[NOTIFICHE] Errore derivazione chiave VAPID ({ex}), uso valore grezzo.", flush=True)
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
                "preso": False
            }).eq("id", res.data[0]["id"]).execute()
        else:
            sb.table("menu_giornalieri").insert({
                "data_menu": "1970-01-01",
                "categoria": "SYS_VAPID",
                "subcategoria": payload_vapid,
                "quantita": 0,
                "unita_misura": "pz",
                "preso": False
            }).execute()
    except Exception as ex:
        print(f"[NOTIFICHE] Errore salvataggio SYS_VAPID: {ex}", flush=True)


def _ricostruisci_mappa_ua():
    mappa = {}
    for info in _STATO["subscriptions"].values():
        uah = info.get("ua_hash")
        if uah and info.get("attivo", True):
            mappa[uah] = True
    _STATO["stato_client_ua"].update(mappa)


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
                            raw_obj = json.loads(sub_txt)
                            sub_obj = raw_obj.get("sub") if ("sub" in raw_obj and isinstance(raw_obj["sub"], dict)) else raw_obj
                            ua_h = raw_obj.get("ua_hash", "") if isinstance(raw_obj, dict) else ""
                            ep = sub_obj.get("endpoint") if isinstance(sub_obj, dict) else None
                            # In SYS_SUB il campo booleano 'preso' indica se il singolo dispositivo è silenziato (True = silenziato)
                            is_attivo = not bool(r.get("preso"))
                            if ep:
                                _STATO["subscriptions"][ep] = {
                                    "id_db": r["id"],
                                    "sub": sub_obj,
                                    "attivo": is_attivo,
                                    "ua_hash": ua_h
                                }
                        except Exception:
                            pass
                _ricostruisci_mappa_ua()
            except Exception as ex:
                print(f"[NOTIFICHE] Errore lettura config DB: {ex}", flush=True)

        if not (_STATO["vapid_public"] and _STATO["vapid_private"]):
            pub_k, priv_k = _genera_chiavi_vapid()
            _STATO["vapid_public"] = pub_k
            _STATO["vapid_private"] = priv_k

        _STATO["inizializzato"] = True
        print(f"[NOTIFICHE] Inizializzato. Dispositivi totali: {len(_STATO['subscriptions'])}", flush=True)

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


def is_notifiche_attive(page=None) -> bool:
    """
    Verifica lo stato delle notifiche in modo INDIPENDENTE per il singolo dispositivo (page).
    Se il dispositivo non ha autorizzato le notifiche, restituisce sempre False.
    """
    if not _STATO["inizializzato"]:
        _carica_o_inizializza_config_db()
    if page is not None:
        ua = getattr(page, "client_user_agent", "") or ""
        uah = _hash_ua(ua)
        if uah and uah in _STATO["stato_client_ua"]:
            return bool(_STATO["stato_client_ua"][uah])
        if uah:
            for info in _STATO["subscriptions"].values():
                if info.get("ua_hash") == uah:
                    return bool(info.get("attivo", True))
        return False
    return any(info.get("attivo", True) for info in _STATO["subscriptions"].values())


def ha_dispositivi_registrati(page=None) -> bool:
    if not _STATO["inizializzato"]:
        _carica_o_inizializza_config_db()
    if page is not None:
        ua = getattr(page, "client_user_agent", "") or ""
        uah = _hash_ua(ua)
        if uah:
            return any(info.get("ua_hash") == uah for info in _STATO["subscriptions"].values())
        return False
    return len(_STATO["subscriptions"]) > 0


def imposta_silenzioso(valore: bool, page=None):
    """Silenzia o riattiva SOLO il dispositivo corrente (senza toccare gli altri telefoni)."""
    sb = _STATO["supabase"]
    uah_target = ""
    if page is not None:
        ua = getattr(page, "client_user_agent", "") or ""
        uah_target = _hash_ua(ua)

    nuovo_attivo = not bool(valore)
    ids_da_aggiornare = []

    with _LOCK:
        for ep, info in _STATO["subscriptions"].items():
            if not uah_target or info.get("ua_hash") == uah_target:
                info["attivo"] = nuovo_attivo
                if info.get("id_db"):
                    ids_da_aggiornare.append(info["id_db"])
        if uah_target:
            _STATO["stato_client_ua"][uah_target] = nuovo_attivo

    if sb and ids_da_aggiornare:
        def _w():
            for id_riga in ids_da_aggiornare:
                try:
                    sb.table("menu_giornalieri").update({"preso": bool(valore)}).eq("id", id_riga).execute()
                except Exception:
                    pass
        threading.Thread(target=_w, daemon=True).start()

    _aggiorna_ui_pagine()
    if nuovo_attivo:
        forza_ricalcolo_impegni()


def forza_ricalcolo_impegni():
    _STATO["ultimo_sync_anagrafica"] = 0
    _STATO["evento_ricalcolo"].set()
    threading.Thread(target=_controlla_impegni_oggi, daemon=True).start()


def _invia_push_singolo(sub_info: dict, payload_dict: dict) -> tuple[int, str]:
    if not webpush:
        return 500, "Libreria pywebpush non installata"
    if not _STATO["vapid_private"]:
        return 500, "Chiave VAPID_PRIVATE_KEY mancante"
    try:
        ep = sub_info.get("endpoint", "")
        parsed = urllib.parse.urlparse(ep)
        aud = f"{parsed.scheme}://{parsed.netloc}" if (parsed.scheme and parsed.netloc) else ""
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
        return 201, "OK"
    except WebPushException as ex:
        code = ex.response.status_code if ex.response is not None else 500
        body_err = ""
        if ex.response is not None and hasattr(ex.response, "text"):
            body_err = (ex.response.text or "")[:140]
        print(f"[NOTIFICHE] WebPushException ({code}): {ex} | {body_err}", flush=True)
        return code, f"HTTP {code}: {body_err or str(ex)}"
    except Exception as ex:
        print(f"[NOTIFICHE] Errore invio push: {ex}", flush=True)
        return 500, str(ex)


def _invia_push_a_tutti(payload_dict: dict):
    if not webpush or not _STATO["vapid_private"] or not _STATO["subscriptions"]:
        return

    da_rimuovere = []
    with _LOCK:
        items = list(_STATO["subscriptions"].items())

    for ep, info in items:
        if not info.get("attivo", True) and not payload_dict.get("badge_only"):
            continue
        code, _ = _invia_push_singolo(info["sub"], payload_dict)
        if code in [401, 403, 404, 410]:
            da_rimuovere.append((ep, info.get("id_db"), info.get("ua_hash")))

    if da_rimuovere:
        with _LOCK:
            for ep, id_db, uah in da_rimuovere:
                _STATO["subscriptions"].pop(ep, None)
                if uah:
                    _STATO["stato_client_ua"].pop(uah, None)
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

        fine_effettiva = min_f if min_f >= 0 else 1439
        if fine_effettiva > minuti_ora:
            residui += 1

        if min_i < 0:
            continue

        ids_fam = [str(f["id_familiare"]) for f in (ev.get("impegni_familiari") or []) if f.get("id_familiare")]
        nomi_lista = [_STATO["cache_famiglia"].get(fid, "") for fid in ids_fam if _STATO["cache_famiglia"].get(fid)]
        chi_testo = ", ".join(nomi_lista) if nomi_lista else "Tutta la famiglia"
        nome_cat = _STATO["cache_categorie"].get(str(ev.get("id_categoria")), "IMPEGNO")
        ora_i_vis = str(ora_i_str)[:5]
        ora_f_vis = str(ora_f_str)[:5] if ora_f_str else f"{(min_f // 60):02d}:{(min_f % 60):02d}"
        riga_note = f"\n📝 {note}" if note else ""

        # 1. Notifica 15 minuti prima dell'INIZIO
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

        # 2. Notifica 15 minuti prima della FINE
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

    if residui != _STATO["ultimo_residui_push"]:
        _STATO["ultimo_residui_push"] = residui
        if not notifiche_da_inviare:
            _invia_push_a_tutti({
                "badge_only": True,
                "badge_count": residui
            })


def _loop_scheduler():
    _carica_o_inizializza_config_db()
    while True:
        try:
            _controlla_impegni_oggi()
            gc.collect()
        except Exception as ex:
            print(f"[NOTIFICHE] Errore loop: {ex}", flush=True)
        _STATO["evento_ricalcolo"].wait(timeout=15)
        _STATO["evento_ricalcolo"].clear()


SW_PUSH_HANDLERS_JS = """
self.addEventListener('push', (event) => {
    let data = {};
    if (event.data) {
        try { data = event.data.json(); } catch (e) { data = { title: 'ELF', body: event.data.text() }; }
    }

    const badgeCount = (typeof data.badge_count === 'number') ? data.badge_count : null;
    const badgePromise = (badgeCount !== null && self.navigator && 'setAppBadge' in self.navigator)
        ? (badgeCount > 0 ? self.navigator.setAppBadge(badgeCount).catch(()=>{}) : self.navigator.clearAppBadge().catch(()=>{}))
        : Promise.resolve();

    const notifyClients = self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
        clients.forEach((c) => c.postMessage({ type: 'ELF_BADGE', badge_count: badgeCount }));
    }).catch(()=>{});

    if (data.badge_only && !data.title) {
        event.waitUntil(Promise.all([badgePromise, notifyClients]));
        return;
    }

    const title = data.title || 'Impegno ELF';
    const options = {
        body: data.body || '',
        icon: '/icons/Icon-192.png',
        vibrate: [250, 120, 250, 120, 400],
        tag: data.tag || ('elf-' + Date.now()),
        renotify: true,
        data: { url: '/' }
    };

    event.waitUntil(Promise.all([
        badgePromise,
        notifyClients,
        self.registration.showNotification(title, options)
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

SW_JS_CODE = """
self.addEventListener('install', (e) => { self.skipWaiting(); });
self.addEventListener('activate', (e) => { e.waitUntil(self.clients.claim()); });
""" + SW_PUSH_HANDLERS_JS

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

    async function syncRapidoBadge() {
        try {
            const r = await fetch('/api/notifiche/status', { cache: 'no-store' });
            const d = await r.json();
            aggiornaBadgeLocale(d.residui);
        } catch (e) {}
    }

    async function getDedicatedSwReg() {
        const reg = await navigator.serviceWorker.register('/sw-notifiche.js', { scope: '/' });
        let sw = reg.installing || reg.waiting || reg.active;
        if (sw && sw.state !== 'activated') {
            await new Promise((resolve) => {
                const t = setTimeout(resolve, 2500);
                sw.addEventListener('statechange', () => {
                    if (sw.state === 'activated') { clearTimeout(t); resolve(); }
                });
            });
        }
        return reg;
    }

    async function sincronizzaDispositivoEBadge() {
        try {
            const hasPerm = ('Notification' in window && Notification.permission === 'granted');
            let currentEndpoint = '';

            if ('serviceWorker' in navigator && 'PushManager' in window && hasPerm) {
                const reg = await getDedicatedSwReg();
                let sub = await reg.pushManager.getSubscription();
                if (sub) currentEndpoint = sub.endpoint || '';
            }

            const r = await fetch('/api/notifiche/status?endpoint=' + encodeURIComponent(currentEndpoint) + '&perm=' + (hasPerm ? '1' : '0'), { cache: 'no-store' });
            const cfg = await r.json();
            aggiornaBadgeLocale(cfg.residui);

            if (hasPerm && 'serviceWorker' in navigator && 'PushManager' in window && cfg.vapid_public) {
                const reg = await getDedicatedSwReg();
                let sub = await reg.pushManager.getSubscription();
                if (sub && sub.options && sub.options.applicationServerKey) {
                    const currentKey = arrayBufferToBase64Url(sub.options.applicationServerKey);
                    if (currentKey && currentKey !== cfg.vapid_public.replace(/=+$/, '')) {
                        try { await sub.unsubscribe(); } catch (e) {}
                        sub = null;
                    }
                }
                if (!sub && !cfg.dispositivo_silenziato) {
                    sub = await reg.pushManager.subscribe({
                        userVisibleOnly: true,
                        applicationServerKey: urlBase64ToUint8Array(cfg.vapid_public)
                    });
                }
                if (sub && !cfg.dispositivo_registrato) {
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

    sincronizzaDispositivoEBadge();
    setInterval(syncRapidoBadge, 3000);
    setInterval(sincronizzaDispositivoEBadge, 30000);

    // Aggiorna istantaneamente il badge appena tocchi un pulsante (es. Salva/Elimina) o torni alla schermata Home
    document.addEventListener('visibilitychange', syncRapidoBadge);
    window.addEventListener('pagehide', syncRapidoBadge);
    window.addEventListener('blur', syncRapidoBadge);
    document.addEventListener('pointerup', () => {
        setTimeout(syncRapidoBadge, 800);
        setTimeout(syncRapidoBadge, 2000);
    });
})();
"""

SW_JS_CODE = """
self.addEventListener('install', (e) => { self.skipWaiting(); });
self.addEventListener('activate', (e) => { e.waitUntil(self.clients.claim()); });
""" + SW_PUSH_HANDLERS_JS

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

    async function getDedicatedSwReg() {
        const reg = await navigator.serviceWorker.register('/sw-notifiche.js', { scope: '/api/notifiche/' });
        let sw = reg.installing || reg.waiting || reg.active;
        if (sw && sw.state !== 'activated') {
            await new Promise((resolve) => {
                const t = setTimeout(resolve, 2500);
                sw.addEventListener('statechange', () => {
                    if (sw.state === 'activated') { clearTimeout(t); resolve(); }
                });
            });
        }
        return reg;
    }

    async function sincronizzaDispositivoEBadge() {
        try {
            const hasPerm = ('Notification' in window && Notification.permission === 'granted');
            let currentEndpoint = '';

            if ('serviceWorker' in navigator && 'PushManager' in window && hasPerm) {
                const reg = await getDedicatedSwReg();
                let sub = await reg.pushManager.getSubscription();
                if (sub) currentEndpoint = sub.endpoint || '';
            }

            const r = await fetch('/api/notifiche/status?endpoint=' + encodeURIComponent(currentEndpoint) + '&perm=' + (hasPerm ? '1' : '0'), { cache: 'no-store' });
            const cfg = await r.json();
            aggiornaBadgeLocale(cfg.residui);

            if (hasPerm && 'serviceWorker' in navigator && 'PushManager' in window && cfg.vapid_public) {
                const reg = await getDedicatedSwReg();
                let sub = await reg.pushManager.getSubscription();
                if (sub && sub.options && sub.options.applicationServerKey) {
                    const currentKey = arrayBufferToBase64Url(sub.options.applicationServerKey);
                    if (currentKey && currentKey !== cfg.vapid_public.replace(/=+$/, '')) {
                        try { await sub.unsubscribe(); } catch (e) {}
                        sub = null;
                    }
                }
                if (!sub && !cfg.dispositivo_silenziato) {
                    sub = await reg.pushManager.subscribe({
                        userVisibleOnly: true,
                        applicationServerKey: urlBase64ToUint8Array(cfg.vapid_public)
                    });
                }
                if (sub && !cfg.dispositivo_registrato) {
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

    sincronizzaDispositivoEBadge();
    setInterval(sincronizzaDispositivoEBadge, 25000);
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) sincronizzaDispositivoEBadge();
    });
})();
"""

PAGINA_ATTIVA_HTML = """<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gestione Notifiche Dispositivo - ELF</title>
<style>
body { font-family: system-ui, sans-serif; background: #EEF2F6; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 15px; box-sizing: border-box; }
.card { background: white; padding: 22px; border-radius: 14px; box-shadow: 0 4px 16px rgba(0,0,0,0.12); max-width: 370px; width: 100%; text-align: center; }
.badge-stato { display: inline-block; padding: 6px 12px; border-radius: 20px; font-size: 13px; font-weight: bold; margin-bottom: 10px; }
.stato-on { background: #E8F5E9; color: #1B5E20; border: 1px solid #81C784; }
.stato-off { background: #FFEBEE; color: #B71C1C; border: 1px solid #E57373; }
button, .btn-link { display: block; box-sizing: border-box; text-decoration: none; color: white; border: none; padding: 13px 16px; border-radius: 8px; font-size: 14px; font-weight: bold; width: 100%; cursor: pointer; margin-top: 10px; }
.btn-attiva { background: #0D47A1; }
.btn-test { background: #2E7D32; }
.btn-mute { background: #C62828; }
.btn-home { background: #546E7A; margin-top: 16px; }
.ok { color: #1B5E20; font-weight: bold; background: #E8F5E9; padding: 10px; border-radius: 8px; }
.err { color: #B71C1C; font-weight: bold; background: #FFEBEE; padding: 10px; border-radius: 8px; }
</style>
</head>
<body>
<div class="card">
    <h3 style="margin-top:0;margin-bottom:8px;color:#0D47A1;">🔔 Notifiche di questo Dispositivo</h3>
    <div id="badgeStato" class="badge-stato stato-off">Verifica stato...</div>
    <p id="status" style="font-size:13px;color:#444;line-height:1.4;">Controllo autorizzazioni del telefono in corso...</p>
    <button id="btnAttiva" class="btn-attiva" onclick="attivaDispositivo()">📲 ATTIVA E INVIA NOTIFICA DI PROVA</button>
    <button id="btnTest" class="btn-test" style="display:none;" onclick="inviaTestDispositivo()">🔔 INVIA NOTIFICA DI PROVA ORA</button>
    <button id="btnMute" class="btn-mute" style="display:none;" onclick="disattivaDispositivo()">🔕 SILENZIA SU QUESTO TELEFONO</button>
    <a class="btn-link btn-home" href="/">⬅️ TORNA ALL'APP</a>
</div>
<script>
function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const rawData = window.atob(base64);
    return Uint8Array.from([...rawData].map((c) => c.charCodeAt(0)));
}

async function pulisciCacheAppInstallata() {
    if (!('caches' in window)) return;
    try {
        const keys = await caches.keys();
        for (const k of keys) {
            const c = await caches.open(k);
            await c.delete('/');
            await c.delete('/index.html');
            await c.delete(window.location.origin + '/');
            await c.delete(window.location.origin + '/index.html');
        }
    } catch (e) {}
}

async function getSwReg() {
    const reg = await navigator.serviceWorker.register('/sw-notifiche.js', { scope: '/api/notifiche/' });
    let sw = reg.installing || reg.waiting || reg.active;
    if (sw && sw.state !== 'activated') {
        await new Promise((resolve) => {
            const timer = setTimeout(resolve, 2500);
            sw.addEventListener('statechange', () => {
                if (sw.state === 'activated') { clearTimeout(timer); resolve(); }
            });
        });
    }
    return reg;
}

async function aggiornaStatoUI() {
    const badge = document.getElementById('badgeStato');
    const st = document.getElementById('status');
    const btnAttiva = document.getElementById('btnAttiva');
    const btnTest = document.getElementById('btnTest');
    const btnMute = document.getElementById('btnMute');

    await pulisciCacheAppInstallata();

    if (!('Notification' in window) || !('serviceWorker' in navigator) || !('PushManager' in window)) {
        badge.className = 'badge-stato stato-off';
        badge.innerText = '❌ NON SUPPORTATE';
        st.innerText = 'Per ricevere notifiche su smartphone, apri l\\'app dall\\'icona installata sulla schermata Home.';
        btnAttiva.style.display = 'none';
        return;
    }

    const perm = Notification.permission;
    let sub = null;
    try {
        const reg = await getSwReg();
        sub = await reg.pushManager.getSubscription();
    } catch (e) {}

    const ep = sub ? sub.endpoint : '';
    const r = await fetch('/api/notifiche/status?endpoint=' + encodeURIComponent(ep) + '&perm=' + (perm === 'granted' ? '1' : '0'), { cache: 'no-store' });
    const cfg = await r.json();

    if ('setAppBadge' in navigator) {
        if (cfg.residui > 0) navigator.setAppBadge(cfg.residui).catch(()=>{});
        else navigator.clearAppBadge().catch(()=>{});
    }

    if (perm === 'granted' && sub && cfg.dispositivo_attivo) {
        badge.className = 'badge-stato stato-on';
        badge.innerText = '✅ ATTIVE SU QUESTO DISPOSITIVO';
        st.innerText = 'Impegni residui di oggi: ' + cfg.residui + '. Riceverai le notifiche 15 min prima di inizio e fine impegno.';
        btnAttiva.innerText = '🔄 RI-SINCRONIZZA QUESTO DISPOSITIVO';
        btnTest.style.display = 'block';
        btnMute.style.display = 'block';
    } else {
        badge.className = 'badge-stato stato-off';
        badge.innerText = '🔕 NON ATTIVE SU QUESTO DISPOSITIVO';
        st.innerText = 'Premi il pulsante blu qui sotto per attivare le notifiche e il numeretto sull\\'icona su questo telefono.';
        btnAttiva.innerText = '📲 ATTIVA E INVIA NOTIFICA DI PROVA';
        btnTest.style.display = 'none';
        btnMute.style.display = 'none';
    }
}

async function attivaDispositivo() {
    const st = document.getElementById('status');
    const btnAttiva = document.getElementById('btnAttiva');
    btnAttiva.disabled = true;
    st.className = '';
    st.innerText = '⏳ Attivazione e invio notifica di prova in corso...';

    try {
        const perm = await Notification.requestPermission();
        if (perm !== 'granted') {
            st.className = 'err';
            st.innerText = '⚠️ Permesso notifiche negato sul telefono. Abilitalo nelle impostazioni dell\\'app.';
            btnAttiva.disabled = false;
            return;
        }

        const reg = await getSwReg();
        const cfgResp = await fetch('/api/notifiche/status', { cache: 'no-store' });
        const cfg = await cfgResp.json();

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

        // Notifica locale immediata dal Service Worker + Notifica Push dal Server
        try {
            await reg.showNotification('✅ Notifiche Attive su questo Telefono', {
                body: '📅 Impegni residui di oggi: ' + out.residui + '\\nAvviso impostato a 15 min prima di inizio e fine impegno.',
                icon: '/icons/Icon-192.png',
                vibrate: [200, 100, 200],
                tag: 'elf-local-check'
            });
        } catch (e) {}

        await aggiornaStatoUI();
        if (out.push_status === 201 || out.push_status === 200) {
            st.className = 'ok';
            st.innerText = '✅ Dispositivo attivo e verificato! Notifica inviata (Impegni residui oggi: ' + out.residui + ').';
        } else {
            st.className = 'err';
            st.innerText = '⚠️ Registrato, ma il server Push ha risposto: ' + (out.push_msg || out.push_status);
        }
    } catch (e) {
        st.className = 'err';
        st.innerText = '❌ Errore: ' + e.message;
    } finally {
        btnAttiva.disabled = false;
    }
}

async function inviaTestDispositivo() {
    const st = document.getElementById('status');
    st.className = '';
    st.innerText = '⏳ Invio notifica Push di prova a questo dispositivo...';
    try {
        const reg = await getSwReg();
        const sub = await reg.pushManager.getSubscription();
        if (!sub) {
            await attivaDispositivo();
            return;
        }
        const res = await fetch('/api/notifiche/subscribe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ sub: sub, send_test: true })
        });
        const out = await res.json();
        if (out.push_status === 201 || out.push_status === 200) {
            st.className = 'ok';
            st.innerText = '✅ Notifica Push inviata con successo! (Impegni residui oggi: ' + out.residui + ')';
        } else {
            st.className = 'err';
            st.innerText = '⚠️ Risposta server Push: ' + (out.push_msg || out.push_status);
        }
    } catch (e) {
        st.className = 'err';
        st.innerText = '❌ Errore test: ' + e.message;
    }
}

async function disattivaDispositivo() {
    const st = document.getElementById('status');
    st.className = '';
    st.innerText = '⏳ Silenziamento di questo dispositivo in corso...';
    try {
        const reg = await getSwReg();
        const sub = await reg.pushManager.getSubscription();
        const ep = sub ? sub.endpoint : '';
        if (sub) {
            try { await sub.unsubscribe(); } catch (e) {}
        }
        await fetch('/api/notifiche/unsubscribe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ endpoint: ep })
        });
        await aggiornaStatoUI();
        st.className = 'ok';
        st.innerText = '🔕 Notifiche silenziate solo su questo dispositivo. Gli altri telefoni restano attivi.';
    } catch (e) {
        st.className = 'err';
        st.innerText = '❌ Errore: ' + e.message;
    }
}

window.addEventListener('load', aggiornaStatoUI);
</script>
</body>
</html>
"""


def collega_api_fastapi(app: fastapi.FastAPI, supabase_client):
    if supabase_client is not None:
        _STATO["supabase"] = supabase_client

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
    async def get_status(req: fastapi.Request):
        ep = req.query_params.get("endpoint", "").strip()
        perm = req.query_params.get("perm", "")
        ua_h = _hash_ua(req.headers.get("user-agent", ""))

        info_sub = _STATO["subscriptions"].get(ep) if ep else None
        if not info_sub and ua_h:
            info_sub = next((v for v in _STATO["subscriptions"].values() if v.get("ua_hash") == ua_h), None)

        disp_reg = info_sub is not None
        disp_attivo = bool(info_sub and info_sub.get("attivo", True))
        disp_sil = bool(info_sub and not info_sub.get("attivo", True))

        if ua_h and perm in ["0", "1"]:
            nuovo_stato_ua = (perm == "1") and disp_attivo
            if _STATO["stato_client_ua"].get(ua_h) != nuovo_stato_ua:
                _STATO["stato_client_ua"][ua_h] = nuovo_stato_ua
                _aggiorna_ui_pagine()

        return JSONResponse({
            "attive": disp_attivo,
            "dispositivo_registrato": disp_reg,
            "dispositivo_attivo": disp_attivo,
            "dispositivo_silenziato": disp_sil,
            "dispositivi": len(_STATO["subscriptions"]),
            "residui": _STATO["residui_attuali"],
            "vapid_public": _STATO["vapid_public"]
        })

    @app.get("/api/notifiche/test")
    async def get_test_push():
        _controlla_impegni_oggi()
        residui = _STATO["residui_attuali"]
        ora_str = _ora_italia().strftime("%H:%M")
        _invia_push_a_tutti({
            "title": f"🔔 Test Notifica ELF ({ora_str})",
            "body": f"Impegni residui di oggi: {residui}. Le notifiche Push funzionano!",
            "badge_count": residui,
            "tag": f"elf-test-{int(time.time())}"
        })
        return JSONResponse({
            "ok": True,
            "dispositivi_registrati": len(_STATO["subscriptions"]),
            "impegni_residui_oggi": residui
        })

    @app.post("/api/notifiche/subscribe")
    async def post_subscribe(req: fastapi.Request):
        payload = await req.json()
        sub_obj = payload.get("sub") if isinstance(payload, dict) and "sub" in payload else payload
        send_test = bool(payload.get("send_test")) if isinstance(payload, dict) else False
        ua_h = _hash_ua(req.headers.get("user-agent", ""))

        ep = sub_obj.get("endpoint") if isinstance(sub_obj, dict) else None
        push_status = 200
        push_msg = "OK"

        if ep:
            record_json = json.dumps({"sub": sub_obj, "ua_hash": ua_h})
            with _LOCK:
                esistente = _STATO["subscriptions"].get(ep)
                id_db = esistente.get("id_db") if esistente else None
                if not id_db and _STATO["supabase"]:
                    try:
                        res_ex = _STATO["supabase"].table("menu_giornalieri").select("id, subcategoria").eq("data_menu", "1970-01-01").eq("categoria", "SYS_SUB").execute()
                        for r_s in (res_ex.data or []):
                            try:
                                raw_s = json.loads(r_s.get("subcategoria") or "{}")
                                s_inner = raw_s.get("sub") if "sub" in raw_s else raw_s
                                if s_inner.get("endpoint") == ep or (ua_h and raw_s.get("ua_hash") == ua_h):
                                    id_db = r_s["id"]
                                    break
                            except Exception:
                                pass
                        if id_db:
                            _STATO["supabase"].table("menu_giornalieri").update({
                                "subcategoria": record_json,
                                "preso": False
                            }).eq("id", id_db).execute()
                        else:
                            res_ins = _STATO["supabase"].table("menu_giornalieri").insert({
                                "data_menu": "1970-01-01",
                                "categoria": "SYS_SUB",
                                "subcategoria": record_json,
                                "quantita": 0,
                                "unita_misura": "pz",
                                "preso": False
                            }).execute()
                            if res_ins.data:
                                id_db = res_ins.data[0]["id"]
                    except Exception as ex:
                        print(f"[NOTIFICHE] Errore salvataggio SYS_SUB: {ex}", flush=True)

                # Rimuove vecchi endpoint dello stesso dispositivo
                if ua_h:
                    vecchi_ep = [k for k, v in _STATO["subscriptions"].items() if v.get("ua_hash") == ua_h and k != ep]
                    for v_ep in vecchi_ep:
                        _STATO["subscriptions"].pop(v_ep, None)

                _STATO["subscriptions"][ep] = {
                    "id_db": id_db,
                    "sub": sub_obj,
                    "attivo": True,
                    "ua_hash": ua_h
                }
                if ua_h:
                    _STATO["stato_client_ua"][ua_h] = True

            _controlla_impegni_oggi()
            _aggiorna_ui_pagine()

            if send_test:
                residui = _STATO["residui_attuali"]
                ora_str = _ora_italia().strftime("%H:%M")
                push_status, push_msg = _invia_push_singolo(sub_obj, {
                    "title": f"🔔 Notifica Push ELF ({ora_str})",
                    "body": f"📅 Impegni residui di oggi: {residui}\nRiceverai un avviso 15 min prima di inizio e fine impegno.",
                    "badge_count": residui,
                    "tag": f"elf-push-test-{int(time.time())}"
                })

        return JSONResponse({
            "ok": True,
            "residui": _STATO["residui_attuali"],
            "push_status": push_status,
            "push_msg": push_msg
        })

    @app.post("/api/notifiche/unsubscribe")
    async def post_unsubscribe(req: fastapi.Request):
        payload = await req.json()
        ep = (payload.get("endpoint") or "").strip() if isinstance(payload, dict) else ""
        ua_h = _hash_ua(req.headers.get("user-agent", ""))

        with _LOCK:
            da_silenziare = []
            for k, v in list(_STATO["subscriptions"].items()):
                if (ep and k == ep) or (ua_h and v.get("ua_hash") == ua_h):
                    v["attivo"] = False
                    if v.get("id_db"):
                        da_silenziare.append(v["id_db"])
            if ua_h:
                _STATO["stato_client_ua"][ua_h] = False

        if _STATO["supabase"] and da_silenziare:
            for id_r in da_silenziare:
                try:
                    _STATO["supabase"].table("menu_giornalieri").update({"preso": True}).eq("id", id_r).execute()
                except Exception:
                    pass

        _aggiorna_ui_pagine()
        return JSONResponse({"ok": True})

    @app.middleware("http")
    async def inietta_script_notifiche(request: fastapi.Request, call_next):
        path = request.url.path
        if path in ["/", "/index.html", "/flutter_service_worker.js"]:
            request.scope["headers"] = [
                (k, v) for k, v in request.scope.get("headers", [])
                if k.lower() not in (b"if-none-match", b"if-modified-since", b"accept-encoding")
            ]

        response = await call_next(request)

        if path == "/flutter_service_worker.js" and response.status_code == 200:
            body = b""
            async for chunk in response.body_iterator:
                body += chunk
            prefisso = ("// v-elf-push-5\n" + SW_PUSH_HANDLERS_JS + "\n").encode("utf-8")
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
