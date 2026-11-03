import os
import gc
import json
import time
import threading
import traceback
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from modulo_push_stato import (
    _STATO,
    _LOCK,
    _DB_LOCK,
    VAPID_CLAIMS_EMAIL,
    INTERVALLO_RETRY_SECONDI,
    DURATA_MASSIMA_CODA_SECONDI,
    log_push,
    _ora_italia,
    _calcola_hash_ep,
    _rinnova_client_se_chiuso,
)
from modulo_push_registrazione import (
    _ricarica_subscriptions_da_db_sync,
    _carica_o_inizializza_config_db,
    _aggiorna_ui_pagine,
)

try:
    from pywebpush import webpush, WebPushException
except ImportError:
    webpush = None
    WebPushException = Exception


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


def _formatta_minuti_hhmm(min_tot: int) -> str:
    m = max(0, min(1439, int(min_tot)))
    return f"{m // 60:02d}:{m % 60:02d}"


def _is_dispositivo_confermato_ack(ack_set: set, ep: str, ep_h: str, did: str) -> bool:
    if not ack_set:
        return False
    return bool(
        (ep and ep in ack_set)
        or (ep_h and ep_h in ack_set)
        or (did and did in ack_set)
    )


def _invia_push_singolo(sub_info: dict, payload_dict: dict) -> tuple[int, str]:
    """Invio singolo con doppio tentativo immediato (0.4s) per annullare micro-errori di rete."""
    if not webpush:
        return 500, "Libreria pywebpush non installata"
    if not _STATO["vapid_private"]:
        return 500, "Chiave VAPID_PRIVATE_KEY mancante"

    ep = sub_info.get("endpoint", "")
    parsed = urllib.parse.urlparse(ep)
    aud = f"{parsed.scheme}://{parsed.netloc}" if (parsed.scheme and parsed.netloc) else ""
    claims = {"sub": VAPID_CLAIMS_EMAIL}
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
            body_err = ""
            try:
                if ex.response is not None and ex.response.text:
                    body_err = ex.response.text[:120]
            except Exception:
                pass
            if code in [401, 403, 404, 410] or tentativo == 1:
                return code, f"HTTP {code} {body_err}".strip()
            time.sleep(0.4)
        except Exception as ex:
            if tentativo == 1:
                return 500, str(ex)
            time.sleep(0.4)
    return 500, "Errore invio"


