import os
import json
import time
import threading
import flet as ft

from modulo_push_stato import (
    _STATO,
    _LOCK,
    _DB_LOCK,
    VAPID_CACHE_FILE,
    log_push,
    _calcola_hash_ep,
    _calcola_fp_page,
    _pulisci_chiave_str,
    _rinnova_client_se_chiuso,
    _valida_o_deriva_coppia_vapid,
    _genera_chiavi_vapid,
    _salva_vapid_su_db_e_disco,
)


def _migra_vecchie_sub_da_menu_giornalieri_sync():
    """
    Se nella vecchia tabella menu_giornalieri (data_menu='1970-01-01') esistono ancora
    SYS_VAPID o SYS_SUB_V2, li copia automaticamente in push_subscriptions e li pulisce.
    """
    sb = _STATO["supabase"]
    if not sb:
        return "", ""
    pub_old, priv_old = "", ""
    with _DB_LOCK:
        try:
            res_old = (
                sb.table("menu_giornalieri")
                .select("id, categoria, subcategoria, preso")
                .eq("data_menu", "1970-01-01")
                .in_("categoria", ["SYS_VAPID", "SYS_SUB_V2"])
                .execute()
            )
            righe_old = res_old.data or []
            if not righe_old:
                return "", ""

            log_push(f"📦 Rilevate {len(righe_old)} righe legacy in menu_giornalieri: avvio migrazione verso push_subscriptions...")
            ids_migrati = []
            ora_int = int(time.time())

            for r in righe_old:
                cat = r.get("categoria")
                sub_txt = r.get("subcategoria") or ""
                if cat == "SYS_VAPID" and sub_txt:
                    try:
                        dv = json.loads(sub_txt)
                        pub_old, priv_old = _valida_o_deriva_coppia_vapid(dv.get("pub", ""), dv.get("priv", ""))
                        ids_migrati.append(r["id"])
                    except Exception:
                        pass
                elif cat == "SYS_SUB_V2" and sub_txt and not bool(r.get("preso")):
                    try:
                        raw_obj = json.loads(sub_txt)
                        sub_obj = raw_obj.get("sub") if ("sub" in raw_obj and isinstance(raw_obj["sub"], dict)) else raw_obj
                        ep = (sub_obj.get("endpoint") or "").strip()
                        keys = (sub_obj.get("keys") or {}) if isinstance(sub_obj, dict) else {}
                        p256 = (keys.get("p256dh") or "").strip()
                        auth = (keys.get("auth") or "").strip()
                        did = (raw_obj.get("device_id") or "").strip() or _calcola_hash_ep(ep)
                        hw_id = (raw_obj.get("hw_id") or "").strip()
                        if ep and p256 and auth:
                            ex_check = sb.table("push_subscriptions").select("id").eq("endpoint", ep).limit(1).execute()
                            if not ex_check.data:
                                sb.table("push_subscriptions").insert({
                                    "device_id": did,
                                    "hw_id": hw_id,
                                    "endpoint": ep,
                                    "p256dh": p256,
                                    "auth": auth,
                                    "muted": False,
                                    "eventi_in_essere": [],
                                    "stato_consegna": {},
                                    "ultimo_accesso_ts": ora_int,
                                }).execute()
                                log_push(f"📦 Migrato dispositivo [dev={did}] in push_subscriptions.")
                        ids_migrati.append(r["id"])
                    except Exception as ex_m:
                        log_push(f"⚠️ Errore migrazione singola riga SYS_SUB_V2: {ex_m}")

            for id_m in ids_migrati:
                try:
                    sb.table("menu_giornalieri").delete().eq("id", id_m).execute()
                except Exception:
                    pass
        except Exception as ex:
            log_push(f"⚠️ Avviso controllo migrazione legacy: {ex}")
    return pub_old, priv_old


