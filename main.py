import flet as ft
import os
from datetime import datetime
import modulo_famiglia
import modulo_calendario
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

# Il .strip() rimuove eventuali spazi vuoti invisibili copiati per sbaglio su Render
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()

@@ -20,41 +19,41 @@
else:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        # Test di connessione reale: proviamo a leggere 1 riga
        supabase.table('famiglia').select('id').limit(1).execute()
    except Exception as e:
        supabase = None
        messaggio_errore = f"ERRORE DI CONNESSIONE SUPABASE: {str(e)}"

# Array per tradurre il mese in italiano
MESI = ["", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]

def main(page: ft.Page):
    page.title = "ELF" 
    page.theme_mode = ft.ThemeMode.LIGHT

    # ⚡ LOG PARLANTE VISIVO ⚡
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
                bgcolor="red",
                padding=20,
                border_radius=10,
                margin=20,
                alignment=ft.Alignment(0, 0)
                bgcolor="red", padding=20, border_radius=10, margin=20, alignment=ft.Alignment(0, 0)
            )
        )
        return # Ferma l'esecuzione dell'app qui, non carica il calendario
        return 

    # ⚡ MAGIA MULTI-PLAYER: L'ANTENNA RICEVENTE GLOBALE ⚡
    def ricevi_broadcast(messaggio):
        if messaggio == "aggiorna_dati":
            # Se la pagina ha una vista attiva configurata per aggiornarsi, la esegue in background
            if hasattr(page, "aggiorna_vista_corrente") and callable(page.aggiorna_vista_corrente):
                page.aggiorna_vista_corrente()

    # Iscriviamo questo specifico dispositivo al canale radio di Flet
    page.pubsub.subscribe(ricevi_broadcast)

    def naviga(route_string):
@@ -64,7 +63,6 @@
    def route_change(e):
        page.views.clear()

        # Calcolo data odierna in italiano
        oggi = datetime.now()
        data_odierna_str = f"{oggi.day} {MESI[oggi.month]} {oggi.year}"

@@ -73,27 +71,21 @@
                route="/",
                controls=[
                    ft.AppBar(
                        # ⚡ TITOLO PULITO CON DATA SOTTOSTANTE ⚡
                        title=ft.Column(
                            controls=[
                                ft.Text("Extra Large Family", weight=ft.FontWeight.BOLD, size=22),
                                ft.Text(data_odierna_str, size=13, color="white70", weight=ft.FontWeight.NORMAL)
                            ],
                            spacing=0,
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER
                            spacing=0, horizontal_alignment=ft.CrossAxisAlignment.CENTER
                        ), 
                        # Corretto: 'Colors' con la C maiuscola
                        bgcolor=ft.Colors.BLUE_900,
                        color=ft.Colors.WHITE,
                        center_title=True,
                        toolbar_height=70, 
                        actions=[
                            ft.PopupMenuButton(
                                items=[
                                    ft.PopupMenuItem(
                                        content=ft.Text(value="Gestione Famiglia"), 
                                        on_click=lambda _: naviga("/famiglia")
                                    )
                                    ft.PopupMenuItem(content=ft.Text(value="Gestione Famiglia"), on_click=lambda _: naviga("/famiglia"))
                                ]
                            )
                        ]
@@ -123,10 +115,4 @@

if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 8080))
    ft.run(
        main,
        port=porta, 
        host="0.0.0.0",
        view=ft.AppView.WEB_BROWSER,
        assets_dir="assets"
    )
    ft.run(main, port=porta, host="0.0.0.0", view=ft.AppView.WEB_BROWSER, assets_dir="assets")