def _invia_push_a_tutti_sync(payload_dict: dict, chiave_notifica: str = "", forza_ritrasmissione_non_ack: bool = False) -> bool:
    """
    Invia la notifica a tutti i telefoni registrati in push_subscriptions che non hanno ancora confermato con ACK.
    Restituisce True solo quando TUTTI i dispositivi attivi hanno confermato la ricezione con ACK.
    """
    if not webpush:
        log_push("❌ Impossibile inviare push: pywebpush non installato!")
        return False
    if not _STATO["vapid_private"]:
        log_push("❌ Impossibile inviare push: chiave VAPID privata assente!")
        return False

    with _LOCK:
        items = list(_STATO["subscriptions"].items())
        gia_inviati_http = set(_STATO["inviate_per_ep"].get(chiave_notifica, set())) if chiave_notifica else set()
        gia_confermati_ack = set(_STATO["ack_per_ep"].get(chiave_notifica, set())) if chiave_notifica else set()

    if not items:
        log_push(f"⚠️ Nessun dispositivo registrato in push_subscriptions per inviare '{chiave_notifica or payload_dict.get('title', 'push')}'.")
        return False

    da_rimuovere = []
    destinatari_validi = []
    inviati_ora = 0

    for ep, info in items:
        if info.get("muted"):
            continue
        ep_h = info.get("ep_hash") or _calcola_hash_ep(ep)
        did = info.get("device_id") or ep_h
        destinatari_validi.append((ep, ep_h, did))

        if chiave_notifica and _is_dispositivo_confermato_ack(gia_confermati_ack, ep, ep_h, did):
            continue

        if chiave_notifica and not forza_ritrasmissione_non_ack and ep in gia_inviati_http:
            continue

        payload_ep = dict(payload_dict)
        if chiave_notifica:
            payload_ep["notif_key"] = chiave_notifica
        payload_ep["ep_hash"] = ep_h
        payload_ep["device_id"] = did

        code, msg = _invia_push_singolo(info["sub"], payload_ep)
        if code in [200, 201, 202]:
            inviati_ora += 1
            with _LOCK:
                _STATO["fallimenti_ep"].pop(ep, None)
            if chiave_notifica:
                gia_inviati_http.add(ep)
            log_push(f"📤 Inviata push [{chiave_notifica or 'diretta'}] a [dev={did}] -> HTTP {code} OK")
        elif code in [401, 403, 404, 410]:
            with _LOCK:
                cnt = _STATO["fallimenti_ep"].get(ep, 0) + 1
                _STATO["fallimenti_ep"][ep] = cnt
                _STATO.setdefault("rinnovo_token_devices", set()).add(did)
            log_push(f"⚠️ Endpoint rifiutato da FCM/Push [dev={did}] -> {msg} (fallimento #{cnt}, prenotato rinnovo automatico token all'accesso)")
            if cnt >= 2:
                destinatari_validi = [d for d in destinatari_validi if d[0] != ep]
                da_rimuovere.append((ep, info.get("id_db"), did, ep_h))
                log_push(f"🗑️ Endpoint scaduto rimosso [dev={did}] ({code}): verrà rigenerato automaticamente appena il dispositivo apre l'app")
        else:
            log_push(f"⚠️ Errore temporaneo invio push a [dev={did}] ({code} - {msg}) -> riproverò al prossimo ciclo")

    totale_destinatari = len(destinatari_validi)

    if chiave_notifica:
        with _LOCK:
            _STATO["inviate_per_ep"][chiave_notifica] = gia_inviati_http
            ack_attuali = set(_STATO["ack_per_ep"].get(chiave_notifica, set()))
            num_ack_reali = sum(
                1 for (ep_d, eph_d, did_d) in destinatari_validi
                if _is_dispositivo_confermato_ack(ack_attuali, ep_d, eph_d, did_d)
            )
            _STATO["report_consegne"][chiave_notifica] = {
                "ora": _ora_italia().strftime("%H:%M:%S"),
                "consegnati": len(gia_inviati_http),
                "ack": num_ack_reali,
                "totale": totale_destinatari,
                "in_coda": max(0, totale_destinatari - num_ack_reali),
            }

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
                for ep, id_db, _, _ in da_rimuovere:
                    try:
                        if id_db:
                            sb.table("push_subscriptions").delete().eq("id", id_db).execute()
                        else:
                            sb.table("push_subscriptions").delete().eq("endpoint", ep).execute()
                    except Exception as ex:
                        _rinnova_client_se_chiuso(ex)
        _aggiorna_ui_pagine()

    if chiave_notifica:
        with _LOCK:
            rep_curr = _STATO["report_consegne"].get(chiave_notifica, {})
            ack_finali = rep_curr.get("ack", 0)
        return totale_destinatari > 0 and ack_finali >= totale_destinatari

    return inviati_ora > 0


