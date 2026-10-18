import flet as ft
import calendar
import threading
from datetime import datetime, timedelta
from modulo_ricette import (
    supabase,
    MESI,
    GIORNI,
    GIORNI_ESTESI,
    SUPERCATEGORIE,
    SUBCATEGORIE_PASTO,
    SUB_CON_RICETTA,
    COLORI_PASTI,
    CACHE_ANAGRAFICA,
    ULTIMA_DATA_MENU_ATTIVA,
    log_render,
    is_valid_uuid,
    apri_dialog_sicuro,
    chiudi_dialog_sicuro,
    parse_qta,
    formatta_numero,
    bordo_campo_uniforme,
    aggiorna_flag_preso_db,
    estrai_meta_ricetta,
    normalizza_ingrediente_record,
    is_ingrediente_dispensa_attivo,
    imposta_stato_dispensa_ingrediente_db,
    assicura_ingrediente_in_dispensa_mese,
    sincronizza_e_pulisci_dispensa_mese,
    cerca_ingrediente_in_cache_per_nome,
    crea_box_quantita,
    crea_selettore_unita,
    assicura_cache_anagrafica,
    ottieni_o_crea_ing_sicuro,
    ottieni_o_crea_ricetta_sicura,
    aggiorna_conteggio_ricette_visibili,
    ricetta_ha_ingredienti,
    apri_popup_rinomina_ingrediente,
    apri_popup_dettaglio_ricetta_semplificata,
    apri_popup_aggiungi_piatto_semplificato,
    apri_popup_aggiungi_voce_diretta
)
from modulo_spesa import apri_popup_fai_la_spesa


