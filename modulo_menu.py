import flet as ft
import calendar
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    supabase = None
    print(f"Errore connessione Supabase in modulo_menu: {e}")

MESI = ["", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]
GIORNI = ["LUN", "MAR", "MER", "GIO", "VEN", "SAB", "DOM"]

SUPERCATEGORIE = ["Colazione", "Pranzo", "Cena", "Merende", "Varie"]
SUBCATEGORIE_PASTO = ["Antipasto", "Primo", "Secondo", "Frutta", "Dolci", "Bevande"]
SUB_CON_RICETTA = ["Primo", "Secondo", "Dolci"]
UNITA_MISURA = ["g", "kg", "ml", "l", "pz", "fette", "cucchiai", "tazze", "q.b."]

COLORI_PASTI = {
    "Colazione": "#FF9800",
    "Pranzo": "#4CAF50",
    "Cena": "#2196F3",
    "Merende": "#9C27B0",
    "Varie": "#795548"
}


def apri_form_menu_giorno(page: ft.Page, data_selezionata: datetime, on_success_callback=None):
    if not supabase:
        return

    data_str = data_selezionata.strftime("%Y-%m-%d")
    data_vis = data_selezionata.strftime("%d/%m/%Y")

    lista_ingredienti = []
    lista_ricette = []
    menu_del_giorno = []
    mappa_ricette_ing = {}

    cat_aperte = set()
    sub_aperte = set()

    def carica_dati_db(inizializza_aperture=False):
        nonlocal lista_ingredienti, lista_ricette, menu_del_giorno, mappa_ricette_ing
        try:
            res_ing = supabase.table("ingredienti").select("*").order("nome").execute()
            lista_ingredienti = res_ing.data if res_ing.data else []

            res_ric = supabase.table("ricette").select("*").order("nome").execute()
            lista_ricette = res_ric.data if res_ric.data else []

            res_menu = supabase.table("menu_giornalieri").select(
                "*, ricette(id, nome), ingredienti(id, nome, unita_default)"
            ).eq("data_menu", data_str).execute()
            menu_del_giorno = res_menu.data if res_menu.data else []

            # Carica gli ingredienti associati alle ricette presenti nel giorno
            ids_ricette_giorno = list({m["id_ricetta"] for m in menu_del_giorno if m.get("id_ricetta")})
            mappa_ricette_ing.clear()
            if ids_ricette_giorno:
                res_ri = supabase.table("ricette_ingredienti").select(
                    "*, ingredienti(id, nome, unita_default)"
                ).in_("id_ricetta", ids_ricette_giorno).execute()
                if res_ri.data:
                    for riga in res_ri.data:
                        rid = riga["id_ricetta"]
                        if rid not in mappa_ricette_ing:
                            mappa_ricette_ing[rid] = []
                        mappa_ricette_ing[rid].append(riga)

            if inizializza_aperture:
                for m in menu_del_giorno:
                    cat_aperte.add(m["categoria"])
                    if m.get("subcategoria"):
                        sub_aperte.add((m["categoria"], m["subcategoria"]))
        except Exception as ex:
            print("Errore caricamento dati menu:", ex)

    carica_dati_db(inizializza_aperture=True)

    colonna_contenuto = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, tight=True)

    def parse_qta(val_str):
        if not val_str:
            return 0
        try:
            return float(str(val_str).replace(",", ".").strip())
        except ValueError:
            return 0

    def ottieni_o_crea_ingrediente(nome_raw, unita_scelta):
        nome_pulito = nome_raw.strip().capitalize()
        esistente = next((i for i in lista_ingredienti if i["nome"].lower() == nome_pulito.lower()), None)
        if esistente:
            return esistente["id"]
        res = supabase.table("ingredienti").insert({
            "nome": nome_pulito,
            "unita_default": unita_scelta or "g"
        }).execute()
        nuovo = res.data[0]
        lista_ingredienti.append(nuovo)
        return nuovo["id"]

    def ottieni_o_crea_ricetta(nome_raw, portata):
        nome_pulito = nome_raw.strip().capitalize()
        esistente = next((r for r in lista_ricette if r["nome"].lower() == nome_pulito.lower()), None)
        if esistente:
            return esistente["id"]
        res = supabase.table("ricette").insert({
            "nome": nome_pulito,
            "portata_default": portata
        }).execute()
        nuova = res.data[0]
        lista_ricette.append(nuova)
        return nuova["id"]

    # ---------------------------------------------------------
    # BLOCCO INGREDIENTI DIRETTI (Colazione, Antipasto, Frutta, Bevande, Merende, Varie)
    # ---------------------------------------------------------
    def crea_sezione_ingredienti_diretti(categoria, subcategoria=None):
        voci = [
            m for m in menu_del_giorno
            if m["categoria"] == categoria and m.get("subcategoria") == subcategoria and m.get("id_ingrediente")
        ]

        lista_ui = ft.Column(spacing=4)
        for v in voci:
            ing_obj = v.get("ingredienti") or {}
            nome_ing = ing_obj.get("nome", "Ingrediente")
            qta = v.get("quantita") or 0
            qta_str = f"{int(qta) if float(qta).is_integer() else qta} {v.get('unita_misura') or ''}".strip()

            def elimina_voce(e, id_voce=v["id"]):
                supabase.table("menu_giornalieri").delete().eq("id", id_voce).execute()
                ricostruisci_form(notifica=True)

            lista_ui.controls.append(
                ft.Container(
                    content=ft.Row([
                        ft.Text(f"• {nome_ing}", size=12, weight=ft.FontWeight.BOLD, expand=True),
                        ft.Text(qta_str, size=12, color=ft.Colors.BLUE_900, weight=ft.FontWeight.BOLD),
                        ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_color=ft.Colors.RED_700, icon_size=16, on_click=elimina_voce)
                    ], spacing=5),
                    bgcolor="#F5F5F5", padding=ft.Padding(8, 2, 4, 2), border_radius=5
                )
            )

        txt_ing = ft.TextField(label="Ingrediente (cerca o nuovo)", dense=True, text_size=12, expand=2)
        txt_qta = ft.TextField(label="Q.tà", value="1", dense=True, text_size=12, width=60)
        dd_unita = ft.Dropdown(
            label="Unità", value="pz", dense=True, width=75,
            options=[ft.dropdown.Option(u) for u in UNITA_MISURA]
        )
        riga_suggerimenti = ft.Row(wrap=True, spacing=4, visible=False)

        def seleziona_suggerimento_ing(ing_scelto):
            txt_ing.value = ing_scelto["nome"]
            if ing_scelto.get("unita_default") in UNITA_MISURA:
                dd_unita.value = ing_scelto["unita_default"]
            riga_suggerimenti.visible = False
            page.update()

        def filtra_ing(e):
            testo = (txt_ing.value or "").strip().lower()
            riga_suggerimenti.controls.clear()
            if len(testo) >= 1:
                matches = [i for i in lista_ingredienti if testo in i["nome"].lower()][:6]
                for m_ing in matches:
                    riga_suggerimenti.controls.append(
                        ft.Container(
                            content=ft.Text(f"🔍 {m_ing['nome']}", size=11, color=ft.Colors.BLUE_900, weight=ft.FontWeight.BOLD),
                            bgcolor=ft.Colors.BLUE_50, padding=5, border_radius=10,
                            on_click=lambda ev, item=m_ing: seleziona_suggerimento_ing(item)
                        )
                    )
                riga_suggerimenti.visible = len(matches) > 0
            else:
                riga_suggerimenti.visible = False
            page.update()

        txt_ing.on_change = filtra_ing

        def aggiungi_ing_diretto(e):
            if not txt_ing.value or not txt_ing.value.strip():
                return
            id_ing = ottieni_o_crea_ingrediente(txt_ing.value, dd_unita.value)
            supabase.table("menu_giornalieri").insert({
                "data_menu": data_str,
                "categoria": categoria,
                "subcategoria": subcategoria,
                "id_ingrediente": id_ing,
                "quantita": parse_qta(txt_qta.value),
                "unita_misura": dd_unita.value
            }).execute()
            ricostruisci_form(notifica=True)

        btn_add = ft.IconButton(icon=ft.Icons.ADD_CIRCLE, icon_color=ft.Colors.BLUE_900, on_click=aggiungi_ing_diretto)

        return ft.Column([
            lista_ui,
            ft.Row([txt_ing, txt_qta, dd_unita, btn_add], spacing=4),
            riga_suggerimenti
        ], spacing=4)

    # ---------------------------------------------------------
    # BLOCCO RICETTA + INGREDIENTI RELAZIONALI (Primo, Secondo, Dolci)
    # ---------------------------------------------------------
    def crea_sezione_ricetta(categoria, subcategoria):
        voci_ricetta = [
            m for m in menu_del_giorno
            if m["categoria"] == categoria and m.get("subcategoria") == subcategoria and m.get("id_ricetta")
        ]

        blocchi_ricette_ui = ft.Column(spacing=8)

        for v in voci_ricetta:
            ric_obj = v.get("ricette") or {}
            id_ric = v["id_ricetta"]
            nome_ric = ric_obj.get("nome", "Ricetta")
            ingredienti_associati = mappa_ricette_ing.get(id_ric, [])

            col_ing_ricetta = ft.Column(spacing=4)
            for ri in ingredienti_associati:
                ing_info = ri.get("ingredienti") or {}
                nome_i = ing_info.get("nome", "Ingrediente")
                qta_val = ri.get("quantita") or 0
                qta_init = str(int(qta_val) if float(qta_val).is_integer() else qta_val)
                unita_init = ri.get("unita_misura") or "g"

                tf_qta_ri = ft.TextField(value=qta_init, dense=True, width=60, text_size=11)
                dd_uni_ri = ft.Dropdown(
                    value=unita_init if unita_init in UNITA_MISURA else "g",
                    dense=True, width=70, options=[ft.dropdown.Option(u) for u in UNITA_MISURA]
                )

                def aggiorna_qta_ri(e, id_riga_ri=ri["id"], campo_q=tf_qta_ri, campo_u=dd_uni_ri):
                    supabase.table("ricette_ingredienti").update({
                        "quantita": parse_qta(campo_q.value),
                        "unita_misura": campo_u.value
                    }).eq("id", id_riga_ri).execute()
                    ricostruisci_form(notifica=True)

                def elimina_ing_da_ricetta(e, id_riga_ri=ri["id"]):
                    supabase.table("ricette_ingredienti").delete().eq("id", id_riga_ri).execute()
                    ricostruisci_form(notifica=True)

                col_ing_ricetta.controls.append(
                    ft.Row([
                        ft.Text(f"▪ {nome_i}", size=11, expand=True),
                        tf_qta_ri,
                        dd_uni_ri,
                        ft.IconButton(icon=ft.Icons.SAVE, icon_color=ft.Colors.GREEN_700, icon_size=16, tooltip="Aggiorna quantità", on_click=aggiorna_qta_ri),
                        ft.IconButton(icon=ft.Icons.CLOSE, icon_color=ft.Colors.RED_700, icon_size=16, tooltip="Rimuovi ingrediente", on_click=elimina_ing_da_ricetta)
                    ], spacing=3)
                )

            # Form per aggiungere un ingrediente alla ricetta corrente (con completamento automatico)
            txt_nuovo_ing_r = ft.TextField(label="Aggiungi ingrediente alla ricetta...", dense=True, text_size=11, expand=2)
            txt_nuova_qta_r = ft.TextField(label="Q.tà", value="100", dense=True, text_size=11, width=60)
            dd_nuova_uni_r = ft.Dropdown(
                label="Unità", value="g", dense=True, width=70,
                options=[ft.dropdown.Option(u) for u in UNITA_MISURA]
            )
            riga_sugg_ing_r = ft.Row(wrap=True, spacing=4, visible=False)

            def sel_sugg_r(ing_scelto, tf_i=txt_nuovo_ing_r, dd_u=dd_nuova_uni_r, r_sugg=riga_sugg_ing_r):
                tf_i.value = ing_scelto["nome"]
                if ing_scelto.get("unita_default") in UNITA_MISURA:
                    dd_u.value = ing_scelto["unita_default"]
                r_sugg.visible = False
                page.update()

            def filtra_ing_r(e, tf_i=txt_nuovo_ing_r, dd_u=dd_nuova_uni_r, r_sugg=riga_sugg_ing_r):
                testo = (tf_i.value or "").strip().lower()
                r_sugg.controls.clear()
                if len(testo) >= 1:
                    matches = [i for i in lista_ingredienti if testo in i["nome"].lower()][:6]
                    for m_ing in matches:
                        r_sugg.controls.append(
                            ft.Container(
                                content=ft.Text(f"🔍 {m_ing['nome']}", size=10, color=ft.Colors.BLUE_900, weight=ft.FontWeight.BOLD),
                                bgcolor=ft.Colors.BLUE_50, padding=4, border_radius=8,
                                on_click=lambda ev, item=m_ing: sel_sugg_r(item, tf_i, dd_u, r_sugg)
                            )
                        )
                    r_sugg.visible = len(matches) > 0
                else:
                    r_sugg.visible = False
                page.update()

            txt_nuovo_ing_r.on_change = filtra_ing_r

            def salva_ing_in_ricetta(e, id_ricetta_corr=id_ric, tf_i=txt_nuovo_ing_r, tf_q=txt_nuova_qta_r, dd_u=dd_nuova_uni_r):
                if not tf_i.value or not tf_i.value.strip():
                    return
                id_ing = ottieni_o_crea_ingrediente(tf_i.value, dd_u.value)
                # Verifica se l'ingrediente è già nella ricetta
                gia_presente = next((x for x in mappa_ricette_ing.get(id_ricetta_corr, []) if x["id_ingrediente"] == id_ing), None)
                if gia_presente:
                    supabase.table("ricette_ingredienti").update({
                        "quantita": parse_qta(tf_q.value),
                        "unita_misura": dd_u.value
                    }).eq("id", gia_presente["id"]).execute()
                else:
                    supabase.table("ricette_ingredienti").insert({
                        "id_ricetta": id_ricetta_corr,
                        "id_ingrediente": id_ing,
                        "quantita": parse_qta(tf_q.value),
                        "unita_misura": dd_u.value
                    }).execute()
                ricostruisci_form(notifica=True)

            def rimuovi_ricetta_dal_giorno(e, id_voce_menu=v["id"]):
                supabase.table("menu_giornalieri").delete().eq("id", id_voce_menu).execute()
                ricostruisci_form(notifica=True)

            blocchi_ricette_ui.controls.append(
                ft.Container(
                    content=ft.Column([
                        ft.Row([
                            ft.Text(f"🍲 Ricetta: {nome_ric}", size=13, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900, expand=True),
                            ft.IconButton(icon=ft.Icons.DELETE, icon_color=ft.Colors.RED_700, icon_size=18, tooltip="Rimuovi ricetta dal giorno", on_click=rimuovi_ricetta_dal_giorno)
                        ]),
                        ft.Text("Ingredienti della ricetta (modifica quantità con 💾 o aggiungi sotto):", size=10, color=ft.Colors.GREY_700, italic=True),
                        col_ing_ricetta,
                        ft.Row([
                            txt_nuovo_ing_r, txt_nuova_qta_r, dd_nuova_uni_r,
                            ft.IconButton(icon=ft.Icons.ADD_CIRCLE, icon_color=ft.Colors.GREEN_700, on_click=salva_ing_in_ricetta)
                        ], spacing=4),
                        riga_sugg_ing_r
                    ], spacing=5),
                    bgcolor="#F0F4F8", padding=8, border_radius=8,
                    border=ft.Border(top=ft.BorderSide(1, "#BBDEFB"), bottom=ft.BorderSide(1, "#BBDEFB"), left=ft.BorderSide(4, ft.Colors.BLUE_900), right=ft.BorderSide(1, "#BBDEFB"))
                )
            )

        # Campo con completamento automatico per cercare o creare una nuova Ricetta
        txt_ricetta = ft.TextField(label=f"Cerca o scrivi nuova ricetta per {subcategoria} (es. Pasta e ceci)", dense=True, text_size=12, expand=True)
        riga_sugg_ricette = ft.Row(wrap=True, spacing=4, visible=False)

        def assegna_ricetta_al_menu(id_ric_scelta):
            supabase.table("menu_giornalieri").insert({
                "data_menu": data_str,
                "categoria": categoria,
                "subcategoria": subcategoria,
                "id_ricetta": id_ric_scelta
            }).execute()
            ricostruisci_form(notifica=True)

        def filtra_ricette(e):
            testo = (txt_ricetta.value or "").strip().lower()
            riga_sugg_ricette.controls.clear()
            if len(testo) >= 1:
                matches = [r for r in lista_ricette if testo in r["nome"].lower()][:6]
                for m_ric in matches:
                    riga_sugg_ricette.controls.append(
                        ft.Container(
                            content=ft.Text(f"🍲 {m_ric['nome']}", size=11, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                            bgcolor=ft.Colors.BLUE_900, padding=6, border_radius=10,
                            on_click=lambda ev, r_id=m_ric["id"]: assegna_ricetta_al_menu(r_id)
                        )
                    )
                riga_sugg_ricette.visible = len(matches) > 0
            else:
                riga_sugg_ricette.visible = False
            page.update()

        txt_ricetta.on_change = filtra_ricette

        def crea_o_usa_ricetta_click(e):
            if not txt_ricetta.value or not txt_ricetta.value.strip():
                return
            id_ric_nuova = ottieni_o_crea_ricetta(txt_ricetta.value, subcategoria)
            assegna_ricetta_al_menu(id_ric_nuova)

        btn_aggiungi_ricetta = ft.Container(
            content=ft.Text("+ Ricetta", color=ft.Colors.WHITE, size=11, weight=ft.FontWeight.BOLD),
            bgcolor=ft.Colors.BLUE_900, padding=8, border_radius=5, on_click=crea_o_usa_ricetta_click
        )

        return ft.Column([
            blocchi_ricette_ui,
            ft.Row([txt_ricetta, btn_aggiungi_ricetta], spacing=5),
            riga_sugg_ricette
        ], spacing=6)

    # ---------------------------------------------------------
    # COSTRUZIONE DELLE 5 SUPERCATEGORIE E SUBCATEGORIE
    # ---------------------------------------------------------
    def ricostruisci_form(notifica=False):
        carica_dati_db(inizializza_aperture=False)
        colonna_contenuto.controls.clear()

        for sup in SUPERCATEGORIE:
            is_sup_open = sup in cat_aperte
            colore_sup = COLORI_PASTI.get(sup, ft.Colors.BLUE_900)

            def toggle_sup(e, nome_sup=sup):
                if e.control.value:
                    cat_aperte.add(nome_sup)
                else:
                    cat_aperte.discard(nome_sup)
                ricostruisci_form(notifica=False)

            chk_sup = ft.Checkbox(label=sup.upper(), value=is_sup_open, on_change=toggle_sup)

            contenuto_interno_sup = ft.Column(spacing=6, visible=is_sup_open)

            if is_sup_open:
                if sup in ["Pranzo", "Cena"]:
                    # Apre le 6 Subcategorie: Antipasto, Primo, Secondo, Frutta, Dolci, Bevande
                    for sub in SUBCATEGORIE_PASTO:
                        chiave_sub = (sup, sub)
                        is_sub_open = chiave_sub in sub_aperte

                        def toggle_sub(e, k_sub=chiave_sub):
                            if e.control.value:
                                sub_aperte.add(k_sub)
                            else:
                                sub_aperte.discard(k_sub)
                            ricostruisci_form(notifica=False)

                        chk_sub = ft.Checkbox(label=sub, value=is_sub_open, on_change=toggle_sub)
                        blocco_sub = ft.Column(spacing=4)
                        blocco_sub.controls.append(chk_sub)

                        if is_sub_open:
                            if sub in SUB_CON_RICETTA:
                                blocco_sub.controls.append(
                                    ft.Container(content=crea_sezione_ricetta(sup, sub), padding=ft.Padding(15, 0, 0, 5))
                                )
                            else:
                                blocco_sub.controls.append(
                                    ft.Container(content=crea_sezione_ingredienti_diretti(sup, sub), padding=ft.Padding(15, 0, 0, 5))
                                )

                        contenuto_interno_sup.controls.append(
                            ft.Container(
                                content=blocco_sub,
                                bgcolor=ft.Colors.WHITE, padding=6, border_radius=6,
                                border=ft.Border(top=ft.BorderSide(1, "#E0E0E0"), bottom=ft.BorderSide(1, "#E0E0E0"), left=ft.BorderSide(3, colore_sup), right=ft.BorderSide(1, "#E0E0E0"))
                            )
                        )
                else:
                    # Colazione, Merende, Varie -> inserimento diretto ingredienti
                    contenuto_interno_sup.controls.append(
                        ft.Container(
                            content=crea_sezione_ingredienti_diretti(sup, None),
                            bgcolor=ft.Colors.WHITE, padding=8, border_radius=6
                        )
                    )

            colonna_contenuto.controls.append(
                ft.Container(
                    content=ft.Column([chk_sup, contenuto_interno_sup], spacing=4),
                    bgcolor="#FAFAFA", padding=8, border_radius=8,
                    border=ft.Border(top=ft.BorderSide(1, "#E0E0E0"), bottom=ft.BorderSide(1, "#E0E0E0"), left=ft.BorderSide(5, colore_sup), right=ft.BorderSide(1, "#E0E0E0"))
                )
            )

        if notifica:
            page.pubsub.send_all("aggiorna_dati")
        page.update()

    ricostruisci_form(notifica=False)

    dlg_menu = None

    def chiudi_dlg(e=None):
        if dlg_menu:
            try:
                page.close(dlg_menu)
            except AttributeError:
                dlg_menu.open = False
                page.update()
        if on_success_callback:
            on_success_callback()

    dlg_menu = ft.AlertDialog(
        title=ft.Text(f"🍽️ Menu del {data_vis}", size=16, weight=ft.FontWeight.BOLD),
        content=ft.Container(content=colonna_contenuto, width=420, height=520),
        actions=[
            ft.Container(
                content=ft.Text("Chiudi", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                bgcolor=ft.Colors.BLUE_900, padding=10, border_radius=5, on_click=chiudi_dlg
            )
        ],
        shape=ft.RoundedRectangleBorder(radius=10),
        content_padding=12
    )

    try:
        page.open(dlg_menu)
    except AttributeError:
        page.overlay.append(dlg_menu)
        dlg_menu.open = True
        page.update()


# ==========================================================
# CALENDARIO MENSILE DEI MENU
# ==========================================================
def crea_calendario_menu(page: ft.Page):
    stato = {"data": datetime.now()}
    oggi = datetime.now()

    testo_header = ft.Text(size=16, weight=ft.FontWeight.BOLD)
    layer_mese = ft.Column(spacing=0)

    def bordo_standard():
        return ft.Border(
            top=ft.BorderSide(0.5, "#E0E0E0"), bottom=ft.BorderSide(0.5, "#E0E0E0"),
            left=ft.BorderSide(0.5, "#E0E0E0"), right=ft.BorderSide(0.5, "#E0E0E0")
        )

    def nav_prec(e):
        primo = stato["data"].replace(day=1)
        scorso = primo - timedelta(days=1)
        stato["data"] = stato["data"].replace(year=scorso.year, month=scorso.month, day=1)
        aggiorna_calendario_menu()

    def nav_succ(e):
        mese = stato["data"].month
        anno = stato["data"].year
        if mese == 12:
            stato["data"] = stato["data"].replace(year=anno + 1, month=1, day=1)
        else:
            stato["data"] = stato["data"].replace(month=mese + 1, day=1)
        aggiorna_calendario_menu()

    def aggiorna_calendario_menu():
        data = stato["data"]
        testo_header.value = f"Menu - {MESI[data.month]} {data.year}"
        layer_mese.controls.clear()

        dizionario_menu = {}
        if supabase:
            try:
                primo_g = data.replace(day=1).strftime("%Y-%m-%d")
                ultimo_num = calendar.monthrange(data.year, data.month)[1]
                ultimo_g = data.replace(day=ultimo_num).strftime("%Y-%m-%d")

                res = supabase.table("menu_giornalieri").select(
                    "*, ricette(nome), ingredienti(nome)"
                ).gte("data_menu", primo_g).lte("data_menu", ultimo_g).execute()

                if res.data:
                    for riga in res.data:
                        d_str = riga["data_menu"]
                        if d_str not in dizionario_menu:
                            dizionario_menu[d_str] = []
                        dizionario_menu[d_str].append(riga)
            except Exception as ex:
                print("Errore caricamento mese menu:", ex)

        griglia = ft.Column(spacing=0)
        matrice = calendar.monthcalendar(data.year, data.month)

        for settimana in matrice:
            riga_ui = ft.Row(alignment=ft.MainAxisAlignment.SPACE_BETWEEN, spacing=0)
            for giorno in settimana:
                if giorno == 0:
                    riga_ui.controls.append(ft.Container(expand=1, height=90, border=bordo_standard()))
                else:
                    is_oggi = (giorno == oggi.day and data.month == oggi.month and data.year == oggi.year)
                    data_giorno = data.replace(day=giorno)
                    d_str = data_giorno.strftime("%Y-%m-%d")
                    voci_giorno = dizionario_menu.get(d_str, [])

                    cerchio = ft.Container(
                        content=ft.Text(str(giorno), color=ft.Colors.WHITE if is_oggi else ft.Colors.BLACK, size=12, weight=ft.FontWeight.BOLD if is_oggi else ft.FontWeight.NORMAL),
                        bgcolor=ft.Colors.BLUE_900 if is_oggi else ft.Colors.TRANSPARENT,
                        border_radius=15, width=24, height=24, alignment=ft.Alignment(0, 0)
                    )

                    # Raggruppa per categoria per mostrare i badge nella cella del mese
                    cat_presenti = []
                    for sup in SUPERCATEGORIE:
                        v_sup = [x for x in voci_giorno if x["categoria"] == sup]
                        if v_sup:
                            # Mostra il nome della prima ricetta o del primo ingrediente
                            prima = v_sup[0]
                            dettaglio = ""
                            if prima.get("ricette") and prima["ricette"].get("nome"):
                                dettaglio = f": {prima['ricette']['nome']}"
                            elif prima.get("ingredienti") and prima["ingredienti"].get("nome"):
                                dettaglio = f": {prima['ingredienti']['nome']}"
                            cat_presenti.append((sup, f"{sup[:3]}{dettaglio}"))

                    col_badge = ft.Column(spacing=1, expand=True)
                    for sup_nome, etichetta in cat_presenti[:2]:
                        col_badge.controls.append(
                            ft.Container(
                                content=ft.Text(etichetta, size=7, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, no_wrap=True),
                                bgcolor=COLORI_PASTI.get(sup_nome, ft.Colors.BLUE_900),
                                padding=2, border_radius=2, height=18
                            )
                        )
                    if len(cat_presenti) > 2:
                        col_badge.controls.append(
                            ft.Container(
                                content=ft.Text(f"+ {len(cat_presenti) - 2} altri", size=8, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_700),
                                alignment=ft.Alignment(0, 0)
                            )
                        )

                    riga_ui.controls.append(
                        ft.Container(
                            content=ft.Column([
                                ft.Container(content=cerchio, alignment=ft.Alignment(0, 0)),
                                col_badge
                            ], spacing=2, horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
                            expand=1, height=90, border=bordo_standard(), padding=2,
                            on_click=lambda e, dt=data_giorno: apri_form_menu_giorno(page, dt, aggiorna_calendario_menu)
                        )
                    )
            griglia.controls.append(riga_ui)

        layer_mese.controls.extend([
            ft.Row(
                controls=[
                    ft.Container(content=ft.Text(g, size=9, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_700), expand=1, alignment=ft.Alignment(0, 0), border=bordo_standard(), padding=2)
                    for g in GIORNI
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN, spacing=0
            ),
            griglia
        ])
        try:
            page.update()
        except Exception:
            pass

    page.aggiorna_vista_menu = aggiorna_calendario_menu

    header = ft.Row(
        controls=[
            ft.Row([
                ft.IconButton(ft.Icons.CHEVRON_LEFT, on_click=nav_prec, icon_size=20),
                ft.IconButton(ft.Icons.CHEVRON_RIGHT, on_click=nav_succ, icon_size=20),
                testo_header,
            ], alignment=ft.MainAxisAlignment.START, spacing=0),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN
    )

    aggiorna_calendario_menu()

    return ft.Container(
        content=ft.Column([header, layer_mese], spacing=10, scroll=ft.ScrollMode.AUTO),
        padding=5, expand=True
    )