def _salva_ack_su_db_async():
    """
    Aggiorna sulla riga di ciascun telefono in push_subscriptions:
    - eventi_in_essere: lista di tutti gli impegni in essere (di oggi e futuri)
    - stato_consegna: mappa {chiave_notifica: "CONSEGNATA (...)" | "IN_CODA (...)" | "PROGRAMMATA (...)"}
    - ultimo_accesso_ts: timestamp reale dell'ultimo accesso/ACK
    """
    sb = _STATO["supabase"]
    if not sb:
        return

    with _LOCK:
        programmati_map = dict(_STATO.get("eventi_programmati_map") or {})
        coda_keys = set(_STATO["coda_impegni_push"].keys())
        tutti_ev_keys = list(programmati_map.keys())
        for k_c in sorted(coda_keys):
            if k_c not in programmati_map:
                tutti_ev_keys.append(k_c)

        mappa_ack_snapshot = {k: set(_STATO["ack_per_ep"].get(k, set())) for k in tutti_ev_keys}
        subs_snapshot = list(_STATO["subscriptions"].items())

    aggiornamenti_db = []
    ora_int = int(time.time())

    for ep, info in subs_snapshot:
        if info.get("muted"):
            continue
        id_db = info.get("id_db")
        ep_h = info.get("ep_hash") or _calcola_hash_ep(ep)
        did = info.get("device_id") or ep_h

        vecchio_st = info.get("stato_consegna") if isinstance(info.get("stato_consegna"), dict) else {}
        ultimo_ack_ts_disp = int(info.get("ultimo_ack_ts") or vecchio_st.get("_ultimo_ack_ts") or 0)
        ultimo_accesso_disp = int(info.get("ultimo_accesso_ts") or ora_int)

        ev_disp = list(tutti_ev_keys)
        st_disp = {}
        if ultimo_ack_ts_disp > 0:
            st_disp["_ultimo_ack_ts"] = ultimo_ack_ts_disp

        for k_ev in tutti_ev_keys:
            info_prog = programmati_map.get(k_ev, {})
            tit_breve = info_prog.get("titolo", "")
            label_prog = info_prog.get("stato_programmata", "PROGRAMMATA")
            ack_set_ev = mappa_ack_snapshot.get(k_ev, set())
            consegnata = _is_dispositivo_confermato_ack(ack_set_ev, ep, ep_h, did)

            if consegnata:
                st_disp[k_ev] = f"CONSEGNATA ({tit_breve})" if tit_breve else "CONSEGNATA"
            elif k_ev in coda_keys:
                st_disp[k_ev] = f"IN_CODA ({tit_breve})" if tit_breve else "IN_CODA"
            else:
                st_disp[k_ev] = label_prog

        vecchi_ev = info.get("eventi_in_essere") or []
        if ev_disp != vecchi_ev or st_disp != vecchio_st:
            with _LOCK:
                if ep in _STATO["subscriptions"]:
                    _STATO["subscriptions"][ep]["eventi_in_essere"] = ev_disp
                    _STATO["subscriptions"][ep]["stato_consegna"] = st_disp
            aggiornamenti_db.append((id_db, ep, did, ev_disp, st_disp, ultimo_accesso_disp))

    if not aggiornamenti_db:
        return

    with _DB_LOCK:
        for id_db, ep, did, ev_disp, st_disp, ts_disp in aggiornamenti_db:
            try:
                upd_payload = {
                    "device_id": did,
                    "eventi_in_essere": ev_disp,
                    "stato_consegna": st_disp,
                    "ultimo_accesso_ts": ts_disp,
                }
                if id_db:
                    sb.table("push_subscriptions").update(upd_payload).eq("id", id_db).execute()
                else:
                    sb.table("push_subscriptions").update(upd_payload).eq("endpoint", ep).execute()
                log_push(f"💾 Tabella push_subscriptions aggiornata per [dev={did}]: {len(ev_disp)} notifiche in essere.")
            except Exception as ex:
                _rinnova_client_se_chiuso(ex)
                log_push(f"⚠️ Errore aggiornamento stato_consegna su DB per [dev={did}]: {ex}")


