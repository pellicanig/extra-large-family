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


def apri_dialog_sicuro(page: ft.Page, dlg: ft.AlertDialog):
    try:
        page.open(dlg)
    except AttributeError:
        if dlg not in page.overlay:
            page.overlay.append(dlg)
        dlg.open = True
        page.update()


def chiudi_dialog_sicuro(page: ft.Page, dlg: ft.AlertDialog):
    if not dlg:
        return
    try:
        page.close(dlg)
    except AttributeError:
        dlg.open = False
        page.update()


def parse_qta(val_str):
    if not val_str:
        return 0.0
    try:
        return float(str(val_str).replace(",", ".").strip())
    except ValueError:
        return 0.0


def formatta_numero(val):
    try:
        f = float(val)
        return str(int(f)) if f.is_integer() else f"{f:.2f}".rstrip("0").rstrip(".")
    except Exception:
        return str(val)


# ==========================================================
# POPUP RINOMINA / MODIFICA INGREDIENTE (SENZA DUPLICARE)
# ==========================================================
def apri_popup_rinomina_ingrediente(page: ft.Page, ing_obj: dict, lista_ingredienti: list, on_done_callback):
    txt_nome = ft.TextField(label="Nuovo nome ingrediente", value=ing_obj.get("nome", ""), dense=True, text_size=13)
    dd_unita = ft.Dropdown(
        label="Unità predefinita",
        value=ing_obj.get("unita_default") if ing_obj.get("unita_default") in UNITA_MISURA else "g",
        dense=True,
        options=[ft.dropdown.Option(u) for u in UNITA_MISURA]
    )
    txt_err = ft.Text("", color=ft.Colors.RED_700, size=11, visible=False)
    dlg_mod = None

    def salva_mod(e):
        nuovo_nome = (txt_nome.value or "").strip().capitalize()
        if not nuovo_nome:
            txt_err.value = "⚠️ Inserisci un nome valido."
            txt_err.visible = True
            page.update()
            return

        try:
            # Controlla se esiste già un ALTRO ingrediente con questo nome
            conflitto = next(
                (i for i in lista_ingredienti if i["nome"].lower() == nuovo_nome.lower() and str(i["id"]) != str(ing_obj["id"])),
                None
            )
            if conflitto:
                # Unisce i riferimenti sul conflitto esistente ed elimina il doppione
                supabase.table("ricette_ingredienti").update({"id_ingrediente": conflitto["id"]}).eq("id_ingrediente", ing_obj["id"]).execute()
                supabase.table("menu_giornalieri").update({"id_ingrediente": conflitto["id"]}).eq("id_ingrediente", ing_obj["id"]).execute()
                supabase.table("ingredienti").delete().eq("id", ing_obj["id"]).execute()
            else:
                # Aggiorna il nome in-place: tutte le ricette e i menu vedranno il nuovo nome!
                supabase.table("ingredienti").update({
                    "nome": nuovo_nome,
                    "unita_default": dd_unita.value
                }).eq("id", ing_obj["id"]).execute()

            chiudi_dialog_sicuro(page, dlg_mod)
            if on_done_callback:
                on_done_callback()
        except Exception as ex:
            txt_err.value = f"⚠️ Errore: {ex}"
            txt_err.visible = True
            page.update()

    dlg_mod = ft.AlertDialog(
        title=ft.Text(f"✏️ Modifica Ingrediente", size=15, weight=ft.FontWeight.BOLD),
        content=ft.Container(
            content=ft.Column([
                ft.Text("La modifica aggiornerà il nome ovunque senza creare duplicati.", size=11, color=ft.Colors.GREY_700),
                txt_nome,
                dd_unita,
                txt_err
            ], tight=True, spacing=8),
            width=310
        ),
        actions=[
            ft.TextButton("Annulla", on_click=lambda e: chiudi_dialog_sicuro(page, dlg_mod)),
            ft.Container(
                content=ft.Text("Salva", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=12),
                bgcolor=ft.Colors.ORANGE_800, padding=8, border_radius=5, on_click=salva_mod
            )
        ]
    )
    apri_dialog_sicuro(page, dlg_mod)


# ==========================================================
# POPUP MODIFICA O ELIMINAZIONE INTERA RICETTA DAL DATABASE
# ==========================================================
def apri_popup_gestione_ricetta(page: ft.Page, id_ricetta: str, nome_attuale: str, portata_attuale: str, on_done_callback):
    txt_nome_ric = ft.TextField(label="Nome Ricetta", value=nome_attuale, dense=True, text_size=13)
    dd_portata = ft.Dropdown(
        label="Portata predefinita",
        value=portata_attuale if portata_attuale in SUB_CON_RICETTA else "Primo",
        dense=True,
        options=[ft.dropdown.Option(p) for p in SUB_CON_RICETTA]
    )
    txt_err = ft.Text("", color=ft.Colors.RED_700, size=11, visible=False)
    dlg_ric = None

    def salva_ricetta_db(e):
        nuovo_n = (txt_nome_ric.value or "").strip().capitalize()
        if not nuovo_n:
            txt_err.value = "⚠️ Il nome della ricetta è obbligatorio."
            txt_err.visible = True
            page.update()
            return
        try:
            supabase.table("ricette").update({
                "nome": nuovo_n,
                "portata_default": dd_portata.value
            }).eq("id", id_ricetta).execute()
            chiudi_dialog_sicuro(page, dlg_ric)
            if on_done_callback:
                on_done_callback()
        except Exception as ex:
            txt_err.value = f"⚠️ Errore: {ex}"
            txt_err.visible = True
            page.update()

    def elimina_ricetta_definitivamente(e):
        try:
            supabase.table("ricette").delete().eq("id", id_ricetta).execute()
            chiudi_dialog_sicuro(page, dlg_ric)
            if on_done_callback:
                on_done_callback()
        except Exception as ex:
            txt_err.value = f"⚠️ Errore eliminazione: {ex}"
            txt_err.visible = True
            page.update()

    dlg_ric = ft.AlertDialog(
        title=ft.Text("🍲 Gestione Ricetta", size=15, weight=ft.FontWeight.BOLD),
        content=ft.Container(
            content=ft.Column([
                txt_nome_ric,
                dd_portata,
                ft.Divider(height=8, color="#E0E0E0"),
                ft.Text("Attenzione: 'Elimina dal DB' cancella definitivamente la ricetta dal ricettario.", size=10, color=ft.Colors.RED_700, italic=True),
                txt_err
            ], tight=True, spacing=8),
            width=310
        ),
        actions=[
            ft.Container(
                content=ft.Text("🗑️ Elimina dal DB", color=ft.Colors.WHITE, size=11, weight=ft.FontWeight.BOLD),
                bgcolor=ft.Colors.RED_700, padding=8, border_radius=5, on_click=elimina_ricetta_definitivamente
            ),
            ft.TextButton("Annulla", on_click=lambda e: chiudi_dialog_sicuro(page, dlg_ric)),
            ft.Container(
                content=ft.Text("Aggiorna", color=ft.Colors.WHITE, size=11, weight=ft.FontWeight.BOLD),
                bgcolor=ft.Colors.BLUE_900, padding=8, border_radius=5, on_click=salva_ricetta_db
            )
        ],
        actions_alignment=ft.MainAxisAlignment.SPACE_BETWEEN
    )
    apri_dialog_sicuro(page, dlg_ric)


