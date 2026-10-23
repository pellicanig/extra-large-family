import os
import gc
import json
import time
import base64
import hashlib
import asyncio
import threading
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo
import fastapi
from fastapi.responses import HTMLResponse, Response, JSONResponse

try:
    from pywebpush import webpush, WebPushException
except ImportError:
    webpush = None
    WebPushException = Exception

try:
    from supabase import create_client
except ImportError:
    create_client = None

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

TZ_ITALIA = ZoneInfo("Europe/Rome")
CAT_SUB_DB = "SYS_SUB_V2"

_STATO = {
    "supabase": None,
    "supabase_url": "",
    "supabase_key": "",
    "inizializzato": False,
    "vapid_public": "",
    "vapid_private": "",
    "subscriptions": {},        # endpoint -> {"id_db": int|None, "sub": dict, "ep_hash": str, "device_id": str}
    "hash_to_ep": {},           # ep_hash -> endpoint
    "stato_device": {},         # device_id / ep_hash -> bool
    "ultimo_ep_attivo": "",     # ultimo ep_hash visto dal middleware HTTP
    "force_unsub_devices": set(),
    "unmute_devices": set(),
    "notifiche_inviate": set(), # "YYYY-MM-DD|id_imp|HH:MM|tipo" (solo quando TUTTI i telefoni l'hanno ricevuta)
    "inviate_per_ep": {},       # chiave_notifica -> set(endpoint_che_hanno_ricevuto_201)
    "fallimenti_ep": {},        # endpoint -> conteggio errori 404/410 consecutivi
    "impegni_notificati_id": set(),
    "data_corrente": "",
    "residui_attuali": 0,
    "cache_famiglia": {},
    "cache_categorie": {},
    "ultimo_sync_anagrafica": 0.0,
    "ultimo_keepalive": 0.0,
    "url_pubblico": "",
    "scheduler_avviato": False,
    "evento_ricalcolo": threading.Event(),
    "pagine_attive": set()
}
_LOCK = threading.Lock()
_DB_LOCK = threading.Lock()


def _ora_italia() -> datetime:
    return datetime.now(TZ_ITALIA)


def _calcola_hash_ep(ep: str) -> str:
    if not ep:
        return ""
    return hashlib.sha256(ep.strip().encode("utf-8")).hexdigest()[:16]


def _calcola_minuti(orario_str) -> int:
    if not orario_str:
        return -1
    try:
        s = str(orario_str).strip()[:5]
        if ":" not in s:
            return -1
        h, m = map(int, s.split(":"))
        return h * 60 + m
    except Exception:
        return -1


def _pulisci_chiave_str(val: str) -> str:
    return (val or "").strip().strip('"').strip("'").strip().rstrip("=")


def _crea_client_supabase_dedicato(fallback_client=None):
    """Crea o rigenera un client Supabase isolato per evitare errori ConnectionState.CLOSED."""
    url = _STATO["supabase_url"] or os.environ.get("SUPABASE_URL") or (getattr(fallback_client, "supabase_url", None) if fallback_client else "")
    key = _STATO["supabase_key"] or os.environ.get("SUPABASE_KEY") or (getattr(fallback_client, "supabase_key", None) if fallback_client else "")
    if url and key:
        _STATO["supabase_url"] = str(url)
        _STATO["supabase_key"] = str(key)
        if create_client is not None:
            try:
                return create_client(str(url), str(key))
            except Exception as ex:
                print(f"[NOTIFICHE] Avviso creazione client Supabase: {ex}", flush=True)
    return fallback_client or _STATO["supabase"]


def _rinnova_client_se_chiuso(ex: Exception):
    msg = str(ex)
    if any(k in msg for k in ["ConnectionState", "SEND_HEADERS", "RECV_", "Server disconnected", "RemoteProtocolError", "LocalProtocolError", "ReadTimeout"]):
        nuovo = _crea_client_supabase_dedicato()
        if nuovo is not None:
            _STATO["supabase"] = nuovo


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
        print(f"[NOTIFICHE] Errore derivazione VAPID ({ex})", flush=True)
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
    with _LOCK:
        inviate_oggi = [k for k in _STATO["notifiche_inviate"] if k.startswith(oggi_str + "|")]
        inviate_ep_oggi = {
            k: list(v) for k, v in _STATO["inviate_per_ep"].items()
            if k.startswith(oggi_str + "|") and v
        }
        payload_vapid = json.dumps({
            "pub": _STATO["vapid_public"],
            "priv": _STATO["vapid_private"],
            "sent": inviate_oggi,
            "sent_ep": inviate_ep_oggi
        })
    with _DB_LOCK:
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
            _rinnova_client_se_chiuso(ex)
            print(f"[NOTIFICHE] Avviso salvataggio SYS_VAPID: {ex}", flush=True)


