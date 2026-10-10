import flet as ft
import calendar
from datetime import datetime, timedelta

MESI = ["", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]
GIORNI = ["LUN", "MAR", "MER", "GIO", "VEN", "SAB", "DOM"]

def crea_calendario(page: ft.Page):
    stato = {"data": datetime.now(), "vista": "Mese"}
    oggi = datetime.now()

    testo_header = ft.Text(size=18, weight=ft.FontWeight.BOLD)
    
    # ⚡ LA SOLUZIONE DEFINITIVA E BLINDATA ⚡
    # Usiamo semplici Container. Sono immortali, non dipendono dai restyling di Flet 1.0+.
    # Impostiamo il padding con un numero semplice (10) per evitare bug di importazione.
    btn_giorno = ft.Container(content=ft.Text("Giorno", size=13, weight=ft.FontWeight.BOLD), border_radius=5, padding=10, on_click=lambda e: cambia_vista("Giorno"))
    btn_settimana = ft.Container(content=ft.Text("Settimana", size=13, weight=ft.FontWeight.BOLD), border_radius=5, padding=10, on_click=lambda e: cambia_vista("Settimana"))
    btn_mese = ft.Container(content=ft.Text("Mese", size=13, weight=ft.FontWeight.BOLD), border_radius=5, padding=10, on_click=lambda e: cambia_vista("Mese"))

    def bordo_standard():
        return ft.Border(
            top=ft.BorderSide(0.5, "#E0E0E0"), bottom=ft.BorderSide(0.5, "#E0E0E0"),
            left=ft.BorderSide(0.5, "#E0E0E0"), right=ft.BorderSide(0.5, "#E0E0E0")
        )

    # I tre layer grafici rimangono sempre fisicamente presenti nella pagina (solo nascosti o mostrati)
    layer_mese = ft.Column(spacing=0, visible=True)
    layer_settimana = ft.Row(alignment=ft.MainAxisAlignment.SPACE_BETWEEN, spacing=0, visible=False)
    layer_giorno = ft.Column(spacing=0, visible=False)

    area_dinamica = ft.Column([layer_mese, layer_settimana, layer_giorno], expand=True, spacing=0)

    def cambia_vista(nuova_vista):
        stato["vista"] = nuova_vista
        aggiorna_tutto()

    def vai_a_giorno(e):
        stato["data"] = stato["data"].replace(day=e.control.data)
        cambia_vista("Giorno")

    def nav_prec(e):
        if stato["vista"] == "Mese":
            primo = stato["data"].replace(day=1)
            scorso = primo - timedelta(days=1)
            stato["data"] = stato["data"].replace(year=scorso.year, month=scorso.month, day=1)
        elif stato["vista"] == "Settimana":
            stato["data"] -= timedelta(days=7)
        elif stato["vista"] == "Giorno":
            stato["data"] -= timedelta(days=1)
        aggiorna_tutto()

    def nav_succ(e):
        if stato["vista"] == "Mese":
            mese = stato["data"].month
            anno = stato["data"].year
            if mese == 12:
                stato["data"] = stato["data"].replace(year=anno+1, month=1, day=1)
            else:
                stato["data"] = stato["data"].replace(month=mese+1, day=1)
        elif stato["vista"] == "Settimana":
            stato["data"] += timedelta(days=7)
        elif stato["vista"] == "Giorno":
            stato["data"] += timedelta(days=1)
        aggiorna_tutto()

    def aggiorna_tutto():
        vista = stato["vista"]
        data = stato["data"]

        # 1. Aggiorna i colori dei nostri "Pulsanti Container" per mostrare quello attivo
        btn_giorno.bgcolor = ft.Colors.BLUE_900 if vista == "Giorno" else ft.Colors.GREY_200
        btn_giorno.content.color = ft.Colors.WHITE if vista == "Giorno" else ft.Colors.BLACK
        
        btn_settimana.bgcolor = ft.Colors.BLUE_900 if vista == "Settimana" else ft.Colors.GREY_200
        btn_settimana.content.color = ft.Colors.WHITE if vista == "Settimana" else ft.Colors.BLACK
        
        btn_mese.bgcolor = ft.Colors.BLUE_900 if vista == "Mese" else ft.Colors.GREY_200
        btn_mese.content.color = ft.Colors.WHITE if vista == "Mese" else ft.Colors.BLACK

        # 2. Mostra solo il livello corretto (Flet aggiornerà la visibilità senza bloccarsi)
        layer_mese.visible = (vista == "Mese")
        layer_settimana.visible = (vista == "Settimana")
        layer_giorno.visible = (vista == "Giorno")

        # --- DISEGNO MESE ---
        if vista == "Mese":
            testo_header.value = f"{MESI[data.month]} {data.year}"
            layer_mese.controls.clear()
            
            griglia = ft.Column(spacing=0)
            matrice = calendar.monthcalendar(data.year, data.month)
            for settimana in matrice:
                riga = ft.Row(alignment=ft.MainAxisAlignment.SPACE_BETWEEN, spacing=0)
                for giorno in settimana:
                    if giorno == 0:
                        riga.controls.append(ft.Container(expand=1, height=70, border=bordo_standard()))
                    else:
                        is_oggi = (giorno == oggi.day and data.month == oggi.month and data.year == oggi.year)
                        is_sel = (giorno == data.day)
                        cerchio = ft.Container(
                            content=ft.Text(str(giorno), color=ft.Colors.WHITE if is_oggi else ft.Colors.BLACK, size=12, weight=ft.FontWeight.BOLD if is_oggi else ft.FontWeight.NORMAL),
                            bgcolor=ft.Colors.BLUE_900 if is_oggi else (ft.Colors.BLUE_100 if is_sel else ft.Colors.TRANSPARENT),
                            border_radius=15, width=24, height=24, alignment=ft.Alignment(0, 0)
                        )
                        riga.controls.append(
                            ft.Container(
                                content=ft.Column([cerchio], alignment=ft.MainAxisAlignment.START, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                                expand=1, height=70, border=bordo_standard(), padding=2,
                                data=giorno,
                                on_click=vai_a_giorno
                            )
                        )
                griglia.controls.append(riga)
            row_giorni = ft.Row(
                controls=[ft.Container(content=ft.Text(g, size=10, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_700), expand=1, alignment=ft.Alignment(0, 0), border=bordo_standard(), padding=5) for g in GIORNI],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN, spacing=0
            )
            layer_mese.controls.append(row_giorni)
            layer_mese.controls.append(griglia)

        # --- DISEGNO SETTIMANA ---
        elif vista == "Settimana":
            lunedi = data - timedelta(days=data.weekday())
            domenica = lunedi + timedelta(days=6)
            if lunedi.month == domenica.month:
                testo_header.value = f"{lunedi.day} - {domenica.day} {MESI[lunedi.month][:3]} {lunedi.year}"
            else:
                testo_header.value = f"{lunedi.day} {MESI[lunedi.month][:3]} - {domenica.day} {MESI[domenica.month][:3]} {domenica.year}"

            layer_settimana.controls.clear()
            for i in range(7):
                gc = lunedi + timedelta(days=i)
                is_oggi = (gc.date() == oggi.date())
                intestazione = ft.Container(
                    content=ft.Column([
                        ft.Text(GIORNI[i], size=10, color=ft.Colors.GREY_700),
                        ft.Container(
                            content=ft.Text(str(gc.day), color=ft.Colors.WHITE if is_oggi else ft.Colors.BLACK, size=14, weight=ft.FontWeight.BOLD),
                            bgcolor=ft.Colors.BLUE_900 if is_oggi else ft.Colors.TRANSPARENT,
                            border_radius=20, width=30, height=30, alignment=ft.Alignment(0, 0)
                        )
                    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=0),
                    alignment=ft.Alignment(0, 0), padding=5
                )
                layer_settimana.controls.append(
                    ft.Container(
                        content=ft.Column([intestazione, ft.Divider(height=1, color="#E0E0E0")]),
                        expand=1, height=300, border=bordo_standard()
                    )
                )

        # --- DISEGNO GIORNO ---
        elif vista == "Giorno":
            testo_header.value = f"{data.day} {MESI[data.month]} {data.year}"
            is_oggi = (data.date() == oggi.date())
            layer_giorno.controls.clear()
            
            intestazione = ft.Container(
                content=ft.Column([
                    ft.Text(GIORNI[data.weekday()], size=12, color=ft.Colors.GREY_700),
                    ft.Text(str(data.day), size=24, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900 if is_oggi else ft.Colors.BLACK)
                ], alignment=ft.MainAxisAlignment.START, spacing=0),
                padding=10, border=bordo_standard()
            )
            spazio_eventi = ft.Container(
                content=ft.Text("Nessun evento in programma.", color=ft.Colors.GREY_500, italic=True),
                alignment=ft.Alignment(0, 0),
                height=250, border=bordo_standard()
            )
            layer_giorno.controls.append(intestazione)
            layer_giorno.controls.append(spazio_eventi)

        # 3. Aggiornamento sicuro
        try:
            page.update()
        except Exception:
            pass

    header = ft.Row(
        controls=[
            ft.Row([
                ft.IconButton(ft.Icons.CHEVRON_LEFT, on_click=nav_prec),
                ft.IconButton(ft.Icons.CHEVRON_RIGHT, on_click=nav_succ),
                testo_header,
            ], alignment=ft.MainAxisAlignment.START, spacing=0),
            
            # I nostri nuovi e indistruttibili pulsanti al posto della tendina
            ft.Row([btn_giorno, btn_settimana, btn_mese], spacing=5)
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN
    )

    aggiorna_tutto()

    return ft.Container(
        content=ft.Column([header, area_dinamica], spacing=15),
        padding=10,
        expand=True
    )