def _registra_ack_interno(notif_key: str, ep: str, ep_h: str, did: str, tag: str):
    ora_int = int(time.time())
    salva_db = False

    with _LOCK:
        if not notif_key and tag:
            for k_job, job_info in _STATO["coda_impegni_push"].items():
                if job_info.get("payload", {}).get("tag") == tag:
                    notif_key = k_job
                    break

        if not notif_key:
            return

        ep_risolto = ep
        if not ep_risolto and ep_h and ep_h in _STATO["hash_to_ep"]:
            ep_risolto = _STATO["hash_to_ep"][ep_h]
        if not ep_risolto and did and did in _STATO["did_to_ep"]:
            ep_risolto = _STATO["did_to_ep"][did]

        info_sub = _STATO["subscriptions"].get(ep_risolto) if ep_risolto else None
        if not info_sub:
            for info_k in _STATO["subscriptions"].values():
                if (did and info_k.get("device_id") == did) or (ep_h and info_k.get("ep_hash") == ep_h):
                    info_sub = info_k
                    ep_risolto = info_k.get("endpoint") or ep_risolto
                    break

        if info_sub:
            ep_h = ep_h or info_sub.get("ep_hash") or _calcola_hash_ep(ep_risolto)
            did = did or info_sub.get("device_id") or ep_h
            info_sub["ultimo_accesso_ts"] = ora_int
            info_sub["ultimo_ack_ts"] = ora_int
            if isinstance(info_sub.get("stato_consegna"), dict):
                info_sub["stato_consegna"]["_ultimo_ack_ts"] = ora_int

        set_ack = _STATO["ack_per_ep"].setdefault(notif_key, set())
        era_gia_confermato = _is_dispositivo_confermato_ack(set_ack, ep_risolto, ep_h, did)

        for identificativo in (ep_risolto, ep_h, did):
            if identificativo:
                set_ack.add(identificativo)

        if not era_gia_confermato:
            rep = _STATO["report_consegne"].get(notif_key)
            if rep:
                rep["ack"] = min(rep.get("totale", 1), rep.get("ack", 0) + 1)
                rep["in_coda"] = max(0, rep.get("totale", 0) - rep["ack"])
            log_push(f"✅ ACK RICEVUTO da dispositivo [dev={did or ep_h}] per notifica '{notif_key}'!")
            _STATO["evento_ricalcolo"].set()
            salva_db = True

    if salva_db:
        threading.Thread(target=_salva_ack_su_db_async, daemon=True).start()


def _esegui_keepalive_render():
    ora_ts = time.time()
    if ora_ts - _STATO["ultimo_keepalive"] < 480:
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
            req = urllib.request.Request(f"{url_base}/api/notifiche/ping", headers={"User-Agent": "ELF-KeepAlive/2.0"})
            with urllib.request.urlopen(req, timeout=8) as _:
                pass
        except Exception:
            pass

    threading.Thread(target=_ping, daemon=True).start()