def _carica_o_inizializza_config_db():
    with _LOCK:
        if _STATO["inizializzato"] and _STATO["vapid_public"]:
            return
    sb = _STATO["supabase"]
    pub_env = _pulisci_chiave_str(os.environ.get("VAPID_PUBLIC_KEY", ""))
    priv_env = _pulisci_chiave_str(os.environ.get("VAPID_PRIVATE_KEY", ""))

    pub_init, priv_init = "", ""
    if priv_env:
        pub_init, priv_init = _valida_o_deriva_coppia_vapid(pub_env, priv_env)

    subs_loaded = {}
    hash_map = {}
    device_map = {}
    sent_loaded = set()
    sent_ep_loaded = {}

    if sb:
        with _DB_LOCK:
            try:
                res = sb.table("menu_giornalieri").select("id, categoria, subcategoria, preso").eq("data_menu", "1970-01-01").in_("categoria", ["SYS_VAPID", CAT_SUB_DB]).execute()
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
                                    sent_loaded.add(str(k_sent))
                            raw_sent_ep = dati_v.get("sent_ep") or {}
                            if isinstance(raw_sent_ep, dict):
                                for k_s, v_list in raw_sent_ep.items():
                                    if str(k_s).startswith(oggi_str + "|") and isinstance(v_list, list):
                                        sent_ep_loaded[str(k_s)] = set(v_list)
                            if not (pub_init and priv_init):
                                pub_init, priv_init = _valida_o_deriva_coppia_vapid(dati_v.get("pub", ""), dati_v.get("priv", ""))
                        except Exception:
                            pass
                    elif cat == CAT_SUB_DB and sub_txt:
                        try:
                            raw_obj = json.loads(sub_txt)
                            sub_obj = raw_obj.get("sub") if ("sub" in raw_obj and isinstance(raw_obj["sub"], dict)) else raw_obj
                            did = (raw_obj.get("device_id") or "").strip() if isinstance(raw_obj, dict) else ""
                            ep = (sub_obj.get("endpoint") or "").strip() if isinstance(sub_obj, dict) else ""
                            if ep and not bool(r.get("preso")):
                                ep_h = _calcola_hash_ep(ep)
                                subs_loaded[ep] = {
                                    "id_db": r["id"],
                                    "sub": sub_obj,
                                    "ep_hash": ep_h,
                                    "device_id": did or ep_h
                                }
                                hash_map[ep_h] = ep
                                device_map[ep_h] = True
                                if did:
                                    device_map[did] = True
                        except Exception:
                            pass
            except Exception as ex:
                _rinnova_client_se_chiuso(ex)
                print(f"[NOTIFICHE] Errore lettura config DB: {ex}", flush=True)

    if not (pub_init and priv_init):
        pub_init, priv_init = _genera_chiavi_vapid()

    with _LOCK:
        _STATO["vapid_public"] = pub_init
        _STATO["vapid_private"] = priv_init
        _STATO["notifiche_inviate"].update(sent_loaded)
        for k_s, v_set in sent_ep_loaded.items():
            _STATO["inviate_per_ep"].setdefault(k_s, set()).update(v_set)
        _STATO["subscriptions"].update(subs_loaded)
        _STATO["hash_to_ep"].update(hash_map)
        _STATO["stato_device"].update(device_map)
        _STATO["inizializzato"] = True
        print(f"[NOTIFICHE] Inizializzato. Dispositivi registrati: {len(_STATO['subscriptions'])}", flush=True)

    threading.Thread(target=_salva_stato_vapid_db, daemon=True).start()


def _aggancia_pagina(page):
    if page is None:
        return
    try:
        if getattr(page, "_elf_ep", None):
            return
        with _LOCK:
            ultimo_h = _STATO["ultimo_ep_attivo"]
            if ultimo_h and ultimo_h in _STATO["hash_to_ep"]:
                ep = _STATO["hash_to_ep"][ultimo_h]
                info = _STATO["subscriptions"].get(ep)
                page._elf_ep = ep
                page._elf_ep_hash = ultimo_h
                page._elf_did = (info.get("device_id") if info else None) or ultimo_h
                return
            if len(_STATO["subscriptions"]) == 1:
                ep, info = next(iter(_STATO["subscriptions"].items()))
                page._elf_ep = ep
                page._elf_ep_hash = info.get("ep_hash")
                page._elf_did = info.get("device_id")
    except Exception:
        pass


def registra_pagina(page):
    try:
        if not _STATO["inizializzato"]:
            _carica_o_inizializza_config_db()
        if not hasattr(page, "_elf_did"):
            page._elf_did = None
        if not hasattr(page, "_elf_ep"):
            page._elf_ep = None
        if not hasattr(page, "_elf_ep_hash"):
            page._elf_ep_hash = None
        page._elf_reg_ts = time.time()

        _aggancia_pagina(page)
        _STATO["pagine_attive"].add(page)

        if hasattr(page, "aggiorna_menu_notifiche") and callable(page.aggiorna_menu_notifiche):
            page.aggiorna_menu_notifiche()
            page.update()
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


def _associa_device_a_pagina(did: str, ep: str):
    ep_h = _calcola_hash_ep(ep) if ep else ""
    if ep_h:
        with _LOCK:
            _STATO["ultimo_ep_attivo"] = ep_h

    morti = set()
    serve_refresh = False

    for p in list(_STATO["pagine_attive"]):
        try:
            p_ep = getattr(p, "_elf_ep", None)
            p_did = getattr(p, "_elf_did", None)
            # Associa se la pagina non aveva ancora un endpoint o se appartiene a questo dispositivo
            if (ep and p_ep == ep) or (did and p_did == did) or (p_ep is None and ep):
                if ep and p_ep != ep:
                    p._elf_ep = ep
                    p._elf_ep_hash = ep_h
                    serve_refresh = True
                if did and p_did != did:
                    p._elf_did = did
                    serve_refresh = True
                stato_ora = is_notifiche_attive(p)
                if getattr(p, "_elf_ultimo_stato_ui", None) != stato_ora:
                    serve_refresh = True
        except Exception:
            morti.add(p)

    for m in morti:
        _STATO["pagine_attive"].discard(m)

    if serve_refresh:
        _aggiorna_ui_pagine()


def is_notifiche_attive(page=None) -> bool:
    if not _STATO["inizializzato"]:
        _carica_o_inizializza_config_db()

    if page is not None:
        if not getattr(page, "_elf_ep", None):
            _aggancia_pagina(page)

        ep = getattr(page, "_elf_ep", None)
        ep_h = getattr(page, "_elf_ep_hash", None)
        did = getattr(page, "_elf_did", None)

        with _LOCK:
            if ep and ep in _STATO["subscriptions"]:
                attivo = True
            elif ep_h and ep_h in _STATO["hash_to_ep"]:
                attivo = True
            elif did and did in _STATO["stato_device"]:
                attivo = bool(_STATO["stato_device"][did])
            else:
                # Se il telefono sta aprendo l'app e ci sono sottoscrizioni attive, mostra Silenzia finché client.js non smentisce
                attivo = len(_STATO["subscriptions"]) > 0

        try:
            page._elf_ultimo_stato_ui = attivo
        except Exception:
            pass
        return attivo

    return len(_STATO["subscriptions"]) > 0