# ==========================================================
# FORM MENU DEL GIORNO (MOBILE-FRIENDLY + SPINNER + RIPETIZIONE MESE)
# ==========================================================
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

    # ⚡ SPINNER DI CARICAMENTO ANTI-FREEZE ⚡
    spinner_header = ft.ProgressRing(width=16, height=16, stroke_width=2, color=ft.Colors.BLUE_900, visible=True)
    lbl_stato_caricamento = ft.Text("Caricamento dati...", size=11, color=ft.Colors.BLUE_900, italic=True, visible=True)

    colonna_contenuto = ft.Column(
        controls=[
            ft.Container(
                content=ft.Column([
                    ft.ProgressRing(width=32, height=32, stroke_width=3, color=ft.Colors.BLUE_900),
                    ft.Text("Caricamento menu e ricettario in corso...", size=12, color=ft.Colors.GREY_700)
                ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=10),
                alignment=ft.Alignment(0, 0), padding=40
            )
        ],
        spacing=6, scroll=ft.ScrollMode.AUTO, tight=True
    )

    dlg_menu = None

    def chiudi_dlg(e=None):
        chiudi_dialog_sicuro(page, dlg_menu)
        if on_success_callback:
            on_success_callback()

    dlg_menu = ft.AlertDialog(
        title=ft.Row([
            ft.Text(f"🍽️ Menu {data_vis}", size=15, weight=ft.FontWeight.BOLD, expand=True),
            spinner_header,
            lbl_stato_caricamento
        ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
        content=ft.Container(content=colonna_contenuto, width=350, height=480),
        actions=[
            ft.Container(
                content=ft.Text("Chiudi", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=12),
                bgcolor=ft.Colors.BLUE_900, padding=ft.Padding(14, 8, 14, 8), border_radius=6, on_click=chiudi_dlg
            )
        ],
        shape=ft.RoundedRectangleBorder(radius=10),
        content_padding=8,
        inset_padding=ft.Padding(10, 15, 10, 15)
    )

    # Mostra SUBITO il popup con la rotella prima di interrogare Supabase
    apri_dialog_sicuro(page, dlg_menu)
    page.update()

    def mostra_loading(testo="Aggiornamento..."):
        spinner_header.visible = True
        lbl_stato_caricamento.value = testo
        lbl_stato_caricamento.visible = True
        try:
            page.update()
        except Exception:
            pass

    def nascondi_loading():
        spinner_header.visible = False
        lbl_stato_caricamento.visible = False
        try:
            page.update()
        except Exception:
            pass

    def carica_dati_db(inizializza_aperture=False):
        nonlocal lista_ingredienti, lista_ricette, menu_del_giorno, mappa_ricette_ing
        try:
            res_ing = supabase.table("ingredienti").select("*").order("nome").execute()
            lista_ingredienti = res_ing.data if res_ing.data else []

            res_ric = supabase.table("ricette").select("*").order("nome").execute()
            lista_ricette = res_ric.data if res_ric.data else []

            res_menu = supabase.table("menu_giornalieri").select(
                "*, ricette(id, nome, portata_default), ingredienti(id, nome, unita_default)"
            ).eq("data_menu", data_str).execute()
            menu_del_giorno = res_menu.data if res_menu.data else []

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
    # FUNZIONE RIPETI IN TUTTI I GIORNI DEL MESE
    # ---------------------------------------------------------
    def apri_dialog_ripeti_mese(categoria, subcategoria=None):
        voci_correnti = [
            m for m in menu_del_giorno
            if m["categoria"] == categoria and m.get("subcategoria") == subcategoria
        ]
        nome_sezione = f"{categoria} - {subcategoria}" if subcategoria else categoria

        dd_ambito = ft.Dropdown(
            label="Giorni su cui applicare",
            value="Tutto il mese",
            dense=True,
            options=[
                ft.dropdown.Option("Tutto il mese"),
                ft.dropdown.Option("Da questo giorno a fine mese")
            ]
        )
        dlg_rip = None

        def esegui_ripetizione(e):
            chiudi_dialog_sicuro(page, dlg_rip)
            mostra_loading("Copia su tutti i giorni del mese...")
            try:
                anno = data_selezionata.year
                mese = data_selezionata.month
                ultimo_g = calendar.monthrange(anno, mese)[1]
                giorno_start = 1 if dd_ambito.value == "Tutto il mese" else data_selezionata.day

                primo_str = f"{anno:04d}-{mese:02d}-{giorno_start:02d}"
                ultimo_str = f"{anno:04d}-{mese:02d}-{ultimo_g:02d}"

                # Rimuove eventuali voci precedenti della stessa categoria negli altri giorni del range per evitare doppioni
                q_del = supabase.table("menu_giornalieri").delete().eq("categoria", categoria).gte("data_menu", primo_str).lte("data_menu", ultimo_str).neq("data_menu", data_str)
                if subcategoria:
                    q_del = q_del.eq("subcategoria", subcategoria)
                else:
                    q_del = q_del.is_("subcategoria", "null")
                q_del.execute()

                # Se ci sono voci nel giorno attuale, le replica in blocco su tutti i giorni del mese
                if voci_correnti:
                    nuovi_records = []
                    for g in range(giorno_start, ultimo_g + 1):
                        d_target = f"{anno:04d}-{mese:02d}-{g:02d}"
                        if d_target == data_str:
                            continue
                        for vc in voci_correnti:
                            nuovi_records.append({
                                "data_menu": d_target,
                                "categoria": categoria,
                                "subcategoria": subcategoria,
                                "id_ricetta": vc.get("id_ricetta"),
                                "id_ingrediente": vc.get("id_ingrediente"),
                                "quantita": vc.get("quantita") or 0,
                                "unita_misura": vc.get("unita_misura") or "g"
                            })
                    if nuovi_records:
                        supabase.table("menu_giornalieri").insert(nuovi_records).execute()

                ricostruisci_form(notifica=True)
            except Exception as ex:
                print("Errore ripetizione mese:", ex)
                nascondi_loading()

        testo_info = (
            f"Vuoi replicare le {len(voci_correnti)} voci di '{nome_sezione}' sugli altri giorni di {MESI[data_selezionata.month]}?"
            if voci_correnti else
            f"'{nome_sezione}' oggi è vuota: confermando svuoterai '{nome_sezione}' anche negli altri giorni del mese."
        )

        dlg_rip = ft.AlertDialog(
            title=ft.Text(f"🔁 Ripeti {nome_sezione}", size=15, weight=ft.FontWeight.BOLD),
            content=ft.Container(
                content=ft.Column([
                    ft.Text(testo_info, size=12),
                    dd_ambito
                ], tight=True, spacing=10),
                width=310
            ),
            actions=[
                ft.TextButton("Annulla", on_click=lambda e: chiudi_dialog_sicuro(page, dlg_rip)),
                ft.Container(
                    content=ft.Text("Applica al Mese", color=ft.Colors.WHITE, size=12, weight=ft.FontWeight.BOLD),
                    bgcolor=ft.Colors.BLUE_900, padding=8, border_radius=5, on_click=esegui_ripetizione
                )
            ]
        )
        apri_dialog_sicuro(page, dlg_rip)

    # ---------------------------------------------------------
    # BLOCCO INGREDIENTI DIRETTI (OTTIMIZZATO MOBILE SU 2 RIGHE)
    # ---------------------------------------------------------
    def crea_sezione_ingredienti_diretti(categoria, subcategoria=None):
        voci = [
            m for m in menu_del_giorno
            if m["categoria"] == categoria and m.get("subcategoria") == subcategoria and m.get("id_ingrediente")
        ]

        lista_ui = ft.Column(spacing=4)
        for v in voci:
            ing_obj = v.get("ingredienti") or {"id": v["id_ingrediente"], "nome": "Ingrediente", "unita_default": "g"}
            nome_ing = ing_obj.get("nome", "Ingrediente")
            qta = v.get("quantita") or 0
            qta_str = f"{formatta_numero(qta)} {v.get('unita_misura') or ''}".strip()

            def elimina_voce(e, id_voce=v["id"]):
                mostra_loading("Rimozione...")
                supabase.table("menu_giornalieri").delete().eq("id", id_voce).execute()
                ricostruisci_form(notifica=True)

            def rinomina_voce(e, item_ing=ing_obj):
                apri_popup_rinomina_ingrediente(page, item_ing, lista_ingredienti, lambda: ricostruisci_form(notifica=True))

            lista_ui.controls.append(
                ft.Container(
                    content=ft.Row([
                        ft.Text(f"• {nome_ing}", size=12, weight=ft.FontWeight.BOLD, expand=True),
                        ft.Text(qta_str, size=11, color=ft.Colors.BLUE_900, weight=ft.FontWeight.BOLD),
                        ft.IconButton(icon=ft.Icons.EDIT, icon_color=ft.Colors.ORANGE_800, icon_size=15, tooltip="Rinomina ingrediente", on_click=rinomina_voce),
                        ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_color=ft.Colors.RED_700, icon_size=16, tooltip="Rimuovi dal giorno", on_click=elimina_voce)
                    ], spacing=2),
                    bgcolor="#F5F5F5", padding=ft.Padding(6, 2, 2, 2), border_radius=5
                )
            )

        # Layout Mobile su 2 righe (niente più testi schiacciati in verticale)
        txt_ing = ft.TextField(label="Cerca o scrivi ingrediente...", dense=True, text_size=12, expand=True)
        txt_qta = ft.TextField(label="Quantità", value="1", dense=True, text_size=12, expand=1)
        dd_unita = ft.Dropdown(
            value="pz", 
            dense=True, 
            width=60, 
            height=36, 
            content_padding=ft.Padding(0, 0, 0, 0),
            text_style=ft.TextStyle(size=12),
            options=[ft.dropdown.Option(u) for u in UNITA_MISURA]
        )
        riga_suggerimenti = ft.Row(wrap=True, spacing=4, run_spacing=4, visible=False)

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
                matches = [i for i in lista_ingredienti if testo in i["nome"].lower()][:8]
                for m_ing in matches:
                    riga_suggerimenti.controls.append(
                        ft.Container(
                            content=ft.Row([
                                ft.Text(f"🔍 {m_ing['nome']}", size=11, color=ft.Colors.BLUE_900, weight=ft.FontWeight.BOLD),
                            ], tight=True, spacing=2),
                            bgcolor=ft.Colors.BLUE_50, padding=ft.Padding(8, 4, 8, 4), border_radius=10,
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
            mostra_loading("Salvataggio ingrediente...")
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

        btn_add = ft.Container(
            content=ft.Text("+ Aggiungi", color=ft.Colors.WHITE, size=11, weight=ft.FontWeight.BOLD),
            bgcolor=ft.Colors.BLUE_900, padding=ft.Padding(10, 8, 10, 8), border_radius=6,
            on_click=aggiungi_ing_diretto
        )

        return ft.Column([
            lista_ui,
            txt_ing,
            riga_suggerimenti,
            ft.Row([txt_qta, dd_unita, btn_add], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        ], spacing=6)

    # ---------------------------------------------------------
    # BLOCCO RICETTA + INGREDIENTI RELAZIONALI (OTTIMIZZATO MOBILE)
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
            portata_ric = ric_obj.get("portata_default") or subcategoria
            ingredienti_associati = mappa_ricette_ing.get(id_ric, [])

            col_ing_ricetta = ft.Column(spacing=6)
            for ri in ingredienti_associati:
                ing_info = ri.get("ingredienti") or {"id": ri["id_ingrediente"], "nome": "Ingrediente", "unita_default": "g"}
                nome_i = ing_info.get("nome", "Ingrediente")
                qta_val = ri.get("quantita") or 0
                qta_init = formatta_numero(qta_val)
                unita_init = ri.get("unita_misura") or "g"

                tf_qta_ri = ft.TextField(value=qta_init, dense=True, width=65, text_size=11, content_padding=6)
                dd_uni_ri = ft.Dropdown(
                    value=unita_init if unita_init in UNITA_MISURA else "g",
                    dense=True, width=72, options=[ft.dropdown.Option(u) for u in UNITA_MISURA]
                )

                def aggiorna_qta_ri(e, id_riga_ri=ri["id"], campo_q=tf_qta_ri, campo_u=dd_uni_ri):
                    mostra_loading("Aggiornamento quantità...")
                    supabase.table("ricette_ingredienti").update({
                        "quantita": parse_qta(campo_q.value),
                        "unita_misura": campo_u.value
                    }).eq("id", id_riga_ri).execute()
                    ricostruisci_form(notifica=True)

                def elimina_ing_da_ricetta(e, id_riga_ri=ri["id"]):
                    mostra_loading("Rimozione ingrediente...")
                    supabase.table("ricette_ingredienti").delete().eq("id", id_riga_ri).execute()
                    ricostruisci_form(notifica=True)

                def rinomina_ing_ricetta(e, item_ing=ing_info):
                    apri_popup_rinomina_ingrediente(page, item_ing, lista_ingredienti, lambda: ricostruisci_form(notifica=True))

                # Mobile-friendly: Nome + Matita sopra, Quantità + Unità + Salva + X sotto
                col_ing_ricetta.controls.append(
                    ft.Container(
                        content=ft.Column([
                            ft.Row([
                                ft.Text(f"▪ {nome_i}", size=12, weight=ft.FontWeight.BOLD, expand=True),
                                ft.IconButton(icon=ft.Icons.EDIT, icon_color=ft.Colors.ORANGE_800, icon_size=14, tooltip="Rinomina ingrediente", on_click=rinomina_ing_ricetta),
                                ft.IconButton(icon=ft.Icons.CLOSE, icon_color=ft.Colors.RED_700, icon_size=15, tooltip="Togli dalla ricetta", on_click=elimina_ing_da_ricetta)
                            ], spacing=2),
                            ft.Row([
                                ft.Text("Q.tà:", size=10, color=ft.Colors.GREY_700),
                                tf_qta_ri,
                                dd_uni_ri,
                                ft.Container(
                                    content=ft.Text("💾 Salva Q.tà", size=10, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                                    bgcolor=ft.Colors.GREEN_700, padding=ft.Padding(8, 5, 8, 5), border_radius=4,
                                    on_click=aggiorna_qta_ri
                                )
                            ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER)
                        ], spacing=2),
                        bgcolor=ft.Colors.WHITE, padding=5, border_radius=5
                    )
                )

            # Aggiunta nuovo ingrediente alla ricetta (su 2 righe per mobile)
            txt_nuovo_ing_r = ft.TextField(label="Aggiungi ingrediente alla ricetta...", dense=True, text_size=11, expand=True)
            txt_nuova_qta_r = ft.TextField(label="Q.tà", value="100", dense=True, text_size=11, expand=1)
            dd_nuova_uni_r = ft.Dropdown(
                label="Unità", value="g", dense=True, expand=1,
                options=[ft.dropdown.Option(u) for u in UNITA_MISURA]
            )
            riga_sugg_ing_r = ft.Row(wrap=True, spacing=4, run_spacing=4, visible=False)

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
                    matches = [i for i in lista_ingredienti if testo in i["nome"].lower()][:8]
                    for m_ing in matches:
                        r_sugg.controls.append(
                            ft.Container(
                                content=ft.Text(f"🔍 {m_ing['nome']}", size=10, color=ft.Colors.BLUE_900, weight=ft.FontWeight.BOLD),
                                bgcolor=ft.Colors.BLUE_50, padding=ft.Padding(6, 3, 6, 3), border_radius=8,
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
                mostra_loading("Aggiunta ingrediente...")
                id_ing = ottieni_o_crea_ingrediente(tf_i.value, dd_u.value)
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
                mostra_loading("Rimozione dal menu...")
                supabase.table("menu_giornalieri").delete().eq("id", id_voce_menu).execute()
                ricostruisci_form(notifica=True)

            def gestisci_ricetta_db(e, r_id=id_ric, r_nome=nome_ric, r_port=portata_ric):
                apri_popup_gestione_ricetta(page, r_id, r_nome, r_port, lambda: ricostruisci_form(notifica=True))

            blocchi_ricette_ui.controls.append(
                ft.Container(
                    content=ft.Column([
                        ft.Row([
                            ft.Text(f"🍲 {nome_ric}", size=13, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900, expand=True),
                            ft.IconButton(icon=ft.Icons.EDIT, icon_color=ft.Colors.ORANGE_800, icon_size=16, tooltip="Rinomina o Elimina Ricetta dal DB", on_click=gestisci_ricetta_db),
                            ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_color=ft.Colors.RED_700, icon_size=17, tooltip="Togli ricetta da questo giorno", on_click=rimuovi_ricetta_dal_giorno)
                        ], spacing=2),
                        ft.Text("Ingredienti associati (per 8 persone):", size=10, color=ft.Colors.GREY_700, italic=True),
                        col_ing_ricetta,
                        ft.Divider(height=4, color="#BBDEFB"),
                        txt_nuovo_ing_r,
                        riga_sugg_ing_r,
                        ft.Row([
                            txt_nuova_qta_r,
                            dd_nuova_uni_r,
                            ft.Container(
                                content=ft.Text("+ Ingrediente", color=ft.Colors.WHITE, size=11, weight=ft.FontWeight.BOLD),
                                bgcolor=ft.Colors.GREEN_700, padding=ft.Padding(10, 7, 10, 7), border_radius=5,
                                on_click=salva_ing_in_ricetta
                            )
                        ], spacing=5)
                    ], spacing=5),
                    bgcolor="#F0F4F8", padding=6, border_radius=8,
                    border=ft.Border(top=ft.BorderSide(1, "#BBDEFB"), bottom=ft.BorderSide(1, "#BBDEFB"), left=ft.BorderSide(4, ft.Colors.BLUE_900), right=ft.BorderSide(1, "#BBDEFB"))
                )
            )

        # Cerca o crea nuova Ricetta
        txt_ricetta = ft.TextField(label=f"Cerca tra le 100+ ricette o scrivi nuova ({subcategoria})", dense=True, text_size=12, expand=True)
        riga_sugg_ricette = ft.Row(wrap=True, spacing=4, run_spacing=4, visible=False)

        def assegna_ricetta_al_menu(id_ric_scelta):
            mostra_loading("Caricamento ricetta...")
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
                matches = [r for r in lista_ricette if testo in r["nome"].lower()][:8]
                for m_ric in matches:
                    riga_sugg_ricette.controls.append(
                        ft.Container(
                            content=ft.Text(f"🍲 {m_ric['nome']}", size=11, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                            bgcolor=ft.Colors.BLUE_900, padding=ft.Padding(8, 5, 8, 5), border_radius=10,
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
            mostra_loading("Creazione ricetta...")
            id_ric_nuova = ottieni_o_crea_ricetta(txt_ricetta.value, subcategoria)
            assegna_ricetta_al_menu(id_ric_nuova)

        btn_aggiungi_ricetta = ft.Container(
            content=ft.Text("+ Crea/Aggiungi Ricetta", color=ft.Colors.WHITE, size=11, weight=ft.FontWeight.BOLD),
            bgcolor=ft.Colors.BLUE_900, padding=ft.Padding(10, 7, 10, 7), border_radius=5, on_click=crea_o_usa_ricetta_click
        )

        return ft.Column([
            blocchi_ricette_ui,
            txt_ricetta,
            riga_sugg_ricette,
            ft.Row([btn_aggiungi_ricetta], alignment=ft.MainAxisAlignment.END)
        ], spacing=6)

    # ---------------------------------------------------------
    # COSTRUZIONE DELLE 5 SUPERCATEGORIE E SUBCATEGORIE
    # ---------------------------------------------------------
    def ricostruisci_form(notifica=False):
        mostra_loading("Caricamento...")
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

            # Pulsante per ripetere Colazione/Merende/Varie su tutti i giorni del mese
            header_sup_controls = [ft.Container(content=chk_sup, expand=True)]
            if sup not in ["Pranzo", "Cena"]:
                btn_ripeti_sup = ft.Container(
                    content=ft.Text("🔁 Ripeti nel mese", size=10, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                    bgcolor=colore_sup, padding=ft.Padding(7, 4, 7, 4), border_radius=12,
                    on_click=lambda e, s_cat=sup: apri_dialog_ripeti_mese(s_cat, None)
                )
                header_sup_controls.append(btn_ripeti_sup)

            riga_header_sup = ft.Row(header_sup_controls, alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER)
            contenuto_interno_sup = ft.Column(spacing=6, visible=is_sup_open)

            if is_sup_open:
                if sup in ["Pranzo", "Cena"]:
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
                        btn_ripeti_sub = ft.Container(
                            content=ft.Text("🔁 Mese", size=9, color=ft.Colors.BLUE_900, weight=ft.FontWeight.BOLD),
                            bgcolor=ft.Colors.BLUE_50, padding=ft.Padding(6, 3, 6, 3), border_radius=10,
                            on_click=lambda e, s_cat=sup, sb_cat=sub: apri_dialog_ripeti_mese(s_cat, sb_cat)
                        )

                        blocco_sub = ft.Column(spacing=4)
                        blocco_sub.controls.append(
                            ft.Row([ft.Container(content=chk_sub, expand=True), btn_ripeti_sub], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
                        )

                        if is_sub_open:
                            if sub in SUB_CON_RICETTA:
                                blocco_sub.controls.append(
                                    ft.Container(content=crea_sezione_ricetta(sup, sub), padding=ft.Padding(4, 0, 2, 4))
                                )
                            else:
                                blocco_sub.controls.append(
                                    ft.Container(content=crea_sezione_ingredienti_diretti(sup, sub), padding=ft.Padding(4, 0, 2, 4))
                                )

                        contenuto_interno_sup.controls.append(
                            ft.Container(
                                content=blocco_sub,
                                bgcolor=ft.Colors.WHITE, padding=5, border_radius=6,
                                border=ft.Border(top=ft.BorderSide(1, "#E0E0E0"), bottom=ft.BorderSide(1, "#E0E0E0"), left=ft.BorderSide(3, colore_sup), right=ft.BorderSide(1, "#E0E0E0"))
                            )
                        )
                else:
                    contenuto_interno_sup.controls.append(
                        ft.Container(
                            content=crea_sezione_ingredienti_diretti(sup, None),
                            bgcolor=ft.Colors.WHITE, padding=6, border_radius=6
                        )
                    )

            colonna_contenuto.controls.append(
                ft.Container(
                    content=ft.Column([riga_header_sup, contenuto_interno_sup], spacing=4),
                    bgcolor="#FAFAFA", padding=6, border_radius=8,
                    border=ft.Border(top=ft.BorderSide(1, "#E0E0E0"), bottom=ft.BorderSide(1, "#E0E0E0"), left=ft.BorderSide(5, colore_sup), right=ft.BorderSide(1, "#E0E0E0"))
                )
            )

        nascondi_loading()
        if notifica:
            page.pubsub.send_all("aggiorna_dati")
        page.update()

    carica_dati_db(inizializza_aperture=True)
    ricostruisci_form(notifica=False)


# ==========================================================
# MOTORE INTELLIGENTE "FAI LA SPESA" (AGGREGAZIONE + SPUNTE DISPENSA)
# ==========================================================
def apri_popup_fai_la_spesa(page: ft.Page, data_riferimento: datetime):
    if not supabase:
        return

    oggi_dt = datetime.now()
    data_start_default = oggi_dt.strftime("%Y-%m-%d")
    spuntati_dispensa = set() # ID chiave degli ingredienti già in dispensa o nel carrello

    colonna_lista = ft.Column(
        controls=[
            ft.Container(
                content=ft.Column([
                    ft.ProgressRing(width=32, height=32, stroke_width=3, color=ft.Colors.GREEN_700),
                    ft.Text("Calcolo e somma ingredienti in corso...", size=12, color=ft.Colors.GREY_700)
                ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=10),
                alignment=ft.Alignment(0, 0), padding=40
            )
        ],
        spacing=6, scroll=ft.ScrollMode.AUTO, expand=True
    )

    dd_periodo = ft.Dropdown(
        label="Periodo spesa",
        value="Da oggi in poi",
        dense=True,
        expand=True,
        options=[
            ft.dropdown.Option("Da oggi in poi"),
            ft.dropdown.Option("Dal giorno selezionato in poi"),
            ft.dropdown.Option("Prossimi 7 giorni"),
            ft.dropdown.Option("Tutto il mese visualizzato")
        ]
    )

    txt_riepilogo = ft.Text("", size=11, color=ft.Colors.GREY_700, weight=ft.FontWeight.BOLD)
    dlg_spesa = None

    dlg_spesa = ft.AlertDialog(
        title=ft.Row([
            ft.Text("🛒 Lista della Spesa", size=16, weight=ft.FontWeight.BOLD, color=ft.Colors.GREEN_800, expand=True),
        ]),
        content=ft.Container(
            content=ft.Column([
                dd_periodo,
                txt_riepilogo,
                ft.Divider(height=4, color="#E0E0E0"),
                colonna_lista
            ], spacing=6),
            width=350, height=480
        ),
        actions=[
            ft.Container(
                content=ft.Text("Chiudi", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=12),
                bgcolor=ft.Colors.BLUE_900, padding=ft.Padding(14, 8, 14, 8), border_radius=6,
                on_click=lambda e: chiudi_dialog_sicuro(page, dlg_spesa)
            )
        ],
        shape=ft.RoundedRectangleBorder(radius=10),
        content_padding=10,
        inset_padding=ft.Padding(10, 15, 10, 15)
    )

    apri_dialog_sicuro(page, dlg_spesa)
    page.update()

    # Normalizza unità per sommare es. 500 g + 1 kg = 1.5 kg
    def normalizza_qta_unita(qta, unita):
        u = (unita or "pz").lower().strip()
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

    def calcola_dati_spesa():
        nonlocal dati_aggregati
        colonna_lista.controls.clear()
        colonna_lista.controls.append(
            ft.Container(
                content=ft.Row([ft.ProgressRing(width=22, height=22), ft.Text("Aggregazione quantità...", size=12)], alignment=ft.MainAxisAlignment.CENTER),
                padding=25
            )
        )
        page.update()

        scelta = dd_periodo.value
        fine_str = None
        if scelta == "Da oggi in poi":
            inizio_str = oggi_dt.strftime("%Y-%m-%d")
        elif scelta == "Dal giorno selezionato in poi":
            inizio_str = data_riferimento.strftime("%Y-%m-%d")
        elif scelta == "Prossimi 7 giorni":
            inizio_str = oggi_dt.strftime("%Y-%m-%d")
            fine_str = (oggi_dt + timedelta(days=7)).strftime("%Y-%m-%d")
        else:
            inizio_str = data_riferimento.replace(day=1).strftime("%Y-%m-%d")
            ult = calendar.monthrange(data_riferimento.year, data_riferimento.month)[1]
            fine_str = data_riferimento.replace(day=ult).strftime("%Y-%m-%d")

        try:
            query = supabase.table("menu_giornalieri").select(
                "*, ingredienti(id, nome), ricette(id, nome)"
            ).gte("data_menu", inizio_str)
            if fine_str:
                query = query.lte("data_menu", fine_str)
            res_m = query.execute()
            righe_menu = res_m.data if res_m.data else []

            ids_ric = list({r["id_ricetta"] for r in righe_menu if r.get("id_ricetta")})
            mappa_ri = {}
            if ids_ric:
                res_ri = supabase.table("ricette_ingredienti").select(
                    "*, ingredienti(id, nome)"
                ).in_("id_ricetta", ids_ric).execute()
                for ri in (res_ri.data or []):
                    rid = ri["id_ricetta"]
                    if rid not in mappa_ri:
                        mappa_ri[rid] = []
                    mappa_ri[rid].append(ri)

            # Aggregazione: chiave = (nome_ingrediente_lower, unita_base)
            somme = {}
            for m in righe_menu:
                if m.get("id_ingrediente") and m.get("ingredienti"):
                    nome_i = m["ingredienti"]["nome"]
                    q_norm, u_base = normalizza_qta_unita(m.get("quantita") or 0, m.get("unita_misura"))
                    chiave = (nome_i.lower(), u_base)
                    if chiave not in somme:
                        somme[chiave] = {"nome": nome_i, "qta": 0.0, "unita": u_base, "occorrenze": 0}
                    somme[chiave]["qta"] += q_norm
                    somme[chiave]["occorrenze"] += 1
                elif m.get("id_ricetta"):
                    for ri in mappa_ri.get(m["id_ricetta"], []):
                        if ri.get("ingredienti"):
                            nome_i = ri["ingredienti"]["nome"]
                            q_norm, u_base = normalizza_qta_unita(ri.get("quantita") or 0, ri.get("unita_misura"))
                            chiave = (nome_i.lower(), u_base)
                            if chiave not in somme:
                                somme[chiave] = {"nome": nome_i, "qta": 0.0, "unita": u_base, "occorrenze": 0}
                            somme[chiave]["qta"] += q_norm
                            somme[chiave]["occorrenze"] += 1

            dati_aggregati = sorted(list(somme.values()), key=lambda x: x["nome"].lower())
            disegna_lista_spesa()
        except Exception as ex:
            colonna_lista.controls.clear()
            colonna_lista.controls.append(ft.Text(f"Errore calcolo spesa: {ex}", color=ft.Colors.RED_700, size=12))
            page.update()

    def disegna_lista_spesa():
        colonna_lista.controls.clear()
        if not dati_aggregati:
            txt_riepilogo.value = "Nessun ingrediente pianificato per il periodo scelto."
            colonna_lista.controls.append(
                ft.Container(
                    content=ft.Text("Non ci sono menu programmati in questo intervallo di date.", size=12, color=ft.Colors.GREY_600, italic=True),
                    padding=20, alignment=ft.Alignment(0, 0)
                )
            )
            page.update()
            return

        da_comprare = []
        gia_in_dispensa = []

        for item in dati_aggregati:
            k_id = f"{item['nome'].lower()}_{item['unita']}"
            if k_id in spuntati_dispensa:
                gia_in_dispensa.append((k_id, item))
            else:
                da_comprare.append((k_id, item))

        txt_riepilogo.value = f"Da acquistare: {len(da_comprare)}  |  In dispensa/Presi: {len(gia_in_dispensa)}"

        def toggle_spunta(e, key_ing):
            if e.control.value:
                spuntati_dispensa.add(key_ing)
            else:
                spuntati_dispensa.discard(key_ing)
            disegna_lista_spesa()

        # Sezione 1: Da Acquistare
        colonna_lista.controls.append(
            ft.Text("🛒 DA ACQUISTARE (spunta se già in dispensa o nel carrello):", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900)
        )
        if not da_comprare:
            colonna_lista.controls.append(
                ft.Container(content=ft.Text("🎉 Hai spuntato tutti gli ingredienti!", size=12, color=ft.Colors.GREEN_700, weight=ft.FontWeight.BOLD), padding=8)
            )
        for k_id, item in da_comprare:
            q_finale = formatta_qta_finale(item["qta"], item["unita"])
            colonna_lista.controls.append(
                ft.Container(
                    content=ft.Row([
                        ft.Checkbox(value=False, on_change=lambda e, k=k_id: toggle_spunta(e, k)),
                        ft.Text(item["nome"], size=13, weight=ft.FontWeight.BOLD, expand=True),
                        ft.Container(
                            content=ft.Text(q_finale, size=12, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                            bgcolor=ft.Colors.BLUE_900, padding=ft.Padding(8, 3, 8, 3), border_radius=8
                        )
                    ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    bgcolor="#F5F7FA", padding=ft.Padding(4, 2, 8, 2), border_radius=6,
                    border=ft.Border(top=ft.BorderSide(1, "#E0E0E0"), bottom=ft.BorderSide(1, "#E0E0E0"), left=ft.BorderSide(4, ft.Colors.GREEN_700), right=ft.BorderSide(1, "#E0E0E0"))
                )
            )

        # Sezione 2: Già in dispensa / Presi
        if gia_in_dispensa:
            colonna_lista.controls.append(ft.Divider(height=10, color="#E0E0E0"))
            colonna_lista.controls.append(
                ft.Text("✅ GIÀ IN DISPENSA / NEL CARRELLO:", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_600)
            )
            for k_id, item in gia_in_dispensa:
                q_finale = formatta_qta_finale(item["qta"], item["unita"])
                colonna_lista.controls.append(
                    ft.Container(
                        content=ft.Row([
                            ft.Checkbox(value=True, on_change=lambda e, k=k_id: toggle_spunta(e, k)),
                            ft.Text(f"✓ {item['nome']}", size=12, color=ft.Colors.GREY_600, italic=True, expand=True),
                            ft.Text(q_finale, size=11, color=ft.Colors.GREY_600)
                        ], spacing=4),
                        bgcolor="#EEEEEE", padding=ft.Padding(4, 1, 8, 1), border_radius=6
                    )
                )

        page.update()

    dd_periodo.on_change = lambda e: calcola_dati_spesa()
    calcola_dati_spesa()


# ==========================================================
# CALENDARIO DEI MENU (VISTE: GIORNO, SETTIMANA, MESE + PULSANTE FAI LA SPESA)
# ==========================================================
def crea_calendario_menu(page: ft.Page):
    stato = {
        "data": datetime.now(),
        "vista": "Mese"
    }
    oggi = datetime.now()

    testo_header = ft.Text(size=15, weight=ft.FontWeight.BOLD)
    spinner_calendario = ft.ProgressRing(width=16, height=16, stroke_width=2, color=ft.Colors.BLUE_900, visible=False)

    btn_giorno = ft.Container(content=ft.Text("Giorno", size=12, weight=ft.FontWeight.BOLD), border_radius=5, padding=6, on_click=lambda e: cambia_vista("Giorno"))
    btn_settimana = ft.Container(content=ft.Text("Settimana", size=12, weight=ft.FontWeight.BOLD), border_radius=5, padding=6, on_click=lambda e: cambia_vista("Settimana"))
    btn_mese = ft.Container(content=ft.Text("Mese", size=12, weight=ft.FontWeight.BOLD), border_radius=5, padding=6, on_click=lambda e: cambia_vista("Mese"))

    layer_mese = ft.Column(spacing=0, visible=True)
    layer_settimana = ft.Column(spacing=0, visible=False)
    layer_giorno = ft.Column(spacing=0, visible=False)
    area_dinamica = ft.Column([layer_mese, layer_settimana, layer_giorno], expand=True, spacing=0)

    def bordo_standard():
        return ft.Border(
            top=ft.BorderSide(0.5, "#E0E0E0"), bottom=ft.BorderSide(0.5, "#E0E0E0"),
            left=ft.BorderSide(0.5, "#E0E0E0"), right=ft.BorderSide(0.5, "#E0E0E0")
        )

    def cambia_vista(nuova_vista):
        stato["vista"] = nuova_vista
        aggiorna_calendario_menu()

    def vai_a_giorno_menu(dt_target):
        stato["data"] = dt_target
        stato["vista"] = "Giorno"
        aggiorna_calendario_menu()

    def nav_prec(e):
        if stato["vista"] == "Mese":
            primo = stato["data"].replace(day=1)
            scorso = primo - timedelta(days=1)
            stato["data"] = stato["data"].replace(year=scorso.year, month=scorso.month, day=1)
        elif stato["vista"] == "Settimana":
            stato["data"] -= timedelta(days=7)
        elif stato["vista"] == "Giorno":
            stato["data"] -= timedelta(days=1)
        aggiorna_calendario_menu()

    def nav_succ(e):
        if stato["vista"] == "Mese":
            mese = stato["data"].month
            anno = stato["data"].year
            if mese == 12:
                stato["data"] = stato["data"].replace(year=anno + 1, month=1, day=1)
            else:
                stato["data"] = stato["data"].replace(month=mese + 1, day=1)
        elif stato["vista"] == "Settimana":
            stato["data"] += timedelta(days=7)
        elif stato["vista"] == "Giorno":
            stato["data"] += timedelta(days=1)
        aggiorna_calendario_menu()

    def aggiorna_calendario_menu():
        vista = stato["vista"]
        data = stato["data"]

        spinner_calendario.visible = True
        try:
            page.update()
        except Exception:
            pass

        btn_giorno.bgcolor = ft.Colors.BLUE_900 if vista == "Giorno" else ft.Colors.GREY_200
        btn_giorno.content.color = ft.Colors.WHITE if vista == "Giorno" else ft.Colors.BLACK
        btn_settimana.bgcolor = ft.Colors.BLUE_900 if vista == "Settimana" else ft.Colors.GREY_200
        btn_settimana.content.color = ft.Colors.WHITE if vista == "Settimana" else ft.Colors.BLACK
        btn_mese.bgcolor = ft.Colors.BLUE_900 if vista == "Mese" else ft.Colors.GREY_200
        btn_mese.content.color = ft.Colors.WHITE if vista == "Mese" else ft.Colors.BLACK

        layer_mese.visible = (vista == "Mese")
        layer_settimana.visible = (vista == "Settimana")
        layer_giorno.visible = (vista == "Giorno")

        dizionario_menu = {}
        if supabase:
            try:
                data_rif = data.replace(day=1)
                inizio_f = (data_rif - timedelta(days=10)).strftime("%Y-%m-%d")
                fine_f = (data_rif + timedelta(days=42)).strftime("%Y-%m-%d")

                res = supabase.table("menu_giornalieri").select(
                    "*, ricette(id, nome), ingredienti(id, nome)"
                ).gte("data_menu", inizio_f).lte("data_menu", fine_f).execute()

                if res.data:
                    for riga in res.data:
                        d_str = riga["data_menu"]
                        if d_str not in dizionario_menu:
                            dizionario_menu[d_str] = []
                        dizionario_menu[d_str].append(riga)
            except Exception as ex:
                print("Errore caricamento menu:", ex)

        # ==========================================
        # 1. VISTA MESE MENU
        # ==========================================
        if vista == "Mese":
            testo_header.value = f"{MESI[data.month]} {data.year}"
            layer_mese.controls.clear()

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

                        cat_presenti = []
                        for sup in SUPERCATEGORIE:
                            v_sup = [x for x in voci_giorno if x["categoria"] == sup]
                            if v_sup:
                                # Dà priorità al nome di una ricetta se presente (es. Pranzo -> Pasta e ceci)
                                voce_ric = next((x for x in v_sup if x.get("ricette") and x["ricette"].get("nome")), v_sup[0])
                                dettaglio = ""
                                if voce_ric.get("ricette") and voce_ric["ricette"].get("nome"):
                                    dettaglio = f": {voce_ric['ricette']['nome']}"
                                elif voce_ric.get("ingredienti") and voce_ric["ingredienti"].get("nome"):
                                    dettaglio = f": {voce_ric['ingredienti']['nome']}"
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

        # ==========================================
        # 2. VISTA SETTIMANA MENU (SU 2 RIGHE COME IMPEGNI)
        # ==========================================
        elif vista == "Settimana":
            lunedi = data - timedelta(days=data.weekday())
            domenica = lunedi + timedelta(days=6)
            testo_header.value = (
                f"{lunedi.day} - {domenica.day} {MESI[lunedi.month][:3]} {lunedi.year}"
                if lunedi.month == domenica.month else
                f"{lunedi.day} {MESI[lunedi.month][:3]} - {domenica.day} {MESI[domenica.month][:3]} {domenica.year}"
            )
            layer_settimana.controls.clear()

            riga1 = ft.Row(alignment=ft.MainAxisAlignment.START, spacing=0)
            riga2 = ft.Row(alignment=ft.MainAxisAlignment.START, spacing=0)

            for i in range(7):
                gc = lunedi + timedelta(days=i)
                d_str = gc.strftime("%Y-%m-%d")
                voci_giorno = dizionario_menu.get(d_str, [])

                cerchio_sett = ft.Container(
                    content=ft.Text(str(gc.day), color=ft.Colors.WHITE if (gc.date() == oggi.date()) else ft.Colors.BLACK, size=13, weight=ft.FontWeight.BOLD),
                    bgcolor=ft.Colors.BLUE_900 if (gc.date() == oggi.date()) else ft.Colors.TRANSPARENT,
                    border_radius=15, width=24, height=24, alignment=ft.Alignment(0, 0)
                )

                intestazione = ft.Container(
                    content=ft.Column([
                        ft.Text(GIORNI[i], size=9, color=ft.Colors.GREY_700),
                        cerchio_sett
                    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=0),
                    padding=4
                )

                col_pasti_sett = ft.Column(spacing=3, scroll=ft.ScrollMode.AUTO)
                for sup in SUPERCATEGORIE:
                    v_sup = [x for x in voci_giorno if x["categoria"] == sup]
                    if v_sup:
                        col_sup = COLORI_PASTI.get(sup, ft.Colors.BLUE_900)
                        testi_voci = []
                        for x in v_sup:
                            pref = f"{x['subcategoria'][:3]}: " if x.get("subcategoria") else ""
                            if x.get("ricette") and x["ricette"].get("nome"):
                                testi_voci.append(f"• {pref}{x['ricette']['nome']}")
                            elif x.get("ingredienti") and x["ingredienti"].get("nome"):
                                testi_voci.append(f"• {pref}{x['ingredienti']['nome']}")

                        col_pasti_sett.controls.append(
                            ft.Container(
                                content=ft.Column(
                                    [ft.Text(sup.upper(), size=8, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE)] +
                                    [ft.Text(tv, size=8, color=ft.Colors.WHITE, no_wrap=False) for tv in testi_voci],
                                    spacing=1
                                ),
                                bgcolor=col_sup, padding=4, border_radius=4, width=float("inf")
                            )
                        )

                box_giorno = ft.Container(
                    content=ft.Column([
                        intestazione,
                        ft.Divider(height=1, color="#E0E0E0"),
                        ft.Container(content=col_pasti_sett, padding=2, expand=True)
                    ], spacing=0),
                    expand=1, height=300, border=bordo_standard(),
                    on_click=lambda e, dt=gc: apri_form_menu_giorno(page, dt, aggiorna_calendario_menu)
                )

                if i < 4:
                    riga1.controls.append(box_giorno)
                else:
                    riga2.controls.append(box_giorno)

            riga2.controls.append(ft.Container(expand=1))
            layer_settimana.controls.extend([riga1, ft.Container(height=12), riga2])

        # ==========================================
        # 3. VISTA GIORNO MENU
        # ==========================================
        elif vista == "Giorno":
            testo_header.value = f"{data.day} {MESI[data.month]} {data.year}"
            is_oggi = (data.date() == oggi.date())
            layer_giorno.controls.clear()

            d_str = data.strftime("%Y-%m-%d")
            voci_giorno = dizionario_menu.get(d_str, [])

            intestazione = ft.Container(
                content=ft.Row([
                    ft.Column([
                        ft.Text(GIORNI[data.weekday()], size=12, color=ft.Colors.GREY_700),
                        ft.Text(str(data.day), size=24, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900 if is_oggi else ft.Colors.BLACK)
                    ], spacing=0),
                    ft.Container(
                        content=ft.Text("✏️ Compila / Modifica Menu del Giorno", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=12),
                        bgcolor=ft.Colors.BLUE_900, padding=10, border_radius=6,
                        on_click=lambda e: apri_form_menu_giorno(page, data, aggiorna_calendario_menu)
                    )
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                padding=10, border=bordo_standard()
            )

            col_dettaglio_giorno = ft.Column(spacing=8)

            if not voci_giorno:
                col_dettaglio_giorno.controls.append(
                    ft.Container(
                        content=ft.Text("Nessun pasto programmato per questa giornata. Clicca su 'Compila / Modifica Menu' per aggiungere ricette o ingredienti.", color=ft.Colors.GREY_600, italic=True, size=12),
                        padding=20, alignment=ft.Alignment(0, 0)
                    )
                )
            else:
                for sup in SUPERCATEGORIE:
                    v_sup = [x for x in voci_giorno if x["categoria"] == sup]
                    if v_sup:
                        col_sup = COLORI_PASTI.get(sup, ft.Colors.BLUE_900)
                        righe_pasto = [ft.Text(f"🍽️ {sup.upper()}", size=13, weight=ft.FontWeight.BOLD, color=col_sup)]
                        for x in v_sup:
                            sub_lbl = f"[{x['subcategoria']}] " if x.get("subcategoria") else ""
                            if x.get("ricette") and x["ricette"].get("nome"):
                                righe_pasto.append(ft.Text(f"  🍲 {sub_lbl}{x['ricette']['nome']}", size=12, weight=ft.FontWeight.BOLD))
                            elif x.get("ingredienti") and x["ingredienti"].get("nome"):
                                q = x.get("quantita") or 0
                                q_str = f" ({formatta_numero(q)} {x.get('unita_misura') or ''})" if q else ""
                                righe_pasto.append(ft.Text(f"  • {sub_lbl}{x['ingredienti']['nome']}{q_str}", size=12))

                        col_dettaglio_giorno.controls.append(
                            ft.Container(
                                content=ft.Column(righe_pasto, spacing=3),
                                bgcolor=ft.Colors.WHITE, padding=10, border_radius=8,
                                border=ft.Border(top=ft.BorderSide(1, "#E0E0E0"), bottom=ft.BorderSide(1, "#E0E0E0"), left=ft.BorderSide(5, col_sup), right=ft.BorderSide(1, "#E0E0E0")),
                                on_click=lambda e: apri_form_menu_giorno(page, data, aggiorna_calendario_menu)
                            )
                        )

            layer_giorno.controls.extend([intestazione, ft.Container(height=6), col_dettaglio_giorno])

        spinner_calendario.visible = False
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
                spinner_calendario
            ], alignment=ft.MainAxisAlignment.START, spacing=2),
            ft.Row([btn_giorno, btn_settimana, btn_mese], spacing=5)
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        wrap=True,
        run_spacing=8
    )

    # ⚡ PULSANTE FISSO "FAI LA SPESA" IN BASSO ALLE VISUALIZZAZIONI ⚡
    btn_fai_la_spesa = ft.Container(
        content=ft.Row([
            ft.Icon(ft.Icons.SHOPPING_CART, color=ft.Colors.WHITE, size=20),
            ft.Text("FAI LA SPESA (Somma Ingredienti & Dispensa)", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=13)
        ], alignment=ft.MainAxisAlignment.CENTER, spacing=8),
        bgcolor=ft.Colors.GREEN_700,
        padding=12,
        border_radius=8,
        on_click=lambda e: apri_popup_fai_la_spesa(page, stato["data"])
    )

    aggiorna_calendario_menu()

    return ft.Container(
        content=ft.Column([
            header,
            area_dinamica,
            ft.Divider(height=10, color="#EEEEEE"),
            btn_fai_la_spesa
        ], spacing=10, scroll=ft.ScrollMode.AUTO),
        padding=5, expand=True
    )