def _aggiorna_coda_impegni_da_db():
    """
    Legge TUTTI gli impegni (di oggi e futuri, singoli e periodici) da Supabase e:
    1) Popola _STATO['eventi_programmati_map'] con tutti gli impegni in essere (oggi e futuri)
       così da mostrarli subito in push_subscriptions (eventi_in_essere e stato_consegna).
    2) Accoda in _STATO['coda_impegni_push'] gli avvisi quando mancano <= 15 min all'inizio (start15) o alla fine (end15).
    3) Chiude sulle tendine dei telefoni gli impegni cancellati.
    """
    sb = _STATO["supabase"]
    if not sb:
        return

    adesso = _ora_italia()
    oggi_str = adesso.strftime("%Y-%m-%d")
    oggi_date = adesso.date()
    minuti_ora = adesso.hour * 60 + adesso.minute
    ora_ts = time.time()

    with _LOCK:
        if _STATO["data_corrente"] != oggi_str:
            log_push(f"📅 Cambio giorno rilevato ({oggi_str}): pulizia code notifiche del giorno precedente.")
            _STATO["data_corrente"] = oggi_str
            _STATO["coda_impegni_push"] = {k: v for k, v in _STATO["coda_impegni_push"].items() if oggi_str in k}
            _STATO["inviate_per_ep"] = {k: v for k, v in _STATO["inviate_per_ep"].items() if oggi_str in k}
            _STATO["ack_per_ep"] = {k: v for k, v in _STATO["ack_per_ep"].items() if oggi_str in k}
            _STATO["impegni_notificati_oggi"].clear()

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
                log_push(f"⚠️ Avviso lettura anagrafica famiglia/categorie: {ex}")

    with _DB_LOCK:
        try:
            res = sb.table("impegni").select("*, impegni_familiari(id_familiare)").lte("data_evento", oggi_str).execute()
            tutti = res.data or []
        except Exception as ex:
            _rinnova_client_se_chiuso(ex)
            log_push(f"❌ Errore lettura tabella impegni: {ex}")
            return

    # Carica in push_subscriptions ESCLUSIVAMENTE gli impegni di OGGI (singoli e periodici attivi oggi)
    istanze_attive = []

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
            escluse = {x.strip() for x in (imp.get("date_escluse") or "").split(",") if x.strip()}
            if oggi_str in escluse:
                continue
            g_str = imp.get("giorni_periodici") or ""
            giorni_rep = [int(g) for g in g_str.split(",") if g.strip().isdigit()] if g_str else [d_start.weekday()]
            if oggi_date.weekday() in giorni_rep:
                copia = dict(imp)
                copia["data_evento_istanza"] = oggi_str
                istanze_attive.append(copia)
        else:
            if str(imp.get("data_evento") or "").strip() == oggi_str:
                copia = dict(imp)
                copia["data_evento_istanza"] = oggi_str
                istanze_attive.append(copia)

    istanze_attive.sort(key=lambda x: (x.get("data_evento_istanza", ""), x.get("ora_inizio") or "00:00"))

    residui_oggi = 0
    ids_presenti_oggi = set()
    nuove_accodate = 0
    nuova_mappa_programmati = {}

    for ev in istanze_attive:
        id_ev = str(ev.get("id"))
        d_ev = ev.get("data_evento_istanza") or oggi_str
        is_oggi = (d_ev == oggi_str)
        if is_oggi:
            ids_presenti_oggi.add(id_ev)

        try:
            dt_vis = datetime.strptime(d_ev, "%Y-%m-%d").strftime("%d/%m")
        except Exception:
            dt_vis = d_ev

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

        if is_oggi and fine_effettiva > minuti_ora:
            residui_oggi += 1

        ids_fam = [str(f["id_familiare"]) for f in (ev.get("impegni_familiari") or []) if f.get("id_familiare")]
        nomi_lista = [_STATO["cache_famiglia"].get(fid, "") for fid in ids_fam if _STATO["cache_famiglia"].get(fid)]
        chi_testo = ", ".join(nomi_lista) if nomi_lista else "Tutta la famiglia"
        nome_cat = _STATO["cache_categorie"].get(str(ev.get("id_categoria")), "IMPEGNO")

        if is_tutto_il_giorno or min_i < 0:
            chiave_allday = f"allday_{d_ev}_{id_ev}"
            nuova_mappa_programmati[chiave_allday] = {
                "titolo": f"{titolo} ({dt_vis})",
                "stato_programmata": f"TUTTO IL GIORNO ({dt_vis} - {titolo} • {chi_testo})"
            }
            continue

        ora_i_vis = ora_i_str[:5]
        ora_f_vis = "24:00" if min_f == 1440 else (ora_f_str[:5] if min_f > min_i else "")
        orario_label = f"{ora_i_vis} - {ora_f_vis}" if ora_f_vis else ora_i_vis
        riga_note = f"\n📝 {note}" if note else ""

        # --- 1. NOTIFICA 15 MINUTI PRIMA DELL'INIZIO ---
        chiave_start = f"start15_{d_ev}_{id_ev}_{ora_i_vis}"
        ora_invio_start = _formatta_minuti_hhmm(max(0, min_i - 15))
        min_mancanti_inizio = (min_i - minuti_ora) if is_oggi else 99999

        # Includiamo l'evento se è nel futuro, oppure se è oggi e non è ancora passato (o è già stato notificato oggi)
        includi_start = (not is_oggi) or (min_mancanti_inizio >= -5) or (chiave_start in _STATO["inviate_per_ep"]) or (chiave_start in _STATO["ack_per_ep"])
        if includi_start:
            nuova_mappa_programmati[chiave_start] = {
                "titolo": f"{titolo} {dt_vis} {ora_i_vis}",
                "stato_programmata": f"PROGRAMMATA (invio {dt_vis} ore {ora_invio_start} - {titolo} [{chi_testo}])"
            }

        if is_oggi and -5 <= min_mancanti_inizio <= 15:
            with _LOCK:
                gia_in_coda = chiave_start in _STATO["coda_impegni_push"]
                ack_set = _STATO["ack_per_ep"].get(chiave_start, set())
                subs_attive = [v for v in _STATO["subscriptions"].values() if not v.get("muted")]
                tutti_gia_ack = bool(subs_attive) and all(
                    _is_dispositivo_confermato_ack(ack_set, v["endpoint"], v["ep_hash"], v["device_id"])
                    for v in subs_attive
                )
                if not gia_in_coda and not tutti_gia_ack:
                    label_tra = f"Tra {max(0, min_mancanti_inizio)} min" if min_mancanti_inizio > 0 else "Iniziato ora"
                    _STATO["coda_impegni_push"][chiave_start] = {
                        "id_ev": id_ev,
                        "creato_ts": ora_ts,
                        "scade_ts": ora_ts + DURATA_MASSIMA_CODA_SECONDI,
                        "tentativi": 0,
                        "ultimo_invio_ts": 0.0,
                        "payload": {
                            "title": f"⏰ {label_tra} ({ora_i_vis}) • {chi_testo}",
                            "body": f"📌 [{nome_cat}] {titolo}\n🕒 Orario: {orario_label}{riga_note}",
                            "tag": f"elf_start_{id_ev}_{oggi_str}",
                            "notif_key": chiave_start,
                        }
                    }
                    _STATO["impegni_notificati_oggi"].setdefault(id_ev, set()).add(chiave_start)
                    nuove_accodate += 1
                    log_push(f"🔔 Accodata notifica INIZIO ({label_tra}): '{titolo}' ({ora_i_vis}) [{chiave_start}]")

        # --- 2. NOTIFICA 15 MINUTI PRIMA DELLA FINE ---
        if min_f > min_i:
            chiave_end = f"end15_{d_ev}_{id_ev}_{ora_f_vis}"
            ora_invio_end = _formatta_minuti_hhmm(max(0, min_f - 15))
            min_mancanti_fine = (min_f - minuti_ora) if is_oggi else 99999

            includi_end = (not is_oggi) or (min_mancanti_fine >= -5) or (chiave_end in _STATO["inviate_per_ep"]) or (chiave_end in _STATO["ack_per_ep"])
            if includi_end:
                nuova_mappa_programmati[chiave_end] = {
                    "titolo": f"Fine {titolo} {dt_vis} {ora_f_vis}",
                    "stato_programmata": f"PROGRAMMATA (invio {dt_vis} ore {ora_invio_end} - Fine {titolo} [{chi_testo}])"
                }

            if is_oggi and -5 <= min_mancanti_fine <= 15:
                with _LOCK:
                    gia_in_coda = chiave_end in _STATO["coda_impegni_push"]
                    ack_set = _STATO["ack_per_ep"].get(chiave_end, set())
                    subs_attive = [v for v in _STATO["subscriptions"].values() if not v.get("muted")]
                    tutti_gia_ack = bool(subs_attive) and all(
                        _is_dispositivo_confermato_ack(ack_set, v["endpoint"], v["ep_hash"], v["device_id"])
                        for v in subs_attive
                    )
                    if not gia_in_coda and not tutti_gia_ack:
                        label_fine = f"Termina tra {max(0, min_mancanti_fine)} min" if min_mancanti_fine > 0 else "Terminato ora"
                        _STATO["coda_impegni_push"][chiave_end] = {
                            "id_ev": id_ev,
                            "creato_ts": ora_ts,
                            "scade_ts": ora_ts + DURATA_MASSIMA_CODA_SECONDI,
                            "tentativi": 0,
                            "ultimo_invio_ts": 0.0,
                            "payload": {
                                "title": f"🏁 {label_fine} ({ora_f_vis}) • {chi_testo}",
                                "body": f"📌 [{nome_cat}] {titolo}\n🕒 Fine prevista: ore {ora_f_vis}{riga_note}",
                                "tag": f"elf_end_{id_ev}_{oggi_str}",
                                "notif_key": chiave_end,
                            }
                        }
                        _STATO["impegni_notificati_oggi"].setdefault(id_ev, set()).add(chiave_end)
                        nuove_accodate += 1
                        log_push(f"🏁 Accodata notifica FINE ({label_fine}): '{titolo}' ({ora_f_vis}) [{chiave_end}]")

    _STATO["residui_attuali"] = residui_oggi

    mappa_cambiata = False
    with _LOCK:
        vecchia_mappa = _STATO.get("eventi_programmati_map") or {}
        if nuova_mappa_programmati != vecchia_mappa:
            _STATO["eventi_programmati_map"] = nuova_mappa_programmati
            mappa_cambiata = True

    if mappa_cambiata:
        log_push(f"📋 Piano impegni sincronizzato: {len(nuova_mappa_programmati)} notifiche in essere (oggi + futuri) pronte in push_subscriptions.")

    # 3. Se un impegno già notificato oggi è stato eliminato, rimuovilo dalla coda e chiudi la notifica sui telefoni
    with _LOCK:
        ids_notificati = set(_STATO["impegni_notificati_oggi"].keys())
    eliminati = ids_notificati - ids_presenti_oggi
    if eliminati:
        tags_da_chiudere = []
        with _LOCK:
            for id_del in eliminati:
                chiavi_del = _STATO["impegni_notificati_oggi"].pop(id_del, set())
                for kd in chiavi_del:
                    _STATO["coda_impegni_push"].pop(kd, None)
                    _STATO["inviate_per_ep"].pop(kd, None)
                    _STATO["ack_per_ep"].pop(kd, None)
                tags_da_chiudere.append(f"elf_start_{id_del}_{oggi_str}")
                tags_da_chiudere.append(f"elf_end_{id_del}_{oggi_str}")
        log_push(f"🗑️ Impegno eliminato ({eliminati}): chiusura notifiche sui dispositivi ({tags_da_chiudere}).")
        _invia_push_a_tutti_sync({"close_tags": tags_da_chiudere})
        mappa_cambiata = True

    if mappa_cambiata or nuove_accodate > 0:
        threading.Thread(target=_salva_ack_su_db_async, daemon=True).start()


