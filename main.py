import flet as ft
import os
import modulo_famiglia

def main(page: ft.Page):
    page.title = "App Famiglia"
    
    # Funzione per gestire i cambi di pagina (Routing)
    def route_change(route):
        page.views.clear()
        
        # 1. Costruiamo sempre la pagina "Home" di base
        page.views.append(
            ft.View(
                "/",
                [
                    ft.AppBar(
                        title=ft.Text("Schermata Principale"), 
                        bgcolor=ft.colors.BLUE_GREY_900,
                        color=ft.colors.WHITE,
                        actions=[
                            # Questo è il menu a tendina
                            ft.PopupMenuButton(
                                items=[
                                    ft.PopupMenuItem(text="Famiglia", on_click=lambda _: page.go("/famiglia")),
                                ]
                            )
                        ]
                    ),
                    ft.Text("Benvenuto!\nClicca sui 3 puntini in alto a destra per aprire il menu.", size=18)
                ]
            )
        )
        
        # 2. Se l'utente clicca su "Famiglia", carichiamo la vista dal modulo separato
        if page.route == "/famiglia":
            page.views.append(modulo_famiglia.vista_famiglia(page))
        
        page.update()

    # Gestisce il tasto "Indietro" del browser
    def view_pop(view):
        page.views.pop()
        top_view = page.views[-1]
        page.go(top_view.route)

    page.on_route_change = route_change
    page.on_view_pop = view_pop
    
    # Inizializza la prima pagina
    page.go(page.route)

if __name__ == "__main__":
    # Cattura la porta assegnata da Render o Codespaces, di default usa 8080
    porta = int(os.environ.get("PORT", 8080))
    
    # Avvia l'app in modalità web server con la sintassi aggiornata
    ft.app(
        target=main, 
        view=ft.AppView.WEB_BROWSER, # <-- Modifica fondamentale
        port=porta, 
        host="0.0.0.0"       
    )