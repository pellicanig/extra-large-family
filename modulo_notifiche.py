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
    "subscriptions": {},        # endpoint -> {"id_db": int|None, "sub": dict, "ep_hash": str, "device_id": str, "hw_id": str, "ultimo_accesso_ts": int, "ultimo_ack_ts": int}
    "hash_to_ep": {},           # ep_hash -> endpoint
    "did_to_ep": {},            # device_id -> endpoint
    "stato_device": {},         # device_id / ep_hash -> bool
    "diag_device": {},          # device_id -> dict diagnostica (perm, standalone, persisted, os_cfg)
    "fp_to_did": {},            # impronta (ip|ua) -> device_id
    "ua_to_did": {},            # impronta (ua) -> device_id
    "ultimo_ep_attivo": "",     # ultimo ep_hash rilevato dal cookie HTTP all'apertura
    "ultimo_did_attivo": "",    # ultimo device_id rilevato dal cookie HTTP all'apertura
    "force_unsub_devices": set(),
    "unmute_devices": set(),
    "notifiche_inviate": set(), # "YYYY-MM-DD|id_imp|HH:MM|tipo" (solo quando TUTTI i telefoni attivi hanno confermato ACK)
    "inviate_per_ep": {},       # chiave_notifica -> set(endpoint_che_hanno_ricevuto_201)
    "ack_per_ep": {},           # chiave_notifica -> set(ep / ep_hash / did che hanno confermato ACK)
    "fallimenti_ep": {},        # endpoint -> conteggio errori 401/403/404/410 consecutivi
    "impegni_notificati_id": set(),
    "data_corrente": "",
    "residui_attuali": 0,
    "cache_famiglia": {},
    "cache_categorie": {},
    "ultimo_sync_anagrafica": 0.0,
    "ultimo_sync_subs_ts": 0.0,
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