def _processa_coda_impegni():
    _esegui_keepalive_render()

    # Sincronizza i dispositivi da Supabase ogni 20 secondi
    if time.time() - _STATO["ultimo_sync_db_ts"] > 20.0:
        _ricarica_subscriptions_da_db_sync(log_sempre=False)

    _aggiorna_coda_impegni_da_db()

    with _LOCK:
        jobs = list(_STATO["coda_impegni_push"].items())

    if not jobs:
        return

    ora_ts = time.time()

    for chiave, job in jobs:
        if ora_ts > job.get("scade_ts", ora_ts + 1):
            with _LOCK:
                _STATO["coda_impegni_push"].pop(chiave, None)
            log_push(f"⏱️ Finestra temporale conclusa per '{chiave}', rimossa dalla coda attiva.")
            threading.Thread(target=_salva_ack_su_db_async, daemon=True).start()
            continue

        tentativi = job.get("tentativi", 0)
        ultimo_invio = job.get("ultimo_invio_ts", 0.0)
        primo_tentativo = (tentativi == 0)

        with _LOCK:
            rep = _STATO["report_consegne"].get(chiave, {})
            tot_dest = rep.get("totale", 0)
            ack_cnt = rep.get("ack", 0)

        if not primo_tentativo and tot_dest > 0 and ack_cnt >= tot_dest:
            with _LOCK:
                _STATO["coda_impegni_push"].pop(chiave, None)
            log_push(f"✅ Consegna confermata da TUTTI i dispositivi (ACK {ack_cnt}/{tot_dest}) per '{chiave}'.")
            threading.Thread(target=_salva_ack_su_db_async, daemon=True).start()
            continue

        # Spunto rapido: 12s per i primi 2 retry, poi ogni 45s
        intervallo_attesa = 12 if tentativi <= 2 else INTERVALLO_RETRY_SECONDI
        if not primo_tentativo and (ora_ts - ultimo_invio < intervallo_attesa):
            continue

        job["tentativi"] = tentativi + 1
        job["ultimo_invio_ts"] = ora_ts

        if not primo_tentativo:
            log_push(f"🔁 Ritrasmissione #{job['tentativi']} per '{chiave}' ai telefoni ancora senza ACK ({ack_cnt}/{tot_dest} confermati)...")

        confermata_da_tutti = _invia_push_a_tutti_sync(
            job["payload"],
            chiave_notifica=chiave,
            forza_ritrasmissione_non_ack=(not primo_tentativo)
        )

        threading.Thread(target=_salva_ack_su_db_async, daemon=True).start()

        if confermata_da_tutti:
            with _LOCK:
                _STATO["coda_impegni_push"].pop(chiave, None)
            log_push(f"🎯 Consegna completata a tutti i telefoni per '{chiave}' (tentativi={job['tentativi']}).")


