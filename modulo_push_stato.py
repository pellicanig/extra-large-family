import os
import json
import base64
import hashlib
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import Request
import flet as ft

try:
    from supabase import create_client
except ImportError:
    create_client = None

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

TZ_ITALIA = ZoneInfo("Europe/Rome")
VAPID_CLAIMS_EMAIL = os.environ.get("VAPID_CLAIMS_EMAIL", "mailto:notifiche@extralargefamily.it").strip()
VAPID_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".vapid_keys_cache.json")

INTERVALLO_RETRY_SECONDI = 45       # Ritrasmissione ogni 45s dentro la finestra per chi non ha inviato ACK
DURATA_MASSIMA_CODA_SECONDI = 1800 # 30 minuti di validità massima per singola notifica d'impegno

_STATO = {
    "supabase": None,
    "supabase_url": "",
    "supabase_key": "",
    "inizializzato": False,
    "vapid_public": "",
    "vapid_private": "",
    # endpoint -> {
    #   "id_db": int|None, "sub": dict, "endpoint": str, "p256dh": str, "auth": str,
    #   "muted": bool, "ep_hash": str, "device_id": str, "hw_id": str,
    #   "eventi_in_essere": list, "stato_consegna": dict,
    #   "ultimo_accesso_ts": int, "ultimo_ack_ts": int
    # }
    "subscriptions": {},
    "hash_to_ep": {},           # ep_hash -> endpoint
    "did_to_ep": {},            # device_id -> endpoint
    "stato_device": {},         # device_id / ep_hash -> bool
    "diag_device": {},          # device_id -> dict diagnostica impostazioni telefono
    "fp_to_did": {},            # impronta (ip|ua) -> device_id
    "ua_to_did": {},            # impronta (ua) -> device_id
    "ultimo_ep_attivo": "",
    "ultimo_did_attivo": "",
    "ultimo_sync_db_ts": 0.0,
    "force_unsub_devices": set(),
    "unmute_devices": set(),
    "data_corrente": "",
    "residui_attuali": 0,
    "cache_famiglia": {},
    "cache_categorie": {},
    "ultimo_sync_anagrafica": 0.0,
    "coda_impegni_push": {},    # chiave_notifica -> dict job
    "inviate_per_ep": {},       # chiave_notifica -> set(endpoint che hanno ricevuto HTTP 201)
    "ack_per_ep": {},           # chiave_notifica -> set(ep / ep_hash / did che hanno confermato ACK)
    "impegni_notificati_oggi": {},  # id_impegno -> set(chiavi_notifica generate oggi)
    "fallimenti_ep": {},        # endpoint -> conteggio errori 404/410 consecutivi
    "report_consegne": {},      # chiave_notifica -> {"ora": str, "consegnati": int, "ack": int, "totale": int, "in_coda": int}
    "ultimo_keepalive": 0.0,
    "url_pubblico": "",
    "scheduler_avviato": False,
    "evento_ricalcolo": threading.Event(),
    "pagine_attive": set(),
}

_LOCK = threading.Lock()
_DB_LOCK = threading.Lock()


def _ora_italia() -> datetime:
    return datetime.now(TZ_ITALIA)


def log_push(messaggio: str):
    ora_str = _ora_italia().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[ELF-PUSH {ora_str}] {messaggio}", flush=True)


def _calcola_hash_ep(ep: str) -> str:
    if not ep:
        return ""
    return hashlib.sha256(ep.strip().encode("utf-8")).hexdigest()[:16]


def _calcola_fp_http(req: Request) -> tuple[str, str]:
    try:
        xff = (req.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        ip = xff or (req.client.host if req.client else "") or ""
        ua = (req.headers.get("user-agent") or "").strip()
        fp = hashlib.sha256(f"{ip}|{ua}".encode("utf-8")).hexdigest()[:16] if (ip or ua) else ""
        ua_h = hashlib.sha256(ua.encode("utf-8")).hexdigest()[:16] if ua else ""
        return fp, ua_h
    except Exception:
        return "", ""


def _calcola_fp_page(page: ft.Page) -> tuple[str, str]:
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


def _pulisci_chiave_str(val: str) -> str:
    return (val or "").strip().strip('"').strip("'").strip().rstrip("=")


def _crea_client_supabase_dedicato(fallback_client=None):
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
                log_push(f"⚠️ Avviso creazione client Supabase dedicato: {ex}")
    return fallback_client or _STATO["supabase"]


def _rinnova_client_se_chiuso(ex: Exception):
    msg = str(ex)
    if any(k in msg for k in [
        "ConnectionState", "SEND_HEADERS", "RECV_", "Server disconnected",
        "RemoteProtocolError", "LocalProtocolError", "ReadTimeout", "WriteTimeout", "PoolTimeout"
    ]):
        log_push(f"🔄 Rilevata connessione Supabase chiusa ({msg[:80]}), rigenerazione client...")
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
        log_push(f"⚠️ Errore derivazione chiave VAPID: {ex}")
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


def _salva_vapid_su_db_e_disco(pub_key: str, priv_key: str):
    try:
        with open(VAPID_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"pub": pub_key, "priv": priv_key}, f)
    except Exception:
        pass

    sb = _STATO["supabase"]
    if not sb:
        return

    with _DB_LOCK:
        try:
            res_v = sb.table("push_subscriptions").select("*").or_("endpoint.like.vapid://%,device_id.eq.SYS_VAPID").limit(1).execute()
            if res_v.data:
                r0 = res_v.data[0]
                db_pub = str(r0.get("p256dh") or str(r0.get("endpoint") or "").replace("vapid://", "")).strip()
                db_priv = str(r0.get("auth") or "").strip()
                p_val, pr_val = _valida_o_deriva_coppia_vapid(db_pub, db_priv)
                if p_val and pr_val:
                    with _LOCK:
                        _STATO["vapid_public"] = p_val
                        _STATO["vapid_private"] = pr_val
                    try:
                        with open(VAPID_CACHE_FILE, "w", encoding="utf-8") as f:
                            json.dump({"pub": p_val, "priv": pr_val}, f)
                    except Exception:
                        pass
                    return

            payload_v = {
                "device_id": "SYS_VAPID",
                "hw_id": "SYS_VAPID",
                "endpoint": f"vapid://{pub_key}",
                "p256dh": pub_key,
                "auth": priv_key,
                "muted": False,
                "eventi_in_essere": [],
                "stato_consegna": {},
                "ultimo_accesso_ts": int(datetime.now().timestamp()),
            }
            if res_v.data:
                sb.table("push_subscriptions").update(payload_v).eq("id", res_v.data[0]["id"]).execute()
            else:
                sb.table("push_subscriptions").insert(payload_v).execute()
            log_push("🔑 Chiavi VAPID salvate su tabella push_subscriptions (SYS_VAPID).")
        except Exception as ex:
            _rinnova_client_se_chiuso(ex)
            log_push(f"⚠️ Impossibile salvare SYS_VAPID su push_subscriptions: {ex}")
