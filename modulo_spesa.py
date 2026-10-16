import flet as ft
import calendar
import threading
from datetime import datetime, timedelta
from modulo_ricette import (
    supabase,
    is_valid_uuid,
    apri_dialog_sicuro,
    chiudi_dialog_sicuro,
    formatta_numero,
    bordo_campo_uniforme,
    aggiorna_flag_preso_db
)


# ==========================================================
# MOTORE INTELLIGENTE "FAI LA SPESA"
# ==========================================================
def apri_popup_fai_la_spesa(page: ft.Page, data_riferimento: datetime, on_close_callback=None):
    if not supabase:
        return

    oggi_dt = datetime.now()
    spuntati_sessione = set()
    stato_filtro = {"scelta": "Da oggi a fine mese"}
    modifiche_spunte = [False]

    spinner_spesa = ft.ProgressRing(width=18, height=18, stroke_width=2.5, color=ft.Colors.GREEN_800, visible=True)
    colonna_lista = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)
    txt_periodo_date = ft.Text("", size=11, color=ft.Colors.BLUE_900, weight=ft.FontWeight.BOLD)
    txt_riepilogo = ft.Text("", size=11, color=ft.Colors.GREY_700, weight=ft.FontWeight.BOLD)

    opzioni_filtro = [
        "Da oggi a fine mese",
        "Prossimi 7 giorni",
        "Solo giorno sel.",
        "Tutto il mese"
    ]
    riga_bottoni_filtro = ft.Row(wrap=True, spacing=4, run_spacing=4)

    def cambia_filtro_spesa(nuova_scelta):
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
            if scelta == "Da oggi a fine mese":
                dt_i = oggi_dt
                dt_f = oggi_dt.replace(day=calendar.monthrange(oggi_dt.year, oggi_dt.month)[1])
            elif scelta == "Prossimi 7 giorni":
                dt_i = oggi_dt
                dt_f = oggi_dt + timedelta(days=6)
            elif scelta == "Solo giorno sel.":
                dt_i = data_riferimento
                dt_f = data_riferimento
            else:
                dt_i = data_riferimento.replace(day=1)
                dt_f = data_riferimento.replace(day=calendar.monthrange(data_riferimento.year, data_riferimento.month)[1])

            inizio_str = dt_i.strftime("%Y-%m-%d")
            fine_str = dt_f.strftime("%Y-%m-%d")
            primo_mese_str = data_riferimento.replace(day=1).strftime("%Y-%m-%d")
            fine_mese_str = data_riferimento.replace(day=calendar.monthrange(data_riferimento.year, data_riferimento.month)[1]).strftime("%Y-%m-%d")
            txt_periodo_date.value = f"📅 Intervallo: {dt_i.strftime('%d/%m/%Y')} → {dt_f.strftime('%d/%m/%Y')}"

            try:
                res_m = supabase.table("menu_giornalieri").select(
                    "*, ingredienti(id, nome, unita_default), ricette(id, nome)"
                ).gte("data_menu", min(inizio_str, primo_mese_str)).lte("data_menu", max(fine_str, fine_mese_str)).execute()

                righe_valide = [
                    r for r in (res_m.data or [])
                    if (r.get("categoria") in ["Dispensa", "Detersivi"] and primo_mese_str <= r.get("data_menu", "") <= fine_mese_str)
                    or (r.get("categoria") not in ["Dispensa", "Detersivi"] and inizio_str <= r.get("data_menu", "") <= fine_str)
                ]

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

                def aggiungi_alla_spesa(ing_obj, qta_ing, unita_ing, id_menu_rif="", cat_riga="", preso_riga=False):
                    if not ing_obj:
                        return
                    if isinstance(ing_obj, str):
                        nome_ing = ing_obj
                        u_def_db = ""
                    else:
                        nome_ing = ing_obj.get("nome", "Prodotto")
                        u_def_db = str(ing_obj.get("unita_default") or "")

                    is_base_dinamico = ("|DISPENSA" in u_def_db) or (cat_riga == "Dispensa")
                    n_low = nome_ing.strip().lower()

                    if is_base_dinamico:
                        chiave = (n_low, "promemoria")
                        somme.setdefault(chiave, {
                            "nome": nome_ing.capitalize(), "qta": 1.0, "unita": "pz",
                            "ids_menu_diretti": [], "preso_db": False, "etichetta_extra": "[Dispensa]"
                        })
                        if id_menu_rif:
                            somme[chiave]["ids_menu_diretti"].append(id_menu_rif)
                    else:
                        q_norm, u_base = normalizza_qta_unita(qta_ing, unita_ing)
                        chiave = (n_low, u_base)
                        somme.setdefault(chiave, {
                            "nome": nome_ing.capitalize(), "qta": 0.0, "unita": u_base,
                            "ids_menu_diretti": [], "preso_db": False,
                            "etichetta_extra": "[Detersivi]" if cat_riga == "Detersivi" else ""
                        })
                        somme[chiave]["qta"] += q_norm
                        if id_menu_rif:
                            somme[chiave]["ids_menu_diretti"].append(id_menu_rif)
                        if cat_riga == "Detersivi":
                            somme[chiave]["etichetta_extra"] = "[Detersivi]"

                    if preso_riga:
                        somme[chiave]["preso_db"] = True

                for m in righe_valide:
                    cat = m.get("categoria") or ""
                    is_preso_db = bool(m.get("preso"))
                    if m.get("id_ingrediente") and m.get("ingredienti"):
                        aggiungi_alla_spesa(m["ingredienti"], m.get("quantita") or 0, m.get("unita_misura"), m["id"], cat, is_preso_db)
                    elif m.get("id_ricetta"):
                        for ri in mappa_ri.get(m["id_ricetta"], []):
                            if ri.get("ingredienti"):
                                aggiungi_alla_spesa(ri["ingredienti"], ri.get("quantita") or 0, ri.get("unita_misura"), "", cat, is_preso_db)

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
                spinner_spesa.visible = False
                disegna_lista_spesa()
            except Exception as ex:
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
            (spuntati_sessione.add if nuovo_stato else spuntati_sessione.discard)(key_ing)
            modifiche_spunte[0] = True
            ids_dir = item_obj.get("ids_menu_diretti") or []
            if ids_dir:
                threading.Thread(target=lambda: [aggiorna_flag_preso_db(mid, nuovo_stato) for mid in ids_dir], daemon=True).start()
            disegna_lista_spesa()

        for k_id, item in da_comprare:
            q_finale = formatta_qta_finale(item["qta"], item["unita"])
            lbl_extra = f" {item['etichetta_extra']}" if item.get("etichetta_extra") else ""
            colonna_lista.controls.append(
                ft.Container(
                    content=ft.Row([
                        ft.Checkbox(value=False, on_change=lambda e, k=k_id, it=item: toggle_spunta(e, k, it)),
                        ft.Text(f"{item['nome']}{lbl_extra}", size=13, weight=ft.FontWeight.BOLD, expand=True),
                        ft.Container(
                            content=ft.Text(q_finale, size=12, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                            bgcolor=ft.Colors.BLUE_900, padding=ft.Padding(8, 3, 8, 3), border_radius=8
                        )
                    ], spacing=4),
                    bgcolor="#F5F7FA", padding=ft.Padding(4, 2, 8, 2), border_radius=6,
                    border=bordo_campo_uniforme("#E0E0E0")
                )
            )
        if gia_in_dispensa:
            colonna_lista.controls.append(ft.Divider(height=8, color="#E0E0E0"))
            for k_id, item in gia_in_dispensa:
                q_finale = formatta_qta_finale(item["qta"], item["unita"])
                colonna_lista.controls.append(
                    ft.Container(
                        content=ft.Row([
                            ft.Checkbox(value=True, on_change=lambda e, k=k_id, it=item: toggle_spunta(e, k, it)),
                            ft.Text(f"✓ {item['nome']}", size=12, color=ft.Colors.GREY_600, italic=True, expand=True),
                            ft.Text(q_finale, size=11, color=ft.Colors.GREY_600)
                        ], spacing=4),
                        bgcolor="#EEEEEE", padding=ft.Padding(4, 1, 8, 1), border_radius=6
                    )
                )
        page.update()

    avvia_calcolo_spesa()
