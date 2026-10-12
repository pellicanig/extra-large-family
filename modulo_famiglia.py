import flet as ft
import logging
from datetime import datetime

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

def vista_famiglia(page: ft.Page, supabase):
    id_in_modifica = [None]
    dati_famiglia_memoria = []
    nome_input = ft.TextField(label="Nome e Cognome", expand=True)
    
    def formatta_data_nascita(e):
        valore_pulito = "".join([c for c in nascita_input.value if c.isdigit()])
        risultato = ""
        for i, num in enumerate(valore_pulito):
            if i == 2 or i == 4: risultato += "/"
            risultato += num
        risultato = risultato[:10] 
        if nascita_input.value != risultato:
            nascita_input.value = risultato
            nascita_input.error_text = None
            page.update()

    nascita_input = ft.TextField(label="Data di nascita (es. 12/05/1990)", expand=True, on_change=formatta_data_nascita)
    ruolo_input = ft.Dropdown(
        label="Ruolo nella famiglia",
        options=[ft.dropdown.Option("Padre"), ft.dropdown.Option("Madre"), ft.dropdown.Option("Figlio/a"), ft.dropdown.Option("Altro")],
        expand=True
    )
    colore_input = ft.Dropdown(label="Colore", expand=True)
    txt_errore_form = ft.Text(value="", color=ft.Colors.RED_700, size=13, weight=ft.FontWeight.BOLD, visible=False)
    lista_componenti_ui = ft.ListView(expand=True, spacing=10, auto_scroll=True)

    bottone_salva = ft.Button("Salva nel Database", icon=ft.Icons.SAVE, color=ft.Colors.WHITE, bgcolor=ft.Colors.BLUE_900, width=float("inf"))
    bottone_annulla = ft.Button("Annulla Modifica", icon=ft.Icons.CANCEL, color=ft.Colors.WHITE, bgcolor=ft.Colors.GREY_700, width=float("inf"), visible=False)
    
    def aggiorna_colori_disponibili():
        colori_occupati = []
        for membro in dati_famiglia_memoria:
            if membro.get('colore') and membro['id'] != id_in_modifica[0]:
                colori_occupati.append(membro['colore'])

        colori_liberi = [c for c in PALETTE_COLORI if c['hex'] not in colori_occupati]
        if not colori_liberi: colori_liberi = PALETTE_COLORI
            
        colore_input.options = [ft.dropdown.Option(key=c['hex'], text=c['nome']) for c in colori_liberi]
        if colori_liberi and id_in_modifica[0] is None:
            colore_input.value = colori_liberi[0]['hex']

    def svuota_e_ripristina(e=None):
        id_in_modifica[0] = None
        nome_input.value = ""
        nome_input.error_text = None
        nascita_input.value = ""
        nascita_input.error_text = None
        ruolo_input.value = None
        txt_errore_form.visible = False
        colore_input.value = None
        aggiorna_colori_disponibili()
        bottone_salva.text = "Salva nel Database"
        bottone_salva.icon = ft.Icons.SAVE
        bottone_salva.bgcolor = ft.Colors.BLUE_900
        bottone_annulla.visible = False
        page.update()

    bottone_annulla.on_click = svuota_e_ripristina

    def carica_dati():
        nonlocal dati_famiglia_memoria
        lista_componenti_ui.controls.clear()
        try:
            risposta = supabase.table("famiglia").select("*").order("creato_il").execute()
            dati_famiglia_memoria = risposta.data
            aggiorna_colori_disponibili()
            for componente in dati_famiglia_memoria:
                crea_scheda_ui(componente['id'], componente['nome'], componente.get('ruolo'), componente.get('data_nascita'), componente.get('colore'))
        except Exception as err:
            logging.error(f"Errore caricamento dati famiglia: {err}")
        page.update()

    page.aggiorna_vista_corrente = carica_dati

    def crea_scheda_ui(db_id, nome, ruolo, data_nascita, colore_assegnato):
        def elimina_dal_db(e):
             try:
                 supabase.table("famiglia").delete().eq("id", db_id).execute()
                 logging.info(f"Eliminato componente famiglia: {nome} (ID: {db_id})")
                 if id_in_modifica[0] == db_id: svuota_e_ripristina()
                 carica_dati()
                 page.pubsub.send_all("aggiorna_dati")
                 snack = ft.SnackBar(content=ft.Text(value=f"{nome} eliminato!"))
                 try: page.open(snack)
                 except AttributeError: page.overlay.append(snack); snack.open = True; page.update()
             except Exception as err: 
                 logging.error(f"Errore cancellazione famiglia: {err}")
        
        def avvia_modifica(e):
            id_in_modifica[0] = db_id
            nome_input.value = nome
            nascita_input.value = data_nascita or ""
            ruolo_input.value = ruolo
            aggiorna_colori_disponibili()
            colore_input.value = colore_assegnato
            bottone_salva.text = "Aggiorna Componente"
            bottone_salva.icon = ft.Icons.EDIT
            bottone_salva.bgcolor = ft.Colors.ORANGE_800
            bottone_annulla.visible = True
            page.update()

        colore_effettivo = colore_assegnato if colore_assegnato else "#9E9E9E"
        icona_profilo = ft.CircleAvatar(content=ft.Icon(ft.Icons.PERSON, color=ft.Colors.WHITE), bgcolor=colore_effettivo)
        scheda = ft.ListTile(
            leading=icona_profilo,
            title=ft.Text(f"{nome} ({ruolo or 'Nessun ruolo'})"),
            subtitle=ft.Text(f"Nato/a il: {data_nascita or 'Sconosciuta'}"),
            trailing=ft.Row(controls=[ft.IconButton(ft.Icons.EDIT, icon_color="blue", on_click=avvia_modifica), ft.IconButton(ft.Icons.DELETE, icon_color="red", on_click=elimina_dal_db)], width=100, alignment=ft.MainAxisAlignment.END)
        )
        lista_componenti_ui.controls.append(scheda)

    def salva_componente(e):
        txt_errore_form.visible = False
        nome_input.error_text = None
        nascita_input.error_text = None
        if not nome_input.value:
            nome_input.error_text = "Obbligatorio"
            txt_errore_form.value = "⚠️ Attenzione: Il nome è obbligatorio."
            txt_errore_form.visible = True
            page.update()
            return
        if nascita_input.value:
            try: datetime.strptime(nascita_input.value, "%d/%m/%Y")
            except ValueError:
                nascita_input.error_text = "Errore"
                txt_errore_form.value = "⚠️ Formato data non valido! Usa GG/MM/AAAA"
                txt_errore_form.visible = True
                page.update()
                return
        try:
            record = {"nome": nome_input.value, "ruolo": ruolo_input.value, "data_nascita": nascita_input.value, "colore": colore_input.value}
            if id_in_modifica[0] is None:
                supabase.table("famiglia").insert(record).execute()
                logging.info(f"Aggiunto nuovo componente famiglia: {nome_input.value} (Ruolo: {ruolo_input.value})")
                msg = f"{nome_input.value} salvato nel database!"
            else:
                supabase.table("famiglia").update(record).eq("id", id_in_modifica[0]).execute()
                logging.info(f"Modificato componente famiglia: {nome_input.value} (ID: {id_in_modifica[0]})")
                msg = f"{nome_input.value} aggiornato con successo!"
            
            carica_dati()
            svuota_e_ripristina()
            page.pubsub.send_all("aggiorna_dati")
            
            snack = ft.SnackBar(content=ft.Text(value=msg))
            try: page.open(snack)
            except AttributeError: page.overlay.append(snack); snack.open = True; page.update()
            
        except Exception as err:
            logging.error(f"Errore DB salvataggio famiglia: {err}")
            snack = ft.SnackBar(content=ft.Text(value=f"Errore DB: {err}", color="white"), bgcolor="red")
            try: page.open(snack)
            except AttributeError: page.overlay.append(snack); snack.open = True; page.update()

    bottone_salva.on_click = salva_componente

    def torna_home(e):
        page.views.pop()
        if page.views:
            page.route = page.views[-1].route
        page.update()

    carica_dati()

    return ft.View(
        route="/famiglia",
        controls=[
            ft.AppBar(title=ft.Text(value="Gestione Famiglia"), bgcolor=ft.Colors.BLUE_900, color=ft.Colors.WHITE, leading=ft.IconButton(icon=ft.Icons.ARROW_BACK, icon_color=ft.Colors.WHITE, on_click=torna_home)),
            ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Text(value="Dati Componente", size=20, weight=ft.FontWeight.BOLD),
                        ft.Divider(height=5, color="transparent"),
                        nome_input, nascita_input, ft.Row([ruolo_input, colore_input]),
                        ft.Divider(height=10, color="transparent"),
                        txt_errore_form, bottone_salva, bottone_annulla,
                        ft.Divider(height=20, color="#E0E0E0"),
                        ft.Text(value="Componenti Salvati", size=20, weight=ft.FontWeight.BOLD),
                        ft.Container(content=lista_componenti_ui, height=250, bgcolor="#F5F5F5", border_radius=10, padding=10)
                    ]
                ),
                padding=15
            )
        ],
        padding=0, scroll=ft.ScrollMode.AUTO
    )
