import flet as ft
import os
import modulo_famiglia
import modulo_calendario
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

# Il .strip() rimuove eventuali spazi vuoti invisibili copiati per sbaglio su Render
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()

supabase = None
messaggio_errore = ""

if not SUPABASE_URL or not SUPABASE_KEY:
    messaggio_errore = "ERRORE: Variabili SUPABASE_URL o SUPABASE_KEY mancanti su Render."
else:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        # Test di connessione reale: proviamo a leggere 1 riga
        supabase.table('famiglia').select('id').limit(1).execute()
    except Exception as e:
        supabase = None
        messaggio_errore = f"ERRORE DI CONNESSIONE SUPABASE: {str(e)}"

def main(page: ft.Page):
    page.title = "ELF" 
    page.theme_mode = ft.ThemeMode.LIGHT
    
    # ⚡ LOG PARLANTE VISIVO ⚡
    if messaggio_errore:
        page.add(
            ft.Container(
                content=ft.Text(messaggio_errore, color="white", weight="bold", size=16),
                bgcolor="red",
                padding=20,
                border_radius=10,
                margin=20,
                alignment=ft.Alignment(0, 0)
            )
        )
        return # Ferma l'esecuzione dell'app qui, non carica il calendario

    # ⚡ MAGIA MULTI-PLAYER: L'ANTENNA RICEVENTE GLOBALE ⚡
    def ricevi_broadcast(messaggio):
        if messaggio == "aggiorna_dati":
            # Se la pagina ha una vista attiva configurata per aggiornarsi, la esegue in background
            if hasattr(page, "aggiorna_vista_corrente") and callable(page.aggiorna_vista_corrente):
                page.aggiorna_vista_corrente()

    # Iscriviamo questo specifico dispositivo al canale radio di Flet
    page.pubsub.subscribe(ricevi_broadcast)

    def naviga(route_string):
        page.route = route_string
        route_change(None) 
    
    def route_change(e):
        page.views.clear()
        
        page.views.append(
            ft.View(
                route="/",
                controls=[
                    ft.AppBar(
                        title=ft.Text(
                            value="Extra Large Family", 
                            font_family="cursive", 
                            italic=True,
                            weight=ft.FontWeight.BOLD,
                            size=24
                        ), 
                        bgcolor=ft.Colors.BLUE_900,
                        color=ft.Colors.WHITE,
                        center_title=True,
                        actions=[
                            ft.PopupMenuButton(
                                items=[
                                    ft.PopupMenuItem(
                                        content=ft.Text(value="Gestione Famiglia"), 
                                        on_click=lambda _: naviga("/famiglia")
                                    )
                                ]
                            )
                        ]
                    ),
                    modulo_calendario.crea_calendario(page)
                ],
                padding=0 
            )
        )
        
        if page.route == "/famiglia":
            page.views.append(modulo_famiglia.vista_famiglia(page, supabase))
        
        page.update()

    def view_pop(e):
        page.views.pop()
        if page.views:
            page.route = page.views[-1].route
            page.update()

    page.on_route_change = route_change
    page.on_view_pop = view_pop
    
    page.route = "/"
    route_change(None)

if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 8080))
    ft.run(
        main,
        port=porta, 
        host="0.0.0.0",
        view=ft.AppView.WEB_BROWSER,
        assets_dir="assets"
    )