def _ricarica_subscriptions_da_db_sync(log_sempre: bool = True) -> tuple[str, str]:
    """
    Ricarica dalla tabella push_subscriptions di Supabase tutti i dispositivi registrati
    e allinea la RAM, eliminando quelli inattivi da oltre 30 giorni consecutivi (senza accesso né ACK).
    """
    sb = _STATO["supabase"]
    if not sb:
        return "", ""

    with _LOCK:
        ram_ts_snapshot = {
            k: (int(v.get("ultimo_accesso_ts") or 0), int(v.get("ultimo_ack_ts") or 0))
            for k, v in _STATO["subscriptions"].items()
        }

    subs_loaded = {}
    ack_loaded = {}
    hash_map = {}
    did_map = {}
    device_map = {}
    seen_did_hw = set()
    ids_duplicati_da_pulire = []
    aggiornamenti_ts_attivi = []
    pub_db, priv_db = "", ""
    query_riuscita = False
    ora_now = time.time()

    with _DB_LOCK:
        for tentativo in range(3):
            try:
                res = sb.table("push_subscriptions").select("*").order("id", desc=True).execute()
                righe = res.data or []
                query_riuscita = True

                for r in righe:
                    ep = str(r.get("endpoint") or "").strip()
                    if not ep:
                        continue

                    if ep.startswith("vapid://") or str(r.get("device_id") or "") == "SYS_VAPID":
                        if not (pub_db and priv_db):
                            raw_pub = str(r.get("p256dh") or ep.replace("vapid://", "")).strip()
                            raw_priv = str(r.get("auth") or "").strip()
                            pub_db, priv_db = _valida_o_deriva_coppia_vapid(raw_pub, raw_priv)
                        continue

                    raw_auth = str(r.get("auth") or "").strip()
                    is_muted = bool(r.get("muted", False)) or raw_auth.startswith("MUTED:")
                    s_auth = raw_auth[6:].strip() if raw_auth.startswith("MUTED:") else raw_auth
                    did = str(r.get("device_id") or "").strip()
                    hw_id_row = str(r.get("hw_id") or "").strip()

                    if s_auth.startswith("DID:") and "|" in s_auth:
                        did_part, s_auth = s_auth.split("|", 1)
                        did = did or did_part[4:].strip()
                    if s_auth.startswith("HW:") and "|" in s_auth:
                        hw_part, s_auth = s_auth.split("|", 1)
                        hw_id_row = hw_id_row or hw_part[3:].strip()

                    p256dh = str(r.get("p256dh") or "").strip()
                    if not s_auth or not p256dh:
                        continue

                    ep_h = _calcola_hash_ep(ep)
                    did_finale = did or ep_h

                    ev_in_essere = r.get("eventi_in_essere")
                    if isinstance(ev_in_essere, str):
                        try:
                            ev_in_essere = json.loads(ev_in_essere)
                        except Exception:
                            ev_in_essere = []
                    if not isinstance(ev_in_essere, list):
                        ev_in_essere = []

                    st_consegna = r.get("stato_consegna")
                    if isinstance(st_consegna, str):
                        try:
                            st_consegna = json.loads(st_consegna)
                        except Exception:
                            st_consegna = {}
                    if not isinstance(st_consegna, dict):
                        st_consegna = {}

                    db_accesso_ts = int(r.get("ultimo_accesso_ts") or 0)
                    db_ack_ts = int(st_consegna.get("_ultimo_ack_ts") or 0)
                    ram_acc_ts, ram_ack_ts = ram_ts_snapshot.get(ep, (0, 0))

                    ultimo_accesso_eff = max(db_accesso_ts, ram_acc_ts)
                    ultimo_ack_eff = max(db_ack_ts, ram_ack_ts)
                    ultima_attivita_combinata = max(ultimo_accesso_eff, ultimo_ack_eff)

                    # Pulizia dispositivi orfani inattivi da oltre 365 giorni senza accesso né ACK
                    if ultima_attivita_combinata > 0 and (ora_now - ultima_attivita_combinata) > (365 * 86400):
                        if r.get("id"):
                            ids_duplicati_da_pulire.append(r["id"])
                            log_push(f"🧹 Rimosso dispositivo inattivo da oltre 365gg [dev={did_finale}] (id={r.get('id')})")
                        continue

                    if r.get("id") and ram_acc_ts > db_accesso_ts and (ram_acc_ts - db_accesso_ts) > 43200:
                        aggiornamenti_ts_attivi.append((r["id"], ram_acc_ts))

                    if ep in subs_loaded or did_finale in seen_did_hw:
                        if r.get("id"):
                            ids_duplicati_da_pulire.append(r["id"])
                        continue

                    seen_did_hw.add(did_finale)

                    for k_ev, val_stato in st_consegna.items():
                        if str(k_ev).startswith("_"):
                            continue
                        if str(val_stato).upper().startswith("CONSEGNAT"):
                            s_ev = ack_loaded.setdefault(str(k_ev), set())
                            s_ev.update([ep, ep_h, did_finale])

                    sub_obj = {
                        "endpoint": ep,
                        "keys": {"p256dh": p256dh, "auth": s_auth}
                    }

                    subs_loaded[ep] = {
                        "id_db": r.get("id"),
                        "endpoint": ep,
                        "p256dh": p256dh,
                        "auth": s_auth,
                        "sub": sub_obj,
                        "muted": is_muted,
                        "ep_hash": ep_h,
                        "device_id": did_finale,
                        "hw_id": hw_id_row,
                        "eventi_in_essere": ev_in_essere,
                        "stato_consegna": st_consegna,
                        "ultimo_accesso_ts": ultimo_accesso_eff or int(ora_now),
                        "ultimo_ack_ts": ultimo_ack_eff,
                    }
                    if not is_muted:
                        hash_map[ep_h] = ep
                        did_map[did_finale] = ep
                        device_map[ep_h] = True
                        device_map[did_finale] = True

                for id_dup in ids_duplicati_da_pulire:
                    try:
                        sb.table("push_subscriptions").delete().eq("id", id_dup).execute()
                    except Exception:
                        pass

                for id_att, ts_att in aggiornamenti_ts_attivi:
                    try:
                        sb.table("push_subscriptions").update({"ultimo_accesso_ts": ts_att}).eq("id", id_att).execute()
                    except Exception:
                        pass
                break
            except Exception as ex:
                _rinnova_client_se_chiuso(ex)
                sb = _STATO["supabase"]
                if tentativo == 2:
                    log_push(f"❌ Errore ricarica da tabella push_subscriptions (hai creato la tabella su Supabase?): {ex}")
                else:
                    time.sleep(0.4)

    if query_riuscita:
        with _LOCK:
            vecchio_num = len(_STATO["subscriptions"])
            _STATO["subscriptions"] = subs_loaded
            if ack_loaded:
                for k_db, set_db in ack_loaded.items():
                    _STATO["ack_per_ep"].setdefault(k_db, set()).update(set_db)
            _STATO["hash_to_ep"] = hash_map
            _STATO["did_to_ep"] = did_map
            for k_dev in list(_STATO["stato_device"].keys()):
                if k_dev not in did_map and k_dev not in hash_map:
                    _STATO["stato_device"][k_dev] = False
            _STATO["stato_device"].update(device_map)
            _STATO["ultimo_sync_db_ts"] = time.time()

        if log_sempre or vecchio_num != len(subs_loaded):
            elenco_dev = [f"{v.get('device_id')}({'MUTO' if v.get('muted') else 'ATTIVO'})" for v in subs_loaded.values()]
            log_push(f"📲 RAM sincronizzata con push_subscriptions: {len(subs_loaded)} dispositivi -> {elenco_dev}")
            _aggiorna_ui_pagine()

    return pub_db, priv_db


