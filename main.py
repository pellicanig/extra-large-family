import flet as ft
import os
import modulo_famiglia
import modulo_calendario
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("ATTENZIONE: File .env mancante o chiavi Supabase non trovate!")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def main(page: ft.Page):
    # ⚡ QUESTO È IL NOME CHE APPARIRÀ SOTTO L'ICONA SUL TELEFONO ⚡
    page.title = "ELF" 
    page.theme_mode = ft.ThemeMode.LIGHT
    
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
        assets_dir="assets" # ⚡ FONDAMENTALE: DICE A FLET DI GENERARE IL MANIFEST PWA CON L'ELFO ⚡
    )