def ha_dispositivi_registrati(page=None) -> bool:
    return is_notifiche_attive(page)


def _rimuovi_endpoint_specifico(ep_target: str, did_target: str, imposta_force_unsub: bool = True):
    """Rimuove ESCLUSIVAMENTE l'endpoint specifico del telefono che ha chiesto di silenziarsi, senza mai toccare gli altri telefoni."""
    sb = _STATO["supabase"]
    ids_da_eliminare = []
    ep_h = _calcola_hash_ep(ep_target) if ep_target else ""

    with _LOCK:
        if ep_target and ep_target in _STATO["subscriptions"]:
            info = _STATO["subscriptions"].pop(ep_target, None)
            if info and info.get("id_db"):
                ids_da_eliminare.append(info["id_db"])
            if ep_h:
                _STATO["hash_to_ep"].pop(ep_h, None)
                _STATO["stato_device"][ep_h] = False
        elif did_target and not ep_target:
            # Cerca solo se c'è un unico endpoint con quel did_target esatto
            for ep_k, info in list(_STATO["subscriptions"].items()):
                if info.get("device_id") == did_target or info.get("ep_hash") == did_target:
                    if info.get("id_db"):
                        ids_da_eliminare.append(info["id_db"])
                    _STATO["subscriptions"].pop(ep_k, None)
                    if info.get("ep_hash"):
                        _STATO["hash_to_ep"].pop(info["ep_hash"], None)
                    break

        if did_target:
            _STATO["stato_device"][did_target] = False
            _STATO["unmute_devices"].discard(did_target)
            if imposta_force_unsub:
                _STATO["force_unsub_devices"].add(did_target)
        if ep_h:
            _STATO["unmute_devices"].discard(ep_h)
            if imposta_force_unsub:
                _STATO["force_unsub_devices"].add(ep_h)

    _aggiorna_ui_pagine()

    if sb and ids_da_eliminare:
        def _del():
            with _DB_LOCK:
                for id_r in ids_da_eliminare:
                    try:
                        sb.table("menu_giornalieri").delete().eq("id", id_r).execute()
                    except Exception as ex:
                        _rinnova_client_se_chiuso(ex)
        threading.Thread(target=_del, daemon=True).start()


def imposta_silenzioso(valore: bool, page=None):
    did_target = getattr(page, "_elf_did", None) if page is not None else None
    ep_target = getattr(page, "_elf_ep", None) if page is not None else None
    ep_h_target = getattr(page, "_elf_ep_hash", None) if page is not None else None

    if valore:
        if ep_target or did_target or ep_h_target:
            _rimuovi_endpoint_specifico(ep_target or "", did_target or ep_h_target or "", imposta_force_unsub=True)
    else:
        with _LOCK:
            for k in (did_target, ep_h_target):
                if k:
                    _STATO["force_unsub_devices"].discard(k)
                    _STATO["unmute_devices"].add(k)
        _aggiorna_ui_pagine()
        forza_ricalcolo_impegni()


def forza_ricalcolo_impegni():
    """Sveglia immediatamente lo scheduler quando viene creato/modificato/eliminato un impegno."""
    _STATO["ultimo_sync_anagrafica"] = 0.0
    _STATO["evento_ricalcolo"].set()


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
        return code, f"HTTP {code}"
    except Exception as ex:
        return 500, str(ex)


def _invia_push_a_tutti_sync(payload_dict: dict, chiave_notifica: str = "") -> bool:
    """
    Invia la notifica a tutti i telefoni registrati tracciando la consegna SU OGNI SINGOLO ENDPOINT.
    Restituisce True solo quando TUTTI i telefoni attivi hanno ricevuto l'avviso (200/201/202).
    """
    if not webpush or not _STATO["vapid_private"]:
        return False

    with _LOCK:
        items = list(_STATO["subscriptions"].items())
        gia_consegnati = set(_STATO["inviate_per_ep"].get(chiave_notifica, set())) if chiave_notifica else set()

    if not items:
        return False

    tutti_consegnati = True
    almeno_uno_consegnato = False
    da_rimuovere = []

    for ep, info in items:
        if chiave_notifica and ep in gia_consegnati:
            almeno_uno_consegnato = True
            continue

        code, msg = _invia_push_singolo(info["sub"], payload_dict)
        if code in [200, 201, 202]:
            almeno_uno_consegnato = True
            with _LOCK:
                _STATO["fallimenti_ep"].pop(ep, None)
            if chiave_notifica:
                gia_consegnati.add(ep)
        elif code in [404, 410]:
            # Rimuove il token solo se Google FCM conferma per 2 volte che è disinstallato/scaduto
            with _LOCK:
                cnt = _STATO["fallimenti_ep"].get(ep, 0) + 1
                _STATO["fallimenti_ep"][ep] = cnt
            if cnt >= 2:
                da_rimuovere.append((ep, info.get("id_db"), info.get("device_id"), info.get("ep_hash")))
            else:
                tutti_consegnati = False
        else:
            # Errore temporaneo di rete o timeout (es. 500, 502, 503, 429): riproverà tra 10 secondi!
            print(f"[NOTIFICHE] Retry programmato per endpoint ({code} {msg})", flush=True)
            tutti_consegnati = False

    if chiave_notifica:
        with _LOCK:
            _STATO["inviate_per_ep"][chiave_notifica] = gia_consegnati

    if da_rimuovere:
        sb = _STATO["supabase"]
        with _LOCK:
            for ep, id_db, did, ep_h in da_rimuovere:
                _STATO["subscriptions"].pop(ep, None)
                _STATO["fallimenti_ep"].pop(ep, None)
                if ep_h:
                    _STATO["hash_to_ep"].pop(ep_h, None)
                    _STATO["stato_device"].pop(ep_h, None)
                if did:
                    _STATO["stato_device"].pop(did, None)
        if sb:
            with _DB_LOCK:
                for _, id_db, _, _ in da_rimuovere:
                    if id_db:
                        try:
                            sb.table("menu_giornalieri").delete().eq("id", id_db).execute()
                        except Exception as ex:
                            _rinnova_client_se_chiuso(ex)
        _aggiorna_ui_pagine()

    return (almeno_uno_consegnato and tutti_consegnati) if chiave_notifica else almeno_uno_consegnato


