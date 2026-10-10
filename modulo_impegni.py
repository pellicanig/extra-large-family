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

        # Recupero dati da Supabase
        res_cat = supabase.table('categorie').select('*').order('nome').execute()
        lista_categorie = res_cat.data if res_cat.data else []
        
        res_fam = supabase.table('famiglia').select('*').order('nome').execute()
        lista_famiglia = res_fam.data if res_fam.data else []

        # Estrazione sicura dei dati
        titolo_iniziale = impegno_esistente.get('titolo', "") if impegno_esistente else ""
        
        inizio_val = impegno_esistente.get('ora_inizio') if impegno_esistente else None
        inizio_iniziale = str(inizio_val)[:5] if inizio_val else ""
        
        fine_val = impegno_esistente.get('ora_fine') if impegno_esistente else None
        fine_iniziale = str(fine_val)[:5] if fine_val else ""
        
        note_iniziali = impegno_esistente.get('note', "") if (impegno_esistente and impegno_esistente.get('note')) else ""
        cat_iniziale = impegno_esistente.get('id_categoria') if impegno_esistente else None
        assegnatari_esistenti = impegno_esistente.get('assegnatari_ids', []) if impegno_esistente else []
        
        # ⚡ LOGICA INTELLIGENTE PERIODICITÀ ⚡
        is_p_db = False
        if impegno_esistente and impegno_esistente.get('is_periodico'):
            val = impegno_esistente.get('is_periodico')
            is_p_db = True if str(val).lower() in ['true', '1', 't', 'y', 'yes'] else False
            
        data_fine_db = impegno_esistente.get('data_fine_periodo') if impegno_esistente else None

        # L'interruttore appare acceso (ON) solo se l'evento è periodico e NON è stato bloccato prima di oggi
        switch_on_iniziale = False
        if is_p_db:
            if data_fine_db is None:
                switch_on_iniziale = True
            elif data_fine_db > data_selezionata.strftime("%Y-%m-%d"):
                switch_on_iniziale = True

        # Creazione dei campi del Form
        txt_titolo = ft.TextField(label="Titolo Impegno", value=titolo_iniziale, dense=True)
        txt_inizio = ft.TextField(label="Inizio", value=inizio_iniziale, dense=True, expand=True)
        txt_fine = ft.TextField(label="Fine", value=fine_iniziale, dense=True, expand=True)
        txt_note = ft.TextField(label="Note aggiuntive", value=note_iniziali, multiline=True, min_lines=2, max_lines=4)
        
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

        # ⚡ TESTO DI AVVISO DINAMICO ⚡
        data_str_it = f"{data_selezionata.day}/{data_selezionata.month}/{data_selezionata.year}"
        testo_avviso_iniziale = ""
        visibile_iniziale = False
        
        # Se era periodico ma la levetta è su OFF (quindi era già interrotto da questa data in poi)
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

        # Gestione interattiva della levetta
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

        def chiudi_form(e=None):
            if dlg_impegno:
                try:
                    page.close(dlg_impegno)
                except AttributeError:
                    dlg_impegno.open = False
                    page.update()

        def apri_nuova_categoria(e):
            nonlocal dlg_categoria
            txt_nuova_cat = ft.TextField(label="Nome Categoria", dense=True)
            dd_colore = ft.Dropdown(
                label="Colore", 
                dense=True,
                options=[
                    ft.dropdown.Option(key="#F44336", text="Rosso"),
                    ft.dropdown.Option(key="#4CAF50", text="Verde"),
                    ft.dropdown.Option(key="#448AFF", text="Blu"),
                    ft.dropdown.Option(key="#9C27B0", text="Viola"),
                    ft.dropdown.Option(key="#FF9800", text="Arancione"),
                ], 
                value="#448AFF"
            )

            def chiudi_cat(e=None):
                try:
                    page.close(dlg_categoria)
                except AttributeError:
                    dlg_categoria.open = False
                    page.update()

            def salva_categoria(e):
                nome_cat = txt_nuova_cat.value.strip().upper()
                if nome_cat:
                    try:
                        res = supabase.table('categorie').insert({"nome": nome_cat, "colore": dd_colore.value}).execute()
                        nuovo_id = res.data[0]['id']
                        dd_categoria.options.append(ft.dropdown.Option(key=nuovo_id, text=nome_cat))
                        dd_categoria.value = nuovo_id
                        dd_categoria.update()
                    except Exception as ex:
                        print(f"Errore salvataggio categoria: {ex}")
                chiudi_cat()

            dlg_categoria = ft.AlertDialog(
                title=ft.Text("Nuova Categoria", size=16, weight=ft.FontWeight.BOLD),
                content=ft.Column([txt_nuova_cat, dd_colore], tight=True),
                actions=[
                    ft.TextButton("Annulla", on_click=chiudi_cat),
                    ft.Container(content=ft.Text("Salva", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD), bgcolor=ft.Colors.BLUE_900, padding=10, border_radius=5, on_click=salva_categoria)
                ]
            )
            try:
                page.open(dlg_categoria)
            except AttributeError:
                page.overlay.append(dlg_categoria)
                dlg_categoria.open = True
                page.update()

        btn_add_cat = ft.IconButton(icon=ft.Icons.ADD_CIRCLE, icon_color=ft.Colors.BLUE_900, on_click=apri_nuova_categoria)

        def salva_impegno(e):
            if not txt_titolo.value or not dd_categoria.value:
                txt_titolo.error_text = "Obbligatorio" if not txt_titolo.value else None
                dd_categoria.error_text = "Obbligatorio" if not dd_categoria.value else None
                page.update()
                return

            dati_impegno = {
                "titolo": txt_titolo.value.strip(),
                "ora_inizio": txt_inizio.value.strip() if txt_inizio.value else None,
                "ora_fine": txt_fine.value.strip() if txt_fine.value else None,
                "id_categoria": dd_categoria.value,
                "note": txt_note.value.strip() if txt_note.value else None
            }

            # ⚡ IL CERVELLO DELLA PERIODICITÀ E DEL MURO TEMPORALE ⚡
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
                    supabase.table('impegni').update(dati_impegno).eq('id', impegno_esistente['id']).execute()
                    supabase.table('impegni_familiari').delete().eq('id_impegno', impegno_esistente['id']).execute()
                    id_operazione = impegno_esistente['id']
                else:
                    dati_impegno["data_evento"] = data_selezionata.strftime("%Y-%m-%d")
                    res_impegno = supabase.table('impegni').insert(dati_impegno).execute()
                    id_operazione = res_impegno.data[0]['id']

                familiari_selezionati = [cb.data for cb in checkboxes_famiglia if cb.value]
                for id_fam in familiari_selezionati:
                    supabase.table('impegni_familiari').insert({"id_impegno": id_operazione, "id_familiare": id_fam}).execute()

                chiudi_form(e) 
                if on_success_callback:
                    on_success_callback()
                    
            except Exception as ex:
                print(f"Errore di salvataggio impegno: {ex}")

        def elimina_impegno(e):
            try:
                supabase.table('impegni').delete().eq('id', impegno_esistente['id']).execute()
                chiudi_form(e)
                if on_success_callback:
                    on_success_callback()
            except Exception as ex:
                print(f"Errore eliminazione: {ex}")

        titolo_modal = "Modifica Impegno" if impegno_esistente else f"Nuovo Impegno"
        testo_btn_salva = "Aggiorna" if impegno_esistente else "Salva"

        btn_annulla = ft.TextButton("Annulla", on_click=chiudi_form)
        btn_salva = ft.Container(content=ft.Text(testo_btn_salva, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD), bgcolor=ft.Colors.BLUE_900, padding=10, border_radius=5, on_click=salva_impegno)
        
        if impegno_esistente:
            btn_elimina = ft.Container(content=ft.Text("🗑️", color=ft.Colors.WHITE), bgcolor=ft.Colors.RED_700, padding=10, border_radius=5, on_click=elimina_impegno)
            riga_azioni = ft.Row(
                [btn_elimina, ft.Row([btn_annulla, btn_salva], spacing=5)], 
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN, 
                wrap=True
            )
        else:
            riga_azioni = ft.Row(
                [btn_annulla, btn_salva], 
                alignment=ft.MainAxisAlignment.END, 
                wrap=True
            )

        colonna_contenuto = [
            txt_titolo,
            ft.Row([dd_categoria, btn_add_cat], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ft.Row([txt_inizio, txt_fine], spacing=10),
            ft.Divider(height=1, color="#E0E0E0"),
            ft.Text("Assegna a:", weight=ft.FontWeight.BOLD, size=13),
            ft.Column(checkboxes_famiglia, spacing=2),
            ft.Divider(height=1, color="#E0E0E0"),
            switch_periodico,
            txt_avviso_periodico, # ⚡ IL NUOVO MESSAGGIO INFORMATIVO QUI ⚡
            txt_note
        ]
        
        dlg_impegno = ft.AlertDialog(
            title=ft.Text(titolo_modal, size=16, weight=ft.FontWeight.BOLD),
            content=ft.Column(colonna_contenuto, tight=True, scroll=ft.ScrollMode.AUTO),
            actions=[riga_azioni]
        )
        
        try:
            page.open(dlg_impegno)
        except AttributeError:
            page.overlay.append(dlg_impegno)
            dlg_impegno.open = True
            page.update()
            
    except Exception as errore_generale:
        print(f"Errore grave form: {errore_generale}")
        snack = ft.SnackBar(ft.Text(f"Errore di sistema: {errore_generale}"))
        try:
            page.open(snack)
        except AttributeError:
            page.overlay.append(snack)
            snack.open = True
            page.update()