def _carica_o_inizializza_config_db():
    with _LOCK:
        if _STATO["inizializzato"] and _STATO["vapid_public"] and _STATO["vapid_private"]:
            return

    pub_mig, priv_mig = _migra_vecchie_sub_da_menu_giornalieri_sync()

    pub_env = _pulisci_chiave_str(os.environ.get("VAPID_PUBLIC_KEY", ""))
    priv_env = _pulisci_chiave_str(os.environ.get("VAPID_PRIVATE_KEY", ""))

    pub_init, priv_init = "", ""
    if priv_env:
        pub_init, priv_init = _valida_o_deriva_coppia_vapid(pub_env, priv_env)

    pub_db, priv_db = _ricarica_subscriptions_da_db_sync(log_sempre=True)
    if not (pub_init and priv_init) and (pub_db and priv_db):
        pub_init, priv_init = pub_db, priv_db
    if not (pub_init and priv_init) and (pub_mig and priv_mig):
        pub_init, priv_init = pub_mig, priv_mig

    if not (pub_init and priv_init) and os.path.exists(VAPID_CACHE_FILE):
        try:
            with open(VAPID_CACHE_FILE, "r", encoding="utf-8") as f:
                d_cache = json.load(f)
                pub_init, priv_init = _valida_o_deriva_coppia_vapid(d_cache.get("pub", ""), d_cache.get("priv", ""))
        except Exception:
            pass

    if not (pub_init and priv_init):
        pub_init, priv_init = _genera_chiavi_vapid()
        log_push("🔑 Generata nuova coppia di chiavi VAPID.")

    with _LOCK:
        _STATO["vapid_public"] = pub_init
        _STATO["vapid_private"] = priv_init
        _STATO["inizializzato"] = True

    log_push(f"✅ Sottosistema Push inizializzato (VAPID pub={pub_init[:14]}..., dispositivi={len(_STATO['subscriptions'])}).")
    threading.Thread(target=_salva_vapid_su_db_e_disco, args=(pub_init, priv_init), daemon=True).start()


