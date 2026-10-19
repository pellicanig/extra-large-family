import flet as ft
import calendar
import threading
from datetime import datetime, timedelta
from modulo_ricette import (
    supabase,
    MESI,
    GIORNI_ESTESI,
    log_render,
    is_valid_uuid,
    apri_dialog_sicuro,
    chiudi_dialog_sicuro,
    formatta_numero,
    bordo_campo_uniforme,
    aggiorna_flag_preso_db,
    assicura_cache_anagrafica,
    generalizza_nome_dispensa,
    is_ingrediente_dispensa_attivo,
    imposta_stato_dispensa_ingrediente_db,
    sincronizza_e_pulisci_dispensa_mese
)


# ==========================================================
# MOTORE INTELLIGENTE "FAI LA SPESA"
# ==========================================================
def apri_popup_fai_la_spesa(page: ft.Page, data_riferimento: datetime, on_close_callback=None):
    if not supabase:
        return

    oggi_dt = datetime.now()
    stesso_mese_oggi = (data_riferimento.year == oggi_dt.year and data_riferimento.month == oggi_dt.month)
    nome_mese_rif = MESI[data_riferimento.month]
    spuntati_sessione = set()
    log_render(f"[UTENTE] Apertura popup 'Fai la Spesa' per mese {nome_mese_rif} {data_riferimento.year} (data rif: {data_riferimento.strftime('%d/%m/%Y')})")

    if stesso_mese_oggi:
        opzioni_filtro = [
            "Da oggi a fine mese",
            "Prossimi 7 giorni",
            "Solo giorno sel.",
            f"Tutto {nome_mese_rif}"
        ]
        scelta_default = "Da oggi a fine mese"
    else:
        opzioni_filtro = [
            f"Tutto {nome_mese_rif}",
            "Dal giorno sel. a fine mese",
            "7 giorni dal giorno sel.",
            "Solo giorno sel."
        ]
        scelta_default = f"Tutto {nome_mese_rif}"

    stato_filtro = {"scelta": scelta_default}
    modifiche_spunte = [False]

    spinner_spesa = ft.ProgressRing(width=18, height=18, stroke_width=2.5, color=ft.Colors.GREEN_800, visible=True)
    colonna_lista = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)
    txt_periodo_date = ft.Text("", size=11, color=ft.Colors.BLUE_900, weight=ft.FontWeight.BOLD)
    txt_riepilogo = ft.Text("", size=11, color=ft.Colors.GREY_700, weight=ft.FontWeight.BOLD)
    riga_bottoni_filtro = ft.Row(wrap=True, spacing=4, run_spacing=4)

    def cambia_filtro_spesa(nuova_scelta):
        log_render(f"[UTENTE] Cambio filtro periodo Spesa -> '{nuova_scelta}'")
        stato_filtro["scelta"] = nuova_scelta
        aggiorna_stile_filtri()
        avvia_calcolo_spesa()

    def aggiorna_stile_filtri():
        riga_bottoni_filtro.controls.clear()
        for opz in opzioni_filtro:
            att = (stato_filtro["scelta"] == opz)
            riga_bottoni_filtro.controls.append(
                ft.Container(
                    content=ft.Text(opz, size=10, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE if att else ft.Colors.GREEN_800),
                    bgcolor=ft.Colors.GREEN_700 if att else "#E8F5E9",
                    padding=ft.Padding(8, 5, 8, 5),
                    border_radius=12,
                    border=bordo_campo_uniforme(ft.Colors.GREEN_700),
                    on_click=lambda e, sc=opz: cambia_filtro_spesa(sc)
                )
            )

    aggiorna_stile_filtri()
    dlg_spesa = None

    def chiudi_spesa(e=None):
        log_render(f"[UTENTE] Chiusura popup 'Fai la Spesa' (Modifiche spunte: {modifiche_spunte[0]})")
        chiudi_dialog_sicuro(page, dlg_spesa)
        if modifiche_spunte[0] and on_close_callback:
            on_close_callback()

    dlg_spesa = ft.AlertDialog(
        title=ft.Row([
            ft.Text("🛒 Lista della Spesa", size=16, weight=ft.FontWeight.BOLD, color=ft.Colors.GREEN_800, expand=True),
            spinner_spesa
        ]),
        content=ft.Container(
            content=ft.Column([
                ft.Text("Filtra periodo (include Dispensa e Detersivi del mese):", size=10, color=ft.Colors.GREY_700, weight=ft.FontWeight.BOLD),
                riga_bottoni_filtro,
                txt_periodo_date,
                txt_riepilogo,
                ft.Divider(height=4, color="#E0E0E0"),
                colonna_lista
            ], spacing=5),
            width=355, height=490
        ),
        actions=[
            ft.Container(
                content=ft.Text("Chiudi", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=12),
                bgcolor=ft.Colors.BLUE_900, padding=ft.Padding(14, 8, 14, 8), border_radius=6,
                on_click=chiudi_spesa
            )
        ],
        shape=ft.RoundedRectangleBorder(radius=10),
        content_padding=10,
        inset_padding=ft.Padding(10, 15, 10, 15)
    )
    apri_dialog_sicuro(page, dlg_spesa)

    def normalizza_qta_unita(qta, unita):
        u = str(unita or "pz").replace("|DET", "").replace("|DISPENSA", "").lower().strip()
        q = float(qta or 0)
        if u == "kg":
            return q * 1000.0, "g"
        if u == "l":
            return q * 1000.0, "ml"
        return q, u

    def formatta_qta_finale(qta_tot, unita_base):
        if unita_base == "g" and qta_tot >= 1000:
            return f"{formatta_numero(qta_tot / 1000.0)} kg"
        if unita_base == "ml" and qta_tot >= 1000:
            return f"{formatta_numero(qta_tot / 1000.0)} l"
        if unita_base == "q.b." or qta_tot == 0:
            return "q.b."
        return f"{formatta_numero(qta_tot)} {unita_base}"

    dati_aggregati = []

    def avvia_calcolo_spesa():
        spinner_spesa.visible = True
        page.update()

        def worker():
            nonlocal dati_aggregati
            scelta = stato_filtro["scelta"]
            ultimo_gg_rif = calendar.monthrange(data_riferimento.year, data_riferimento.month)[1]

            if scelta == "Da oggi a fine mese":
                dt_i = oggi_dt
                dt_f = oggi_dt.replace(day=calendar.monthrange(oggi_dt.year, oggi_dt.month)[1])
            elif scelta == "Dal giorno sel. a fine mese":
                dt_i = data_riferimento
                dt_f = data_riferimento.replace(day=ultimo_gg_rif)
            elif scelta == "Prossimi 7 giorni":
                dt_i = oggi_dt
                dt_f = oggi_dt + timedelta(days=6)
            elif scelta == "7 giorni dal giorno sel.":
                dt_i = data_riferimento
                dt_f = data_riferimento + timedelta(days=6)
            elif scelta == "Solo giorno sel.":
                dt_i = data_riferimento
                dt_f = data_riferimento
            else:
                dt_i = data_riferimento.replace(day=1)
                dt_f = data_riferimento.replace(day=ultimo_gg_rif)

            inizio_str = dt_i.strftime("%Y-%m-%d")
            fine_str = dt_f.strftime("%Y-%m-%d")
            primo_mese_str = data_riferimento.replace(day=1).strftime("%Y-%m-%d")
            fine_mese_str = data_riferimento.replace(day=ultimo_gg_rif).strftime("%Y-%m-%d")
            txt_periodo_date.value = f"📅 Intervallo ({nome_mese_rif}): {dt_i.strftime('%d/%m/%Y')} → {dt_f.strftime('%d/%m/%Y')}"

            try:
                log_render(f"[SPESA] Avvio calcolo lista spesa per filtro '{scelta}' ({inizio_str} -> {fine_str})")
                assicura_cache_anagrafica(forza_ricarica=False)
                sincronizza_e_pulisci_dispensa_mese(data_riferimento)
                res_m = supabase.table("menu_giornalieri").select(
                    "*, ingredienti(id, nome, unita_default), ricette(id, nome)"
                ).gte("data_menu", min(inizio_str, primo_mese_str)).lte("data_menu", max(fine_str, fine_mese_str)).execute()

                righe_valide = [
                    r for r in (res_m.data or [])
                    if (r.get("categoria") in ["Dispensa", "Detersivi"] and primo_mese_str <= r.get("data_menu", "") <= fine_mese_str)
                    or (r.get("categoria") not in ["Dispensa", "Detersivi"] and inizio_str <= r.get("data_menu", "") <= fine_str)
                ]

                # Raccoglie tutti i nomi generalizzati presenti nella Dispensa del mese (es. 'sale', 'olio extravergine d'oliva', ecc.)
                nomi_gen_dispensa_mese = set()
                for r_d in righe_valide:
                    if r_d.get("categoria") == "Dispensa" and r_d.get("ingredienti"):
                        n_d = (r_d["ingredienti"].get("nome") or "").strip()
                        if n_d:
                            nomi_gen_dispensa_mese.add(generalizza_nome_dispensa(n_d).lower())

                ids_ric = list({r["id_ricetta"] for r in righe_valide if is_valid_uuid(r.get("id_ricetta"))})
                mappa_ri = {}
                if ids_ric:
                    res_ri = supabase.table("ricette_ingredienti").select(
                        "*, ingredienti(id, nome, unita_default)"
                    ).in_("id_ricetta", ids_ric).limit(5000).execute()
                    for ri in (res_ri.data or []):
                        mappa_ri.setdefault(ri["id_ricetta"], []).append(ri)

                somme = {}
                spuntati_sessione.clear()

                def aggiungi_alla_spesa(ing_obj, qta_ing, unita_ing, id_menu_rif="", cat_riga="", preso_riga=False, data_menu_str="", nome_ricetta_orig=""):
                    if not ing_obj:
                        return
                    if isinstance(ing_obj, str):
                        nome_ing = ing_obj
                        u_def_db = ""
                    else:
                        nome_ing = ing_obj.get("nome", "Prodotto")
                        u_def_db = str(ing_obj.get("unita_default") or "")

                    nome_disp = generalizza_nome_dispensa(nome_ing)
                    is_base_dinamico = (
                        ("|DISPENSA" in u_def_db)
                        or (cat_riga == "Dispensa")
                        or (nome_disp.lower() in nomi_gen_dispensa_mese)
                        or is_ingrediente_dispensa_attivo(ing_obj)
                    )
                    n_low = nome_ing.strip().lower()
                    q_norm, u_base = normalizza_qta_unita(qta_ing, unita_ing)

                    if is_base_dinamico and cat_riga != "Detersivi":
                        if nome_ing.strip().lower() != nome_disp.lower():
                            log_render(f"[SPESA] Unificato ingrediente '{nome_ing}' nella voce di Dispensa '{nome_disp}'")
                        chiave = (nome_disp.lower(), "promemoria")
                        somme.setdefault(chiave, {
                            "nome": nome_disp, "qta": 1.0, "unita": "pz",
                            "ids_menu_diretti": [], "preso_db": False, "etichetta_extra": "[Dispensa]",
                            "dettagli_uso": []
                        })
                        if id_menu_rif:
                            somme[chiave]["ids_menu_diretti"].append(id_menu_rif)
                    else:
                        chiave = (n_low, u_base)
                        somme.setdefault(chiave, {
                            "nome": nome_ing.capitalize(), "qta": 0.0, "unita": u_base,
                            "ids_menu_diretti": [], "preso_db": False,
                            "etichetta_extra": "[Detersivi]" if cat_riga == "Detersivi" else "",
                            "dettagli_uso": []
                        })
                        somme[chiave]["qta"] += q_norm
                        if id_menu_rif:
                            somme[chiave]["ids_menu_diretti"].append(id_menu_rif)
                        if cat_riga == "Detersivi":
                            somme[chiave]["etichetta_extra"] = "[Detersivi]"

                    if data_menu_str and cat_riga not in ["Dispensa", "Detersivi"]:
                        somme[chiave]["dettagli_uso"].append({
                            "data_menu": data_menu_str,
                            "categoria": cat_riga,
                            "ricetta": nome_ricetta_orig or f"Voce diretta ({cat_riga})",
                            "qta_str": formatta_qta_finale(q_norm, u_base)
                        })

                    if preso_riga:
                        somme[chiave]["preso_db"] = True

                for m in righe_valide:
                    cat = m.get("categoria") or ""
                    d_m_str = m.get("data_menu") or ""
                    is_preso_db = bool(m.get("preso"))
                    if m.get("id_ingrediente") and m.get("ingredienti"):
                        aggiungi_alla_spesa(m["ingredienti"], m.get("quantita") or 0, m.get("unita_misura"), m["id"], cat, is_preso_db, d_m_str, "")
                    elif m.get("id_ricetta"):
                        nome_r_m = (m.get("ricette") or {}).get("nome") or "Ricetta"
                        for ri in mappa_ri.get(m["id_ricetta"], []):
                            if ri.get("ingredienti"):
                                aggiungi_alla_spesa(ri["ingredienti"], ri.get("quantita") or 0, ri.get("unita_misura"), "", cat, is_preso_db, d_m_str, nome_r_m)

                for v in somme.values():
                    if v["preso_db"]:
                        spuntati_sessione.add(f"{v['nome'].lower()}_{v['unita']}")

                def chiave_ordinamento(item):
                    ext = item.get("etichetta_extra", "")
                    if "[Dispensa]" in ext:
                        prio = 1
                    elif "[Detersivi]" in ext:
                        prio = 2
                    else:
                        prio = 0
                    return (prio, item["nome"].lower())

                dati_aggregati = sorted(list(somme.values()), key=chiave_ordinamento)
                log_render(f"[SPESA] Calcolo completato: {len(dati_aggregati)} voci aggregate totali ({len(spuntati_sessione)} già prese/in dispensa)")
                spinner_spesa.visible = False
                disegna_lista_spesa()
            except Exception as ex:
                log_render(f"[ERRORE] Calcolo lista spesa: {ex}")
                spinner_spesa.visible = False
                colonna_lista.controls.clear()
                colonna_lista.controls.append(ft.Text(f"Errore calcolo spesa: {ex}", color=ft.Colors.RED_700, size=12))
                page.update()

        threading.Thread(target=worker, daemon=True).start()

    def disegna_lista_spesa():
        colonna_lista.controls.clear()
        if not dati_aggregati:
            txt_riepilogo.value = "Nessun ingrediente pianificato."
            page.update()
            return

        da_comprare, gia_in_dispensa = [], []
        for item in dati_aggregati:
            k_id = f"{item['nome'].lower()}_{item['unita']}"
            (gia_in_dispensa if k_id in spuntati_sessione else da_comprare).append((k_id, item))

        txt_riepilogo.value = f"Da acquistare: {len(da_comprare)}  |  In dispensa/Presi: {len(gia_in_dispensa)}"

        def toggle_spunta(e, key_ing, item_obj):
            nuovo_stato = bool(e.control.value)
            log_render(f"[UTENTE] Spunta lista della spesa su '{item_obj.get('nome')}': preso={nuovo_stato}")
            (spuntati_sessione.add if nuovo_stato else spuntati_sessione.discard)(key_ing)
            modifiche_spunte[0] = True
            ids_dir = item_obj.get("ids_menu_diretti") or []
            if ids_dir:
                threading.Thread(target=lambda: [aggiorna_flag_preso_db(mid, nuovo_stato) for mid in ids_dir], daemon=True).start()
            disegna_lista_spesa()

        # Configurazione grafica per distinguere le 3 categorie
        STILI_SEZIONE = {
            "": {
                "titolo": "🥩 INGREDIENTI DA COMPRARE",
                "bg_header": "#1565C0", "bg_card": "#F5F7FA", "bordo": "#90CAF9", "mostra_qta": True
            },
            "[Dispensa]": {
                "titolo": "📦 DISPENSA (CONTROLLO SCORTE)",
                "bg_header": "#00897B", "bg_card": "#E0F2F1", "bordo": "#4DB6AC", "mostra_qta": False
            },
            "[Detersivi]": {
                "titolo": "🧼 DETERSIVI E CASA",
                "bg_header": "#546E7A", "bg_card": "#ECEFF1", "bordo": "#90A4AE", "mostra_qta": False
            }
        }

        def mostra_popup_ricette_ingrediente(item_obj):
            nome_i = item_obj.get("nome", "Ingrediente")
            usi = sorted(item_obj.get("dettagli_uso") or [], key=lambda x: (x.get("data_menu", ""), x.get("categoria", "")))
            ext = item_obj.get("etichetta_extra", "")
            tot_str = f" — Totale: {formatta_qta_finale(item_obj['qta'], item_obj['unita'])}" if ext == "" else ""

            righe_dettaglio = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, tight=True)
            if not usi:
                righe_dettaglio.controls.append(
                    ft.Container(
                        content=ft.Text("Prodotto inserito manualmente in Dispensa o Detersivi del mese (non legato a una ricetta specifica).", size=12, color=ft.Colors.GREY_700, italic=True),
                        padding=8
                    )
                )
            else:
                for u_info in usi:
                    d_raw = u_info.get("data_menu", "")
                    try:
                        dt_u = datetime.strptime(d_raw, "%Y-%m-%d")
                        data_leggibile = f"{GIORNI_ESTESI[dt_u.weekday()]} {dt_u.strftime('%d/%m')}"
                    except Exception:
                        data_leggibile = d_raw
                    cat_u = u_info.get("categoria", "")
                    icona_cat = "☀️" if cat_u == "Pranzo" else ("🌙" if cat_u == "Cena" else "📝")
                    righe_dettaglio.controls.append(
                        ft.Container(
                            content=ft.Row([
                                ft.Column([
                                    ft.Text(f"📅 {data_leggibile} ({icona_cat} {cat_u})", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900),
                                    ft.Text(f"🍲 {u_info.get('ricetta', '')}", size=12, weight=ft.FontWeight.BOLD, color=ft.Colors.BLACK)
                                ], spacing=2, expand=True),
                                ft.Container(
                                    content=ft.Text(u_info.get("qta_str", ""), size=11, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                                    bgcolor=ft.Colors.TEAL_700, padding=ft.Padding(8, 4, 8, 4), border_radius=6
                                )
                            ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                            bgcolor="#F5F7FA", padding=8, border_radius=6, border=bordo_campo_uniforme("#CFD8DC")
                        )
                    )

            dlg_uso = None
            dlg_uso = ft.AlertDialog(
                title=ft.Text(f"🔍 {nome_i}{tot_str}", size=15, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900),
                content=ft.Container(content=righe_dettaglio, width=330, height=min(360, max(90, len(usi) * 62))),
                actions=[
                    ft.Container(
                        content=ft.Text("Chiudi", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=12),
                        bgcolor=ft.Colors.BLUE_900, padding=ft.Padding(14, 8, 14, 8), border_radius=6,
                        on_click=lambda e: chiudi_dialog_sicuro(page, dlg_uso)
                    )
                ],
                shape=ft.RoundedRectangleBorder(radius=10), content_padding=10
            )
            apri_dialog_sicuro(page, dlg_uso)
        
        def sposta_in_dispensa_rapido(e, nome_ing):
            log_render(f"[UTENTE] Spostamento rapido in Dispensa dalla Spesa per '{nome_ing}'")
            imposta_stato_dispensa_ingrediente_db(nome_ing, "", True, data_riferimento)
            avvia_calcolo_spesa()

        def togli_da_dispensa_rapido(e, nome_ing):
            log_render(f"[UTENTE] Rimozione rapida da Dispensa dalla Spesa per '{nome_ing}'")
            imposta_stato_dispensa_ingrediente_db(nome_ing, "", False, data_riferimento)
            avvia_calcolo_spesa()

        sezione_corrente = None
        for k_id, item in da_comprare:
            ext = item.get("etichetta_extra", "")
            stile = STILI_SEZIONE.get(ext, STILI_SEZIONE[""])

            if ext != sezione_corrente:
                sezione_corrente = ext
                colonna_lista.controls.append(
                    ft.Container(
                        content=ft.Text(stile["titolo"], size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                        bgcolor=stile["bg_header"], padding=ft.Padding(8, 4, 8, 4), border_radius=5
                    )
                )

            riga_ctrl = [
                ft.Checkbox(value=False, on_change=lambda e, k=k_id, it=item: toggle_spunta(e, k, it)),
                ft.Container(
                    content=ft.Row([
                        ft.Text(item["nome"], size=13, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900 if item.get("dettagli_uso") else ft.Colors.BLACK, expand=True),
                        *([ft.Text(f"({len(item['dettagli_uso'])} ric.)", size=10, color=ft.Colors.GREY_600)] if item.get("dettagli_uso") else [])
                    ], spacing=4),
                    expand=True, padding=ft.Padding(0, 6, 0, 6),
                    on_click=lambda e, it=item: mostra_popup_ricette_ingrediente(it)
                )
            ]

            # Se siamo nella sezione acquisti (senza etichetta extra), aggiungiamo il tasto rapido 📌 Dispensa
            if ext == "":
                riga_ctrl.append(
                    ft.Container(
                        content=ft.Text("📌 Dispensa", size=10, color="#00897B", weight=ft.FontWeight.BOLD),
                        bgcolor="#E0F2F1", padding=ft.Padding(6, 3, 6, 3), border_radius=6,
                        border=bordo_campo_uniforme("#00897B", 1),
                        on_click=lambda e, ni=item["nome"]: sposta_in_dispensa_rapido(e, ni)
                    )
                )
            elif ext == "[Dispensa]":
                riga_ctrl.append(
                    ft.Container(
                        content=ft.Text("↩️ Togli", size=10, color=ft.Colors.RED_700, weight=ft.FontWeight.BOLD),
                        bgcolor="#FFEBEE", padding=ft.Padding(6, 3, 6, 3), border_radius=6,
                        border=bordo_campo_uniforme(ft.Colors.RED_300, 1),
                        on_click=lambda e, ni=item["nome"]: togli_da_dispensa_rapido(e, ni)
                    )
                )

            # Mostra la quantità con il badge blu SOLO per gli ingredienti delle ricette
            if stile["mostra_qta"]:
                q_finale = formatta_qta_finale(item["qta"], item["unita"])
                riga_ctrl.append(
                    ft.Container(
                        content=ft.Text(q_finale, size=12, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                        bgcolor=ft.Colors.BLUE_900, padding=ft.Padding(8, 3, 8, 3), border_radius=8
                    )
                )

            colonna_lista.controls.append(
                ft.Container(
                    content=ft.Row(riga_ctrl, spacing=4),
                    bgcolor=stile["bg_card"], padding=ft.Padding(4, 2, 8, 2), border_radius=6,
                    border=ft.Border(
                        top=ft.BorderSide(1, stile["bordo"]), bottom=ft.BorderSide(1, stile["bordo"]),
                        left=ft.BorderSide(4, stile["bg_header"]), right=ft.BorderSide(1, stile["bordo"])
                    )
                )
            )

        if gia_in_dispensa:
            colonna_lista.controls.append(ft.Divider(height=8, color="#E0E0E0"))
            colonna_lista.controls.append(
                ft.Text("✅ GIÀ IN DISPENSA / PRESI:", size=10, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_700)
            )
            for k_id, item in gia_in_dispensa:
                ext = item.get("etichetta_extra", "")
                mostra_q = (ext == "")
                q_finale = formatta_qta_finale(item["qta"], item["unita"]) if mostra_q else ""
                colonna_lista.controls.append(
                    ft.Container(
                        content=ft.Row([
                            ft.Checkbox(value=True, on_change=lambda e, k=k_id, it=item: toggle_spunta(e, k, it)),
                            ft.Container(
                                content=ft.Text(f"✓ {item['nome']}", size=12, color=ft.Colors.GREY_600, italic=True),
                                expand=True, padding=ft.Padding(0, 4, 0, 4),
                                on_click=lambda e, it=item: mostra_popup_ricette_ingrediente(it)
                            ),
                            *([ft.Text(q_finale, size=11, color=ft.Colors.GREY_600)] if mostra_q else [])
                        ], spacing=4),
                        bgcolor="#EEEEEE", padding=ft.Padding(4, 1, 8, 1), border_radius=6
                    )
                )
        page.update()

    avvia_calcolo_spesa()
