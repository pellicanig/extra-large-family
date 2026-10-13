import flet as ft
import os
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client, Client
from modulo_sovrapposizioni import analizza_sovrapposizioni_giornata

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    supabase = None
    print(f"Errore connessione Supabase in modulo_impegni: {e}")

PALETTE_COLORI = [
    {"nome": "🔴", "hex": "#F44336"},
    {"nome": "🟠", "hex": "#FF9800"},
    {"nome": "🟡", "hex": "#FFC107"},
    {"nome": "🟢", "hex": "#4CAF50"},
    {"nome": "🔵", "hex": "#2196F3"},
    {"nome": "🟣", "hex": "#9C27B0"},
    {"nome": "🟤", "hex": "#795548"},
    {"nome": "⚫", "hex": "#212121"},
    {"nome": "⚪", "hex": "#9E9E9E"},
]

def apri_form_impegno(page: ft.Page, data_selezionata: datetime, on_success_callback=None, impegno_esistente=None):
    try:
        if not supabase:
            snack = ft.SnackBar(ft.Text("Errore: Database non connesso! Controlla il file .env"))
            try:
                page.open(snack)
            except AttributeError:
                page.overlay.append(snack)
                snack.open = True
                page.update()
            return

        res_cat = supabase.table('categorie').select('*').order('nome').execute()
        lista_categorie = res_cat.data if res_cat.data else []
        
        res_fam = supabase.table('famiglia').select('*').order('nome').execute()
        lista_famiglia = res_fam.data if res_fam.data else []

        def get_colori_liberi_cat(id_cat_esclusa=None):
            occupati = []
            for c in lista_categorie:
                if c.get('colore') and str(c['id']) != str(id_cat_esclusa):
                    occupati.append(c['colore'])
            
            liberi = [p for p in PALETTE_COLORI if p['hex'] not in occupati]
            if not liberi:
                liberi = PALETTE_COLORI
            return liberi

        # TRUCCO: Inseriamo il carattere invisibile \u200b nei campi vuoti.
        # Flet crederà che il campo sia pieno e forzerà PER SEMPRE l'etichetta sul bordo superiore.
        titolo_iniziale = impegno_esistente.get('titolo', "") if impegno_esistente else "\u200b"
        
        inizio_val = impegno_esistente.get('ora_inizio') if impegno_esistente else None
        inizio_iniziale = str(inizio_val)[:5] if inizio_val else "\u200b"
        
        fine_val = impegno_esistente.get('ora_fine') if impegno_esistente else None
        fine_iniziale = str(fine_val)[:5] if fine_val else "\u200b"
        
        note_iniziali = impegno_esistente.get('note', "") if (impegno_esistente and impegno_esistente.get('note')) else ""
        
        # Nessuna pre-selezione: il campo parte vuoto se è un nuovo impegno
        cat_iniziale = impegno_esistente.get('id_categoria') if impegno_esistente else None
            
        assegnatari_esistenti = impegno_esistente.get('assegnatari_ids', []) if impegno_esistente else []
        
        is_p_db = False
        if impegno_esistente and impegno_esistente.get('is_periodico'):
            val = impegno_esistente.get('is_periodico')
            is_p_db = True if str(val).lower() in ['true', '1', 't', 'y', 'yes'] else False
            
        data_fine_db = impegno_esistente.get('data_fine_periodo') if impegno_esistente else None
        date_escluse_db = impegno_esistente.get('date_escluse') if impegno_esistente else ""

        switch_on_iniziale = False
        if is_p_db:
            if data_fine_db is None:
                switch_on_iniziale = True
            elif data_fine_db > data_selezionata.strftime("%Y-%m-%d"):
                switch_on_iniziale = True

        # ⚡ TRUCCO 2: Aggiunto hint_text per costringere anche l'etichetta del Titolo sempre sul bordo
        txt_titolo = ft.TextField(label="Titolo Impegno", hint_text="Scrivi qui...", value=titolo_iniziale, text_size=13)
        
        def formatta_data_input(e):
            valore_pulito = "".join([c for c in txt_data.value if c.isdigit()])
            risultato = ""
            for i, num in enumerate(valore_pulito):
                if i == 2 or i == 4: risultato += "/"
                risultato += num
            risultato = risultato[:10]
            if txt_data.value != risultato:
                txt_data.value = risultato
                txt_data.error_text = None
                page.update()

        def formatta_ora_inizio(e):
            valore_pulito = "".join([c for c in txt_inizio.value if c.isdigit()])
            risultato = ""
            for i, num in enumerate(valore_pulito):
                if i == 2: risultato += ":"
                risultato += num
            risultato = risultato[:5]
            
            aggiorna_ui = False
            if txt_inizio.value != risultato:
                txt_inizio.value = risultato
                txt_inizio.error_text = None
                aggiorna_ui = True
                
            # ⚡ AUTO-COMPILA L'ORARIO DI FINE A +1 ORA ⚡
            if len(risultato) == 5:
                try:
                    dt_inizio = datetime.strptime(risultato, "%H:%M")
                    h = dt_inizio.hour
                    m = dt_inizio.minute
                    
                    if h == 23:
                        nuova_fine = "23:59" # Evita lo scavalco di mezzanotte
                    else:
                        nuova_fine = f"{(h + 1):02d}:{m:02d}"
                        
                    # Scrive automaticamente l'orario di fine (sovrascrive per agevolare l'utente)
                    txt_fine.value = nuova_fine
                    txt_fine.error_text = None
                    aggiorna_ui = True
                except ValueError:
                    pass # Se scrive un orario impossibile (es 25:00) lo ignora qui, ci penserà la validazione

            if aggiorna_ui:
                page.update()

        def formatta_ora_fine(e):
            valore_pulito = "".join([c for c in txt_fine.value if c.isdigit()])
            risultato = ""
            for i, num in enumerate(valore_pulito):
                if i == 2: risultato += ":"
                risultato += num
            risultato = risultato[:5]
            if txt_fine.value != risultato:
                txt_fine.value = risultato
                txt_fine.error_text = None
                page.update()

        data_visualizzata_iniziale = data_selezionata.strftime("%d/%m/%Y")
        if impegno_esistente and impegno_esistente.get('data_evento'):
            try:
                dt_obj = datetime.strptime(impegno_esistente['data_evento'], "%Y-%m-%d")
                data_visualizzata_iniziale = dt_obj.strftime("%d/%m/%Y")
            except:
                pass

        txt_data = ft.TextField(label="Data", hint_text="GG/MM/AAAA", value=data_visualizzata_iniziale, text_size=13, on_change=formatta_data_input, text_align=ft.TextAlign.CENTER, expand=True)
        txt_inizio = ft.TextField(label="Inizio", hint_text="HH:MM", value=inizio_iniziale, text_size=13, on_change=formatta_ora_inizio, text_align=ft.TextAlign.CENTER, expand=True)
        txt_fine = ft.TextField(label="Fine", hint_text="HH:MM", value=fine_iniziale, text_size=13, on_change=formatta_ora_fine, text_align=ft.TextAlign.CENTER, expand=True)
        
        riga_date_orari = ft.Row([txt_data, txt_inizio, txt_fine], spacing=5)
        
        txt_note = ft.TextField(label="Note aggiuntive", hint_text="Eventuali dettagli...", value=note_iniziali, multiline=True, min_lines=2, max_lines=4)
        txt_errore_form = ft.Text(value="", color=ft.Colors.RED_700, size=13, weight=ft.FontWeight.BOLD, visible=False)

        # ⚡ Aggiunto hint_text per mantenere l'etichetta sul bordo anche quando è vuoto
        dd_categoria = ft.Dropdown(
            label="Categoria (*)", 
            hint_text="Scegli una categoria...",
            value=cat_iniziale,
            options=[ft.dropdown.Option(key=c['id'], text=c['nome']) for c in lista_categorie],
            expand=True
        )

        # ⚡ PULSANTI ASSEGNATARI IN STILE FILTRO ⚡
        selezionati_famiglia = set(assegnatari_esistenti)

        def toggle_famiglia(e, f_id, colore_fam):
            if f_id in selezionati_famiglia:
                selezionati_famiglia.remove(f_id)
                e.control.bgcolor = ft.Colors.TRANSPARENT
                e.control.content.color = colore_fam
            else:
                selezionati_famiglia.add(f_id)
                e.control.bgcolor = colore_fam
                e.control.content.color = ft.Colors.WHITE
            e.control.update()

        bottoni_famiglia = []
        for f in lista_famiglia:
            colore_fam = f.get('colore', '#9E9E9E')
            is_sel = f['id'] in selezionati_famiglia
            bottoni_famiglia.append(
                ft.Container(
                    content=ft.Text(f['nome'], size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE if is_sel else colore_fam),
                    bgcolor=colore_fam if is_sel else ft.Colors.TRANSPARENT,
                    border=ft.Border(top=ft.BorderSide(2, colore_fam), bottom=ft.BorderSide(2, colore_fam), left=ft.BorderSide(2, colore_fam), right=ft.BorderSide(2, colore_fam)),
                    padding=6, border_radius=15, width=120, alignment=ft.Alignment(0, 0),
                    on_click=lambda e, f_id=f['id'], c_fam=colore_fam: toggle_famiglia(e, f_id, c_fam)
                )
            )
        
        # Divisione in 2 colonne esatte
        mezzo = (len(bottoni_famiglia) + 1) // 2
        griglia_assegnatari = ft.Row([
            ft.Column(bottoni_famiglia[:mezzo], spacing=5), 
            ft.Column(bottoni_famiglia[mezzo:], spacing=5)
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.START)

        # ⚡ INIZIO GESTIONE GIORNI MULTIPLI (L M M G V S D) ⚡
        giorni_db_str = impegno_esistente.get('giorni_periodici') if impegno_esistente else None
        selezionati_giorni = set()
        
        if giorni_db_str:
            selezionati_giorni = set([int(g) for g in giorni_db_str.split(',') if g.strip().isdigit()])
        elif is_p_db or not impegno_esistente:
            # Di default accende il giorno della data selezionata (0=Lunedì, 6=Domenica)
            selezionati_giorni.add(data_selezionata.weekday())

        etichette_giorni = ["L", "M", "M", "G", "V", "S", "D"]
        bottoni_giorni = []

        def toggle_giorno(e, indice_giorno):
            if not switch_periodico.value: return # Bloccati se lo switch è spento
            
            if indice_giorno in selezionati_giorni:
                # Non permette di deselezionare l'ultimo giorno rimasto
                if len(selezionati_giorni) > 1:
                    selezionati_giorni.remove(indice_giorno)
                    e.control.bgcolor = ft.Colors.TRANSPARENT
                    e.control.content.color = ft.Colors.GREY_600
            else:
                selezionati_giorni.add(indice_giorno)
                e.control.bgcolor = ft.Colors.BLUE_900
                e.control.content.color = ft.Colors.WHITE
            e.control.update()

        for i, lettera in enumerate(etichette_giorni):
            is_g_sel = i in selezionati_giorni
            bottoni_giorni.append(
                ft.Container(
                    content=ft.Text(lettera, size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE if is_g_sel else ft.Colors.GREY_600),
                    bgcolor=ft.Colors.BLUE_900 if is_g_sel else ft.Colors.TRANSPARENT,
                    border=ft.Border(top=ft.BorderSide(2, ft.Colors.BLUE_900), bottom=ft.BorderSide(2, ft.Colors.BLUE_900), left=ft.BorderSide(2, ft.Colors.BLUE_900), right=ft.BorderSide(2, ft.Colors.BLUE_900)),
                    shape=ft.BoxShape.CIRCLE,
                    width=30, height=30, alignment=ft.Alignment(0, 0),
                    on_click=lambda e, idx=i: toggle_giorno(e, idx)
                )
            )

        riga_giorni = ft.Row(bottoni_giorni, spacing=5, alignment=ft.MainAxisAlignment.CENTER, visible=switch_on_iniziale)
        # ⚡ FINE GESTIONE GIORNI MULTIPLI ⚡

        # Funzione che scatta quando si cambia la voce nella tendina (Singolo / Successivi / Serie)
        def on_change_tipo_modifica(e):
            if dd_modifica_periodico.value == "Solo questa data":
                # Se è "solo questa data", l'evento diventerà singolo. Nascondiamo lo switch e i giorni.
                switch_periodico.visible = False
                riga_giorni.visible = False
                txt_avviso_periodico.visible = False
            else:
                # Per "Questo e i successivi" o "Tutta la serie", mostriamo lo switch e i giorni.
                switch_periodico.visible = True
                riga_giorni.visible = switch_periodico.value
                
                # Se l'utente spegne l'interruttore mentre applica al futuro, lo avvisiamo
                if not switch_periodico.value:
                    txt_avviso_periodico.value = f"🛑 Periodicità interrotta a partire dal {data_str_it}"
                    txt_avviso_periodico.visible = True
                else:
                    txt_avviso_periodico.visible = False
                    
            page.update()

        data_str_it = f"{data_selezionata.day}/{data_selezionata.month}/{data_selezionata.year}"
        
        txt_avviso_periodico = ft.Text(
            value=f"🛑 Periodicità interrotta a partire dal {data_str_it}",
            color=ft.Colors.RED_700, size=12, weight=ft.FontWeight.BOLD, italic=True, visible=False
        )

        switch_periodico = ft.Switch(
            label="Ripeti in questi giorni:", 
            value=switch_on_iniziale
        )

        def aggiorna_ui_periodico(e=None):
            # Se l'utente sceglie "Solo questa data", disattiva e nasconde i giorni
            if dd_modifica_periodico.value == "Solo questa data":
                switch_periodico.visible = False
                riga_giorni.visible = False
                txt_avviso_periodico.visible = False
            else:
                # Se sceglie "Questo e i successivi" o "Tutta la serie", mostra lo switch e i giorni
                switch_periodico.visible = True
                riga_giorni.visible = switch_periodico.value
                txt_avviso_periodico.visible = not switch_periodico.value
            
            # Forza l'aggiornamento grafico del form a schermo
            try:
                page.update()
            except:
                pass

        dd_modifica_periodico = ft.Dropdown(
            label="Applica modifiche a:",
            options=[
                ft.dropdown.Option("Solo questa data"), 
                ft.dropdown.Option("Questo e i successivi"),
                ft.dropdown.Option("Tutta la serie")
            ],
            value="Solo questa data",
            dense=True,
            visible=(impegno_esistente is not None and is_p_db)
        )
        
        # Assegnazione sicura dell'evento di cambio opzione
        dd_modifica_periodico.on_change = aggiorna_ui_periodico
        switch_periodico.on_change = aggiorna_ui_periodico

        # Inizializzazione all'apertura del form
        if impegno_esistente and is_p_db:
            switch_periodico.visible = False
            riga_giorni.visible = False

        # 4. Assegnazione sicura delle azioni utente
        def cambia_stato_periodico(e):
            riga_giorni.visible = switch_periodico.value
            if impegno_esistente and is_p_db:
                txt_avviso_periodico.visible = not switch_periodico.value
            page.update()

        def on_change_tipo_modifica(e):
            if dd_modifica_periodico.value == "Solo questa data":
                switch_periodico.visible = False
                riga_giorni.visible = False
                txt_avviso_periodico.visible = False
            else:
                switch_periodico.visible = True
                riga_giorni.visible = switch_periodico.value
                if not switch_periodico.value:
                    txt_avviso_periodico.visible = True
                else:
                    txt_avviso_periodico.visible = False
            page.update()

        switch_periodico.on_change = cambia_stato_periodico
        dd_modifica_periodico.on_change = on_change_tipo_modifica

        dlg_impegno = None
        dlg_categoria = None
        dlg_modifica_cat = None 
        dlg_conferma = None 

        def chiudi_form(e=None):
            if dlg_impegno:
                try: page.close(dlg_impegno)
                except AttributeError: dlg_impegno.open = False; page.update()

        def chiudi_conferma(e=None):
            if dlg_conferma:
                try: page.close(dlg_conferma)
                except AttributeError: dlg_conferma.open = False; page.update()

        def apri_nuova_categoria(e):
            nonlocal dlg_categoria
            txt_nuova_cat = ft.TextField(label="Nome Categoria", dense=True)
            
            # ⚡ Errore interno al popup Nuova Categoria ⚡
            txt_errore_cat = ft.Text(value="", color=ft.Colors.RED_700, size=12, weight=ft.FontWeight.BOLD, visible=False)
            
            colori_liberi = get_colori_liberi_cat(None)
            dd_colore = ft.Dropdown(
                label="Colore", 
                dense=True,
                options=[ft.dropdown.Option(key=c['hex'], text=c['nome']) for c in colori_liberi],
                value=colori_liberi[0]['hex']
            )

            def chiudi_cat(e=None):
                try: page.close(dlg_categoria)
                except AttributeError: dlg_categoria.open = False; page.update()

            def salva_categoria(e):
                txt_errore_cat.visible = False
                txt_nuova_cat.error_text = None
                nome_cat = txt_nuova_cat.value.strip().upper()
                
                if not nome_cat:
                    txt_errore_cat.value = "⚠️ Inserisci un nome per la categoria."
                    txt_errore_cat.visible = True
                    txt_nuova_cat.error_text = "Obbligatorio"
                    page.update()
                    return

                try:
                    res = supabase.table('categorie').insert({"nome": nome_cat, "colore": dd_colore.value}).execute()
                    nuovo_id = res.data[0]['id']
                    lista_categorie.append({"id": nuovo_id, "nome": nome_cat, "colore": dd_colore.value})
                    dd_categoria.options.append(ft.dropdown.Option(key=nuovo_id, text=nome_cat))
                    dd_categoria.value = nuovo_id
                    dd_categoria.update()
                    page.pubsub.send_all("aggiorna_dati") # ⚡ SINCRONIZZA CATEGORIA CON GLI ALTRI
                except Exception as ex:
                    txt_errore_cat.value = f"⚠️ Errore salvataggio: {ex}"
                    txt_errore_cat.visible = True
                    page.update()
                    return
                chiudi_cat()

            dlg_categoria = ft.AlertDialog(
                title=ft.Text("Nuova Categoria", size=16, weight=ft.FontWeight.BOLD),
                content=ft.Column([txt_nuova_cat, dd_colore, txt_errore_cat], tight=True),
                actions=[
                    ft.TextButton("Annulla", on_click=chiudi_cat),
                    ft.Container(content=ft.Text("Salva", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD), bgcolor=ft.Colors.BLUE_900, padding=10, border_radius=5, on_click=salva_categoria)
                ]
            )
            try: page.open(dlg_categoria)
            except AttributeError: page.overlay.append(dlg_categoria); dlg_categoria.open = True; page.update()

        def apri_modifica_categoria(e):
            nonlocal dlg_modifica_cat
            txt_errore_form.visible = False
            dd_categoria.error_text = None
            
            # ⚡ Errore instradato direttamente dentro il popup principale invece della SnackBar ⚡
            if not dd_categoria.value:
                txt_errore_form.value = "⚠️ Seleziona una categoria dal menu a tendina per modificarla."
                txt_errore_form.visible = True
                dd_categoria.error_text = "Seleziona per modificare"
                page.update()
                return

            cat_corrente = next((c for c in lista_categorie if str(c['id']) == str(dd_categoria.value)), None)
            if not cat_corrente: return

            # ⚡ Errore interno al popup Modifica Categoria ⚡
            txt_errore_mod = ft.Text(value="", color=ft.Colors.RED_700, size=12, weight=ft.FontWeight.BOLD, visible=False)

            colori_liberi = get_colori_liberi_cat(cat_corrente['id'])
            dd_colore_mod = ft.Dropdown(
                label="Nuovo Colore", 
                dense=True,
                options=[ft.dropdown.Option(key=c['hex'], text=c['nome']) for c in colori_liberi],
                value=cat_corrente.get('colore') if cat_corrente.get('colore') in [c['hex'] for c in colori_liberi] else colori_liberi[0]['hex']
            )

            def chiudi_mod_cat(e=None):
                try: page.close(dlg_modifica_cat)
                except AttributeError: dlg_modifica_cat.open = False; page.update()

            def salva_modifica_cat(e):
                txt_errore_mod.visible = False
                if dd_colore_mod.value:
                    try:
                        supabase.table('categorie').update({"colore": dd_colore_mod.value}).eq('id', cat_corrente['id']).execute()
                        page.pubsub.send_all("aggiorna_dati") # ⚡ SINCRONIZZA IL NUOVO COLORE CON GLI ALTRI
                        if on_success_callback: on_success_callback() 
                    except Exception as ex:
                        txt_errore_mod.value = f"⚠️ Errore modifica: {ex}"
                        txt_errore_mod.visible = True
                        page.update()
                        return
                chiudi_mod_cat()

            dlg_modifica_cat = ft.AlertDialog(
                title=ft.Text(f"Modifica colore {cat_corrente['nome']}", size=16, weight=ft.FontWeight.BOLD),
                content=ft.Column([dd_colore_mod, txt_errore_mod], tight=True),
                actions=[
                    ft.TextButton("Annulla", on_click=chiudi_mod_cat),
                    ft.Container(content=ft.Text("Aggiorna", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD), bgcolor=ft.Colors.ORANGE_800, padding=10, border_radius=5, on_click=salva_modifica_cat)
                ]
            )
            try: page.open(dlg_modifica_cat)
            except AttributeError: page.overlay.append(dlg_modifica_cat); dlg_modifica_cat.open = True; page.update()

        btn_edit_cat = ft.IconButton(icon=ft.Icons.EDIT, icon_color=ft.Colors.ORANGE_800, on_click=apri_modifica_categoria)
        btn_add_cat = ft.IconButton(icon=ft.Icons.ADD_CIRCLE, icon_color=ft.Colors.BLUE_900, on_click=apri_nuova_categoria)

        def salva_impegno(e):
            txt_errore_form.visible = False
            txt_titolo.error_text = None
            dd_categoria.error_text = None
            txt_data.error_text = None
            txt_inizio.error_text = None
            txt_fine.error_text = None

            # Puliamo i campi dal carattere invisibile prima di validare
            titolo_pulito = txt_titolo.value.replace("\u200b", "").strip() if txt_titolo.value else ""
            
            if not titolo_pulito or not dd_categoria.value:
                txt_errore_form.value = "⚠️ Attenzione: Titolo e Categoria sono obbligatori!"
                txt_errore_form.visible = True
                if not titolo_pulito: txt_titolo.error_text = "Obbligatorio"
                if not dd_categoria.value: dd_categoria.error_text = "Obbligatorio"
                page.update()
                return

            data_inserita = txt_data.value.replace("\u200b", "").strip() if txt_data.value else ""
            try:
                dt_obj = datetime.strptime(data_inserita, "%d/%m/%Y")
                data_db = dt_obj.strftime("%Y-%m-%d")
            except:
                txt_errore_form.value = "⚠️ Formato data non valido! Usa GG/MM/AAAA"
                txt_errore_form.visible = True
                txt_data.error_text = "Errore"
                page.update()
                return

            ora_i_pulita = txt_inizio.value.replace("\u200b", "").strip() if txt_inizio.value else ""
            ora_f_pulita = txt_fine.value.replace("\u200b", "").strip() if txt_fine.value else ""

            ora_inizio_db = ora_i_pulita if ora_i_pulita else None
            ora_fine_db = ora_f_pulita if ora_f_pulita else None

            if ora_inizio_db:
                try:
                    datetime.strptime(ora_inizio_db, "%H:%M")
                except:
                    txt_errore_form.value = "⚠️ Ora Inizio non valida! Usa HH:MM"
                    txt_errore_form.visible = True
                    txt_inizio.error_text = "Errore"
                    page.update()
                    return

            if ora_fine_db:
                try:
                    datetime.strptime(ora_fine_db, "%H:%M")
                except:
                    txt_errore_form.value = "⚠️ Ora Fine non valida! Usa HH:MM"
                    txt_errore_form.visible = True
                    txt_fine.error_text = "Errore"
                    page.update()
                    return

            # ⚡ BLOCCO ASSOLUTO: FINE DEVE ESSERE > DI INIZIO ⚡
            if ora_inizio_db and ora_fine_db:
                dt_i = datetime.strptime(ora_inizio_db, "%H:%M")
                dt_f = datetime.strptime(ora_fine_db, "%H:%M")
                if dt_f <= dt_i:
                    txt_errore_form.value = "⚠️ L'orario di fine deve essere successivo all'orario di inizio!"
                    txt_errore_form.visible = True
                    txt_inizio.error_text = "Errore"
                    txt_fine.error_text = "Errore"
                    page.update()
                    return

            dati_impegno = {
                "titolo": titolo_pulito,
                "ora_inizio": ora_inizio_db,
                "ora_fine": ora_fine_db,
                "id_categoria": dd_categoria.value,
                "note": txt_note.value.strip() if txt_note.value else None
            }

            # ⚡ GESTIONE SALVATAGGIO GIORNI MULTIPLI E PERIODICITA' ⚡
            if switch_periodico.value:
                str_giorni = ",".join(map(str, sorted(list(selezionati_giorni))))
                dati_impegno['giorni_periodici'] = str_giorni
                dati_impegno['is_periodico'] = True
            else:
                dati_impegno['giorni_periodici'] = None
                dati_impegno['is_periodico'] = False

            if impegno_esistente and is_p_db:
                dati_impegno['data_fine_periodo'] = impegno_esistente.get('data_fine_periodo')
            else:
                dati_impegno['data_fine_periodo'] = None

            try:
                # Controllo preventivo sovrapposizioni basato su ID relazionali
                familiari_selezionati = list(selezionati_famiglia)
                
                res_giorno = supabase.table('impegni').select('*, impegni_familiari(id_familiare)').eq('data_evento', data_db).execute()
                eventi_esistenti = res_giorno.data if res_giorno.data else []
                
                lista_controllo = []
                for ev in eventi_esistenti:
                    if impegno_esistente and str(ev['id']) == str(impegno_esistente['id']):
                        continue
                    
                    o_i = ev.get('ora_inizio') or "00:00"
                    o_f = ev.get('ora_fine') or "23:59"
                    
                    a_ids = [f['id_familiare'] for f in ev.get('impegni_familiari', [])]
                    lista_controllo.append({
                        'id': ev['id'],
                        'ora_inizio': o_i,
                        'ora_fine': o_f,
                        'assegnatari_ids': a_ids
                    })
                
                id_test_corrente = impegno_esistente['id'] if impegno_esistente else 'nuovo_temp'
                
                o_i_curr = ora_inizio_db if ora_inizio_db else "00:00"
                o_f_curr = ora_fine_db if ora_fine_db else "23:59"
                
                lista_controllo.append({
                    'id': id_test_corrente,
                    'ora_inizio': o_i_curr,
                    'ora_fine': o_f_curr,
                    'assegnatari_ids': familiari_selezionati
                })
                
                mappa_conflitti = analizza_sovrapposizioni_giornata(lista_controllo)
                esito_conflitto = mappa_conflitti.get(id_test_corrente)
                
                def esegui_scrittura_db():
                    from datetime import timedelta
                    
                    if impegno_esistente:
                        # 1. Recupero la VERA data di origine per non rovinare lo storico
                        vero_inizio = impegno_esistente['data_evento']
                        try:
                            res_orig = supabase.table('impegni').select('data_evento').eq('id', impegno_esistente['id']).execute()
                            if res_orig.data: vero_inizio = res_orig.data[0]['data_evento']
                        except: pass

                        if is_p_db and dd_modifica_periodico.value == "Solo questa data":
                            # REGOLA: Modifica solo l'evento selezionato, non fa vedere copie.
                            data_sel_str = data_selezionata.strftime("%Y-%m-%d")
                            nuove_escluse = date_escluse_db + "," + data_sel_str if date_escluse_db else data_sel_str
                            supabase.table('impegni').update({'date_escluse': nuove_escluse}).eq('id', impegno_esistente['id']).execute()

                            dati_impegno['data_evento'] = data_db
                            dati_impegno['is_periodico'] = False
                            dati_impegno['giorni_periodici'] = None
                            dati_impegno['date_escluse'] = None
                            dati_impegno['data_fine_periodo'] = None
                            res_imp = supabase.table('impegni').insert(dati_impegno).execute()
                            id_op = res_imp.data[0]['id']
                            
                        elif is_p_db and dd_modifica_periodico.value == "Questo e i successivi":
                            # REGOLA: Cambia tutti i successivi cristallizzando il passato.
                            data_sel_str = data_selezionata.strftime("%Y-%m-%d")
                            ieri = data_selezionata - timedelta(days=1)
                            ieri_str = ieri.strftime("%Y-%m-%d")
                            
                            dt_evento_orig = datetime.strptime(vero_inizio, "%Y-%m-%d")

                            if ieri < dt_evento_orig:
                                dati_impegno['data_evento'] = data_sel_str
                                supabase.table('impegni').update(dati_impegno).eq('id', impegno_esistente['id']).execute()
                                supabase.table('impegni_familiari').delete().eq('id_impegno', impegno_esistente['id']).execute()
                                id_op = impegno_esistente['id']
                            else:
                                supabase.table('impegni').update({'data_fine_periodo': ieri_str}).eq('id', impegno_esistente['id']).execute()
                                
                                dati_impegno['data_evento'] = data_sel_str
                                dati_impegno['data_fine_periodo'] = None 
                                dati_impegno['date_escluse'] = None
                                res_imp = supabase.table('impegni').insert(dati_impegno).execute()
                                id_op = res_imp.data[0]['id']
                                
                        else: 
                            # REGOLA: "Tutta la serie" (o se l'evento NON era periodico)
                            if is_p_db:
                                dati_impegno['data_evento'] = vero_inizio
                            else:
                                dati_impegno['data_evento'] = data_db
                                
                            supabase.table('impegni').update(dati_impegno).eq('id', impegno_esistente['id']).execute()
                            supabase.table('impegni_familiari').delete().eq('id_impegno', impegno_esistente['id']).execute()
                            id_op = impegno_esistente['id']
                    else:
                        dati_impegno["data_evento"] = data_db
                        dati_impegno['date_escluse'] = None
                        res_imp = supabase.table('impegni').insert(dati_impegno).execute()
                        id_op = res_imp.data[0]['id']

                    for id_fam in familiari_selezionati:
                        supabase.table('impegni_familiari').insert({"id_impegno": id_op, "id_familiare": id_fam}).execute()

                    chiudi_form(e)
                    if on_success_callback:
                        on_success_callback()

                if esito_conflitto:
                    testi_avviso = {
                        'lieve': "🟡 ⚠️ Rilevata Sovrapposizione Lieve (Gli impegni si susseguono con margine ≤ 5 min). Vuoi procedere?",
                        'media': "🟠 ⚠️ Rilevata Sovrapposizione Media (Orari sovrapposti > 5 min con assegnatari DIVERSI). Vuoi procedere?",
                        'grave': "🔴 ⚠️ ATTENZIONE: Sovrapposizione Grave! Orari sovrapposti con STESSI ASSEGNATARI. Vuoi procedere?"
                    }
                    
                    def conferma_conflitto(ev_click):
                        # 1. Chiude il popup di avviso
                        try: page.close(dlg_avviso_sovrapposizione)
                        except AttributeError: dlg_avviso_sovrapposizione.open = False
                        
                        # 2. Salva nel database
                        esegui_scrittura_db() 
                        
                        # 3. Forza la chiusura del popup principale dell'impegno
                        try: page.close(dlg_impegno)
                        except AttributeError: dlg_impegno.open = False
                        
                        page.update()

                    def annulla_conflitto(ev_click):
                        try: page.close(dlg_avviso_sovrapposizione)
                        except AttributeError: dlg_avviso_sovrapposizione.open = False

                    dlg_avviso_sovrapposizione = ft.AlertDialog(
                        title=ft.Text("Avviso Sovrapposizione", size=16, weight=ft.FontWeight.BOLD),
                        content=ft.Text(testi_avviso.get(esito_conflitto, "Attenzione alla sovrapposizione rilevata.")),
                        actions=[
                            ft.TextButton("Annulla", on_click=annulla_conflitto),
                            ft.Container(content=ft.Text("Procedi ugualmente", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD), bgcolor=ft.Colors.BLUE_900, padding=10, border_radius=5, on_click=conferma_conflitto)
                        ],
                        actions_alignment=ft.MainAxisAlignment.END
                    )
                    try: page.open(dlg_avviso_sovrapposizione)
                    except AttributeError: page.overlay.append(dlg_avviso_sovrapposizione); dlg_avviso_sovrapposizione.open = True; page.update()
                else:
                    esegui_scrittura_db()
                    
            except Exception as ex:
                txt_errore_form.value = f"⚠️ Errore salvataggio DB: {ex}"
                txt_errore_form.visible = True
                page.update()

        def esegui_elimina_singolo():
            try:
                data_sel_str = data_selezionata.strftime("%Y-%m-%d")
                nuove_escluse = date_escluse_db + "," + data_sel_str if date_escluse_db else data_sel_str
                supabase.table('impegni').update({'date_escluse': nuove_escluse}).eq('id', impegno_esistente['id']).execute()
                chiudi_form()
                if on_success_callback: on_success_callback()
            except Exception as ex: print(f"Errore: {ex}")

        def esegui_elimina_serie():
            try:
                supabase.table('impegni').delete().eq('id', impegno_esistente['id']).execute()
                chiudi_form()
                if on_success_callback: on_success_callback()
            except Exception as ex: print(f"Errore: {ex}")

        def chiedi_conferma(titolo, messaggio, funzione_esecutiva, avviso_rosso=False):
            nonlocal dlg_conferma
            
            def on_conferma_click(e):
                chiudi_conferma()
                funzione_esecutiva()
                
            dlg_conferma = ft.AlertDialog(
                title=ft.Text(titolo, size=16, weight=ft.FontWeight.BOLD),
                content=ft.Text(messaggio, color=ft.Colors.RED_700 if avviso_rosso else ft.Colors.BLACK),
                actions=[
                    ft.TextButton("Annulla", on_click=chiudi_conferma),
                    ft.Container(content=ft.Text("Conferma Elimina", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD), bgcolor=ft.Colors.RED_700, padding=10, border_radius=5, on_click=on_conferma_click)
                ],
                actions_alignment=ft.MainAxisAlignment.END
            )
            try: page.open(dlg_conferma)
            except AttributeError: page.overlay.append(dlg_conferma); dlg_conferma.open = True; page.update()

        def conferma_elimina_singolo(e):
            chiedi_conferma("Elimina Singolo Evento", "Sei sicuro di voler eliminare solo questo evento? Le altre ripetizioni della serie rimarranno intatte.", esegui_elimina_singolo)

        def conferma_elimina_serie(e):
            chiedi_conferma("Elimina Intera Serie", "ATTENZIONE: Sei sicuro di voler eliminare TUTTA la serie? Questo evento scomparirà definitivamente anche dal passato.", esegui_elimina_serie, avviso_rosso=True)

        def conferma_elimina_normale(e):
            chiedi_conferma("Elimina Evento", "Sei sicuro di voler eliminare questo evento?", esegui_elimina_serie)

        titolo_modal = "Modifica Impegno" if impegno_esistente else f"Nuovo Impegno"
        testo_btn_salva = "Aggiorna" if impegno_esistente else "Salva"

        btn_annulla = ft.TextButton("Annulla", on_click=chiudi_form)
        btn_salva = ft.Container(content=ft.Text(testo_btn_salva, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD), bgcolor=ft.Colors.BLUE_900, padding=10, border_radius=5, on_click=salva_impegno)
        
        # ⚡ PULSANTI TUTTI SU UNA STESSA LINEA ⚡
        if impegno_esistente:
            if is_p_db:
                btn_elimina_singolo = ft.Container(content=ft.Text("🗑️ Singolo", color=ft.Colors.WHITE, size=11), bgcolor=ft.Colors.RED_500, padding=8, border_radius=5, on_click=conferma_elimina_singolo)
                btn_elimina_serie = ft.Container(content=ft.Text("🗑️ Serie", color=ft.Colors.WHITE, size=11), bgcolor=ft.Colors.RED_900, padding=8, border_radius=5, on_click=conferma_elimina_serie)
                riga_azioni = ft.Row([ft.Row([btn_elimina_singolo, btn_elimina_serie], spacing=5), ft.Row([btn_annulla, btn_salva], spacing=5)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
            else:
                btn_elimina = ft.Container(content=ft.Text("🗑️ Elimina", color=ft.Colors.WHITE), bgcolor=ft.Colors.RED_700, padding=10, border_radius=5, on_click=conferma_elimina_normale)
                riga_azioni = ft.Row([btn_elimina, ft.Row([btn_annulla, btn_salva], spacing=5)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
        else:
            riga_azioni = ft.Row([btn_annulla, btn_salva], alignment=ft.MainAxisAlignment.END)

        colonna_contenuto = [
            txt_titolo,
            ft.Row([dd_categoria, ft.Row([btn_edit_cat, btn_add_cat], spacing=0)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            riga_date_orari,
            ft.Divider(height=1, color="#E0E0E0"),
            ft.Text("Assegna a:", weight=ft.FontWeight.BOLD, size=13),
            griglia_assegnatari,
            ft.Divider(height=1, color="#E0E0E0")
        ]
        
        if is_p_db:
            colonna_contenuto.append(dd_modifica_periodico)
            
        colonna_contenuto.extend([switch_periodico, riga_giorni, txt_avviso_periodico, txt_note, txt_errore_form])
        
        # ⚡ POPUP ESTESO DINAMICAMENTE AL MASSIMO DEL DISPLAY ⚡
        dlg_impegno = ft.AlertDialog(
            title=ft.Text(titolo_modal, size=16, weight=ft.FontWeight.BOLD),
            content=ft.Container(
                content=ft.Column(colonna_contenuto, tight=True, scroll=ft.ScrollMode.AUTO), 
                width=float('inf')  # ⚡ IL COMANDO CORRETTO PER L'ESTENSIONE MASSIMA
            ),
            actions=[riga_azioni],
            shape=ft.RoundedRectangleBorder(radius=10),
            content_padding=15
        )
        
        try: page.open(dlg_impegno)
        except AttributeError: page.overlay.append(dlg_impegno); dlg_impegno.open = True; page.update()
            
    except Exception as errore_generale:
        print(f"Errore grave form: {errore_generale}")
