import flet as ft
import calendar
import os
import re
import json
import html
import threading
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    supabase = None
    print(f"Errore connessione Supabase in modulo_menu: {e}")

MESI = ["", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]
GIORNI = ["LUN", "MAR", "MER", "GIO", "VEN", "SAB", "DOM"]
GIORNI_ESTESI = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]

SUPERCATEGORIE = ["Colazione", "Pranzo", "Cena", "Merende", "Varie"]
SUBCATEGORIE_PASTO = ["Antipasto", "Primo", "Secondo", "Frutta", "Dolci", "Bevande"]
SUB_CON_RICETTA = ["Primo", "Secondo", "Dolci"]
UNITA_MISURA = ["g", "kg", "ml", "l", "pz", "fette", "cucchiai", "tazze", "q.b."]

COLORI_PASTI = {
    "Colazione": "#FF9800",
    "Pranzo": "#4CAF50",
    "Cena": "#2196F3",
    "Merende": "#9C27B0",
    "Varie": "#795548",
    "Dispensa": "#00897B",
    "Detersivi": "#546E7A"
}

CACHE_ANAGRAFICA = {
    "ingredienti": None,
    "ricette": None,
    "conteggio_ing_ricette": None
}


def apri_dialog_sicuro(page: ft.Page, dlg: ft.AlertDialog):
    try:
        page.open(dlg)
    except AttributeError:
        if dlg not in page.overlay:
            page.overlay.append(dlg)
        dlg.open = True
        page.update()


def chiudi_dialog_sicuro(page: ft.Page, dlg: ft.AlertDialog):
    if not dlg:
        return
    try:
        page.close(dlg)
    except AttributeError:
        dlg.open = False
        page.update()


def parse_qta(val_str):
    if not val_str:
        return 0.0
    try:
        return float(str(val_str).replace(",", ".").strip())
    except ValueError:
        return 0.0


def formatta_numero(val):
    try:
        f = float(val)
        return str(int(f)) if f.is_integer() else f"{f:.2f}".rstrip("0").rstrip(".")
    except Exception:
        return str(val)


def bordo_campo_uniforme(colore="#9E9E9E", spessore=1):
    return ft.Border(
        top=ft.BorderSide(spessore, colore),
        bottom=ft.BorderSide(spessore, colore),
        left=ft.BorderSide(spessore, colore),
        right=ft.BorderSide(spessore, colore)
    )


def aggiorna_flag_preso_db(id_riga_menu, valore_bool):
    try:
        supabase.table("menu_giornalieri").update({"preso": bool(valore_bool)}).eq("id", id_riga_menu).execute()
    except Exception as ex:
        print("Nota: esegui 'ALTER TABLE menu_giornalieri ADD COLUMN IF NOT EXISTS preso BOOLEAN DEFAULT FALSE;' su Supabase:", ex)


# ==========================================================
# COMPONENTI GEMELLI: QUANTITÀ E UNITÀ CON STESSA ALTEZZA AL PIXEL
# ==========================================================
def crea_box_quantita(valore_iniziale="1", width=90, height=34, text_size=12, mostra_prefisso=True, expand=False):
    tf = ft.TextField(
        value=str(valore_iniziale),
        border=ft.InputBorder.NONE,
        dense=True,
        text_size=text_size,
        content_padding=ft.Padding(2, 0, 2, 0),
        text_align=ft.TextAlign.CENTER,
        expand=True
    )
    controlli_riga = []
    if mostra_prefisso:
        controlli_riga.append(ft.Text("Q.tà:", size=10, color=ft.Colors.GREY_700))
    controlli_riga.append(tf)

    box = ft.Container(
        content=ft.Row(
            controlli_riga,
            spacing=2,
            vertical_alignment=ft.CrossAxisAlignment.CENTER
        ),
        width=None if expand else width,
        expand=expand,
        height=height,
        padding=ft.Padding(6, 0, 6, 0),
        bgcolor=ft.Colors.WHITE,
        border=bordo_campo_uniforme("#9E9E9E"),
        border_radius=5
    )
    box.textfield = tf
    return box


def crea_selettore_unita(page: ft.Page, valore_iniziale="pz", width=72, height=34, text_size=12):
    val_corrente = valore_iniziale if valore_iniziale in UNITA_MISURA else "g"
    stato_u = {"value": val_corrente}
    lbl_val = ft.Text(val_corrente, size=text_size, color=ft.Colors.BLACK, weight=ft.FontWeight.BOLD, no_wrap=True)

    def seleziona_u(u_scelta):
        stato_u["value"] = u_scelta
        lbl_val.value = u_scelta
        try:
            page.update()
        except Exception:
            pass

    box_visivo = ft.Container(
        content=ft.Row([
            lbl_val,
            ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=16, color=ft.Colors.GREY_700)
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=2),
        width=width,
        height=height,
        padding=ft.Padding(8, 0, 4, 0),
        bgcolor=ft.Colors.WHITE,
        border=bordo_campo_uniforme("#9E9E9E"),
        border_radius=5
    )

    menu_btn = ft.PopupMenuButton(
        content=box_visivo,
        items=[
            ft.PopupMenuItem(
                content=ft.Text(u, size=13, weight=ft.FontWeight.BOLD if u == stato_u["value"] else ft.FontWeight.NORMAL),
                on_click=lambda e, u_val=u: seleziona_u(u_val)
            ) for u in UNITA_MISURA
        ],
        tooltip="Seleziona unità di misura"
    )
    menu_btn.get_value = lambda: stato_u["value"]

    def set_value(nuovo_v):
        if nuovo_v in UNITA_MISURA:
            stato_u["value"] = nuovo_v
            lbl_val.value = nuovo_v

    menu_btn.set_value = set_value
    return menu_btn


def assicura_cache_anagrafica(forza_ricarica=False):
    if not supabase:
        return [], []
    if forza_ricarica or CACHE_ANAGRAFICA["ingredienti"] is None:
        res_ing = supabase.table("ingredienti").select("*").order("nome").execute()
        CACHE_ANAGRAFICA["ingredienti"] = res_ing.data if res_ing.data else []
    if forza_ricarica or CACHE_ANAGRAFICA["ricette"] is None:
        res_ric = supabase.table("ricette").select("*").order("nome").execute()
        CACHE_ANAGRAFICA["ricette"] = res_ric.data if res_ric.data else []
    if forza_ricarica or CACHE_ANAGRAFICA["conteggio_ing_ricette"] is None:
        res_ri = supabase.table("ricette_ingredienti").select("id_ricetta").execute()
        conteggi = {}
        for r in (res_ri.data or []):
            rid = r.get("id_ricetta")
            if rid:
                conteggi[rid] = conteggi.get(rid, 0) + 1
        CACHE_ANAGRAFICA["conteggio_ing_ricette"] = conteggi
    return CACHE_ANAGRAFICA["ingredienti"], CACHE_ANAGRAFICA["ricette"]


def ricetta_ha_ingredienti(id_ricetta):
    if not id_ricetta:
        return False
    conteggi = CACHE_ANAGRAFICA.get("conteggio_ing_ricette") or {}
    return conteggi.get(id_ricetta, 0) > 0


# ==========================================================
# MOTORE DI RICERCA RICETTE SU INTERNET + RICALCOLO QUANTITÀ
# ==========================================================
def analizza_stringa_ingrediente_web(testo_raw, fattore_scala):
    testo = html.unescape(str(testo_raw or "")).strip()
    testo = re.sub(r"<[^>]+>", " ", testo)
    testo = re.sub(r"\([^)]*\)", " ", testo)
    testo = re.sub(r"\s+", " ", testo).strip()
    if not testo:
        return None

    low = testo.lower()
    if "q.b." in low or "quanto basta" in low or re.search(r"\bqb\b", low):
        nome_pulito = re.sub(r"(?i)\b(q\.?b\.?|quanto basta)\b", "", testo).strip(" ,.-:")
        nome_pulito = re.sub(r"(?i)^(di|d'|del|della|dei|degli|delle)\s+", "", nome_pulito).strip()
        if nome_pulito:
            return {"nome": nome_pulito.capitalize(), "quantita": 0.0, "unita": "q.b."}

    mappa_unita = [
        (r"\b(kg|chili|chilo|chilogrammi)\b", "kg"),
        (r"\b(g|gr|grammi|grammo)\b", "g"),
        (r"\b(ml|millilitri)\b", "ml"),
        (r"\b(l|lt|litri|litro)\b", "l"),
        (r"\b(cucchiai|cucchiaio|cucchiaini|cucchiaino)\b", "cucchiai"),
        (r"\b(fette|fetta)\b", "fette"),
        (r"\b(tazze|tazza|bicchieri|bicchiere)\b", "tazze"),
        (r"\b(spicchi|spicchio|pz|pezzi|pezzo|bustina|bustine|rametto|rametti|foglie|foglia)\b", "pz"),
    ]

    match_num = re.search(r"(\d+[.,]\d+|\d+\s*/\s*\d+|\d+)", testo)
    qta_base = 1.0
    if match_num:
        num_str = match_num.group(1).replace(" ", "")
        if "/" in num_str:
            parti = num_str.split("/")
            try:
                qta_base = float(parti[0]) / float(parti[1])
            except Exception:
                qta_base = 1.0
        else:
            qta_base = parse_qta(num_str)
        testo_senza_num = (testo[:match_num.start()] + " " + testo[match_num.end():]).strip()
    else:
        testo_senza_num = testo
        qta_base = 0.0

    unita_trovata = "pz" if qta_base > 0 else "q.b."
    for pattern, u_std in mappa_unita:
        if re.search(pattern, testo_senza_num, flags=re.IGNORECASE):
            unita_trovata = u_std
            testo_senza_num = re.sub(pattern, " ", testo_senza_num, count=1, flags=re.IGNORECASE).strip()
            break

    nome_finale = re.sub(r"\s+", " ", testo_senza_num).strip(" ,.-:/")
    nome_finale = re.sub(r"(?i)^(di|d'|del|della|dei|degli|delle|per)\s+", "", nome_finale).strip()
    nome_finale = re.sub(r"(?i)\s+(di|d'|a|per)$", "", nome_finale).strip()

    if not nome_finale or len(nome_finale) < 2:
        return None

    if unita_trovata == "q.b." or qta_base <= 0:
        qta_scalata = 0.0
        unita_trovata = "q.b."
    else:
        qta_scalata = qta_base * fattore_scala
        if unita_trovata in ["g", "ml"] and qta_scalata >= 20:
            qta_scalata = round(qta_scalata / 5.0) * 5.0
        elif unita_trovata == "pz":
            qta_scalata = max(1.0, round(qta_scalata))
        else:
            qta_scalata = round(qta_scalata, 2)

    return {
        "nome": nome_finale[:60].capitalize(),
        "quantita": qta_scalata,
        "unita": unita_trovata
    }


def estrai_recipe_da_jsonld(html_page):
    blocchi = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html_page, flags=re.DOTALL | re.IGNORECASE)
    for b in blocchi:
        try:
            dati = json.loads(b.strip())
            candidati = []
            if isinstance(dati, list):
                candidati.extend(dati)
            elif isinstance(dati, dict):
                if "@graph" in dati and isinstance(dati["@graph"], list):
                    candidati.extend(dati["@graph"])
                else:
                    candidati.append(dati)
            for item in candidati:
                if not isinstance(item, dict):
                    continue
                tipo = item.get("@type")
                is_recipe = (tipo == "Recipe") or (isinstance(tipo, list) and "Recipe" in tipo)
                if is_recipe and item.get("recipeIngredient"):
                    porzioni_base = 4
                    yield_val = item.get("recipeYield")
                    if yield_val:
                        if isinstance(yield_val, list):
                            yield_val = yield_val[0]
                        m_y = re.search(r"(\d+)", str(yield_val))
                        if m_y:
                            p_val = int(m_y.group(1))
                            if 1 <= p_val <= 30:
                                porzioni_base = p_val
                    return porzioni_base, item.get("recipeIngredient", [])
        except Exception:
            continue
    return None, None


