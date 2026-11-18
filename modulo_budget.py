import flet as ft
import calendar
import os
import threading
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
from supabase import create_client, Client
from modulo_ui import inizia_operazione, termina_operazione

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if (SUPABASE_URL and SUPABASE_KEY) else None
except Exception as e:
    supabase = None
    print(f"Errore connessione Supabase in modulo_budget: {e}")

MESI = ["", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]
GIORNI = ["LUN", "MAR", "MER", "GIO", "VEN", "SAB", "DOM"]

PALETTE_COLORI = [
    {"nome": "🔴", "hex": "#F44336"},
    {"nome": "🟠", "hex": "#FF9800"},
    {"nome": "🟡", "hex": "#FFC107"},
    {"nome": "🟢", "hex": "#4CAF50"},
    {"nome": "🔵", "hex": "#2196F3"},
    {"nome": "🟣", "hex": "#9C27B0"},
    {"nome": "🟤", "hex": "#795548"},
    {"nome": "⚫", "hex": "#212121"},
    {"nome": "⚪", "hex": "#9E9E9E"},
]

TIPI_PERIODICITA = [
    ("MENSILE", "Mensile (ogni mese)"),
    ("BIMESTRALE", "Bimestrale (ogni 2 mesi)"),
    ("TRIMESTRALE", "Trimestrale (ogni 3 mesi)"),
    ("SEMESTRALE", "Semestrale (ogni 6 mesi)"),
    ("ANNUALE", "Annuale (ogni anno)"),
    ("SETTIMANALE", "Settimanale (giorni scelti)"),
]

PASSO_MESI_PERIODICITA = {
    "MENSILE": 1,
    "BIMESTRALE": 2,
    "TRIMESTRALE": 3,
    "SEMESTRALE": 6,
    "ANNUALE": 12,
}


def normalizza_stato_budget(val) -> str:
    s = str(val or "PREVISTO").strip().upper()
    return "EFFETTIVO" if s.startswith("EFFETTIV") else "PREVISTO"


def formatta_testo_intelligente(testo: str) -> str:
    t = (testo or "").strip()
    if not t:
        return ""
    if t.isupper() or t.islower():
        return t.capitalize()
    return t[0].upper() + t[1:]


def parse_importo(val_str) -> float:
    if val_str is None:
        return 0.0
    s = str(val_str).replace("€", "").replace("\u200b", "").strip()
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        return round(float(s), 2)
    except ValueError:
        return 0.0


def formatta_euro(val) -> str:
    try:
        num = float(val or 0.0)
    except Exception:
        num = 0.0
    s = f"{abs(num):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    pref = "-" if num < -0.004 else ""
    return f"{pref}{s} €"


def _apri_dlg(page: ft.Page, dlg: ft.AlertDialog):
    try:
        page.open(dlg)
    except AttributeError:
        if dlg not in page.overlay:
            page.overlay.append(dlg)
        dlg.open = True
    try:
        page.update()
    except Exception:
        pass


def _chiudi_dlg(page: ft.Page, dlg: ft.AlertDialog):
    if not dlg:
        return
    dlg.open = False
    try:
        page.close(dlg)
    except Exception:
        pass
    try:
        page.update()
    except Exception:
        pass


def occorrenza_periodica_valida(mov: dict, target_date: date) -> bool:
    """Verifica se un movimento periodico cade nella data target_date."""
    try:
        d_start = datetime.strptime(str(mov["data_movimento"])[:10], "%Y-%m-%d").date()
    except Exception:
        return False

    if target_date < d_start:
        return False

    if mov.get("data_fine_periodo"):
        try:
            d_stop = datetime.strptime(str(mov["data_fine_periodo"])[:10], "%Y-%m-%d").date()
            if target_date > d_stop:
                return False
        except Exception:
            pass

    escluse_raw = mov.get("date_escluse") or ""
    escluse = {x.strip() for x in escluse_raw.split(",") if x.strip()}
    if target_date.strftime("%Y-%m-%d") in escluse:
        return False

    tipo_per = str(mov.get("tipo_periodicita") or "MENSILE").upper().strip()
    if tipo_per == "SETTIMANALE":
        g_str = mov.get("giorni_periodici") or ""
        giorni_rep = [int(g) for g in g_str.split(",") if g.strip().isdigit()] if g_str else [d_start.weekday()]
        return target_date.weekday() in giorni_rep

    passo_mesi = PASSO_MESI_PERIODICITA.get(tipo_per, 1)
    diff_mesi = (target_date.year - d_start.year) * 12 + (target_date.month - d_start.month)
    if diff_mesi < 0 or (diff_mesi % passo_mesi) != 0:
        return False

    ultimo_gg_mese = calendar.monthrange(target_date.year, target_date.month)[1]
    giorno_target = min(d_start.day, ultimo_gg_mese)
    return target_date.day == giorno_target


def espandi_movimenti_in_finestra(lista_movimenti: list, d_inizio: date, d_fine: date) -> list:
    """Espande i movimenti singoli e periodici nell'intervallo [d_inizio, d_fine]."""
    risultati = []
    for mov in (lista_movimenti or []):
        val_p = mov.get("is_periodico")
        is_p = str(val_p).lower() in ["true", "1", "t", "y", "yes"]
        assegnatari_ids = [
            f["id_familiare"] for f in (mov.get("movimenti_budget_familiari") or []) if f.get("id_familiare")
        ]

        if is_p:
            curr = d_inizio
            while curr <= d_fine:
                if occorrenza_periodica_valida(mov, curr):
                    copia = dict(mov)
                    copia["stato"] = normalizza_stato_budget(mov.get("stato"))
                    copia["data_originale_serie"] = mov.get("data_movimento")
                    copia["data_movimento"] = curr.strftime("%Y-%m-%d")
                    copia["assegnatari_ids"] = list(assegnatari_ids)
                    risultati.append(copia)
                curr += timedelta(days=1)
        else:
            d_str = str(mov.get("data_movimento") or "")[:10]
            if d_inizio.strftime("%Y-%m-%d") <= d_str <= d_fine.strftime("%Y-%m-%d"):
                copia = dict(mov)
                copia["stato"] = normalizza_stato_budget(mov.get("stato"))
                copia["data_originale_serie"] = d_str
                copia["assegnatari_ids"] = list(assegnatari_ids)
                risultati.append(copia)
    return risultati


# ==========================================================
# POPUP RAPIDO: TRASFORMA PREVISTO -> EFFETTIVO (MODIFICA DATA E IMPORTO)
# ==========================================================
def apri_popup_trasforma_in_effettivo(page: ft.Page, mov: dict, on_success_callback=None):
    if not supabase:
        return

    data_istanza_str = str(mov.get("data_movimento") or datetime.now().strftime("%Y-%m-%d"))[:10]
    try:
        dt_ist = datetime.strptime(data_istanza_str, "%Y-%m-%d")
    except Exception:
        dt_ist = datetime.now()

    val_p = mov.get("is_periodico")
    is_p_db = str(val_p).lower() in ["true", "1", "t", "y", "yes"]
    tipo_mov = str(mov.get("tipo") or "SPESA").upper()
    titolo_mov = mov.get("titolo") or "Movimento"
    fornitore_mov = mov.get("fornitore_luogo") or ""
    imp_previsto = float(mov.get("importo") or 0.0)

    def formatta_data_eff(e):
        val_pulito = "".join([c for c in (txt_data_eff.value or "") if c.isdigit()])
        ris = ""
        for i, num in enumerate(val_pulito):
            if i in (2, 4):
                ris += "/"
            ris += num
        ris = ris[:10]
        if txt_data_eff.value != ris:
            txt_data_eff.value = ris
            txt_data_eff.error_text = None
            page.update()

    txt_data_eff = ft.TextField(
        label="Data Effettiva Pagamento / Incasso",
        value=dt_ist.strftime("%d/%m/%Y"),
        on_change=formatta_data_eff,
        dense=True,
        text_size=13,
    )
    txt_importo_eff = ft.TextField(
        label="Importo Effettivo (€)",
        value=f"{imp_previsto:.2f}".replace(".", ","),
        dense=True,
        text_size=14,
        autofocus=True,
    )
    txt_fornitore_eff = ft.TextField(
        label="Luogo / Fornitore (es. Decò, Plenitude...)",
        value=fornitore_mov,
        dense=True,
        text_size=13,
    )
    txt_err = ft.Text("", color=ft.Colors.RED_700, size=12, weight=ft.FontWeight.BOLD, visible=False)
    dlg_conv = None

    def conferma_conversione(e):
        txt_err.visible = False
        try:
            dt_eff = datetime.strptime((txt_data_eff.value or "").strip(), "%d/%m/%Y")
            data_eff_db = dt_eff.strftime("%Y-%m-%d")
        except Exception:
            txt_err.value = "⚠️ Data non valida! Usa il formato GG/MM/AAAA."
            txt_err.visible = True
            page.update()
            return

        imp_eff = parse_importo(txt_importo_eff.value)
        if imp_eff <= 0:
            txt_err.value = "⚠️ Inserisci un importo effettivo maggiore di 0."
            txt_err.visible = True
            page.update()
            return

        forn_clean = formatta_testo_intelligente(txt_fornitore_eff.value) if txt_fornitore_eff.value else None

        if not inizia_operazione(page):
            return

        def worker():
            try:
                if is_p_db:
                    res_orig = supabase.table("movimenti_budget").select("date_escluse").eq("id", mov["id"]).execute()
                    escl_attuali = (res_orig.data[0].get("date_escluse") or "") if res_orig.data else (mov.get("date_escluse") or "")
                    lista_escl = [x.strip() for x in escl_attuali.split(",") if x.strip()]
                    if data_istanza_str not in lista_escl:
                        lista_escl.append(data_istanza_str)
                    nuove_escl = ",".join(lista_escl)
                    supabase.table("movimenti_budget").update({"date_escluse": nuove_escl}).eq("id", mov["id"]).execute()

                    nuovo_rec = {
                        "tipo": tipo_mov,
                        "stato": "EFFETTIVO",
                        "titolo": titolo_mov,
                        "fornitore_luogo": forn_clean,
                        "importo": imp_eff,
                        "importo_previsto_originale": imp_previsto,
                        "data_movimento": data_eff_db,
                        "id_categoria": mov.get("id_categoria"),
                        "note": mov.get("note"),
                        "is_periodico": False,
                        "tipo_periodicita": None,
                        "giorni_periodici": None,
                        "data_fine_periodo": None,
                        "date_escluse": None,
                        "notifica_attiva": False,
                        "ora_notifica": mov.get("ora_notifica") or "09:00",
                        "preavviso_valore": mov.get("preavviso_valore") or 1,
                        "preavviso_unita": mov.get("preavviso_unita") or "giorni",
                        "preavviso_ripeti": False,
                        "is_evidenziato": True,
                    }
                    res_ins = supabase.table("movimenti_budget").insert(nuovo_rec).execute()
                    if res_ins.data:
                        nuovo_id = res_ins.data[0]["id"]
                        for id_f in (mov.get("assegnatari_ids") or []):
                            supabase.table("movimenti_budget_familiari").insert({
                                "id_movimento": nuovo_id,
                                "id_familiare": id_f
                            }).execute()
                else:
                    supabase.table("movimenti_budget").update({
                        "stato": "EFFETTIVO",
                        "data_movimento": data_eff_db,
                        "importo": imp_eff,
                        "importo_previsto_originale": imp_previsto,
                        "fornitore_luogo": forn_clean,
                        "notifica_attiva": False,
                    }).eq("id", mov["id"]).execute()

                _chiudi_dlg(page, dlg_conv)
                page.pubsub.send_all("aggiorna_budget")
                page.pubsub.send_all("aggiorna_dati")
                if on_success_callback:
                    on_success_callback()
            except Exception as ex:
                txt_err.value = f"⚠️ Errore DB: {ex}"
                txt_err.visible = True
                page.update()
            finally:
                termina_operazione(page)

        threading.Thread(target=worker, daemon=True).start()

    dlg_conv = ft.AlertDialog(
        title=ft.Text(
            f"✅ Da Prevista a Effettiva ({'Spesa' if tipo_mov == 'SPESA' else 'Entrata'})",
            size=15,
            weight=ft.FontWeight.BOLD,
            color=ft.Colors.GREEN_800,
        ),
        content=ft.Container(
            content=ft.Column([
                ft.Text(f"📌 {titolo_mov}", size=13, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900),
                ft.Text(
                    f"Importo previsto: {formatta_euro(imp_previsto)} • Scadenza: {dt_ist.strftime('%d/%m/%Y')}",
                    size=11,
                    color=ft.Colors.GREY_700,
                ),
                ft.Divider(height=4),
                txt_data_eff,
                txt_importo_eff,
                txt_fornitore_eff,
                txt_err,
            ], tight=True, spacing=8),
            width=330,
        ),
        actions=[
            ft.TextButton("Annulla", on_click=lambda e: _chiudi_dlg(page, dlg_conv)),
            ft.Container(
                content=ft.Text("✅ Conferma Effettiva", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=12),
                bgcolor=ft.Colors.GREEN_700,
                padding=ft.Padding(12, 8, 12, 8),
                border_radius=6,
                on_click=conferma_conversione,
            ),
        ],
        shape=ft.RoundedRectangleBorder(radius=10),
    )
    _apri_dlg(page, dlg_conv)


