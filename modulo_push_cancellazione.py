import threading
from modulo_push_stato import (
    _STATO,
    _LOCK,
    _DB_LOCK,
    log_push,
    _calcola_hash_ep,
    _rinnova_client_se_chiuso,
)


def _rimuovi_endpoint_specifico(ep_target: str, did_target: str, imposta_force_unsub: bool = True):
    """Rimuove l'endpoint specifico del telefono che ha chiesto di silenziarsi da RAM e da push_subscriptions."""
    from modulo_push_registrazione import _ricarica_subscriptions_da_db_sync, _aggiorna_ui_pagine

    sb = _STATO["supabase"]
    ids_da_eliminare = []
    eps_da_eliminare = []
    ep_h = _calcola_hash_ep(ep_target) if ep_target else ""

    with _LOCK:
        if ep_target and ep_target in _STATO["subscriptions"]:
            info = _STATO["subscriptions"].pop(ep_target, None)
            if info:
                if info.get("id_db"):
                    ids_da_eliminare.append(info["id_db"])
                else:
                    eps_da_eliminare.append(info.get("endpoint") or ep_target)
            if ep_h:
                _STATO["hash_to_ep"].pop(ep_h, None)
                _STATO["stato_device"][ep_h] = False
        else:
            for ep_k, info in list(_STATO["subscriptions"].items()):
                match_ep = bool(ep_target and (ep_k == ep_target or info.get("endpoint") == ep_target))
                match_did = bool(did_target and (info.get("device_id") == did_target or info.get("ep_hash") == did_target))
                if match_ep or (did_target and not ep_target and match_did):
                    if info.get("id_db"):
                        ids_da_eliminare.append(info["id_db"])
                    else:
                        eps_da_eliminare.append(info.get("endpoint") or ep_k)
                    _STATO["subscriptions"].pop(ep_k, None)
                    if info.get("ep_hash"):
                        _STATO["hash_to_ep"].pop(info["ep_hash"], None)
                        _STATO["stato_device"][info["ep_hash"]] = False
                    break

        if did_target:
            _STATO["did_to_ep"].pop(did_target, None)
            _STATO["stato_device"][did_target] = False
            _STATO["unmute_devices"].discard(did_target)
            if imposta_force_unsub:
                _STATO["force_unsub_devices"].add(did_target)
        if ep_h:
            _STATO["stato_device"][ep_h] = False
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

    if sb and (ids_da_eliminare or eps_da_eliminare):
        def _del():
            with _DB_LOCK:
                for id_r in ids_da_eliminare:
                    try:
                        sb.table("push_subscriptions").delete().eq("id", id_r).execute()
                    except Exception as ex:
                        _rinnova_client_se_chiuso(ex)
                for ep_r in eps_da_eliminare:
                    try:
                        sb.table("push_subscriptions").delete().eq("endpoint", ep_r).execute()
                    except Exception as ex:
                        _rinnova_client_se_chiuso(ex)
            _ricarica_subscriptions_da_db_sync(log_sempre=True)
            log_push(f"🔕 Dispositivo silenziato e rimosso da push_subscriptions (dev={did_target or ep_h}).")
        threading.Thread(target=_del, daemon=True).start()


def imposta_silenzioso(valore: bool, page=None):
    from modulo_push_registrazione import _carica_o_inizializza_config_db, _risolvi_did_pagina, _aggiorna_ui_pagine
    from modulo_push_invio import forza_ricalcolo_impegni

    if not _STATO["inizializzato"]:
        _carica_o_inizializza_config_db()

    did_target = _risolvi_did_pagina(page) if page is not None else ""
    ep_target = getattr(page, "_elf_ep", None) if page is not None else ""
    ep_h_target = getattr(page, "_elf_ep_hash", None) if page is not None else ""

    if valore:
        log_push(f"🔕 Richiesto silenziamento per dispositivo [dev={did_target or ep_h_target}]")
        _rimuovi_endpoint_specifico(ep_target or "", did_target or ep_h_target or "", imposta_force_unsub=True)
    else:
        with _LOCK:
            for k in (did_target, ep_h_target):
                if k:
                    _STATO["force_unsub_devices"].discard(k)
                    _STATO["unmute_devices"].add(k)
        log_push(f"🔔 Richiesta riattivazione notifiche per dispositivo [dev={did_target or ep_h_target}]")
        _aggiorna_ui_pagine()
        forza_ricalcolo_impegni()