def _esegui_keepalive_render():
    """Impedisce a Render Free di spegnere il server (spin-down) tra l'inizio e la fine di un impegno."""
    ora_ts = time.time()
    if ora_ts - _STATO["ultimo_keepalive"] < 480:  # Ogni 8 minuti
        return
    _STATO["ultimo_keepalive"] = ora_ts

    adesso = _ora_italia()
    if not (6 <= adesso.hour <= 23):
        return

    url_base = (
        _STATO["url_pubblico"]
        or os.environ.get("RENDER_EXTERNAL_URL")
        or "https://extra-large-family.onrender.com"
    ).rstrip("/")
    if not url_base.startswith("http"):
        return

    def _ping():
        try:
            req = urllib.request.Request(f"{url_base}/api/notifiche/ping", headers={"User-Agent": "ELF-KeepAlive/1.0"})
            with urllib.request.urlopen(req, timeout=8) as _:
                pass
        except Exception:
            pass

    threading.Thread(target=_ping, daemon=True).start()


def _controlla_impegni_oggi():
    sb = _STATO["supabase"]
    if not sb:
        return

    _esegui_keepalive_render()

    adesso = _ora_italia()
    oggi_str = adesso.strftime("%Y-%m-%d")
    oggi_date = adesso.date()
    minuti_ora = adesso.hour * 60 + adesso.minute
    ora_ts = time.time()

    with _LOCK:
        if _STATO["data_corrente"] != oggi_str:
            _STATO["data_corrente"] = oggi_str
            _STATO["notifiche_inviate"] = {k for k in _STATO["notifiche_inviate"] if k.startswith(oggi_str + "|")}
            _STATO["inviate_per_ep"] = {k: v for k, v in _STATO["inviate_per_ep"].items() if k.startswith(oggi_str + "|")}
            _STATO["impegni_notificati_id"].clear()

    if ora_ts - _STATO["ultimo_sync_anagrafica"] > 1800 or not _STATO["cache_famiglia"]:
        with _DB_LOCK:
            try:
                rf = sb.table("famiglia").select("id, nome").execute()
                _STATO["cache_famiglia"] = {str(x["id"]): x["nome"] for x in (rf.data or [])}
                rc = sb.table("categorie").select("id, nome").execute()
                _STATO["cache_categorie"] = {str(x["id"]): x["nome"] for x in (rc.data or [])}
                _STATO["ultimo_sync_anagrafica"] = ora_ts
            except Exception as ex:
                _rinnova_client_se_chiuso(ex)

    with _DB_LOCK:
        try:
            res = sb.table("impegni").select("*, impegni_familiari(id_familiare)").lte("data_evento", oggi_str).execute()
            tutti = res.data or []
        except Exception as ex:
            _rinnova_client_se_chiuso(ex)
            print(f"[NOTIFICHE] Avviso lettura impegni: {ex}", flush=True)
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
    ids_presenti_oggi = set()
    notifiche_da_inviare = []

    for ev in eventi_oggi:
        id_ev = str(ev.get("id"))
        ids_presenti_oggi.add(id_ev)
        titolo = (ev.get("titolo") or "Impegno").strip()
        note = (ev.get("note") or "").strip()
        ora_i_str = (ev.get("ora_inizio") or "").strip()
        ora_f_str = (ev.get("ora_fine") or "").strip()
        min_i = _calcola_minuti(ora_i_str)
        min_f = _calcola_minuti(ora_f_str)

        if min_i > 0 and ora_f_str[:5] in ["00:00", "23:59", "24:00"]:
            min_f = 1440

        is_tutto_il_giorno = (min_i <= 0 and min_f <= 0) or (min_i == 0 and min_f >= 1439)
        if is_tutto_il_giorno:
            fine_effettiva = 1439
        elif min_f > min_i and min_i >= 0:
            fine_effettiva = min_f
        elif min_i >= 0:
            fine_effettiva = min(min_i + 60, 1439)
        else:
            fine_effettiva = 1439

        if fine_effettiva > minuti_ora:
            residui += 1

        if is_tutto_il_giorno or min_i < 0:
            continue

        ids_fam = [str(f["id_familiare"]) for f in (ev.get("impegni_familiari") or []) if f.get("id_familiare")]
        nomi_lista = [_STATO["cache_famiglia"].get(fid, "") for fid in ids_fam if _STATO["cache_famiglia"].get(fid)]
        chi_testo = ", ".join(nomi_lista) if nomi_lista else "Tutta la famiglia"
        nome_cat = _STATO["cache_categorie"].get(str(ev.get("id_categoria")), "IMPEGNO")

        ora_i_vis = ora_i_str[:5]
        if min_f == 1440:
            ora_f_vis = "24:00"
        elif min_f > min_i:
            ora_f_vis = ora_f_str[:5]
        else:
            ora_f_vis = ""

        orario_label = f"{ora_i_vis} - {ora_f_vis}" if ora_f_vis else ora_i_vis
        riga_note = f"\n📝 {note}" if note else ""

        # 1. Avviso 15 minuti prima dell'INIZIO
        min_mancanti_inizio = min_i - minuti_ora
        chiave_start = f"{oggi_str}|{id_ev}|{ora_i_vis}|start15"
        if 0 < min_mancanti_inizio <= 15 and chiave_start not in _STATO["notifiche_inviate"]:
            notifiche_da_inviare.append((
                chiave_start,
                id_ev,
                {
                    "title": f"⏰ Tra {min_mancanti_inizio} min ({ora_i_vis}) • {chi_testo}",
                    "body": f"📌 [{nome_cat}] {titolo}\n🕒 Orario: {orario_label}{riga_note}",
                    "tag": f"elf_start_{id_ev}_{oggi_str}"
                }
            ))

        # 2. Avviso 15 minuti prima della FINE
        if min_f > min_i:
            min_mancanti_fine = min_f - minuti_ora
            chiave_end = f"{oggi_str}|{id_ev}|{ora_f_vis}|end15"
            if 0 < min_mancanti_fine <= 15 and chiave_end not in _STATO["notifiche_inviate"]:
                notifiche_da_inviare.append((
                    chiave_end,
                    id_ev,
                    {
                        "title": f"🏁 Termina tra {min_mancanti_fine} min ({ora_f_vis}) • {chi_testo}",
                        "body": f"📌 [{nome_cat}] {titolo}\n🕒 Fine prevista: ore {ora_f_vis}{riga_note}",
                        "tag": f"elf_end_{id_ev}_{oggi_str}"
                    }
                ))

    _STATO["residui_attuali"] = residui

    eliminati_notificati = _STATO["impegni_notificati_id"] - ids_presenti_oggi
    if eliminati_notificati:
        tags_da_chiudere = []
        for id_del in eliminati_notificati:
            _STATO["impegni_notificati_id"].discard(id_del)
            tags_da_chiudere.append(f"elf_start_{id_del}_{oggi_str}")
            tags_da_chiudere.append(f"elf_end_{id_del}_{oggi_str}")
        _invia_push_a_tutti_sync({
            "close_tags": tags_da_chiudere
        })

    stato_modificato = False
    for chiave, id_ev, notif_payload in notifiche_da_inviare:
        consegnata_a_tutti = _invia_push_a_tutti_sync(notif_payload, chiave_notifica=chiave)
        with _LOCK:
            _STATO["impegni_notificati_id"].add(id_ev)
            if consegnata_a_tutti:
                _STATO["notifiche_inviate"].add(chiave)
            stato_modificato = True

    if stato_modificato:
        _salva_stato_vapid_db()