# ==========================================================
# FORM COMPLETO NUOVA / MODIFICA SPESA O ENTRATA
# ==========================================================
def apri_form_movimento_budget(
    page: ft.Page,
    data_selezionata: datetime,
    on_success_callback=None,
    movimento_esistente=None,
    modalita_sporadica: bool = False,
    tipo_default: str = "SPESA",
):
    if not supabase:
        snack = ft.SnackBar(ft.Text("Errore: Database Supabase non connesso!"))
        page.overlay.append(snack)
        snack.open = True
        page.update()
        return

    res_cat = supabase.table("categorie_budget").select("*").order("nome").execute()
    lista_categorie = res_cat.data if res_cat.data else []

    res_fam = supabase.table("famiglia").select("*").order("nome").execute()
    lista_famiglia = res_fam.data if res_fam.data else []

    def get_colori_liberi_cat(id_cat_esclusa=None):
        occupati = [c["colore"] for c in lista_categorie if c.get("colore") and str(c["id"]) != str(id_cat_esclusa)]
        liberi = [p for p in PALETTE_COLORI if p["hex"] not in occupati]
        return liberi if liberi else PALETTE_COLORI

    data_istanza = data_selezionata
    if movimento_esistente and movimento_esistente.get("data_movimento"):
        try:
            data_istanza = datetime.strptime(str(movimento_esistente["data_movimento"])[:10], "%Y-%m-%d")
        except Exception:
            pass

    data_istanza_str = data_istanza.strftime("%Y-%m-%d")
    data_vis_init = data_istanza.strftime("%d/%m/%Y")

    tipo_init = (movimento_esistente.get("tipo") if movimento_esistente else tipo_default) or "SPESA"
    stato_init = (
        normalizza_stato_budget(movimento_esistente.get("stato"))
        if movimento_esistente
        else ("EFFETTIVO" if modalita_sporadica else "PREVISTO")
    )
    stato_form = {"tipo": tipo_init.upper(), "stato": stato_init.upper()}

    titolo_init = (movimento_esistente.get("titolo") or "") if movimento_esistente else ""
    fornitore_init = (movimento_esistente.get("fornitore_luogo") or "") if movimento_esistente else ""
    importo_init = (
        f"{float(movimento_esistente.get('importo') or 0):.2f}".replace(".", ",")
        if movimento_esistente
        else ""
    )
    cat_init = movimento_esistente.get("id_categoria") if movimento_esistente else None
    note_init = (movimento_esistente.get("note") or "") if movimento_esistente else ""
    assegnatari_init = movimento_esistente.get("assegnatari_ids", []) if movimento_esistente else []

    is_p_db = False
    if movimento_esistente and movimento_esistente.get("is_periodico"):
        is_p_db = str(movimento_esistente.get("is_periodico")).lower() in ["true", "1", "t", "y", "yes"]

    data_fine_db = movimento_esistente.get("data_fine_periodo") if movimento_esistente else None
    date_escluse_db = (movimento_esistente.get("date_escluse") or "") if movimento_esistente else ""
    tipo_per_init = (movimento_esistente.get("tipo_periodicita") or "MENSILE") if movimento_esistente else "MENSILE"

    switch_per_init = False
    if is_p_db:
        if data_fine_db is None or str(data_fine_db) >= data_istanza_str:
            switch_per_init = True

    val_ev_db = movimento_esistente.get("is_evidenziato") if movimento_esistente else None
    evidenziato_init = True if val_ev_db is None else (str(val_ev_db).lower() in ["true", "1", "t", "y", "yes"])

    btn_tipo_spesa = ft.Container(
        content=ft.Text("💸 SPESA", size=12, weight=ft.FontWeight.BOLD),
        padding=8, border_radius=6, expand=1, alignment=ft.Alignment(0, 0),
    )
    btn_tipo_entrata = ft.Container(
        content=ft.Text("💰 ENTRATA", size=12, weight=ft.FontWeight.BOLD),
        padding=8, border_radius=6, expand=1, alignment=ft.Alignment(0, 0),
    )
    btn_stato_previsto = ft.Container(
        content=ft.Text("⏳ PREVISTA", size=12, weight=ft.FontWeight.BOLD),
        padding=8, border_radius=6, expand=1, alignment=ft.Alignment(0, 0),
    )
    btn_stato_effettivo = ft.Container(
        content=ft.Text("✅ EFFETTIVA", size=12, weight=ft.FontWeight.BOLD),
        padding=8, border_radius=6, expand=1, alignment=ft.Alignment(0, 0),
    )

    dd_categoria = ft.Dropdown(
        label="Categoria (*)",
        hint_text="Scegli categoria...",
        value=cat_init,
        options=[ft.dropdown.Option(key=c["id"], text=c["nome"]) for c in lista_categorie],
        expand=True,
        dense=True,
        text_size=13,
    )
    txt_fornitore = ft.TextField(
        label="Luogo / Fornitore (es. Decò, Plenitude, Datore...)",
        hint_text="Dove o da chi...",
        value=fornitore_init,
        text_size=13,
        dense=True,
    )
    txt_titolo = ft.TextField(
        label="Descrizione (opzionale se indichi Luogo/Fornitore)",
        hint_text="Es. Spesa alimentare, Mutuo, Stipendio...",
        value=titolo_init,
        text_size=13,
        dense=True,
    )
    txt_importo = ft.TextField(
        label="Importo € (*)",
        hint_text="Es. 35,00",
        value=importo_init,
        text_size=14,
        dense=True,
        expand=4,
        text_align=ft.TextAlign.RIGHT,
        autofocus=modalita_sporadica,
    )

    def formatta_data_input(e):
        val_pulito = "".join([c for c in (txt_data.value or "") if c.isdigit()])
        ris = ""
        for i, num in enumerate(val_pulito):
            if i in (2, 4):
                ris += "/"
            ris += num
        ris = ris[:10]
        if txt_data.value != ris:
            txt_data.value = ris
            txt_data.error_text = None
            page.update()

    txt_data = ft.TextField(
        label="Data (*)",
        hint_text="GG/MM/AAAA",
        value=data_vis_init,
        text_size=13,
        dense=True,
        on_change=formatta_data_input,
        text_align=ft.TextAlign.CENTER,
        expand=4,
    )

    riga_importo_data = ft.Row([txt_importo, txt_data], spacing=8)

    notif_attiva_init = (
        str(movimento_esistente.get("notifica_attiva", True)).lower() in ["true", "1", "t", "y", "yes"]
        if movimento_esistente
        else (stato_form["stato"] == "PREVISTO")
    )
    ora_notif_init = str(movimento_esistente.get("ora_notifica") or "09:00")[:5] if movimento_esistente else "09:00"
    preavviso_val_init = int(movimento_esistente.get("preavviso_valore") or 1) if movimento_esistente else 1
    preavviso_unita_init = (
        str(movimento_esistente.get("preavviso_unita") or "giorni").strip().lower()
        if movimento_esistente
        else "giorni"
    )
    if preavviso_unita_init not in ["minuti", "ore", "giorni", "settimane"]:
        preavviso_unita_init = "giorni"

    val_rip_db = movimento_esistente.get("preavviso_ripeti") if movimento_esistente else None
    preavviso_ripeti_init = True if val_rip_db is None else (str(val_rip_db).lower() in ["true", "1", "t", "y", "yes"])

    switch_notifica = ft.Switch(
        label="🔔 Avvisami alla scadenza",
        value=notif_attiva_init,
    )

    def formatta_ora_notif(e):
        val_pulito = "".join([c for c in (txt_ora_notifica.value or "") if c.isdigit()])
        ris = ""
        for i, num in enumerate(val_pulito):
            if i == 2:
                ris += ":"
            ris += num
        ris = ris[:5]
        if txt_ora_notifica.value != ris:
            txt_ora_notifica.value = ris
            page.update()

    txt_ora_notifica = ft.TextField(
        label="Ora avviso",
        value=ora_notif_init,
        width=74,
        text_size=12,
        label_style=ft.TextStyle(size=10),
        dense=True,
        content_padding=ft.Padding(4, 8, 4, 8),
        text_align=ft.TextAlign.CENTER,
        on_change=formatta_ora_notif,
    )
    txt_preavviso_val = ft.TextField(
        label="Anticipo",
        value=str(preavviso_val_init),
        width=54,
        text_size=12,
        label_style=ft.TextStyle(size=10),
        dense=True,
        content_padding=ft.Padding(2, 8, 2, 8),
        text_align=ft.TextAlign.CENTER,
    )
    dd_preavviso_unita = ft.Dropdown(
        label="Unità",
        value=preavviso_unita_init,
        options=[
            ft.dropdown.Option(key="minuti", text="minuti"),
            ft.dropdown.Option(key="ore", text="ore"),
            ft.dropdown.Option(key="giorni", text="giorni"),
            ft.dropdown.Option(key="settimane", text="settimane"),
        ],
        dense=True,
        text_size=12,
        label_style=ft.TextStyle(size=10),
        content_padding=ft.Padding(8, 8, 4, 8),
        expand=True,
    )
    switch_preavviso_ripeti = ft.Switch(value=preavviso_ripeti_init, scale=0.75)
    riga_dettagli_notifica = ft.Row(
        [
            txt_ora_notifica,
            txt_preavviso_val,
            dd_preavviso_unita,
            ft.Row([
                ft.Container(content=switch_preavviso_ripeti, width=38, height=28, alignment=ft.Alignment(0, 0)),
                ft.Text("Ripeti", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900),
            ], spacing=2, tight=True),
        ],
        spacing=5,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        visible=notif_attiva_init,
    )

    def on_change_switch_notifica(e):
        riga_dettagli_notifica.visible = bool(switch_notifica.value)
        page.update()

    switch_notifica.on_change = on_change_switch_notifica

    box_notifiche_scadenza = ft.Container(
        content=ft.Column([switch_notifica, riga_dettagli_notifica], spacing=4, tight=True),
        bgcolor="#F5F7FA",
        padding=8,
        border_radius=8,
        border=ft.Border(
            top=ft.BorderSide(1, "#CFD8DC"),
            bottom=ft.BorderSide(1, "#CFD8DC"),
            left=ft.BorderSide(1, "#CFD8DC"),
            right=ft.BorderSide(1, "#CFD8DC"),
        ),
        visible=(stato_form["stato"] == "PREVISTO"),
    )

    def aggiorna_stile_selettori():
        is_spesa = stato_form["tipo"] == "SPESA"
        btn_tipo_spesa.bgcolor = "#C62828" if is_spesa else "#ECEFF1"
        btn_tipo_spesa.content.color = ft.Colors.WHITE if is_spesa else ft.Colors.GREY_800
        btn_tipo_entrata.bgcolor = "#2E7D32" if not is_spesa else "#ECEFF1"
        btn_tipo_entrata.content.color = ft.Colors.WHITE if not is_spesa else ft.Colors.GREY_800

        is_prev = stato_form["stato"] == "PREVISTO"
        btn_stato_previsto.bgcolor = "#EF6C00" if is_prev else "#ECEFF1"
        btn_stato_previsto.content.color = ft.Colors.WHITE if is_prev else ft.Colors.GREY_800
        btn_stato_effettivo.bgcolor = "#1565C0" if not is_prev else "#ECEFF1"
        btn_stato_effettivo.content.color = ft.Colors.WHITE if not is_prev else ft.Colors.GREY_800

        box_notifiche_scadenza.visible = is_prev
        txt_fornitore.label = (
            "Luogo / Fornitore (es. Decò, Plenitude...)"
            if is_spesa
            else "Fonte / Ente (es. Datore di lavoro, INPS, Cliente...)"
        )

    def imposta_tipo(nuovo_tipo):
        stato_form["tipo"] = nuovo_tipo
        aggiorna_stile_selettori()
        page.update()

    def imposta_stato(nuovo_stato):
        stato_form["stato"] = nuovo_stato
        if nuovo_stato == "PREVISTO" and not movimento_esistente:
            switch_notifica.value = True
            riga_dettagli_notifica.visible = True
        aggiorna_stile_selettori()
        page.update()

    btn_tipo_spesa.on_click = lambda e: imposta_tipo("SPESA")
    btn_tipo_entrata.on_click = lambda e: imposta_tipo("ENTRATA")
    btn_stato_previsto.on_click = lambda e: imposta_stato("PREVISTO")
    btn_stato_effettivo.on_click = lambda e: imposta_stato("EFFETTIVO")
    aggiorna_stile_selettori()

    selezionati_famiglia = set(assegnatari_init)

    def toggle_famiglia(e, f_id, colore_fam):
        if f_id in selezionati_famiglia:
            selezionati_famiglia.remove(f_id)
            e.control.bgcolor = ft.Colors.TRANSPARENT
            e.control.content.color = colore_fam
        else:
            selezionati_famiglia.add(f_id)
            e.control.bgcolor = colore_fam
            e.control.content.color = ft.Colors.WHITE
        e.control.update()

    bottoni_famiglia = []
    for f in lista_famiglia:
        colore_fam = f.get("colore", "#9E9E9E")
        is_sel = f["id"] in selezionati_famiglia
        bottoni_famiglia.append(
            ft.Container(
                content=ft.Text(f["nome"], size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE if is_sel else colore_fam),
                bgcolor=colore_fam if is_sel else ft.Colors.TRANSPARENT,
                border=ft.Border(
                    top=ft.BorderSide(2, colore_fam),
                    bottom=ft.BorderSide(2, colore_fam),
                    left=ft.BorderSide(2, colore_fam),
                    right=ft.BorderSide(2, colore_fam),
                ),
                padding=6,
                border_radius=15,
                width=120,
                alignment=ft.Alignment(0, 0),
                on_click=lambda e, fid=f["id"], cf=colore_fam: toggle_famiglia(e, fid, cf),
            )
        )

    mezzo = (len(bottoni_famiglia) + 1) // 2
    griglia_assegnatari = ft.Row(
        [
            ft.Column(bottoni_famiglia[:mezzo], spacing=5),
            ft.Column(bottoni_famiglia[mezzo:], spacing=5),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        vertical_alignment=ft.CrossAxisAlignment.START,
    )

    giorni_db_str = movimento_esistente.get("giorni_periodici") if movimento_esistente else None
    selezionati_giorni = set()
    if giorni_db_str:
        selezionati_giorni = {int(g) for g in giorni_db_str.split(",") if g.strip().isdigit()}
    else:
        selezionati_giorni.add(data_istanza.weekday())

    switch_periodico = ft.Switch(label="🔄 Movimento Periodico (es. Stipendio, Mutuo)", value=switch_per_init)
    dd_tipo_periodicita = ft.Dropdown(
        label="Frequenza di ripetizione",
        value=tipo_per_init if tipo_per_init in [x[0] for x in TIPI_PERIODICITA] else "MENSILE",
        options=[ft.dropdown.Option(key=k, text=lbl) for k, lbl in TIPI_PERIODICITA],
        dense=True,
        text_size=12,
        visible=switch_per_init,
    )

    etichette_giorni = ["L", "M", "M", "G", "V", "S", "D"]
    bottoni_giorni = []

    def toggle_giorno(e, idx_g):
        if not switch_periodico.value:
            return
        if idx_g in selezionati_giorni:
            if len(selezionati_giorni) > 1:
                selezionati_giorni.remove(idx_g)
                e.control.bgcolor = ft.Colors.TRANSPARENT
                e.control.content.color = ft.Colors.GREY_600
        else:
            selezionati_giorni.add(idx_g)
            e.control.bgcolor = ft.Colors.BLUE_900
            e.control.content.color = ft.Colors.WHITE
        e.control.update()

    for i, lettera in enumerate(etichette_giorni):
        is_g_sel = i in selezionati_giorni
        bottoni_giorni.append(
            ft.Container(
                content=ft.Text(lettera, size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE if is_g_sel else ft.Colors.GREY_600),
                bgcolor=ft.Colors.BLUE_900 if is_g_sel else ft.Colors.TRANSPARENT,
                border=ft.Border(
                    top=ft.BorderSide(2, ft.Colors.BLUE_900),
                    bottom=ft.BorderSide(2, ft.Colors.BLUE_900),
                    left=ft.BorderSide(2, ft.Colors.BLUE_900),
                    right=ft.BorderSide(2, ft.Colors.BLUE_900),
                ),
                shape=ft.BoxShape.CIRCLE,
                width=30,
                height=30,
                alignment=ft.Alignment(0, 0),
                on_click=lambda e, idx=i: toggle_giorno(e, idx),
            )
        )

    riga_giorni_sett = ft.Row(
        bottoni_giorni,
        spacing=5,
        alignment=ft.MainAxisAlignment.CENTER,
        visible=(switch_per_init and dd_tipo_periodicita.value == "SETTIMANALE"),
    )

    def aggiorna_visibilita_periodicita(e=None):
        dd_tipo_periodicita.visible = bool(switch_periodico.value)
        riga_giorni_sett.visible = bool(switch_periodico.value and dd_tipo_periodicita.value == "SETTIMANALE")
        page.update()

    switch_periodico.on_change = aggiorna_visibilita_periodicita
    dd_tipo_periodicita.on_change = aggiorna_visibilita_periodicita

    switch_evidenziato = ft.Switch(label="Evidenziato nel calendario", value=evidenziato_init)
    txt_note = ft.TextField(label="Note aggiuntive", value=note_init, multiline=True, min_lines=1, max_lines=3, text_size=12)
    txt_errore_form = ft.Text("", color=ft.Colors.RED_700, size=12, weight=ft.FontWeight.BOLD, visible=False)

    dlg_movimento = None
    dlg_categoria = None
    dlg_modifica_cat = None

    def apri_nuova_categoria(e):
        nonlocal dlg_categoria
        txt_nuova_cat = ft.TextField(label="Nome Categoria (es. VITTO, UTENZE)", dense=True)
        txt_err_cat = ft.Text("", color=ft.Colors.RED_700, size=12, weight=ft.FontWeight.BOLD, visible=False)
        colori_liberi = get_colori_liberi_cat(None)
        dd_colore = ft.Dropdown(
            label="Colore",
            dense=True,
            options=[ft.dropdown.Option(key=c["hex"], text=c["nome"]) for c in colori_liberi],
            value=colori_liberi[0]["hex"],
        )

        def salva_cat(ev):
            nome_cat = (txt_nuova_cat.value or "").strip().upper()
            if not nome_cat:
                txt_err_cat.value = "⚠️ Inserisci un nome per la categoria."
                txt_err_cat.visible = True
                page.update()
                return
            if not inizia_operazione(page):
                return
            try:
                res = supabase.table("categorie_budget").insert({"nome": nome_cat, "colore": dd_colore.value}).execute()
                nuovo_id = res.data[0]["id"]
                lista_categorie.append({"id": nuovo_id, "nome": nome_cat, "colore": dd_colore.value})
                dd_categoria.options.append(ft.dropdown.Option(key=nuovo_id, text=nome_cat))
                dd_categoria.value = nuovo_id
                dd_categoria.update()
                page.pubsub.send_all("aggiorna_budget")
                _chiudi_dlg(page, dlg_categoria)
            except Exception as ex:
                txt_err_cat.value = f"⚠️ Errore: {ex}"
                txt_err_cat.visible = True
                page.update()
            finally:
                termina_operazione(page)

        dlg_categoria = ft.AlertDialog(
            title=ft.Text("Nuova Categoria Budget", size=16, weight=ft.FontWeight.BOLD),
            content=ft.Column([txt_nuova_cat, dd_colore, txt_err_cat], tight=True),
            actions=[
                ft.TextButton("Annulla", on_click=lambda ev: _chiudi_dlg(page, dlg_categoria)),
                ft.Container(
                    content=ft.Text("Salva", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                    bgcolor=ft.Colors.BLUE_900,
                    padding=10,
                    border_radius=5,
                    on_click=salva_cat,
                ),
            ],
        )
        _apri_dlg(page, dlg_categoria)

    def apri_modifica_categoria(e):
        nonlocal dlg_modifica_cat
        if not dd_categoria.value:
            txt_errore_form.value = "⚠️ Seleziona prima una categoria da modificare."
            txt_errore_form.visible = True
            page.update()
            return
        cat_corr = next((c for c in lista_categorie if str(c["id"]) == str(dd_categoria.value)), None)
        if not cat_corr:
            return

        txt_nome_mod = ft.TextField(label="Nome Categoria", value=cat_corr.get("nome", ""), dense=True)
        colori_liberi = get_colori_liberi_cat(cat_corr["id"])
        dd_colore_mod = ft.Dropdown(
            label="Colore",
            dense=True,
            options=[ft.dropdown.Option(key=c["hex"], text=c["nome"]) for c in colori_liberi],
            value=cat_corr.get("colore") if cat_corr.get("colore") in [c["hex"] for c in colori_liberi] else colori_liberi[0]["hex"],
        )
        txt_err_mod = ft.Text("", color=ft.Colors.RED_700, size=12, weight=ft.FontWeight.BOLD, visible=False)

        def salva_mod_cat(ev):
            nuovo_nome = (txt_nome_mod.value or "").strip().upper()
            if not nuovo_nome:
                txt_err_mod.value = "⚠️ Il nome categoria è obbligatorio."
                txt_err_mod.visible = True
                page.update()
                return
            if not inizia_operazione(page):
                return
            try:
                supabase.table("categorie_budget").update({
                    "nome": nuovo_nome,
                    "colore": dd_colore_mod.value,
                }).eq("id", cat_corr["id"]).execute()
                cat_corr["nome"] = nuovo_nome
                cat_corr["colore"] = dd_colore_mod.value
                for opt in dd_categoria.options:
                    if str(opt.key) == str(cat_corr["id"]):
                        opt.text = nuovo_nome
                dd_categoria.update()
                page.pubsub.send_all("aggiorna_budget")
                if on_success_callback:
                    on_success_callback()
                _chiudi_dlg(page, dlg_modifica_cat)
            except Exception as ex:
                txt_err_mod.value = f"⚠️ Errore: {ex}"
                txt_err_mod.visible = True
                page.update()
            finally:
                termina_operazione(page)

        dlg_modifica_cat = ft.AlertDialog(
            title=ft.Text(f"Modifica {cat_corr['nome']}", size=16, weight=ft.FontWeight.BOLD),
            content=ft.Column([txt_nome_mod, dd_colore_mod, txt_err_mod], tight=True),
            actions=[
                ft.TextButton("Annulla", on_click=lambda ev: _chiudi_dlg(page, dlg_modifica_cat)),
                ft.Container(
                    content=ft.Text("Aggiorna", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                    bgcolor=ft.Colors.ORANGE_800,
                    padding=10,
                    border_radius=5,
                    on_click=salva_mod_cat,
                ),
            ],
        )
        _apri_dlg(page, dlg_modifica_cat)

    btn_edit_cat = ft.IconButton(icon=ft.Icons.EDIT, icon_color=ft.Colors.ORANGE_800, on_click=apri_modifica_categoria)
    btn_add_cat = ft.IconButton(icon=ft.Icons.ADD_CIRCLE, icon_color=ft.Colors.BLUE_900, on_click=apri_nuova_categoria)

    def esegui_db_con_loading(funzione):
        if not inizia_operazione(page):
            return

        def worker():
            try:
                funzione()
            finally:
                termina_operazione(page)

        threading.Thread(target=worker, daemon=True).start()

    def _scrivi_movimento_db(dati_mov: dict, data_db: str, ambito: str):
        if is_p_db and ambito == "Solo questa occorrenza":
            dati_mov["is_periodico"] = False
            dati_mov["tipo_periodicita"] = None
            dati_mov["giorni_periodici"] = None
        else:
            if switch_periodico.value:
                dati_mov["is_periodico"] = True
                dati_mov["tipo_periodicita"] = dd_tipo_periodicita.value or "MENSILE"
                if dati_mov["tipo_periodicita"] == "SETTIMANALE":
                    dati_mov["giorni_periodici"] = ",".join(map(str, sorted(list(selezionati_giorni))))
                else:
                    dati_mov["giorni_periodici"] = None
            else:
                dati_mov["is_periodico"] = False
                dati_mov["tipo_periodicita"] = None
                dati_mov["giorni_periodici"] = None

        familiari_sel = list(selezionati_famiglia)
        try:
            if movimento_esistente:
                vero_inizio = str(movimento_esistente.get("data_originale_serie") or movimento_esistente["data_movimento"])[:10]
                try:
                    res_orig = supabase.table("movimenti_budget").select("data_movimento, date_escluse").eq("id", movimento_esistente["id"]).execute()
                    if res_orig.data:
                        vero_inizio = str(res_orig.data[0]["data_movimento"])[:10]
                        escl_db_fresche = res_orig.data[0].get("date_escluse") or ""
                    else:
                        escl_db_fresche = date_escluse_db
                except Exception:
                    escl_db_fresche = date_escluse_db

                if is_p_db and ambito == "Solo questa occorrenza":
                    lista_e = [x.strip() for x in escl_db_fresche.split(",") if x.strip()]
                    if data_istanza_str not in lista_e:
                        lista_e.append(data_istanza_str)
                    supabase.table("movimenti_budget").update({"date_escluse": ",".join(lista_e)}).eq("id", movimento_esistente["id"]).execute()

                    dati_mov["data_movimento"] = data_db
                    dati_mov["is_periodico"] = False
                    dati_mov["tipo_periodicita"] = None
                    dati_mov["giorni_periodici"] = None
                    dati_mov["date_escluse"] = None
                    dati_mov["data_fine_periodo"] = None
                    res_ins = supabase.table("movimenti_budget").insert(dati_mov).execute()
                    id_op = res_ins.data[0]["id"]

                elif is_p_db and ambito == "Da questa in poi":
                    ieri_str = (data_istanza - timedelta(days=1)).strftime("%Y-%m-%d")
                    if ieri_str < vero_inizio:
                        dati_mov["data_movimento"] = data_db
                        supabase.table("movimenti_budget").update(dati_mov).eq("id", movimento_esistente["id"]).execute()
                        supabase.table("movimenti_budget_familiari").delete().eq("id_movimento", movimento_esistente["id"]).execute()
                        id_op = movimento_esistente["id"]
                    else:
                        supabase.table("movimenti_budget").update({"data_fine_periodo": ieri_str}).eq("id", movimento_esistente["id"]).execute()
                        dati_mov["data_movimento"] = data_db
                        dati_mov["data_fine_periodo"] = None
                        dati_mov["date_escluse"] = None
                        res_ins = supabase.table("movimenti_budget").insert(dati_mov).execute()
                        id_op = res_ins.data[0]["id"]
                else:
                    dati_mov["data_movimento"] = vero_inizio if (is_p_db and data_db == data_istanza_str) else data_db
                    supabase.table("movimenti_budget").update(dati_mov).eq("id", movimento_esistente["id"]).execute()
                    supabase.table("movimenti_budget_familiari").delete().eq("id_movimento", movimento_esistente["id"]).execute()
                    id_op = movimento_esistente["id"]
            else:
                dati_mov["data_movimento"] = data_db
                dati_mov["date_escluse"] = None
                dati_mov["data_fine_periodo"] = None
                res_ins = supabase.table("movimenti_budget").insert(dati_mov).execute()
                id_op = res_ins.data[0]["id"]

            for id_fam in familiari_sel:
                supabase.table("movimenti_budget_familiari").insert({
                    "id_movimento": id_op,
                    "id_familiare": id_fam,
                }).execute()

            _chiudi_dlg(page, dlg_movimento)
            page.pubsub.send_all("aggiorna_budget")
            page.pubsub.send_all("aggiorna_dati")
            if on_success_callback:
                on_success_callback()
        except Exception as ex:
            txt_errore_form.value = f"⚠️ Errore salvataggio DB: {ex}"
            txt_errore_form.visible = True
            page.update()

    def avvia_salvataggio(e):
        txt_errore_form.visible = False
        dd_categoria.error_text = None
        txt_importo.error_text = None
        txt_data.error_text = None

        if not dd_categoria.value:
            dd_categoria.error_text = "Obbligatoria"
            txt_errore_form.value = "⚠️ Seleziona una Categoria!"
            txt_errore_form.visible = True
            page.update()
            return

        imp_val = parse_importo(txt_importo.value)
        if imp_val <= 0:
            txt_importo.error_text = "Obbligatorio"
            txt_errore_form.value = "⚠️ Inserisci un importo valido maggiore di 0 €!"
            txt_errore_form.visible = True
            page.update()
            return

        try:
            dt_obj = datetime.strptime((txt_data.value or "").strip(), "%d/%m/%Y")
            data_db = dt_obj.strftime("%Y-%m-%d")
        except Exception:
            txt_data.error_text = "Errore"
            txt_errore_form.value = "⚠️ Formato data non valido! Usa GG/MM/AAAA."
            txt_errore_form.visible = True
            page.update()
            return

        forn_pulito = formatta_testo_intelligente(txt_fornitore.value) if txt_fornitore.value else None
        tit_pulito = formatta_testo_intelligente(txt_titolo.value) if txt_titolo.value else ""
        if not tit_pulito:
            cat_sel = next((c["nome"] for c in lista_categorie if str(c["id"]) == str(dd_categoria.value)), "Movimento")
            tit_pulito = f"{cat_sel} - {forn_pulito}" if forn_pulito else cat_sel

        ora_n_clean = (txt_ora_notifica.value or "09:00").strip()
        try:
            datetime.strptime(ora_n_clean, "%H:%M")
        except Exception:
            ora_n_clean = "09:00"

        try:
            val_preavviso_int = max(0, int("".join(c for c in (txt_preavviso_val.value or "") if c.isdigit()) or "1"))
        except Exception:
            val_preavviso_int = 1

        unita_preavviso_str = (
            dd_preavviso_unita.value
            if dd_preavviso_unita.value in ["minuti", "ore", "giorni", "settimane"]
            else "giorni"
        )

        dati_base = {
            "tipo": stato_form["tipo"],
            "stato": stato_form["stato"],
            "titolo": tit_pulito,
            "fornitore_luogo": forn_pulito,
            "importo": imp_val,
            "id_categoria": dd_categoria.value,
            "note": formatta_testo_intelligente(txt_note.value) if txt_note.value else None,
            "is_evidenziato": bool(switch_evidenziato.value),
            "notifica_attiva": bool(switch_notifica.value) if stato_form["stato"] == "PREVISTO" else False,
            "ora_notifica": ora_n_clean,
            "preavviso_valore": val_preavviso_int,
            "preavviso_unita": unita_preavviso_str,
            "preavviso_ripeti": bool(switch_preavviso_ripeti.value),
        }

        if movimento_esistente and is_p_db:
            dd_scelta = ft.Dropdown(
                label="Ambito di modifica",
                options=[
                    ft.dropdown.Option("Solo questa occorrenza"),
                    ft.dropdown.Option("Da questa in poi"),
                    ft.dropdown.Option("Tutta la serie"),
                ],
                value="Solo questa occorrenza",
                dense=True,
            )
            dlg_scelta = None

            def conferma_scelta(ev):
                _chiudi_dlg(page, dlg_scelta)
                esegui_db_con_loading(lambda: _scrivi_movimento_db(dati_base, data_db, dd_scelta.value))

            dlg_scelta = ft.AlertDialog(
                title=ft.Text("Modifica Movimento Periodico", size=16, weight=ft.FontWeight.BOLD),
                content=ft.Column([ft.Text("Come vuoi applicare le modifiche?"), dd_scelta], tight=True),
                actions=[
                    ft.TextButton("Annulla", on_click=lambda ev: _chiudi_dlg(page, dlg_scelta)),
                    ft.Container(
                        content=ft.Text("Conferma", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                        bgcolor=ft.Colors.BLUE_900,
                        padding=10,
                        border_radius=5,
                        on_click=conferma_scelta,
                    ),
                ],
            )
            _apri_dlg(page, dlg_scelta)
        else:
            esegui_db_con_loading(lambda: _scrivi_movimento_db(dati_base, data_db, "Tutta la serie"))

    def apri_dialog_eliminazione(e):
        dlg_del = None
        if is_p_db:
            dd_del = ft.Dropdown(
                label="Ambito di eliminazione",
                options=[
                    ft.dropdown.Option("Solo questa occorrenza"),
                    ft.dropdown.Option("Da questa in poi"),
                    ft.dropdown.Option("Tutta la serie"),
                ],
                value="Solo questa occorrenza",
                dense=True,
            )

            def esegui_del_per(ev):
                scelta = dd_del.value
                _chiudi_dlg(page, dlg_del)

                def op_del():
                    if scelta == "Solo questa occorrenza":
                        nuove_escl = f"{date_escluse_db},{data_istanza_str}" if date_escluse_db else data_istanza_str
                        supabase.table("movimenti_budget").update({"date_escluse": nuove_escl}).eq("id", movimento_esistente["id"]).execute()
                    elif scelta == "Da questa in poi":
                        ieri_str = (data_istanza - timedelta(days=1)).strftime("%Y-%m-%d")
                        vero_inizio = str(movimento_esistente.get("data_originale_serie") or movimento_esistente["data_movimento"])[:10]
                        if ieri_str < vero_inizio:
                            supabase.table("movimenti_budget").delete().eq("id", movimento_esistente["id"]).execute()
                        else:
                            supabase.table("movimenti_budget").update({"data_fine_periodo": ieri_str}).eq("id", movimento_esistente["id"]).execute()
                    else:
                        supabase.table("movimenti_budget").delete().eq("id", movimento_esistente["id"]).execute()
                    _chiudi_dlg(page, dlg_movimento)
                    page.pubsub.send_all("aggiorna_budget")
                    page.pubsub.send_all("aggiorna_dati")
                    if on_success_callback:
                        on_success_callback()

                esegui_db_con_loading(op_del)

            dlg_del = ft.AlertDialog(
                title=ft.Text("Elimina Movimento Periodico", size=16, weight=ft.FontWeight.BOLD),
                content=ft.Column([ft.Text("Scegli la modalità di eliminazione:"), dd_del], tight=True),
                actions=[
                    ft.TextButton("Annulla", on_click=lambda ev: _chiudi_dlg(page, dlg_del)),
                    ft.Container(
                        content=ft.Text("Conferma Elimina", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                        bgcolor=ft.Colors.RED_700,
                        padding=10,
                        border_radius=5,
                        on_click=esegui_del_per,
                    ),
                ],
            )
        else:
            def esegui_del_sing(ev):
                _chiudi_dlg(page, dlg_del)

                def op_del():
                    supabase.table("movimenti_budget").delete().eq("id", movimento_esistente["id"]).execute()
                    _chiudi_dlg(page, dlg_movimento)
                    page.pubsub.send_all("aggiorna_budget")
                    page.pubsub.send_all("aggiorna_dati")
                    if on_success_callback:
                        on_success_callback()

                esegui_db_con_loading(op_del)

            dlg_del = ft.AlertDialog(
                title=ft.Text("Elimina Movimento", size=16, weight=ft.FontWeight.BOLD),
                content=ft.Text("Sei sicuro di voler eliminare questa voce?"),
                actions=[
                    ft.TextButton("Annulla", on_click=lambda ev: _chiudi_dlg(page, dlg_del)),
                    ft.Container(
                        content=ft.Text("Elimina", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                        bgcolor=ft.Colors.RED_700,
                        padding=10,
                        border_radius=5,
                        on_click=esegui_del_sing,
                    ),
                ],
            )
        _apri_dlg(page, dlg_del)

    titolo_modal = (
        "⚡ Nuova Spesa Sporadica"
        if (modalita_sporadica and not movimento_esistente)
        else ("Modifica Spesa / Entrata" if movimento_esistente else "Nuova Spesa / Entrata")
    )

    btn_annulla = ft.TextButton("Annulla", on_click=lambda e: _chiudi_dlg(page, dlg_movimento))
    btn_salva = ft.Container(
        content=ft.Text("Aggiorna" if movimento_esistente else "Salva", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
        bgcolor=ft.Colors.BLUE_900,
        padding=10,
        border_radius=5,
        on_click=avvia_salvataggio,
    )

    controlli_extra_modifica = []
    if movimento_esistente and normalizza_stato_budget(movimento_esistente.get("stato")) == "PREVISTO":
        controlli_extra_modifica.append(
            ft.Container(
                content=ft.Row([
                    ft.Icon(ft.Icons.CHECK_CIRCLE, color=ft.Colors.WHITE, size=18),
                    ft.Text("TRASFORMA IN EFFETTIVA (CONFERMA DATA E IMPORTO)", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=11),
                ], alignment=ft.MainAxisAlignment.CENTER, spacing=6),
                bgcolor=ft.Colors.GREEN_700,
                padding=10,
                border_radius=6,
                on_click=lambda e: (
                    _chiudi_dlg(page, dlg_movimento),
                    apri_popup_trasforma_in_effettivo(page, movimento_esistente, on_success_callback),
                ),
            )
        )

    if movimento_esistente:
        btn_elimina = ft.Container(
            content=ft.Text("🗑️ Elimina", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
            bgcolor=ft.Colors.RED_700,
            padding=10,
            border_radius=5,
            on_click=apri_dialog_eliminazione,
        )
        riga_azioni = ft.Row([btn_elimina, ft.Row([btn_annulla, btn_salva], spacing=5)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
    else:
        riga_azioni = ft.Row([btn_annulla, btn_salva], alignment=ft.MainAxisAlignment.END)

    colonna_contenuto = [
        *controlli_extra_modifica,
        ft.Row([btn_tipo_spesa, btn_tipo_entrata], spacing=6),
        ft.Row([btn_stato_previsto, btn_stato_effettivo], spacing=6),
        ft.Row([dd_categoria, ft.Row([btn_edit_cat, btn_add_cat], spacing=0)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        txt_fornitore,
        riga_importo_data,
        txt_titolo,
        box_notifiche_scadenza,
        ft.Divider(height=1, color="#E0E0E0"),
        ft.Text("Assegna a (vuoto = Tutta la famiglia):", weight=ft.FontWeight.BOLD, size=12),
        griglia_assegnatari,
        ft.Divider(height=1, color="#E0E0E0"),
        switch_periodico,
        dd_tipo_periodicita,
        riga_giorni_sett,
        switch_evidenziato,
        txt_note,
        txt_errore_form,
    ]

    dlg_movimento = ft.AlertDialog(
        title=ft.Text(titolo_modal, size=16, weight=ft.FontWeight.BOLD),
        content=ft.Container(
            content=ft.Column(colonna_contenuto, tight=True, spacing=7, scroll=ft.ScrollMode.AUTO),
            width=355,
        ),
        actions=[riga_azioni],
        shape=ft.RoundedRectangleBorder(radius=10),
        content_padding=15,
    )
    _apri_dlg(page, dlg_movimento)


# ==========================================================
# CALENDARIO E CRUSCOTTO BUDGET (MESE, SETTIMANA, GIORNO, RIEPILOGO)
# ==========================================================
def crea_calendario_budget(page: ft.Page, stato_condiviso: dict = None, on_switch_impegni=None):
    stato = {
        "data": (stato_condiviso.get("data") if stato_condiviso else None) or datetime.now(),
        "vista": (stato_condiviso.get("vista") if stato_condiviso else None) or "Giorno",
        "filtro_categoria": [],
        "filtro_familiare": [],
        "filtro_stato": "TUTTI",
    }
    oggi = datetime.now()

    lista_categorie = []
    lista_famiglia = []
    mappa_famiglia = {}
    mappa_categorie = {}

    def sincronizza_stato_esterno():
        if stato_condiviso is not None:
            stato_condiviso["data"] = stato["data"]
            if stato["vista"] in ["Giorno", "Settimana", "Mese"]:
                stato_condiviso["vista"] = stato["vista"]

    def ricarica_dati_base():
        if not supabase:
            return
        for tentativo in range(3):
            try:
                res_cat = supabase.table("categorie_budget").select("*").order("nome").execute()
                lista_categorie.clear()
                lista_categorie.extend(res_cat.data if res_cat.data else [])

                res_fam = supabase.table("famiglia").select("*").order("nome").execute()
                lista_famiglia.clear()
                lista_famiglia.extend(res_fam.data if res_fam.data else [])
                break
            except Exception as ex:
                if tentativo == 2:
                    print("Errore caricamento anagrafiche budget:", ex)

    ricarica_dati_base()

    testo_header = ft.Text(size=16, weight=ft.FontWeight.BOLD)
    btn_giorno = ft.Container(content=ft.Text("Giorno", size=12, weight=ft.FontWeight.BOLD), border_radius=5, padding=6, on_click=lambda e: cambia_vista("Giorno"))
    btn_settimana = ft.Container(content=ft.Text("Settimana", size=12, weight=ft.FontWeight.BOLD), border_radius=5, padding=6, on_click=lambda e: cambia_vista("Settimana"))
    btn_mese = ft.Container(content=ft.Text("Mese", size=12, weight=ft.FontWeight.BOLD), border_radius=5, padding=6, on_click=lambda e: cambia_vista("Mese"))
    btn_riepilogo = ft.Container(content=ft.Text("📊 Budget", size=12, weight=ft.FontWeight.BOLD), border_radius=5, padding=6, on_click=lambda e: cambia_vista("Budget"))

    def bordo_standard():
        return ft.Border(
            top=ft.BorderSide(0.5, "#E0E0E0"),
            bottom=ft.BorderSide(0.5, "#E0E0E0"),
            left=ft.BorderSide(0.5, "#E0E0E0"),
            right=ft.BorderSide(0.5, "#E0E0E0"),
        )

    def bordo_personalizzato(colore, spessore=2):
        return ft.Border(
            top=ft.BorderSide(spessore, colore),
            bottom=ft.BorderSide(spessore, colore),
            left=ft.BorderSide(spessore, colore),
            right=ft.BorderSide(spessore, colore),
        )

    box_cruscotto_mese = ft.Container()
    layer_mese = ft.Column(spacing=0, visible=False)
    layer_settimana = ft.Column(spacing=0, visible=False)
    layer_giorno = ft.Column(spacing=0, visible=True)
    layer_budget = ft.Column(spacing=8, visible=False)
    area_dinamica = ft.Column([layer_mese, layer_settimana, layer_giorno, layer_budget], expand=True, spacing=0)

    row_filtri_stato = ft.Row(wrap=True, spacing=5)
    row_filtri_cat = ft.Row(wrap=True, spacing=5)
    row_filtri_fam = ft.Row(wrap=True, spacing=5)

    def cambia_vista(nuova_vista):
        stato["vista"] = nuova_vista
        sincronizza_stato_esterno()
        aggiorna_tutto()

    def vai_a_giorno(e):
        valore = e.control.data
        if isinstance(valore, int):
            stato["data"] = stato["data"].replace(day=valore)
        else:
            stato["data"] = valore
        cambia_vista("Giorno")

    def nav_prec(e):
        if stato["vista"] in ["Mese", "Budget"]:
            primo = stato["data"].replace(day=1)
            scorso = primo - timedelta(days=1)
            stato["data"] = stato["data"].replace(year=scorso.year, month=scorso.month, day=1)
        elif stato["vista"] == "Settimana":
            stato["data"] -= timedelta(days=7)
        elif stato["vista"] == "Giorno":
            stato["data"] -= timedelta(days=1)
        sincronizza_stato_esterno()
        aggiorna_tutto()

    def nav_succ(e):
        if stato["vista"] in ["Mese", "Budget"]:
            m, a = stato["data"].month, stato["data"].year
            stato["data"] = stato["data"].replace(year=a + (1 if m == 12 else 0), month=(1 if m == 12 else m + 1), day=1)
        elif stato["vista"] == "Settimana":
            stato["data"] += timedelta(days=7)
        elif stato["vista"] == "Giorno":
            stato["data"] += timedelta(days=1)
        sincronizza_stato_esterno()
        aggiorna_tutto()

    def imposta_filtro_cat(id_cat):
        if id_cat is None:
            stato["filtro_categoria"].clear()
        elif id_cat in stato["filtro_categoria"]:
            stato["filtro_categoria"].remove(id_cat)
        else:
            stato["filtro_categoria"].append(id_cat)
        aggiorna_tutto()

    def imposta_filtro_fam(id_fam):
        if id_fam is None:
            stato["filtro_familiare"].clear()
        elif id_fam in stato["filtro_familiare"]:
            stato["filtro_familiare"].remove(id_fam)
        else:
            stato["filtro_familiare"].append(id_fam)
        aggiorna_tutto()

    def imposta_filtro_stato(nuovo_s):
        stato["filtro_stato"] = nuovo_s
        aggiorna_tutto()

    def crea_card_movimento(mov: dict, vista_card="Giorno"):
        tipo = str(mov.get("tipo") or "SPESA").upper()
        stato_m = normalizza_stato_budget(mov.get("stato"))
        is_spesa = (tipo == "SPESA")
        is_previsto = (stato_m == "PREVISTO")

        titolo = mov.get("titolo") or "Movimento"
        fornitore = mov.get("fornitore_luogo") or ""
        importo = float(mov.get("importo") or 0.0)
        note = mov.get("note") or ""

        cat_dati = mappa_categorie.get(mov.get("id_categoria")) or {}
        cat_nome = cat_dati.get("nome", "SENZA CATEGORIA")
        cat_colore = cat_dati.get("colore", "#607D8B")

        val_p = mov.get("is_periodico")
        is_p = str(val_p).lower() in ["true", "1", "t", "y", "yes"]
        per_label = f" 🔄 {mov.get('tipo_periodicita', 'MENSILE')[:4]}" if is_p else ""

        assegnatari_ids = mov.get("assegnatari_ids") or []
        nomi_asg = [mappa_famiglia[fid]["nome"] for fid in assegnatari_ids if fid in mappa_famiglia]
        testo_asg = ", ".join(nomi_asg) if nomi_asg else "Tutta la famiglia"

        segno = "-" if is_spesa else "+"
        colore_imp = "#C62828" if is_spesa else "#2E7D32"
        badge_stato_txt = "⏳ PREVISTA" if is_previsto else "✅ EFFETTIVA"
        badge_stato_bg = "#FFF3E0" if is_previsto else "#E8F5E9"
        badge_stato_col = "#E65100" if is_previsto else "#1B5E20"

        dt_mov = datetime.strptime(str(mov["data_movimento"])[:10], "%Y-%m-%d")

        if vista_card == "Mese":
            sfondo_m = "#FFEBEE" if (is_spesa and not is_previsto) else ("#FFF8E1" if is_previsto else "#E8F5E9")
            return ft.Container(
                content=ft.Row([
                    ft.Text(f"{'⏳' if is_previsto else ''}{segno}{int(round(importo))}€", size=7, weight=ft.FontWeight.BOLD, color=colore_imp, no_wrap=True),
                    ft.Text(fornitore or titolo, size=7, color=ft.Colors.BLACK, no_wrap=True, expand=True),
                ], spacing=2),
                bgcolor=sfondo_m,
                padding=2,
                border_radius=3,
                border=ft.Border(left=ft.BorderSide(3, cat_colore)),
                height=19,
            )

        if vista_card == "Settimana":
            return ft.Container(
                content=ft.Column([
                    ft.Row([
                        ft.Text(cat_nome[:10] + per_label, size=8, weight=ft.FontWeight.BOLD, color=cat_colore, expand=True),
                        ft.Text("⏳" if is_previsto else "✅", size=9),
                    ], spacing=2),
                    ft.Text(f"{segno} {formatta_euro(importo)}", size=10, weight=ft.FontWeight.BOLD, color=colore_imp),
                    ft.Text(fornitore if fornitore else titolo, size=9, weight=ft.FontWeight.BOLD, color=ft.Colors.BLACK, no_wrap=True),
                    ft.Text(testo_asg, size=8, color=ft.Colors.GREY_700, no_wrap=True),
                ], spacing=1),
                bgcolor="#FFFDF7" if is_previsto else ft.Colors.WHITE,
                padding=4,
                border_radius=5,
                border=ft.Border(
                    left=ft.BorderSide(4, cat_colore),
                    top=ft.BorderSide(1, "#FFE082" if is_previsto else "#E0E0E0"),
                    bottom=ft.BorderSide(1, "#FFE082" if is_previsto else "#E0E0E0"),
                    right=ft.BorderSide(1, "#FFE082" if is_previsto else "#E0E0E0"),
                ),
                on_click=lambda e, m_curr=mov, d_c=dt_mov: apri_form_movimento_budget(page, d_c, ricarica_e_aggiorna, m_curr),
            )

        sottotitolo_luogo = f"📍 Luogo/Fornitore: {fornitore}" if fornitore else ""
        azioni_extra = []
        if is_previsto:
            azioni_extra.append(
                ft.Container(
                    content=ft.Row([
                        ft.Icon(ft.Icons.CHECK_CIRCLE_OUTLINE, color=ft.Colors.WHITE, size=14),
                        ft.Text("Converti in Effettiva", color=ft.Colors.WHITE, size=10, weight=ft.FontWeight.BOLD),
                    ], spacing=4, tight=True),
                    bgcolor=ft.Colors.GREEN_700,
                    padding=ft.Padding(8, 4, 8, 4),
                    border_radius=6,
                    on_click=lambda e, m_curr=mov: apri_popup_trasforma_in_effettivo(page, m_curr, ricarica_e_aggiorna),
                )
            )

        return ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Container(
                        content=ft.Text(cat_nome + per_label, size=10, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                        bgcolor=cat_colore,
                        padding=ft.Padding(7, 2, 7, 2),
                        border_radius=6,
                    ),
                    ft.Container(
                        content=ft.Text(badge_stato_txt, size=10, weight=ft.FontWeight.BOLD, color=badge_stato_col),
                        bgcolor=badge_stato_bg,
                        padding=ft.Padding(7, 2, 7, 2),
                        border_radius=6,
                    ),
                    ft.Container(expand=True),
                    ft.Text(f"{segno} {formatta_euro(importo)}", size=15, weight=ft.FontWeight.BOLD, color=colore_imp),
                ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Row([
                    ft.Column([
                        ft.Text(titolo, size=13, weight=ft.FontWeight.BOLD, color=ft.Colors.BLACK),
                        *([ft.Text(sottotitolo_luogo, size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_GREY_800)] if sottotitolo_luogo else []),
                        ft.Text(f"👨‍👩‍👧‍👦 {testo_asg}", size=10, color=ft.Colors.BLUE_900, weight=ft.FontWeight.BOLD),
                        *([ft.Text(f"📝 {note}", size=10, color=ft.Colors.GREY_700, italic=True)] if note else []),
                    ], spacing=2, expand=True),
                    *azioni_extra,
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ], spacing=5),
            bgcolor="#FFFDE7" if is_previsto else ft.Colors.WHITE,
            padding=8,
            border_radius=8,
            border=ft.Border(
                left=ft.BorderSide(6, cat_colore),
                top=ft.BorderSide(1, "#FFD54F" if is_previsto else "#CFD8DC"),
                bottom=ft.BorderSide(1, "#FFD54F" if is_previsto else "#CFD8DC"),
                right=ft.BorderSide(1, "#FFD54F" if is_previsto else "#CFD8DC"),
            ),
            ink=True,
            on_click=lambda e, m_curr=mov, d_c=dt_mov: apri_form_movimento_budget(page, d_c, ricarica_e_aggiorna, m_curr),
        )

    def aggiorna_tutto():
        nonlocal mappa_famiglia, mappa_categorie
        if stato_condiviso and stato_condiviso.get("data"):
            stato["data"] = stato_condiviso["data"]

        mappa_famiglia = {f["id"]: f for f in lista_famiglia}
        mappa_categorie = {c["id"]: c for c in lista_categorie}
        vista = stato["vista"]
        data = stato["data"]

        for btn, nome_v in [(btn_giorno, "Giorno"), (btn_settimana, "Settimana"), (btn_mese, "Mese"), (btn_riepilogo, "Budget")]:
            btn.bgcolor = ft.Colors.BLUE_900 if vista == nome_v else ft.Colors.GREY_200
            btn.content.color = ft.Colors.WHITE if vista == nome_v else ft.Colors.BLACK

        layer_mese.visible = (vista == "Mese")
        layer_settimana.visible = (vista == "Settimana")
        layer_giorno.visible = (vista == "Giorno")
        layer_budget.visible = (vista == "Budget")

        row_filtri_stato.controls.clear()
        for cod_s, lbl_s in [("TUTTI", "Tutti"), ("PREVISTO", "⏳ Solo Previsti"), ("EFFETTIVO", "✅ Solo Effettivi")]:
            att = (stato["filtro_stato"] == cod_s)
            row_filtri_stato.controls.append(
                ft.Container(
                    content=ft.Text(lbl_s, size=10, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE if att else ft.Colors.BLUE_900),
                    bgcolor=ft.Colors.BLUE_900 if att else ft.Colors.TRANSPARENT,
                    border=bordo_personalizzato(ft.Colors.BLUE_900, 1.5),
                    padding=6,
                    border_radius=15,
                    on_click=lambda e, cs=cod_s: imposta_filtro_stato(cs),
                )
            )

        row_filtri_cat.controls.clear()
        is_tutte_cat = len(stato["filtro_categoria"]) == 0
        row_filtri_cat.controls.append(
            ft.Container(
                content=ft.Text("Tutte", size=10, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE if is_tutte_cat else ft.Colors.BLACK),
                bgcolor=ft.Colors.BLUE_900 if is_tutte_cat else ft.Colors.TRANSPARENT,
                border=bordo_personalizzato(ft.Colors.BLUE_900 if is_tutte_cat else ft.Colors.GREY_400),
                padding=6,
                border_radius=15,
                on_click=lambda e: imposta_filtro_cat(None),
            )
        )
        for c in lista_categorie:
            is_sel = c["id"] in stato["filtro_categoria"]
            row_filtri_cat.controls.append(
                ft.Container(
                    content=ft.Text(c["nome"], size=10, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE if is_sel else c["colore"]),
                    bgcolor=c["colore"] if is_sel else ft.Colors.TRANSPARENT,
                    border=bordo_personalizzato(c["colore"]),
                    padding=6,
                    border_radius=15,
                    on_click=lambda e, cid=c["id"]: imposta_filtro_cat(cid),
                )
            )

        row_filtri_fam.controls.clear()
        is_tutti_fam = len(stato["filtro_familiare"]) == 0
        row_filtri_fam.controls.append(
            ft.Container(
                content=ft.Text("Tutti", size=10, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE if is_tutti_fam else ft.Colors.BLACK),
                bgcolor=ft.Colors.BLUE_900 if is_tutti_fam else ft.Colors.TRANSPARENT,
                border=bordo_personalizzato(ft.Colors.BLUE_900 if is_tutti_fam else ft.Colors.GREY_400),
                padding=6,
                border_radius=15,
                on_click=lambda e: imposta_filtro_fam(None),
            )
        )
        for f in lista_famiglia:
            is_sel = f["id"] in stato["filtro_familiare"]
            colore_fam = f.get("colore", "#9E9E9E")
            row_filtri_fam.controls.append(
                ft.Container(
                    content=ft.Text(f["nome"], size=10, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE if is_sel else colore_fam),
                    bgcolor=colore_fam if is_sel else ft.Colors.TRANSPARENT,
                    border=bordo_personalizzato(colore_fam),
                    padding=6,
                    border_radius=15,
                    on_click=lambda e, fid=f["id"]: imposta_filtro_fam(fid),
                )
            )

        dizionario_movimenti = {}
        movimenti_mese_corrente = []
        primo_mese_date = data.replace(day=1).date()
        ultimo_gg_mese = calendar.monthrange(data.year, data.month)[1]
        ultimo_mese_date = data.replace(day=ultimo_gg_mese).date()

        inizio_finestra = primo_mese_date - timedelta(days=10)
        fine_finestra = ultimo_mese_date + timedelta(days=10)

        if supabase:
            try:
                primo_g = inizio_finestra.strftime("%Y-%m-%d")
                ultimo_g = fine_finestra.strftime("%Y-%m-%d")
                filtro_q = (
                    f"and(data_movimento.gte.{primo_g},data_movimento.lte.{ultimo_g}),"
                    f"and(is_periodico.eq.true,data_movimento.lte.{ultimo_g},"
                    f"or(data_fine_periodo.is.null,data_fine_periodo.gte.{primo_g}))"
                )
                res = (
                    supabase.table("movimenti_budget")
                    .select("*, movimenti_budget_familiari(id_familiare)")
                    .or_(filtro_q)
                    .execute()
                )
                espansi = espandi_movimenti_in_finestra(res.data or [], inizio_finestra, fine_finestra)

                for mov in espansi:
                    if stato["filtro_stato"] != "TUTTI" and normalizza_stato_budget(mov.get("stato")) != stato["filtro_stato"]:
                        continue
                    if stato["filtro_categoria"] and mov.get("id_categoria") not in stato["filtro_categoria"]:
                        continue
                    if stato["filtro_familiare"]:
                        asg = mov.get("assegnatari_ids") or []
                        if len(asg) > 0 and not any(fid in stato["filtro_familiare"] for fid in asg):
                            continue

                    d_s = mov["data_movimento"]
                    dizionario_movimenti.setdefault(d_s, []).append(mov)
                    if primo_mese_date.strftime("%Y-%m-%d") <= d_s <= ultimo_mese_date.strftime("%Y-%m-%d"):
                        movimenti_mese_corrente.append(mov)
            except Exception as ex:
                print("Errore lettura movimenti_budget:", ex)

        entrate_eff = sum(float(m["importo"] or 0) for m in movimenti_mese_corrente if m.get("tipo") == "ENTRATA" and normalizza_stato_budget(m.get("stato")) == "EFFETTIVO")
        entrate_prev = sum(float(m["importo"] or 0) for m in movimenti_mese_corrente if m.get("tipo") == "ENTRATA" and normalizza_stato_budget(m.get("stato")) == "PREVISTO")
        spese_eff = sum(float(m["importo"] or 0) for m in movimenti_mese_corrente if m.get("tipo") == "SPESA" and normalizza_stato_budget(m.get("stato")) == "EFFETTIVO")
        spese_prev = sum(float(m["importo"] or 0) for m in movimenti_mese_corrente if m.get("tipo") == "SPESA" and normalizza_stato_budget(m.get("stato")) == "PREVISTO")

        tot_entrate = entrate_eff + entrate_prev
        tot_spese = spese_eff + spese_prev
        saldo_effettivo = entrate_eff - spese_eff
        saldo_fine_mese = tot_entrate - tot_spese

        box_cruscotto_mese.content = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Text(f"📊 BILANCIO {MESI[data.month].upper()} {data.year}", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900, expand=True),
                    ft.Text(
                        f"Saldo Previsto Fine Mese: {formatta_euro(saldo_fine_mese)}",
                        size=11,
                        weight=ft.FontWeight.BOLD,
                        color=ft.Colors.GREEN_800 if saldo_fine_mese >= 0 else ft.Colors.RED_700,
                    ),
                ]),
                ft.Row([
                    ft.Container(
                        content=ft.Column([
                            ft.Text("💰 ENTRATE MESE", size=9, weight=ft.FontWeight.BOLD, color="#1B5E20"),
                            ft.Text(formatta_euro(tot_entrate), size=13, weight=ft.FontWeight.BOLD, color="#2E7D32"),
                            ft.Text(f"Eff: {formatta_euro(entrate_eff)} | Prev: {formatta_euro(entrate_prev)}", size=8.5, color=ft.Colors.GREY_800),
                        ], spacing=1),
                        bgcolor="#E8F5E9", padding=6, border_radius=6, expand=1,
                    ),
                    ft.Container(
                        content=ft.Column([
                            ft.Text("💸 SPESE MESE", size=9, weight=ft.FontWeight.BOLD, color="#B71C1C"),
                            ft.Text(formatta_euro(tot_spese), size=13, weight=ft.FontWeight.BOLD, color="#C62828"),
                            ft.Text(f"Eff: {formatta_euro(spese_eff)} | Prev: {formatta_euro(spese_prev)}", size=8.5, color=ft.Colors.GREY_800),
                        ], spacing=1),
                        bgcolor="#FFEBEE", padding=6, border_radius=6, expand=1,
                    ),
                    ft.Container(
                        content=ft.Column([
                            ft.Text("🏦 CASSA ATTUALE", size=9, weight=ft.FontWeight.BOLD, color="#0D47A1"),
                            ft.Text(
                                formatta_euro(saldo_effettivo),
                                size=13,
                                weight=ft.FontWeight.BOLD,
                                color="#1565C0" if saldo_effettivo >= 0 else "#C62828",
                            ),
                            ft.Text("Entrate Eff. - Spese Eff.", size=8.5, color=ft.Colors.GREY_800),
                        ], spacing=1),
                        bgcolor="#E3F2FD", padding=6, border_radius=6, expand=1,
                    ),
                ], spacing=6),
            ], spacing=4),
            bgcolor="#F8F9FA",
            padding=8,
            border_radius=8,
            border=bordo_standard(),
        )

        if vista == "Mese":
            testo_header.value = f"{MESI[data.month]} {data.year}"
            layer_mese.controls.clear()
            griglia = ft.Column(spacing=0)
            matrice = calendar.monthcalendar(data.year, data.month)

            for settimana in matrice:
                riga = ft.Row(alignment=ft.MainAxisAlignment.SPACE_BETWEEN, spacing=0)
                for giorno in settimana:
                    if giorno == 0:
                        riga.controls.append(ft.Container(expand=1, height=90, border=bordo_standard()))
                    else:
                        is_oggi = (giorno == oggi.day and data.month == oggi.month and data.year == oggi.year)
                        is_sel = (giorno == data.day)
                        d_str = data.replace(day=giorno).strftime("%Y-%m-%d")
                        mov_oggi = dizionario_movimenti.get(d_str, [])

                        cerchio = ft.Container(
                            content=ft.Text(str(giorno), color=ft.Colors.WHITE if is_oggi else ft.Colors.BLACK, size=12, weight=ft.FontWeight.BOLD if is_oggi else ft.FontWeight.NORMAL),
                            bgcolor=ft.Colors.BLUE_900 if is_oggi else (ft.Colors.BLUE_100 if is_sel else ft.Colors.TRANSPARENT),
                            border_radius=15, width=24, height=24, alignment=ft.Alignment(0, 0),
                        )

                        col_ind = ft.Column(spacing=1, expand=True)
                        for m_item in mov_oggi[:2]:
                            col_ind.controls.append(crea_card_movimento(m_item, vista_card="Mese"))
                        if len(mov_oggi) > 2:
                            col_ind.controls.append(
                                ft.Text(f"+{len(mov_oggi) - 2} altre", size=8, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_700)
                            )

                        ha_previsti = any(normalizza_stato_budget(x.get("stato")) == "PREVISTO" for x in mov_oggi)
                        riga.controls.append(
                            ft.Container(
                                content=ft.Column([
                                    ft.Container(content=cerchio, alignment=ft.Alignment(0, 0)),
                                    col_ind,
                                ], spacing=2, horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
                                expand=1,
                                height=90,
                                border=bordo_personalizzato("#FB8C00", 1.8) if ha_previsti else bordo_standard(),
                                padding=2,
                                data=giorno,
                                on_click=vai_a_giorno,
                            )
                        )
                griglia.controls.append(riga)

            layer_mese.controls.extend([
                ft.Row([
                    ft.Container(content=ft.Text(g, size=9, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_700), expand=1, alignment=ft.Alignment(0, 0), border=bordo_standard(), padding=2)
                    for g in GIORNI
                ], spacing=0),
                griglia,
            ])

        elif vista == "Settimana":
            lunedi = data - timedelta(days=data.weekday())
            domenica = lunedi + timedelta(days=6)
            testo_header.value = (
                f"{lunedi.day} - {domenica.day} {MESI[lunedi.month][:3]} {lunedi.year}"
                if lunedi.month == domenica.month
                else f"{lunedi.day} {MESI[lunedi.month][:3]} - {domenica.day} {MESI[domenica.month][:3]} {domenica.year}"
            )
            layer_settimana.controls.clear()
            riga1 = ft.Row(spacing=0)
            riga2 = ft.Row(spacing=0)

            for i in range(7):
                gc = lunedi + timedelta(days=i)
                d_str = gc.strftime("%Y-%m-%d")
                mov_oggi = dizionario_movimenti.get(d_str, [])

                cerchio_s = ft.Container(
                    content=ft.Text(str(gc.day), color=ft.Colors.WHITE if gc.date() == oggi.date() else ft.Colors.BLACK, size=14, weight=ft.FontWeight.BOLD),
                    bgcolor=ft.Colors.BLUE_900 if gc.date() == oggi.date() else ft.Colors.TRANSPARENT,
                    border_radius=15, width=24, height=24, alignment=ft.Alignment(0, 0),
                )
                col_ind = ft.Column([crea_card_movimento(m, vista_card="Settimana") for m in mov_oggi], spacing=3, scroll=ft.ScrollMode.AUTO)
                box_g = ft.Container(
                    content=ft.Column([
                        ft.Container(
                            content=ft.Column([ft.Text(GIORNI[i], size=9, color=ft.Colors.GREY_700), cerchio_s], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=0),
                            padding=4,
                        ),
                        ft.Divider(height=1, color="#E0E0E0"),
                        ft.Container(content=col_ind, padding=2, expand=True),
                    ]),
                    expand=1,
                    height=340,
                    border=bordo_standard(),
                    data=gc,
                    on_click=vai_a_giorno,
                )
                (riga1 if i < 4 else riga2).controls.append(box_g)

            riga2.controls.append(ft.Container(expand=1))
            layer_settimana.controls.extend([riga1, ft.Container(height=12), riga2])

        elif vista == "Giorno":
            testo_header.value = f"{data.day} {MESI[data.month]} {data.year}"
            is_oggi = (data.date() == oggi.date())
            layer_giorno.controls.clear()

            d_str = data.strftime("%Y-%m-%d")
            mov_oggi = dizionario_movimenti.get(d_str, [])
            tot_spese_g = sum(float(x["importo"] or 0) for x in mov_oggi if x.get("tipo") == "SPESA")
            tot_entrate_g = sum(float(x["importo"] or 0) for x in mov_oggi if x.get("tipo") == "ENTRATA")

            pulsante_switch_impegni = (
                ft.Container(
                    content=ft.Row([
                        ft.Icon(ft.Icons.SWAP_HORIZ, color=ft.Colors.BLUE_900, size=16),
                        ft.Text("Passa a 📅 Impegni del giorno", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900),
                    ], spacing=4, tight=True),
                    bgcolor="#E3F2FD",
                    padding=ft.Padding(10, 6, 10, 6),
                    border_radius=15,
                    border=bordo_personalizzato("#1976D2", 1),
                    on_click=lambda e: on_switch_impegni(data) if on_switch_impegni else None,
                )
                if on_switch_impegni
                else ft.Container()
            )

            intestazione = ft.Container(
                content=ft.Row([
                    ft.Column([
                        ft.Text(GIORNI[data.weekday()], size=12, color=ft.Colors.GREY_700),
                        ft.Text(str(data.day), size=24, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900 if is_oggi else ft.Colors.BLACK),
                    ], spacing=0),
                    ft.Column([
                        pulsante_switch_impegni,
                        ft.Text(
                            f"Uscite giorno: -{formatta_euro(tot_spese_g)}  |  Entrate: +{formatta_euro(tot_entrate_g)}",
                            size=11,
                            weight=ft.FontWeight.BOLD,
                            color=ft.Colors.GREY_800,
                        ),
                    ], horizontal_alignment=ft.CrossAxisAlignment.END, spacing=4),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                padding=10,
                border=bordo_standard(),
            )

            riga_bottoni_inserimento = ft.Row([
                ft.Container(
                    content=ft.Text("➕ Nuova Spesa / Entrata", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=12),
                    bgcolor=ft.Colors.BLUE_900,
                    padding=10,
                    border_radius=6,
                    expand=1,
                    alignment=ft.Alignment(0, 0),
                    on_click=lambda e: apri_form_movimento_budget(page, data, ricarica_e_aggiorna, modalita_sporadica=False),
                ),
                ft.Container(
                    content=ft.Text("⚡ Spesa Sporadica Rapida", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=12),
                    bgcolor="#2E7D32",
                    padding=10,
                    border_radius=6,
                    expand=1,
                    alignment=ft.Alignment(0, 0),
                    on_click=lambda e: apri_form_movimento_budget(page, data, ricarica_e_aggiorna, modalita_sporadica=True),
                ),
            ], spacing=6)

            col_cards = ft.Column(spacing=8)
            if mov_oggi:
                for m_item in mov_oggi:
                    col_cards.controls.append(crea_card_movimento(m_item, vista_card="Giorno"))
            else:
                col_cards.controls.append(
                    ft.Container(
                        content=ft.Text("Nessuna spesa o entrata registrata in questa giornata.", color=ft.Colors.GREY_500, italic=True),
                        padding=20,
                        alignment=ft.Alignment(0, 0),
                    )
                )

            layer_giorno.controls.extend([
                intestazione,
                ft.Container(height=6),
                riga_bottoni_inserimento,
                ft.Container(height=8),
                col_cards,
            ])

        elif vista == "Budget":
            testo_header.value = f"Budget {MESI[data.month]} {data.year}"
            layer_budget.controls.clear()

            per_cat = {}
            for m in movimenti_mese_corrente:
                cid = m.get("id_categoria")
                c_info = mappa_categorie.get(cid) or {"nome": "SENZA CATEGORIA", "colore": "#607D8B"}
                chiave = c_info["nome"]
                voce = per_cat.setdefault(chiave, {
                    "colore": c_info["colore"],
                    "spese_eff": 0.0,
                    "spese_prev": 0.0,
                    "entrate_eff": 0.0,
                    "entrate_prev": 0.0,
                })
                imp = float(m.get("importo") or 0.0)
                st_norm = normalizza_stato_budget(m.get("stato"))
                if m.get("tipo") == "SPESA":
                    if st_norm == "EFFETTIVO":
                        voce["spese_eff"] += imp
                    else:
                        voce["spese_prev"] += imp
                else:
                    if st_norm == "EFFETTIVO":
                        voce["entrate_eff"] += imp
                    else:
                        voce["entrate_prev"] += imp

            layer_budget.controls.append(
                ft.Text("📂 Dettaglio Mensile per Categoria:", size=13, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900)
            )
            if not per_cat:
                layer_budget.controls.append(ft.Text("Nessun movimento nel mese selezionato.", size=12, italic=True, color=ft.Colors.GREY_600))
            else:
                for nome_c, st_c in sorted(per_cat.items(), key=lambda x: -(x[1]["spese_eff"] + x[1]["spese_prev"] + x[1]["entrate_eff"] + x[1]["entrate_prev"])):
                    tot_s = st_c["spese_eff"] + st_c["spese_prev"]
                    tot_e = st_c["entrate_eff"] + st_c["entrate_prev"]
                    layer_budget.controls.append(
                        ft.Container(
                            content=ft.Column([
                                ft.Row([
                                    ft.Icon(ft.Icons.CIRCLE, color=st_c["colore"], size=12),
                                    ft.Text(nome_c, size=13, weight=ft.FontWeight.BOLD, expand=True),
                                    *([ft.Text(f"Uscite: {formatta_euro(tot_s)}", size=12, weight=ft.FontWeight.BOLD, color="#C62828")] if tot_s > 0 else []),
                                    *([ft.Text(f"Entrate: {formatta_euro(tot_e)}", size=12, weight=ft.FontWeight.BOLD, color="#2E7D32")] if tot_e > 0 else []),
                                ]),
                                ft.Text(
                                    f"Spese Effettive: {formatta_euro(st_c['spese_eff'])} • Spese Previste: {formatta_euro(st_c['spese_prev'])}"
                                    + (f"  |  Entrate Eff.: {formatta_euro(st_c['entrate_eff'])} • Prev.: {formatta_euro(st_c['entrate_prev'])}" if tot_e > 0 else ""),
                                    size=10.5,
                                    color=ft.Colors.GREY_800,
                                ),
                            ], spacing=3),
                            bgcolor=ft.Colors.WHITE,
                            padding=8,
                            border_radius=6,
                            border=ft.Border(left=ft.BorderSide(5, st_c["colore"]), top=ft.BorderSide(1, "#E0E0E0"), bottom=ft.BorderSide(1, "#E0E0E0"), right=ft.BorderSide(1, "#E0E0E0")),
                        )
                    )

            previsti_mese = [m for m in movimenti_mese_corrente if normalizza_stato_budget(m.get("stato")) == "PREVISTO"]
            previsti_mese.sort(key=lambda x: x.get("data_movimento", ""))
            layer_budget.controls.append(ft.Divider(height=10))
            layer_budget.controls.append(
                ft.Text(f"⏳ Scadenze Previste nel mese da confermare ({len(previsti_mese)}):", size=13, weight=ft.FontWeight.BOLD, color="#E65100")
            )
            for mp in previsti_mese:
                layer_budget.controls.append(crea_card_movimento(mp, vista_card="Giorno"))

        try:
            page.update()
        except Exception:
            pass

    def ricarica_e_aggiorna():
        ricarica_dati_base()
        aggiorna_tutto()

    page.aggiorna_vista_budget = ricarica_e_aggiorna

    header = ft.Row(
        controls=[
            ft.Row([
                ft.IconButton(ft.Icons.CHEVRON_LEFT, on_click=nav_prec, icon_size=20),
                ft.IconButton(ft.Icons.CHEVRON_RIGHT, on_click=nav_succ, icon_size=20),
                testo_header,
            ], alignment=ft.MainAxisAlignment.START, spacing=0),
            ft.Row([btn_giorno, btn_settimana, btn_mese, btn_riepilogo], spacing=4),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        wrap=True,
        run_spacing=8,
    )

    sezione_filtri = ft.Column([
        ft.Text("Filtra per Stato:", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_600),
        row_filtri_stato,
        ft.Text("Filtra per Categoria Budget (Selezione Multipla):", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_600),
        row_filtri_cat,
        ft.Text("Filtra per Componente Famiglia (Selezione Multipla):", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_600),
        row_filtri_fam,
    ], spacing=5)

    aggiorna_tutto()

    container_principale = ft.Container(
        content=ft.Column([
            header,
            box_cruscotto_mese,
            area_dinamica,
            ft.Divider(height=16, color="#EEEEEE"),
            sezione_filtri,
        ], spacing=8, scroll=ft.ScrollMode.AUTO),
        padding=5,
        expand=True,
    )
    container_principale.ricarica_e_aggiorna = ricarica_e_aggiorna
    container_principale.imposta_data_e_vista = lambda dt, vst="Giorno": (
        stato.update({"data": dt, "vista": vst}),
        sincronizza_stato_esterno(),
        aggiorna_tutto(),
    )
    return container_principale
