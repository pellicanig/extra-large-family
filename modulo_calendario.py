import flet as ft
import calendar
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from supabase import create_client, Client
from modulo_impegni import apri_form_impegno

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    supabase = None
    print(f"Errore connessione Supabase in calendario: {e}")

MESI = ["", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]
GIORNI = ["LUN", "MAR", "MER", "GIO", "VEN", "SAB", "DOM"]

def crea_calendario(page: ft.Page):
    stato = {
        "data": datetime.now(), 
        "vista": "Mese",
        "filtro_categoria": None,
        "filtro_familiare": None
    }
    oggi = datetime.now()

    lista_categorie = []
    lista_famiglia = []
    if supabase:
        try:
            res_cat = supabase.table('categorie').select('*').order('nome').execute()
            lista_categorie = res_cat.data if res_cat.data else []
            res_fam = supabase.table('famiglia').select('*').order('nome').execute()
            lista_famiglia = res_fam.data if res_fam.data else []
        except Exception as e:
            print("Errore caricamento liste filtri:", e)

    testo_header = ft.Text(size=16, weight=ft.FontWeight.BOLD)
    
    btn_giorno = ft.Container(content=ft.Text("Giorno", size=12, weight=ft.FontWeight.BOLD), border_radius=5, padding=6, on_click=lambda e: cambia_vista("Giorno"))
    btn_settimana = ft.Container(content=ft.Text("Settimana", size=12, weight=ft.FontWeight.BOLD), border_radius=5, padding=6, on_click=lambda e: cambia_vista("Settimana"))
    btn_mese = ft.Container(content=ft.Text("Mese", size=12, weight=ft.FontWeight.BOLD), border_radius=5, padding=6, on_click=lambda e: cambia_vista("Mese"))

    def bordo_standard():
        return ft.Border(top=ft.BorderSide(0.5, "#E0E0E0"), bottom=ft.BorderSide(0.5, "#E0E0E0"), left=ft.BorderSide(0.5, "#E0E0E0"), right=ft.BorderSide(0.5, "#E0E0E0"))

    layer_mese = ft.Column(spacing=0, visible=True)
    layer_settimana = ft.Row(alignment=ft.MainAxisAlignment.SPACE_BETWEEN, spacing=0, visible=False)
    layer_giorno = ft.Column(spacing=0, visible=False)
    area_dinamica = ft.Column([layer_mese, layer_settimana, layer_giorno], expand=True, spacing=0)

    row_filtri_cat = ft.Row(wrap=True, spacing=5)
    row_filtri_fam = ft.Row(wrap=True, spacing=5)

    def cambia_vista(nuova_vista):
        stato["vista"] = nuova_vista
        aggiorna_tutto()
    
    def vai_a_giorno(e):
        valore = e.control.data
        if isinstance(valore, int): 
            stato["data"] = stato["data"].replace(day=valore)
        else: 
            stato["data"] = valore
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

    def imposta_filtro_cat(id_cat):
        stato["filtro_categoria"] = id_cat
        aggiorna_tutto()

    def imposta_filtro_fam(id_fam):
        stato["filtro_familiare"] = id_fam
        aggiorna_tutto()

    def aggiorna_tutto():
        vista = stato["vista"]
        data = stato["data"]

        btn_giorno.bgcolor = ft.Colors.BLUE_900 if vista == "Giorno" else ft.Colors.GREY_200
        btn_giorno.content.color = ft.Colors.WHITE if vista == "Giorno" else ft.Colors.BLACK
        btn_settimana.bgcolor = ft.Colors.BLUE_900 if vista == "Settimana" else ft.Colors.GREY_200
        btn_settimana.content.color = ft.Colors.WHITE if vista == "Settimana" else ft.Colors.BLACK
        btn_mese.bgcolor = ft.Colors.BLUE_900 if vista == "Mese" else ft.Colors.GREY_200
        btn_mese.content.color = ft.Colors.WHITE if vista == "Mese" else ft.Colors.BLACK

        layer_mese.visible = (vista == "Mese")
        layer_settimana.visible = (vista == "Settimana")
        layer_giorno.visible = (vista == "Giorno")

        row_filtri_cat.controls.clear()
        row_filtri_fam.controls.clear()

        is_tutte_cat = stato["filtro_categoria"] is None
        row_filtri_cat.controls.append(
            ft.Container(
                content=ft.Text("Tutte", size=10, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE if is_tutte_cat else ft.Colors.BLACK), 
                bgcolor=ft.Colors.GREY_800 if is_tutte_cat else ft.Colors.GREY_300, 
                padding=6, 
                border_radius=15, 
                on_click=lambda e: imposta_filtro_cat(None)
            )
        )
        
        for c in lista_categorie:
            is_sel = stato["filtro_categoria"] == c['id']
            row_filtri_cat.controls.append(
                ft.Container(
                    content=ft.Text(c['nome'], size=10, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE if is_sel else c['colore']), 
                    bgcolor=c['colore'] if is_sel else ft.Colors.TRANSPARENT, 
                    border=ft.border.all(2, c['colore']), 
                    padding=6, 
                    border_radius=15, 
                    on_click=lambda e, cid=c['id']: imposta_filtro_cat(cid)
                )
            )

        is_tutti_fam = stato["filtro_familiare"] is None
        row_filtri_fam.controls.append(
            ft.Container(
                content=ft.Text("Tutti", size=10, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE if is_tutti_fam else ft.Colors.BLACK), 
                bgcolor=ft.Colors.GREY_800 if is_tutti_fam else ft.Colors.GREY_300, 
                padding=6, 
                border_radius=15, 
                on_click=lambda e: imposta_filtro_fam(None)
            )
        )
        
        for f in lista_famiglia:
            is_sel = stato["filtro_familiare"] == f['id']
            row_filtri_fam.controls.append(
                ft.Container(
                    content=ft.Text(f['nome'], size=10, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE if is_sel else ft.Colors.BLUE_900), 
                    bgcolor=ft.Colors.BLUE_900 if is_sel else ft.Colors.TRANSPARENT, 
                    border=ft.border.all(2, ft.Colors.BLUE_900), 
                    padding=6, 
                    border_radius=15, 
                    on_click=lambda e, fid=f['id']: imposta_filtro_fam(fid)
                )
            )

        dizionario_eventi = {}
        if supabase:
            try:
                data_rif = data.replace(day=1)
                inizio_finestra = data_rif - timedelta(days=15)
                fine_finestra = data_rif + timedelta(days=45)
                primo_g = inizio_finestra.strftime("%Y-%m-%d")
                ultimo_g = fine_finestra.strftime("%Y-%m-%d")
                
                res = supabase.table('impegni').select('*, categorie(colore, nome), impegni_familiari(id_familiare)').lte('data_evento', ultimo_g).execute()
                
                if res.data:
                    for imp in res.data:
                        if stato["filtro_categoria"] and imp.get('id_categoria') != stato["filtro_categoria"]:
                            continue
                            
                        if stato["filtro_familiare"]:
                            assegnatari = [f['id_familiare'] for f in imp.get('impegni_familiari', [])] if imp.get('impegni_familiari') else []
                            if stato["filtro_familiare"] not in assegnatari:
                                continue

                        val_p = imp.get('is_periodico')
                        is_periodico = True if str(val_p).lower() in ['true', '1', 't', 'y', 'yes'] else False

                        if is_periodico:
                            data_start = datetime.strptime(imp['data_evento'], "%Y-%m-%d").date()
                            data_stop = datetime.strptime(imp['data_fine_periodo'], "%Y-%m-%d").date() if imp.get('data_fine_periodo') else None
                            corrente = inizio_finestra.date()
                            fine_f = fine_finestra.date()
                            
                            while corrente <= fine_f:
                                if corrente >= data_start and corrente.weekday() == data_start.weekday():
                                    if data_stop is None or corrente <= data_stop:
                                        d_str = corrente.strftime("%Y-%m-%d")
                                        if d_str not in dizionario_eventi: 
                                            dizionario_eventi[d_str] = []
                                        imp_copy = imp.copy()
                                        imp_copy['data_evento'] = d_str 
                                        dizionario_eventi[d_str].append(imp_copy)
                                corrente += timedelta(days=1)
                        else:
                            d_str = imp['data_evento']
                            if primo_g <= d_str <= ultimo_g:
                                if d_str not in dizionario_eventi: 
                                    dizionario_eventi[d_str] = []
                                dizionario_eventi[d_str].append(imp)
                                
                for k in dizionario_eventi:
                    dizionario_eventi[k].sort(key=lambda x: x.get('ora_inizio') or '24:00')

            except Exception as e:
                print("Errore caricamento impegni:", e)

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
                        data_str = data.replace(day=giorno).strftime("%Y-%m-%d")
                        eventi_oggi = dizionario_eventi.get(data_str, [])

                        cerchio = ft.Container(
                            content=ft.Text(str(giorno), color=ft.Colors.WHITE if is_oggi else ft.Colors.BLACK, size=12, weight=ft.FontWeight.BOLD if is_oggi else ft.FontWeight.NORMAL),
                            bgcolor=ft.Colors.BLUE_900 if is_oggi else (ft.Colors.BLUE_100 if is_sel else ft.Colors.TRANSPARENT),
                            border_radius=15, width=24, height=24, alignment=ft.Alignment(0, 0)
                        )

                        indicatori = [
                            ft.Container(
                                content=ft.Text(ev.get('titolo', ''), size=8, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, no_wrap=True), 
                                bgcolor=(ev.get('categorie') or {}).get('colore', '#9E9E9E'), 
                                padding=ft.padding.all(2), 
                                border_radius=2, 
                                alignment=ft.Alignment(-1, 0)
                            ) 
                            for ev in eventi_oggi[:2]
                        ]
                        if len(eventi_oggi) > 2: 
                            indicatori.append(ft.Text(f"+{len(eventi_oggi)-2}", size=8, color=ft.Colors.GREY_600))

                        riga.controls.append(
                            ft.Container(
                                content=ft.Column([
                                    ft.Container(content=cerchio, alignment=ft.Alignment(0,0)), 
                                    ft.Column(indicatori, spacing=1)
                                ], alignment=ft.MainAxisAlignment.START, horizontal_alignment=ft.CrossAxisAlignment.STRETCH, spacing=2),
                                expand=1, height=70, border=bordo_standard(), padding=2, data=giorno, on_click=vai_a_giorno
                            )
                        )
                griglia.controls.append(riga)
            layer_mese.controls.extend([
                ft.Row(
                    controls=[
                        ft.Container(content=ft.Text(g, size=9, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_700), expand=1, alignment=ft.Alignment(0, 0), border=bordo_standard(), padding=2) 
                        for g in GIORNI
                    ], 
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN, spacing=0
                ),
                griglia
            ])

        elif vista == "Settimana":
            lunedi = data - timedelta(days=data.weekday())
            domenica = lunedi + timedelta(days=6)
            testo_header.value = f"{lunedi.day} - {domenica.day} {MESI[lunedi.month][:3]} {lunedi.year}" if lunedi.month == domenica.month else f"{lunedi.day} {MESI[lunedi.month][:3]} - {domenica.day} {MESI[domenica.month][:3]} {domenica.year}"
            layer_settimana.controls.clear()
            for i in range(7):
                gc = lunedi + timedelta(days=i)
                data_str = gc.strftime("%Y-%m-%d")
                eventi_oggi = dizionario_eventi.get(data_str, [])

                intestazione = ft.Container(
                    content=ft.Column([
                        ft.Text(GIORNI[i], size=9, color=ft.Colors.GREY_700),
                        ft.Container(
                            content=ft.Text(str(gc.day), color=ft.Colors.WHITE if (gc.date() == oggi.date()) else ft.Colors.BLACK, size=14, weight=ft.FontWeight.BOLD), 
                            bgcolor=ft.Colors.BLUE_900 if (gc.date() == oggi.date()) else ft.Colors.TRANSPARENT, 
                            border_radius=15, width=24, height=24, alignment=ft.Alignment(0, 0)
                        )
                    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=0), padding=5
                )

                indicatori = []
                for ev in eventi_oggi:
                    orario = f"{ev.get('ora_inizio')[:5]}\n" if ev.get('ora_inizio') else ""
                    indicatori.append(
                        ft.Container(
                            content=ft.Text(f"{orario}{ev.get('titolo', '')}", size=10, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, no_wrap=True), 
                            bgcolor=(ev.get('categorie') or {}).get('colore', '#9E9E9E'), 
                            padding=3, border_radius=3, alignment=ft.Alignment(-1, 0)
                        )
                    )

                layer_settimana.controls.append(
                    ft.Container(
                        content=ft.Column([
                            intestazione, ft.Divider(height=1, color="#E0E0E0"), 
                            ft.Container(content=ft.Column(indicatori, spacing=2, scroll=ft.ScrollMode.AUTO), padding=2, expand=True)
                        ]), 
                        expand=1, height=300, border=bordo_standard(), data=gc, on_click=vai_a_giorno
                    )
                )

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
            
            btn_aggiungi_evento = ft.Container(
                content=ft.Text("➕ Aggiungi Impegno", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                bgcolor=ft.Colors.BLUE_900, padding=10, border_radius=5, alignment=ft.Alignment(0, 0),
                on_click=lambda e: apri_form_impegno(page, data, aggiorna_tutto)
            )

            lista_card_eventi = []
            data_str = data.strftime("%Y-%m-%d")
            eventi_oggi = dizionario_eventi.get(data_str, [])

            if eventi_oggi:
                for imp in eventi_oggi:
                    titolo = imp.get('titolo', 'Senza Titolo')
                    ora_i = imp.get('ora_inizio')
                    ora_f = imp.get('ora_fine')
                    orario = f"{ora_i[:5]} - {ora_f[:5]}" if ora_i and ora_f else (ora_i[:5] if ora_i else "Tutto il giorno")
                    note = imp.get('note', '')
                    
                    cat_dati = imp.get('categorie') or {}
                    cat_nome = cat_dati.get('nome', 'Nessuna')
                    cat_colore = cat_dati.get('colore', '#9E9E9E')

                    val_p = imp.get('is_periodico')
                    is_p = True if str(val_p).lower() in ['true', '1', 't', 'y', 'yes'] else False
                    icona_periodico = " 🔄" if is_p else ""

                    try:
                        res_fam = supabase.table('impegni_familiari').select('id_familiare, famiglia(nome)').eq('id_impegno', imp['id']).execute()
                        familiari_nomi = [f['famiglia']['nome'] for f in res_fam.data if f.get('famiglia')] if res_fam.data else []
                        imp['assegnatari_ids'] = [f['id_familiare'] for f in res_fam.data] if res_fam.data else []
                        testo_familiari = "Assegnato a: " + ", ".join(familiari_nomi) if familiari_nomi else "Nessun assegnatario"
                    except:
                        testo_familiari = "Assegnato a: errore lettura"
                        imp['assegnatari_ids'] = []

                    card = ft.Container(
                        content=ft.Column([
                            ft.Row([
                                ft.Container(bgcolor=cat_colore, width=12, height=12, border_radius=6),
                                ft.Text(cat_nome + icona_periodico, size=11, weight=ft.FontWeight.BOLD, color=cat_colore),
                                ft.Text(orario, size=13, color=ft.Colors.GREY_700, weight=ft.FontWeight.BOLD)
                            ], alignment=ft.MainAxisAlignment.START),
                            ft.Text(titolo, size=16, weight=ft.FontWeight.BOLD),
                            ft.Text(testo_familiari, size=12, color=ft.Colors.BLUE_900, italic=True),
                            ft.Text(note, size=12, color=ft.Colors.GREY_600) if note else ft.Container()
                        ], spacing=3),
                        bgcolor=ft.Colors.WHITE, padding=15, border_radius=8, ink=True,
                        border=ft.Border(left=ft.BorderSide(5, cat_colore), top=ft.BorderSide(1, "#EEEEEE"), bottom=ft.BorderSide(1, "#EEEEEE"), right=ft.BorderSide(1, "#EEEEEE")),
                        on_click=lambda e, impegno_corrente=imp: apri_form_impegno(page, data, aggiorna_tutto, impegno_corrente) 
                    )
                    lista_card_eventi.append(card)
            else:
                lista_card_eventi.append(
                    ft.Container(
                        content=ft.Text("Nessun evento corrispondente.", color=ft.Colors.GREY_500, italic=True), 
                        padding=20, alignment=ft.Alignment(0,0)
                    )
                )

            layer_giorno.controls.extend([
                intestazione, btn_aggiungi_evento, ft.Divider(height=10, color="transparent"), 
                ft.Column(lista_card_eventi, scroll=ft.ScrollMode.AUTO, expand=True, spacing=10)
            ])

        try:
            page.update()
        except Exception:
            pass

    header = ft.Row(
        controls=[
            ft.Row([
                ft.IconButton(ft.Icons.CHEVRON_LEFT, on_click=nav_prec, icon_size=20),
                ft.IconButton(ft.Icons.CHEVRON_RIGHT, on_click=nav_succ, icon_size=20),
                testo_header,
            ], alignment=ft.MainAxisAlignment.START, spacing=0),
            
            ft.Row([btn_giorno, btn_settimana, btn_mese], spacing=5)
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        wrap=True,         
        run_spacing=10     
    )

    sezione_filtri = ft.Column([
        ft.Text("Filtra per Categoria:", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_600),
        row_filtri_cat,
        ft.Text("Filtra per Assegnatario:", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_600),
        row_filtri_fam
    ], spacing=5)

    aggiorna_tutto()

    return ft.Container(
        content=ft.Column([
            header, 
            area_dinamica, 
            ft.Divider(height=20, color="#EEEEEE"), 
            sezione_filtri
        ], spacing=10, scroll=ft.ScrollMode.AUTO), 
        padding=5, expand=True
    )