def cerca_ricetta_su_internet(nome_piatto: str, num_persone: int):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "it-IT,it;q=0.9"
    }
    q_enc = urllib.parse.quote_plus(nome_piatto.strip())
    urls_ricerca = [
        f"https://www.giallozafferano.it/ricerca-ricette/{q_enc}/",
        f"https://www.cucchiaio.it/ricerca.html?q={q_enc}"
    ]

    links_ricette = []
    for u_search in urls_ricerca:
        try:
            req = urllib.request.Request(u_search, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                html_s = resp.read().decode("utf-8", errors="ignore")
            trovati_gz = re.findall(r'https://ricette\.giallozafferano\.it/[a-zA-Z0-9\-_]+\.html', html_s)
            trovati_ca = re.findall(r'https://www\.cucchiaio\.it/ricetta/[a-zA-Z0-9\-_]+/?', html_s)
            for lk in (trovati_gz + trovati_ca):
                if lk not in links_ricette:
                    links_ricette.append(lk)
            if links_ricette:
                break
        except Exception:
            continue

    for link in links_ricette[:3]:
        try:
            req_r = urllib.request.Request(link, headers=headers)
            with urllib.request.urlopen(req_r, timeout=8) as resp_r:
                html_r = resp_r.read().decode("utf-8", errors="ignore")
            porzioni_base, raw_ings = estrai_recipe_da_jsonld(html_r)
            if raw_ings:
                fattore = float(num_persone) / float(porzioni_base or 4)
                risultati = []
                nomi_visti = set()
                for r_str in raw_ings:
                    parsed = analizza_stringa_ingrediente_web(r_str, fattore)
                    if parsed and parsed["nome"].lower() not in nomi_visti:
                        nomi_visti.add(parsed["nome"].lower())
                        risultati.append(parsed)
                if risultati:
                    return risultati, f"Ricetta trovata online (calibrata da {porzioni_base} a {num_persone} persone)"
        except Exception:
            continue

    # Fallback intelligente se il piatto ha un nome casalingo non indicizzato
    low_p = nome_piatto.lower()
    f = float(num_persone) / 8.0
    fallback = []
    if any(k in low_p for k in ["pasta", "spaghetti", "rigatoni", "penne", "fusilli", "orecchiette", "lasagne", "risotto", "riso"]):
        base_principale = "Riso" if "ris" in low_p else "Pasta"
        fallback.append({"nome": base_principale, "quantita": round(640 * f), "unita": "g"})
        fallback.append({"nome": "Olio extravergine d'oliva", "quantita": max(1, round(4 * f)), "unita": "cucchiai"})
        fallback.append({"nome": "Sale", "quantita": 0, "unita": "q.b."})
    elif any(k in low_p for k in ["pollo", "carne", "arrosto", "polpette", "bistecca", "scaloppine", "spezzatino", "cotolett"]):
        fallback.append({"nome": nome_piatto.strip().capitalize(), "quantita": round(1200 * f), "unita": "g"})
        fallback.append({"nome": "Contorno / Patate", "quantita": round(800 * f), "unita": "g"})
        fallback.append({"nome": "Olio extravergine d'oliva", "quantita": max(1, round(3 * f)), "unita": "cucchiai"})
    elif any(k in low_p for k in ["pesce", "merluzzo", "orata", "spigola", "salmone", "tonno", "calamari", "gamberi"]):
        fallback.append({"nome": nome_piatto.strip().capitalize(), "quantita": round(1200 * f), "unita": "g"})
        fallback.append({"nome": "Olio extravergine d'oliva", "quantita": max(1, round(3 * f)), "unita": "cucchiai"})
    else:
        fallback.append({"nome": nome_piatto.strip().capitalize(), "quantita": round(800 * f), "unita": "g"})
        fallback.append({"nome": "Sale", "quantita": 0, "unita": "q.b."})

    return fallback, f"Impostata base per {num_persone} persone (personalizzabile)"


# ==========================================================
# POPUP RINOMINA / MODIFICA INGREDIENTE (SENZA DUPLICARE)
# ==========================================================
def apri_popup_rinomina_ingrediente(page: ft.Page, ing_obj: dict, lista_ingredienti: list, on_done_callback):
    txt_nome = ft.TextField(label="Nuovo nome ingrediente", value=ing_obj.get("nome", ""), dense=True, text_size=13)
    sel_unita = crea_selettore_unita(
        page,
        valore_iniziale=ing_obj.get("unita_default") if ing_obj.get("unita_default") in UNITA_MISURA else "g",
        width=90,
        height=36,
        text_size=12
    )
    txt_err = ft.Text("", color=ft.Colors.RED_700, size=11, visible=False)
    spinner_mod = ft.ProgressRing(width=18, height=18, stroke_width=2, color=ft.Colors.ORANGE_800, visible=False)
    dlg_mod = None

    def salva_mod(e):
        nuovo_nome = (txt_nome.value or "").strip().capitalize()
        if not nuovo_nome:
            txt_err.value = "⚠️ Inserisci un nome valido."
            txt_err.visible = True
            page.update()
            return

        spinner_mod.visible = True
        page.update()

        def lavoro():
            try:
                unita_scelta = sel_unita.get_value()
                conflitto = next(
                    (i for i in lista_ingredienti if i["nome"].lower() == nuovo_nome.lower() and str(i["id"]) != str(ing_obj["id"])),
                    None
                )
                if conflitto:
                    supabase.table("ricette_ingredienti").update({"id_ingrediente": conflitto["id"]}).eq("id_ingrediente", ing_obj["id"]).execute()
                    supabase.table("menu_giornalieri").update({"id_ingrediente": conflitto["id"]}).eq("id_ingrediente", ing_obj["id"]).execute()
                    supabase.table("ingredienti").delete().eq("id", ing_obj["id"]).execute()
                else:
                    supabase.table("ingredienti").update({
                        "nome": nuovo_nome,
                        "unita_default": unita_scelta
                    }).eq("id", ing_obj["id"]).execute()

                assicura_cache_anagrafica(forza_ricarica=True)
                chiudi_dialog_sicuro(page, dlg_mod)
                if on_done_callback:
                    on_done_callback()
            except Exception as ex:
                spinner_mod.visible = False
                txt_err.value = f"⚠️ Errore: {ex}"
                txt_err.visible = True
                page.update()

        threading.Thread(target=lavoro, daemon=True).start()

    dlg_mod = ft.AlertDialog(
        title=ft.Row([
            ft.Text("✏️ Modifica Ingrediente", size=15, weight=ft.FontWeight.BOLD, expand=True),
            spinner_mod
        ]),
        content=ft.Container(
            content=ft.Column([
                ft.Text("La modifica aggiornerà il nome ovunque senza creare duplicati.", size=11, color=ft.Colors.GREY_700),
                txt_nome,
                ft.Row([ft.Text("Unità predefinita:", size=12), sel_unita], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                txt_err
            ], tight=True, spacing=8),
            width=310
        ),
        actions=[
            ft.TextButton("Annulla", on_click=lambda e: chiudi_dialog_sicuro(page, dlg_mod)),
            ft.Container(
                content=ft.Text("Salva", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=12),
                bgcolor=ft.Colors.ORANGE_800, padding=8, border_radius=5, on_click=salva_mod
            )
        ]
    )
    apri_dialog_sicuro(page, dlg_mod)


# ==========================================================
# POPUP MODIFICA O ELIMINAZIONE INTERA RICETTA DAL DATABASE
# ==========================================================
def apri_popup_gestione_ricetta(page: ft.Page, id_ricetta: str, nome_attuale: str, portata_attuale: str, on_done_callback):
    txt_nome_ric = ft.TextField(label="Nome Ricetta", value=nome_attuale, dense=True, text_size=13)
    stato_portata = {"val": portata_attuale if portata_attuale in SUB_CON_RICETTA else "Primo"}
    lbl_portata = ft.Text(stato_portata["val"], size=12, weight=ft.FontWeight.BOLD)

    def cambia_port(p_scelta):
        stato_portata["val"] = p_scelta
        lbl_portata.value = p_scelta
        page.update()

    sel_portata = ft.PopupMenuButton(
        content=ft.Container(
            content=ft.Row([lbl_portata, ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=16)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            width=110, height=34, padding=ft.Padding(8, 0, 4, 0),
            bgcolor=ft.Colors.WHITE, border=bordo_campo_uniforme(), border_radius=5
        ),
        items=[ft.PopupMenuItem(content=ft.Text(p), on_click=lambda e, pv=p: cambia_port(pv)) for p in SUB_CON_RICETTA]
    )

    txt_err = ft.Text("", color=ft.Colors.RED_700, size=11, visible=False)
    spinner_ric = ft.ProgressRing(width=18, height=18, stroke_width=2, color=ft.Colors.BLUE_900, visible=False)
    dlg_ric = None

    def salva_ricetta_db(e):
        nuovo_n = (txt_nome_ric.value or "").strip().capitalize()
        if not nuovo_n:
            txt_err.value = "⚠️ Il nome della ricetta è obbligatorio."
            txt_err.visible = True
            page.update()
            return
        spinner_ric.visible = True
        page.update()

        def lavoro():
            try:
                supabase.table("ricette").update({
                    "nome": nuovo_n,
                    "portata_default": stato_portata["val"]
                }).eq("id", id_ricetta).execute()
                assicura_cache_anagrafica(forza_ricarica=True)
                chiudi_dialog_sicuro(page, dlg_ric)
                if on_done_callback:
                    on_done_callback()
            except Exception as ex:
                spinner_ric.visible = False
                txt_err.value = f"⚠️ Errore: {ex}"
                txt_err.visible = True
                page.update()

        threading.Thread(target=lavoro, daemon=True).start()

    def elimina_ricetta_definitivamente(e):
        spinner_ric.visible = True
        page.update()

        def lavoro():
            try:
                supabase.table("ricette").delete().eq("id", id_ricetta).execute()
                assicura_cache_anagrafica(forza_ricarica=True)
                chiudi_dialog_sicuro(page, dlg_ric)
                if on_done_callback:
                    on_done_callback()
            except Exception as ex:
                spinner_ric.visible = False
                txt_err.value = f"⚠️ Errore eliminazione: {ex}"
                txt_err.visible = True
                page.update()

        threading.Thread(target=lavoro, daemon=True).start()

    dlg_ric = ft.AlertDialog(
        title=ft.Row([
            ft.Text("🍲 Gestione Ricetta", size=15, weight=ft.FontWeight.BOLD, expand=True),
            spinner_ric
        ]),
        content=ft.Container(
            content=ft.Column([
                txt_nome_ric,
                ft.Row([ft.Text("Portata predefinita:", size=12), sel_portata], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Divider(height=8, color="#E0E0E0"),
                ft.Text("Attenzione: 'Elimina dal DB' cancella definitivamente la ricetta dal ricettario.", size=10, color=ft.Colors.RED_700, italic=True),
                txt_err
            ], tight=True, spacing=8),
            width=310
        ),
        actions=[
            ft.Container(
                content=ft.Text("🗑️ Elimina dal DB", color=ft.Colors.WHITE, size=11, weight=ft.FontWeight.BOLD),
                bgcolor=ft.Colors.RED_700, padding=8, border_radius=5, on_click=elimina_ricetta_definitivamente
            ),
            ft.TextButton("Annulla", on_click=lambda e: chiudi_dialog_sicuro(page, dlg_ric)),
            ft.Container(
                content=ft.Text("Aggiorna", color=ft.Colors.WHITE, size=11, weight=ft.FontWeight.BOLD),
                bgcolor=ft.Colors.BLUE_900, padding=8, border_radius=5, on_click=salva_ricetta_db
            )
        ],
        actions_alignment=ft.MainAxisAlignment.SPACE_BETWEEN
    )
    apri_dialog_sicuro(page, dlg_ric)


# ==========================================================
# POPUP SCHEDA SINGOLA RICETTA + RICERCA INTERNET AUTOMATICA
# ==========================================================
def apri_popup_dettaglio_ricetta_semplificata(page: ft.Page, id_voce_menu: str, id_ricetta: str, nome_ricetta: str, portata: str, on_close_refresh):
    if not supabase:
        return

    lista_ingredienti, _ = assicura_cache_anagrafica(forza_ricarica=False)
    spinner_det = ft.ProgressRing(width=18, height=18, stroke_width=2.5, color=ft.Colors.WHITE)
    lbl_det_status = ft.Text("Caricamento ricetta...", size=11, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD)
    banner_det = ft.Container(
        content=ft.Row([spinner_det, lbl_det_status], alignment=ft.MainAxisAlignment.CENTER, spacing=8),
        bgcolor=ft.Colors.ORANGE_800, padding=6, border_radius=6, visible=True
    )

    col_corpo = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)
    modificato = [False]
    dlg_det = None

    def chiudi_det(e=None):
        chiudi_dialog_sicuro(page, dlg_det)
        if modificato[0] and on_close_refresh:
            on_close_refresh()

    def togli_dal_menu_giorno(e):
        lbl_det_status.value = "Rimozione piatto dal giorno..."
        banner_det.visible = True
        page.update()

        def w():
            try:
                supabase.table("menu_giornalieri").delete().eq("id", id_voce_menu).execute()
                modificato[0] = True
                chiudi_det()
            except Exception as ex:
                print("Errore rimozione piatto:", ex)

        threading.Thread(target=w, daemon=True).start()

    dlg_det = ft.AlertDialog(
        title=ft.Column([
            ft.Row([
                ft.Text(f"🍲 {nome_ricetta}", size=15, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900, expand=True),
                ft.IconButton(
                    icon=ft.Icons.EDIT, icon_color=ft.Colors.ORANGE_800, icon_size=18,
                    tooltip="Rinomina o Elimina dal DB",
                    on_click=lambda e: apri_popup_gestione_ricetta(page, id_ricetta, nome_ricetta, portata, lambda: (modificato.__setitem__(0, True), ricarica_ingredienti_ricetta()))
                ),
                ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE, icon_color=ft.Colors.RED_700, icon_size=18,
                    tooltip="Togli piatto da questo giorno",
                    on_click=togli_dal_menu_giorno
                )
            ], spacing=2),
            banner_det
        ], spacing=4, tight=True),
        content=ft.Container(content=col_corpo, width=350, height=440),
        actions=[
            ft.Container(
                content=ft.Text("Chiudi", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=12),
                bgcolor=ft.Colors.BLUE_900, padding=ft.Padding(14, 8, 14, 8), border_radius=6, on_click=chiudi_det
            )
        ],
        shape=ft.RoundedRectangleBorder(radius=10),
        content_padding=10,
        inset_padding=ft.Padding(10, 15, 10, 15)
    )

    apri_dialog_sicuro(page, dlg_det)

    def ottieni_o_crea_ing(nome_raw, unita_scelta):
        nome_pulito = nome_raw.strip().capitalize()
        esistente = next((i for i in lista_ingredienti if i["nome"].lower() == nome_pulito.lower()), None)
        if esistente:
            return esistente["id"]
        res = supabase.table("ingredienti").insert({
            "nome": nome_pulito,
            "unita_default": unita_scelta or "g"
        }).execute()
        nuovo = res.data[0]
        lista_ingredienti.append(nuovo)
        return nuovo["id"]

    def ricarica_ingredienti_ricetta(msg_extra=None):
        banner_det.visible = True
        lbl_det_status.value = "Lettura ingredienti..."
        page.update()

        def worker():
            try:
                res_ri = supabase.table("ricette_ingredienti").select(
                    "*, ingredienti(id, nome, unita_default)"
                ).eq("id_ricetta", id_ricetta).execute()
                righe_ri = res_ri.data if res_ri.data else []

                if CACHE_ANAGRAFICA["conteggio_ing_ricette"] is not None:
                    CACHE_ANAGRAFICA["conteggio_ing_ricette"][id_ricetta] = len(righe_ri)

                col_corpo.controls.clear()

                if msg_extra:
                    col_corpo.controls.append(
                        ft.Container(
                            content=ft.Text(f"✅ {msg_extra}", size=11, color=ft.Colors.GREEN_800, weight=ft.FontWeight.BOLD),
                            bgcolor="#E8F5E9", padding=6, border_radius=6
                        )
                    )

                # SE LA RICETTA NON HA INGREDIENTI NEL DB -> BLOCCO RICERCA SU INTERNET CON SCELTA PERSONE
                if not righe_ri:
                    box_persone = crea_box_quantita(valore_iniziale="8", width=85, height=34, text_size=13, mostra_prefisso=False)

                    def avvia_ricerca_web(ev):
                        num_p = int(max(1, parse_qta(box_persone.textfield.value) or 8))
                        banner_det.visible = True
                        lbl_det_status.value = f"🌐 Ricerca '{nome_ricetta}' per {num_p} persone..."
                        page.update()

                        def worker_web():
                            try:
                                trovati, info_msg = cerca_ricetta_su_internet(nome_ricetta, num_p)
                                for item_w in trovati:
                                    id_i = ottieni_o_crea_ing(item_w["nome"], item_w["unita"])
                                    supabase.table("ricette_ingredienti").insert({
                                        "id_ricetta": id_ricetta,
                                        "id_ingrediente": id_i,
                                        "quantita": item_w["quantita"],
                                        "unita_misura": item_w["unita"]
                                    }).execute()
                                assicura_cache_anagrafica(forza_ricarica=True)
                                modificato[0] = True
                                ricarica_ingredienti_ricetta(msg_extra=info_msg)
                            except Exception as ex:
                                print("Errore ricerca web:", ex)
                                banner_det.visible = False
                                page.update()

                        threading.Thread(target=worker_web, daemon=True).start()

                    col_corpo.controls.append(
                        ft.Container(
                            content=ft.Column([
                                ft.Row([
                                    ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color=ft.Colors.RED_700, size=20),
                                    ft.Text("Ricetta non ancora compilata nel DB!", size=12, weight=ft.FontWeight.BOLD, color=ft.Colors.RED_700, expand=True)
                                ], spacing=6),
                                ft.Text("Indica per quante persone vuoi rapportare le dosi e avvia la compilazione automatica da internet:", size=11, color=ft.Colors.GREY_800),
                                ft.Row([
                                    ft.Text("Numero persone:", size=12, weight=ft.FontWeight.BOLD),
                                    box_persone
                                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                                ft.Container(
                                    content=ft.Row([
                                        ft.Icon(ft.Icons.TRAVEL_EXPLORE, color=ft.Colors.WHITE, size=18),
                                        ft.Text("CERCA SU INTERNET E COMPILA", color=ft.Colors.WHITE, size=11, weight=ft.FontWeight.BOLD)
                                    ], alignment=ft.MainAxisAlignment.CENTER, spacing=6),
                                    bgcolor=ft.Colors.ORANGE_800, padding=10, border_radius=6,
                                    on_click=avvia_ricerca_web
                                )
                            ], spacing=8),
                            bgcolor="#FFF8E1", padding=10, border_radius=8,
                            border=bordo_campo_uniforme("#E53935", 1.5)
                        )
                    )
                else:
                    col_corpo.controls.append(
                        ft.Text("Ingredienti associati alla ricetta:", size=11, color=ft.Colors.GREY_700, italic=True)
                    )

                for ri in righe_ri:
                    ing_info = ri.get("ingredienti") or {"id": ri["id_ingrediente"], "nome": "Ingrediente", "unita_default": "g"}
                    nome_i = ing_info.get("nome", "Ingrediente")
                    qta_init = formatta_numero(ri.get("quantita") or 0)
                    unita_init = ri.get("unita_misura") or "g"

                    box_qta_ri = crea_box_quantita(valore_iniziale=qta_init, width=88, height=32, text_size=11, mostra_prefisso=True)
                    sel_uni_ri = crea_selettore_unita(page, valore_iniziale=unita_init, width=72, height=32, text_size=11)

                    def salva_qta_ri(e, id_riga=ri["id"], b_q=box_qta_ri, s_u=sel_uni_ri):
                        banner_det.visible = True
                        lbl_det_status.value = "Salvataggio quantità..."
                        page.update()

                        def w_q():
                            supabase.table("ricette_ingredienti").update({
                                "quantita": parse_qta(b_q.textfield.value),
                                "unita_misura": s_u.get_value()
                            }).eq("id", id_riga).execute()
                            modificato[0] = True
                            ricarica_ingredienti_ricetta()

                        threading.Thread(target=w_q, daemon=True).start()

                    def elim_ing_ri(e, id_riga=ri["id"]):
                        banner_det.visible = True
                        lbl_det_status.value = "Rimozione ingrediente..."
                        page.update()

                        def w_d():
                            supabase.table("ricette_ingredienti").delete().eq("id", id_riga).execute()
                            modificato[0] = True
                            ricarica_ingredienti_ricetta()

                        threading.Thread(target=w_d, daemon=True).start()

                    col_corpo.controls.append(
                        ft.Container(
                            content=ft.Column([
                                ft.Row([
                                    ft.Text(f"▪ {nome_i}", size=12, weight=ft.FontWeight.BOLD, expand=True),
                                    ft.IconButton(
                                        icon=ft.Icons.EDIT, icon_color=ft.Colors.ORANGE_800, icon_size=14,
                                        on_click=lambda e, item=ing_info: apri_popup_rinomina_ingrediente(page, item, lista_ingredienti, lambda: (modificato.__setitem__(0, True), ricarica_ingredienti_ricetta()))
                                    ),
                                    ft.IconButton(icon=ft.Icons.CLOSE, icon_color=ft.Colors.RED_700, icon_size=15, on_click=elim_ing_ri)
                                ], spacing=2),
                                ft.Row([
                                    box_qta_ri,
                                    sel_uni_ri,
                                    ft.Container(
                                        content=ft.Text("💾 Salva Q.tà", size=10, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                                        bgcolor=ft.Colors.GREEN_700, height=32, padding=ft.Padding(10, 0, 10, 0),
                                        alignment=ft.Alignment(0, 0), border_radius=5, on_click=salva_qta_ri
                                    )
                                ], spacing=5, vertical_alignment=ft.CrossAxisAlignment.CENTER)
                            ], spacing=3),
                            bgcolor="#F5F7FA", padding=5, border_radius=5, border=bordo_campo_uniforme("#E0E0E0")
                        )
                    )

                # Aggiunta manuale nuovo ingrediente
                col_corpo.controls.append(ft.Divider(height=6, color="#BBDEFB"))
                txt_nuovo_i = ft.TextField(label="Aggiungi ingrediente manualmente...", dense=True, text_size=11, expand=True)
                box_nuova_q = crea_box_quantita(valore_iniziale="100", height=34, text_size=11, mostra_prefisso=True, expand=True)
                sel_nuova_u = crea_selettore_unita(page, valore_iniziale="g", width=72, height=34, text_size=11)
                riga_sugg = ft.Row(wrap=True, spacing=4, run_spacing=4, visible=False)

                def sel_s(ing_s):
                    txt_nuovo_i.value = ing_s["nome"]
                    if ing_s.get("unita_default") in UNITA_MISURA:
                        sel_nuova_u.set_value(ing_s["unita_default"])
                    riga_sugg.visible = False
                    page.update()

                def filtra_s(e):
                    t = (txt_nuovo_i.value or "").strip().lower()
                    riga_sugg.controls.clear()
                    if t:
                        matches = [i for i in lista_ingredienti if t in i["nome"].lower()][:6]
                        for m in matches:
                            riga_sugg.controls.append(
                                ft.Container(
                                    content=ft.Text(f"🔍 {m['nome']}", size=10, color=ft.Colors.BLUE_900, weight=ft.FontWeight.BOLD),
                                    bgcolor=ft.Colors.BLUE_50, padding=ft.Padding(6, 3, 6, 3), border_radius=8,
                                    on_click=lambda ev, item=m: sel_s(item)
                                )
                            )
                        riga_sugg.visible = len(matches) > 0
                    else:
                        riga_sugg.visible = False
                    page.update()

                txt_nuovo_i.on_change = filtra_s

                def add_manuale(e):
                    n_val = (txt_nuovo_i.value or "").strip()
                    if not n_val:
                        return
                    q_v = parse_qta(box_nuova_q.textfield.value)
                    u_v = sel_nuova_u.get_value()
                    banner_det.visible = True
                    lbl_det_status.value = "Aggiunta ingrediente..."
                    page.update()

                    def w_add():
                        id_i = ottieni_o_crea_ing(n_val, u_v)
                        supabase.table("ricette_ingredienti").insert({
                            "id_ricetta": id_ricetta,
                            "id_ingrediente": id_i,
                            "quantita": q_v,
                            "unita_misura": u_v
                        }).execute()
                        modificato[0] = True
                        ricarica_ingredienti_ricetta()

                    threading.Thread(target=w_add, daemon=True).start()

                col_corpo.controls.extend([
                    txt_nuovo_i,
                    riga_sugg,
                    ft.Row([
                        box_nuova_q,
                        sel_nuova_u,
                        ft.Container(
                            content=ft.Text("+ Ingrediente", color=ft.Colors.WHITE, size=11, weight=ft.FontWeight.BOLD),
                            bgcolor=ft.Colors.GREEN_700, height=34, padding=ft.Padding(10, 0, 10, 0),
                            alignment=ft.Alignment(0, 0), border_radius=5, on_click=add_manuale
                        )
                    ], spacing=5, vertical_alignment=ft.CrossAxisAlignment.CENTER)
                ])

                banner_det.visible = False
                page.update()
            except Exception as ex:
                print("Errore dettaglio ricetta:", ex)
                banner_det.visible = False
                page.update()

        threading.Thread(target=worker, daemon=True).start()

    ricarica_ingredienti_ricetta()


# ==========================================================
# POPUP INSERIMENTO RAPIDO PIATTO UNICO (PRANZO / CENA)
# ==========================================================
def apri_popup_aggiungi_piatto_semplificato(page: ft.Page, data_target: datetime, categoria: str, subcategoria: str, on_done_callback):
    if not supabase:
        return

    data_str = data_target.strftime("%Y-%m-%d")
    _, lista_ricette = assicura_cache_anagrafica(forza_ricarica=False)

    txt_piatto = ft.TextField(
        label=f"Scrivi o cerca piatto unico ({categoria})",
        hint_text="Anche se non è nel DB...",
        dense=True, text_size=13, autofocus=True
    )
    riga_sugg = ft.Row(wrap=True, spacing=4, run_spacing=4, visible=False)
    spinner_add = ft.ProgressRing(width=18, height=18, stroke_width=2, color=ft.Colors.BLUE_900, visible=False)
    dlg_add = None

    def salva_piatto_scelto(nome_scelto, id_ric_esistente=None):
        nome_pulito = (nome_scelto or "").strip().capitalize()
        if not nome_pulito:
            return
        spinner_add.visible = True
        page.update()

        def worker():
            try:
                id_ric = id_ric_esistente
                if not id_ric:
                    match_db = next((r for r in lista_ricette if r["nome"].lower() == nome_pulito.lower()), None)
                    if match_db:
                        id_ric = match_db["id"]
                    else:
                        res_new = supabase.table("ricette").insert({
                            "nome": nome_pulito,
                            "portata_default": subcategoria
                        }).execute()
                        nuova_r = res_new.data[0]
                        lista_ricette.append(nuova_r)
                        id_ric = nuova_r["id"]
                        if CACHE_ANAGRAFICA["conteggio_ing_ricette"] is not None:
                            CACHE_ANAGRAFICA["conteggio_ing_ricette"][id_ric] = 0

                supabase.table("menu_giornalieri").insert({
                    "data_menu": data_str,
                    "categoria": categoria,
                    "subcategoria": subcategoria,
                    "id_ricetta": id_ric
                }).execute()

                chiudi_dialog_sicuro(page, dlg_add)
                if on_done_callback:
                    on_done_callback()
            except Exception as ex:
                print("Errore inserimento piatto unico:", ex)
                spinner_add.visible = False
                page.update()

        threading.Thread(target=worker, daemon=True).start()

    def filtra_p(e):
        t = (txt_piatto.value or "").strip().lower()
        riga_sugg.controls.clear()
        if t:
            matches = [r for r in lista_ricette if t in r["nome"].lower()][:8]
            for m in matches:
                ha_ing = ricetta_ha_ingredienti(m["id"])
                riga_sugg.controls.append(
                    ft.Container(
                        content=ft.Text(
                            f"{'🍲' if ha_ing else '⚠️'} {m['nome']}",
                            size=11,
                            color=ft.Colors.WHITE if ha_ing else ft.Colors.BLACK,
                            weight=ft.FontWeight.BOLD
                        ),
                        bgcolor=ft.Colors.BLUE_900 if ha_ing else "#FFF59D",
                        padding=ft.Padding(8, 4, 8, 4),
                        border_radius=10,
                        on_click=lambda ev, r_obj=m: salva_piatto_scelto(r_obj["nome"], r_obj["id"])
                    )
                )
            riga_sugg.visible = len(matches) > 0
        else:
            riga_sugg.visible = False
        page.update()

    txt_piatto.on_change = filtra_p
    txt_piatto.on_submit = lambda e: salva_piatto_scelto(txt_piatto.value)

    dlg_add = ft.AlertDialog(
        title=ft.Row([
            ft.Text(f"🍽️ {categoria} ({data_target.strftime('%d/%m')})", size=15, weight=ft.FontWeight.BOLD, expand=True),
            spinner_add
        ]),
        content=ft.Container(
            content=ft.Column([
                ft.Text("Seleziona dal ricettario o scrivi liberamente un nuovo piatto (verrà evidenziato in giallo/rosso se da compilare):", size=11, color=ft.Colors.GREY_700),
                txt_piatto,
                riga_sugg
            ], tight=True, spacing=8),
            width=320
        ),
        actions=[
            ft.TextButton("Annulla", on_click=lambda e: chiudi_dialog_sicuro(page, dlg_add)),
            ft.Container(
                content=ft.Text("+ Inserisci Piatto", color=ft.Colors.WHITE, size=12, weight=ft.FontWeight.BOLD),
                bgcolor=ft.Colors.BLUE_900, padding=8, border_radius=6,
                on_click=lambda e: salva_piatto_scelto(txt_piatto.value)
            )
        ]
    )
    apri_dialog_sicuro(page, dlg_add)


# ==========================================================
# POPUP INSERIMENTO RAPIDO VOCE (VARIE / DISPENSA / DETERSIVI)
# ==========================================================
def apri_popup_aggiungi_voce_diretta(page: ft.Page, data_str: str, categoria: str, titolo_box: str, on_done_callback):
    if not supabase:
        return

    lista_ingredienti, _ = assicura_cache_anagrafica(forza_ricarica=False)
    txt_voce = ft.TextField(label=f"Aggiungi a {titolo_box}...", dense=True, text_size=12, autofocus=True)
    box_qta = crea_box_quantita(valore_iniziale="1", height=34, text_size=12, mostra_prefisso=True, expand=True)
    sel_uni = crea_selettore_unita(page, valore_iniziale="pz", width=74, height=34, text_size=12)
    riga_sugg = ft.Row(wrap=True, spacing=4, run_spacing=4, visible=False)
    spinner_v = ft.ProgressRing(width=18, height=18, stroke_width=2, color=ft.Colors.BLUE_900, visible=False)
    dlg_v = None

    def sel_sugg(ing_s):
        txt_voce.value = ing_s["nome"]
        if ing_s.get("unita_default") in UNITA_MISURA:
            sel_uni.set_value(ing_s["unita_default"])
        riga_sugg.visible = False
        page.update()

    def filtra_v(e):
        t = (txt_voce.value or "").strip().lower()
        riga_sugg.controls.clear()
        if t:
            matches = [i for i in lista_ingredienti if t in i["nome"].lower()][:8]
            for m in matches:
                riga_sugg.controls.append(
                    ft.Container(
                        content=ft.Text(f"🔍 {m['nome']}", size=11, color=ft.Colors.BLUE_900, weight=ft.FontWeight.BOLD),
                        bgcolor=ft.Colors.BLUE_50, padding=ft.Padding(8, 4, 8, 4), border_radius=10,
                        on_click=lambda ev, item=m: sel_sugg(item)
                    )
                )
            riga_sugg.visible = len(matches) > 0
        else:
            riga_sugg.visible = False
        page.update()

    txt_voce.on_change = filtra_v

    def salva_voce(e=None):
        nome_v = (txt_voce.value or "").strip().capitalize()
        if not nome_v:
            return
        q_val = parse_qta(box_qta.textfield.value)
        u_val = sel_uni.get_value()
        spinner_v.visible = True
        page.update()

        def worker():
            try:
                esistente = next((i for i in lista_ingredienti if i["nome"].lower() == nome_v.lower()), None)
                if esistente:
                    id_i = esistente["id"]
                else:
                    res_i = supabase.table("ingredienti").insert({"nome": nome_v, "unita_default": u_val}).execute()
                    nuovo_i = res_i.data[0]
                    lista_ingredienti.append(nuovo_i)
                    id_i = nuovo_i["id"]

                record = {
                    "data_menu": data_str,
                    "categoria": categoria,
                    "subcategoria": None,
                    "id_ingrediente": id_i,
                    "quantita": q_val,
                    "unita_misura": u_val
                }
                supabase.table("menu_giornalieri").insert(record).execute()
                chiudi_dialog_sicuro(page, dlg_v)
                if on_done_callback:
                    on_done_callback()
            except Exception as ex:
                print("Errore salvataggio voce:", ex)
                spinner_v.visible = False
                page.update()

        threading.Thread(target=worker, daemon=True).start()

    dlg_v = ft.AlertDialog(
        title=ft.Row([
            ft.Text(f"➕ {titolo_box}", size=15, weight=ft.FontWeight.BOLD, expand=True),
            spinner_v
        ]),
        content=ft.Container(
            content=ft.Column([
                txt_voce,
                riga_sugg,
                ft.Row([box_qta, sel_uni], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER)
            ], tight=True, spacing=8),
            width=320
        ),
        actions=[
            ft.TextButton("Annulla", on_click=lambda e: chiudi_dialog_sicuro(page, dlg_v)),
            ft.Container(
                content=ft.Text("+ Aggiungi", color=ft.Colors.WHITE, size=12, weight=ft.FontWeight.BOLD),
                bgcolor=ft.Colors.BLUE_900, padding=8, border_radius=6, on_click=salva_voce
            )
        ]
    )
    apri_dialog_sicuro(page, dlg_v)


# ==========================================================
# FORM MENU DEL GIORNO COMPLETO (VELOCIZZATO + ALTEZZE GEMELLE)
# ==========================================================
def apri_form_menu_giorno(page: ft.Page, data_selezionata: datetime, on_success_callback=None):
    if not supabase:
        return

    data_str = data_selezionata.strftime("%Y-%m-%d")
    data_vis = data_selezionata.strftime("%d/%m/%Y")

    lista_ingredienti = []
    lista_ricette = []
    menu_del_giorno = []
    mappa_ricette_ing = {}
    modifiche_effettuate = [False]

    cat_aperte = set()
    sub_aperte = set()

    spinner_header = ft.ProgressRing(width=18, height=18, stroke_width=2.5, color=ft.Colors.WHITE)
    lbl_stato_caricamento = ft.Text("Lettura dati in corso...", size=11, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD)
    banner_loading = ft.Container(
        content=ft.Row([spinner_header, lbl_stato_caricamento], alignment=ft.MainAxisAlignment.CENTER, spacing=8),
        bgcolor=ft.Colors.ORANGE_800,
        padding=ft.Padding(10, 6, 10, 6),
        border_radius=6,
        visible=True
    )

    colonna_contenuto = ft.Column(
        controls=[
            ft.Container(
                content=ft.Column([
                    ft.ProgressRing(width=36, height=36, stroke_width=3, color=ft.Colors.BLUE_900),
                    ft.Text("Caricamento menu in corso...", size=12, color=ft.Colors.GREY_700, weight=ft.FontWeight.BOLD)
                ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=12),
                alignment=ft.Alignment(0, 0), padding=40
            )
        ],
        spacing=6, scroll=ft.ScrollMode.AUTO, expand=True
    )

    dlg_menu = None

    def chiudi_dlg(e=None):
        chiudi_dialog_sicuro(page, dlg_menu)
        if modifiche_effettuate[0]:
            if on_success_callback:
                on_success_callback()
            page.pubsub.send_all("aggiorna_menu")

    dlg_menu = ft.AlertDialog(
        title=ft.Column([
            ft.Row([
                ft.Text(f"🍽️ Menu {data_vis}", size=15, weight=ft.FontWeight.BOLD, expand=True),
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            banner_loading
        ], spacing=6, tight=True),
        content=ft.Container(content=colonna_contenuto, width=350, height=480),
        actions=[
            ft.Container(
                content=ft.Text("Chiudi", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=12),
                bgcolor=ft.Colors.BLUE_900, padding=ft.Padding(14, 8, 14, 8), border_radius=6, on_click=chiudi_dlg
            )
        ],
        shape=ft.RoundedRectangleBorder(radius=10),
        content_padding=8,
        inset_padding=ft.Padding(10, 15, 10, 15)
    )

    apri_dialog_sicuro(page, dlg_menu)
    page.update()

    def mostra_loading(testo="Operazione in corso..."):
        lbl_stato_caricamento.value = testo
        banner_loading.visible = True
        try:
            page.update()
        except Exception:
            pass

    def nascondi_loading():
        banner_loading.visible = False
        try:
            page.update()
        except Exception:
            pass

    def carica_dati_giorno_db(inizializza_aperture=False):
        nonlocal lista_ingredienti, lista_ricette, menu_del_giorno, mappa_ricette_ing
        try:
            lista_ingredienti, lista_ricette = assicura_cache_anagrafica(forza_ricarica=False)

            res_menu = supabase.table("menu_giornalieri").select(
                "*, ricette(id, nome, portata_default), ingredienti(id, nome, unita_default)"
            ).eq("data_menu", data_str).execute()
            menu_del_giorno = res_menu.data if res_menu.data else []

            ids_ricette_giorno = list({m["id_ricetta"] for m in menu_del_giorno if m.get("id_ricetta")})
            mappa_ricette_ing.clear()
            if ids_ricette_giorno:
                res_ri = supabase.table("ricette_ingredienti").select(
                    "*, ingredienti(id, nome, unita_default)"
                ).in_("id_ricetta", ids_ricette_giorno).execute()
                if res_ri.data:
                    for riga in res_ri.data:
                        rid = riga["id_ricetta"]
                        if rid not in mappa_ricette_ing:
                            mappa_ricette_ing[rid] = []
                        mappa_ricette_ing[rid].append(riga)
                if CACHE_ANAGRAFICA["conteggio_ing_ricette"] is not None:
                    for rid in ids_ricette_giorno:
                        CACHE_ANAGRAFICA["conteggio_ing_ricette"][rid] = len(mappa_ricette_ing.get(rid, []))

            if inizializza_aperture:
                for m in menu_del_giorno:
                    cat_aperte.add(m["categoria"])
                    if m.get("subcategoria"):
                        sub_aperte.add((m["categoria"], m["subcategoria"]))
        except Exception as ex:
            print("Errore caricamento dati menu:", ex)

    def esegui_azione_db(testo_loading, funzione_db, ricarica_giorno=True):
        mostra_loading(testo_loading)

        def worker():
            try:
                funzione_db()
                modifiche_effettuate[0] = True
                if ricarica_giorno:
                    carica_dati_giorno_db(inizializza_aperture=False)
                disegna_form_da_memoria()
            except Exception as ex:
                print("Errore operazione DB menu:", ex)
            finally:
                nascondi_loading()

        threading.Thread(target=worker, daemon=True).start()

    def ottieni_o_crea_ingrediente(nome_raw, unita_scelta):
        nome_pulito = nome_raw.strip().capitalize()
        esistente = next((i for i in lista_ingredienti if i["nome"].lower() == nome_pulito.lower()), None)
        if esistente:
            return esistente["id"]
        res = supabase.table("ingredienti").insert({
            "nome": nome_pulito,
            "unita_default": unita_scelta or "g"
        }).execute()
        nuovo = res.data[0]
        lista_ingredienti.append(nuovo)
        return nuovo["id"]

    def ottieni_o_crea_ricetta(nome_raw, portata):
        nome_pulito = nome_raw.strip().capitalize()
        esistente = next((r for r in lista_ricette if r["nome"].lower() == nome_pulito.lower()), None)
        if esistente:
            return esistente["id"]
        res = supabase.table("ricette").insert({
            "nome": nome_pulito,
            "portata_default": portata
        }).execute()
        nuova = res.data[0]
        lista_ricette.append(nuova)
        return nuova["id"]

    def apri_dialog_ripeti_mese(categoria):
        voci_correnti = [
            m for m in menu_del_giorno
            if m["categoria"] == categoria and not m.get("subcategoria")
        ]

        stato_ambito = {"val": "Tutto il mese"}
        btn_tutto = ft.Container(
            content=ft.Text("Tutto il mese", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
            bgcolor=ft.Colors.BLUE_900, padding=8, border_radius=6, expand=1, alignment=ft.Alignment(0, 0)
        )
        btn_da_oggi = ft.Container(
            content=ft.Text("Da oggi a fine mese", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900),
            bgcolor=ft.Colors.BLUE_50, padding=8, border_radius=6, expand=1, alignment=ft.Alignment(0, 0)
        )

        def sel_ambito(val_scelto):
            stato_ambito["val"] = val_scelto
            is_tutto = (val_scelto == "Tutto il mese")
            btn_tutto.bgcolor = ft.Colors.BLUE_900 if is_tutto else ft.Colors.BLUE_50
            btn_tutto.content.color = ft.Colors.WHITE if is_tutto else ft.Colors.BLUE_900
            btn_da_oggi.bgcolor = ft.Colors.BLUE_900 if not is_tutto else ft.Colors.BLUE_50
            btn_da_oggi.content.color = ft.Colors.WHITE if not is_tutto else ft.Colors.BLUE_900
            page.update()

        btn_tutto.on_click = lambda e: sel_ambito("Tutto il mese")
        btn_da_oggi.on_click = lambda e: sel_ambito("Da questo giorno a fine mese")

        dlg_rip = None

        def esegui_ripetizione(e):
            chiudi_dialog_sicuro(page, dlg_rip)

            def op_ripeti():
                anno = data_selezionata.year
                mese = data_selezionata.month
                ultimo_g = calendar.monthrange(anno, mese)[1]
                giorno_start = 1 if stato_ambito["val"] == "Tutto il mese" else data_selezionata.day

                primo_str = f"{anno:04d}-{mese:02d}-{giorno_start:02d}"
                ultimo_str = f"{anno:04d}-{mese:02d}-{ultimo_g:02d}"

                supabase.table("menu_giornalieri").delete().eq("categoria", categoria).is_("subcategoria", "null").gte("data_menu", primo_str).lte("data_menu", ultimo_str).neq("data_menu", data_str).execute()

                if voci_correnti:
                    nuovi_records = []
                    for g in range(giorno_start, ultimo_g + 1):
                        d_target = f"{anno:04d}-{mese:02d}-{g:02d}"
                        if d_target == data_str:
                            continue
                        for vc in voci_correnti:
                            nuovi_records.append({
                                "data_menu": d_target,
                                "categoria": categoria,
                                "subcategoria": None,
                                "id_ricetta": vc.get("id_ricetta"),
                                "id_ingrediente": vc.get("id_ingrediente"),
                                "quantita": vc.get("quantita") or 0,
                                "unita_misura": vc.get("unita_misura") or "g"
                            })
                    if nuovi_records:
                        supabase.table("menu_giornalieri").insert(nuovi_records).execute()

            esegui_azione_db(f"⏳ Copia di {categoria} nel mese...", op_ripeti, ricarica_giorno=False)

        testo_info = (
            f"Vuoi replicare le {len(voci_correnti)} voci di '{categoria}' sugli altri giorni di {MESI[data_selezionata.month]}?"
            if voci_correnti else
            f"'{categoria}' oggi è vuota: confermando svuoterai '{categoria}' anche negli altri giorni del mese."
        )

        dlg_rip = ft.AlertDialog(
            title=ft.Text(f"🔁 Ripeti {categoria}", size=15, weight=ft.FontWeight.BOLD),
            content=ft.Container(
                content=ft.Column([
                    ft.Text(testo_info, size=12),
                    ft.Row([btn_tutto, btn_da_oggi], spacing=6)
                ], tight=True, spacing=10),
                width=310
            ),
            actions=[
                ft.TextButton("Annulla", on_click=lambda e: chiudi_dialog_sicuro(page, dlg_rip)),
                ft.Container(
                    content=ft.Text("Applica al Mese", color=ft.Colors.WHITE, size=12, weight=ft.FontWeight.BOLD),
                    bgcolor=ft.Colors.BLUE_900, padding=8, border_radius=5, on_click=esegui_ripetizione
                )
            ]
        )
        apri_dialog_sicuro(page, dlg_rip)

    def crea_sezione_ingredienti_diretti(categoria, subcategoria=None):
        voci = [
            m for m in menu_del_giorno
            if m["categoria"] == categoria and m.get("subcategoria") == subcategoria and m.get("id_ingrediente")
        ]

        lista_ui = ft.Column(spacing=4)
        for v in voci:
            ing_obj = v.get("ingredienti") or {"id": v["id_ingrediente"], "nome": "Ingrediente", "unita_default": "g"}
            nome_ing = ing_obj.get("nome", "Ingrediente")
            qta = v.get("quantita") or 0
            qta_str = f"{formatta_numero(qta)} {v.get('unita_misura') or ''}".strip()

            def elimina_voce(e, id_voce=v["id"]):
                esegui_azione_db(
                    "⏳ Rimozione ingrediente...",
                    lambda: supabase.table("menu_giornalieri").delete().eq("id", id_voce).execute()
                )

            def rinomina_voce(e, item_ing=ing_obj):
                apri_popup_rinomina_ingrediente(
                    page, item_ing, lista_ingredienti,
                    lambda: esegui_azione_db("⏳ Aggiornamento...", lambda: None)
                )

            lista_ui.controls.append(
                ft.Container(
                    content=ft.Row([
                        ft.Text(f"• {nome_ing}", size=12, weight=ft.FontWeight.BOLD, expand=True),
                        ft.Text(qta_str, size=11, color=ft.Colors.BLUE_900, weight=ft.FontWeight.BOLD),
                        ft.IconButton(icon=ft.Icons.EDIT, icon_color=ft.Colors.ORANGE_800, icon_size=15, tooltip="Rinomina ingrediente", on_click=rinomina_voce),
                        ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_color=ft.Colors.RED_700, icon_size=16, tooltip="Rimuovi dal giorno", on_click=elimina_voce)
                    ], spacing=2),
                    bgcolor="#F5F5F5", padding=ft.Padding(6, 2, 2, 2), border_radius=5
                )
            )

        txt_ing = ft.TextField(label="Cerca o scrivi ingrediente...", dense=True, text_size=12, expand=True)
        box_qta = crea_box_quantita(valore_iniziale="1", height=34, text_size=12, mostra_prefisso=True, expand=True)
        sel_unita = crea_selettore_unita(page, valore_iniziale="pz", width=74, height=34, text_size=12)
        riga_suggerimenti = ft.Row(wrap=True, spacing=4, run_spacing=4, visible=False)

        def seleziona_suggerimento_ing(ing_scelto):
            txt_ing.value = ing_scelto["nome"]
            if ing_scelto.get("unita_default") in UNITA_MISURA:
                sel_unita.set_value(ing_scelto["unita_default"])
            riga_suggerimenti.visible = False
            page.update()

        def filtra_ing(e):
            testo = (txt_ing.value or "").strip().lower()
            riga_suggerimenti.controls.clear()
            if len(testo) >= 1:
                matches = [i for i in lista_ingredienti if testo in i["nome"].lower()][:8]
                for m_ing in matches:
                    riga_suggerimenti.controls.append(
                        ft.Container(
                            content=ft.Row([
                                ft.Text(f"🔍 {m_ing['nome']}", size=11, color=ft.Colors.BLUE_900, weight=ft.FontWeight.BOLD),
                            ], tight=True, spacing=2),
                            bgcolor=ft.Colors.BLUE_50, padding=ft.Padding(8, 4, 8, 4), border_radius=10,
                            on_click=lambda ev, item=m_ing: seleziona_suggerimento_ing(item)
                        )
                    )
                riga_suggerimenti.visible = len(matches) > 0
            else:
                riga_suggerimenti.visible = False
            page.update()

        txt_ing.on_change = filtra_ing

        def aggiungi_ing_diretto(e):
            nome_val = (txt_ing.value or "").strip()
            if not nome_val:
                return
            qta_val = parse_qta(box_qta.textfield.value)
            uni_val = sel_unita.get_value()

            def op_add():
                id_ing = ottieni_o_crea_ingrediente(nome_val, uni_val)
                supabase.table("menu_giornalieri").insert({
                    "data_menu": data_str,
                    "categoria": categoria,
                    "subcategoria": subcategoria,
                    "id_ingrediente": id_ing,
                    "quantita": qta_val,
                    "unita_misura": uni_val
                }).execute()

            esegui_azione_db("⏳ Salvataggio ingrediente...", op_add)

        btn_add = ft.Container(
            content=ft.Text("+ Aggiungi", color=ft.Colors.WHITE, size=11, weight=ft.FontWeight.BOLD),
            bgcolor=ft.Colors.BLUE_900,
            height=34,
            padding=ft.Padding(12, 0, 12, 0),
            alignment=ft.Alignment(0, 0),
            border_radius=5,
            on_click=aggiungi_ing_diretto
        )

        return ft.Column([
            lista_ui,
            txt_ing,
            riga_suggerimenti,
            ft.Row([box_qta, sel_unita, btn_add], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        ], spacing=6)

    def crea_sezione_ricetta(categoria, subcategoria):
        voci_ricetta = [
            m for m in menu_del_giorno
            if m["categoria"] == categoria and m.get("subcategoria") == subcategoria and m.get("id_ricetta")
        ]

        blocchi_ricette_ui = ft.Column(spacing=8)

        for v in voci_ricetta:
            ric_obj = v.get("ricette") or {}
            id_ric = v["id_ricetta"]
            nome_ric = ric_obj.get("nome", "Ricetta")
            portata_ric = ric_obj.get("portata_default") or subcategoria
            ingredienti_associati = mappa_ricette_ing.get(id_ric, [])

            col_ing_ricetta = ft.Column(spacing=6)
            for ri in ingredienti_associati:
                ing_info = ri.get("ingredienti") or {"id": ri["id_ingrediente"], "nome": "Ingrediente", "unita_default": "g"}
                nome_i = ing_info.get("nome", "Ingrediente")
                qta_val = ri.get("quantita") or 0
                qta_init = formatta_numero(qta_val)
                unita_init = ri.get("unita_misura") or "g"

                box_qta_ri = crea_box_quantita(valore_iniziale=qta_init, width=88, height=32, text_size=11, mostra_prefisso=True, expand=False)
                sel_uni_ri = crea_selettore_unita(page, valore_iniziale=unita_init, width=72, height=32, text_size=11)

                def aggiorna_qta_ri(e, id_riga_ri=ri["id"], b_q=box_qta_ri, s_u=sel_uni_ri):
                    q_num = parse_qta(b_q.textfield.value)
                    u_str = s_u.get_value()
                    esegui_azione_db(
                        "⏳ Salvataggio quantità...",
                        lambda: supabase.table("ricette_ingredienti").update({
                            "quantita": q_num,
                            "unita_misura": u_str
                        }).eq("id", id_riga_ri).execute()
                    )

                def elimina_ing_da_ricetta(e, id_riga_ri=ri["id"]):
                    esegui_azione_db(
                        "⏳ Rimozione ingrediente...",
                        lambda: supabase.table("ricette_ingredienti").delete().eq("id", id_riga_ri).execute()
                    )

                def rinomina_ing_ricetta(e, item_ing=ing_info):
                    apri_popup_rinomina_ingrediente(
                        page, item_ing, lista_ingredienti,
                        lambda: esegui_azione_db("⏳ Aggiornamento...", lambda: None)
                    )

                col_ing_ricetta.controls.append(
                    ft.Container(
                        content=ft.Column([
                            ft.Row([
                                ft.Text(f"▪ {nome_i}", size=12, weight=ft.FontWeight.BOLD, expand=True),
                                ft.IconButton(icon=ft.Icons.EDIT, icon_color=ft.Colors.ORANGE_800, icon_size=14, tooltip="Rinomina ingrediente", on_click=rinomina_ing_ricetta),
                                ft.IconButton(icon=ft.Icons.CLOSE, icon_color=ft.Colors.RED_700, icon_size=15, tooltip="Togli dalla ricetta", on_click=elimina_ing_da_ricetta)
                            ], spacing=2),
                            ft.Row([
                                box_qta_ri,
                                sel_uni_ri,
                                ft.Container(
                                    content=ft.Text("💾 Salva Q.tà", size=10, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                                    bgcolor=ft.Colors.GREEN_700,
                                    height=32,
                                    padding=ft.Padding(10, 0, 10, 0),
                                    alignment=ft.Alignment(0, 0),
                                    border_radius=5,
                                    on_click=aggiorna_qta_ri
                                )
                            ], spacing=5, vertical_alignment=ft.CrossAxisAlignment.CENTER)
                        ], spacing=3),
                        bgcolor=ft.Colors.WHITE, padding=5, border_radius=5
                    )
                )

            txt_nuovo_ing_r = ft.TextField(label="Aggiungi ingrediente alla ricetta...", dense=True, text_size=11, expand=True)
            box_nuova_qta_r = crea_box_quantita(valore_iniziale="100", height=34, text_size=11, mostra_prefisso=True, expand=True)
            sel_nuova_uni_r = crea_selettore_unita(page, valore_iniziale="g", width=72, height=34, text_size=11)
            riga_sugg_ing_r = ft.Row(wrap=True, spacing=4, run_spacing=4, visible=False)

            def sel_sugg_r(ing_scelto, tf_i=txt_nuovo_ing_r, s_u=sel_nuova_uni_r, r_sugg=riga_sugg_ing_r):
                tf_i.value = ing_scelto["nome"]
                if ing_scelto.get("unita_default") in UNITA_MISURA:
                    s_u.set_value(ing_scelto["unita_default"])
                r_sugg.visible = False
                page.update()

            def filtra_ing_r(e, tf_i=txt_nuovo_ing_r, s_u=sel_nuova_uni_r, r_sugg=riga_sugg_ing_r):
                testo = (tf_i.value or "").strip().lower()
                r_sugg.controls.clear()
                if len(testo) >= 1:
                    matches = [i for i in lista_ingredienti if testo in i["nome"].lower()][:8]
                    for m_ing in matches:
                        r_sugg.controls.append(
                            ft.Container(
                                content=ft.Text(f"🔍 {m_ing['nome']}", size=10, color=ft.Colors.BLUE_900, weight=ft.FontWeight.BOLD),
                                bgcolor=ft.Colors.BLUE_50, padding=ft.Padding(6, 3, 6, 3), border_radius=8,
                                on_click=lambda ev, item=m_ing: sel_sugg_r(item, tf_i, s_u, r_sugg)
                            )
                        )
                    r_sugg.visible = len(matches) > 0
                else:
                    r_sugg.visible = False
                page.update()

            txt_nuovo_ing_r.on_change = filtra_ing_r

            def salva_ing_in_ricetta(e, id_ricetta_corr=id_ric, tf_i=txt_nuovo_ing_r, b_q=box_nuova_qta_r, s_u=sel_nuova_uni_r):
                nome_val = (tf_i.value or "").strip()
                if not nome_val:
                    return
                q_val = parse_qta(b_q.textfield.value)
                u_val = s_u.get_value()

                def op_salva_ing_ric():
                    id_ing = ottieni_o_crea_ingrediente(nome_val, u_val)
                    gia_presente = next((x for x in mappa_ricette_ing.get(id_ricetta_corr, []) if x["id_ingrediente"] == id_ing), None)
                    if gia_presente:
                        supabase.table("ricette_ingredienti").update({
                            "quantita": q_val,
                            "unita_misura": u_val
                        }).eq("id", gia_presente["id"]).execute()
                    else:
                        supabase.table("ricette_ingredienti").insert({
                            "id_ricetta": id_ricetta_corr,
                            "id_ingrediente": id_ing,
                            "quantita": q_val,
                            "unita_misura": u_val
                        }).execute()

                esegui_azione_db("⏳ Aggiunta ingrediente alla ricetta...", op_salva_ing_ric)

            def rimuovi_ricetta_dal_giorno(e, id_voce_menu=v["id"]):
                esegui_azione_db(
                    "⏳ Rimozione ricetta dal giorno...",
                    lambda: supabase.table("menu_giornalieri").delete().eq("id", id_voce_menu).execute()
                )

            def gestisci_ricetta_db(e, r_id=id_ric, r_nome=nome_ric, r_port=portata_ric):
                apri_popup_gestione_ricetta(
                    page, r_id, r_nome, r_port,
                    lambda: esegui_azione_db("⏳ Aggiornamento ricetta...", lambda: None)
                )

            ha_ing_ric = len(ingredienti_associati) > 0
            header_ricetta_extra = []
            if not ha_ing_ric:
                header_ricetta_extra.append(
                    ft.Container(
                        content=ft.Text("🌐 Compila da Web", size=10, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                        bgcolor=ft.Colors.ORANGE_800, padding=ft.Padding(6, 3, 6, 3), border_radius=6,
                        on_click=lambda e, v_id=v["id"], r_id=id_ric, r_n=nome_ric, r_p=portata_ric: apri_popup_dettaglio_ricetta_semplificata(
                            page, v_id, r_id, r_n, r_p, lambda: esegui_azione_db("⏳ Aggiornamento...", lambda: None)
                        )
                    )
                )

            blocchi_ricette_ui.controls.append(
                ft.Container(
                    content=ft.Column([
                        ft.Row([
                            ft.Text(f"{'🍲' if ha_ing_ric else '⚠️'} {nome_ric}", size=13, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900 if ha_ing_ric else ft.Colors.RED_700, expand=True),
                            *header_ricetta_extra,
                            ft.IconButton(icon=ft.Icons.EDIT, icon_color=ft.Colors.ORANGE_800, icon_size=16, tooltip="Rinomina o Elimina Ricetta dal DB", on_click=gestisci_ricetta_db),
                            ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_color=ft.Colors.RED_700, icon_size=17, tooltip="Togli ricetta da questo giorno", on_click=rimuovi_ricetta_dal_giorno)
                        ], spacing=2),
                        ft.Text("Ingredienti associati:", size=10, color=ft.Colors.GREY_700, italic=True),
                        col_ing_ricetta,
                        ft.Divider(height=4, color="#BBDEFB"),
                        txt_nuovo_ing_r,
                        riga_sugg_ing_r,
                        ft.Row([
                            box_nuova_qta_r,
                            sel_nuova_uni_r,
                            ft.Container(
                                content=ft.Text("+ Ingrediente", color=ft.Colors.WHITE, size=11, weight=ft.FontWeight.BOLD),
                                bgcolor=ft.Colors.GREEN_700,
                                height=34,
                                padding=ft.Padding(10, 0, 10, 0),
                                alignment=ft.Alignment(0, 0),
                                border_radius=5,
                                on_click=salva_ing_in_ricetta
                            )
                        ], spacing=5, vertical_alignment=ft.CrossAxisAlignment.CENTER)
                    ], spacing=5),
                    bgcolor="#F0F4F8" if ha_ing_ric else "#FFF8E1", padding=6, border_radius=8,
                    border=ft.Border(
                        top=ft.BorderSide(1, "#BBDEFB" if ha_ing_ric else "#E53935"),
                        bottom=ft.BorderSide(1, "#BBDEFB" if ha_ing_ric else "#E53935"),
                        left=ft.BorderSide(4, ft.Colors.BLUE_900 if ha_ing_ric else ft.Colors.RED_700),
                        right=ft.BorderSide(1, "#BBDEFB" if ha_ing_ric else "#E53935")
                    )
                )
            )

        txt_ricetta = ft.TextField(label=f"Cerca tra le 100+ ricette o scrivi nuova ({subcategoria})", dense=True, text_size=12, expand=True)
        riga_sugg_ricette = ft.Row(wrap=True, spacing=4, run_spacing=4, visible=False)

        def assegna_ricetta_al_menu(id_ric_scelta):
            esegui_azione_db(
                "⏳ Inserimento ricetta nel menu...",
                lambda: supabase.table("menu_giornalieri").insert({
                    "data_menu": data_str,
                    "categoria": categoria,
                    "subcategoria": subcategoria,
                    "id_ricetta": id_ric_scelta
                }).execute()
            )

        def filtra_ricette(e):
            testo = (txt_ricetta.value or "").strip().lower()
            riga_sugg_ricette.controls.clear()
            if len(testo) >= 1:
                matches = [r for r in lista_ricette if testo in r["nome"].lower()][:8]
                for m_ric in matches:
                    riga_sugg_ricette.controls.append(
                        ft.Container(
                            content=ft.Text(f"🍲 {m_ric['nome']}", size=11, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                            bgcolor=ft.Colors.BLUE_900, padding=ft.Padding(8, 5, 8, 5), border_radius=10,
                            on_click=lambda ev, r_id=m_ric["id"]: assegna_ricetta_al_menu(r_id)
                        )
                    )
                riga_sugg_ricette.visible = len(matches) > 0
            else:
                riga_sugg_ricette.visible = False
            page.update()

        txt_ricetta.on_change = filtra_ricette

        def crea_o_usa_ricetta_click(e):
            nome_r = (txt_ricetta.value or "").strip()
            if not nome_r:
                return

            def op_crea_ric():
                id_ric_nuova = ottieni_o_crea_ricetta(nome_r, subcategoria)
                supabase.table("menu_giornalieri").insert({
                    "data_menu": data_str,
                    "categoria": categoria,
                    "subcategoria": subcategoria,
                    "id_ricetta": id_ric_nuova
                }).execute()

            esegui_azione_db("⏳ Creazione e aggiunta ricetta...", op_crea_ric)

        btn_aggiungi_ricetta = ft.Container(
            content=ft.Text("+ Crea/Aggiungi Ricetta", color=ft.Colors.WHITE, size=11, weight=ft.FontWeight.BOLD),
            bgcolor=ft.Colors.BLUE_900, padding=ft.Padding(10, 7, 10, 7), border_radius=5, on_click=crea_o_usa_ricetta_click
        )

        return ft.Column([
            blocchi_ricette_ui,
            txt_ricetta,
            riga_sugg_ricette,
            ft.Row([btn_aggiungi_ricetta], alignment=ft.MainAxisAlignment.END)
        ], spacing=6)

    def disegna_form_da_memoria():
        colonna_contenuto.controls.clear()

        for sup in SUPERCATEGORIE:
            is_sup_open = sup in cat_aperte
            colore_sup = COLORI_PASTI.get(sup, ft.Colors.BLUE_900)

            def toggle_sup(e, nome_sup=sup):
                if e.control.value:
                    cat_aperte.add(nome_sup)
                else:
                    cat_aperte.discard(nome_sup)
                disegna_form_da_memoria()

            chk_sup = ft.Checkbox(label=sup.upper(), value=is_sup_open, on_change=toggle_sup)

            header_sup_controls = [ft.Container(content=chk_sup, expand=True)]
            if sup not in ["Pranzo", "Cena"]:
                btn_ripeti_sup = ft.Container(
                    content=ft.Text("🔁 Ripeti nel mese", size=10, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                    bgcolor=colore_sup, padding=ft.Padding(7, 4, 7, 4), border_radius=12,
                    on_click=lambda e, s_cat=sup: apri_dialog_ripeti_mese(s_cat)
                )
                header_sup_controls.append(btn_ripeti_sup)

            riga_header_sup = ft.Row(header_sup_controls, alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER)
            contenuto_interno_sup = ft.Column(spacing=6, visible=is_sup_open)

            if is_sup_open:
                if sup in ["Pranzo", "Cena"]:
                    for sub in SUBCATEGORIE_PASTO:
                        chiave_sub = (sup, sub)
                        is_sub_open = chiave_sub in sub_aperte

                        def toggle_sub(e, k_sub=chiave_sub):
                            if e.control.value:
                                sub_aperte.add(k_sub)
                            else:
                                sub_aperte.discard(k_sub)
                            disegna_form_da_memoria()

                        chk_sub = ft.Checkbox(label=sub, value=is_sub_open, on_change=toggle_sub)

                        blocco_sub = ft.Column(spacing=4)
                        blocco_sub.controls.append(
                            ft.Row([ft.Container(content=chk_sub, expand=True)], alignment=ft.MainAxisAlignment.START)
                        )

                        if is_sub_open:
                            if sub in SUB_CON_RICETTA:
                                blocco_sub.controls.append(
                                    ft.Container(content=crea_sezione_ricetta(sup, sub), padding=ft.Padding(4, 0, 2, 4))
                                )
                            else:
                                blocco_sub.controls.append(
                                    ft.Container(content=crea_sezione_ingredienti_diretti(sup, sub), padding=ft.Padding(4, 0, 2, 4))
                                )

                        contenuto_interno_sup.controls.append(
                            ft.Container(
                                content=blocco_sub,
                                bgcolor=ft.Colors.WHITE, padding=5, border_radius=6,
                                border=ft.Border(top=ft.BorderSide(1, "#E0E0E0"), bottom=ft.BorderSide(1, "#E0E0E0"), left=ft.BorderSide(3, colore_sup), right=ft.BorderSide(1, "#E0E0E0"))
                            )
                        )
                else:
                    contenuto_interno_sup.controls.append(
                        ft.Container(
                            content=crea_sezione_ingredienti_diretti(sup, None),
                            bgcolor=ft.Colors.WHITE, padding=6, border_radius=6
                        )
                    )

            colonna_contenuto.controls.append(
                ft.Container(
                    content=ft.Column([riga_header_sup, contenuto_interno_sup], spacing=4),
                    bgcolor="#FAFAFA", padding=6, border_radius=8,
                    border=ft.Border(top=ft.BorderSide(1, "#E0E0E0"), bottom=ft.BorderSide(1, "#E0E0E0"), left=ft.BorderSide(5, colore_sup), right=ft.BorderSide(1, "#E0E0E0"))
                )
            )

        page.update()

    def avvio_iniziale():
        carica_dati_giorno_db(inizializza_aperture=True)
        disegna_form_da_memoria()
        nascondi_loading()

    threading.Thread(target=avvio_iniziale, daemon=True).start()


# ==========================================================
# MOTORE INTELLIGENTE "FAI LA SPESA" (MERGE COMPLETA + SEMPLIFICATA + DISPENSA/DETERSIVI)
# ==========================================================
def apri_popup_fai_la_spesa(page: ft.Page, data_riferimento: datetime, on_close_callback=None):
    if not supabase:
        return

    oggi_dt = datetime.now()
    spuntati_sessione = set()
    stato_filtro = {"scelta": "Da oggi a fine mese"}
    modifiche_spunte = [False]

    spinner_spesa = ft.ProgressRing(width=18, height=18, stroke_width=2.5, color=ft.Colors.GREEN_800, visible=True)

    colonna_lista = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)
    txt_periodo_date = ft.Text("", size=11, color=ft.Colors.BLUE_900, weight=ft.FontWeight.BOLD)
    txt_riepilogo = ft.Text("", size=11, color=ft.Colors.GREY_700, weight=ft.FontWeight.BOLD)

    opzioni_filtro = [
        "Da oggi a fine mese",
        "Prossimi 7 giorni",
        "Solo giorno sel.",
        "Tutto il mese"
    ]
    riga_bottoni_filtro = ft.Row(wrap=True, spacing=4, run_spacing=4)

    def cambia_filtro_spesa(nuova_scelta):
        stato_filtro["scelta"] = nuova_scelta
        aggiorna_stile_filtri()
        avvia_calcolo_spesa()

    def aggiorna_stile_filtri():
        riga_bottoni_filtro.controls.clear()
        for opz in opzioni_filtro:
            is_attivo = (stato_filtro["scelta"] == opz)
            riga_bottoni_filtro.controls.append(
                ft.Container(
                    content=ft.Text(opz, size=10, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE if is_attivo else ft.Colors.GREEN_800),
                    bgcolor=ft.Colors.GREEN_700 if is_attivo else "#E8F5E9",
                    padding=ft.Padding(8, 5, 8, 5),
                    border_radius=12,
                    border=bordo_campo_uniforme(ft.Colors.GREEN_700),
                    on_click=lambda e, sc=opz: cambia_filtro_spesa(sc)
                )
            )

    aggiorna_stile_filtri()
    dlg_spesa = None

    def chiudi_spesa(e=None):
        chiudi_dialog_sicuro(page, dlg_spesa)
        if modifiche_spunte[0] and on_close_callback:
            on_close_callback()

    dlg_spesa = ft.AlertDialog(
        title=ft.Row([
            ft.Text("🛒 Lista della Spesa", size=16, weight=ft.FontWeight.BOLD, color=ft.Colors.GREEN_800, expand=True),
            spinner_spesa
        ]),
        content=ft.Container(
            content=ft.Column([
                ft.Text("Filtra periodo (include Dispensa e Detersivi del mese):", size=10, color=ft.Colors.GREY_700, weight=ft.FontWeight.BOLD),
                riga_bottoni_filtro,
                txt_periodo_date,
                txt_riepilogo,
                ft.Divider(height=4, color="#E0E0E0"),
                colonna_lista
            ], spacing=5),
            width=350, height=490
        ),
        actions=[
            ft.Container(
                content=ft.Text("Chiudi", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=12),
                bgcolor=ft.Colors.BLUE_900, padding=ft.Padding(14, 8, 14, 8), border_radius=6,
                on_click=chiudi_spesa
            )
        ],
        shape=ft.RoundedRectangleBorder(radius=10),
        content_padding=10,
        inset_padding=ft.Padding(10, 15, 10, 15)
    )

    apri_dialog_sicuro(page, dlg_spesa)
    page.update()

    def normalizza_qta_unita(qta, unita):
        u = (unita or "pz").lower().strip()
        q = float(qta or 0)
        if u == "kg":
            return q * 1000.0, "g"
        if u == "l":
            return q * 1000.0, "ml"
        return q, u

    def formatta_qta_finale(qta_tot, unita_base):
        if unita_base == "g" and qta_tot >= 1000:
            return f"{formatta_numero(qta_tot / 1000.0)} kg"
        if unita_base == "ml" and qta_tot >= 1000:
            return f"{formatta_numero(qta_tot / 1000.0)} l"
        if unita_base == "q.b." or qta_tot == 0:
            return "q.b."
        return f"{formatta_numero(qta_tot)} {unita_base}"

    dati_aggregati = []

    def avvia_calcolo_spesa():
        spinner_spesa.visible = True
        colonna_lista.controls.clear()
        colonna_lista.controls.append(
            ft.Container(
                content=ft.Row([
                    ft.ProgressRing(width=24, height=24, stroke_width=2.5, color=ft.Colors.GREEN_700),
                    ft.Text("Ricalcolo quantità e merge dispensa...", size=12, weight=ft.FontWeight.BOLD)
                ], alignment=ft.MainAxisAlignment.CENTER, spacing=10),
                padding=30
            )
        )
        page.update()

        def worker():
            nonlocal dati_aggregati
            scelta = stato_filtro["scelta"]
            if scelta == "Da oggi a fine mese":
                dt_i = oggi_dt
                ult_o = calendar.monthrange(oggi_dt.year, oggi_dt.month)[1]
                dt_f = oggi_dt.replace(day=ult_o)
            elif scelta == "Prossimi 7 giorni":
                dt_i = oggi_dt
                dt_f = oggi_dt + timedelta(days=6)
            elif scelta == "Solo giorno sel.":
                dt_i = data_riferimento
                dt_f = data_riferimento
            else:
                dt_i = data_riferimento.replace(day=1)
                ult = calendar.monthrange(data_riferimento.year, data_riferimento.month)[1]
                dt_f = data_riferimento.replace(day=ult)

            inizio_str = dt_i.strftime("%Y-%m-%d")
            fine_str = dt_f.strftime("%Y-%m-%d")

            # Intervallo mensile per includere sempre Dispensa e Detersivi del mese corrente
            primo_mese_str = data_riferimento.replace(day=1).strftime("%Y-%m-%d")
            ult_mese = calendar.monthrange(data_riferimento.year, data_riferimento.month)[1]
            fine_mese_str = data_riferimento.replace(day=ult_mese).strftime("%Y-%m-%d")

            txt_periodo_date.value = f"📅 Intervallo: {dt_i.strftime('%d/%m/%Y')} → {dt_f.strftime('%d/%m/%Y')}"

            try:
                res_m = supabase.table("menu_giornalieri").select(
                    "*, ingredienti(id, nome), ricette(id, nome)"
                ).gte("data_menu", min(inizio_str, primo_mese_str)).lte("data_menu", max(fine_str, fine_mese_str)).execute()
                tutte_righe = res_m.data if res_m.data else []

                righe_valide = []
                for r in tutte_righe:
                    cat = r.get("categoria")
                    d_m = r.get("data_menu", "")
                    if cat in ["Dispensa", "Detersivi"]:
                        if primo_mese_str <= d_m <= fine_mese_str:
                            righe_valide.append(r)
                    else:
                        if inizio_str <= d_m <= fine_str:
                            righe_valide.append(r)

                ids_ric = list({r["id_ricetta"] for r in righe_valide if r.get("id_ricetta")})
                mappa_ri = {}
                if ids_ric:
                    res_ri = supabase.table("ricette_ingredienti").select(
                        "*, ingredienti(id, nome)"
                    ).in_("id_ricetta", ids_ric).execute()
                    for ri in (res_ri.data or []):
                        rid = ri["id_ricetta"]
                        if rid not in mappa_ri:
                            mappa_ri[rid] = []
                        mappa_ri[rid].append(ri)

                somme = {}
                for m in righe_valide:
                    cat = m.get("categoria")
                    is_preso_db = bool(m.get("preso"))
                    if m.get("id_ingrediente") and m.get("ingredienti"):
                        nome_i = m["ingredienti"]["nome"]
                        q_norm, u_base = normalizza_qta_unita(m.get("quantita") or 0, m.get("unita_misura"))
                        chiave = (nome_i.lower(), u_base)
                        if chiave not in somme:
                            somme[chiave] = {
                                "nome": nome_i, "qta": 0.0, "unita": u_base,
                                "ids_menu_diretti": [], "preso_db": False, "etichetta_extra": ""
                            }
                        somme[chiave]["qta"] += q_norm
                        somme[chiave]["ids_menu_diretti"].append(m["id"])
                        if is_preso_db:
                            somme[chiave]["preso_db"] = True
                        if cat in ["Dispensa", "Detersivi"]:
                            somme[chiave]["etichetta_extra"] = f"[{cat}]"
                    elif m.get("id_ricetta"):
                        for ri in mappa_ri.get(m["id_ricetta"], []):
                            if ri.get("ingredienti"):
                                nome_i = ri["ingredienti"]["nome"]
                                q_norm, u_base = normalizza_qta_unita(ri.get("quantita") or 0, ri.get("unita_misura"))
                                chiave = (nome_i.lower(), u_base)
                                if chiave not in somme:
                                    somme[chiave] = {
                                        "nome": nome_i, "qta": 0.0, "unita": u_base,
                                        "ids_menu_diretti": [], "preso_db": False, "etichetta_extra": ""
                                    }
                                somme[chiave]["qta"] += q_norm

                for k, v in somme.items():
                    k_id = f"{v['nome'].lower()}_{v['unita']}"
                    if v["preso_db"]:
                        spuntati_sessione.add(k_id)

                dati_aggregati = sorted(list(somme.values()), key=lambda x: x["nome"].lower())
                spinner_spesa.visible = False
                disegna_lista_spesa()
            except Exception as ex:
                spinner_spesa.visible = False
                colonna_lista.controls.clear()
                colonna_lista.controls.append(ft.Text(f"Errore calcolo spesa: {ex}", color=ft.Colors.RED_700, size=12))
                page.update()

        threading.Thread(target=worker, daemon=True).start()

    def disegna_lista_spesa():
        colonna_lista.controls.clear()
        if not dati_aggregati:
            txt_riepilogo.value = "Nessun ingrediente pianificato in questo intervallo."
            colonna_lista.controls.append(
                ft.Container(
                    content=ft.Text("Non ci sono menu o voci dispensa programmati per le date selezionate.", size=12, color=ft.Colors.GREY_600, italic=True),
                    padding=20, alignment=ft.Alignment(0, 0)
                )
            )
            page.update()
            return

        da_comprare = []
        gia_in_dispensa = []

        for item in dati_aggregati:
            k_id = f"{item['nome'].lower()}_{item['unita']}"
            if k_id in spuntati_sessione:
                gia_in_dispensa.append((k_id, item))
            else:
                da_comprare.append((k_id, item))

        txt_riepilogo.value = f"Da acquistare: {len(da_comprare)}  |  In dispensa/Presi: {len(gia_in_dispensa)}"

        def toggle_spunta(e, key_ing, item_obj):
            nuovo_stato = bool(e.control.value)
            if nuovo_stato:
                spuntati_sessione.add(key_ing)
            else:
                spuntati_sessione.discard(key_ing)
            modifiche_spunte[0] = True
            ids_dir = item_obj.get("ids_menu_diretti") or []
            if ids_dir:
                def w_sync():
                    for mid in ids_dir:
                        aggiorna_flag_preso_db(mid, nuovo_stato)
                threading.Thread(target=w_sync, daemon=True).start()
            disegna_lista_spesa()

        colonna_lista.controls.append(
            ft.Text("🛒 DA ACQUISTARE (spunta se già in dispensa):", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900)
        )
        if not da_comprare:
            colonna_lista.controls.append(
                ft.Container(content=ft.Text("🎉 Hai spuntato tutti gli ingredienti!", size=12, color=ft.Colors.GREEN_700, weight=ft.FontWeight.BOLD), padding=8)
            )
        for k_id, item in da_comprare:
            q_finale = formatta_qta_finale(item["qta"], item["unita"])
            lbl_extra = f" {item['etichetta_extra']}" if item.get("etichetta_extra") else ""
            colonna_lista.controls.append(
                ft.Container(
                    content=ft.Row([
                        ft.Checkbox(value=False, on_change=lambda e, k=k_id, it=item: toggle_spunta(e, k, it)),
                        ft.Text(f"{item['nome']}{lbl_extra}", size=13, weight=ft.FontWeight.BOLD, expand=True),
                        ft.Container(
                            content=ft.Text(q_finale, size=12, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                            bgcolor=ft.Colors.BLUE_900, padding=ft.Padding(8, 3, 8, 3), border_radius=8
                        )
                    ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    bgcolor="#F5F7FA", padding=ft.Padding(4, 2, 8, 2), border_radius=6,
                    border=ft.Border(top=ft.BorderSide(1, "#E0E0E0"), bottom=ft.BorderSide(1, "#E0E0E0"), left=ft.BorderSide(4, ft.Colors.GREEN_700), right=ft.BorderSide(1, "#E0E0E0"))
                )
            )

        if gia_in_dispensa:
            colonna_lista.controls.append(ft.Divider(height=10, color="#E0E0E0"))
            colonna_lista.controls.append(
                ft.Text("✅ GIÀ IN DISPENSA / NEL CARRELLO:", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_600)
            )
            for k_id, item in gia_in_dispensa:
                q_finale = formatta_qta_finale(item["qta"], item["unita"])
                lbl_extra = f" {item['etichetta_extra']}" if item.get("etichetta_extra") else ""
                colonna_lista.controls.append(
                    ft.Container(
                        content=ft.Row([
                            ft.Checkbox(value=True, on_change=lambda e, k=k_id, it=item: toggle_spunta(e, k, it)),
                            ft.Text(f"✓ {item['nome']}{lbl_extra}", size=12, color=ft.Colors.GREY_600, italic=True, expand=True),
                            ft.Text(q_finale, size=11, color=ft.Colors.GREY_600)
                        ], spacing=4),
                        bgcolor="#EEEEEE", padding=ft.Padding(4, 1, 8, 1), border_radius=6
                    )
                )

        page.update()

    avvia_calcolo_spesa()


# ==========================================================
# CALENDARIO DEI MENU (COMPLETA + INTERRUTTORE "SEMPLIFICATA" SU 3 RIGHE)
# ==========================================================
def crea_calendario_menu(page: ft.Page):
    stato = {
        "data": datetime.now(),
        "vista": "Mese",
        "semplificata": False
    }
    oggi = datetime.now()

    testo_header = ft.Text(size=15, weight=ft.FontWeight.BOLD)
    badge_loading_cal = ft.Container(
        content=ft.Row([
            ft.ProgressRing(width=14, height=14, stroke_width=2, color=ft.Colors.WHITE),
            ft.Text("Caricamento...", size=10, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD)
        ], spacing=5, tight=True),
        bgcolor=ft.Colors.ORANGE_800,
        padding=ft.Padding(8, 3, 8, 3),
        border_radius=10,
        visible=False
    )

    btn_giorno = ft.Container(content=ft.Text("Giorno", size=12, weight=ft.FontWeight.BOLD), border_radius=5, padding=6, on_click=lambda e: cambia_vista("Giorno"))
    btn_settimana = ft.Container(content=ft.Text("Settimana", size=12, weight=ft.FontWeight.BOLD), border_radius=5, padding=6, on_click=lambda e: cambia_vista("Settimana"))
    btn_mese = ft.Container(content=ft.Text("Mese", size=12, weight=ft.FontWeight.BOLD), border_radius=5, padding=6, on_click=lambda e: cambia_vista("Mese"))
    box_bottoni_vista = ft.Row([btn_giorno, btn_settimana, btn_mese], spacing=5, visible=True)

    def toggle_modalita_semplificata(e):
        stato["semplificata"] = bool(switch_semplificata.value)
        lbl_semplificata.weight = ft.FontWeight.BOLD if stato["semplificata"] else ft.FontWeight.NORMAL
        lbl_semplificata.color = ft.Colors.ORANGE_900 if stato["semplificata"] else ft.Colors.BLUE_900
        box_bottoni_vista.visible = not stato["semplificata"]
        aggiorna_calendario_menu()

    lbl_semplificata = ft.Text("⚡ Semplificata", size=12, weight=ft.FontWeight.NORMAL, color=ft.Colors.BLUE_900)
    switch_semplificata = ft.Switch(value=False, on_change=toggle_modalita_semplificata)
    box_switch_semplificata = ft.Container(
        content=ft.Row([lbl_semplificata, switch_semplificata], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        bgcolor="#FFF3E0", padding=ft.Padding(8, 2, 6, 2), border_radius=15,
        border=bordo_campo_uniforme("#FFB74D")
    )

    layer_mese = ft.Column(spacing=0, visible=True)
    layer_settimana = ft.Column(spacing=0, visible=False)
    layer_giorno = ft.Column(spacing=0, visible=False)
    layer_semplificata = ft.Column(spacing=8, visible=False)
    area_dinamica = ft.Column([layer_mese, layer_settimana, layer_giorno, layer_semplificata], expand=True, spacing=0)

    def bordo_standard():
        return ft.Border(
            top=ft.BorderSide(0.5, "#E0E0E0"), bottom=ft.BorderSide(0.5, "#E0E0E0"),
            left=ft.BorderSide(0.5, "#E0E0E0"), right=ft.BorderSide(0.5, "#E0E0E0")
        )

    def apri_giorno_selezionato(dt_target):
        stato["data"] = dt_target
        apri_form_menu_giorno(page, dt_target, aggiorna_calendario_menu)

    def cambia_vista(nuova_vista):
        stato["vista"] = nuova_vista
        aggiorna_calendario_menu()

    def nav_prec(e):
        if stato["semplificata"] or stato["vista"] == "Settimana":
            stato["data"] -= timedelta(days=7)
        elif stato["vista"] == "Mese":
            primo = stato["data"].replace(day=1)
            scorso = primo - timedelta(days=1)
            stato["data"] = stato["data"].replace(year=scorso.year, month=scorso.month, day=1)
        elif stato["vista"] == "Giorno":
            stato["data"] -= timedelta(days=1)
        aggiorna_calendario_menu()

    def nav_succ(e):
        if stato["semplificata"] or stato["vista"] == "Settimana":
            stato["data"] += timedelta(days=7)
        elif stato["vista"] == "Mese":
            mese = stato["data"].month
            anno = stato["data"].year
            if mese == 12:
                stato["data"] = stato["data"].replace(year=anno + 1, month=1, day=1)
            else:
                stato["data"] = stato["data"].replace(month=mese + 1, day=1)
        elif stato["vista"] == "Giorno":
            stato["data"] += timedelta(days=1)
        aggiorna_calendario_menu()

    # ---------------------------------------------------------
    # COSTRUTTORE CASELLA GIORNO IN MODALITÀ SEMPLIFICATA (3 CASELLE)
    # ---------------------------------------------------------
    def crea_card_giorno_semplificata(dt_giorno: datetime, indice_sett: int, voci_giorno: list):
        is_oggi = (dt_giorno.date() == oggi.date())

        # 1. PRANZO (associato a Pranzo -> Primo in modalità completa)
        voci_pranzo = [
            x for x in voci_giorno
            if x.get("categoria") == "Pranzo" and x.get("id_ricetta") and (x.get("subcategoria") in ["Primo", None] or True)
        ]
        # Dà priorità a Primo ma mostra anche eventuali altre ricette di Pranzo
        voci_pranzo.sort(key=lambda x: 0 if x.get("subcategoria") == "Primo" else 1)

        # 2. CENA (associata a Cena -> Secondo in modalità completa)
        voci_cena = [
            x for x in voci_giorno
            if x.get("categoria") == "Cena" and x.get("id_ricetta")
        ]
        voci_cena.sort(key=lambda x: 0 if x.get("subcategoria") == "Secondo" else 1)

        # 3. VARIE
        voci_varie = [
            x for x in voci_giorno
            if x.get("categoria") == "Varie" and x.get("id_ingrediente")
        ]

        def costruisci_badge_piatto(voce_m, colore_tema, portata_default):
            ric = voce_m.get("ricette") or {}
            id_ric = voce_m.get("id_ricetta")
            nome_r = ric.get("nome") or "Piatto"
            port_r = ric.get("portata_default") or voce_m.get("subcategoria") or portata_default
            compilata = ricetta_ha_ingredienti(id_ric)

            # Semaforo: se non compilata nel DB -> Evidenziazione Gialla con bordo Rosso!
            sfondo = "#E8F5E9" if (compilata and colore_tema == "#4CAF50") else ("#E3F2FD" if compilata else "#FFF59D")
            col_bordo = colore_tema if compilata else "#D32F2F"
            col_testo = "#1B5E20" if (compilata and colore_tema == "#4CAF50") else ("#0D47A1" if compilata else "#B71C1C")
            pref_icon = "🍲 " if compilata else "⚠️ "

            return ft.Container(
                content=ft.Row([
                    ft.Text(
                        f"{pref_icon}{nome_r}",
                        size=11, weight=ft.FontWeight.BOLD, color=col_testo,
                        no_wrap=False, expand=True
                    ),
                    ft.Icon(ft.Icons.OPEN_IN_NEW, size=12, color=col_testo)
                ], spacing=2, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                bgcolor=sfondo,
                padding=ft.Padding(6, 4, 6, 4),
                border_radius=5,
                border=bordo_campo_uniforme(col_bordo, 1.5 if not compilata else 1),
                on_click=lambda e, vm=voce_m, r_id=id_ric, r_n=nome_r, r_p=port_r: apri_popup_dettaglio_ricetta_semplificata(
                    page, vm["id"], r_id, r_n, r_p, aggiorna_calendario_menu
                )
            )

        # Casella 1: PRANZO
        col_piatti_pranzo = ft.Column(
            [costruisci_badge_piatto(vp, "#4CAF50", "Primo") for vp in voci_pranzo] if voci_pranzo else [
                ft.Text("Nessun piatto previsto", size=10, color=ft.Colors.GREY_500, italic=True)
            ],
            spacing=3
        )
        casella_pranzo = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Text("☀️ PRANZO", size=10, weight=ft.FontWeight.BOLD, color="#2E7D32", expand=True),
                    ft.Container(
                        content=ft.Text("+ Piatto", size=9, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                        bgcolor="#4CAF50", padding=ft.Padding(6, 2, 6, 2), border_radius=8,
                        on_click=lambda e, dt=dt_giorno: apri_popup_aggiungi_piatto_semplificato(page, dt, "Pranzo", "Primo", aggiorna_calendario_menu)
                    )
                ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                col_piatti_pranzo
            ], spacing=4),
            bgcolor=ft.Colors.WHITE, padding=6, border_radius=6,
            border=ft.Border(top=ft.BorderSide(1, "#C8E6C9"), bottom=ft.BorderSide(1, "#C8E6C9"), left=ft.BorderSide(4, "#4CAF50"), right=ft.BorderSide(1, "#C8E6C9"))
        )

        # Casella 2: CENA
        col_piatti_cena = ft.Column(
            [costruisci_badge_piatto(vc, "#2196F3", "Secondo") for vc in voci_cena] if voci_cena else [
                ft.Text("Nessun piatto previsto", size=10, color=ft.Colors.GREY_500, italic=True)
            ],
            spacing=3
        )
        casella_cena = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Text("🌙 CENA", size=10, weight=ft.FontWeight.BOLD, color="#1565C0", expand=True),
                    ft.Container(
                        content=ft.Text("+ Piatto", size=9, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                        bgcolor="#2196F3", padding=ft.Padding(6, 2, 6, 2), border_radius=8,
                        on_click=lambda e, dt=dt_giorno: apri_popup_aggiungi_piatto_semplificato(page, dt, "Cena", "Secondo", aggiorna_calendario_menu)
                    )
                ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                col_piatti_cena
            ], spacing=4),
            bgcolor=ft.Colors.WHITE, padding=6, border_radius=6,
            border=ft.Border(top=ft.BorderSide(1, "#BBDEFB"), bottom=ft.BorderSide(1, "#BBDEFB"), left=ft.BorderSide(4, "#2196F3"), right=ft.BorderSide(1, "#BBDEFB"))
        )

        # Casella 3: VARIE
        righe_varie_ui = []
        for vv in voci_varie:
            ing_v = vv.get("ingredienti") or {}
            nome_v = ing_v.get("nome") or "Voce"
            q_v = vv.get("quantita") or 0
            q_str = f" ({formatta_numero(q_v)} {vv.get('unita_misura') or ''})" if q_v else ""

            def del_varie(e, id_v=vv["id"]):
                supabase.table("menu_giornalieri").delete().eq("id", id_v).execute()
                aggiorna_calendario_menu()

            righe_varie_ui.append(
                ft.Row([
                    ft.Text(f"• {nome_v}{q_str}", size=10, weight=ft.FontWeight.BOLD, color="#4E342E", expand=True),
                    ft.GestureDetector(
                        content=ft.Icon(ft.Icons.CLOSE, size=13, color=ft.Colors.RED_700),
                        on_tap=del_varie
                    )
                ], spacing=2)
            )

        if not righe_varie_ui:
            righe_varie_ui.append(ft.Text("Nessuna voce", size=9, color=ft.Colors.GREY_500, italic=True))

        casella_varie = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Text("📝 VARIE", size=10, weight=ft.FontWeight.BOLD, color="#5D4037", expand=True),
                    ft.Container(
                        content=ft.Text("+ Varie", size=9, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                        bgcolor="#795548", padding=ft.Padding(6, 2, 6, 2), border_radius=8,
                        on_click=lambda e, d_s=dt_giorno.strftime("%Y-%m-%d"): apri_popup_aggiungi_voce_diretta(page, d_s, "Varie", f"Varie {dt_giorno.strftime('%d/%m')}", aggiorna_calendario_menu)
                    )
                ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Column(righe_varie_ui, spacing=2)
            ], spacing=3),
            bgcolor=ft.Colors.WHITE, padding=6, border_radius=6,
            border=ft.Border(top=ft.BorderSide(1, "#D7CCC8"), bottom=ft.BorderSide(1, "#D7CCC8"), left=ft.BorderSide(4, "#795548"), right=ft.BorderSide(1, "#D7CCC8"))
        )

        intestazione_giorno = ft.Container(
            content=ft.Row([
                ft.Text(f"{GIORNI_ESTESI[indice_sett].upper()} {dt_giorno.day}/{dt_giorno.month}", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE if is_oggi else ft.Colors.BLUE_900, expand=True),
                ft.GestureDetector(
                    content=ft.Icon(ft.Icons.LIST_ALT, size=15, color=ft.Colors.WHITE if is_oggi else ft.Colors.BLUE_900, tooltip="Apri vista completa del giorno"),
                    on_tap=lambda e, dt=dt_giorno: apri_giorno_selezionato(dt)
                )
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            bgcolor=ft.Colors.BLUE_900 if is_oggi else "#DCE4EC",
            padding=ft.Padding(8, 5, 8, 5),
            border_radius=6
        )

        return ft.Container(
            content=ft.Column([
                intestazione_giorno,
                casella_pranzo,
                casella_cena,
                casella_varie
            ], spacing=5),
            expand=1,
            bgcolor="#F4F6F9",
            padding=6,
            border_radius=8,
            border=bordo_campo_uniforme(ft.Colors.BLUE_900 if is_oggi else "#CFD8DC", 2 if is_oggi else 1)
        )

    # ---------------------------------------------------------
    # COSTRUTTORE CASELLE MENSILI: DISPENSA & DETERSIVI E VARIE
    # ---------------------------------------------------------
    def crea_card_mensile_spuntabile(titolo: str, icona_em: str, categoria_db: str, colore_tema: str, voci_mese: list, data_rif_mese: datetime):
        primo_del_mese_str = data_rif_mese.replace(day=1).strftime("%Y-%m-%d")
        nome_mese = MESI[data_rif_mese.month]

        lista_voci_ui = ft.Column(spacing=3, scroll=ft.ScrollMode.AUTO, height=195)

        if not voci_mese:
            lista_voci_ui.controls.append(
                ft.Container(
                    content=ft.Text(f"Nessun prodotto in {titolo} per {nome_mese}. Clicca '+ Aggiungi' in alto.", size=10, color=ft.Colors.GREY_600, italic=True),
                    padding=10
                )
            )
        else:
            # Ordina: prima da prendere (preso=False), poi già presenti (preso=True)
            voci_ordinate = sorted(voci_mese, key=lambda x: (1 if x.get("preso") else 0, (x.get("ingredienti") or {}).get("nome", "")))
            for vm in voci_ordinate:
                ing_o = vm.get("ingredienti") or {}
                nome_p = ing_o.get("nome") or "Prodotto"
                q_p = vm.get("quantita") or 0
                q_str = f" ({formatta_numero(q_p)} {vm.get('unita_misura') or ''})" if q_p else ""
                is_preso = bool(vm.get("preso"))

                def cambia_spunta_mese(e, id_riga=vm["id"]):
                    nuovo_v = bool(e.control.value)
                    aggiorna_flag_preso_db(id_riga, nuovo_v)
                    aggiorna_calendario_menu()

                def elimina_voce_mese(e, id_riga=vm["id"]):
                    supabase.table("menu_giornalieri").delete().eq("id", id_riga).execute()
                    aggiorna_calendario_menu()

                lista_voci_ui.controls.append(
                    ft.Container(
                        content=ft.Row([
                            ft.Checkbox(value=is_preso, on_change=cambia_spunta_mese),
                            ft.Text(
                                f"{'✓ ' if is_preso else ''}{nome_p}{q_str}",
                                size=11,
                                weight=ft.FontWeight.NORMAL if is_preso else ft.FontWeight.BOLD,
                                color=ft.Colors.GREY_600 if is_preso else ft.Colors.BLACK,
                                italic=is_preso,
                                expand=True
                            ),
                            ft.GestureDetector(
                                content=ft.Icon(ft.Icons.CLOSE, size=14, color=ft.Colors.RED_700),
                                on_tap=elimina_voce_mese
                            )
                        ], spacing=2, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        bgcolor="#EEEEEE" if is_preso else ft.Colors.WHITE,
                        padding=ft.Padding(4, 1, 6, 1),
                        border_radius=5,
                        border=bordo_campo_uniforme("#E0E0E0" if is_preso else colore_tema)
                    )
                )

        tot_presenti = sum(1 for x in voci_mese if x.get("preso"))
        tot_da_prendere = len(voci_mese) - tot_presenti

        return ft.Container(
            content=ft.Column([
                ft.Container(
                    content=ft.Row([
                        ft.Text(f"{icona_em} {titolo.upper()} ({nome_mese[:3].upper()})", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE, expand=True),
                        ft.Container(
                            content=ft.Text("+ Aggiungi", size=9, color=colore_tema, weight=ft.FontWeight.BOLD),
                            bgcolor=ft.Colors.WHITE, padding=ft.Padding(6, 2, 6, 2), border_radius=8,
                            on_click=lambda e: apri_popup_aggiungi_voce_diretta(page, primo_del_mese_str, categoria_db, f"{titolo} ({nome_mese})", aggiorna_calendario_menu)
                        )
                    ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    bgcolor=colore_tema, padding=ft.Padding(8, 5, 8, 5), border_radius=6
                ),
                ft.Text(
                    f"Da prendere: {tot_da_prendere}  |  Presenti: {tot_presenti}",
                    size=10, color=ft.Colors.GREY_700, weight=ft.FontWeight.BOLD
                ),
                lista_voci_ui
            ], spacing=5),
            expand=1,
            bgcolor="#F4F6F9",
            padding=6,
            border_radius=8,
            border=bordo_campo_uniforme(colore_tema, 1.5)
        )

    def aggiorna_calendario_menu():
        vista = stato["vista"]
        data = stato["data"]
        is_semplificata = stato["semplificata"]

        badge_loading_cal.visible = True
        try:
            page.update()
        except Exception:
            pass

        def worker_calendario():
            btn_giorno.bgcolor = ft.Colors.BLUE_900 if vista == "Giorno" else ft.Colors.GREY_200
            btn_giorno.content.color = ft.Colors.WHITE if vista == "Giorno" else ft.Colors.BLACK
            btn_settimana.bgcolor = ft.Colors.BLUE_900 if vista == "Settimana" else ft.Colors.GREY_200
            btn_settimana.content.color = ft.Colors.WHITE if vista == "Settimana" else ft.Colors.BLACK
            btn_mese.bgcolor = ft.Colors.BLUE_900 if vista == "Mese" else ft.Colors.GREY_200
            btn_mese.content.color = ft.Colors.WHITE if vista == "Mese" else ft.Colors.BLACK

            layer_semplificata.visible = is_semplificata
            layer_mese.visible = (not is_semplificata and vista == "Mese")
            layer_settimana.visible = (not is_semplificata and vista == "Settimana")
            layer_giorno.visible = (not is_semplificata and vista == "Giorno")

            dizionario_menu = {}
            voci_dispensa_mese = []
            voci_detersivi_mese = []

            if supabase:
                try:
                    assicura_cache_anagrafica(forza_ricarica=True)

                    data_rif = data.replace(day=1)
                    inizio_f = (data_rif - timedelta(days=10)).strftime("%Y-%m-%d")
                    fine_f = (data_rif + timedelta(days=42)).strftime("%Y-%m-%d")

                    primo_mese_corr = data.replace(day=1).strftime("%Y-%m-%d")
                    ult_g_mese = calendar.monthrange(data.year, data.month)[1]
                    ultimo_mese_corr = data.replace(day=ult_g_mese).strftime("%Y-%m-%d")

                    res = supabase.table("menu_giornalieri").select(
                        "*, ricette(id, nome, portata_default), ingredienti(id, nome, unita_default)"
                    ).gte("data_menu", inizio_f).lte("data_menu", fine_f).execute()

                    if res.data:
                        for riga in res.data:
                            cat_r = riga.get("categoria")
                            d_str = riga["data_menu"]
                            if cat_r == "Dispensa":
                                if primo_mese_corr <= d_str <= ultimo_mese_corr:
                                    voci_dispensa_mese.append(riga)
                            elif cat_r == "Detersivi":
                                if primo_mese_corr <= d_str <= ultimo_mese_corr:
                                    voci_detersivi_mese.append(riga)
                            else:
                                if d_str not in dizionario_menu:
                                    dizionario_menu[d_str] = []
                                dizionario_menu[d_str].append(riga)
                except Exception as ex:
                    print("Errore caricamento menu:", ex)

            # ==========================================
            # MODALITÀ SEMPLIFICATA (SETTIMANALE SU 3 RIGHE + DISPENSA + DETERSIVI)
            # ==========================================
            if is_semplificata:
                lunedi = data - timedelta(days=data.weekday())
                domenica = lunedi + timedelta(days=6)
                testo_header.value = (
                    f"{lunedi.day} - {domenica.day} {MESI[lunedi.month][:3]} {lunedi.year}"
                    if lunedi.month == domenica.month else
                    f"{lunedi.day} {MESI[lunedi.month][:3]} - {domenica.day} {MESI[domenica.month][:3]} {domenica.year}"
                )
                layer_semplificata.controls.clear()

                cards_giorni = []
                for i in range(7):
                    gc = lunedi + timedelta(days=i)
                    d_str = gc.strftime("%Y-%m-%d")
                    cards_giorni.append(crea_card_giorno_semplificata(gc, i, dizionario_menu.get(d_str, [])))

                card_dispensa = crea_card_mensile_spuntabile("Dispensa", "📦", "Dispensa", "#00897B", voci_dispensa_mese, data)
                card_detersivi = crea_card_mensile_spuntabile("Detersivi e Varie", "🧼", "Detersivi", "#546E7A", voci_detersivi_mese, data)

                # Riga 1: Lunedì, Martedì, Mercoledì
                riga_1 = ft.Row([cards_giorni[0], cards_giorni[1], cards_giorni[2]], spacing=6, vertical_alignment=ft.CrossAxisAlignment.START)
                # Riga 2: Giovedì, Venerdì, Sabato
                riga_2 = ft.Row([cards_giorni[3], cards_giorni[4], cards_giorni[5]], spacing=6, vertical_alignment=ft.CrossAxisAlignment.START)
                # Riga 3: Domenica, Dispensa (Mese), Detersivi e Varie (Mese)
                riga_3 = ft.Row([cards_giorni[6], card_dispensa, card_detersivi], spacing=6, vertical_alignment=ft.CrossAxisAlignment.START)

                layer_semplificata.controls.extend([riga_1, riga_2, riga_3])

            # ==========================================
            # MODALITÀ COMPLETA - VISTA MESE
            # ==========================================
            elif vista == "Mese":
                testo_header.value = f"{MESI[data.month]} {data.year}"
                layer_mese.controls.clear()

                griglia = ft.Column(spacing=0)
                matrice = calendar.monthcalendar(data.year, data.month)

                for settimana in matrice:
                    riga_ui = ft.Row(alignment=ft.MainAxisAlignment.SPACE_BETWEEN, spacing=0)
                    for giorno in settimana:
                        if giorno == 0:
                            riga_ui.controls.append(ft.Container(expand=1, height=90, border=bordo_standard()))
                        else:
                            is_oggi = (giorno == oggi.day and data.month == oggi.month and data.year == oggi.year)
                            is_sel = (giorno == data.day)
                            data_giorno = data.replace(day=giorno)
                            d_str = data_giorno.strftime("%Y-%m-%d")
                            voci_giorno = dizionario_menu.get(d_str, [])

                            cerchio = ft.Container(
                                content=ft.Text(str(giorno), color=ft.Colors.WHITE if is_oggi else ft.Colors.BLACK, size=12, weight=ft.FontWeight.BOLD if is_oggi else ft.FontWeight.NORMAL),
                                bgcolor=ft.Colors.BLUE_900 if is_oggi else (ft.Colors.BLUE_100 if is_sel else ft.Colors.TRANSPARENT),
                                border_radius=15, width=24, height=24, alignment=ft.Alignment(0, 0)
                            )

                            cat_presenti = []
                            for sup in SUPERCATEGORIE:
                                v_sup = [x for x in voci_giorno if x["categoria"] == sup]
                                if v_sup:
                                    voce_ric = next((x for x in v_sup if x.get("ricette") and x["ricette"].get("nome")), v_sup[0])
                                    dettaglio = ""
                                    ha_warn = False
                                    if voce_ric.get("ricette") and voce_ric["ricette"].get("nome"):
                                        dettaglio = f": {voce_ric['ricette']['nome']}"
                                        if not ricetta_ha_ingredienti(voce_ric.get("id_ricetta")):
                                            ha_warn = True
                                    elif voce_ric.get("ingredienti") and voce_ric["ingredienti"].get("nome"):
                                        dettaglio = f": {voce_ric['ingredienti']['nome']}"
                                    cat_presenti.append((sup, f"{'⚠️ ' if ha_warn else ''}{sup[:3]}{dettaglio}", ha_warn))

                            col_badge = ft.Column(spacing=1, expand=True)
                            for sup_nome, etichetta, is_warn in cat_presenti[:2]:
                                col_badge.controls.append(
                                    ft.Container(
                                        content=ft.Text(etichetta, size=7, color=ft.Colors.BLACK if is_warn else ft.Colors.WHITE, weight=ft.FontWeight.BOLD, no_wrap=True),
                                        bgcolor="#FFF59D" if is_warn else COLORI_PASTI.get(sup_nome, ft.Colors.BLUE_900),
                                        padding=2, border_radius=2, height=18
                                    )
                                )
                            if len(cat_presenti) > 2:
                                col_badge.controls.append(
                                    ft.Container(
                                        content=ft.Text(f"+ {len(cat_presenti) - 2} altri", size=8, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_700),
                                        alignment=ft.Alignment(0, 0)
                                    )
                                )

                            riga_ui.controls.append(
                                ft.Container(
                                    content=ft.Column([
                                        ft.Container(content=cerchio, alignment=ft.Alignment(0, 0)),
                                        col_badge
                                    ], spacing=2, horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
                                    expand=1, height=90, border=bordo_standard(), padding=2,
                                    on_click=lambda e, dt=data_giorno: apri_giorno_selezionato(dt)
                                )
                            )
                    griglia.controls.append(riga_ui)

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
            # MODALITÀ COMPLETA - VISTA SETTIMANA
            # ==========================================
            elif vista == "Settimana":
                lunedi = data - timedelta(days=data.weekday())
                domenica = lunedi + timedelta(days=6)
                testo_header.value = (
                    f"{lunedi.day} - {domenica.day} {MESI[lunedi.month][:3]} {lunedi.year}"
                    if lunedi.month == domenica.month else
                    f"{lunedi.day} {MESI[lunedi.month][:3]} - {domenica.day} {MESI[domenica.month][:3]} {domenica.year}"
                )
                layer_settimana.controls.clear()

                riga1 = ft.Row(alignment=ft.MainAxisAlignment.START, spacing=0)
                riga2 = ft.Row(alignment=ft.MainAxisAlignment.START, spacing=0)

                for i in range(7):
                    gc = lunedi + timedelta(days=i)
                    d_str = gc.strftime("%Y-%m-%d")
                    voci_giorno = dizionario_menu.get(d_str, [])

                    cerchio_sett = ft.Container(
                        content=ft.Text(str(gc.day), color=ft.Colors.WHITE if (gc.date() == oggi.date()) else ft.Colors.BLACK, size=13, weight=ft.FontWeight.BOLD),
                        bgcolor=ft.Colors.BLUE_900 if (gc.date() == oggi.date()) else ft.Colors.TRANSPARENT,
                        border_radius=15, width=24, height=24, alignment=ft.Alignment(0, 0)
                    )

                    intestazione = ft.Container(
                        content=ft.Column([
                            ft.Text(GIORNI[i], size=9, color=ft.Colors.GREY_700),
                            cerchio_sett
                        ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=0),
                        padding=4
                    )

                    col_pasti_sett = ft.Column(spacing=3, scroll=ft.ScrollMode.AUTO)
                    for sup in SUPERCATEGORIE:
                        v_sup = [x for x in voci_giorno if x["categoria"] == sup]
                        if v_sup:
                            col_sup = COLORI_PASTI.get(sup, ft.Colors.BLUE_900)
                            testi_voci = []
                            for x in v_sup:
                                pref = f"{x['subcategoria'][:3]}: " if x.get("subcategoria") else ""
                                if x.get("ricette") and x["ricette"].get("nome"):
                                    testi_voci.append(f"• {pref}{x['ricette']['nome']}")
                                elif x.get("ingredienti") and x["ingredienti"].get("nome"):
                                    testi_voci.append(f"• {pref}{x['ingredienti']['nome']}")

                            col_pasti_sett.controls.append(
                                ft.Container(
                                    content=ft.Column(
                                        [ft.Text(sup.upper(), size=8, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE)] +
                                        [ft.Text(tv, size=8, color=ft.Colors.WHITE, no_wrap=False) for tv in testi_voci],
                                        spacing=1
                                    ),
                                    bgcolor=col_sup, padding=4, border_radius=4, width=float("inf")
                                )
                            )

                    box_giorno = ft.Container(
                        content=ft.Column([
                            intestazione,
                            ft.Divider(height=1, color="#E0E0E0"),
                            ft.Container(content=col_pasti_sett, padding=2, expand=True)
                        ], spacing=0),
                        expand=1, height=300, border=bordo_standard(),
                        on_click=lambda e, dt=gc: apri_giorno_selezionato(dt)
                    )

                    if i < 4:
                        riga1.controls.append(box_giorno)
                    else:
                        riga2.controls.append(box_giorno)

                riga2.controls.append(ft.Container(expand=1))
                layer_settimana.controls.extend([riga1, ft.Container(height=12), riga2])

            # ==========================================
            # MODALITÀ COMPLETA - VISTA GIORNO
            # ==========================================
            elif vista == "Giorno":
                testo_header.value = f"{data.day} {MESI[data.month]} {data.year}"
                is_oggi = (data.date() == oggi.date())
                layer_giorno.controls.clear()

                d_str = data.strftime("%Y-%m-%d")
                voci_giorno = dizionario_menu.get(d_str, [])

                intestazione = ft.Container(
                    content=ft.Row([
                        ft.Column([
                            ft.Text(GIORNI[data.weekday()], size=12, color=ft.Colors.GREY_700),
                            ft.Text(str(data.day), size=24, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900 if is_oggi else ft.Colors.BLACK)
                        ], spacing=0),
                        ft.Container(
                            content=ft.Text("✏️ Compila / Modifica Menu del Giorno", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=12),
                            bgcolor=ft.Colors.BLUE_900, padding=10, border_radius=6,
                            on_click=lambda e: apri_giorno_selezionato(data)
                        )
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    padding=10, border=bordo_standard()
                )

                col_dettaglio_giorno = ft.Column(spacing=8)

                if not voci_giorno:
                    col_dettaglio_giorno.controls.append(
                        ft.Container(
                            content=ft.Text("Nessun pasto programmato per questa giornata. Clicca su 'Compila / Modifica Menu' per aggiungere ricette o ingredienti.", color=ft.Colors.GREY_600, italic=True, size=12),
                            padding=20, alignment=ft.Alignment(0, 0)
                        )
                    )
                else:
                    for sup in SUPERCATEGORIE:
                        v_sup = [x for x in voci_giorno if x["categoria"] == sup]
                        if v_sup:
                            col_sup = COLORI_PASTI.get(sup, ft.Colors.BLUE_900)
                            righe_pasto = [ft.Text(f"🍽️ {sup.upper()}", size=13, weight=ft.FontWeight.BOLD, color=col_sup)]
                            for x in v_sup:
                                sub_lbl = f"[{x['subcategoria']}] " if x.get("subcategoria") else ""
                                if x.get("ricette") and x["ricette"].get("nome"):
                                    righe_pasto.append(ft.Text(f"  🍲 {sub_lbl}{x['ricette']['nome']}", size=12, weight=ft.FontWeight.BOLD))
                                elif x.get("ingredienti") and x["ingredienti"].get("nome"):
                                    q = x.get("quantita") or 0
                                    q_str = f" ({formatta_numero(q)} {x.get('unita_misura') or ''})" if q else ""
                                    righe_pasto.append(ft.Text(f"  • {sub_lbl}{x['ingredienti']['nome']}{q_str}", size=12))

                            col_dettaglio_giorno.controls.append(
                                ft.Container(
                                    content=ft.Column(righe_pasto, spacing=3),
                                    bgcolor=ft.Colors.WHITE, padding=10, border_radius=8,
                                    border=ft.Border(top=ft.BorderSide(1, "#E0E0E0"), bottom=ft.BorderSide(1, "#E0E0E0"), left=ft.BorderSide(5, col_sup), right=ft.BorderSide(1, "#E0E0E0")),
                                    on_click=lambda e: apri_giorno_selezionato(data)
                                )
                            )

                layer_giorno.controls.extend([intestazione, ft.Container(height=6), col_dettaglio_giorno])

            badge_loading_cal.visible = False
            try:
                page.update()
            except Exception:
                pass

        threading.Thread(target=worker_calendario, daemon=True).start()

    page.aggiorna_vista_menu = aggiorna_calendario_menu

    header = ft.Row(
        controls=[
            ft.Row([
                ft.IconButton(ft.Icons.CHEVRON_LEFT, on_click=nav_prec, icon_size=20),
                ft.IconButton(ft.Icons.CHEVRON_RIGHT, on_click=nav_succ, icon_size=20),
                testo_header,
                badge_loading_cal
            ], alignment=ft.MainAxisAlignment.START, spacing=4),
            ft.Row([
                box_switch_semplificata,
                box_bottoni_vista
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER, wrap=True)
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        wrap=True,
        run_spacing=8
    )

    btn_fai_la_spesa = ft.Container(
        content=ft.Row([
            ft.Icon(ft.Icons.SHOPPING_CART, color=ft.Colors.WHITE, size=20),
            ft.Text("FAI LA SPESA (Somma Ingredienti & Dispensa)", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=13)
        ], alignment=ft.MainAxisAlignment.CENTER, spacing=8),
        bgcolor=ft.Colors.GREEN_700,
        padding=12,
        border_radius=8,
        on_click=lambda e: apri_popup_fai_la_spesa(page, stato["data"], aggiorna_calendario_menu)
    )

    aggiorna_calendario_menu()

    return ft.Container(
        content=ft.Column([
            header,
            area_dinamica,
            ft.Divider(height=10, color="#EEEEEE"),
            btn_fai_la_spesa
        ], spacing=10, scroll=ft.ScrollMode.AUTO),
        padding=5, expand=True
    )
