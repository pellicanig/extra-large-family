import flet as ft
import os
import threading
import time
import urllib.request
from datetime import datetime
import modulo_famiglia
import modulo_calendario
import modulo_menu
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

# Il .strip() rimuove eventuali spazi vuoti invisibili copiati per sbaglio su Render
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://extra-large-family.onrender.com").strip()

supabase = None
messaggio_errore = ""

def connetti_e_sveglia_supabase():
    global supabase, messaggio_errore
    if not SUPABASE_URL or not SUPABASE_KEY:
        messaggio_errore = "ERRORE: Variabili SUPABASE_URL o SUPABASE_KEY mancanti su Render."
        return False
    for tentativo in range(3):
        try:
            if supabase is None:
                supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
            supabase.table('famiglia').select('id').limit(1).execute()
            messaggio_errore = ""
            return True
        except Exception as e:
            messaggio_errore = f"ERRORE DI CONNESSIONE SUPABASE: {str(e)}"
            time.sleep(2)
    return False

connetti_e_sveglia_supabase()

# ⚡ CUSTODE SILENZIOSO: TIENE SVEGLIO SUPABASE E AIUTA UPTIME ROBOT ⚡
def custode_keep_alive():
    while True:
        time.sleep(600) # Ogni 10 minuti
        try:
            connetti_e_sveglia_supabase()
        except Exception:
            pass
        try:
            if RENDER_URL:
                req = urllib.request.Request(RENDER_URL, headers={'User-Agent': 'ELF-KeepAlive'})
                urllib.request.urlopen(req, timeout=10)
        except Exception:
            pass

threading.Thread(target=custode_keep_alive, daemon=True).start()

# Array per tradurre il mese in italiano
MESI = ["", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]

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
            if hasattr(page, "aggiorna_vista_corrente") and callable(page.aggiorna_vista_corrente):
                page.aggiorna_vista_corrente()
            if hasattr(page, "aggiorna_vista_menu") and callable(page.aggiorna_vista_menu):
                page.aggiorna_vista_menu()

    # Iscriviamo questo specifico dispositivo al canale radio di Flet
    page.pubsub.subscribe(ricevi_broadcast)

    def naviga(route_string):
        page.route = route_string
        route_change(None) 
    
    def route_change(e):
        page.views.clear()
        
        # Calcolo data odierna in italiano
        oggi = datetime.now()
        data_odierna_str = f"{oggi.day} {MESI[oggi.month]} {oggi.year}"

        # ⚡ CONTENITORI E SWITCH IMPEGNI / MENU ⚡
        vista_impegni_box = ft.Container(content=modulo_calendario.crea_calendario(page), visible=True, expand=True)
        vista_menu_box = ft.Container(content=modulo_menu.crea_calendario_menu(page), visible=False, expand=True)

        def cambia_modalita_home(ev):
            is_menu = switch_modalita.value
            vista_impegni_box.visible = not is_menu
            vista_menu_box.visible = is_menu
            lbl_impegni.weight = ft.FontWeight.NORMAL if is_menu else ft.FontWeight.BOLD
            lbl_menu.weight = ft.FontWeight.BOLD if is_menu else ft.FontWeight.NORMAL
            page.update()

        lbl_impegni = ft.Text("📅 IMPEGNI", size=13, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900)
        switch_modalita = ft.Switch(value=False, on_change=cambia_modalita_home)
        lbl_menu = ft.Text("🍽️ MENU", size=13, weight=ft.FontWeight.NORMAL, color=ft.Colors.BLUE_900)

        barra_switch = ft.Container(
            content=ft.Row([lbl_impegni, switch_modalita, lbl_menu], alignment=ft.MainAxisAlignment.CENTER, spacing=10),
            bgcolor="#EEF2F6", padding=4
        )
        
        page.views.append(
            ft.View(
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
                        ), 
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
                                ]
                            )
                        ]
                    ),
                    barra_switch,
                    vista_impegni_box,
                    vista_menu_box
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