def _loop_scheduler():
    _carica_o_inizializza_config_db()
    while True:
        try:
            _controlla_impegni_oggi()
            gc.collect()
        except Exception as ex:
            print(f"[NOTIFICHE] Errore loop: {ex}", flush=True)
        scattato = _STATO["evento_ricalcolo"].wait(timeout=10)
        if scattato:
            time.sleep(0.4)
            _STATO["evento_ricalcolo"].clear()


SW_PUSH_HANDLERS_JS = r"""
async function aggiornaContatoreNonLette() {
    if (!self.registration || !self.registration.getNotifications) return;
    try {
        const list = await self.registration.getNotifications();
        const unreadCount = list.length;
        if (self.navigator && "setAppBadge" in self.navigator) {
            if (unreadCount > 0) await self.navigator.setAppBadge(unreadCount);
            else await self.navigator.clearAppBadge();
        }
    } catch (e) {}
}

self.addEventListener("activate", (event) => {
    event.waitUntil((async () => {
        try {
            if ("caches" in self) {
                const keys = await caches.keys();
                for (const k of keys) {
                    const c = await caches.open(k);
                    await c.delete("/");
                    await c.delete("/index.html");
                }
            }
        } catch (e) {}
    })());
});

self.addEventListener("push", (event) => {
    let data = {};
    if (event.data) {
        try { data = event.data.json(); } catch (e) { data = {}; }
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

    if (!data.title) {
        event.waitUntil(aggiornaContatoreNonLette());
        return;
    }

    const alertTag = data.tag || ("elf_notif_" + Date.now());
    const options = {
        body: data.body || "",
        icon: "/icons/Icon-192.png",
        badge: "/icons/Icon-192.png",
        vibrate: [250, 120, 250, 120, 400],
        tag: alertTag,
        silent: false,
        renotify: true,
        requireInteraction: true,
        timestamp: Date.now(),
        data: { url: "/" }
    };

    const showWork = async () => {
        await self.registration.showNotification(data.title, options);
        await aggiornaContatoreNonLette();
    };

    event.waitUntil(showWork());
});

self.addEventListener("notificationclick", (event) => {
    event.notification.close();
    event.waitUntil(
        Promise.all([
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
self.addEventListener("install", (e) => { self.skipWaiting(); });
self.addEventListener("activate", (e) => { e.waitUntil(self.clients.claim()); });
""" + SW_PUSH_HANDLERS_JS

