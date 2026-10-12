import flet as ft
import calendar
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from supabase import create_client, Client
from modulo_impegni import apri_form_impegno
from modulo_sovrapposizioni import analizza_sovrapposizioni_giornata

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
        "filtro_categoria": [],
        "filtro_familiare": []
    }
    oggi = datetime.now()

    lista_categorie = []
    lista_famiglia = []
    
    def ricarica_dati_base():
        if supabase:
            try:
                res_cat = supabase.table('categorie').select('*').order('nome').execute()
                lista_categorie.clear()
                lista_categorie.extend(res_cat.data if res_cat.data else [])
                
                res_fam = supabase.table('famiglia').select('*').order('nome').execute()
                lista_famiglia.clear()
                lista_famiglia.extend(res_fam.data if res_fam.data else [])
            except Exception as e:
                print("Errore caricamento liste filtri:", e)

    ricarica_dati_base()

    testo_header = ft.Text(size=16, weight=ft.FontWeight.BOLD)
    
    btn_giorno = ft.Container(content=ft.Text("Giorno", size=12, weight=ft.FontWeight.BOLD), border_radius=5, padding=6, on_click=lambda e: cambia_vista("Giorno"))
    btn_settimana = ft.Container(content=ft.Text("Settimana", size=12, weight=ft.FontWeight.BOLD), border_radius=5, padding=6, on_click=lambda e: cambia_vista("Settimana"))
    btn_mese = ft.Container(content=ft.Text("Mese", size=12, weight=ft.FontWeight.BOLD), border_radius=5, padding=6, on_click=lambda e: cambia_vista("Mese"))

    def bordo_standard():
        return ft.Border(top=ft.BorderSide(0.5, "#E0E0E0"), bottom=ft.BorderSide(0.5, "#E0E0E0"), left=ft.BorderSide(0.5, "#E0E0E0"), right=ft.BorderSide(0.5, "#E0E0E0"))

    def bordo_personalizzato(colore):
        return ft.Border(top=ft.BorderSide(2, colore), bottom=ft.BorderSide(2, colore), left=ft.BorderSide(2, colore), right=ft.BorderSide(2, colore))

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
        if isinstance(valore, int): stato["data"] = stato["data"].replace(day=valore)
        else: stato["data"] = valore
        cambia_vista("Giorno")

    def nav_prec(e):
        if stato["vista"] == "Mese":
            primo = stato["data"].replace(day=1)
            scorso = primo - timedelta(days=1)
            stato["data"] = stato["data"].replace(year=scorso.year, month=scorso.month, day=1)
        elif stato["vista"] == "Settimana": stato["data"] -= timedelta(days=7)
        elif stato["vista"] == "Giorno": stato["data"] -= timedelta(days=1)
        aggiorna_tutto()

    def nav_succ(e):
        if stato["vista"] == "Mese":
            mese = stato["data"].month
            anno = stato["data"].year
            if mese == 12: stato["data"] = stato["data"].replace(year=anno+1, month=1, day=1)
            else: stato["data"] = stato["data"].replace(month=mese+1, day=1)
        elif stato["vista"] == "Settimana": stato["data"] += timedelta(days=7)
        elif stato["vista"] == "Giorno": stato["data"] += timedelta(days=1)
        aggiorna_tutto()

    def imposta_filtro_cat(id_cat):
        if id_cat is None: stato["filtro_categoria"].clear() 
        else:
            if id_cat in stato["filtro_categoria"]: stato["filtro_categoria"].remove(id_cat) 
            else: stato["filtro_categoria"].append(id_cat) 
        aggiorna_tutto()

    def imposta_filtro_fam(id_fam):
        if id_fam is None: stato["filtro_familiare"].clear() 
        else:
            if id_fam in stato["filtro_familiare"]: stato["filtro_familiare"].remove(id_fam) 
            else: stato["filtro_familiare"].append(id_fam) 
        aggiorna_tutto()

    def ricarica_e_aggiorna():
        ricarica_dati_base()
        aggiorna_tutto()

    page.aggiorna_vista_corrente = ricarica_e_aggiorna

    def calcola_minuti(orario_str):
        if not orario_str: return 0
        try:
            h, m = map(int, str(orario_str)[:5].split(':'))
            return h * 60 + m
        except: return 0

    def ottieni_gruppi_sovrapposti(lista_eventi):
        gruppi = []
        for ev in lista_eventi:
            inizio = calcola_minuti(ev.get('ora_inizio'))
            fine = calcola_minuti(ev.get('ora_fine')) if ev.get('ora_fine') else inizio + 60
            if fine <= inizio and ev.get('ora_fine'): fine += 1440 # Gestione orario oltre la mezzanotte
            
            inserito = False
            if gruppi:
                ultimo_gruppo = gruppi[-1]
                sovrappone = False
                for g_ev in ultimo_gruppo:
                    g_inizio = calcola_minuti(g_ev.get('ora_inizio'))
                    g_fine = calcola_minuti(g_ev.get('ora_fine')) if g_ev.get('ora_fine') else g_inizio + 60
                    if g_fine <= g_inizio and g_ev.get('ora_fine'): g_fine += 1440
                    
                    if max(inizio, g_inizio) < min(fine, g_fine):
                        sovrappone = True
                        break
                
                if sovrappone:
                    ultimo_gruppo.append(ev)
                    inserito = True
                    
            if not inserito:
                gruppi.append([ev])
        return gruppi

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

        is_tutte_cat = len(stato["filtro_categoria"]) == 0
        row_filtri_cat.controls.append(
            ft.Container(
                content=ft.Text("Tutte", size=10, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE if is_tutte_cat else ft.Colors.BLACK), 
                bgcolor=ft.Colors.BLUE_900 if is_tutte_cat else ft.Colors.TRANSPARENT, 
                border=bordo_personalizzato(ft.Colors.BLUE_900 if is_tutte_cat else ft.Colors.GREY_400),
                padding=6, border_radius=15, on_click=lambda e: imposta_filtro_cat(None)
            )
        )
        
        for c in lista_categorie:
            is_sel = c['id'] in stato["filtro_categoria"]
            row_filtri_cat.controls.append(
                ft.Container(
                    content=ft.Text(c['nome'], size=10, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE if is_sel else c['colore']), 
                    bgcolor=c['colore'] if is_sel else ft.Colors.TRANSPARENT, 
                    border=bordo_personalizzato(c['colore']), 
                    padding=6, border_radius=15, on_click=lambda e, cid=c['id']: imposta_filtro_cat(cid)
                )
            )

        is_tutti_fam = len(stato["filtro_familiare"]) == 0
        row_filtri_fam.controls.append(
            ft.Container(
                content=ft.Text("Tutti", size=10, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE if is_tutti_fam else ft.Colors.BLACK), 
                bgcolor=ft.Colors.BLUE_900 if is_tutti_fam else ft.Colors.TRANSPARENT, 
                border=bordo_personalizzato(ft.Colors.BLUE_900 if is_tutti_fam else ft.Colors.GREY_400),
                padding=6, border_radius=15, on_click=lambda e: imposta_filtro_fam(None)
            )
        )
        
        for f in lista_famiglia:
            is_sel = f['id'] in stato["filtro_familiare"]
            colore_fam = f.get('colore', '#9E9E9E')
            row_filtri_fam.controls.append(
                ft.Container(
                    content=ft.Text(f['nome'], size=10, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE if is_sel else colore_fam), 
                    bgcolor=colore_fam if is_sel else ft.Colors.TRANSPARENT, 
                    border=bordo_personalizzato(colore_fam), 
                    padding=6, border_radius=15, on_click=lambda e, fid=f['id']: imposta_filtro_fam(fid)
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
                        if stato["filtro_categoria"] and imp.get('id_categoria') not in stato["filtro_categoria"]:
                            continue
                            
                        if stato["filtro_familiare"]:
                            assegnatari = [f['id_familiare'] for f in imp.get('impegni_familiari', [])] if imp.get('impegni_familiari') else []
                            if len(assegnatari) > 0:
                                sovrapposizione = [fid for fid in assegnatari if fid in stato["filtro_familiare"]]
                                if not sovrapposizione:
                                    continue

                        val_p = imp.get('is_periodico')
                        is_periodico = True if str(val_p).lower() in ['true', '1', 't', 'y', 'yes'] else False

                        if is_periodico:
                            data_start = datetime.strptime(imp['data_evento'], "%Y-%m-%d").date()
                            data_stop = datetime.strptime(imp['data_fine_periodo'], "%Y-%m-%d").date() if imp.get('data_fine_periodo') else None
                            
                            date_escluse_str = imp.get('date_escluse') or ""
                            lista_escluse = date_escluse_str.split(',') if date_escluse_str else []

                            corrente = inizio_finestra.date()
                            fine_f = fine_finestra.date()
                            
                            while corrente <= fine_f:
                                if corrente >= data_start and corrente.weekday() == data_start.weekday():
                                    if data_stop is None or corrente <= data_stop:
                                        d_str = corrente.strftime("%Y-%m-%d")
                                        if d_str not in lista_escluse:
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
                    dizionario_eventi[k].sort(
                        key=lambda x: (0 if not x.get('ora_inizio') else 1, x.get('ora_inizio') or '24:00')
                    )

            except Exception as e:
                print("Errore caricamento impegni:", e)

        # =======================================================
        # COSTRUTTORE UNIVERSALE DELLE CARD (SCALABILE E SENZA MARGINI)
        # =======================================================
        def crea_card(imp, vista="Giorno", espandi=False, offset_minuti=0, durata_minuti=60, livello_sovrapposizione=None):
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

            assegnatari_ids = [f['id_familiare'] for f in imp.get('impegni_familiari', [])] if imp.get('impegni_familiari') else []
            imp['assegnatari_ids'] = assegnatari_ids

            # ⚡ IL TRIANGOLO SULLA CARD ORA APPARE SOLO IN VISTA GIORNO ⚡
            icona_conflitto = None
            if vista == "Giorno" and livello_sovrapposizione:
                if livello_sovrapposizione == 'lieve':
                    icona_conflitto = ft.Text("▲", color=ft.Colors.YELLOW_700, size=14, weight=ft.FontWeight.BOLD)
                elif livello_sovrapposizione == 'media':
                    icona_conflitto = ft.Text("▲", color=ft.Colors.ORANGE, size=14, weight=ft.FontWeight.BOLD)
                elif livello_sovrapposizione == 'grave':
                    icona_conflitto = ft.Text("▲", color=ft.Colors.RED, size=14, weight=ft.FontWeight.BOLD)

            if vista == "Mese":
                scala = 0.3
                min_height = 20
                padding_card = 2
                border_radius_card = 2
                content_col = ft.Row([ft.Text(f"{ora_i[:5] if ora_i else ''} {titolo}", size=7, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, no_wrap=True)], spacing=2, alignment=ft.MainAxisAlignment.START)
                bgcolor_card = cat_colore
                border_card = None
                
            elif vista == "Settimana":
                scala = 0.8
                min_height = 30
                padding_card = 4
                border_radius_card = 4
                content_col = ft.Column([
                    ft.Row([ft.Text(f"{ora_i[:5] if ora_i else ''}", size=9, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD)], spacing=2),
                    ft.Text(titolo, size=10, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, no_wrap=True)
                ], spacing=0, alignment=ft.MainAxisAlignment.START)
                bgcolor_card = cat_colore
                border_card = None
                
            else: # Giorno
                scala = 1.5
                min_height = 80 # ⚡ AUMENTATA ALTEZZA MINIMA
                padding_card = 5  # ⚡ RIDOTTO IL PADDING
                border_radius_card = 8
                riga_assegnatari = ft.Row(spacing=3, wrap=True)
                if len(assegnatari_ids) > 0:
                    for i, f_id in enumerate(assegnatari_ids):
                        fam_match = next((f for f in lista_famiglia if f['id'] == f_id), None)
                        if fam_match:
                            nome_testo = fam_match['nome'] + ("," if i < len(assegnatari_ids) - 1 else "")
                            riga_assegnatari.controls.append(ft.Text(nome_testo, size=11, color=fam_match.get('colore', ft.Colors.BLUE_900), weight=ft.FontWeight.BOLD))
                else:
                    riga_assegnatari.controls.append(ft.Text("Tutta la famiglia", size=11, color=ft.Colors.BLUE_900, italic=True))

                triangolo_categoria = ft.Text("▲", color=cat_colore, size=12)
                
                elementi_intestazione = [triangolo_categoria, ft.Text(cat_nome + icona_periodico, size=11, weight=ft.FontWeight.BOLD, color=cat_colore)]
                if icona_conflitto:
                    elementi_intestazione.append(icona_conflitto)

                content_col = ft.Column([
                    ft.Row(elementi_intestazione, spacing=5, alignment=ft.MainAxisAlignment.START),
                    ft.Text(orario, size=13, color=ft.Colors.GREY_700, weight=ft.FontWeight.BOLD),
                    ft.Text(titolo, size=16, weight=ft.FontWeight.BOLD, no_wrap=False),
                    riga_assegnatari,
                    ft.Text(note, size=12, color=ft.Colors.GREY_600) if note else ft.Container()
                ], spacing=3, scroll=ft.ScrollMode.AUTO)
                bgcolor_card = ft.Colors.WHITE
                border_card = ft.Border(left=ft.BorderSide(5, cat_colore), top=ft.BorderSide(1, "#EEEEEE"), bottom=ft.BorderSide(1, "#EEEEEE"), right=ft.BorderSide(1, "#EEEEEE"))

            spazio_vuoto = offset_minuti * scala
            altezza_card = max(durata_minuti * scala, min_height)

            card_container = ft.Container(
                content=content_col,
                bgcolor=bgcolor_card,
                padding=padding_card,
                border_radius=border_radius_card,
                border=border_card,
                ink=True,
                height=altezza_card,
                on_click=lambda e, impegno_corrente=imp: apri_form_impegno(page, data, lambda: page.pubsub.send_all("aggiorna_dati"), impegno_corrente)
            )

            if spazio_vuoto > 0:
                card_container.width = float('inf')
                wrapper = ft.Column([ft.Container(height=spazio_vuoto), card_container], spacing=0)
                if espandi:
                    wrapper.expand = 1
                return wrapper
            else:
                if espandi:
                    card_container.expand = 1
                else:
                    card_container.width = float('inf')
                return card_container


        # ==========================================
        # VISUALIZZAZIONE MESE CON AFFIANCAMENTO
        # ==========================================
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
                        gruppi_oggi = ottieni_gruppi_sovrapposti(eventi_oggi)

                        # ⚡ CALCOLA IL CONFLITTO GLOBALE DELLA GIORNATA ⚡
                        livello_giorno = None
                        if eventi_oggi:
                            lista_analisi = [{'id': ev['id'], 'ora_inizio': ev.get('ora_inizio'), 'ora_fine': ev.get('ora_fine'), 'assegnatari_ids': [f['id_familiare'] for f in ev.get('impegni_familiari', [])] if ev.get('impegni_familiari') else []} for ev in eventi_oggi]
                            mappa = analizza_sovrapposizioni_giornata(lista_analisi)
                            valori = list(mappa.values())
                            if 'grave' in valori: livello_giorno = 'grave'
                            elif 'media' in valori: livello_giorno = 'media'
                            elif 'lieve' in valori: livello_giorno = 'lieve'

                        icona_header = None
                        if livello_giorno == 'lieve': icona_header = ft.Text("▲", color=ft.Colors.YELLOW_700, size=12, weight=ft.FontWeight.BOLD)
                        elif livello_giorno == 'media': icona_header = ft.Text("▲", color=ft.Colors.ORANGE, size=12, weight=ft.FontWeight.BOLD)
                        elif livello_giorno == 'grave': icona_header = ft.Text("▲", color=ft.Colors.RED, size=12, weight=ft.FontWeight.BOLD)

                        cerchio = ft.Container(
                            content=ft.Text(str(giorno), color=ft.Colors.WHITE if is_oggi else ft.Colors.BLACK, size=12, weight=ft.FontWeight.BOLD if is_oggi else ft.FontWeight.NORMAL),
                            bgcolor=ft.Colors.BLUE_900 if is_oggi else (ft.Colors.BLUE_100 if is_sel else ft.Colors.TRANSPARENT),
                            border_radius=15, width=24, height=24, alignment=ft.Alignment(0, 0)
                        )
                        
                        riga_cerchio = ft.Row([cerchio, icona_header] if icona_header else [cerchio], alignment=ft.MainAxisAlignment.CENTER, spacing=2)

                        colonna_indicatori = ft.Column(spacing=1, expand=True, scroll=ft.ScrollMode.AUTO)
                        
                        conteggio = 0
                        for gruppo in gruppi_oggi:
                            if conteggio >= 3: break
                            if len(gruppo) == 1:
                                inizio = calcola_minuti(gruppo[0].get('ora_inizio'))
                                fine = calcola_minuti(gruppo[0].get('ora_fine')) if gruppo[0].get('ora_fine') else inizio + 60
                                se_notte = 1440 if (fine <= inizio and gruppo[0].get('ora_fine')) else 0
                                durata = max((fine + se_notte) - inizio, 30)
                                
                                colonna_indicatori.controls.append(crea_card(gruppo[0], vista="Mese", espandi=False, offset_minuti=0, durata_minuti=durata))
                            else:
                                riga_ind = ft.Row(spacing=1, vertical_alignment=ft.CrossAxisAlignment.START)
                                minuti_minimi = min([calcola_minuti(ev.get('ora_inizio')) for ev in gruppo])
                                for ev in gruppo:
                                    inizio = calcola_minuti(ev.get('ora_inizio'))
                                    fine = calcola_minuti(ev.get('ora_fine')) if ev.get('ora_fine') else inizio + 60
                                    se_notte = 1440 if (fine <= inizio and ev.get('ora_fine')) else 0
                                    durata = max((fine + se_notte) - inizio, 30)
                                    offset = inizio - minuti_minimi
                                    
                                    riga_ind.controls.append(crea_card(ev, vista="Mese", espandi=True, offset_minuti=offset, durata_minuti=durata))
                                colonna_indicatori.controls.append(riga_ind)
                            conteggio += 1
                        
                        if len(gruppi_oggi) > 3:
                            colonna_indicatori.controls.append(ft.Text(f"+ altri", size=7, color=ft.Colors.GREY_600))

                        riga.controls.append(
                            ft.Container(
                                content=ft.Column([
                                    ft.Container(content=riga_cerchio, alignment=ft.Alignment(0,0)), 
                                    ft.Container(content=colonna_indicatori, expand=True)
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


        # ==========================================
        # VISUALIZZAZIONE SETTIMANA CON AFFIANCAMENTO
        # ==========================================
        elif vista == "Settimana":
            lunedi = data - timedelta(days=data.weekday())
            domenica = lunedi + timedelta(days=6)
            testo_header.value = f"{lunedi.day} - {domenica.day} {MESI[lunedi.month][:3]} {lunedi.year}" if lunedi.month == domenica.month else f"{lunedi.day} {MESI[lunedi.month][:3]} - {domenica.day} {MESI[domenica.month][:3]} {domenica.year}"
            layer_settimana.controls.clear()
            for i in range(7):
                gc = lunedi + timedelta(days=i)
                data_str = gc.strftime("%Y-%m-%d")
                
                eventi_oggi = dizionario_eventi.get(data_str, [])
                gruppi_oggi = ottieni_gruppi_sovrapposti(eventi_oggi)

                # ⚡ CALCOLA IL CONFLITTO GLOBALE DELLA GIORNATA ⚡
                livello_giorno = None
                if eventi_oggi:
                    lista_analisi = [{'id': ev['id'], 'ora_inizio': ev.get('ora_inizio'), 'ora_fine': ev.get('ora_fine'), 'assegnatari_ids': [f['id_familiare'] for f in ev.get('impegni_familiari', [])] if ev.get('impegni_familiari') else []} for ev in eventi_oggi]
                    mappa = analizza_sovrapposizioni_giornata(lista_analisi)
                    valori = list(mappa.values())
                    if 'grave' in valori: livello_giorno = 'grave'
                    elif 'media' in valori: livello_giorno = 'media'
                    elif 'lieve' in valori: livello_giorno = 'lieve'

                icona_header = None
                if livello_giorno == 'lieve': icona_header = ft.Text("▲", color=ft.Colors.YELLOW_700, size=14, weight=ft.FontWeight.BOLD)
                elif livello_giorno == 'media': icona_header = ft.Text("▲", color=ft.Colors.ORANGE, size=14, weight=ft.FontWeight.BOLD)
                elif livello_giorno == 'grave': icona_header = ft.Text("▲", color=ft.Colors.RED, size=14, weight=ft.FontWeight.BOLD)

                cerchio_sett = ft.Container(
                    content=ft.Text(str(gc.day), color=ft.Colors.WHITE if (gc.date() == oggi.date()) else ft.Colors.BLACK, size=14, weight=ft.FontWeight.BOLD), 
                    bgcolor=ft.Colors.BLUE_900 if (gc.date() == oggi.date()) else ft.Colors.TRANSPARENT, 
                    border_radius=15, width=24, height=24, alignment=ft.Alignment(0, 0)
                )
                
                riga_cerchio = ft.Row([cerchio_sett, icona_header] if icona_header else [cerchio_sett], alignment=ft.MainAxisAlignment.CENTER, spacing=2)

                intestazione = ft.Container(
                    content=ft.Column([
                        ft.Text(GIORNI[i], size=9, color=ft.Colors.GREY_700),
                        riga_cerchio
                    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=0), padding=5
                )

                colonna_indicatori = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO)
                
                for gruppo in gruppi_oggi:
                    if len(gruppo) == 1:
                        inizio = calcola_minuti(gruppo[0].get('ora_inizio'))
                        fine = calcola_minuti(gruppo[0].get('ora_fine')) if gruppo[0].get('ora_fine') else inizio + 60
                        se_notte = 1440 if (fine <= inizio and gruppo[0].get('ora_fine')) else 0
                        durata = max((fine + se_notte) - inizio, 30)
                        
                        colonna_indicatori.controls.append(crea_card(gruppo[0], vista="Settimana", espandi=False, offset_minuti=0, durata_minuti=durata))
                    else:
                        riga_ind = ft.Row(spacing=2, vertical_alignment=ft.CrossAxisAlignment.START)
                        minuti_minimi = min([calcola_minuti(ev.get('ora_inizio')) for ev in gruppo])
                        for ev in gruppo:
                            inizio = calcola_minuti(ev.get('ora_inizio'))
                            fine = calcola_minuti(ev.get('ora_fine')) if ev.get('ora_fine') else inizio + 60
                            se_notte = 1440 if (fine <= inizio and ev.get('ora_fine')) else 0
                            durata = max((fine + se_notte) - inizio, 30)
                            offset = inizio - minuti_minimi
                            
                            riga_ind.controls.append(crea_card(ev, vista="Settimana", espandi=True, offset_minuti=offset, durata_minuti=durata))
                        colonna_indicatori.controls.append(riga_ind)

                layer_settimana.controls.append(
                    ft.Container(
                        content=ft.Column([
                            intestazione, ft.Divider(height=1, color="#E0E0E0"), 
                            ft.Container(content=colonna_indicatori, padding=2, expand=True)
                        ]), 
                        expand=1, height=300, border=bordo_standard(), data=gc, on_click=vai_a_giorno
                    )
                )


        # ==========================================
        # VISUALIZZAZIONE GIORNO CON AFFIANCAMENTO
        # ==========================================
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
                on_click=lambda e: apri_form_impegno(page, data, lambda: page.pubsub.send_all("aggiorna_dati"))
            )

            colonna_card_eventi = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, spacing=10)
            data_str = data.strftime("%Y-%m-%d")
            
            eventi_oggi = dizionario_eventi.get(data_str, [])

            if eventi_oggi:
                lista_per_analisi = []
                for ev in eventi_oggi:
                    lista_per_analisi.append({
                        'id': ev['id'],
                        'ora_inizio': ev.get('ora_inizio'),
                        'ora_fine': ev.get('ora_fine'),
                        'assegnatari_ids': [f['id_familiare'] for f in ev.get('impegni_familiari', [])] if ev.get('impegni_familiari') else []
                    })
                mappa_conflitti = analizza_sovrapposizioni_giornata(lista_per_analisi)

                gruppi_oggi = ottieni_gruppi_sovrapposti(eventi_oggi)
                
                for gruppo in gruppi_oggi:
                    if len(gruppo) == 1:
                        inizio = calcola_minuti(gruppo[0].get('ora_inizio'))
                        fine = calcola_minuti(gruppo[0].get('ora_fine')) if gruppo[0].get('ora_fine') else inizio + 60
                        se_notte = 1440 if (fine <= inizio and gruppo[0].get('ora_fine')) else 0
                        durata = max((fine + se_notte) - inizio, 30)
                        
                        livello = mappa_conflitti.get(gruppo[0]['id'])
                        colonna_card_eventi.controls.append(crea_card(gruppo[0], vista="Giorno", espandi=False, offset_minuti=0, durata_minuti=durata, livello_sovrapposizione=livello))
                    else:
                        riga_sovrapposti = ft.Row(spacing=10, vertical_alignment=ft.CrossAxisAlignment.START)
                        minuti_minimi = min([calcola_minuti(ev.get('ora_inizio')) for ev in gruppo])
                        
                        for imp in gruppo:
                            inizio = calcola_minuti(imp.get('ora_inizio'))
                            fine = calcola_minuti(imp.get('ora_fine')) if imp.get('ora_fine') else inizio + 60
                            se_notte = 1440 if (fine <= inizio and imp.get('ora_fine')) else 0
                            durata = max((fine + se_notte) - inizio, 30)
                            offset = inizio - minuti_minimi
                            
                            livello = mappa_conflitti.get(imp['id'])
                            riga_sovrapposti.controls.append(crea_card(imp, vista="Giorno", espandi=True, offset_minuti=offset, durata_minuti=durata, livello_sovrapposizione=livello))
                            
                        colonna_card_eventi.controls.append(riga_sovrapposti)
            else:
                colonna_card_eventi.controls.append(
                    ft.Container(
                        content=ft.Text("Nessun evento corrispondente ai filtri.", color=ft.Colors.GREY_500, italic=True), 
                        padding=20, alignment=ft.Alignment(0,0)
                    )
                )

            layer_giorno.controls.extend([
                intestazione, btn_aggiungi_evento, ft.Divider(height=10, color="transparent"), 
                colonna_card_eventi
            ])

        try:
            page.update()
        except Exception:
            pass

    return ft.Container(
        content=ft.Column([
            header, 
            area_dinamica, 
            ft.Divider(height=20, color="#EEEEEE"), 
            sezione_filtri
        ], spacing=10, scroll=ft.ScrollMode.AUTO), 
        padding=5, expand=True
    )
