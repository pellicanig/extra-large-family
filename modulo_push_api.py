import time
import asyncio
import threading
import fastapi
from fastapi import Request
from fastapi.responses import HTMLResponse, Response, JSONResponse

from modulo_push_stato import (
    _STATO,
    _LOCK,
    log_push,
    _ora_italia,
    _calcola_hash_ep,
    _calcola_fp_http,
    _crea_client_supabase_dedicato,
)
from modulo_push_registrazione import (
    _ricarica_subscriptions_da_db_sync,
    _carica_o_inizializza_config_db,
    _salva_sub_db_sync,
    _associa_device_a_pagina,
    _aggiorna_ui_pagine,
)
from modulo_push_cancellazione import _rimuovi_endpoint_specifico
from modulo_push_invio import (
    _is_dispositivo_confermato_ack,
    _invia_push_singolo,
    _invia_push_a_tutti_sync,
    _loop_scheduler,
    _registra_ack_interno,
)
from modulo_push_web import (
    SW_PUSH_HANDLERS_JS,
    SW_JS_CODE,
    CLIENT_JS_CODE,
    PAGINA_ATTIVA_HTML,
)


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

    log_push("🌐 API FastAPI Web Push collegate correttamente (/api/notifiche/...).")

    @app.get("/api/notifiche/ping")
    async def get_ping():
        return JSONResponse({"ok": True, "dispositivi": len(_STATO["subscriptions"])})

    @app.get("/sw-notifiche.js")
    @app.get("/api/notifiche/sw.js")
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
    async def handle_ack(req: Request):
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
    async def get_status(req: Request):
        if not _STATO["inizializzato"]:
            await asyncio.to_thread(_carica_o_inizializza_config_db)
        elif time.time() - _STATO["ultimo_sync_db_ts"] > 8.0:
            await asyncio.to_thread(_ricarica_subscriptions_da_db_sync, False)

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
            for info_cand in _STATO["subscriptions"].values():
                match_did = bool(did and (info_cand.get("device_id") == did or info_cand.get("ep_hash") == did))
                match_ep = bool(ep and info_cand.get("endpoint") == ep)
                match_hw = bool(hw_id and info_cand.get("hw_id") == hw_id)
                if match_did or match_ep or match_hw:
                    info_sub_per_did = info_cand
                    break

            if not info_sub and info_sub_per_did and (not ep or info_sub_per_did.get("endpoint") == ep):
                info_sub = info_sub_per_did
                if not ep_h:
                    ep_h = info_sub.get("ep_hash") or ""

            if info_sub_per_did:
                info_sub_per_did["ultimo_accesso_ts"] = int(time.time())

            forza_rinnovo_token = bool(
                (ep and _STATO["fallimenti_ep"].get(ep, 0) > 0)
                or (did and did in _STATO.get("rinnovo_token_devices", set()))
            )
            if forza_rinnovo_token and did and "rinnovo_token_devices" in _STATO:
                _STATO["rinnovo_token_devices"].discard(did)

            token_disallineato = False
            if perm == "1" and not force_unsub:
                if forza_rinnovo_token:
                    token_disallineato = True
                elif info_sub_per_did is not None and not info_sub_per_did.get("muted", False):
                    ep_db = str(info_sub_per_did.get("endpoint") or "").strip()
                    p256_db = str(info_sub_per_did.get("p256dh") or "").strip()
                    auth_db = str(info_sub_per_did.get("auth") or "").strip()
                    did_db = str(info_sub_per_did.get("device_id") or "").strip()
                    hw_db = str(info_sub_per_did.get("hw_id") or "").strip()
                    if not ep or ep != ep_db:
                        token_disallineato = True
                    elif (p256dh_q and p256dh_q != p256_db) or (auth_q and auth_q != auth_db):
                        token_disallineato = True
                    elif did and did_db and did != did_db:
                        token_disallineato = True
                    elif hw_id and hw_id != hw_db:
                        token_disallineato = True
                elif info_sub is None:
                    # Dispositivo con permessi attivi nel browser ma non ancora allineato su DB
                    token_disallineato = True

            disp_attivo = (
                (info_sub is not None)
                and bool(ep)
                and (info_sub.get("endpoint") == ep)
                and not info_sub.get("muted", False)
                and not force_unsub
                and (perm == "1")
                and not token_disallineato
            )

            if did:
                _STATO["stato_device"][did] = disp_attivo
                _STATO["diag_device"][did] = {
                    "perm": perm,
                    "standalone": standalone,
                    "persisted": persisted,
                    "os_cfg": os_cfg,
                    "attivo": disp_attivo,
                }
            if ep_h:
                _STATO["stato_device"][ep_h] = disp_attivo

            pending_ack_keys = []
            if info_sub and disp_attivo:
                ep_sub = info_sub.get("endpoint") or ep
                for k_job in _STATO["coda_impegni_push"].keys():
                    inviati_set = _STATO["inviate_per_ep"].get(k_job, set())
                    ack_set = _STATO["ack_per_ep"].get(k_job, set())
                    if ep_sub in inviati_set and not _is_dispositivo_confermato_ack(ack_set, ep_sub, ep_h, did):
                        pending_ack_keys.append(k_job)

        if ep or did:
            _associa_device_a_pagina(did, ep, disp_attivo=disp_attivo, fp=fp, ua_h=ua_h)

        return JSONResponse({
            "attive": disp_attivo,
            "dispositivo_registrato": disp_attivo,
            "dispositivo_attivo": disp_attivo,
            "token_disallineato": token_disallineato,
            "forza_rinnovo_token": forza_rinnovo_token,
            "pending_ack_keys": pending_ack_keys,
            "ep_hash": ep_h or "",
            "force_unsub": force_unsub,
            "unmute_cmd": unmute_cmd,
            "dispositivi": len(_STATO["subscriptions"]),
            "residui": _STATO["residui_attuali"],
            "vapid_public": _STATO["vapid_public"],
        })

    @app.get("/api/notifiche/test")
    async def get_test_push():
        ora_str = _ora_italia().strftime("%H:%M")
        log_push("🧪 Richiesto test notifiche broadcast su /api/notifiche/test")
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
    async def post_subscribe(req: Request):
        if not _STATO["inizializzato"]:
            await asyncio.to_thread(_carica_o_inizializza_config_db)

        fp, ua_h = _calcola_fp_http(req)
        payload = await req.json()
        sub_obj = payload.get("sub") if isinstance(payload, dict) and "sub" in payload else payload
        send_test = bool(payload.get("send_test")) if isinstance(payload, dict) else False
        did = ((payload.get("device_id") if isinstance(payload, dict) else "") or req.cookies.get("elf_did") or "").strip()
        hw_id = ((payload.get("hw_id") if isinstance(payload, dict) else "") or "").strip()

        ep = (sub_obj.get("endpoint") or "").strip() if isinstance(sub_obj, dict) else ""
        keys_dict = (sub_obj.get("keys") if isinstance(sub_obj, dict) and isinstance(sub_obj.get("keys"), dict) else {}) or {}
        p256dh = str(keys_dict.get("p256dh") or (payload.get("p256dh") if isinstance(payload, dict) else "") or "").strip()
        auth_clean = str(keys_dict.get("auth") or (payload.get("auth") if isinstance(payload, dict) else "") or "").replace("MUTED:", "").strip()

        if ep and p256dh and auth_clean:
            sub_obj = {"endpoint": ep, "keys": {"p256dh": p256dh, "auth": auth_clean}}

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
                for old_k, old_info in list(_STATO["subscriptions"].items()):
                    if old_k != ep and (
                        (did and old_info.get("device_id") == did)
                        or (hw_id and old_info.get("hw_id") == hw_id)
                    ):
                        if esistente is None and old_info.get("id_db"):
                            esistente = old_info
                        old_eph = old_info.get("ep_hash")
                        old_did = old_info.get("device_id")
                        if old_eph and old_eph != ep_h:
                            _STATO["hash_to_ep"].pop(old_eph, None)
                            _STATO["stato_device"].pop(old_eph, None)
                        if old_did and old_did != did:
                            _STATO["did_to_ep"].pop(old_did, None)
                            _STATO["stato_device"].pop(old_did, None)
                        _STATO["fallimenti_ep"].pop(old_k, None)
                        _STATO["subscriptions"].pop(old_k, None)

                _STATO["subscriptions"][ep] = {
                    "id_db": esistente.get("id_db") if esistente else None,
                    "sub": sub_obj,
                    "endpoint": ep,
                    "p256dh": p256dh,
                    "auth": auth_clean,
                    "muted": False,
                    "ep_hash": ep_h,
                    "device_id": did or ep_h,
                    "hw_id": hw_id,
                    "eventi_in_essere": esistente.get("eventi_in_essere", []) if esistente else [],
                    "stato_consegna": esistente.get("stato_consegna", {}) if esistente else {},
                    "ultimo_accesso_ts": int(time.time()),
                    "ultimo_ack_ts": esistente.get("ultimo_ack_ts", 0) if esistente else 0,
                }
                _STATO["hash_to_ep"][ep_h] = ep
                if did:
                    _STATO["did_to_ep"][did] = ep
                _STATO["fallimenti_ep"].pop(ep, None)

            _associa_device_a_pagina(did or ep_h, ep, disp_attivo=True, fp=fp, ua_h=ua_h)
            _aggiorna_ui_pagine()
            await asyncio.to_thread(_salva_sub_db_sync, ep, sub_obj, did or ep_h, ep_h, False, hw_id)

            if send_test:
                ora_str = _ora_italia().strftime("%H:%M")
                push_status, push_msg = await asyncio.to_thread(_invia_push_singolo, sub_obj, {
                    "title": f"🔔 Notifica di Prova ELF ({ora_str})",
                    "body": "✅ Questo dispositivo è registrato su push_subscriptions e riceve correttamente gli avvisi.",
                    "tag": f"elf_test_{int(time.time())}",
                    "ep_hash": ep_h,
                    "device_id": did or ep_h
                })
                log_push(f"🧪 Test individuale su [dev={did or ep_h}] -> HTTP {push_status} ({push_msg})")

        return JSONResponse({
            "ok": True,
            "ep_hash": ep_h,
            "dispositivi_totali": len(_STATO["subscriptions"]),
            "residui": _STATO["residui_attuali"],
            "push_status": push_status,
            "push_msg": push_msg
        })

    @app.post("/api/notifiche/unsubscribe")
    async def post_unsubscribe(req: Request):
        payload = await req.json()
        ep = (payload.get("endpoint") or "").strip() if isinstance(payload, dict) else ""
        did = ((payload.get("device_id") if isinstance(payload, dict) else "") or req.cookies.get("elf_did") or "").strip()
        log_push(f"🔕 Richiesto /api/notifiche/unsubscribe per [dev={did}]")
        _rimuovi_endpoint_specifico(ep, did, imposta_force_unsub=True)
        return JSONResponse({"ok": True})

    @app.middleware("http")
    async def inietta_script_notifiche(request: Request, call_next):
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
            prefisso = ("// v-elf-push-30\n" + SW_PUSH_HANDLERS_JS + "\n").encode("utf-8")
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
