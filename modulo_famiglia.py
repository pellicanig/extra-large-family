import flet as ft

def vista_famiglia(page: ft.Page):
    # Campo di input per il nome
    nome_input = ft.TextField(label="Nome del componente", width=300)

    def salva_componente(e):
        if nome_input.value:
            # Qui in futuro inseriremo la chiamata a Supabase
            print(f"Pronto per salvare '{nome_input.value}' su Supabase!")
            
            # Mostra un messaggio di conferma a schermo
            page.snack_bar = ft.SnackBar(ft.Text(f"Componente '{nome_input.value}' aggiunto con successo!"))
            page.snack_bar.open = True
            
            # Svuota il campo di testo
            nome_input.value = ""
            page.update()
        else:
            # Mostra un messaggio di errore
            page.snack_bar = ft.SnackBar(ft.Text("Il campo nome non può essere vuoto."))
            page.snack_bar.open = True
            page.update()

    # Costruisce l'interfaccia di questa pagina
    return ft.View(
        "/famiglia",
        [
            ft.AppBar(title=ft.Text("Gestione Famiglia"), bgcolor=ft.colors.SURFACE_VARIANT),
            ft.Text("Inserisci un nuovo componente", size=20, weight=ft.FontWeight.BOLD),
            nome_input,
            ft.ElevatedButton("Salva Componente", on_click=salva_componente),
            ft.ElevatedButton("Torna alla Schermata Principale", on_click=lambda _: page.go("/"))
        ]
    )