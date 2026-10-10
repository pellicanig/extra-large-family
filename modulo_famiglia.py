import flet as ft

def vista_famiglia(page: ft.Page, supabase):
    
    # 1. Variabile per tenere in memoria l'ID del componente che stiamo modificando (se Nessuno, stiamo inserendo)
    id_in_modifica = [None]

    # 2. Campi di input
    nome_input = ft.TextField(label="Nome e Cognome", expand=True)
    nascita_input = ft.TextField(label="Data di nascita (es. 12/05/1990)", expand=True)
    ruolo_input = ft.Dropdown(
        label="Ruolo nella famiglia",
        options=[
            ft.dropdown.Option("Padre"),
            ft.dropdown.Option("Madre"),
            ft.dropdown.Option("Figlio/a"),
            ft.dropdown.Option("Altro")
        ],
        expand=True
    )

    lista_componenti_ui = ft.ListView(expand=True, spacing=10, auto_scroll=True)

    # 3. Bottoni creati separatamente per poterne cambiare il colore e il testo durante la modifica
    bottone_salva = ft.Button(
        "Salva nel Database",
        icon=ft.Icons.SAVE,
        color=ft.Colors.WHITE,
        bgcolor=ft.Colors.BLUE_900,
        width=float("inf")
    )
    
    bottone_annulla = ft.Button(
        "Annulla Modifica",
        icon=ft.Icons.CANCEL,
        color=ft.Colors.WHITE,
        bgcolor=ft.Colors.GREY_700,
        width=float("inf"),
        visible=False # Inizialmente nascosto
    )

    # FUNZIONE PER ANNULLARE LA MODIFICA E SVUOTARE I CAMPI
    def svuota_e_ripristina(e=None):
        id_in_modifica[0] = None
        nome_input.value = ""
        nascita_input.value = ""
        ruolo_input.value = None
        
        # Riporta il bottone al suo stato originale (blu per salvare)
        bottone_salva.text = "Salva nel Database"
        bottone_salva.icon = ft.Icons.SAVE
        bottone_salva.bgcolor = ft.Colors.BLUE_900
        bottone_annulla.visible = False
        page.update()

    bottone_annulla.on_click = svuota_e_ripristina

    # FUNZIONE PER CARICARE I DATI DA SUPABASE
    def carica_dati():
        lista_componenti_ui.controls.clear()
        try:
            # Ordiniamo i dati per data di creazione, così non cambiano posizione dopo una modifica
            risposta = supabase.table("famiglia").select("*").order("creato_il").execute()
            dati = risposta.data
            
            for componente in dati:
                crea_scheda_ui(
                    componente['id'], 
                    componente['nome'], 
                    componente.get('ruolo'), 
                    componente.get('data_nascita')
                )
        except Exception as err:
            print("Errore caricamento dati:", err)
        page.update()

    # FUNZIONE PER DISEGNARE LA SCHEDA CON MATITA E CESTINO
    def crea_scheda_ui(db_id, nome, ruolo, data_nascita):
        
        def elimina_dal_db(e):
             try:
                 supabase.table("famiglia").delete().eq("id", db_id).execute()
                 
                 # Se stiamo eliminando proprio il componente che avevamo messo in modifica, svuotiamo i campi
                 if id_in_modifica[0] == db_id:
                     svuota_e_ripristina()
                     
                 carica_dati()
                 snack = ft.SnackBar(content=ft.Text(value=f"{nome} eliminato!"))
                 page.overlay.append(snack)
                 snack.open = True
                 page.update()
             except Exception as err:
                 print("Errore cancellazione:", err)
        
        # Questa funzione prepara il modulo per l'aggiornamento
        def avvia_modifica(e):
            id_in_modifica[0] = db_id
            nome_input.value = nome
            nascita_input.value = data_nascita or ""
            ruolo_input.value = ruolo
            
            # Trasforma il pulsante blu in un pulsante arancione di avvertimento
            bottone_salva.text = "Aggiorna Componente"
            bottone_salva.icon = ft.Icons.EDIT
            bottone_salva.bgcolor = ft.Colors.ORANGE_800
            bottone_annulla.visible = True
            page.update()

        scheda = ft.ListTile(
            leading=ft.Icon(ft.Icons.PERSON),
            title=ft.Text(f"{nome} ({ruolo or 'Nessun ruolo'})"),
            subtitle=ft.Text(f"Nato/a il: {data_nascita or 'Sconosciuta'}"),
            # Inseriamo entrambi i pulsanti (matita e cestino) sulla stessa riga a destra
            trailing=ft.Row(
                controls=[
                    ft.IconButton(ft.Icons.EDIT, icon_color="blue", on_click=avvia_modifica),
                    ft.IconButton(ft.Icons.DELETE, icon_color="red", on_click=elimina_dal_db)
                ],
                width=100,
                alignment=ft.MainAxisAlignment.END
            )
        )
        lista_componenti_ui.controls.append(scheda)

    # FUNZIONE PER INSERIRE O AGGIORNARE IN SUPABASE
    def salva_componente(e):
        if nome_input.value:
            try:
                record = {
                    "nome": nome_input.value,
                    "ruolo": ruolo_input.value,
                    "data_nascita": nascita_input.value
                }
                
                if id_in_modifica[0] is None:
                    # Inserimento (se non stiamo modificando nessuno)
                    supabase.table("famiglia").insert(record).execute()
                    msg = f"{nome_input.value} salvato nel database!"
                else:
                    # Aggiornamento (se stiamo modificando)
                    supabase.table("famiglia").update(record).eq("id", id_in_modifica[0]).execute()
                    msg = f"{nome_input.value} aggiornato con successo!"
                
                # Ricarichiamo la lista e resettiamo il modulo
                carica_dati()
                svuota_e_ripristina()
                
                snack = ft.SnackBar(content=ft.Text(value=msg))
                page.overlay.append(snack)
                snack.open = True
                page.update()
                
            except Exception as err:
                snack = ft.SnackBar(content=ft.Text(value=f"Errore DB: {err}", color="white"), bgcolor="red")
                page.overlay.append(snack)
                snack.open = True
                page.update()
        else:
            snack = ft.SnackBar(content=ft.Text(value="Il nome è obbligatorio!", color="white"), bgcolor="red")
            page.overlay.append(snack)
            snack.open = True
            page.update()

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
            ft.AppBar(
                title=ft.Text(value="Gestione Famiglia"), 
                bgcolor=ft.Colors.BLUE_900,
                color=ft.Colors.WHITE,
                leading=ft.IconButton(icon=ft.Icons.ARROW_BACK, icon_color=ft.Colors.WHITE, on_click=torna_home)
            ),
            ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Text(value="Dati Componente", size=20, weight=ft.FontWeight.BOLD),
                        ft.Divider(height=5, color="transparent"),
                        nome_input,
                        nascita_input,
                        ruolo_input,
                        ft.Divider(height=10, color="transparent"),
                        
                        # Inseriamo i bottoni dinamici
                        bottone_salva,
                        bottone_annulla,
                        
                        ft.Divider(height=20, color="#E0E0E0"),
                        
                        ft.Text(value="Componenti Salvati", size=20, weight=ft.FontWeight.BOLD),
                        ft.Container(
                            content=lista_componenti_ui,
                            height=250, 
                            bgcolor="#F5F5F5",
                            border_radius=10,
                            padding=10
                        )
                    ]
                ),
                padding=15
            )
        ],
        padding=0,
        scroll=ft.ScrollMode.AUTO
    )