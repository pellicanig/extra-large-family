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

def crea_calendario(page: ft.Page, stato_condiviso: dict = None, on_switch_budget=None):
    stato = {
        "data": (stato_condiviso.get("data") if stato_condiviso else None) or datetime.now(), 
        "vista": (stato_condiviso.get("vista") if stato_condiviso else None) or "Settimana",
        "filtro_categoria": [],
        "filtro_familiare": []
    }
    oggi = datetime.now()

    def sincronizza_stato_esterno():
        if stato_condiviso is not None:
            stato_condiviso["data"] = stato["data"]
            stato_condiviso["vista"] = stato["vista"]

    lista_categorie = []
    lista_famiglia = []
    mappa_famiglia = {}
    
    def ricarica_dati_base():
        if not supabase:
            return
        for tentativo in range(3):
            try:
                res_cat = supabase.table('categorie').select('*').order('nome').execute()
                lista_categorie.clear()
                lista_categorie.extend(res_cat.data if res_cat.data else [])
                
                res_fam = supabase.table('famiglia').select('*').order('nome').execute()
                lista_famiglia.clear()
                lista_famiglia.extend(res_fam.data if res_fam.data else [])
                break
            except Exception as e:
                if tentativo == 2:
                    print("Errore caricamento liste filtri:", e)
                else:
                    import time
                    time.sleep(0.3)

    ricarica_dati_base()

    testo_header = ft.Text(size=16, weight=ft.FontWeight.BOLD)
    
    btn_giorno = ft.Container(content=ft.Text("Giorno", size=12, weight=ft.FontWeight.BOLD), border_radius=5, padding=6, on_click=lambda e: cambia_vista("Giorno"))
    btn_settimana = ft.Container(content=ft.Text("Settimana", size=12, weight=ft.FontWeight.BOLD), border_radius=5, padding=6, on_click=lambda e: cambia_vista("Settimana"))
    btn_mese = ft.Container(content=ft.Text("Mese", size=12, weight=ft.FontWeight.BOLD), border_radius=5, padding=6, on_click=lambda e: cambia_vista("Mese"))

    def bordo_standard():
        return ft.Border(top=ft.BorderSide(0.5, "#E0E0E0"), bottom=ft.BorderSide(0.5, "#E0E0E0"), left=ft.BorderSide(0.5, "#E0E0E0"), right=ft.BorderSide(0.5, "#E0E0E0"))

    def bordo_personalizzato(colore):
        return ft.Border(top=ft.BorderSide(2, colore), bottom=ft.BorderSide(2, colore), left=ft.BorderSide(2, colore), right=ft.BorderSide(2, colore))

    def bordo_evidenziato_blu():
        return ft.Border(
            top=ft.BorderSide(2.5, "#64B5F6"),
            left=ft.BorderSide(2.5, "#2196F3"),
            right=ft.BorderSide(2.5, "#1565C0"),
            bottom=ft.BorderSide(2.5, "#0D47A1")
        )

    def ha_eventi_evidenziati(lista_ev):
        for ev in lista_ev:
            val_p = ev.get('is_periodico')
            is_p = True if str(val_p).lower() in ['true', '1', 't', 'y', 'yes'] else False
            val_ev = ev.get('is_evidenziato')
            is_ev = (not is_p) if val_ev is None else (True if str(val_ev).lower() in ['true', '1', 't', 'y', 'yes'] else False)
            if is_ev:
                return True
        return False

    layer_mese = ft.Column(spacing=0, visible=True)
    layer_settimana = ft.Column(spacing=0, visible=False)
    layer_giorno = ft.Column(spacing=0, visible=False)
    area_dinamica = ft.Column([layer_mese, layer_settimana, layer_giorno], expand=True, spacing=0)

    row_filtri_cat = ft.Row(wrap=True, spacing=5)
    row_filtri_fam = ft.Row(wrap=True, spacing=5)

    def cambia_vista(nuova_vista):
        stato["vista"] = nuova_vista
        sincronizza_stato_esterno()
        aggiorna_tutto()
    
    def vai_a_giorno(e):
        valore = e.control.data
        if isinstance(valore, int): stato["data"] = stato["data"].replace(day=valore)
        else: stato["data"] = valore
        sincronizza_stato_esterno()
        cambia_vista("Giorno")

    def nav_prec(e):
        if stato["vista"] == "Mese":
            primo = stato["data"].replace(day=1)
            scorso = primo - timedelta(days=1)
            stato["data"] = stato["data"].replace(year=scorso.year, month=scorso.month, day=1)
        elif stato["vista"] == "Settimana": stato["data"] -= timedelta(days=7)
        elif stato["vista"] == "Giorno": stato["data"] -= timedelta(days=1)
        sincronizza_stato_esterno()
        aggiorna_tutto()

    def nav_succ(e):
        if stato["vista"] == "Mese":
            mese = stato["data"].month
            anno = stato["data"].year
            if mese == 12: stato["data"] = stato["data"].replace(year=anno+1, month=1, day=1)
            else: stato["data"] = stato["data"].replace(month=mese+1, day=1)
        elif stato["vista"] == "Settimana": stato["data"] += timedelta(days=7)
        elif stato["vista"] == "Giorno": stato["data"] += timedelta(days=1)
        sincronizza_stato_esterno()
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
            if fine <= inizio and ev.get('ora_fine'): fine += 1440
            
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
        nonlocal mappa_famiglia
        mappa_famiglia = {f["id"]: f for f in lista_famiglia}
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
                
                filtro_finestra = (
                    f"and(data_evento.gte.{primo_g},data_evento.lte.{ultimo_g}),"
                    f"and(is_periodico.eq.true,data_evento.lt.{primo_g},"
                    f"or(data_fine_periodo.is.null,data_fine_periodo.gte.{primo_g}))"
                )
                res = (
                    supabase.table('impegni')
                    .select('*, categorie(colore, nome), impegni_familiari(id_familiare)')
                    .or_(filtro_finestra)
                    .execute()
                )
                
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
                            
                            giorni_str = imp.get('giorni_periodici')
                            if giorni_str:
                                giorni_da_ripetere = [int(g) for g in giorni_str.split(',') if g.strip().isdigit()]
                            else:
                                giorni_da_ripetere = [data_start.weekday()]

                            corrente = inizio_finestra.date()
                            fine_f = fine_finestra.date()
                            
                            while corrente <= fine_f:
                                if corrente >= data_start and corrente.weekday() in giorni_da_ripetere:
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

            val_ev = imp.get('is_evidenziato')
            if val_ev is None:
                is_evidenziato = not is_p
            else:
                is_evidenziato = True if str(val_ev).lower() in ['true', '1', 't', 'y', 'yes'] else False

            assegnatari_ids = [f['id_familiare'] for f in imp.get('impegni_familiari', [])] if imp.get('impegni_familiari') else []
            imp['assegnatari_ids'] = assegnatari_ids

            icona_conflitto = None
            if vista in ["Giorno", "Settimana"] and livello_sovrapposizione:
                if livello_sovrapposizione == 'lieve': icona_conflitto = ft.Icon(ft.Icons.WARNING_ROUNDED, color=ft.Colors.YELLOW_700, size=16)
                elif livello_sovrapposizione == 'media': icona_conflitto = ft.Icon(ft.Icons.WARNING_ROUNDED, color=ft.Colors.ORANGE, size=16)
                elif livello_sovrapposizione == 'grave': icona_conflitto = ft.Icon(ft.Icons.WARNING_ROUNDED, color=ft.Colors.RED, size=16)

            colori_assegnatari = []
            if len(assegnatari_ids) > 0:
                for f_id in assegnatari_ids:
                    fam_match = mappa_famiglia.get(f_id)
                    if fam_match and fam_match.get('colore'):
                        colori_assegnatari.append(fam_match['colore'])
            else:
                colori_assegnatari = [f['colore'] for f in lista_famiglia if f.get('colore')]

            sfondo_card = None
            gradiente_card = None

            if is_evidenziato:
                colore_testo = ft.Colors.WHITE
                if len(colori_assegnatari) == 0:
                    sfondo_card = "#ECEFF1" 
                    colore_testo = ft.Colors.BLACK 
                elif len(colori_assegnatari) == 1:
                    sfondo_card = colori_assegnatari[0] 
                else:
                    gradiente_card = ft.LinearGradient(
                        begin=ft.Alignment(-1.0, -1.0), 
                        end=ft.Alignment(1.0, 1.0), 
                        colors=colori_assegnatari
                    )
                colore_cat_header = colore_testo
                colore_orario = colore_testo
                colore_note = colore_testo
            else:
                sfondo_card = ft.Colors.WHITE
                gradiente_card = None
                colore_testo = ft.Colors.BLACK
                colore_cat_header = cat_colore
                colore_orario = ft.Colors.GREY_700
                colore_note = ft.Colors.GREY_600

            riga_assegnatari = ft.Row(spacing=2, wrap=True)
            elementi_mese_assegnatari = []

            if len(assegnatari_ids) > 0:
                for i, f_id in enumerate(assegnatari_ids):
                    fam_match = mappa_famiglia.get(f_id)
                    if fam_match:
                        nome_testo = fam_match['nome'] + ("," if i < len(assegnatari_ids) - 1 else "")
                        col_asg = colore_testo if is_evidenziato else fam_match.get('colore', ft.Colors.BLUE_900)
                        if vista in ["Giorno", "Settimana"]:
                            riga_assegnatari.controls.append(ft.Text(nome_testo, size=9 if vista=="Settimana" else 10, color=col_asg, weight=ft.FontWeight.BOLD))
                        else:
                            elementi_mese_assegnatari.append(ft.Text(nome_testo, size=7, color=col_asg, weight=ft.FontWeight.BOLD, no_wrap=True))
            else:
                col_asg = colore_testo if is_evidenziato else ft.Colors.BLUE_900
                if vista in ["Giorno", "Settimana"]:
                    riga_assegnatari.controls.append(ft.Text("Tutta la famiglia", size=9 if vista=="Settimana" else 10, color=col_asg, italic=True))
                else:
                    elementi_mese_assegnatari.append(ft.Text("Tutti", size=7, color=col_asg, italic=True, no_wrap=True))

            border_card = ft.Border(left=ft.BorderSide(4 if vista in ["Mese", "Settimana"] else 5, cat_colore), top=ft.BorderSide(1, "#E0E0E0"), bottom=ft.BorderSide(1, "#E0E0E0"), right=ft.BorderSide(1, "#E0E0E0"))

            if vista == "Mese":
                testo_base_mese = ft.Text(f"{ora_i[:5] if ora_i else ''} {titolo}", size=7, color=colore_testo, weight=ft.FontWeight.BOLD, no_wrap=True)
                content_col = ft.Row([testo_base_mese] + elementi_mese_assegnatari, spacing=3, alignment=ft.MainAxisAlignment.START)
                altezza_card = 20 
                spazio_vuoto = 0 
            elif vista == "Settimana":
                elementi_intestazione = [ft.Icon(ft.Icons.CIRCLE, color=colore_cat_header, size=7), ft.Text(cat_nome + icona_periodico, size=8, weight=ft.FontWeight.BOLD, color=colore_cat_header)]
                if icona_conflitto: elementi_intestazione.append(icona_conflitto)

                elementi_settimana = [
                    ft.Row(elementi_intestazione, spacing=3, alignment=ft.MainAxisAlignment.START),
                    ft.Text(orario, size=9, color=colore_orario, weight=ft.FontWeight.BOLD),
                    ft.Text(titolo, size=10, color=colore_testo, weight=ft.FontWeight.BOLD, no_wrap=False),
                    riga_assegnatari
                ]
                if note:
                    elementi_settimana.append(ft.Text(note, size=8, color=colore_note, no_wrap=False))
                    
                content_col = ft.Column(elementi_settimana, spacing=1, alignment=ft.MainAxisAlignment.START)
                altezza_card = max(durata_minuti * 0.9, 65)
                spazio_vuoto = offset_minuti * 0.9
            else:
                elementi_intestazione = [ft.Icon(ft.Icons.CIRCLE, color=colore_cat_header, size=9), ft.Text(cat_nome + icona_periodico, size=10, weight=ft.FontWeight.BOLD, color=colore_cat_Per integrare il modulo **Spese, Entrate e Budget** mantenendo l'architettura, la grafica e il sistema di notifiche Web Push di **ELF**[cite: 1, 2, 6, 9], la soluzione prevede:

1. **Tabella e Categorie su Supabase**: Script SQL per creare `categorie_budget`, `movimenti_budget` e `movimenti_budget_familiari` (con allineamento automatico al tipo di ID della tabella `famiglia` esistente[cite: 2, 3, 4]).
2. **Nuovo file `modulo_budget.py` (Indipendente)**:
   * Stessa struttura del calendario (**Mese**, **Settimana**, **Giorno**)[cite: 2] + **Cruscotto Budget Mensile** (Entrate Previste/Effettive, Spese Previste/Effettive, Saldo Attuale e Saldo Previsionale di fine mese) + filtri multipli per Tipo, Stato, Categoria e Familiare[cite: 2].
   * **Spese Sporadiche e Pianificate**: Campi per **Tipo** (*Spesa* / *Entrata*), **Stato** (*Prevista* / *Effettiva*), **Importo (€)**, **Luogo / Fornitore** (es. *Decò*, *Plenitude* con autocompletamento dai fornitori già usati) e **Titolo/Descrizione** (opzionale: se lasciato vuoto per una spesa veloce, viene generato automaticamente da *Categoria - Luogo/Fornitore*).
   * **Trasformazione Prevista ➔ Effettiva in 1 click**: Sia tramite il pulsante rapido **✅ Paga / Incassa** sulle card, sia dentro il form di modifica, puoi trasformare una voce prevista in effettiva modificando **data reale** e **importo effettivo**. Se la voce fa parte di una serie periodica (es. *Mutuo* o *Stipendio*), il sistema segna come saldata quella specifica scadenza mensile e crea il movimento effettivo senza alterare le scadenze future[cite: 4].
   * **Periodicità Completa**: Supporta ricorrenze **Mensili** (es. giorno 27 per lo stipendio o giorno 1 per il mutuo), **Bimestrali** (es. utenze), **Trimestrali**, **Semestrali**, **Annuali** o **Settimanali**, con modifica su *"Solo questa scadenza"*, *"Da questa in poi"* o *"Tutta la serie"* come per gli impegni[cite: 4].
   * **Notifiche alla Scadenza**: Switch dedicato **🔔 Avvisami alla scadenza** per scegliere se ricevere la notifica e con quale anticipo/ripetizione[cite: 4, 9].
3. **Switch immediato tra Impegni e Spese nel Giorno**: Aprendo qualsiasi giorno nel calendario puoi passare istantaneamente tra **📅 Impegni** e **💶 Spese / Entrate** di quel giorno, oltre a poter navigare dalla barra principale in alto[cite: 1, 2].

---

### 1. Script SQL da eseguire su Supabase (SQL Editor)

Esegui questo script nell'**SQL Editor** di Supabase per creare le tabelle necessarie e precaricare le categorie base:

```sql
-- 1. Tabella Categorie Budget (Spese ed Entrate)
CREATE TABLE IF NOT EXISTS public.categorie_budget (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    nome text NOT NULL,
    colore text DEFAULT '#2196F3',
    tipo_default text DEFAULT 'spesa', -- 'spesa', 'entrata', 'entrambi'
    creato_il timestamptz DEFAULT now()
);

-- 2. Tabella Movimenti Budget (Spese ed Entrate, Previste ed Effettive, Singole e Periodiche)
CREATE TABLE IF NOT EXISTS public.movimenti_budget (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    tipo text NOT NULL DEFAULT 'spesa',              -- 'spesa' oppure 'entrata'
    stato text NOT NULL DEFAULT 'effettivo',         -- 'previsto' oppure 'effettivo'
    titolo text NOT NULL,
    luogo_fornitore text,                            -- es. Decò, Plenitude, Enel, INPS...
    importo numeric(12,2) NOT NULL DEFAULT 0,
    importo_previsto_orig numeric(12,2),             -- conserva l'importo previsto originario post-trasformazione
    data_movimento date NOT NULL,
    ora_scadenza text DEFAULT '09:00',               -- orario di riferimento per la notifica di scadenza
    id_categoria uuid REFERENCES public.categorie_budget(id) ON DELETE SET NULL,
    note text,
    is_periodico boolean DEFAULT false,
    tipo_periodicita text DEFAULT 'mensile',         -- 'mensile', 'bimestrale', 'trimestrale', 'semestrale', 'annuale', 'settimanale'
    giorni_periodici text,                           -- usato se tipo_periodicita = 'settimanale'
    data_fine_periodo date,
    date_escluse text,
    notifica_attiva boolean DEFAULT true,
    preavviso_valore integer DEFAULT 1,
    preavviso_unita text DEFAULT 'giorni',           -- 'minuti', 'ore', 'giorni', 'settimane'
    preavviso_ripeti boolean DEFAULT true,
    is_evidenziato boolean DEFAULT true,
    creato_il timestamptz DEFAULT now()
);

-- 3. Tabella di associazione Movimenti <-> Componenti della Famiglia
-- (Rileva automaticamente il tipo della colonna id di public.famiglia)
DO $$
DECLARE
    tipo_id_fam text;
BEGIN
    SELECT data_type INTO tipo_id_fam
    FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'famiglia' AND column_name = 'id';

    IF tipo_id_fam IS NULL THEN
        tipo_id_fam := 'bigint';
    END IF;

    EXECUTE format('
        CREATE TABLE IF NOT EXISTS public.movimenti_budget_familiari (
            id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
            id_movimento uuid REFERENCES public.movimenti_budget(id) ON DELETE CASCADE,
            id_familiare %s REFERENCES public.famiglia(id) ON DELETE CASCADE
        );
    ', tipo_id_fam);
END $$;

-- 4. Inserimento Categorie Iniziali (se la tabella è vuota)
INSERT INTO public.categorie_budget (nome, colore, tipo_default)
SELECT * FROM (VALUES
    ('VITTO / SPESA', '#4CAF50', 'spesa'),
    ('UTENZE DOMESTICHE', '#FF9800', 'spesa'),
    ('MUTUO / AFFITTO', '#F44336', 'spesa'),
    ('AUTO / TRASPORTI', '#2196F3', 'spesa'),
    ('SALUTE / FARMACIA', '#9C27B0', 'spesa'),
    ('SCUOLA / FIGLI', '#FFC107', 'spesa'),
    ('CASA / EXTRA', '#795548', 'spesa'),
    ('STIPENDIO', '#4CAF50', 'entrata'),
    ('ALTRE ENTRATE', '#2196F3', 'entrata')
) AS v(nome, colore, tipo_default)
WHERE NOT EXISTS (SELECT 1 FROM public.categorie_budget LIMIT 1);