def _loop_scheduler():
    log_push("🚀 Avvio thread scheduler notifiche impegni ELF...")
    try:
        _carica_o_inizializza_config_db()
    except Exception as ex:
        log_push(f"❌ Errore in inizializzazione config DB: {ex}")
        traceback.print_exc()

    while True:
        try:
            _processa_coda_impegni()
            gc.collect()
        except Exception as ex:
            log_push(f"❌ Errore nel ciclo _processa_coda_impegni: {ex}")
            traceback.print_exc()
        scattato = _STATO["evento_ricalcolo"].wait(timeout=10)
        if scattato:
            time.sleep(0.3)
            _STATO["evento_ricalcolo"].clear()


def forza_ricalcolo_impegni():
    """Sveglia immediatamente lo scheduler quando un impegno viene creato, modificato o cancellato."""
    log_push("⚡ Richiesto ricalcolo immediato impegni da interfaccia utente.")
    _STATO["ultimo_sync_anagrafica"] = 0.0
    _STATO["evento_ricalcolo"].set()


def invia_notifica_test_globale():
    if not _STATO["inizializzato"]:
        _carica_o_inizializza_config_db()
    ora_str = _ora_italia().strftime("%H:%M")
    chiave_test = f"test_{int(time.time())}"
    with _LOCK:
        _STATO["coda_impegni_push"][chiave_test] = {
            "id_ev": "test",
            "creato_ts": time.time(),
            "scade_ts": time.time() + 300,
            "tentativi": 0,
            "ultimo_invio_ts": 0.0,
            "payload": {
                "title": f"🔔 Test Notifica ELF ({ora_str})",
                "body": "✅ Ricezione notifiche impegni verificata correttamente!",
                "tag": f"elf_test_{int(time.time())}",
                "notif_key": chiave_test,
            }
        }
    _STATO["evento_ricalcolo"].set()
    log_push(f"🧪 Accodato test globale notifiche ({chiave_test}).")
    return len(_STATO["subscriptions"])
