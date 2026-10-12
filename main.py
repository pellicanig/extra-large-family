import flet as ft
import os
from datetime import datetime
import modulo_famiglia
import modulo_calendario
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()

supabase = None
messaggio_errore = ""

if not SUPABASE_URL or not SUPABASE_KEY:
    messaggio_errore = "ERRORE: Variabili SUPABASE_URL o SUPABASE_KEY mancanti su Render."
else:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        supabase = None
        messaggio_errore = f"ERRORE DI CONNESSIONE SUPABASE: {str(e)}"

MESI = ["", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]

def main(page: ft.Page):
    page.title = "ELF" 
    page.theme_mode = ft.ThemeMode.LIGHT
    
    # ⚡ IL "MODULO MONITORAGGIO" INTEGRATO ⚡
    # Poiché UptimeRobot aprirà questa pagina ogni 5 minuti,
    # questo ping a Supabase verrà eseguito in automatico 288 volte al giorno,
    # impedendo per sempre al piano gratuito di congelare il database.
    global messaggio_errore
    if supabase and not messaggio_errore:
        try:
            supabase.table('famiglia').select('id').limit(1).execute()
        except Exception as e:
            messaggio_errore = f"ERRORE DB: {str(e)}"

    if messaggio_errore:
        page.add(
            ft.Container(
                content=ft.Text(messaggio_errore, color="white", weight="bold", size=16),
                bgcolor="red", padding=20, border_radius=10, margin=20, alignment=ft.Alignment(0, 0)
            )
        )
        return 

    def ricevi_broadcast(messaggio):
        if messaggio == "aggiorna_dati":
            if hasattr(page, "aggiorna_vista_corrente") and callable(page.aggiorna_vista_corrente):
                page.aggiorna_vista_corrente()

    page.pubsub.subscribe(ricevi_broadcast)

    def naviga(route_string):
        page.route = route_string
        route_change(None) 
    
    def route_change(e):
        page.views.clear()
        
        oggi = datetime.now()
        data_odierna_str = f"{oggi.day} {MESI[oggi.month]} {oggi.year}"
        
        page.views.append(
            ft.View(
                route="/",
                controls=[
                    ft.AppBar(
                        title=ft.Column(
                            controls=[
                                ft.Text("Extra Large Family", weight=ft.FontWeight.BOLD, size=22),
                                ft.Text(data_odierna_str, size=13, color="white70", weight=ft.FontWeight.NORMAL)
                            ],
                            spacing=0, horizontal_alignment=ft.CrossAxisAlignment.CENTER
                        ), 
                        bgcolor=ft.Colors.BLUE_900,
                        color=ft.Colors.WHITE,
                        center_title=True,
                        toolbar_height=70, 
                        actions=[
                            ft.PopupMenuButton(
                                items=[
                                    ft.PopupMenuItem(content=ft.Text(value="Gestione Famiglia"), on_click=lambda _: naviga("/famiglia"))
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
    ft.run(main, port=porta, host="0.0.0.0", view=ft.AppView.WEB_BROWSER, assets_dir="assets")