def _calcola_fp_http(req: fastapi.Request) -> tuple[str, str]:
    try:
        xff = (req.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        ip = xff or (req.client.host if req.client else "") or ""
        ua = (req.headers.get("user-agent") or "").strip()
        fp = hashlib.sha256(f"{ip}|{ua}".encode("utf-8")).hexdigest()[:16] if (ip or ua) else ""
        ua_h = hashlib.sha256(ua.encode("utf-8")).hexdigest()[:16] if ua else ""
        return fp, ua_h
    except Exception:
        return "", ""


def _calcola_fp_page(page) -> tuple[str, str]:
    if page is None:
        return "", ""
    try:
        ip = str(getattr(page, "client_ip", "") or "").split(",")[0].strip()
        ua = str(getattr(page, "client_user_agent", "") or "").strip()
        fp = hashlib.sha256(f"{ip}|{ua}".encode("utf-8")).hexdigest()[:16] if (ip or ua) else ""
        ua_h = hashlib.sha256(ua.encode("utf-8")).hexdigest()[:16] if ua else ""
        return fp, ua_h
    except Exception:
        return "", ""


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
    url = (
        _STATO["supabase_url"]
        or os.environ.get("SUPABASE_URL")
        or (getattr(fallback_client, "supabase_url", None) if fallback_client else "")
    )
    key = (
        _STATO["supabase_key"]
        or os.environ.get("SUPABASE_KEY")
        or (getattr(fallback_client, "supabase_key", None) if fallback_client else "")
    )
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
    if any(k in msg for k in [
        "ConnectionState", "SEND_HEADERS", "RECV_", "Server disconnected",
        "RemoteProtocolError", "LocalProtocolError", "ReadTimeout", "WriteTimeout", "PoolTimeout"
    ]):
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
        ack_ep_oggi = {
            k: list(v) for k, v in _STATO["ack_per_ep"].items()
            if k.startswith(oggi_str + "|") and v
        }
        payload_vapid = json.dumps({
            "pub": _STATO["vapid_public"],
            "priv": _STATO["vapid_private"],
            "sent": inviate_oggi,
            "sent_ep": inviate_ep_oggi,
            "ack_ep": ack_ep_oggi
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


def _carica_o_inizializza_config_db(forza_reload_subs: bool = False):
    with _LOCK:
        if _STATO["inizializzato"] and _STATO["vapid_public"] and not forza_reload_subs:
            return
    sb = _STATO["supabase"]
    pub_env = _pulisci_chiave_str(os.environ.get("VAPID_PUBLIC_KEY", ""))
    priv_env = _pulisci_chiave_str(os.environ.get("VAPID_PRIVATE_KEY", ""))

    pub_init, priv_init = "", ""
    if priv_env:
        pub_init, priv_init = _valida_o_deriva_coppia_vapid(pub_env, priv_env)

    subs_loaded = {}
    hash_map = {}
    did_map = {}
    device_map = {}
    sent_loaded = set()
    sent_ep_loaded = {}
    ack_ep_loaded = {}
    ids_da_pulire = []
    seen_did_hw = set()
    ora_now = int(time.time())

    if sb:
        with _DB_LOCK:
            try:
                res = sb.table("menu_giornalieri").select("id, categoria, subcategoria, preso").eq("data_menu", "1970-01-01").in_("categoria", ["SYS_VAPID", CAT_SUB_DB]).order("id", desc=True).execute()
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
                            for k_s, v_list in (dati_v.get("sent_ep") or {}).items():
                                if str(k_s).startswith(oggi_str + "|") and isinstance(v_list, list):
                                    sent_ep_loaded[str(k_s)] = set(v_list)
                            for k_a, v_list in (dati_v.get("ack_ep") or {}).items():
                                if str(k_a).startswith(oggi_str + "|") and isinstance(v_list, list):
                                    ack_ep_loaded[str(k_a)] = set(v_list)
                            if not (pub_init and priv_init):
                                pub_init, priv_init = _valida_o_deriva_coppia_vapid(dati_v.get("pub", ""), dati_v.get("priv", ""))
                        except Exception:
                            pass
                    elif cat == CAT_SUB_DB and sub_txt:
                        try:
                            raw_obj = json.loads(sub_txt)
                            sub_obj = raw_obj.get("sub") if ("sub" in raw_obj and isinstance(raw_obj["sub"], dict)) else raw_obj
                            did = (raw_obj.get("device_id") or "").strip() if isinstance(raw_obj, dict) else ""
                            hw_id = (raw_obj.get("hw_id") or "").strip() if isinstance(raw_obj, dict) else ""
                            ultimo_acc = int(raw_obj.get("ultimo_accesso_ts") or 0) if isinstance(raw_obj, dict) else 0
                            ultimo_ack = int(raw_obj.get("ultimo_ack_ts") or 0) if isinstance(raw_obj, dict) else 0
                            ep = (sub_obj.get("endpoint") or "").strip() if isinstance(sub_obj, dict) else ""

                            if ep and not bool(r.get("preso")):
                                ultima_att = max(ultimo_acc, ultimo_ack)
                                if ultima_att > 0 and (ora_now - ultima_att) > (30 * 86400):
                                    ids_da_pulire.append(r["id"])
                                    continue

                                ep_h = _calcola_hash_ep(ep)
                                did_fin = did or ep_h
                                hw_key = f"hw:{hw_id}" if hw_id else ""
                                if ep in subs_loaded or did_fin in seen_did_hw or (hw_key and hw_key in seen_did_hw):
                                    ids_da_pulire.append(r["id"])
                                    continue

                                seen_did_hw.add(did_fin)
                                if hw_key:
                                    seen_did_hw.add(hw_key)

                                subs_loaded[ep] = {
                                    "id_db": r["id"],
                                    "sub": sub_obj,
                                    "ep_hash": ep_h,
                                    "device_id": did_fin,
                                    "hw_id": hw_id,
                                    "ultimo_accesso_ts": ultimo_acc or ora_now,
                                    "ultimo_ack_ts": ultimo_ack
                                }
                                hash_map[ep_h] = ep
                                did_map[did_fin] = ep
                                device_map[ep_h] = True
                                device_map[did_fin] = True
                        except Exception:
                            pass

                for id_dup in ids_da_pulire:
                    try:
                        sb.table("menu_giornalieri").delete().eq("id", id_dup).execute()
                    except Exception:
                        pass
            except Exception as ex:
                _rinnova_client_se_chiuso(ex)
                print(f"[NOTIFICHE] Errore lettura config DB: {ex}", flush=True)

    if not (pub_init and priv_init):
        pub_init, priv_init = _genera_chiavi_vapid()

    with _LOCK:
        if pub_init and priv_init:
            _STATO["vapid_public"] = pub_init
            _STATO["vapid_private"] = priv_init
        _STATO["notifiche_inviate"].update(sent_loaded)
        for k_s, v_set in sent_ep_loaded.items():
            _STATO["inviate_per_ep"].setdefault(k_s, set()).update(v_set)
        for k_a, v_set in ack_ep_loaded.items():
            _STATO["ack_per_ep"].setdefault(k_a, set()).update(v_set)
        _STATO["subscriptions"] = subs_loaded
        _STATO["hash_to_ep"] = hash_map
        _STATO["did_to_ep"] = did_map
        _STATO["stato_device"].update(device_map)
        _STATO["ultimo_sync_subs_ts"] = time.time()
        _STATO["inizializzato"] = True

    if not forza_reload_subs:
        print(f"[NOTIFICHE] Inizializzato. Dispositivi registrati: {len(_STATO['subscriptions'])}", flush=True)
        threading.Thread(target=_salva_stato_vapid_db, daemon=True).start()


def _risolvi_did_pagina(page=None) -> str:
    if page is not None:
        did_p = getattr(page, "_elf_did", None)
        if did_p:
            return did_p
        fp, ua_h = _calcola_fp_page(page)
        with _LOCK:
            if fp and fp in _STATO["fp_to_did"]:
                return _STATO["fp_to_did"][fp]
            if ua_h and ua_h in _STATO["ua_to_did"]:
                return _STATO["ua_to_did"][ua_h]
    with _LOCK:
        return _STATO["ultimo_did_attivo"] or _STATO["ultimo_ep_attivo"] or ""


def ottieni_diag_dispositivo(page=None) -> tuple[str, dict]:
    did = _risolvi_did_pagina(page)
    with _LOCK:
        diag = dict(_STATO["diag_device"].get(did, {})) if did else {}
    return did, diag


def _aggancia_pagina(page):
    if page is None:
        return
    try:
        fp, ua_h = _calcola_fp_page(page)
        with _LOCK:
            did_trovato = (
                getattr(page, "_elf_did", None)
                or (_STATO["fp_to_did"].get(fp) if fp else None)
                or (_STATO["ua_to_did"].get(ua_h) if ua_h else None)
                or _STATO["ultimo_did_attivo"]
            )
            ultimo_h = _STATO["ultimo_ep_attivo"]
            if did_trovato and did_trovato in _STATO["did_to_ep"]:
                ep = _STATO["did_to_ep"][did_trovato]
                info = _STATO["subscriptions"].get(ep, {})
                page._elf_ep = ep
                page._elf_ep_hash = info.get("ep_hash") or _calcola_hash_ep(ep)
                page._elf_did = did_trovato
            elif ultimo_h and ultimo_h in _STATO["hash_to_ep"]:
                ep = _STATO["hash_to_ep"][ultimo_h]
                info = _STATO["subscriptions"].get(ep, {})
                page._elf_ep = ep
                page._elf_ep_hash = ultimo_h
                page._elf_did = did_trovato or info.get("device_id") or ultimo_h
            elif did_trovato:
                page._elf_did = did_trovato
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


def _associa_device_a_pagina(did: str, ep: str, disp_attivo: bool = False, fp: str = "", ua_h: str = ""):
    ep_h = _calcola_hash_ep(ep) if ep else ""
    with _LOCK:
        _STATO["ultimo_ep_attivo"] = ep_h if disp_attivo else ""
        if did:
            _STATO["ultimo_did_attivo"] = did
            if fp:
                _STATO["fp_to_did"][fp] = did
            if ua_h:
                _STATO["ua_to_did"][ua_h] = did

    morti = set()
    serve_refresh = False

    for p in list(_STATO["pagine_attive"]):
        try:
            p_ep = getattr(p, "_elf_ep", None)
            p_did = getattr(p, "_elf_did", None)
            p_fp, p_ua_h = _calcola_fp_page(p)

            match_device = (
                (ep and p_ep == ep)
                or (did and p_did == did)
                or (fp and p_fp and fp == p_fp)
                or (ua_h and p_ua_h and ua_h == p_ua_h)
                or (p_did is None and p_ep is None)
            )
            if match_device:
                if did and p_did != did:
                    p._elf_did = did
                    serve_refresh = True
                if disp_attivo and ep:
                    if p_ep != ep:
                        p._elf_ep = ep
                        p._elf_ep_hash = ep_h
                        serve_refresh = True
                elif not disp_attivo:
                    if p_ep is not None:
                        p._elf_ep = None
                        p._elf_ep_hash = None
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
        _aggancia_pagina(page)
        ep = getattr(page, "_elf_ep", None)
        ep_h = getattr(page, "_elf_ep_hash", None)
        did = _risolvi_did_pagina(page)

        with _LOCK:
            diag_d = _STATO["diag_device"].get(did, {}) if did else {}
            if diag_d and diag_d.get("perm") not in ("", "1"):
                attivo = False
            elif ep and ep in _STATO["subscriptions"]:
                attivo = bool(_STATO["stato_device"].get(did or ep_h, True))
            elif did and did in _STATO["did_to_ep"]:
                attivo = bool(_STATO["stato_device"].get(did, True))
            elif ep_h and ep_h in _STATO["hash_to_ep"]:
                attivo = bool(_STATO["stato_device"].get(ep_h, True))
            elif did and did in _STATO["stato_device"]:
                attivo = bool(_STATO["stato_device"][did])
            else:
                attivo = False

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
            for ep_k, info in list(_STATO["subscriptions"].items()):
                if info.get("device_id") == did_target or info.get("ep_hash") == did_target:
                    if info.get("id_db"):
                        ids_da_eliminare.append(info["id_db"])
                    _STATO["subscriptions"].pop(ep_k, None)
                    if info.get("ep_hash"):
                        _STATO["hash_to_ep"].pop(info["ep_hash"], None)
                        _STATO["stato_device"][info["ep_hash"]] = False
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

        for p in list(_STATO["pagine_attive"]):
            try:
                if (ep_target and getattr(p, "_elf_ep", None) == ep_target) or (did_target and getattr(p, "_elf_did", None) == did_target):
                    p._elf_ep = None
                    p._elf_ep_hash = None
            except Exception:
                pass

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
    """Sveglia immediatamente lo scheduler in background senza rallentare il salvataggio in main.py."""
    _STATO["ultimo_sync_anagrafica"] = 0.0
    _STATO["evento_ricalcolo"].set()


def _is_dispositivo_confermato_ack(ack_set: set, ep: str, ep_h: str, did: str) -> bool:
    if not ack_set:
        return False
    return bool((ep and ep in ack_set) or (ep_h and ep_h in ack_set) or (did and did in ack_set))


def _registra_ack_interno(notif_key: str, ep: str, ep_h: str, did: str, tag: str):
    if not notif_key and tag:
        oggi_s = _ora_italia().strftime("%Y-%m-%d")
        with _LOCK:
            for k_cand in list(_STATO["inviate_per_ep"].keys()):
                if k_cand.startswith(oggi_s + "|"):
                    parti = k_cand.split("|")
                    if len(parti) >= 4:
                        id_c, tipo_c = parti[1], parti[3]
                        pref_t = "elf_start_" if tipo_c == "start15" else "elf_end_"
                        if tag == f"{pref_t}{id_c}_{oggi_s}":
                            notif_key = k_cand
                            break
    if not notif_key:
        return

    ora_int = int(time.time())
    salva_db = False

    with _LOCK:
        ep_risolto = ep or _STATO["hash_to_ep"].get(ep_h, "") or _STATO["did_to_ep"].get(did, "")
        info_sub = _STATO["subscriptions"].get(ep_risolto) if ep_risolto else None
        if not info_sub:
            for info_k in _STATO["subscriptions"].values():
                if (did and info_k.get("device_id") == did) or (ep_h and info_k.get("ep_hash") == ep_h):
                    info_sub = info_k
                    ep_risolto = (info_k.get("sub") or {}).get("endpoint") or ""
                    break

        if info_sub:
            ep_h = ep_h or info_sub.get("ep_hash") or _calcola_hash_ep(ep_risolto)
            did = did or info_sub.get("device_id") or ep_h
            info_sub["ultimo_accesso_ts"] = ora_int
            info_sub["ultimo_ack_ts"] = ora_int

        set_ack = _STATO["ack_per_ep"].setdefault(notif_key, set())
        era_gia_ack = _is_dispositivo_confermato_ack(set_ack, ep_risolto, ep_h, did)
        for ident in (ep_risolto, ep_h, did):
            if ident:
                set_ack.add(ident)

        tutti_ack = True
        for ep_s, inf_s in _STATO["subscriptions"].items():
            if not _is_dispositivo_confermato_ack(set_ack, ep_s, inf_s.get("ep_hash", ""), inf_s.get("device_id", "")):
                tutti_ack = False
                break
        if tutti_ack and _STATO["subscriptions"]:
            _STATO["notifiche_inviate"].add(notif_key)

        if not era_gia_ack:
            salva_db = True

    if salva_db:
        threading.Thread(target=_salva_stato_vapid_db, daemon=True).start()


def _invia_push_singolo(sub_info: dict, payload_dict: dict) -> tuple[int, str]:
    if not webpush:
        return 500, "Libreria pywebpush non installata"
    if not _STATO["vapid_private"]:
        return 500, "Chiave VAPID_PRIVATE_KEY mancante"

    ep = sub_info.get("endpoint", "")
    parsed = urllib.parse.urlparse(ep)
    aud = f"{parsed.scheme}://{parsed.netloc}" if (parsed.scheme and parsed.netloc) else ""
    claims = {"sub": "mailto:notifiche@extralargefamily.it"}
    if aud:
        claims["aud"] = aud

    data_json = json.dumps(payload_dict)

    for tentativo in range(2):
        try:
            webpush(
                subscription_info=sub_info,
                data=data_json,
                vapid_private_key=_STATO["vapid_private"],
                vapid_claims=claims,
                ttl=3600,
                headers={"Urgency": "high", "TTL": "3600"},
                timeout=10
            )
            return 201, "OK"
        except WebPushException as ex:
            code = ex.response.status_code if ex.response is not None else 500
            if code in [401, 403, 404, 410] or tentativo == 1:
                return code, f"HTTP {code}"
            time.sleep(0.4)
        except Exception as ex:
            if tentativo == 1:
                return 500, str(ex)
            time.sleep(0.4)
    return 500, "Errore invio"


def _invia_push_a_tutti_sync(payload_dict: dict, chiave_notifica: str = "") -> bool:
    """
    Invia la notifica ai telefoni registrati che non hanno ancora confermato con ACK.
    Restituisce True solo quando tutti i dispositivi attivi hanno confermato l'ACK.
    """
    if not webpush or not _STATO["vapid_private"]:
        return False

    with _LOCK:
        items = list(_STATO["subscriptions"].items())
        gia_inviati_http = set(_STATO["inviate_per_ep"].get(chiave_notifica, set())) if chiave_notifica else set()
        gia_confermati_ack = set(_STATO["ack_per_ep"].get(chiave_notifica, set())) if chiave_notifica else set()

    if not items:
        return False

    almeno_uno_inviato = False
    da_rimuovere = []
    destinatari_validi = []

    for ep, info in items:
        ep_h = info.get("ep_hash") or _calcola_hash_ep(ep)
        did = info.get("device_id") or ep_h
        destinatari_validi.append((ep, ep_h, did))

        if chiave_notifica and _is_dispositivo_confermato_ack(gia_confermati_ack, ep, ep_h, did):
            almeno_uno_inviato = True
            continue

        payload_ep = dict(payload_dict)
        if chiave_notifica:
            payload_ep["notif_key"] = chiave_notifica
        payload_ep["ep_hash"] = ep_h
        payload_ep["device_id"] = did

        code, msg = _invia_push_singolo(info["sub"], payload_ep)
        if code in [200, 201, 202]:
            almeno_uno_inviato = True
            with _LOCK:
                _STATO["fallimenti_ep"].pop(ep, None)
            if chiave_notifica:
                gia_inviati_http.add(ep)
        elif code in [401, 403, 404, 410]:
            with _LOCK:
                cnt = _STATO["fallimenti_ep"].get(ep, 0) + 1
                _STATO["fallimenti_ep"][ep] = cnt
            if cnt >= 2:
                destinatari_validi = [d for d in destinatari_validi if d[0] != ep]
                da_rimuovere.append((ep, info.get("id_db"), did, ep_h))
        else:
            print(f"[NOTIFICHE] Retry programmato per endpoint ({code} {msg})", flush=True)

    if chiave_notifica:
        with _LOCK:
            _STATO["inviate_per_ep"][chiave_notifica] = gia_inviati_http

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
                    _STATO["did_to_ep"].pop(did, None)
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

    if chiave_notifica:
        with _LOCK:
            ack_attuali = set(_STATO["ack_per_ep"].get(chiave_notifica, set()))
        tutti_confermati = all(
            _is_dispositivo_confermato_ack(ack_attuali, ep_d, eph_d, did_d)
            for (ep_d, eph_d, did_d) in destinatari_validi
        )
        return len(destinatari_validi) > 0 and tutti_confermati

    return almeno_uno_inviato


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
            body: JSON.stringify({ notif_key: notifKey || "", ep_hash: epHash || "", did: did || "", tag: tag || "" }),
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
    const notifKey = data.notif_key || "";
    const epHash = data.ep_hash || "";
    const did = data.device_id || "";

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
            body: data.body || "",
            icon: "/icons/Icon-192.png",
            badge: "/icons/Icon-192.png",
            vibrate: stessaNotificaGiaVisibile ? [] : [350, 140, 350, 140, 500],
            tag: alertTag,
            silent: stessaNotificaGiaVisibile,
            renotify: !stessaNotificaGiaVisibile,
            requireInteraction: true,
            timestamp: Date.now(),
            data: { url: "/", notif_key: notifKey, ep_hash: epHash, did: did }
        };

        try {
            await self.registration.showNotification(data.title, options);
        } catch (errNotif) {
            await self.registration.showNotification(data.title, {
                body: data.body || "",
                tag: alertTag,
                requireInteraction: true,
                data: { url: "/", notif_key: notifKey, ep_hash: epHash, did: did }
            });
        }

        await confermaRicezionePush(notifKey, epHash, did, alertTag);
        await svuotaAckPendenti();
        await aggiornaContatoreNonLette();
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
            if (!did) did = generaIdHardwareStabile();
            localStorage.setItem("elf_device_id", did);
            document.cookie = "elf_did=" + encodeURIComponent(did) + "; path=/; max-age=31536000; SameSite=Lax";
        } catch (e) {}
        return did || generaIdHardwareStabile();
    }

    function isStandalonePWA() {
        try {
            return (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) || (window.navigator && window.navigator.standalone === true);
        } catch (e) { return false; }
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

    function urlBase64ToUint8Array(base64String) {
        const padding = "=".repeat((4 - base64String.length % 4) % 4);
        const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
        const rawData = window.atob(base64);
        return Uint8Array.from([...rawData].map((c) => c.charCodeAt(0)));
    }

    function estraiChiaviSub(subObj) {
        if (!subObj) return { endpoint: "", p256dh: "", auth: "" };
        try {
            const j = subObj.toJSON ? subObj.toJSON() : subObj;
            const k = (j && j.keys) || {};
            return { endpoint: (j && j.endpoint) || subObj.endpoint || "", p256dh: k.p256dh || "", auth: k.auth || "" };
        } catch (e) {
            return { endpoint: subObj.endpoint || "", p256dh: "", auth: "" };
        }
    }

    async function getDedicatedSwReg() {
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

    async function allineaBadgeEAckTendina(reg, epHash, did, endpointUrl, pendingKeys) {
        if (!reg || !reg.getNotifications) return;
        try {
            const setPending = new Set(Array.isArray(pendingKeys) ? pendingKeys : []);
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

            if (permGranted && !mutedLocal && reg && (cfg.token_disallineato || !cfg.dispositivo_attivo) && cfg.vapid_public) {
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
                    const improntaSync = did + "|" + chiaviSub.endpoint + "|" + chiaviSub.p256dh + "|" + chiaviSub.auth;
                    if (ultimoTokenSincronizzato !== improntaSync) {
                        const resSub = await fetch("/api/notifiche/subscribe", {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ sub: sub, device_id: did, hw_id: hwId, silent_sync: true, send_test: false })
                        });
                        if (resSub && resSub.ok) {
                            const dataSub = await resSub.json();
                            ultimoTokenSincronizzato = improntaSync;
                            cfg.dispositivo_attivo = true;
                            if (dataSub.ep_hash) cfg.ep_hash = dataSub.ep_hash;
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
    setInterval(() => { if (!document.hidden) sincronizzaDispositivo(); }, 8000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) sincronizzaDispositivo(); });
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
        if (!did) did = generaIdHardwareStabile();
        localStorage.setItem("elf_device_id", did);
        document.cookie = "elf_did=" + encodeURIComponent(did) + "; path=/; max-age=31536000; SameSite=Lax";
    } catch (e) {}
    return did || generaIdHardwareStabile();
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

        st.innerText = "⏳ Registrazione di questo telefono in corso...";
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
        st.innerText = "✅ Permessi attivati! Ora verifica i punti 3 e 4 nel riquadro giallo qui sotto.";
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


def _salva_sub_db_sync(ep: str, sub_obj: dict, did: str, ep_h: str, hw_id: str = ""):
    sb = _STATO["supabase"]
    ora_int = int(time.time())
    id_db = None
    ids_extra_da_pulire = []

    with _LOCK:
        esistente = _STATO["subscriptions"].get(ep)
        ultimo_ack = esistente.get("ultimo_ack_ts", 0) if esistente else 0
        if esistente:
            id_db = esistente.get("id_db")

    record_json = json.dumps({
        "sub": sub_obj,
        "device_id": did,
        "ep_hash": ep_h,
        "hw_id": hw_id,
        "ultimo_accesso_ts": ora_int,
        "ultimo_ack_ts": ultimo_ack
    })

    if sb:
        with _DB_LOCK:
            try:
                res_ex = sb.table("menu_giornalieri").select("id, subcategoria").eq("data_menu", "1970-01-01").eq("categoria", CAT_SUB_DB).execute()
                for r_s in (res_ex.data or []):
                    try:
                        raw_s = json.loads(r_s.get("subcategoria") or "{}")
                        s_inner = raw_s.get("sub") if "sub" in raw_s else raw_s
                        r_ep = (s_inner.get("endpoint") or "").strip()
                        r_did = (raw_s.get("device_id") or "").strip()
                        r_hw = (raw_s.get("hw_id") or "").strip()
                        if r_ep == ep or (did and r_did == did) or (hw_id and r_hw == hw_id):
                            if id_db is None:
                                id_db = r_s["id"]
                            elif r_s["id"] != id_db:
                                ids_extra_da_pulire.append(r_s["id"])
                    except Exception:
                        pass

                for id_extra in ids_extra_da_pulire:
                    try:
                        sb.table("menu_giornalieri").delete().eq("id", id_extra).execute()
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
    if getattr(app, "_elf_push_collegato", False):
        return
    setattr(app, "_elf_push_collegato", True)

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
    async def get_pagina_attiva(did: str = ""):
        html = PAGINA_ATTIVA_HTML.replace("__DID_PLACEHOLDER__", (did or "").strip())
        return HTMLResponse(
            content=html,
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"}
        )

    @app.get("/api/notifiche/ack")
    @app.post("/api/notifiche/ack")
    async def handle_ack(req: fastapi.Request):
        if not _STATO["inizializzato"]:
            await asyncio.to_thread(_carica_o_inizializza_config_db)
        payload = {}
        if req.method == "POST":
            try:
                payload = await req.json()
            except Exception:
                payload = {}

        notif_key = str(payload.get("notif_key") or req.query_params.get("notif_key") or "").strip()
        ep = str(payload.get("endpoint") or req.query_params.get("endpoint") or "").strip()
        ep_h = str(payload.get("ep_hash") or req.query_params.get("ep_hash") or req.cookies.get("elf_ep_h") or "").strip()
        did = str(payload.get("did") or req.query_params.get("did") or req.cookies.get("elf_did") or "").strip()
        tag = str(payload.get("tag") or req.query_params.get("tag") or "").strip()

        if ep and not ep_h:
            ep_h = _calcola_hash_ep(ep)

        _registra_ack_interno(notif_key, ep, ep_h, did, tag)
        return JSONResponse({"ok": True})

    @app.get("/api/notifiche/status")
    async def get_status(req: fastapi.Request):
        if not _STATO["inizializzato"]:
            await asyncio.to_thread(_carica_o_inizializza_config_db)
        elif time.time() - _STATO["ultimo_sync_subs_ts"] > 10.0:
            await asyncio.to_thread(_carica_o_inizializza_config_db, True)

        fp, ua_h = _calcola_fp_http(req)
        did = (req.query_params.get("did") or req.cookies.get("elf_did") or "").strip()
        hw_id = (req.query_params.get("hw_id") or "").strip()
        ep = req.query_params.get("endpoint", "").strip()
        p256dh_q = req.query_params.get("p256dh", "").strip()
        auth_q = req.query_params.get("auth", "").strip()
        ep_h_cookie = (req.cookies.get("elf_ep_h") or "").strip()
        perm = req.query_params.get("perm", "0")
        standalone = req.query_params.get("standalone", "0")
        persisted = req.query_params.get("persisted", "0")
        os_cfg = req.query_params.get("os_cfg", "0")

        ep_h = _calcola_hash_ep(ep) if ep else ep_h_cookie

        with _LOCK:
            if fp and did:
                _STATO["fp_to_did"][fp] = did
            if ua_h and did:
                _STATO["ua_to_did"][ua_h] = did

            unmute_cmd = bool((did and did in _STATO["unmute_devices"]) or (ep_h and ep_h in _STATO["unmute_devices"]))
            if unmute_cmd:
                _STATO["unmute_devices"].discard(did)
                _STATO["unmute_devices"].discard(ep_h)

            force_unsub = bool((did and did in _STATO["force_unsub_devices"]) or (ep_h and ep_h in _STATO["force_unsub_devices"]))
            if force_unsub:
                _STATO["force_unsub_devices"].discard(did)
                _STATO["force_unsub_devices"].discard(ep_h)

            info_sub = _STATO["subscriptions"].get(ep) if ep else None
            info_sub_per_did = None
            for ep_cand, info_cand in _STATO["subscriptions"].items():
                if (did and (info_cand.get("device_id") == did or info_cand.get("ep_hash") == did)) or (hw_id and info_cand.get("hw_id") == hw_id):
                    info_sub_per_did = (ep_cand, info_cand)
                    break

            if not info_sub and info_sub_per_did and (not ep or info_sub_per_did[0] == ep):
                info_sub = info_sub_per_did[1]
                if not ep_h:
                    ep_h = info_sub.get("ep_hash") or ""

            if info_sub:
                info_sub["ultimo_accesso_ts"] = int(time.time())

            token_disallineato = False
            if perm == "1" and not force_unsub:
                if info_sub_per_did is not None:
                    ep_db, cand_db = info_sub_per_did
                    keys_db = ((cand_db.get("sub") or {}).get("keys") or {})
                    if not ep or ep != ep_db:
                        token_disallineato = True
                    elif (p256dh_q and p256dh_q != keys_db.get("p256dh", "")) or (auth_q and auth_q != keys_db.get("auth", "")):
                        token_disallineato = True
                elif ep and info_sub is None:
                    token_disallineato = True

            disp_attivo = (info_sub is not None) and bool(ep) and not force_unsub and (perm == "1") and not token_disallineato
            if ep_h:
                _STATO["stato_device"][ep_h] = disp_attivo
            if did:
                _STATO["stato_device"][did] = disp_attivo
                _STATO["diag_device"][did] = {
                    "perm": perm,
                    "standalone": standalone,
                    "persisted": persisted,
                    "os_cfg": os_cfg,
                    "attivo": disp_attivo
                }

            pending_ack_keys = []
            if info_sub and disp_attivo:
                for k_job, inviati_set in _STATO["inviate_per_ep"].items():
                    ack_set = _STATO["ack_per_ep"].get(k_job, set())
                    if ep in inviati_set and not _is_dispositivo_confermato_ack(ack_set, ep, ep_h, did):
                        pending_ack_keys.append(k_job)

        if ep or did:
            _associa_device_a_pagina(did, ep, disp_attivo=disp_attivo, fp=fp, ua_h=ua_h)

        return JSONResponse({
            "attive": disp_attivo,
            "dispositivo_registrato": disp_attivo,
            "dispositivo_attivo": disp_attivo,
            "token_disallineato": token_disallineato,
            "pending_ack_keys": pending_ack_keys,
            "ep_hash": ep_h if disp_attivo else "",
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
        fp, ua_h = _calcola_fp_http(req)
        payload = await req.json()
        sub_obj = payload.get("sub") if isinstance(payload, dict) and "sub" in payload else payload
        send_test = bool(payload.get("send_test")) if isinstance(payload, dict) else False
        did = ((payload.get("device_id") if isinstance(payload, dict) else "") or req.cookies.get("elf_did") or "").strip()
        hw_id = ((payload.get("hw_id") if isinstance(payload, dict) else "") or "").strip()

        ep = (sub_obj.get("endpoint") or "").strip() if isinstance(sub_obj, dict) else ""
        ep_h = _calcola_hash_ep(ep)
        push_status = 200
        push_msg = "OK"

        if ep:
            with _LOCK:
                if fp and did:
                    _STATO["fp_to_did"][fp] = did
                if ua_h and did:
                    _STATO["ua_to_did"][ua_h] = did

                for k in (did, ep_h):
                    if k:
                        _STATO["force_unsub_devices"].discard(k)
                        _STATO["unmute_devices"].discard(k)
                        _STATO["stato_device"][k] = True

                esistente = _STATO["subscriptions"].get(ep)
                for old_ep, old_info in list(_STATO["subscriptions"].items()):
                    if old_ep != ep and ((did and old_info.get("device_id") == did) or (hw_id and old_info.get("hw_id") == hw_id)):
                        if esistente is None and old_info.get("id_db"):
                            esistente = old_info
                        old_eph = old_info.get("ep_hash")
                        if old_eph and old_eph != ep_h:
                            _STATO["hash_to_ep"].pop(old_eph, None)
                            _STATO["stato_device"].pop(old_eph, None)
                        _STATO["fallimenti_ep"].pop(old_ep, None)
                        _STATO["subscriptions"].pop(old_ep, None)

                _STATO["subscriptions"][ep] = {
                    "id_db": esistente.get("id_db") if esistente else None,
                    "sub": sub_obj,
                    "ep_hash": ep_h,
                    "device_id": did or ep_h,
                    "hw_id": hw_id,
                    "ultimo_accesso_ts": int(time.time()),
                    "ultimo_ack_ts": esistente.get("ultimo_ack_ts", 0) if esistente else 0
                }
                _STATO["hash_to_ep"][ep_h] = ep
                if did:
                    _STATO["did_to_ep"][did] = ep
                _STATO["fallimenti_ep"].pop(ep, None)

            _associa_device_a_pagina(did or ep_h, ep, disp_attivo=True, fp=fp, ua_h=ua_h)
            _aggiorna_ui_pagine()
            await asyncio.to_thread(_salva_sub_db_sync, ep, sub_obj, did or ep_h, ep_h, hw_id)

            if send_test:
                ora_str = _ora_italia().strftime("%H:%M")
                push_status, push_msg = await asyncio.to_thread(_invia_push_singolo, sub_obj, {
                    "title": f"🔔 Notifica di Prova ELF ({ora_str})",
                    "body": "✅ Questo dispositivo è registrato in modo indipendente e riceve tutti gli avvisi.",
                    "tag": f"elf_test_{int(time.time())}",
                    "ep_hash": ep_h,
                    "device_id": did or ep_h
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

        if path in ["/", "/index.html"]:
            fp, ua_h = _calcola_fp_http(request)
            ep_h_cookie = (request.cookies.get("elf_ep_h") or "").strip()
            did_cookie = (request.cookies.get("elf_did") or "").strip()
            with _LOCK:
                _STATO["ultimo_ep_attivo"] = ep_h_cookie
                _STATO["ultimo_did_attivo"] = did_cookie
                if fp and did_cookie:
                    _STATO["fp_to_did"][fp] = did_cookie
                if ua_h and did_cookie:
                    _STATO["ua_to_did"][ua_h] = did_cookie

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
            prefisso = ("// v-elf-push-28\n" + SW_PUSH_HANDLERS_JS + "\n").encode("utf-8")
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