CLIENT_JS_CODE = r"""
(function() {
    if (window.__elfNotifInit) return;
    window.__elfNotifInit = true;

    let cachedEndpoint = "";
    let syncInCorso = false;

    function getDeviceId() {
        let did = "";
        try {
            did = localStorage.getItem("elf_device_id") || "";
            if (!did) {
                did = "dev_" + Math.random().toString(36).substring(2, 10) + "_" + Date.now().toString(36);
                localStorage.setItem("elf_device_id", did);
            }
            document.cookie = "elf_did=" + did + "; path=/; max-age=31536000; SameSite=Lax";
        } catch (e) {}
        return did;
    }

    function urlBase64ToUint8Array(base64String) {
        const padding = "=".repeat((4 - base64String.length % 4) % 4);
        const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
        const rawData = window.atob(base64);
        return Uint8Array.from([...rawData].map((c) => c.charCodeAt(0)));
    }

    function arrayBufferToBase64Url(buffer) {
        if (!buffer) return "";
        const bytes = new Uint8Array(buffer);
        let binary = "";
        for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
        return window.btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    }

    async function getDedicatedSwReg() {
        try {
            const regs = await navigator.serviceWorker.getRegistrations();
            for (const r of regs) {
                if (r.scope && r.scope.includes("/api/notifiche")) {
                    await r.unregister();
                }
            }
        } catch (e) {}
        const reg = await navigator.serviceWorker.register("/sw-notifiche.js", { scope: "/elf-push-sw/" });
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

    async function allineaBadgeTendina(reg) {
        if (!reg || !reg.getNotifications || !("setAppBadge" in navigator)) return;
        try {
            const list = await reg.getNotifications();
            if (list.length > 0) await navigator.setAppBadge(list.length);
            else await navigator.clearAppBadge();
        } catch (e) {}
    }

    async function sincronizzaDispositivo() {
        if (syncInCorso) return;
        syncInCorso = true;
        try {
            const did = getDeviceId();
            const permGranted = ("Notification" in window && Notification.permission === "granted");
            let mutedLocal = false;
            try { mutedLocal = (localStorage.getItem("elf_muted") === "1"); } catch (e) {}

            let reg = null;
            let sub = null;
            if ("serviceWorker" in navigator && "PushManager" in window && permGranted) {
                reg = await getDedicatedSwReg();
                sub = await reg.pushManager.getSubscription();
                if (sub) cachedEndpoint = sub.endpoint || "";
                await allineaBadgeTendina(reg);
            } else if (!permGranted) {
                cachedEndpoint = "";
            }

            const permParam = (permGranted && !mutedLocal) ? "1" : "0";
            const r = await fetch(
                "/api/notifiche/status?did=" + encodeURIComponent(did)
                + "&endpoint=" + encodeURIComponent(cachedEndpoint)
                + "&perm=" + permParam,
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
                if (reg && sub) {
                    try { await sub.unsubscribe(); } catch (e) {}
                    sub = null;
                    cachedEndpoint = "";
                }
                if (reg && reg.getNotifications) {
                    const ns = await reg.getNotifications();
                    ns.forEach((n) => n.close());
                }
                if ("clearAppBadge" in navigator) {
                    navigator.clearAppBadge().catch(()=>{});
                }
                return;
            }

            // Se Android ha il permesso attivo, registra o riallinea silenziosamente il token
            if (permGranted && reg && cfg.vapid_public) {
                if (sub && sub.options && sub.options.applicationServerKey) {
                    const currentKey = arrayBufferToBase64Url(sub.options.applicationServerKey);
                    if (currentKey && currentKey !== cfg.vapid_public.replace(/=+$/, "")) {
                        try { await sub.unsubscribe(); } catch (e) {}
                        sub = null;
                    }
                }
                if (!sub) {
                    try {
                        sub = await reg.pushManager.subscribe({
                            userVisibleOnly: true,
                            applicationServerKey: urlBase64ToUint8Array(cfg.vapid_public)
                        });
                    } catch (e) {}
                }
                if (sub) {
                    cachedEndpoint = sub.endpoint || "";
                    if (!cfg.dispositivo_attivo) {
                        const resSub = await fetch("/api/notifiche/subscribe", {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ sub: sub, device_id: did, silent_sync: true })
                        });
                        const dataSub = await resSub.json();
                        if (dataSub.ep_hash) {
                            document.cookie = "elf_ep_h=" + dataSub.ep_hash + "; path=/; max-age=31536000; SameSite=Lax";
                        }
                    } else if (cfg.ep_hash) {
                        document.cookie = "elf_ep_h=" + cfg.ep_hash + "; path=/; max-age=31536000; SameSite=Lax";
                    }
                }
            }
        } catch (e) {
        } finally {
            syncInCorso = false;
        }
    }

    sincronizzaDispositivo();
    setTimeout(sincronizzaDispositivo, 1000);
    setTimeout(sincronizzaDispositivo, 2800);
    setInterval(sincronizzaDispositivo, 15000);
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
    <button id="btnAttiva" class="btn-attiva" onclick="attivaDispositivo()">📲 ATTIVA NOTIFICHE</button>
    <button id="btnTest" class="btn-test" style="display:none;" onclick="inviaTestDispositivo()">🔔 INVIA NOTIFICA DI PROVA</button>
    <button id="btnMute" class="btn-mute" style="display:none;" onclick="disattivaDispositivo()">🔕 SILENZIA QUESTO TELEFONO</button>
    <a class="btn-link btn-home" href="/">⬅️ TORNA ALL'APP</a>
</div>
<script>
function getDeviceId() {
    let did = "";
    try {
        did = localStorage.getItem("elf_device_id") || "";
        if (!did) {
            did = "dev_" + Math.random().toString(36).substring(2, 10) + "_" + Date.now().toString(36);
            localStorage.setItem("elf_device_id", did);
        }
        document.cookie = "elf_did=" + did + "; path=/; max-age=31536000; SameSite=Lax";
    } catch (e) {}
    return did;
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
            if (r.scope && r.scope.includes("/api/notifiche")) {
                await r.unregister();
            }
        }
    } catch (e) {}
    const reg = await navigator.serviceWorker.register("/sw-notifiche.js", { scope: "/elf-push-sw/" });
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

async function aggiornaStatoUI() {
    const badge = document.getElementById("badgeStato");
    const st = document.getElementById("status");
    const btnAttiva = document.getElementById("btnAttiva");
    const btnTest = document.getElementById("btnTest");
    const btnMute = document.getElementById("btnMute");

    if (!("Notification" in window) || !("serviceWorker" in navigator) || !("PushManager" in window)) {
        badge.className = "badge-stato stato-off";
        badge.innerText = "❌ NON SUPPORTATE";
        st.innerText = "Per ricevere notifiche su smartphone, apri l'app dall'icona installata sulla schermata Home.";
        btnAttiva.style.display = "none";
        return;
    }

    const did = getDeviceId();
    const perm = Notification.permission;
    let muted = false;
    try { muted = (localStorage.getItem("elf_muted") === "1"); } catch (e) {}

    let reg = null;
    let sub = null;
    if (perm === "granted" && !muted) {
        try {
            reg = await getSwReg();
            sub = await reg.pushManager.getSubscription();
        } catch (e) {}
    }

    let ep = sub ? sub.endpoint : "";
    let cfg = { dispositivo_attivo: false, vapid_public: "" };
    try {
        cfg = await fetchConTimeout(
            "/api/notifiche/status?did=" + encodeURIComponent(did)
            + "&endpoint=" + encodeURIComponent(ep)
            + "&perm=" + (perm === "granted" && !muted ? "1" : "0"),
            {},
            5000
        );
    } catch (e) {}

    if (perm === "granted" && !muted && reg && cfg.vapid_public && (!sub || !cfg.dispositivo_attivo)) {
        try {
            if (!sub) {
                sub = await reg.pushManager.subscribe({
                    userVisibleOnly: true,
                    applicationServerKey: urlBase64ToUint8Array(cfg.vapid_public)
                });
            }
            if (sub) {
                const resSub = await fetchConTimeout("/api/notifiche/subscribe", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ sub: sub, device_id: did, send_test: false })
                }, 6000);
                if (resSub && resSub.ep_hash) {
                    document.cookie = "elf_ep_h=" + resSub.ep_hash + "; path=/; max-age=31536000; SameSite=Lax";
                }
                cfg.dispositivo_attivo = true;
            }
        } catch (e) {}
    }

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
        st.innerText = "Tocca il pulsante blu qui sotto per attivare le notifiche su questo telefono.";
        btnAttiva.style.display = "block";
        btnAttiva.innerText = "📲 ATTIVA NOTIFICHE";
        btnTest.style.display = "none";
        btnMute.style.display = "none";
    }
}

async function attivaDispositivo() {
    const st = document.getElementById("status");
    const btnAttiva = document.getElementById("btnAttiva");
    btnAttiva.disabled = true;
    st.className = "";
    st.innerText = "⏳ Richiesta autorizzazione notifiche...";

    try {
        try { localStorage.removeItem("elf_muted"); } catch (e) {}

        // Chiamata immediata al popup nativo di Android
        let perm = Notification.permission;
        if (perm !== "granted") {
            perm = await Notification.requestPermission();
        }

        if (perm !== "granted") {
            st.className = "err";
            st.innerText = "⚠️ Autorizzazione negata da Android. Se avevi spento l'interruttore nelle impostazioni del telefono, riaccendilo da Info App -> Notifiche.";
            btnAttiva.disabled = false;
            return;
        }

        st.innerText = "⏳ Registrazione di questo telefono in corso...";
        const did = getDeviceId();
        const reg = await getSwReg();
        const cfg = await fetchConTimeout("/api/notifiche/status?did=" + encodeURIComponent(did) + "&perm=1", {}, 5000);

        // Riusa la sottoscrizione esistente se valida per non invalidare il token su Google FCM
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
            body: JSON.stringify({ sub: sub, device_id: did, send_test: false })
        }, 6000);

        if (resSub && resSub.ep_hash) {
            document.cookie = "elf_ep_h=" + resSub.ep_hash + "; path=/; max-age=31536000; SameSite=Lax";
        }

        await aggiornaStatoUI();
        st.className = "ok";
        st.innerText = "✅ Notifiche attivate con successo su questo telefono!";
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
            body: JSON.stringify({ sub: sub, device_id: did, send_test: true })
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
        try { localStorage.setItem("elf_muted", "1"); } catch (e) {}
        const did = getDeviceId();
        const reg = await getSwReg();
        const sub = await reg.pushManager.getSubscription();
        const ep = sub ? sub.endpoint : "";
        if (sub) {
            try { await sub.unsubscribe(); } catch (e) {}
        }
        if (reg && reg.getNotifications) {
            const ns = await reg.getNotifications();
            ns.forEach((n) => n.close());
        }
        if ("clearAppBadge" in navigator) {
            navigator.clearAppBadge().catch(()=>{});
        }
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


def _salva_sub_db_sync(ep: str, sub_obj: dict, did: str, ep_h: str):
    """
    Salva il telefono su Supabase usando ESCLUSIVAMENTE l'endpoint come chiave univoca.
    Due telefoni diversi non potranno MAI sovrascriversi a vicenda.
    """
    sb = _STATO["supabase"]
    record_json = json.dumps({"sub": sub_obj, "device_id": did, "ep_hash": ep_h})
    id_db = None

    with _LOCK:
        esistente = _STATO["subscriptions"].get(ep)
        if esistente:
            id_db = esistente.get("id_db")

    if sb:
        with _DB_LOCK:
            try:
                if not id_db:
                    res_ex = sb.table("menu_giornalieri").select("id, subcategoria").eq("data_menu", "1970-01-01").eq("categoria", CAT_SUB_DB).execute()
                    for r_s in (res_ex.data or []):
                        try:
                            raw_s = json.loads(r_s.get("subcategoria") or "{}")
                            s_inner = raw_s.get("sub") if "sub" in raw_s else raw_s
                            # Confronta ESCLUSIVAMENTE l'endpoint hardware, mai il device_id!
                            if s_inner.get("endpoint") == ep:
                                id_db = r_s["id"]
                                break
                        except Exception:
                            pass
                if id_db:
                    sb.table("menu_giornalieri").update({
                        "subcategoria": record_json,
                        "preso": False
                    }).eq("id", id_db).execute()
                else:
                    res_ins = sb.table("menu_giornalieri").insert({
                        "data_menu": "1970-01-01",
                        "categoria": CAT_SUB_DB,
                        "subcategoria": record_json,
                        "quantita": 0,
                        "unita_misura": "pz",
                        "preso": False
                    }).execute()
                    if res_ins.data:
                        id_db = res_ins.data[0]["id"]
            except Exception as ex:
                _rinnova_client_se_chiuso(ex)
                print(f"[NOTIFICHE] Avviso salvataggio {CAT_SUB_DB}: {ex}", flush=True)

    with _LOCK:
        if ep in _STATO["subscriptions"]:
            _STATO["subscriptions"][ep]["id_db"] = id_db


def collega_api_fastapi(app: fastapi.FastAPI, supabase_client):
    if supabase_client is not None:
        _STATO["supabase"] = _crea_client_supabase_dedicato(supabase_client)

    with _LOCK:
        if not _STATO["scheduler_avviato"] and _STATO["supabase"] is not None:
            _STATO["scheduler_avviato"] = True
            threading.Thread(target=_loop_scheduler, daemon=True).start()

    @app.get("/api/notifiche/ping")
    async def get_ping():
        return JSONResponse({"ok": True, "dispositivi": len(_STATO["subscriptions"])})

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
        did = (req.query_params.get("did") or req.cookies.get("elf_did") or "").strip()
        ep = req.query_params.get("endpoint", "").strip()
        ep_h_cookie = (req.cookies.get("elf_ep_h") or "").strip()
        perm = req.query_params.get("perm", "")

        ep_h = _calcola_hash_ep(ep) if ep else ep_h_cookie

        with _LOCK:
            unmute_cmd = bool((did and did in _STATO["unmute_devices"]) or (ep_h and ep_h in _STATO["unmute_devices"]))
            if unmute_cmd:
                _STATO["unmute_devices"].discard(did)
                _STATO["unmute_devices"].discard(ep_h)

            force_unsub = bool((did and did in _STATO["force_unsub_devices"]) or (ep_h and ep_h in _STATO["force_unsub_devices"]))
            if force_unsub:
                _STATO["force_unsub_devices"].discard(did)
                _STATO["force_unsub_devices"].discard(ep_h)

            info_sub = _STATO["subscriptions"].get(ep) if ep else None
            if not info_sub and ep_h and ep_h in _STATO["hash_to_ep"]:
                ep_mapped = _STATO["hash_to_ep"][ep_h]
                info_sub = _STATO["subscriptions"].get(ep_mapped)

            disp_attivo = (info_sub is not None) and not force_unsub and (perm != "0")
            if ep_h:
                _STATO["stato_device"][ep_h] = disp_attivo
            if did and disp_attivo:
                _STATO["stato_device"][did] = True

        if ep or did:
            _associa_device_a_pagina(did, ep)

        return JSONResponse({
            "attive": disp_attivo,
            "dispositivo_registrato": disp_attivo,
            "dispositivo_attivo": disp_attivo,
            "ep_hash": ep_h,
            "force_unsub": force_unsub,
            "unmute_cmd": unmute_cmd,
            "dispositivi": len(_STATO["subscriptions"]),
            "residui": _STATO["residui_attuali"],
            "vapid_public": _STATO["vapid_public"]
        })

    @app.get("/api/notifiche/test")
    async def get_test_push():
        ora_str = _ora_italia().strftime("%H:%M")
        await asyncio.to_thread(_invia_push_a_tutti_sync, {
            "title": f"🔔 Test Notifica ELF ({ora_str})",
            "body": "Tutti i dispositivi registrati della famiglia ricevono correttamente gli avvisi!",
            "tag": f"elf_test_{int(time.time())}"
        })
        return JSONResponse({
            "ok": True,
            "dispositivi_registrati": len(_STATO["subscriptions"])
        })

    @app.post("/api/notifiche/subscribe")
    async def post_subscribe(req: fastapi.Request):
        payload = await req.json()
        sub_obj = payload.get("sub") if isinstance(payload, dict) and "sub" in payload else payload
        send_test = bool(payload.get("send_test")) if isinstance(payload, dict) else False
        did = ((payload.get("device_id") if isinstance(payload, dict) else "") or req.cookies.get("elf_did") or "").strip()

        ep = (sub_obj.get("endpoint") or "").strip() if isinstance(sub_obj, dict) else ""
        ep_h = _calcola_hash_ep(ep)
        push_status = 200
        push_msg = "OK"

        if ep:
            with _LOCK:
                for k in (did, ep_h):
                    if k:
                        _STATO["force_unsub_devices"].discard(k)
                        _STATO["unmute_devices"].discard(k)
                        _STATO["stato_device"][k] = True

                esistente = _STATO["subscriptions"].get(ep)
                _STATO["subscriptions"][ep] = {
                    "id_db": esistente.get("id_db") if esistente else None,
                    "sub": sub_obj,
                    "ep_hash": ep_h,
                    "device_id": did or ep_h
                }
                _STATO["hash_to_ep"][ep_h] = ep
                _STATO["fallimenti_ep"].pop(ep, None)

            _associa_device_a_pagina(did or ep_h, ep)
            _aggiorna_ui_pagine()
            threading.Thread(target=_salva_sub_db_sync, args=(ep, sub_obj, did or ep_h, ep_h), daemon=True).start()

            if send_test:
                ora_str = _ora_italia().strftime("%H:%M")
                push_status, push_msg = await asyncio.to_thread(_invia_push_singolo, sub_obj, {
                    "title": f"🔔 Notifica di Prova ELF ({ora_str})",
                    "body": "✅ Questo dispositivo è registrato in modo indipendente e riceve tutti gli avvisi.",
                    "tag": f"elf_test_{int(time.time())}"
                })

        return JSONResponse({
            "ok": True,
            "ep_hash": ep_h,
            "dispositivi_totali": len(_STATO["subscriptions"]),
            "residui": _STATO["residui_attuali"],
            "push_status": push_status,
            "push_msg": push_msg
        })

    @app.post("/api/notifiche/unsubscribe")
    async def post_unsubscribe(req: fastapi.Request):
        payload = await req.json()
        ep = (payload.get("endpoint") or "").strip() if isinstance(payload, dict) else ""
        did = ((payload.get("device_id") if isinstance(payload, dict) else "") or req.cookies.get("elf_did") or "").strip()
        _rimuovi_endpoint_specifico(ep, did, imposta_force_unsub=True)
        return JSONResponse({"ok": True})

    @app.middleware("http")
    async def inietta_script_notifiche(request: fastapi.Request, call_next):
        path = request.url.path
        if not _STATO["url_pubblico"] and request.url.scheme and request.url.netloc and "127.0.0.1" not in request.url.netloc and "localhost" not in request.url.netloc:
            _STATO["url_pubblico"] = f"{request.url.scheme}://{request.url.netloc}"

        ep_h_cookie = (request.cookies.get("elf_ep_h") or "").strip()
        if ep_h_cookie:
            with _LOCK:
                _STATO["ultimo_ep_attivo"] = ep_h_cookie

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
            prefisso = ("// v-elf-push-20\n" + SW_PUSH_HANDLERS_JS + "\n").encode("utf-8")
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