def _salva_sub_db_sync(ep: str, sub_obj: dict, did: str, ep_h: str, is_muted: bool = False, hw_id: str = ""):
    sb = _STATO["supabase"]
    if not sb or not ep:
        return

    keys_d = (sub_obj.get("keys") if isinstance(sub_obj, dict) else {}) or {}
    p256dh = str(keys_d.get("p256dh") or "").strip()
    auth_clean = str(keys_d.get("auth") or "").replace("MUTED:", "").strip()
    if not p256dh or not auth_clean:
        log_push(f"⚠️ Tentativo di salvataggio subscription senza chiavi p256dh/auth per dev={did}")
        return

    did_clean = (did or ep_h or "").strip()
    hw_clean = (hw_id or "").strip()
    ora_int = int(time.time())
    modificato_db = False
    nuovo_dispositivo = False

    with _DB_LOCK:
        try:
            res_all = sb.table("push_subscriptions").select("*").execute()
            righe_db = res_all.data or []

            id_target = None
            riga_target = None
            ids_da_eliminare = []

            for r in righe_db:
                r_ep = str(r.get("endpoint") or "").strip()
                r_did = str(r.get("device_id") or "").strip()
                r_hw = str(r.get("hw_id") or "").strip()
                if r_ep.startswith("vapid://") or r_did == "SYS_VAPID":
                    continue

                stesso_ep = (r_ep == ep)
                stesso_did = bool(did_clean and r_did == did_clean)

                if stesso_did or stesso_ep:
                    if id_target is None:
                        id_target = r["id"]
                        riga_target = r
                    else:
                        ids_da_eliminare.append(r["id"])

            for id_extra in ids_da_eliminare:
                try:
                    sb.table("push_subscriptions").delete().eq("id", id_extra).execute()
                    modificato_db = True
                except Exception:
                    pass

            stato_consegna_prec = {}
            eventi_prec = []
            if riga_target:
                raw_sc = riga_target.get("stato_consegna")
                if isinstance(raw_sc, str):
                    try:
                        raw_sc = json.loads(raw_sc)
                    except Exception:
                        raw_sc = {}
                if isinstance(raw_sc, dict):
                    stato_consegna_prec = dict(raw_sc)

                raw_ev = riga_target.get("eventi_in_essere")
                if isinstance(raw_ev, str):
                    try:
                        raw_ev = json.loads(raw_ev)
                    except Exception:
                        raw_ev = []
                if isinstance(raw_ev, list):
                    eventi_prec = list(raw_ev)

                old_ep_db = str(riga_target.get("endpoint") or "").strip()
                old_eph_db = _calcola_hash_ep(old_ep_db) if old_ep_db else ""
                old_did_db = str(riga_target.get("device_id") or "").strip()
                with _LOCK:
                    for s_ack in _STATO["ack_per_ep"].values():
                        if (
                            (old_ep_db and old_ep_db in s_ack)
                            or (old_eph_db and old_eph_db in s_ack)
                            or (old_did_db and old_did_db in s_ack)
                            or (did_clean and did_clean in s_ack)
                        ):
                            s_ack.update([ep, ep_h, did_clean])

            payload_esteso = {
                "device_id": did_clean,
                "hw_id": hw_clean,
                "endpoint": ep,
                "p256dh": p256dh,
                "auth": f"MUTED:{auth_clean}" if is_muted else auth_clean,
                "muted": is_muted,
                "eventi_in_essere": eventi_prec,
                "stato_consegna": stato_consegna_prec,
                "ultimo_accesso_ts": ora_int,
            }

            if id_target:
                ep_cambiato = str((riga_target or {}).get("endpoint") or "").strip() != ep
                p256_cambiato = str((riga_target or {}).get("p256dh") or "").strip() != p256dh
                auth_cambiato = str((riga_target or {}).get("auth") or "").strip() != payload_esteso["auth"]
                did_cambiato = str((riga_target or {}).get("device_id") or "").strip() != did_clean
                era_muted = bool((riga_target or {}).get("muted", False))

                if ep_cambiato or p256_cambiato or auth_cambiato or did_cambiato or (era_muted != is_muted):
                    sb.table("push_subscriptions").update(payload_esteso).eq("id", id_target).execute()
                    modificato_db = True
                    nuovo_dispositivo = not is_muted
                    log_push(f"🔄 Dispositivo aggiornato su push_subscriptions [dev={did_clean}] (hw={hw_clean}, id={id_target})")
                else:
                    vecchio_ts = int((riga_target or {}).get("ultimo_accesso_ts") or 0)
                    if ora_int - vecchio_ts > 43200:
                        sb.table("push_subscriptions").update({"ultimo_accesso_ts": ora_int, "device_id": did_clean, "hw_id": hw_clean}).eq("id", id_target).execute()
            else:
                sb.table("push_subscriptions").insert(payload_esteso).execute()
                modificato_db = True
                nuovo_dispositivo = not is_muted
                log_push(f"✅ Nuovo dispositivo registrato su push_subscriptions [dev={did_clean}] (hw={hw_clean})")
        except Exception as ex:
            _rinnova_client_se_chiuso(ex)
            log_push(f"❌ Errore salvataggio dispositivo su push_subscriptions [dev={did_clean}]: {ex}")

    if modificato_db:
        _ricarica_subscriptions_da_db_sync(log_sempre=True)

    if nuovo_dispositivo:
        try:
            from modulo_push_invio import _salva_ack_su_db_async, forza_ricalcolo_impegni
            threading.Thread(target=_salva_ack_su_db_async, daemon=True).start()
            forza_ricalcolo_impegni()
        except Exception as ex_all:
            log_push(f"⚠️ Avviso allineamento stato consegna nuovo dispositivo: {ex_all}")


def _risolvi_did_pagina(page: ft.Page = None) -> str:
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


def ottieni_diag_dispositivo(page: ft.Page = None) -> tuple[str, dict]:
    did = _risolvi_did_pagina(page)
    with _LOCK:
        diag = dict(_STATO["diag_device"].get(did, {})) if did else {}
    return did, diag


def _aggancia_pagina(page: ft.Page):
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


def registra_pagina(page: ft.Page):
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
    except Exception as ex:
        log_push(f"⚠️ Avviso in registra_pagina: {ex}")


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


def is_notifiche_attive(page: ft.Page = None) -> bool:
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
            elif ep and ep in _STATO["subscriptions"] and not _STATO["subscriptions"][ep].get("muted"):
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


def ha_dispositivi_registrati(page: ft.Page = None) -> bool:
    return is_notifiche_attive(page)