# ==========================================================
# FORM MENU DEL GIORNO COMPLETO (CON INGREDIENTI ESPANSI SOTTO OGNI RICETTA)
# ==========================================================
def apri_form_menu_giorno(page: ft.Page, data_selezionata: datetime, on_success_callback=None):
    if not supabase:
        return

    ULTIMA_DATA_MENU_ATTIVA[0] = data_selezionata
    data_str = data_selezionata.strftime("%Y-%m-%d")
    data_vis = data_selezionata.strftime("%d/%m/%Y")
    log_render(f"[UTENTE] Apertura form Menu del Giorno per {data_vis} ({data_str})")

    lista_ingredienti = []
    lista_ricette = []
    menu_del_giorno = []
    mappa_ricette_ing = {}
    modifiche_effettuate = [False]
    cat_aperte = set()
    sub_aperte = set()

    spinner_header = ft.ProgressRing(width=18, height=18, stroke_width=2.5, color=ft.Colors.WHITE)
    lbl_stato_caricamento = ft.Text("Lettura dati...", size=11, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD)
    banner_loading = ft.Container(
        content=ft.Row([spinner_header, lbl_stato_caricamento], alignment=ft.MainAxisAlignment.CENTER, spacing=8),
        bgcolor=ft.Colors.ORANGE_800, padding=ft.Padding(10, 6, 10, 6), border_radius=6, visible=True
    )
    colonna_contenuto = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)
    dlg_menu = None

    def chiudi_dlg(e=None):
        chiudi_dialog_sicuro(page, dlg_menu)
        if modifiche_effettuate[0]:
            if on_success_callback:
                on_success_callback()
            page.pubsub.send_all("aggiorna_menu")

    dlg_menu = ft.AlertDialog(
        title=ft.Column([ft.Text(f"🍽️ Menu {data_vis}", size=15, weight=ft.FontWeight.BOLD), banner_loading], spacing=6, tight=True),
        content=ft.Container(content=colonna_contenuto, width=355, height=490),
        actions=[
            ft.Container(
                content=ft.Text("Chiudi", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=12),
                bgcolor=ft.Colors.BLUE_900, padding=ft.Padding(14, 8, 14, 8), border_radius=6, on_click=chiudi_dlg
            )
        ],
        shape=ft.RoundedRectangleBorder(radius=10), content_padding=8, inset_padding=ft.Padding(10, 15, 10, 15)
    )
    apri_dialog_sicuro(page, dlg_menu)

    def mostra_loading(testo="Operazione in corso..."):
        lbl_stato_caricamento.value = testo
        banner_loading.visible = True
        page.update()

    def nascondi_loading():
        banner_loading.visible = False
        page.update()

    def carica_dati_giorno_db(inizializza_aperture=False):
        nonlocal lista_ingredienti, lista_ricette, menu_del_giorno, mappa_ricette_ing
        try:
            lista_ingredienti, lista_ricette = assicura_cache_anagrafica(forza_ricarica=False)
            res_menu = supabase.table("menu_giornalieri").select(
                "*, ricette(id, nome, portata_default), ingredienti(id, nome, unita_default)"
            ).eq("data_menu", data_str).execute()
            menu_del_giorno = res_menu.data if res_menu.data else []

            ids_ricette_giorno = [m["id_ricetta"] for m in menu_del_giorno if is_valid_uuid(m.get("id_ricetta"))]
            mappa_ricette_ing.clear()
            if ids_ricette_giorno:
                res_ri = supabase.table("ricette_ingredienti").select(
                    "*, ingredienti(id, nome, unita_default)"
                ).in_("id_ricetta", ids_ricette_giorno).execute()
                visti_per_ric = {}
                for riga in (res_ri.data or []):
                    rid = riga["id_ricetta"]
                    ing_d = riga.get("ingredienti") or {}
                    chiave_u = str(ing_d.get("nome") or riga.get("id_ingrediente") or riga.get("id")).strip().lower()
                    if chiave_u not in visti_per_ric.setdefault(rid, set()):
                        visti_per_ric[rid].add(chiave_u)
                        mappa_ricette_ing.setdefault(rid, []).append(riga)
                for rid in ids_ricette_giorno:
                    CACHE_ANAGRAFICA["conteggio_ing_ricette"][str(rid)] = len(mappa_ricette_ing.get(rid, []))

            if inizializza_aperture:
                for m in menu_del_giorno:
                    if m["categoria"] in SUPERCATEGORIE:
                        cat_aperte.add(m["categoria"])
                    if m.get("subcategoria"):
                        sub_aperte.add((m["categoria"], m["subcategoria"]))
            log_render(f"[MENU GIORNO] Caricate {len(menu_del_giorno)} voci e {len(ids_ricette_giorno)} ricette per {data_str}")
        except Exception as ex:
            log_render(f"[ERRORE] carica_dati_giorno_db ({data_str}): {ex}")

    def esegui_azione_db(testo_loading, funzione_db, ricarica_giorno=True):
        log_render(f"[MENU GIORNO] Azione richiesta su {data_str}: {testo_loading}")
        mostra_loading(testo_loading)

        def worker():
            try:
                funzione_db()
                modifiche_effettuate[0] = True
                sincronizza_e_pulisci_dispensa_mese(data_selezionata)
                if ricarica_giorno:
                    carica_dati_giorno_db(inizializza_aperture=False)
                disegna_form_da_memoria()
            except Exception as ex:
                log_render(f"[ERRORE] esegui_azione_db ({testo_loading}): {ex}")
            finally:
                nascondi_loading()

        threading.Thread(target=worker, daemon=True).start()

    def apri_dialog_ripeti_mese(categoria):
        voci_correnti = [m for m in menu_del_giorno if m["categoria"] == categoria and not m.get("subcategoria")]
        dlg_rip = None

        def esegui_rip(da_oggi_bool):
            chiudi_dialog_sicuro(page, dlg_rip)

            def op():
                anno, mese = data_selezionata.year, data_selezionata.month
                ultimo_g = calendar.monthrange(anno, mese)[1]
                g_start = data_selezionata.day if da_oggi_bool else 1
                p_str, u_str = f"{anno:04d}-{mese:02d}-{g_start:02d}", f"{anno:04d}-{mese:02d}-{ultimo_g:02d}"
                supabase.table("menu_giornalieri").delete().eq("categoria", categoria).is_("subcategoria", "null").gte("data_menu", p_str).lte("data_menu", u_str).neq("data_menu", data_str).execute()
                if voci_correnti:
                    recs = []
                    for g in range(g_start, ultimo_g + 1):
                        d_t = f"{anno:04d}-{mese:02d}-{g:02d}"
                        if d_t != data_str:
                            for vc in voci_correnti:
                                recs.append({
                                    "data_menu": d_t, "categoria": categoria, "subcategoria": None,
                                    "id_ricetta": vc.get("id_ricetta"), "id_ingrediente": vc.get("id_ingrediente"),
                                    "quantita": vc.get("quantita") or 0, "unita_misura": vc.get("unita_misura") or "g"
                                })
                    if recs:
                        supabase.table("menu_giornalieri").insert(recs).execute()

            esegui_azione_db(f"⏳ Copia di {categoria} nel mese...", op, ricarica_giorno=False)

        dlg_rip = ft.AlertDialog(
            title=ft.Text(f"🔁 Ripeti {categoria} nel mese", size=15, weight=ft.FontWeight.BOLD),
            content=ft.Text(f"Vuoi copiare le voci di '{categoria}' sugli altri giorni di {MESI[data_selezionata.month]}?", size=12),
            actions=[
                ft.TextButton("Annulla", on_click=lambda e: chiudi_dialog_sicuro(page, dlg_rip)),
                ft.Container(content=ft.Text("Da oggi a fine mese", color=ft.Colors.WHITE, size=11, weight=ft.FontWeight.BOLD), bgcolor=ft.Colors.TEAL_700, padding=8, border_radius=5, on_click=lambda e: esegui_rip(True)),
                ft.Container(content=ft.Text("Tutto il mese", color=ft.Colors.WHITE, size=11, weight=ft.FontWeight.BOLD), bgcolor=ft.Colors.BLUE_900, padding=8, border_radius=5, on_click=lambda e: esegui_rip(False))
            ]
        )
        apri_dialog_sicuro(page, dlg_rip)

    def crea_sezione_ingredienti_diretti(categoria, subcategoria=None):
        voci = [m for m in menu_del_giorno if m["categoria"] == categoria and m.get("subcategoria") == subcategoria and m.get("id_ingrediente")]
        lista_ui = ft.Column(spacing=4)
        for v in voci:
            ing_obj = normalizza_ingrediente_record(v.get("ingredienti") or {"id": v["id_ingrediente"], "nome": "Ingrediente", "unita_default": "g"})
            u_clean = str(v.get("unita_misura") or "").replace("|DET", "").replace("|DISPENSA", "").strip()
            qta_str = f"{formatta_numero(v.get('quantita') or 0)} {u_clean}".strip()
            lista_ui.controls.append(
                ft.Container(
                    content=ft.Row([
                        ft.Text(f"• {ing_obj['nome']}", size=12, weight=ft.FontWeight.BOLD, expand=True),
                        ft.Text(qta_str, size=11, color=ft.Colors.BLUE_900, weight=ft.FontWeight.BOLD),
                        ft.IconButton(icon=ft.Icons.EDIT, icon_color=ft.Colors.ORANGE_800, icon_size=15, on_click=lambda e, item=ing_obj: apri_popup_rinomina_ingrediente(page, item, lista_ingredienti, lambda: esegui_azione_db("⏳ Aggiornamento...", lambda: None))),
                        ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_color=ft.Colors.RED_700, icon_size=16, on_click=lambda e, id_v=v["id"]: esegui_azione_db("⏳ Rimozione...", lambda: supabase.table("menu_giornalieri").delete().eq("id", id_v).execute()))
                    ], spacing=2),
                    bgcolor="#F5F5F5", padding=ft.Padding(6, 2, 2, 2), border_radius=5
                )
            )

        txt_ing = ft.TextField(label="Cerca o scrivi ingrediente...", dense=True, text_size=12, expand=True)
        box_qta = crea_box_quantita(valore_iniziale="1", height=34, text_size=12, mostra_prefisso=True, expand=True)
        sel_unita = crea_selettore_unita(page, valore_iniziale="pz", width=74, height=34, text_size=12)
        riga_sugg = ft.Row(wrap=True, spacing=4, run_spacing=4, visible=False)

        def filtra_ing(e):
            t = (txt_ing.value or "").strip().lower()
            riga_sugg.controls.clear()
            if t:
                for m_ing in [i for i in lista_ingredienti if not i.get("is_detersivo") and t in i["nome"].lower()][:6]:
                    riga_sugg.controls.append(
                        ft.Container(
                            content=ft.Text(f"🔍 {m_ing['nome']}", size=11, color=ft.Colors.BLUE_900, weight=ft.FontWeight.BOLD),
                            bgcolor=ft.Colors.BLUE_50, padding=ft.Padding(8, 4, 8, 4), border_radius=10,
                            on_click=lambda ev, item=m_ing: (setattr(txt_ing, "value", item["nome"]), sel_unita.set_value(item.get("unita_default", "pz")), setattr(riga_sugg, "visible", False), page.update())
                        )
                    )
                riga_sugg.visible = len(riga_sugg.controls) > 0
            else:
                riga_sugg.visible = False
            page.update()

        txt_ing.on_change = filtra_ing

        def add_dir(e):
            n_val = (txt_ing.value or "").strip()
            if not n_val:
                return
            q_v, u_v = parse_qta(box_qta.textfield.value), sel_unita.get_value()
            esegui_azione_db("⏳ Salvataggio...", lambda: supabase.table("menu_giornalieri").insert({
                "data_menu": data_str, "categoria": categoria, "subcategoria": subcategoria,
                "id_ingrediente": ottieni_o_crea_ing_sicuro(n_val, u_v, False), "quantita": q_v, "unita_misura": u_v
            }).execute())

        return ft.Column([
            lista_ui, txt_ing, riga_sugg,
            ft.Row([
                box_qta, sel_unita,
                ft.Container(content=ft.Text("+ Aggiungi", color=ft.Colors.WHITE, size=11, weight=ft.FontWeight.BOLD), bgcolor=ft.Colors.BLUE_900, height=34, padding=ft.Padding(12, 0, 12, 0), alignment=ft.Alignment(0, 0), border_radius=5, on_click=add_dir)
            ], spacing=6)
        ], spacing=6)

    def crea_sezione_ricetta(categoria, subcategoria):
        voci_ricetta = [m for m in menu_del_giorno if m["categoria"] == categoria and (subcategoria is None or m.get("subcategoria") == subcategoria) and m.get("id_ricetta")]
        blocchi_ui = ft.Column(spacing=8)
        for v in voci_ricetta:
            ric_obj = v.get("ricette") or {}
            id_ric = v["id_ricetta"]
            nome_ric = ric_obj.get("nome", "Ricetta")
            meta_r = estrai_meta_ricetta(ric_obj)
            porzioni_ric = meta_r["porzioni"]
            ings_associati = mappa_ricette_ing.get(id_ric, [])
            ha_i = len(ings_associati) > 0

            col_ing_espansi = ft.Column(spacing=4)
            for ri in ings_associati:
                raw_d = ri.get("ingredienti") or {"id": ri["id_ingrediente"], "nome": "Ingrediente", "unita_default": "g"}
                ing_c = cerca_ingrediente_in_cache_per_nome(raw_d.get("nome", ""))
                ing_info = dict(ing_c) if ing_c else normalizza_ingrediente_record(raw_d)

                box_q_ri = crea_box_quantita(valore_iniziale=formatta_numero(ri.get("quantita") or 0), width=86, height=32, text_size=11, mostra_prefisso=True)
                sel_u_ri = crea_selettore_unita(page, valore_iniziale=ri.get("unita_misura") or "g", width=70, height=32, text_size=11)

                chk_disp_g = ft.Checkbox(
                    label="📌 Dispensa (Base)",
                    value=is_ingrediente_dispensa_attivo(ing_info)
                )

                def toggle_disp_giorno(e, info_ref=ing_info):
                    nuovo_stato = bool(e.control.value)
                    modifiche_effettuate[0] = True

                    def w_tg():
                        imposta_stato_dispensa_ingrediente_db(
                            info_ref.get("nome", ""),
                            info_ref.get("id", ""),
                            nuovo_stato,
                            data_selezionata
                        )
                    threading.Thread(target=w_tg, daemon=True).start()

                chk_disp_g.on_change = toggle_disp_giorno

                col_ing_espansi.controls.append(
                    ft.Container(
                        content=ft.Column([
                            ft.Row([
                                ft.Text(f"▪ {ing_info['nome']}", size=11, weight=ft.FontWeight.BOLD, expand=True),
                                ft.IconButton(icon=ft.Icons.EDIT, icon_color=ft.Colors.ORANGE_800, icon_size=14, on_click=lambda e, item=ing_info: apri_popup_rinomina_ingrediente(page, item, lista_ingredienti, lambda: esegui_azione_db("⏳ Aggiornamento...", lambda: None))),
                                ft.IconButton(icon=ft.Icons.CLOSE, icon_color=ft.Colors.RED_700, icon_size=14, on_click=lambda e, id_r=ri["id"]: esegui_azione_db("⏳ Rimozione...", lambda: supabase.table("ricette_ingredienti").delete().eq("id", id_r).execute()))
                            ], spacing=2),
                            ft.Row([
                                box_q_ri, sel_u_ri,
                                ft.Container(
                                    content=ft.Text("💾 Salva Q.tà", size=10, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                                    bgcolor=ft.Colors.GREEN_700, height=32, padding=ft.Padding(8, 0, 8, 0), alignment=ft.Alignment(0, 0), border_radius=5,
                                    on_click=lambda e, id_r=ri["id"], bq=box_q_ri, su=sel_u_ri: esegui_azione_db("⏳ Salvataggio...", lambda: supabase.table("ricette_ingredienti").update({"quantita": parse_qta(bq.textfield.value), "unita_misura": su.get_value()}).eq("id", id_r).execute())
                                )
                            ], spacing=4),
                            chk_disp_g
                        ], spacing=2),
                        bgcolor=ft.Colors.WHITE, padding=5, border_radius=5
                    )
                )

            blocchi_ui.controls.append(
                ft.Container(
                    content=ft.Column([
                        ft.Row([
                            ft.Text(f"{'🍲' if ha_i else '⚠️'} {nome_ric} ({porzioni_ric}p)", size=13, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900 if ha_i else ft.Colors.RED_700, expand=True),
                            ft.Container(
                                content=ft.Text("🔍 Ricerca / +Ing", size=10, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                                bgcolor=ft.Colors.ORANGE_800, padding=ft.Padding(8, 4, 8, 4), border_radius=6,
                                on_click=lambda e, vm=v, r_id=id_ric, r_n=nome_ric, r_p=meta_r["portata"]: apri_popup_dettaglio_ricetta_semplificata(
                                    page, vm["id"], r_id, r_n, r_p, lambda: esegui_azione_db("⏳ Aggiornamento...", lambda: None)
                                )
                            ),
                            ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_color=ft.Colors.RED_700, icon_size=17, on_click=lambda e, id_m=v["id"]: esegui_azione_db("⏳ Rimozione...", lambda: supabase.table("menu_giornalieri").delete().eq("id", id_m).execute()))
                        ], spacing=4),
                        col_ing_espansi
                    ], spacing=5),
                    bgcolor="#F0F4F8" if ha_i else "#FFF8E1", padding=6, border_radius=8, border=bordo_campo_uniforme("#BBDEFB" if ha_i else "#E53935", 1.5)
                )
            )

        txt_ric = ft.TextField(label=f"Aggiungi ricetta ({subcategoria or categoria})...", dense=True, text_size=12, expand=True)
        riga_sugg_r = ft.Row(wrap=True, spacing=4, run_spacing=4, visible=False)

        def assegna_r(id_r):
            esegui_azione_db("⏳ Inserimento...", lambda: supabase.table("menu_giornalieri").insert({"data_menu": data_str, "categoria": categoria, "subcategoria": subcategoria or "Dolci", "id_ricetta": id_r}).execute())

        def filtra_r(e):
            t = (txt_ric.value or "").strip().lower()
            riga_sugg_r.controls.clear()
            if t:
                for mr in [r for r in lista_ricette if t in r["nome"].lower()][:6]:
                    riga_sugg_r.controls.append(
                        ft.Container(
                            content=ft.Text(f"🍲 {mr['nome']}", size=11, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                            bgcolor=ft.Colors.BLUE_900, padding=ft.Padding(8, 4, 8, 4), border_radius=10,
                            on_click=lambda ev, rid=mr["id"]: assegna_r(rid)
                        )
                    )
                riga_sugg_r.visible = len(riga_sugg_r.controls) > 0
            else:
                riga_sugg_r.visible = False
            page.update()

        txt_ric.on_change = filtra_r

        def crea_r_click(e):
            n_r = (txt_ric.value or "").strip()
            if not n_r:
                return
            esegui_azione_db("⏳ Creazione...", lambda: supabase.table("menu_giornalieri").insert({
                "data_menu": data_str, "categoria": categoria, "subcategoria": subcategoria or "Dolci",
                "id_ricetta": ottieni_o_crea_ricetta_sicura(n_r, subcategoria or "Dolci", ignora_cache=True)["id"]
            }).execute())

        return ft.Column([
            blocchi_ui,
            ft.Row([
                txt_ric,
                ft.Container(content=ft.Text("+ Ricetta", color=ft.Colors.WHITE, size=11, weight=ft.FontWeight.BOLD), bgcolor=ft.Colors.BLUE_900, height=34, padding=ft.Padding(10, 0, 10, 0), alignment=ft.Alignment(0, 0), border_radius=5, on_click=crea_r_click)
            ], spacing=6),
            riga_sugg_r
        ], spacing=6)

    def disegna_form_da_memoria():
        colonna_contenuto.controls.clear()
        for sup in SUPERCATEGORIE:
            is_sup_open = sup in cat_aperte
            colore_sup = COLORI_PASTI.get(sup, ft.Colors.BLUE_900)

            def toggle_sup(e, nome_sup=sup):
                if e.control.value:
                    cat_aperte.add(nome_sup)
                else:
                    cat_aperte.discard(nome_sup)
                disegna_form_da_memoria()

            header_sup = [ft.Container(content=ft.Checkbox(label=sup.upper(), value=is_sup_open, on_change=toggle_sup), expand=True)]
            if sup not in ["Pranzo", "Cena"]:
                header_sup.append(
                    ft.Container(
                        content=ft.Text("🔁 Ripeti nel mese", size=10, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                        bgcolor=colore_sup, padding=ft.Padding(7, 4, 7, 4), border_radius=12,
                        on_click=lambda e, s_cat=sup: apri_dialog_ripeti_mese(s_cat)
                    )
                )

            contenuto_sup = ft.Column(spacing=6, visible=is_sup_open)
            if is_sup_open:
                if sup in ["Pranzo", "Cena"]:
                    for sub in SUBCATEGORIE_PASTO:
                        k_sub = (sup, sub)
                        is_sub_open = k_sub in sub_aperte

                        def toggle_sub(e, ks=k_sub):
                            if e.control.value:
                                sub_aperte.add(ks)
                            else:
                                sub_aperte.discard(ks)
                            disegna_form_da_memoria()

                        blocco_sub = ft.Column([ft.Checkbox(label=sub, value=is_sub_open, on_change=toggle_sub)], spacing=4)
                        if is_sub_open:
                            blocco_sub.controls.append(crea_sezione_ricetta(sup, sub) if sub in SUB_CON_RICETTA else crea_sezione_ingredienti_diretti(sup, sub))
                        contenuto_sup.controls.append(
                            ft.Container(content=blocco_sub, bgcolor=ft.Colors.WHITE, padding=5, border_radius=6, border=bordo_campo_uniforme("#E0E0E0"))
                        )
                elif sup == "Varie":
                    contenuto_sup.controls.append(
                        ft.Container(
                            content=ft.Column([
                                ft.Text("🥖 Ingredienti semplici in Varie:", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.BROWN_700),
                                crea_sezione_ingredienti_diretti(sup, None),
                                ft.Divider(height=6, color="#D7CCC8"),
                                ft.Text("🧁 Ricette / Preparazioni in Varie:", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900),
                                crea_sezione_ricetta(sup, None)
                            ], spacing=6),
                            bgcolor=ft.Colors.WHITE, padding=6, border_radius=6
                        )
                    )
                else:
                    contenuto_sup.controls.append(ft.Container(content=crea_sezione_ingredienti_diretti(sup, None), bgcolor=ft.Colors.WHITE, padding=6, border_radius=6))

            colonna_contenuto.controls.append(
                ft.Container(
                    content=ft.Column([ft.Row(header_sup, alignment=ft.MainAxisAlignment.SPACE_BETWEEN), contenuto_sup], spacing=4),
                    bgcolor="#FAFAFA", padding=6, border_radius=8,
                    border=ft.Border(top=ft.BorderSide(1, "#E0E0E0"), bottom=ft.BorderSide(1, "#E0E0E0"), left=ft.BorderSide(5, colore_sup), right=ft.BorderSide(1, "#E0E0E0"))
                )
            )
        page.update()

    def avvio():
        try:
            disegna_form_da_memoria()
            carica_dati_giorno_db(inizializza_aperture=True)
            disegna_form_da_memoria()
        except Exception as ex:
            print("Errore avvio form giorno:", ex)
        finally:
            nascondi_loading()

    threading.Thread(target=avvio, daemon=True).start()


# ==========================================================
# CALENDARIO DEI MENU (SINCRONIZZAZIONE CACHE AUTOMATICA + VARIE BIVALENTE)
# ==========================================================
def crea_calendario_menu(page: ft.Page):
    stato = {"data": datetime.now(), "vista": "Mese", "semplificata": False}
    oggi = datetime.now()
    ram_menu = {"dizionario_menu": {}, "voci_dispensa_mese": [], "voci_detersivi_mese": []}

    testo_header = ft.Text(size=15, weight=ft.FontWeight.BOLD)
    badge_loading_cal = ft.Container(
        content=ft.Row([ft.ProgressRing(width=14, height=14, stroke_width=2, color=ft.Colors.WHITE), ft.Text("Sync...", size=10, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD)], spacing=5, tight=True),
        bgcolor=ft.Colors.ORANGE_800, padding=ft.Padding(8, 3, 8, 3), border_radius=10, visible=False
    )

    btn_giorno = ft.Container(content=ft.Text("Giorno", size=12, weight=ft.FontWeight.BOLD), border_radius=5, padding=6, on_click=lambda e: cambia_vista("Giorno"))
    btn_settimana = ft.Container(content=ft.Text("Settimana", size=12, weight=ft.FontWeight.BOLD), border_radius=5, padding=6, on_click=lambda e: cambia_vista("Settimana"))
    btn_mese = ft.Container(content=ft.Text("Mese", size=12, weight=ft.FontWeight.BOLD), border_radius=5, padding=6, on_click=lambda e: cambia_vista("Mese"))
    box_bottoni_vista = ft.Row([btn_giorno, btn_settimana, btn_mese], spacing=5, visible=True)

    def toggle_modalita_semplificata(e):
        stato["semplificata"] = bool(switch_semplificata.value)
        box_bottoni_vista.visible = not stato["semplificata"]
        disegna_vista_da_ram()
        aggiorna_calendario_menu()

    lbl_semplificata = ft.Text("⚡ Semplificata", size=12, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900)
    switch_semplificata = ft.Switch(value=True, on_change=toggle_modalita_semplificata)
    box_switch_semplificata = ft.Container(
        content=ft.Row([lbl_semplificata, switch_semplificata], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        bgcolor="#FFF3E0", padding=ft.Padding(8, 2, 6, 2), border_radius=15, border=bordo_campo_uniforme("#FFB74D")
    )

    layer_mese = ft.Column(spacing=0, visible=True)
    layer_settimana = ft.Column(spacing=0, visible=False)
    layer_giorno = ft.Column(spacing=0, visible=False)
    layer_semplificata = ft.Column(spacing=8, visible=False)
    area_dinamica = ft.Column([layer_mese, layer_settimana, layer_giorno, layer_semplificata], expand=True, spacing=0)

    def bordo_standard():
        return ft.Border(top=ft.BorderSide(0.5, "#E0E0E0"), bottom=ft.BorderSide(0.5, "#E0E0E0"), left=ft.BorderSide(0.5, "#E0E0E0"), right=ft.BorderSide(0.5, "#E0E0E0"))

    def apri_giorno_selezionato(dt_target):
        stato["data"] = dt_target
        apri_form_menu_giorno(page, dt_target, aggiorna_calendario_menu)

    def cambia_vista(nuova_vista):
        stato["vista"] = nuova_vista
        disegna_vista_da_ram()
        aggiorna_calendario_menu()

    def nav_prec(e):
        if stato["semplificata"] or stato["vista"] == "Settimana":
            stato["data"] -= timedelta(days=7)
        elif stato["vista"] == "Mese":
            scorso = stato["data"].replace(day=1) - timedelta(days=1)
            stato["data"] = stato["data"].replace(year=scorso.year, month=scorso.month, day=1)
        else:
            stato["data"] -= timedelta(days=1)
        aggiorna_calendario_menu()

    def nav_succ(e):
        if stato["semplificata"] or stato["vista"] == "Settimana":
            stato["data"] += timedelta(days=7)
        elif stato["vista"] == "Mese":
            m, a = stato["data"].month, stato["data"].year
            stato["data"] = stato["data"].replace(year=a + (1 if m == 12 else 0), month=(1 if m == 12 else m + 1), day=1)
        else:
            stato["data"] += timedelta(days=1)
        aggiorna_calendario_menu()

    def crea_card_giorno_semplificata(dt_giorno: datetime, indice_sett: int, voci_giorno: list):
        is_oggi = (dt_giorno.date() == oggi.date())
        d_str_g = dt_giorno.strftime("%Y-%m-%d")

        voci_pranzo = [x for x in voci_giorno if x.get("categoria") == "Pranzo" and x.get("id_ricetta")]
        voci_cena = [x for x in voci_giorno if x.get("categoria") == "Cena" and x.get("id_ricetta")]
        voci_varie = [x for x in voci_giorno if x.get("categoria") == "Varie"]

        def aggiungi_ottimistico_giorno(nuova_voce):
            ram_menu["dizionario_menu"].setdefault(d_str_g, []).append(nuova_voce)
            disegna_vista_da_ram()

        def costruisci_badge_piatto(voce_m, colore_tema, portata_default):
            ric = voce_m.get("ricette") or {}
            id_ric = voce_m.get("id_ricetta")
            nome_r = ric.get("nome") or "Piatto"
            meta_r = estrai_meta_ricetta(ric)
            port_r = meta_r["portata"] or voce_m.get("subcategoria") or portata_default
            porzioni_r = meta_r["porzioni"]
            in_salv = bool(voce_m.get("in_salvataggio")) or not is_valid_uuid(id_ric)

            if in_salv:
                return ft.Container(
                    content=ft.Row([
                        ft.ProgressRing(width=11, height=11, stroke_width=2, color=ft.Colors.GREY_700),
                        ft.Text(f"⏳ {nome_r} (salvataggio...)", size=11, italic=True, color=ft.Colors.GREY_700, expand=True)
                    ], spacing=6),
                    bgcolor="#E0E0E0", padding=ft.Padding(6, 4, 6, 4), border_radius=5,
                    border=bordo_campo_uniforme("#9E9E9E", 1)
                )

            compilata = ricetta_ha_ingredienti(id_ric)
            sfondo = "#E8F5E9" if (compilata and colore_tema == "#4CAF50") else ("#E3F2FD" if compilata else "#FFF59D")
            col_bordo = colore_tema if compilata else "#D32F2F"
            col_testo = "#1B5E20" if (compilata and colore_tema == "#4CAF50") else ("#0D47A1" if compilata else "#B71C1C")
            pref_icon = "🍲 " if compilata else "⚠️ "
            info_pers = f" ({porzioni_r}p)" if compilata else ""

            return ft.Container(
                content=ft.Row([
                    ft.Text(f"{pref_icon}{nome_r}{info_pers}", size=11, weight=ft.FontWeight.BOLD, color=col_testo, no_wrap=False, expand=True),
                    ft.Icon(ft.Icons.OPEN_IN_NEW, size=12, color=col_testo)
                ], spacing=2, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                bgcolor=sfondo, padding=ft.Padding(6, 4, 6, 4), border_radius=5,
                border=bordo_campo_uniforme(col_bordo, 1.5 if not compilata else 1),
                on_click=lambda e, vm=voce_m, r_p=port_r: apri_popup_dettaglio_ricetta_semplificata(
                    page, vm.get("id"), vm.get("id_ricetta"), (vm.get("ricette") or {}).get("nome", "Piatto"), r_p, aggiorna_calendario_menu
                )
            )

        casella_pranzo = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Text("☀️ PRANZO", size=8, weight=ft.FontWeight.BOLD, color="#2E7D32", expand=True),
                    ft.Container(
                        content=ft.Text("+ Piatto", size=9, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                        bgcolor="#4CAF50", padding=ft.Padding(6, 2, 6, 2), border_radius=8,
                        on_click=lambda e, dt=dt_giorno, vg=voci_giorno: apri_popup_aggiungi_piatto_semplificato(page, dt, "Pranzo", "Primo", vg, aggiungi_ottimistico_giorno, aggiorna_calendario_menu)
                    )
                ]),
                ft.Column([costruisci_badge_piatto(vp, "#4CAF50", "Primo") for vp in voci_pranzo] if voci_pranzo else [ft.Text("Nessun piatto previsto", size=10, color=ft.Colors.GREY_500, italic=True)], spacing=3)
            ], spacing=4),
            bgcolor=ft.Colors.WHITE, padding=6, border_radius=6,
            border=ft.Border(top=ft.BorderSide(1, "#C8E6C9"), bottom=ft.BorderSide(1, "#C8E6C9"), left=ft.BorderSide(4, "#4CAF50"), right=ft.BorderSide(1, "#C8E6C9"))
        )

        casella_cena = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Text("🌙 CENA", size=9, weight=ft.FontWeight.BOLD, color="#1565C0", expand=True),
                    ft.Container(
                        content=ft.Text("+ Piatto", size=9, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                        bgcolor="#2196F3", padding=ft.Padding(6, 2, 6, 2), border_radius=8,
                        on_click=lambda e, dt=dt_giorno, vg=voci_giorno: apri_popup_aggiungi_piatto_semplificato(page, dt, "Cena", "Secondo", vg, aggiungi_ottimistico_giorno, aggiorna_calendario_menu)
                    )
                ]),
                ft.Column([costruisci_badge_piatto(vc, "#2196F3", "Secondo") for vc in voci_cena] if voci_cena else [ft.Text("Nessun piatto previsto", size=10, color=ft.Colors.GREY_500, italic=True)], spacing=3)
            ], spacing=4),
            bgcolor=ft.Colors.WHITE, padding=6, border_radius=6,
            border=ft.Border(top=ft.BorderSide(1, "#BBDEFB"), bottom=ft.BorderSide(1, "#BBDEFB"), left=ft.BorderSide(4, "#2196F3"), right=ft.BorderSide(1, "#BBDEFB"))
        )

        righe_varie_ui = []
        for vv in voci_varie:
            if vv.get("id_ricetta"):
                righe_varie_ui.append(costruisci_badge_piatto(vv, "#795548", "Dolci"))
            elif vv.get("id_ingrediente"):
                ing_v = vv.get("ingredienti") or {}
                nome_v = ing_v.get("nome") or "Voce"
                q_v = vv.get("quantita") or 0
                u_clean = str(vv.get("unita_misura") or "").replace("|DET", "").replace("|DISPENSA", "").strip()
                q_str = ""

                def del_varie(e, id_v=vv["id"], nome_log=nome_v):
                    log_render(f"[UTENTE] Rimozione voce Varie '{nome_log}' del {d_str_g}")
                    if d_str_g in ram_menu["dizionario_menu"]:
                        ram_menu["dizionario_menu"][d_str_g] = [x for x in ram_menu["dizionario_menu"][d_str_g] if x.get("id") != id_v]
                    disegna_vista_da_ram()
                    if is_valid_uuid(id_v):
                        def w_del_v():
                            supabase.table("menu_giornalieri").delete().eq("id", id_v).execute()
                            sincronizza_e_pulisci_dispensa_mese(dt_giorno)
                            aggiorna_calendario_menu()
                        threading.Thread(target=w_del_v, daemon=True).start()

                righe_varie_ui.append(
                    ft.Row([
                        ft.Text(f"• {nome_v}{q_str}", size=10, weight=ft.FontWeight.BOLD, color="#4E342E", expand=True),
                        ft.GestureDetector(content=ft.Icon(ft.Icons.CLOSE, size=13, color=ft.Colors.RED_700), on_tap=del_varie)
                    ], spacing=2)
                )

        if not righe_varie_ui:
            righe_varie_ui.append(ft.Text("Nessuna voce", size=9, color=ft.Colors.GREY_500, italic=True))

        casella_varie = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Text("📝 VARIE", size=9, weight=ft.FontWeight.BOLD, color="#5D4037", expand=True),
                    ft.Container(
                        content=ft.Text("+ Varie", size=9, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                        bgcolor="#795548", padding=ft.Padding(6, 2, 6, 2), border_radius=8,
                        on_click=lambda e, d_s=d_str_g: apri_popup_aggiungi_voce_diretta(page, d_s, "Varie", f"Varie {dt_giorno.strftime('%d/%m')}", aggiorna_calendario_menu, aggiungi_ottimistico_giorno)
                    )
                ]),
                ft.Column(righe_varie_ui, spacing=3)
            ], spacing=3),
            bgcolor=ft.Colors.WHITE, padding=6, border_radius=6,
            border=ft.Border(top=ft.BorderSide(1, "#D7CCC8"), bottom=ft.BorderSide(1, "#D7CCC8"), left=ft.BorderSide(4, "#795548"), right=ft.BorderSide(1, "#D7CCC8"))
        )

        intestazione_giorno = ft.Container(
            content=ft.Row([
                ft.Text(f"{GIORNI_ESTESI[indice_sett].upper()} {dt_giorno.day}/{dt_giorno.month}", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE if is_oggi else ft.Colors.BLUE_900, expand=True),
                ft.GestureDetector(content=ft.Icon(ft.Icons.LIST_ALT, size=15, color=ft.Colors.WHITE if is_oggi else ft.Colors.BLUE_900), on_tap=lambda e, dt=dt_giorno: apri_giorno_selezionato(dt))
            ]),
            bgcolor=ft.Colors.BLUE_900 if is_oggi else "#DCE4EC", padding=ft.Padding(8, 5, 8, 5), border_radius=6
        )

        return ft.Container(
            content=ft.Column([intestazione_giorno, casella_pranzo, casella_cena, casella_varie], spacing=5),
            expand=1, bgcolor="#F4F6F9", padding=6, border_radius=8,
            border=bordo_campo_uniforme(ft.Colors.BLUE_900 if is_oggi else "#CFD8DC", 2 if is_oggi else 1)
        )

    def crea_card_mensile_tutto_schermo(titolo: str, icona_em: str, categoria_db: str, colore_tema: str, voci_mese: list, data_rif_mese: datetime):
        primo_del_mese_str = data_rif_mese.replace(day=1).strftime("%Y-%m-%d")
        nome_mese = MESI[data_rif_mese.month]
        lista_voci_ui = ft.Column(spacing=4)

        def aggiungi_ottimistico_mese(nuova_v):
            ram_menu["voci_dispensa_mese" if categoria_db == "Dispensa" else "voci_detersivi_mese"].append(nuova_v)
            disegna_vista_da_ram()

        if not voci_mese:
            lista_voci_ui.controls.append(ft.Container(content=ft.Text(f"Nessun prodotto in {titolo} per {nome_mese}. Clicca '+ Aggiungi' a destra.", size=11, color=ft.Colors.GREY_600, italic=True), padding=8))
        else:
            for vm in sorted(voci_mese, key=lambda x: (1 if x.get("preso") else 0, (x.get("ingredienti") or {}).get("nome", ""))):
                nome_p = (vm.get("ingredienti") or {}).get("nome") or "Prodotto"
                q_p = vm.get("quantita") or 0
                u_p = str(vm.get("unita_misura") or "").replace("|DET", "").replace("|DISPENSA", "").strip()
                q_str = f" ({formatta_numero(q_p)} {u_p})" if q_p else ""
                is_preso = bool(vm.get("preso"))

                def cambia_spunta_mese(e, voce_ref=vm, np_log=nome_p):
                    voce_ref["preso"] = bool(e.control.value)
                    log_render(f"[UTENTE] Spunta '{categoria_db}' per '{np_log}': preso={voce_ref['preso']}")
                    disegna_vista_da_ram()
                    threading.Thread(target=lambda: aggiorna_flag_preso_db(voce_ref["id"], voce_ref["preso"]), daemon=True).start()

                def elimina_voce_mese(e, id_riga=vm["id"], id_ing_r=vm.get("id_ingrediente"), np_log=nome_p):
                    log_render(f"[UTENTE] Eliminazione voce '{np_log}' da card '{categoria_db}' ({nome_mese})")
                    chiave_ram = "voci_dispensa_mese" if categoria_db == "Dispensa" else "voci_detersivi_mese"
                    ram_menu[chiave_ram] = [x for x in ram_menu[chiave_ram] if x.get("id") != id_riga]
                    disegna_vista_da_ram()
                    if is_valid_uuid(id_riga):
                        def w_del_m():
                            if categoria_db == "Dispensa":
                                imposta_stato_dispensa_ingrediente_db(np_log, id_ing_r or "", False, data_rif_mese)
                            else:
                                supabase.table("menu_giornalieri").delete().eq("id", id_riga).execute()
                            aggiorna_calendario_menu()
                        threading.Thread(target=w_del_m, daemon=True).start()

                lista_voci_ui.controls.append(
                    ft.Container(
                        content=ft.Row([
                            ft.Checkbox(value=is_preso, on_change=cambia_spunta_mese),
                            ft.Text(f"{'✓ Presente: ' if is_preso else ''}{nome_p}{q_str}", size=12, weight=ft.FontWeight.NORMAL if is_preso else ft.FontWeight.BOLD, color=ft.Colors.GREY_600 if is_preso else ft.Colors.BLACK, italic=is_preso, expand=True),
                            ft.GestureDetector(content=ft.Icon(ft.Icons.CLOSE, size=16, color=ft.Colors.RED_700), on_tap=elimina_voce_mese)
                        ], spacing=4),
                        bgcolor="#EEEEEE" if is_preso else ft.Colors.WHITE, padding=ft.Padding(6, 2, 10, 2), border_radius=6,
                        border=bordo_campo_uniforme("#E0E0E0" if is_preso else colore_tema)
                    )
                )

        tot_presenti = sum(1 for x in voci_mese if x.get("preso"))
        barra_titolo = ft.Container(
            content=ft.Row([
                ft.Text(f"{icona_em} {titolo.upper()} ({nome_mese.upper()}) — Da prendere: {len(voci_mese) - tot_presenti} | Presenti: {tot_presenti}", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE, expand=True),
                ft.Container(
                    content=ft.Text("+ Aggiungi", size=10, color=colore_tema, weight=ft.FontWeight.BOLD),
                    bgcolor=ft.Colors.WHITE, padding=ft.Padding(10, 4, 10, 4), border_radius=8,
                    on_click=lambda e: apri_popup_aggiungi_voce_diretta(page, primo_del_mese_str, categoria_db, f"{titolo} ({nome_mese})", aggiorna_calendario_menu, aggiungi_ottimistico_mese)
                )
            ], spacing=6),
            bgcolor=colore_tema, padding=ft.Padding(10, 6, 10, 6), border_radius=6
        )

        return ft.Row([
            ft.Container(content=ft.Column([barra_titolo, lista_voci_ui], spacing=6), expand=1, bgcolor="#F4F6F9", padding=8, border_radius=8, border=bordo_campo_uniforme(colore_tema, 1.5))
        ], spacing=6)

    def disegna_vista_da_ram():
        vista, data, is_semplificata = stato["vista"], stato["data"], stato["semplificata"]

        for b, nome_v in [(btn_giorno, "Giorno"), (btn_settimana, "Settimana"), (btn_mese, "Mese")]:
            b.bgcolor = ft.Colors.BLUE_900 if vista == nome_v else ft.Colors.GREY_200
            b.content.color = ft.Colors.WHITE if vista == nome_v else ft.Colors.BLACK

        layer_semplificata.visible = is_semplificata
        layer_mese.visible = (not is_semplificata and vista == "Mese")
        layer_settimana.visible = (not is_semplificata and vista == "Settimana")
        layer_giorno.visible = (not is_semplificata and vista == "Giorno")

        dizionario_menu = ram_menu["dizionario_menu"]

        if is_semplificata:
            lunedi = data - timedelta(days=data.weekday())
            domenica = lunedi + timedelta(days=6)
            testo_header.value = f"{lunedi.day} - {domenica.day} {MESI[lunedi.month][:3]} {lunedi.year}" if lunedi.month == domenica.month else f"{lunedi.day} {MESI[lunedi.month][:3]} - {domenica.day} {MESI[domenica.month][:3]} {domenica.year}"
            layer_semplificata.controls.clear()

            cards_g = [crea_card_giorno_semplificata(lunedi + timedelta(days=i), i, dizionario_menu.get((lunedi + timedelta(days=i)).strftime("%Y-%m-%d"), [])) for i in range(7)]
            layer_semplificata.controls.extend([
                ft.Row([cards_g[0], cards_g[1], cards_g[2]], spacing=6, vertical_alignment=ft.CrossAxisAlignment.START),
                ft.Row([cards_g[3], cards_g[4], cards_g[5]], spacing=6, vertical_alignment=ft.CrossAxisAlignment.START),
                ft.Row([cards_g[6]], spacing=6, vertical_alignment=ft.CrossAxisAlignment.START),
                ft.Divider(height=6, color="#CFD8DC"),
                crea_card_mensile_tutto_schermo("Dispensa", "📦", "Dispensa", "#00897B", ram_menu["voci_dispensa_mese"], data),
                crea_card_mensile_tutto_schermo("Detersivi e Varie", "🧼", "Detersivi", "#546E7A", ram_menu["voci_detersivi_mese"], data)
            ])

        elif vista == "Mese":
            testo_header.value = f"{MESI[data.month]} {data.year}"
            layer_mese.controls.clear()
            griglia = ft.Column(spacing=0)
            for settimana in calendar.monthcalendar(data.year, data.month):
                riga_ui = ft.Row(spacing=0)
                for giorno in settimana:
                    if giorno == 0:
                        riga_ui.controls.append(ft.Container(expand=1, height=90, border=bordo_standard()))
                    else:
                        is_oggi = (giorno == oggi.day and data.month == oggi.month and data.year == oggi.year)
                        data_giorno = data.replace(day=giorno)
                        voci_giorno = dizionario_menu.get(data_giorno.strftime("%Y-%m-%d"), [])
                        cerchio = ft.Container(
                            content=ft.Text(str(giorno), color=ft.Colors.WHITE if is_oggi else ft.Colors.BLACK, size=12, weight=ft.FontWeight.BOLD if is_oggi else ft.FontWeight.NORMAL),
                            bgcolor=ft.Colors.BLUE_900 if is_oggi else ft.Colors.TRANSPARENT, border_radius=15, width=24, height=24, alignment=ft.Alignment(0, 0)
                        )
                        col_badge = ft.Column(spacing=1, expand=True)
                        for sup in SUPERCATEGORIE:
                            v_sup = [x for x in voci_giorno if x["categoria"] == sup]
                            if v_sup and len(col_badge.controls) < 2:
                                vr = next((x for x in v_sup if x.get("ricette")), v_sup[0])
                                dett = f": {vr['ricette']['nome']}" if vr.get("ricette") else (f": {vr['ingredienti']['nome']}" if vr.get("ingredienti") else "")
                                is_w = bool(vr.get("ricette")) and not ricetta_ha_ingredienti(vr.get("id_ricetta"))
                                col_badge.controls.append(
                                    ft.Container(
                                        content=ft.Text(f"{'⚠️ ' if is_w else ''}{sup[:3]}{dett}", size=7, color=ft.Colors.BLACK if is_w else ft.Colors.WHITE, weight=ft.FontWeight.BOLD, no_wrap=True),
                                        bgcolor="#FFF59D" if is_w else COLORI_PASTI.get(sup, ft.Colors.BLUE_900), padding=2, border_radius=2, height=18
                                    )
                                )
                        riga_ui.controls.append(
                            ft.Container(
                                content=ft.Column([ft.Container(content=cerchio, alignment=ft.Alignment(0, 0)), col_badge], spacing=2, horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
                                expand=1, height=90, border=bordo_standard(), padding=2, on_click=lambda e, dt=data_giorno: apri_giorno_selezionato(dt)
                            )
                        )
                griglia.controls.append(riga_ui)
            layer_mese.controls.extend([
                ft.Row([ft.Container(content=ft.Text(g, size=9, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_700), expand=1, alignment=ft.Alignment(0, 0), border=bordo_standard(), padding=2) for g in GIORNI], spacing=0),
                griglia
            ])

        elif vista == "Settimana":
            lunedi = data - timedelta(days=data.weekday())
            domenica = lunedi + timedelta(days=6)
            testo_header.value = f"{lunedi.day} - {domenica.day} {MESI[lunedi.month][:3]} {lunedi.year}"
            layer_settimana.controls.clear()
            r1, r2 = ft.Row(spacing=0), ft.Row(spacing=0)
            for i in range(7):
                gc = lunedi + timedelta(days=i)
                voci_g = dizionario_menu.get(gc.strftime("%Y-%m-%d"), [])
                col_p = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO)
                for sup in SUPERCATEGORIE:
                    vs = [x for x in voci_g if x["categoria"] == sup]
                    if vs:
                        tv = [f"• {(x.get('ricette') or x.get('ingredienti') or {}).get('nome', '')}" for x in vs]
                        col_p.controls.append(ft.Container(content=ft.Column([ft.Text(sup.upper(), size=8, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE)] + [ft.Text(t, size=8, color=ft.Colors.WHITE) for t in tv], spacing=1), bgcolor=COLORI_PASTI.get(sup, ft.Colors.BLUE_900), padding=4, border_radius=4))
                box_g = ft.Container(
                    content=ft.Column([ft.Text(f"{GIORNI[i]} {gc.day}", size=10, weight=ft.FontWeight.BOLD), ft.Divider(height=1), ft.Container(content=col_p, expand=True)], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                    expand=1, height=280, border=bordo_standard(), padding=3, on_click=lambda e, dt=gc: apri_giorno_selezionato(dt)
                )
                (r1 if i < 4 else r2).controls.append(box_g)
            r2.controls.append(ft.Container(expand=1))
            layer_settimana.controls.extend([r1, ft.Container(height=10), r2])

        elif vista == "Giorno":
            testo_header.value = f"{data.day} {MESI[data.month]} {data.year}"
            layer_giorno.controls.clear()
            voci_g = dizionario_menu.get(data.strftime("%Y-%m-%d"), [])
            layer_giorno.controls.append(
                ft.Container(
                    content=ft.Row([
                        ft.Text(f"{GIORNI[data.weekday()]} {data.day}", size=20, weight=ft.FontWeight.BOLD),
                        ft.Container(content=ft.Text("✏️ Compila Menu del Giorno", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=12), bgcolor=ft.Colors.BLUE_900, padding=10, border_radius=6, on_click=lambda e: apri_giorno_selezionato(data))
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    padding=10, border=bordo_standard()
                )
            )
            for sup in SUPERCATEGORIE:
                vs = [x for x in voci_g if x["categoria"] == sup]
                if vs:
                    righe_p = [ft.Text(f"🍽️ {sup.upper()}", size=13, weight=ft.FontWeight.BOLD, color=COLORI_PASTI.get(sup))]
                    for x in vs:
                        if x.get("ricette"):
                            righe_p.append(ft.Text(f"  🍲 {x['ricette']['nome']}", size=12, weight=ft.FontWeight.BOLD))
                        elif x.get("ingredienti"):
                            u_c = str(x.get("unita_misura") or "").replace("|DET", "").replace("|DISPENSA", "").strip()
                            righe_p.append(ft.Text(f"  • {x['ingredienti']['nome']} ({formatta_numero(x.get('quantita') or 0)} {u_c})", size=12))
                    layer_giorno.controls.append(ft.Container(content=ft.Column(righe_p, spacing=2), bgcolor=ft.Colors.WHITE, padding=8, border_radius=8, border=bordo_standard(), on_click=lambda e: apri_giorno_selezionato(data)))

        try:
            page.update()
        except Exception:
            pass

    def aggiorna_calendario_menu():
        data = stato["data"]
        ULTIMA_DATA_MENU_ATTIVA[0] = data
        badge_loading_cal.visible = True
        try:
            page.update()
        except Exception:
            pass

        def worker_calendario():
            if not supabase:
                badge_loading_cal.visible = False
                disegna_vista_da_ram()
                return

            try:
                log_render(f"[CALENDARIO MENU] Aggiornamento calendario e Dispensa per data riferimento {data.strftime('%d/%m/%Y')}")
                assicura_cache_anagrafica(forza_ricarica=False)
                sincronizza_e_pulisci_dispensa_mese(data)
                data_rif = data.replace(day=1)
                inizio_f = (data_rif - timedelta(days=10)).strftime("%Y-%m-%d")
                fine_f = (data_rif + timedelta(days=42)).strftime("%Y-%m-%d")
                primo_mese_corr = data.replace(day=1).strftime("%Y-%m-%d")
                ultimo_mese_corr = data.replace(day=calendar.monthrange(data.year, data.month)[1]).strftime("%Y-%m-%d")

                res = supabase.table("menu_giornalieri").select(
                    "*, ricette(id, nome, portata_default), ingredienti(id, nome, unita_default)"
                ).gte("data_menu", inizio_f).lte("data_menu", fine_f).execute()

                dizionario_menu, voci_dispensa_mese, voci_detersivi_mese = {}, [], []
                visti_disp, visti_det = set(), set()
                ids_ric_vis = set()

                for riga in (res.data or []):
                    cat_r, d_str = riga.get("categoria"), riga["data_menu"]
                    if is_valid_uuid(riga.get("id_ricetta")):
                        ids_ric_vis.add(riga["id_ricetta"])
                    if cat_r == "Dispensa" and primo_mese_corr <= d_str <= ultimo_mese_corr:
                        k_disp = str(riga.get("id_ingrediente") or riga.get("id"))
                        if k_disp not in visti_disp:
                            visti_disp.add(k_disp)
                            voci_dispensa_mese.append(riga)
                    elif cat_r == "Detersivi" and primo_mese_corr <= d_str <= ultimo_mese_corr:
                        if riga.get("id_ingrediente"):
                            CACHE_ANAGRAFICA["ids_detersivi"].add(riga["id_ingrediente"])
                        k_det = str(riga.get("id_ingrediente") or riga.get("id"))
                        if k_det not in visti_det:
                            visti_det.add(k_det)
                            voci_detersivi_mese.append(riga)
                    elif cat_r not in ["Dispensa", "Detersivi"]:
                        dizionario_menu.setdefault(d_str, []).append(riga)

                for d_k, lista_old in ram_menu["dizionario_menu"].items():
                    for item_old in lista_old:
                        if item_old.get("in_salvataggio"):
                            nome_old = (item_old.get("ricette") or {}).get("nome", "").lower()
                            gia_su_db = any(
                                (x.get("ricette") or {}).get("nome", "").lower() == nome_old
                                for x in dizionario_menu.get(d_k, [])
                            )
                            if not gia_su_db:
                                dizionario_menu.setdefault(d_k, []).append(item_old)

                if ids_ric_vis:
                    aggiorna_conteggio_ricette_visibili(list(ids_ric_vis))

                ram_menu["dizionario_menu"] = dizionario_menu
                ram_menu["voci_dispensa_mese"] = voci_dispensa_mese
                ram_menu["voci_detersivi_mese"] = voci_detersivi_mese
                log_render(f"[CALENDARIO MENU] Caricati {len(dizionario_menu)} giorni con pasti, {len(voci_dispensa_mese)} voci Dispensa, {len(voci_detersivi_mese)} voci Detersivi.")
            except Exception as ex:
                log_render(f"[ERRORE] Caricamento calendario menu: {ex}")
            finally:
                badge_loading_cal.visible = False
                disegna_vista_da_ram()

        threading.Thread(target=worker_calendario, daemon=True).start()

    page.aggiorna_vista_menu = aggiorna_calendario_menu

    header = ft.Row(
        controls=[
            ft.Row([ft.IconButton(ft.Icons.CHEVRON_LEFT, on_click=nav_prec, icon_size=20), ft.IconButton(ft.Icons.CHEVRON_RIGHT, on_click=nav_succ, icon_size=20), testo_header, badge_loading_cal], spacing=4),
            ft.Row([box_switch_semplificata, box_bottoni_vista], spacing=8, wrap=True)
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN, wrap=True, run_spacing=8
    )

    btn_fai_la_spesa = ft.Container(
        content=ft.Row([ft.Icon(ft.Icons.SHOPPING_CART, color=ft.Colors.WHITE, size=20), ft.Text("FAI LA SPESA (Somma Ingredienti & Dispensa)", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=13)], alignment=ft.MainAxisAlignment.CENTER, spacing=8),
        bgcolor=ft.Colors.GREEN_700, padding=12, border_radius=8,
        on_click=lambda e: apri_popup_fai_la_spesa(page, stato["data"], aggiorna_calendario_menu)
    )

    aggiorna_calendario_menu()

    return ft.Container(
        content=ft.Column([header, area_dinamica, ft.Divider(height=10, color="#EEEEEE"), btn_fai_la_spesa], spacing=10, scroll=ft.ScrollMode.AUTO),
        padding=5, expand=True
    )
