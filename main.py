import flet as ft
import os
import threading
import time
import urllib.request
from datetime import datetime
import modulo_famiglia
import modulo_calendario
import modulo_menu
import modulo_notifiche

from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

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

# ⚡ CUSTODE SILENZIOSO: PING OGNI 4 MINUTI CON ANTI-CACHE PER NON FAR SPEGNERE RENDER ⚡
def custode_keep_alive():
    while True:
        time.sleep(240)  # Ogni 4 minuti (sotto la soglia dei 15 minuti di Render)
        try:
            connetti_e_sveglia_supabase()
        except Exception:
            pass
        try:
            if RENDER_URL:
                url_ping = f"{RENDER_URL.rstrip('/')}/?keepalive={int(time.time())}"
                req = urllib.request.Request(
                    url_ping,
                    headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) ELF-KeepAlive/2.0',
                        'Accept': 'text/html,application/xhtml+xml',
                        'Cache-Control': 'no-cache, no-store, must-revalidate'
                    }
                )
                urllib.request.urlopen(req, timeout=10)
        except Exception:
            pass

threading.Thread(target=custode_keep_alive, daemon=True).start()

MESI = ["", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]

def main(page: ft.Page):
    page.title = "ELF"
    page.theme_mode = ft.ThemeMode.LIGHT

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
        return

    # ⚡ SINCRONIZZAZIONE SELETTIVA PER AZZERARE LA LATENZA ⚡
    # ⚡ SINCRONIZZAZIONE SELETTIVA PER AZZERARE LA LATENZA ⚡
    def ricevi_broadcast(messaggio):
        if messaggio == "aggiorna_dati":
            modulo_notifiche.forza_ricalcolo_impegni()
            if hasattr(page, "aggiorna_vista_corrente") and callable(page.aggiorna_vista_corrente):
                page.aggiorna_vista_corrente()
        elif messaggio == "aggiorna_menu":
            if hasattr(page, "aggiorna_vista_menu") and callable(page.aggiorna_vista_menu):
                page.aggiorna_vista_menu()

    page.pubsub.subscribe(ricevi_broadcast)

    # ⚡ GESTIONE RITORNO DALLO STANDBY SU SMARTPHONE ⚡
    def gestisci_riconnessione_standby(e):
        def sveglia_bg():
            try:
                connetti_e_sveglia_supabase()
                page.update()
            except Exception:
                pass
        threading.Thread(target=sveglia_bg, daemon=True).start()

    page.on_connect = gestisci_riconnessione_standby

    def naviga(route_string):
        page.route = route_string
        route_change(None)

    def route_change(e):
        page.views.clear()

        oggi = datetime.now()
        data_odierna_str = f"{oggi.day} {MESI[oggi.month]} {oggi.year}"

        # Precarica in background l'anagrafica del menu mentre l'utente vede subito gli impegni
        threading.Thread(target=lambda: modulo_menu.assicura_cache_anagrafica(forza_ricarica=False), daemon=True).start()

        vista_impegni_box = ft.Container(content=modulo_calendario.crea_calendario(page), visible=True, expand=True)
        vista_menu_box = ft.Container(content=None, visible=False, expand=True)
        menu_inizializzato = [False]

        def cambia_modalita_home(ev):
            is_menu = switch_modalita.value
            if is_menu and not menu_inizializzato[0]:
                vista_menu_box.content = modulo_menu.crea_calendario_menu(page)
                menu_inizializzato[0] = True
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

        txt_menu_notifiche = ft.Text(
            value="🔕 Silenzia Notifiche" if modulo_notifiche.is_notifiche_attive(page) else "🔔 Attiva Notifiche",
            weight=ft.FontWeight.BOLD,
            color=ft.Colors.RED_700 if modulo_notifiche.is_notifiche_attive(page) else ft.Colors.GREEN_800
        )

        def aggiorna_testo_menu_notifiche():
            att = modulo_notifiche.is_notifiche_attive(page)
            txt_menu_notifiche.value = "🔕 Silenzia Notifiche" if att else "🔔 Attiva Notifiche"
            txt_menu_notifiche.color = ft.Colors.RED_700 if att else ft.Colors.GREEN_800

        page.aggiorna_menu_notifiche = aggiorna_testo_menu_notifiche
        modulo_notifiche.registra_pagina(page)

        def gestisci_click_notifiche():
            import urllib.parse
            attivo_qui = modulo_notifiche.is_notifiche_attive(page)
            did_curr, diag = modulo_notifiche.ottieni_diag_dispositivo(page)
            url_attiva = f"{RENDER_URL.rstrip('/')}/api/notifiche/pagina-attiva?did={urllib.parse.quote(did_curr or '')}"

            def chiudi_dlg_n(e=None):
                dlg_n.open = False
                aggiorna_testo_menu_notifiche()
                page.update()

            def silenzia_qui(e):
                dlg_n.open = False
                modulo_notifiche.imposta_silenzioso(True, page)
                aggiorna_testo_menu_notifiche()
                snack = ft.SnackBar(content=ft.Text("🔕 Notifiche silenziate su questo dispositivo."))
                page.overlay.append(snack)
                snack.open = True
                page.update()

            stato_perm = "✅ Autorizzate" if attivo_qui else ("❌ Bloccate da OS" if diag.get("perm") == "denied" else "⚪ Da attivare")
            stato_pwa = "✅ Schermata Home" if diag.get("standalone") == "1" else "⚠️ Browser standard (Consigliata Home)"
            stato_persist = "✅ Protetto" if (diag.get("persisted") == "1" and attivo_qui) else "⚠️ Standard"
            stato_os_bat = "✅ Sbloccata" if diag.get("os_cfg") == "1" else "🟠 Da verificare in Impostazioni"

            box_diag = ft.Container(
                content=ft.Column([
                    ft.Text("🔍 Controllo Impostazioni Dispositivo:", weight=ft.FontWeight.BOLD, size=12, color="#0D47A1"),
                    ft.Text(f"• Permesso Notifiche OS: {stato_perm}", size=11.5, color="#263238"),
                    ft.Text(f"• Modalità App: {stato_pwa}", size=11.5, color="#263238"),
                    ft.Text(f"• Protezione Sospensione Dati: {stato_persist}", size=11.5, color="#263238"),
                    ft.Text(f"• Batteria Senza Restrizioni: {stato_os_bat}", size=11.5, color="#263238"),
                ], spacing=3, tight=True),
                bgcolor="#F5F7FA", padding=10, border_radius=8
            )

            azioni_col = [
                ft.Button(
                    "📲 Attiva Notifiche e Configura Dispositivo" if not attivo_qui else "⚙️ Verifica / Sblocca Impostazioni Batteria e Test",
                    bgcolor="#0D47A1", color=ft.Colors.WHITE, width=float("inf"),
                    url=url_attiva, on_click=chiudi_dlg_n
                )
            ]
            if attivo_qui:
                azioni_col.append(
                    ft.Button(
                        "🔕 Silenzia questo Telefono",
                        bgcolor="#C62828", color=ft.Colors.WHITE, width=float("inf"),
                        on_click=silenzia_qui
                    )
                )
            azioni_col.append(ft.TextButton("Chiudi", on_click=chiudi_dlg_n))

            stato_str = "🟢 ATTIVE SU QUESTO DISPOSITIVO" if attivo_qui else "🔴 NON ATTIVE SU QUESTO DISPOSITIVO"
            colore_stato = "#1B5E20" if attivo_qui else "#B71C1C"

            dlg_n = ft.AlertDialog(
                title=ft.Text("🔔 Notifiche Dispositivo", size=16, weight=ft.FontWeight.BOLD, color="#0D47A1"),
                content=ft.Column([
                    ft.Text(f"Stato attuale: {stato_str}", weight=ft.FontWeight.BOLD, size=13, color=colore_stato),
                    box_diag,
                    ft.Text(
                        "Ogni telefono gestisce le proprie notifiche in modo indipendente (15 min prima di inizio e fine impegno). Tocca il pulsante blu per attivare i permessi o sbloccare la Batteria.",
                        size=11.5, color="#455A64"
                    )
                ], tight=True, spacing=8),
                actions=[ft.Column(azioni_col, tight=True, spacing=6)],
                content_padding=15, inset_padding=10
            )
            try:
                page.open(dlg_n)
            except Exception:
                page.overlay.append(dlg_n)
                dlg_n.open = True
                page.update()

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
                                    ),
                                    ft.PopupMenuItem(
                                        content=txt_menu_notifiche,
                                        on_click=lambda _: gestisci_click_notifiche()
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

import fastapi
_orig_fastapi_init = fastapi.FastAPI.__init__
def _patched_fastapi_init(self, *args, **kwargs):
    kwargs.pop("openapi_prefix", None)
    _orig_fastapi_init(self, *args, **kwargs)
    modulo_notifiche.collega_api_fastapi(self, supabase)
fastapi.FastAPI.__init__ = _patched_fastapi_init

if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 8080))
    ft.run(
        main,
        port=porta,
        host="0.0.0.0",
        view=ft.AppView.WEB_BROWSER,
        assets_dir="assets"
    )
