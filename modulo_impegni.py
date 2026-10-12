import flet as ft
import os
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client, Client

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

        titolo_iniziale = impegno_esistente.get('titolo', "") if impegno_esistente else ""
        
        inizio_val = impegno_esistente.get('ora_inizio') if impegno_esistente else None
        inizio_iniziale = str(inizio_val)[:5] if inizio_val else ""
        
        fine_val = impegno_esistente.get('ora_fine') if impegno_esistente else None
        fine_iniziale = str(fine_val)[:5] if fine_val else ""
        
        note_iniziali = impegno_esistente.get('note', "") if (impegno_esistente and impegno_esistente.get('note')) else ""
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

        txt_titolo = ft.TextField(label="Titolo Impegno", value=titolo_iniziale, dense=True)
        
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
            if txt_inizio.value != risultato:
                txt_inizio.value = risultato
                txt_inizio.error_text = None
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

        # ⚡ ETICHETTE ESTERNE, DATA PIÙ STRETTA E HINT INTERNI ⚡
        # Rimuoviamo il parametro 'label' e lasciamo solo 'hint_text'
        txt_data = ft.TextField(hint_text="GG/MM/AAAA", value=data_visualizzata_iniziale, dense=True, text_size=13, on_change=formatta_data_input, text_align=ft.TextAlign.CENTER)
        txt_inizio = ft.TextField(hint_text="HH:MM", value=inizio_iniziale, dense=True, text_size=13, on_change=formatta_ora_inizio, text_align=ft.TextAlign.CENTER)
        txt_fine = ft.TextField(hint_text="HH:MM", value=fine_iniziale, dense=True, text_size=13, on_change=formatta_ora_fine, text_align=ft.TextAlign.CENTER)
        
        # Creiamo le colonne per posizionare il testo in piccolo sopra ai campi. 
        # La data avrà larghezza fissa a 110px, orari in expand.
        col_data = ft.Column([ft.Text("Data", size=11, color=ft.Colors.GREY_700, weight=ft.FontWeight.BOLD), txt_data], spacing=2, width=110)
        col_inizio = ft.Column([ft.Text("Inizio", size=11, color=ft.Colors.GREY_700, weight=ft.FontWeight.BOLD), txt_inizio], spacing=2, expand=1)
        col_fine = ft.Column([ft.Text("Fine", size=11, color=ft.Colors.GREY_700, weight=ft.FontWeight.BOLD), txt_fine], spacing=2, expand=1)
        
        txt_note = ft.TextField(label="Note aggiuntive", value=note_iniziali, multiline=True, min_lines=2, max_lines=4)
        
        txt_errore_form = ft.Text(value="", color=ft.Colors.RED_700, size=13, weight=ft.FontWeight.BOLD, visible=False)

        dd_categoria = ft.Dropdown(
            label="Categoria (*)", 
            value=cat_iniziale,
            options=[ft.dropdown.Option(key=c['id'], text=c['nome']) for c in lista_categorie],
            dense=True, 
            expand=True
        )

        checkboxes_famiglia = [
            ft.Checkbox(label=f['nome'], data=f['id'], value=(f['id'] in assegnatari_esistenti)) for f in lista_famiglia
        ]

        dd_modifica_periodico = ft.Dropdown(
            label="Applica modifiche a:",
            options=[ft.dropdown.Option("Solo questa data"), ft.dropdown.Option("Tutta la serie")],
            value="Solo questa data",
            dense=True,
            visible=(impegno_esistente is not None and is_p_db)
        )

        data_str_it = f"{data_selezionata.day}/{data_selezionata.month}/{data_selezionata.year}"
        testo_avviso_iniziale = ""
        visibile_iniziale = False
        
        if is_p_db and not switch_on_iniziale:
            testo_avviso_iniziale = f"🛑 Periodicità interrotta a partire dal {data_str_it}"
            visibile_iniziale = True

        txt_avviso_periodico = ft.Text(
            value=testo_avviso_iniziale,
            color=ft.Colors.RED_700,
            size=12,
            weight=ft.FontWeight.BOLD,
            italic=True,
            visible=visibile_iniziale
        )

        def cambia_stato_periodico(e):
            if impegno_esistente and is_p_db:
                if not switch_periodico.value:
                    txt_avviso_periodico.value = f"🛑 Periodicità interrotta a partire dal {data_str_it}"
                    txt_avviso_periodico.visible = True
                else:
                    txt_avviso_periodico.visible = False
                txt_avviso_periodico.update()

        switch_periodico = ft.Switch(
            label="Ripeti ogni settimana", 
            value=switch_on_iniziale,
            on_change=cambia_stato_periodico
        )

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

            if not txt_titolo.value or not dd_categoria.value:
                txt_errore_form.value = "⚠️ Attenzione: Titolo e Categoria sono obbligatori!"
                txt_errore_form.visible = True
                if not txt_titolo.value: txt_titolo.error_text = "Obbligatorio"
                if not dd_categoria.value: dd_categoria.error_text = "Obbligatorio"
                page.update()
                return

            data_inserita = txt_data.value.strip()
            try:
                dt_obj = datetime.strptime(data_inserita, "%d/%m/%Y")
                data_db = dt_obj.strftime("%Y-%m-%d")
            except:
                txt_errore_form.value = "⚠️ Formato data non valido! Usa GG/MM/AAAA"
                txt_errore_form.visible = True
                txt_data.error_text = "Errore"
                page.update()
                return

            ora_inizio_db = txt_inizio.value.strip() if txt_inizio.value else None
            ora_fine_db = txt_fine.value.strip() if txt_fine.value else None

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

            dati_impegno = {
                "titolo": txt_titolo.value.strip(),
                "ora_inizio": ora_inizio_db,
                "ora_fine": ora_fine_db,
                "id_categoria": dd_categoria.value,
                "note": txt_note.value.strip() if txt_note.value else None
            }

            if switch_periodico.value != switch_on_iniziale:
                if switch_periodico.value == True:
                    dati_impegno['is_periodico'] = True
                    dati_impegno['data_fine_periodo'] = None
                else:
                    if impegno_esistente and is_p_db:
                        dati_impegno['is_periodico'] = True
                        dati_impegno['data_fine_periodo'] = data_selezionata.strftime("%Y-%m-%d")
                    else:
                        dati_impegno['is_periodico'] = False
                        dati_impegno['data_fine_periodo'] = None
            else:
                if not impegno_esistente:
                    dati_impegno['is_periodico'] = switch_periodico.value
                    dati_impegno['data_fine_periodo'] = None

            try:
                if impegno_esistente:
                    if is_p_db and dd_modifica_periodico.value == "Solo questa data":
                        data_sel_str = data_selezionata.strftime("%Y-%m-%d")
                        nuove_escluse = date_escluse_db + "," + data_sel_str if date_escluse_db else data_sel_str
                        supabase.table('impegni').update({'date_escluse': nuove_escluse}).eq('id', impegno_esistente['id']).execute()

                        dati_impegno['data_evento'] = data_db
                        dati_impegno['is_periodico'] = False
                        dati_impegno['data_fine_periodo'] = None
                        res_impegno = supabase.table('impegni').insert(dati_impegno).execute()
                        id_operazione = res_impegno.data[0]['id']
                    else:
                        if not is_p_db:
                            dati_impegno['data_evento'] = data_db
                        supabase.table('impegni').update(dati_impegno).eq('id', impegno_esistente['id']).execute()
                        supabase.table('impegni_familiari').delete().eq('id_impegno', impegno_esistente['id']).execute()
                        id_operazione = impegno_esistente['id']
                else:
                    dati_impegno["data_evento"] = data_db
                    res_impegno = supabase.table('impegni').insert(dati_impegno).execute()
                    id_operazione = res_impegno.data[0]['id']

                familiari_selezionati = [cb.data for cb in checkboxes_famiglia if cb.value]
                for id_fam in familiari_selezionati:
                    supabase.table('impegni_familiari').insert({"id_impegno": id_operazione, "id_familiare": id_fam}).execute()

                chiudi_form(e) 
                if on_success_callback:
                    on_success_callback()
                    
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
        
        if impegno_esistente:
            if is_p_db:
                riga_azioni = ft.Row([
                    ft.Row([
                        ft.Container(content=ft.Text("🗑️ Singolo", color=ft.Colors.WHITE, size=11), bgcolor=ft.Colors.RED_500, padding=8, border_radius=5, on_click=conferma_elimina_singolo),
                        ft.Container(content=ft.Text("🗑️ Serie", color=ft.Colors.WHITE, size=11), bgcolor=ft.Colors.RED_900, padding=8, border_radius=5, on_click=conferma_elimina_serie),
                    ], spacing=5, wrap=True),
                    ft.Row([btn_annulla, btn_salva], spacing=5)
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, wrap=True)
            else:
                btn_elimina = ft.Container(content=ft.Text("🗑️ Elimina", color=ft.Colors.WHITE), bgcolor=ft.Colors.RED_700, padding=10, border_radius=5, on_click=conferma_elimina_normale)
                riga_azioni = ft.Row([btn_elimina, ft.Row([btn_annulla, btn_salva], spacing=5)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, wrap=True)
        else:
            riga_azioni = ft.Row([btn_annulla, btn_salva], alignment=ft.MainAxisAlignment.END, wrap=True)

        colonna_contenuto = [
            txt_titolo,
            ft.Row([dd_categoria, ft.Row([btn_edit_cat, btn_add_cat], spacing=0)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ft.Row([col_data, col_inizio, col_fine], spacing=5),
            ft.Divider(height=1, color="#E0E0E0"),
            ft.Text("Assegna a:", weight=ft.FontWeight.BOLD, size=13),
            ft.Column(checkboxes_famiglia, spacing=2),
            ft.Divider(height=1, color="#E0E0E0")
        ]
        
        if is_p_db:
            colonna_contenuto.append(dd_modifica_periodico)
            
        colonna_contenuto.extend([switch_periodico, txt_avviso_periodico, txt_note, txt_errore_form])
        
        dlg_impegno = ft.AlertDialog(
            title=ft.Text(titolo_modal, size=16, weight=ft.FontWeight.BOLD),
            content=ft.Column(colonna_contenuto, tight=True, scroll=ft.ScrollMode.AUTO),
            actions=[riga_azioni]
        )
        
        try: page.open(dlg_impegno)
        except AttributeError: page.overlay.append(dlg_impegno); dlg_impegno.open = True; page.update()
            
    except Exception as errore_generale:
        print(f"Errore grave form: {errore_generale}")
