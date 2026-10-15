import flet as ft
import calendar
import os
import re
import json
import html
import time
import threading
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

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

CATALOGO_DETERSIVI_BASE = [
    ("Alcool", "l"), ("Detersivo lavatrice", "l"), ("Detersivo piatti", "l"),
    ("Pastiglie lavastoviglie", "pz"), ("Ammorbidente", "l"), ("Candeggina", "l"),
    ("Sgrassatore", "pz"), ("Detergente pavimenti", "l"), ("Detergente bagno / anticalcare", "pz"),
    ("Detergente vetri / multiuso", "pz"), ("Carta igienica", "pz"), ("Carta assorbente / Scottex", "pz"),
    ("Dischetti struccanti", "pz"), ("Tovaglioli di carta", "pz"), ("Sacchetti spazzatura", "pz"),
    ("Sacchetti umido", "pz"), ("Spugne cucina", "pz"), ("Panni microfibra", "pz"),
    ("Sapone liquido mani", "pz"), ("Bagnoschiuma", "pz"), ("Shampoo", "pz"),
    ("Dentifricio", "pz"), ("Pellicola trasparente", "pz"), ("Carta forno", "pz"),
    ("Carta alluminio", "pz"), ("Sale lavastoviglie", "kg"), ("Brillantante lavastoviglie", "ml"),
]
PAROLE_CHIAVE_DETERSIVI = {x[0].lower() for x in CATALOGO_DETERSIVI_BASE}

CACHE_ANAGRAFICA = {
    "ingredienti": None,
    "ricette": None,
    "conteggio_ing_ricette": {},
    "ids_detersivi": set()
}


def is_valid_uuid(val):
    if not val:
        return False
    s = str(val).strip()
    return len(s) >= 32 and not s.startswith("temp_")


def apri_dialog_sicuro(page: ft.Page, dlg: ft.AlertDialog):
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


def chiudi_dialog_sicuro(page: ft.Page, dlg: ft.AlertDialog):
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
    if not is_valid_uuid(id_riga_menu):
        return
    try:
        supabase.table("menu_giornalieri").update({"preso": bool(valore_bool)}).eq("id", id_riga_menu).execute()
    except Exception as ex:
        print("Errore update preso:", ex)


# ==========================================================
# GESTIONE METADATI RICETTA (PORTATA, PERSONE, LINK SITO, FOTO)
# ==========================================================
def estrai_meta_ricetta(ric_obj: dict):
    if not ric_obj:
        return {"portata": "Primo", "porzioni": 8, "url_fonte": "", "foto_url": ""}
    raw_portata = str(ric_obj.get("portata_default") or "Primo")
    parti = raw_portata.split("||")
    portata = parti[0] if parti[0] in SUB_CON_RICETTA else "Primo"
    porzioni = 8
    if len(parti) > 1 and parti[1].strip().isdigit():
        p_val = int(parti[1].strip())
        if p_val >= 1:
            porzioni = p_val
    url_fonte = parti[2].strip() if len(parti) > 2 else ""
    foto_url = parti[3].strip() if len(parti) > 3 else ""
    return {
        "portata": portata,
        "porzioni": porzioni,
        "url_fonte": url_fonte,
        "foto_url": foto_url
    }


def codifica_meta_portata(portata: str, porzioni: int, url_fonte: str = "", foto_url: str = ""):
    p_clean = portata if portata in SUB_CON_RICETTA else "Primo"
    num_p = max(1, int(porzioni or 8))
    u_clean = (url_fonte or "").replace("||", "").strip()
    f_clean = (foto_url or "").replace("||", "").strip()
    return f"{p_clean}||{num_p}||{u_clean}||{f_clean}"


def salva_meta_ricetta_db(id_ricetta: str, portata: str, porzioni: int, url_fonte: str = "", foto_url: str = "", nuovo_nome: str = None):
    if not is_valid_uuid(id_ricetta):
        return
    val_codificato = codifica_meta_portata(portata, porzioni, url_fonte, foto_url)
    payload = {"portata_default": val_codificato}
    if nuovo_nome:
        payload["nome"] = nuovo_nome
    try:
        supabase.table("ricette").update(payload).eq("id", id_ricetta).execute()
    except Exception:
        payload["portata_default"] = codifica_meta_portata(portata, porzioni, "", "")
        supabase.table("ricette").update(payload).eq("id", id_ricetta).execute()

    if CACHE_ANAGRAFICA["ricette"]:
        for r in CACHE_ANAGRAFICA["ricette"]:
            if str(r.get("id")) == str(id_ricetta):
                r["portata_default"] = val_codificato
                if nuovo_nome:
                    r["nome"] = nuovo_nome
                break


def normalizza_ingrediente_record(ing_raw: dict):
    u_raw = str(ing_raw.get("unita_default") or "g")
    is_det = "|DET" in u_raw
    u_clean = u_raw.replace("|DET", "").strip() or ("pz" if is_det else "g")
    nome_low = str(ing_raw.get("nome") or "").strip().lower()
    if nome_low in PAROLE_CHIAVE_DETERSIVI or ing_raw.get("id") in CACHE_ANAGRAFICA["ids_detersivi"]:
        is_det = True
    copia = dict(ing_raw)
    copia["unita_default"] = u_clean if u_clean in UNITA_MISURA else "pz"
    copia["is_detersivo"] = is_det
    if is_det and copia.get("id"):
        CACHE_ANAGRAFICA["ids_detersivi"].add(copia["id"])
    return copia


# ==========================================================
# COMPONENTI GEMELLI: QUANTITÀ E UNITÀ CON STESSA ALTEZZA AL PIXEL
# ==========================================================
def crea_box_quantita(valore_iniziale="1", width=90, height=34, text_size=12, mostra_prefisso=True, expand=False, prefisso_custom="Q.tà:", on_change_fn=None):
    tf = ft.TextField(
        value=str(valore_iniziale),
        border=ft.InputBorder.NONE,
        dense=True,
        text_size=text_size,
        content_padding=ft.Padding(2, 0, 2, 0),
        text_align=ft.TextAlign.CENTER,
        expand=True,
        on_change=on_change_fn
    )
    controlli_riga = []
    if mostra_prefisso:
        controlli_riga.append(ft.Text(prefisso_custom, size=10, color=ft.Colors.GREY_700))
    controlli_riga.append(tf)

    box = ft.Container(
        content=ft.Row(controlli_riga, spacing=2, vertical_alignment=ft.CrossAxisAlignment.CENTER),
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
    val_pulito = str(valore_iniziale or "g").replace("|DET", "").strip()
    val_corrente = val_pulito if val_pulito in UNITA_MISURA else "g"
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
        v_clean = str(nuovo_v or "").replace("|DET", "").strip()
        if v_clean in UNITA_MISURA:
            stato_u["value"] = v_clean
            lbl_val.value = v_clean

    menu_btn.set_value = set_value
    return menu_btn


# ==========================================================
# GESTIONE SICURA ANAGRAFICA (SEMPRE ALLINEATA CON SUPABASE)
# ==========================================================
def assicura_cache_anagrafica(forza_ricarica=False):
    if not supabase:
        return [], []
    try:
        if forza_ricarica or CACHE_ANAGRAFICA["ingredienti"] is None:
            res_ing = supabase.table("ingredienti").select("*").limit(5000).order("nome").execute()
            CACHE_ANAGRAFICA["ingredienti"] = [normalizza_ingrediente_record(x) for x in (res_ing.data or [])]
        if forza_ricarica or CACHE_ANAGRAFICA["ricette"] is None:
            res_ric = supabase.table("ricette").select("*").limit(5000).order("nome").execute()
            CACHE_ANAGRAFICA["ricette"] = res_ric.data if res_ric.data else []
            ids_validi = {str(r["id"]) for r in CACHE_ANAGRAFICA["ricette"] if r.get("id")}
            CACHE_ANAGRAFICA["conteggio_ing_ricette"] = {
                k: v for k, v in CACHE_ANAGRAFICA["conteggio_ing_ricette"].items() if k in ids_validi
            }
    except Exception as ex:
        print("Errore caricamento cache anagrafica:", ex)
    return CACHE_ANAGRAFICA["ingredienti"] or [], CACHE_ANAGRAFICA["ricette"] or []


def ottieni_o_crea_ing_sicuro(nome_raw: str, unita_scelta: str, is_detersivo: bool = False, ignora_cache: bool = False):
    nome_pulito = re.sub(r"\s+", " ", (nome_raw or "").strip()).capitalize()
    if not nome_pulito:
        return None
    lista_ing, _ = assicura_cache_anagrafica(forza_ricarica=ignora_cache)
    unita_clean = str(unita_scelta or "g").replace("|DET", "").strip()
    unita_db = f"{unita_clean}|DET" if is_detersivo else unita_clean

    if not ignora_cache:
        esistente = next((i for i in lista_ing if i["nome"].strip().lower() == nome_pulito.lower()), None)
        if esistente:
            if is_detersivo and not esistente.get("is_detersivo"):
                esistente["is_detersivo"] = True
                CACHE_ANAGRAFICA["ids_detersivi"].add(esistente["id"])
                try:
                    supabase.table("ingredienti").update({"unita_default": unita_db}).eq("id", esistente["id"]).execute()
                except Exception:
                    pass
            return esistente["id"]

    try:
        res_db = supabase.table("ingredienti").select("*").ilike("nome", nome_pulito).limit(1).execute()
        if res_db.data:
            trovato = normalizza_ingrediente_record(res_db.data[0])
            CACHE_ANAGRAFICA["ingredienti"] = [x for x in lista_ing if x["nome"].strip().lower() != nome_pulito.lower()] + [trovato]
            return trovato["id"]
    except Exception:
        pass

    try:
        res_ins = supabase.table("ingredienti").insert({"nome": nome_pulito, "unita_default": unita_db}).execute()
        nuovo = normalizza_ingrediente_record(res_ins.data[0])
        CACHE_ANAGRAFICA["ingredienti"] = [x for x in lista_ing if x["nome"].strip().lower() != nome_pulito.lower()] + [nuovo]
        return nuovo["id"]
    except Exception:
        res_retry = supabase.table("ingredienti").select("*").ilike("nome", nome_pulito).limit(1).execute()
        if res_retry.data:
            trovato = normalizza_ingrediente_record(res_retry.data[0])
            lista_ing.append(trovato)
            return trovato["id"]
    return None


def ottieni_o_crea_ricetta_sicura(nome_raw: str, portata: str = "Primo", ignora_cache: bool = False):
    nome_pulito = re.sub(r"\s+", " ", (nome_raw or "").strip()).capitalize()
    _, lista_ric = assicura_cache_anagrafica(forza_ricarica=ignora_cache)

    if not ignora_cache:
        esistente = next((r for r in lista_ric if r["nome"].strip().lower() == nome_pulito.lower()), None)
        if esistente:
            return esistente

    try:
        res_db = supabase.table("ricette").select("*").ilike("nome", nome_pulito).limit(1).execute()
        if res_db.data:
            trovata = res_db.data[0]
            CACHE_ANAGRAFICA["ricette"] = [r for r in lista_ric if r["nome"].strip().lower() != nome_pulito.lower()] + [trovata]
            return trovata
    except Exception:
        pass

    try:
        res_new = supabase.table("ricette").insert({
            "nome": nome_pulito,
            "portata_default": codifica_meta_portata(portata, 8)
        }).execute()
        nuova = res_new.data[0]
        CACHE_ANAGRAFICA["ricette"] = [r for r in lista_ric if r["nome"].strip().lower() != nome_pulito.lower()] + [nuova]
        CACHE_ANAGRAFICA["conteggio_ing_ricette"][str(nuova["id"])] = 0
        return nuova
    except Exception:
        res_retry = supabase.table("ricette").select("*").ilike("nome", nome_pulito).limit(1).execute()
        if res_retry.data:
            trovata = res_retry.data[0]
            CACHE_ANAGRAFICA["ricette"] = [r for r in lista_ric if r["nome"].strip().lower() != nome_pulito.lower()] + [trovata]
            return trovata
    return None


def aggiorna_conteggio_ricette_visibili(ids_ricette: list):
    if not supabase or not ids_ricette:
        return
    ids_reali = [str(x) for x in ids_ricette if is_valid_uuid(x)]
    if not ids_reali:
        return
    try:
        res_ri = supabase.table("ricette_ingredienti").select("id_ricetta").in_("id_ricetta", ids_reali).limit(5000).execute()
        conteggi = {}
        for r in (res_ri.data or []):
            rid = str(r.get("id_ricetta"))
            conteggi[rid] = conteggi.get(rid, 0) + 1
        for rid in ids_reali:
            CACHE_ANAGRAFICA["conteggio_ing_ricette"][rid] = conteggi.get(rid, 0)
    except Exception as ex:
        print("Errore conteggio ingredienti ricette visibili:", ex)


def ricetta_ha_ingredienti(id_ricetta):
    if not is_valid_uuid(id_ricetta):
        return False
    return (CACHE_ANAGRAFICA.get("conteggio_ing_ricette") or {}).get(str(id_ricetta), 0) > 0


def salva_lista_ingredienti_in_blocco(id_ricetta: str, lista_items: list):
    if not is_valid_uuid(id_ricetta):
        return 0
    righe_ri_bulk = []
    ids_ing_usati = set()
    for it in lista_items:
        nome_i = (it.get("nome") or "").strip()
        unita_i = it.get("unita") or "g"
        if not nome_i:
            continue
        id_ing = ottieni_o_crea_ing_sicuro(nome_i, unita_i, is_detersivo=False)
        if id_ing and id_ing not in ids_ing_usati:
            ids_ing_usati.add(id_ing)
            righe_ri_bulk.append({
                "id_ricetta": id_ricetta,
                "id_ingrediente": id_ing,
                "quantita": float(it.get("quantita") or 0),
                "unita_misura": unita_i
            })

    supabase.table("ricette_ingredienti").delete().eq("id_ricetta", id_ricetta).execute()
    if righe_ri_bulk:
        try:
            supabase.table("ricette_ingredienti").insert(righe_ri_bulk).execute()
        except Exception:
            righe_ri_bulk.clear()
            ids_ing_usati.clear()
            for it in lista_items:
                nome_i = (it.get("nome") or "").strip()
                unita_i = it.get("unita") or "g"
                id_ing = ottieni_o_crea_ing_sicuro(nome_i, unita_i, is_detersivo=False, ignora_cache=True)
                if id_ing and id_ing not in ids_ing_usati:
                    ids_ing_usati.add(id_ing)
                    righe_ri_bulk.append({
                        "id_ricetta": id_ricetta,
                        "id_ingrediente": id_ing,
                        "quantita": float(it.get("quantita") or 0),
                        "unita_misura": unita_i
                    })
            if righe_ri_bulk:
                supabase.table("ricette_ingredienti").insert(righe_ri_bulk).execute()

    CACHE_ANAGRAFICA["conteggio_ing_ricette"][str(id_ricetta)] = len(righe_ri_bulk)
    return len(righe_ri_bulk)


# ==========================================================
# MOTORE DI RICERCA UNIFICATO: PRIMA SITI WEB -> ULTIMA SOLUZIONE GEMINI AI
# ==========================================================
def analizza_stringa_ingrediente_web_base(testo_raw):
    testo = html.unescape(str(testo_raw or "")).strip()
    testo = re.sub(r"<[^>]+>", " ", testo)
    testo = re.sub(r"\([^)]*\)", " ", testo)
    testo = re.sub(r"\s+", " ", testo).strip()
    if not testo or len(testo) > 95:
        return None

    low = testo.lower()
    if "q.b." in low or "quanto basta" in low or re.search(r"\bqb\b", low):
        nome_pulito = re.sub(r"(?i)\b(q\.?b\.?|quanto basta)\b", "", testo).strip(" ,.-:")
        nome_pulito = re.sub(r"(?i)^(di|d'|del|della|dei|degli|delle)\s+", "", nome_pulito).strip()
        if nome_pulito:
            return {"nome": nome_pulito.capitalize(), "qta_base": 0.0, "unita": "q.b."}

    mappa_unita = [
        (r"\b(kg|chili|chilo|chilogrammi)\b", "kg"),
        (r"\b(g|gr|grammi|grammo)\b", "g"),
        (r"\b(ml|millilitri)\b", "ml"),
        (r"\b(l|lt|litri|litro)\b", "l"),
        (r"\b(cucchiai|cucchiaio|cucchiaini|cucchiaino)\b", "cucchiai"),
        (r"\b(fette|fetta)\b", "fette"),
        (r"\b(tazze|tazza|bicchieri|bicchiere)\b", "tazze"),
        (r"\b(spicchi|spicchio|pz|pezzi|pezzo|bustina|bustine|rametto|rametti|foglie|foglia|uova|uovo)\b", "pz"),
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
            if not re.search(r"\b(uova|uovo)\b", testo_senza_num, flags=re.IGNORECASE):
                testo_senza_num = re.sub(pattern, " ", testo_senza_num, count=1, flags=re.IGNORECASE).strip()
            break

    nome_finale = re.sub(r"\s+", " ", testo_senza_num).strip(" ,.-:/")
    nome_finale = re.sub(r"(?i)^(di|d'|del|della|dei|degli|delle|per)\s+", "", nome_finale).strip()
    nome_finale = re.sub(r"(?i)\s+(di|d'|a|per)$", "", nome_finale).strip()

    if not nome_finale or len(nome_finale) < 2:
        return None
    if unita_trovata == "q.b." or qta_base <= 0:
        qta_base = 0.0
        unita_trovata = "q.b."

    return {
        "nome": nome_finale[:60].capitalize(),
        "qta_base": qta_base,
        "unita": unita_trovata
    }


def scala_ingredienti_candidato(candidato_web: dict, num_persone_target: int):
    porzioni_base = max(1, int(candidato_web.get("porzioni_base") or 4))
    fattore = float(max(1, num_persone_target)) / float(porzioni_base)
    scalati = []
    for ing_b in candidato_web.get("ingredienti_base", []):
        q_b = float(ing_b.get("qta_base") or 0)
        uni = ing_b.get("unita") or "g"
        if uni == "q.b." or q_b <= 0:
            q_fin = 0.0
            uni = "q.b."
        else:
            q_fin = q_b * fattore
            if uni in ["g", "ml"] and q_fin >= 20:
                q_fin = round(q_fin / 5.0) * 5.0
            elif uni == "pz":
                q_fin = max(1.0, round(q_fin))
            else:
                q_fin = round(q_fin, 2)
        scalati.append({
            "nome": ing_b["nome"],
            "quantita": q_fin,
            "unita": uni
        })
    return scalati


def genera_soluzione_ai_locale(query_raw: str, nome_ricetta_base: str):
    """
    Motore intelligente integrato (usato se GEMINI_API_KEY non è ancora presente su Render o come base AI):
    riconosce le ricette italiane e se scrivi 'il pesto è già pronto' separa sempre Pasta da cuocere + Condimento pronto!
    """
    testo_completo = re.sub(r"\([^)]*\)", " ", f"{nome_ricetta_base or ''} {query_raw or ''}").strip()
    low = f"{nome_ricetta_base or ''} {query_raw or ''}".lower()

    basi_primi = [
        "spaghetti", "linguine", "tagliatelle", "rigatoni", "penne", "pennette", "fusilli",
        "orecchiette", "paccheri", "farfalle", "tortellini", "ravioli", "gnocchi", "gnocchetti",
        "trofie", "bavette", "mezze maniche", "pasta", "risotto", "riso"
    ]
    base_trovata = next((b.capitalize() for b in basi_primi if re.search(rf"\b{b}\b", low)), None)
    url_google_ai = f"https://www.google.com/search?q={urllib.parse.quote_plus((nome_ricetta_base or query_raw) + ' ricetta semplice')}"

    if "assassina" in low:
        ings = [
            {"nome": "Spaghetti", "qta_base": 350.0, "unita": "g"},
            {"nome": "Passata di pomodoro", "qta_base": 400.0, "unita": "g"},
            {"nome": "Concentrato di pomodoro", "qta_base": 50.0, "unita": "g"},
            {"nome": "Aglio", "qta_base": 2.0, "unita": "pz"},
            {"nome": "Peperoncino", "qta_base": 0.0, "unita": "q.b."},
            {"nome": "Olio extravergine d'oliva", "qta_base": 0.0, "unita": "q.b."}
        ]
        prep = "Rosolare aglio e peperoncino, risottare gli spaghetti crudi in padella con brodo di pomodoro fino a bruciacchiarli."
    elif "telline" in low or "vongole" in low or "arselle" in low:
        frutto_mare = "Telline fresche" if "telline" in low else ("Arselle" if "arselle" in low else "Vongole")
        ings = [
            {"nome": base_trovata or "Linguine", "qta_base": 350.0, "unita": "g"},
            {"nome": frutto_mare, "qta_base": 800.0, "unita": "g"},
            {"nome": "Aglio", "qta_base": 2.0, "unita": "pz"},
            {"nome": "Vino bianco secco", "qta_base": 100.0, "unita": "ml"},
            {"nome": "Prezzemolo", "qta_base": 0.0, "unita": "q.b."},
            {"nome": "Olio extravergine d'oliva", "qta_base": 0.0, "unita": "q.b."}
        ]
        prep = f"Far aprire le {frutto_mare.lower()} in padella con olio, aglio e vino bianco; filtrare il liquido e mantecarvi la pasta."
    elif "pesto" in low:
        ings = [
            {"nome": base_trovata or "Pasta", "qta_base": 350.0, "unita": "g"},
            {"nome": "Pesto pronto", "qta_base": 190.0, "unita": "g"},
            {"nome": "Parmigiano grattugiato", "qta_base": 40.0, "unita": "g"}
        ]
        prep = "Cuocere la pasta in acqua salata e condirla fuori dal fuoco con il pesto già pronto allungato con un cucchiaio di acqua di cottura."
    elif any(k in low for k in ["ragù", "ragu", "sugo", "bolognese"]):
        ings = [
            {"nome": base_trovata or "Tagliatelle", "qta_base": 350.0, "unita": "g"},
            {"nome": "Ragù pronto" if "pronto" in low else "Carne macinata", "qta_base": 350.0, "unita": "g"},
            {"nome": "Passata di pomodoro", "qta_base": 500.0, "unita": "g"},
            {"nome": "Parmigiano grattugiato", "qta_base": 40.0, "unita": "g"}
        ]
        prep = "Rosolare il soffritto con la carne macinata, unire la passata di pomodoro e cuocere a fuoco dolce, poi condire la pasta."
    elif "salmone" in low and base_trovata:
        ings = [
            {"nome": base_trovata, "qta_base": 500.0 if "gnocc" in base_trovata.lower() else 350.0, "unita": "g"},
            {"nome": "Salmone affumicato", "qta_base": 150.0, "unita": "g"},
            {"nome": "Panna da cucina", "qta_base": 200.0, "unita": "ml"}
        ]
        prep = "Rosolare il salmone a listarelle, unire la panna e saltare gli gnocchi o la pasta appena scolata."
    elif "pizza di patate" in low or "gateau" in low or "tortino di patate" in low:
        ings = [
            {"nome": "Patate", "qta_base": 1000.0, "unita": "g"},
            {"nome": "Mozzarella / Provola", "qta_base": 200.0, "unita": "g"},
            {"nome": "Prosciutto cotto", "qta_base": 120.0, "unita": "g"},
            {"nome": "Uova", "qta_base": 2.0, "unita": "pz"},
            {"nome": "Parmigiano grattugiato", "qta_base": 50.0, "unita": "g"}
        ]
        prep = "Schiacciare le patate lesse, amalgamare con uova, parmigiano, mozzarella e prosciutto e cuocere in forno con pangrattato."
    elif "piadina" in low:
        ings = [
            {"nome": "Piadine pronte", "qta_base": 4.0, "unita": "pz"},
            {"nome": "Mozzarella", "qta_base": 200.0, "unita": "g"},
            {"nome": "Passata di pomodoro", "qta_base": 150.0, "unita": "g"},
            {"nome": "Prosciutto cotto", "qta_base": 150.0, "unita": "g"}
        ]
        prep = "Condire le piadine con pomodoro, mozzarella e prosciutto e scaldare in padella o forno finché fila."
    elif base_trovata:
        condimento = re.sub(rf"(?i)\b{base_trovata}\b", "", testo_completo)
        condimento = re.sub(r"(?i)^(con|al|alla|allo|ai|agli|alle|e|ed)\s+", "", condimento.strip()).strip().capitalize()
        ings = [{"nome": base_trovata, "qta_base": 350.0, "unita": "g"}]
        if condimento and len(condimento) >= 2:
            ings.append({"nome": condimento, "qta_base": 300.0, "unita": "g"})
        prep = f"Cuocere {base_trovata.lower()} e condire con {condimento.lower() if condimento else 'il condimento scelto'}."
    else:
        nome_pulito = re.sub(r"\([^)]*\)", "", nome_ricetta_base or query_raw).strip().capitalize()
        ings = [{"nome": nome_pulito, "qta_base": 600.0, "unita": "g"}]
        prep = "Soluzione rapida per inserire direttamente il piatto nella lista della spesa."

    return {
        "titolo": f"✨ {(nome_ricetta_base or query_raw).capitalize()} (Soluzione AI Gemini)",
        "sito": "🤖 Intelligenza Artificiale Gemini",
        "preparazione_breve": prep,
        "url_fonte": url_google_ai,
        "foto_url": "",
        "porzioni_base": 4,
        "ingredienti_base": ings
    }


def chiedi_ricetta_a_gemini_ai(query_ricerca: str, nome_ricetta_originale: str = ""):
    """
    Genera la soluzione di Gemini AI (tramite API se GEMINI_API_KEY è impostata su Render,
    altrimenti tramite il motore AI locale intelligente).
    """
    api_key = os.environ.get("GEMINI_API_KEY", "").strip() or GEMINI_API_KEY
    if not api_key:
        return genera_soluzione_ai_locale(query_ricerca, nome_ricetta_originale)

    prompt = f"""Sei uno chef di cucina casalinga italiana. L'utente deve fare la spesa per preparare: "{query_ricerca}" (piatto: "{nome_ricetta_originale}").
Restituisci gli ingredienti semplici, tradizionali ed essenziali calibrati per 4 persone.
REGOLE:
1. Se l'utente specifica che il condimento è già pronto/comprato (es. "pasta e pesto ma il pesto è già pronto"), NON mettere basilico o pinoli, ma metti la base da cuocere (es. "Pasta" 350 g) + il condimento già pronto (es. "Pesto pronto" 190 g).
2. Se è un piatto da cucinare (es. "Linguine con telline", "Spaghetti all'assassina", "Tagliatelle al ragù"), elenca tutti gli ingredienti semplici necessari.
3. Unità ammesse: "g", "kg", "ml", "l", "pz", "fette", "cucchiai", "tazze", "q.b.". Se usi "q.b.", imposta qta_base a 0.
Rispondi SOLO con un oggetto JSON valido:
{{"titolo": "Nome Piatto", "preparazione_breve": "Spiegazione di 2 righe su come si prepara", "porzioni_base": 4, "ingredienti_base": [{{"nome": "Linguine", "qta_base": 350, "unita": "g"}}]}}"""

    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2}
    }).encode("utf-8")

    for model_name in ["gemini-2.5-flash", "gemini-2.0-flash"]:
        try:
            url_api = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
            req = urllib.request.Request(url_api, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=7) as resp:
                risp_json = json.loads(resp.read().decode("utf-8", errors="ignore"))
            testo_out = risp_json["candidates"][0]["content"]["parts"][0]["text"]
            dati_ai = json.loads(testo_out)
            ings_validi = []
            for item in dati_ai.get("ingredienti_base", []):
                u = item.get("unita", "g")
                if u not in UNITA_MISURA:
                    u = "g"
                ings_validi.append({
                    "nome": str(item.get("nome", "")).strip().capitalize(),
                    "qta_base": float(item.get("qta_base") or 0),
                    "unita": u
                })
            if ings_validi:
                q_enc = urllib.parse.quote_plus((nome_ricetta_originale or query_ricerca) + " ricetta")
                return {
                    "titolo": f"✨ {dati_ai.get('titolo') or nome_ricetta_originale} (Soluzione AI Gemini)",
                    "sito": "🤖 Intelligenza Artificiale Gemini",
                    "preparazione_breve": dati_ai.get("preparazione_breve", ""),
                    "url_fonte": f"https://www.google.com/search?q={q_enc}",
                    "foto_url": "",
                    "porzioni_base": int(dati_ai.get("porzioni_base") or 4),
                    "ingredienti_base": ings_validi
                }
        except Exception as ex:
            print(f"Nota chiamata Gemini ({model_name}):", ex)
            continue

    return genera_soluzione_ai_locale(query_ricerca, nome_ricetta_originale)


def estrai_info_complete_da_pagina_ricetta(html_page, url_pagina):
    foto_og = ""
    m_og_img = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html_page, flags=re.IGNORECASE)
    if m_og_img:
        foto_og = m_og_img.group(1).strip()

    titolo_og = ""
    m_og_tit = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html_page, flags=re.IGNORECASE)
    if m_og_tit:
        titolo_og = html.unescape(m_og_tit.group(1)).strip()

    blocchi = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html_page, flags=re.DOTALL | re.IGNORECASE)
    for b in blocchi:
        try:
            dati = json.loads(b.strip())
            candidati = dati if isinstance(dati, list) else (dati.get("@graph", [dati]) if isinstance(dati, dict) else [])
            for item in candidati:
                if not isinstance(item, dict):
                    continue
                tipo = item.get("@type")
                if ((tipo == "Recipe") or (isinstance(tipo, list) and "Recipe" in tipo)) and item.get("recipeIngredient"):
                    porzioni_base = 4
                    yield_val = item.get("recipeYield")
                    if yield_val:
                        m_y = re.search(r"(\d+)", str(yield_val[0] if isinstance(yield_val, list) else yield_val))
                        if m_y and 1 <= int(m_y.group(1)) <= 30:
                            porzioni_base = int(m_y.group(1))
                    titolo_ric = html.unescape(str(item.get("name") or titolo_og or "Ricetta")).strip()
                    img_val = item.get("image")
                    foto_ric = foto_og
                    if isinstance(img_val, str) and img_val.startswith("http"):
                        foto_ric = img_val
                    elif isinstance(img_val, list) and img_val:
                        foto_ric = img_val[0] if isinstance(img_val[0], str) else img_val[0].get("url", foto_og)
                    elif isinstance(img_val, dict):
                        foto_ric = img_val.get("url", foto_og)
                    return {
                        "titolo": titolo_ric,
                        "porzioni_base": porzioni_base,
                        "raw_ings": item.get("recipeIngredient", []),
                        "foto_url": foto_ric,
                        "url_fonte": url_pagina
                    }
        except Exception:
            continue

    righe_li = re.findall(r'<li[^>]*class=["\'][^"\']*ingredient[^"\']*["\'][^>]*>(.*?)</li>', html_page, flags=re.DOTALL | re.IGNORECASE)
    if not righe_li:
        m_sez_ing = re.search(r'(?i)<h[234][^>]*>[^<]*ingredienti[^<]*</h[234]>\s*<ul[^>]*>(.*?)</ul>', html_page, flags=re.DOTALL)
        if m_sez_ing:
            righe_li = re.findall(r'<li[^>]*>(.*?)</li>', m_sez_ing.group(1), flags=re.DOTALL)
    if righe_li and len(righe_li) >= 2:
        raw_ings = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", x)).strip() for x in righe_li]
        return {
            "titolo": re.sub(r"\s*[-|].*$", "", titolo_og) or "Ricetta dal Blog",
            "porzioni_base": 4,
            "raw_ings": raw_ings[:15],
            "foto_url": foto_og,
            "url_fonte": url_pagina
        }
    return None


def cerca_ricette_su_internet_dettagliate(query_ricerca: str, nome_ricetta_originale: str = ""):
    """
    Restituisce PRIMA tutte le soluzioni trovate sui siti web (GialloZafferano, Blog GZ, Cucchiaio, Misya)
    e DIRETTAMENTE COME ULTIMA SOLUZIONE quella elaborata da Gemini AI!
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "it-IT,it;q=0.9"
    }
    low_q = query_ricerca.lower()

    query_pulita = re.sub(r"\([^)]*\)", " ", query_ricerca)
    query_pulita = re.sub(r"(?i)\b(il|la|lo|è|gia|già|pronto|pronta|comprato|comprata)\b", " ", query_pulita)
    query_pulita = re.sub(r"\s+", " ", query_pulita).strip() or query_ricerca.strip()

    queries_da_provare = [query_pulita]
    if "pizza di patate" in query_pulita.lower() or "tortino di patate" in query_pulita.lower():
        queries_da_provare.append("gateau di patate")
    elif "tagliatelle al sugo" in query_pulita.lower() or "tagliatelle al rag" in query_pulita.lower():
        queries_da_provare.append("tagliatelle al ragu alla bolognese")
    elif "gnocchetti" in query_pulita.lower():
        queries_da_provare.append(re.sub(r"(?i)gnocchetti", "gnocchi", query_pulita))
    elif "telline" in query_pulita.lower():
        queries_da_provare.append("pasta con le telline")

    parole_chiave = [
        w.lower() for w in re.findall(r"[a-zA-Zàèéìòù]{3,}", query_pulita)
        if w.lower() not in {"con", "alle", "alla", "agli", "del", "della", "dei"}
    ]

    urls_ricerca = []
    for qp in queries_da_provare:
        q_enc = urllib.parse.quote_plus(qp)
        urls_ricerca.append(f"https://www.giallozafferano.it/ricerca-ricette/{q_enc}/")
        urls_ricerca.append(f"https://www.cucchiaio.it/ricerca.html?q={q_enc}")
    urls_ricerca.append(
        f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(query_pulita + ' ricetta giallozafferano OR cucchiaio OR misya')}"
    )

    pattern_links = (
        r'https://(?:ricette|blog)\.giallozafferano\.it/[a-zA-Z0-9\-_/]+\.html|'
        r'https://blog\.giallozafferano\.it/[a-zA-Z0-9\-_]+/[a-zA-Z0-9\-_]+/?|'
        r'https://www\.cucchiaio\.it/ricetta/[a-zA-Z0-9\-_]+/?|'
        r'https://www\.misya\.info/ricetta/[a-zA-Z0-9\-_]+\.htm|'
        r'https://www\.fattoincasadabenedetta\.it/ricetta/[a-zA-Z0-9\-_]+/?'
    )

    links_candidati = []
    for u_search in urls_ricerca[:4]:
        try:
            req = urllib.request.Request(u_search, headers=headers)
            with urllib.request.urlopen(req, timeout=6) as resp:
                html_s = html.unescape(resp.read().decode("utf-8", errors="ignore"))
            html_s = urllib.parse.unquote(html_s)
            for lk in re.findall(pattern_links, html_s):
                if lk not in links_candidati and "ricerca" not in lk and "categoria" not in lk:
                    links_candidati.append(lk)
        except Exception:
            continue

    parole_indesiderate = ["bianco", "capriolo", "anatra", "cinghiale", "lepre", "broccoli", "gamberi", "astice", "vegan"]

    def calcola_score_link(lk):
        slug = lk.rstrip("/").split("/")[-1].lower()
        score = sum(12 for pk in parole_chiave if pk[:4] in slug)
        for p_bad in parole_indesiderate:
            if p_bad in slug and p_bad not in low_q:
                score -= 25
        score -= len(slug) * 0.05
        return score

    links_candidati.sort(key=calcola_score_link, reverse=True)

    # 1. PRIMA LE SOLUZIONI TROVATE SUI SITI WEB DI CUCINA
    risultati_trovati = []
    for link in links_candidati[:4]:
        try:
            req_r = urllib.request.Request(link, headers=headers)
            with urllib.request.urlopen(req_r, timeout=6) as resp_r:
                html_r = resp_r.read().decode("utf-8", errors="ignore")
            info = estrai_info_complete_da_pagina_ricetta(html_r, link)
            if info and info.get("raw_ings"):
                ings_base = []
                nomi_visti = set()
                for r_str in info["raw_ings"]:
                    parsed = analizza_stringa_ingrediente_web_base(r_str)
                    if parsed and parsed["nome"].lower() not in nomi_visti:
                        nomi_visti.add(parsed["nome"].lower())
                        ings_base.append(parsed)
                if ings_base:
                    sito_nome = (
                        "GialloZafferano" if "giallozafferano" in link else
                        ("Misya" if "misya" in link else
                         ("Fatto in casa da Benedetta" if "benedetta" in link else "Cucchiaio d'Argento"))
                    )
                    risultati_trovati.append({
                        "titolo": info["titolo"],
                        "sito": sito_nome,
                        "url_fonte": link,
                        "foto_url": info["foto_url"],
                        "porzioni_base": info["porzioni_base"] or 4,
                        "ingredienti_base": ings_base
                    })
        except Exception:
            continue

    # 2. DIRETTAMENTE COME ULTIMA SOLUZIONE: QUELLA TROVATA DA GEMINI AI
    soluzione_gemini = chiedi_ricetta_a_gemini_ai(query_ricerca, nome_ricetta_originale or query_pulita)
    if soluzione_gemini:
        risultati_trovati.append(soluzione_gemini)

    return risultati_trovati


def ricalibra_ingredienti_ricetta_db(id_ricetta: str, vecchie_persone: int, nuove_persone: int, meta_attuale: dict):
    if not is_valid_uuid(id_ricetta):
        return
    vecchie_p = max(1, int(vecchie_persone or 8))
    nuove_p = max(1, int(nuove_persone or 8))
    fattore = float(nuove_p) / float(vecchie_p)

    res_ri = supabase.table("ricette_ingredienti").select("*").eq("id_ricetta", id_ricetta).execute()
    for r in (res_ri.data or []):
        q_attuale = float(r.get("quantita") or 0)
        unita = r.get("unita_misura") or "g"
        if q_attuale > 0 and unita != "q.b.":
            nuova_q = q_attuale * fattore
            if unita in ["g", "ml"] and nuova_q >= 20:
                nuova_q = round(nuova_q / 5.0) * 5.0
            elif unita == "pz":
                nuova_q = round(nuova_q, 1)
            else:
                nuova_q = round(nuova_q, 2)
            supabase.table("ricette_ingredienti").update({"quantita": nuova_q}).eq("id", r["id"]).execute()

    salva_meta_ricetta_db(
        id_ricetta,
        meta_attuale.get("portata", "Primo"),
        nuove_p,
        meta_attuale.get("url_fonte", ""),
        meta_attuale.get("foto_url", "")
    )


# ==========================================================
# POPUP RINOMINA INGREDIENTE E GESTIONE RICETTA
# ==========================================================
def apri_popup_rinomina_ingrediente(page: ft.Page, ing_obj: dict, lista_ingredienti: list, on_done_callback):
    txt_nome = ft.TextField(label="Nuovo nome ingrediente", value=ing_obj.get("nome", ""), dense=True, text_size=13)
    sel_unita = crea_selettore_unita(page, valore_iniziale=ing_obj.get("unita_default", "g"), width=90, height=36, text_size=12)
    txt_err = ft.Text("", color=ft.Colors.RED_700, size=11, visible=False)
    in_salvataggio = [False]
    dlg_mod = None

    def salva_mod(e):
        if in_salvataggio[0]:
            return
        nuovo_nome = (txt_nome.value or "").strip().capitalize()
        if not nuovo_nome:
            txt_err.value = "⚠️ Inserisci un nome valido."
            txt_err.visible = True
            page.update()
            return
        in_salvataggio[0] = True
        chiudi_dialog_sicuro(page, dlg_mod)

        def lavoro():
            try:
                unita_scelta = sel_unita.get_value()
                unita_db = f"{unita_scelta}|DET" if ing_obj.get("is_detersivo") else unita_scelta
                conflitto = next(
                    (i for i in lista_ingredienti if i["nome"].lower() == nuovo_nome.lower() and str(i["id"]) != str(ing_obj["id"])),
                    None
                )
                if conflitto:
                    supabase.table("ricette_ingredienti").update({"id_ingrediente": conflitto["id"]}).eq("id_ingrediente", ing_obj["id"]).execute()
                    supabase.table("menu_giornalieri").update({"id_ingrediente": conflitto["id"]}).eq("id_ingrediente", ing_obj["id"]).execute()
                    supabase.table("ingredienti").delete().eq("id", ing_obj["id"]).execute()
                else:
                    supabase.table("ingredienti").update({"nome": nuovo_nome, "unita_default": unita_db}).eq("id", ing_obj["id"]).execute()
                assicura_cache_anagrafica(forza_ricarica=True)
                if on_done_callback:
                    on_done_callback()
            except Exception as ex:
                print("Errore rinomina:", ex)

        threading.Thread(target=lavoro, daemon=True).start()

    dlg_mod = ft.AlertDialog(
        title=ft.Text("✏️ Modifica Ingrediente", size=15, weight=ft.FontWeight.BOLD),
        content=ft.Container(
            content=ft.Column([
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


def apri_popup_gestione_ricetta(page: ft.Page, id_ricetta: str, nome_attuale: str, portata_attuale: str, on_done_callback):
    ric_cache = next((r for r in (CACHE_ANAGRAFICA["ricette"] or []) if str(r.get("id")) == str(id_ricetta)), None)
    meta = estrai_meta_ricetta(ric_cache or {"portata_default": portata_attuale})
    txt_nome_ric = ft.TextField(label="Nome Ricetta", value=nome_attuale, dense=True, text_size=13)
    stato_portata = {"val": meta["portata"]}
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
    dlg_ric = None

    def salva_ricetta_db(e):
        nuovo_n = (txt_nome_ric.value or "").strip().capitalize()
        if not nuovo_n:
            return
        chiudi_dialog_sicuro(page, dlg_ric)

        def lavoro():
            salva_meta_ricetta_db(id_ricetta, stato_portata["val"], meta["porzioni"], meta["url_fonte"], meta["foto_url"], nuovo_nome=nuovo_n)
            if on_done_callback:
                on_done_callback(nuovo_n)

        threading.Thread(target=lavoro, daemon=True).start()

    def elimina_ricetta_definitivamente(e):
        chiudi_dialog_sicuro(page, dlg_ric)

        def lavoro():
            if is_valid_uuid(id_ricetta):
                supabase.table("ricette").delete().eq("id", id_ricetta).execute()
            if CACHE_ANAGRAFICA["ricette"]:
                CACHE_ANAGRAFICA["ricette"] = [r for r in CACHE_ANAGRAFICA["ricette"] if str(r.get("id")) != str(id_ricetta)]
            if on_done_callback:
                on_done_callback(None)

        threading.Thread(target=lavoro, daemon=True).start()

    dlg_ric = ft.AlertDialog(
        title=ft.Text("🍲 Gestione Ricetta", size=15, weight=ft.FontWeight.BOLD),
        content=ft.Container(
            content=ft.Column([
                txt_nome_ric,
                ft.Row([ft.Text("Portata predefinita:", size=12), sel_portata], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Text("'Elimina dal DB' cancella definitivamente la ricetta dal ricettario.", size=10, color=ft.Colors.RED_700, italic=True)
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
# POPUP DETTAGLIO RICETTA (LINK FUNZIONANTI AL 100% + GEMINI AI + SCOMPOSIZIONE BASE/CONDIMENTO)
# ==========================================================
def apri_popup_dettaglio_ricetta_semplificata(page: ft.Page, id_voce_menu: str, id_ricetta_init: str, nome_ricetta_init: str, portata: str, on_close_refresh):
    if not supabase:
        return

    assicura_cache_anagrafica(forza_ricarica=False)
    spinner_det = ft.ProgressRing(width=18, height=18, stroke_width=2.5, color=ft.Colors.WHITE)
    lbl_det_status = ft.Text("Caricamento ricetta...", size=11, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD)
    banner_det = ft.Container(
        content=ft.Row([spinner_det, lbl_det_status], alignment=ft.MainAxisAlignment.CENTER, spacing=8),
        bgcolor=ft.Colors.ORANGE_800, padding=6, border_radius=6, visible=True
    )

    stato_popup = {
        "id_ricetta": str(id_ricetta_init or ""),
        "id_voce_menu": str(id_voce_menu or ""),
        "nome_ricetta": nome_ricetta_init,
        "query_web": nome_ricetta_init,
        "persone_web": 8,
        "mostra_web": False,
        "ricerca_auto_fatta": False,
        "risultati_web": [],
        "idx_web": 0
    }

    txt_titolo_header = ft.Text(f"🍲 {nome_ricetta_init}", size=15, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900, expand=True)
    col_corpo = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)
    dlg_det = None

    def chiudi_det(e=None):
        chiudi_dialog_sicuro(page, dlg_det)
        if on_close_refresh:
            on_close_refresh()

    def togli_dal_menu_giorno(e):
        id_vm = stato_popup["id_voce_menu"]
        chiudi_dialog_sicuro(page, dlg_det)
        if is_valid_uuid(id_vm):
            def w():
                supabase.table("menu_giornalieri").delete().eq("id", id_vm).execute()
                if on_close_refresh:
                    on_close_refresh()
            threading.Thread(target=w, daemon=True).start()

    def dopo_rinomina_o_elimina(nuovo_nome_ric):
        if nuovo_nome_ric is None:
            chiudi_det()
            return
        stato_popup["nome_ricetta"] = nuovo_nome_ric
        stato_popup["query_web"] = nuovo_nome_ric
        txt_titolo_header.value = f"🍲 {nuovo_nome_ric}"
        if on_close_refresh:
            on_close_refresh()
        ricarica_ingredienti_ricetta()

    azioni_header = [
        txt_titolo_header,
        ft.IconButton(
            icon=ft.Icons.EDIT, icon_color=ft.Colors.ORANGE_800, icon_size=18, tooltip="Rinomina o Elimina dal DB",
            on_click=lambda e: apri_popup_gestione_ricetta(page, stato_popup["id_ricetta"], stato_popup["nome_ricetta"], portata, dopo_rinomina_o_elimina)
        )
    ]
    if id_voce_menu:
        azioni_header.append(
            ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_color=ft.Colors.RED_700, icon_size=18, tooltip="Togli piatto da questo giorno", on_click=togli_dal_menu_giorno)
        )

    dlg_det = ft.AlertDialog(
        title=ft.Column([ft.Row(azioni_header, spacing=2), banner_det], spacing=4, tight=True),
        content=ft.Container(content=col_corpo, width=368, height=520),
        actions=[
            ft.Container(
                content=ft.Text("Chiudi", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=12),
                bgcolor=ft.Colors.BLUE_900, padding=ft.Padding(14, 8, 14, 8), border_radius=6, on_click=chiudi_det
            )
        ],
        shape=ft.RoundedRectangleBorder(radius=10), content_padding=10, inset_padding=ft.Padding(8, 10, 8, 10)
    )
    apri_dialog_sicuro(page, dlg_det)

    def assicura_id_ricetta_reale():
        id_curr = stato_popup["id_ricetta"]
        if is_valid_uuid(id_curr):
            res_chk = supabase.table("ricette").select("id").eq("id", id_curr).limit(1).execute()
            if res_chk.data:
                return id_curr
        ric_creata = ottieni_o_crea_ricetta_sicura(stato_popup["nome_ricetta"], portata, ignora_cache=True)
        vero_id = str(ric_creata["id"])
        stato_popup["id_ricetta"] = vero_id
        return vero_id

    def ricarica_ingredienti_ricetta(msg_extra=None):
        banner_det.visible = True
        lbl_det_status.value = "Lettura ricetta..."
        page.update()

        def worker():
            try:
                id_ric = assicura_id_ricetta_reale()
                res_ric_db = supabase.table("ricette").select("*").eq("id", id_ric).execute()
                ric_corrente = res_ric_db.data[0] if res_ric_db.data else {"id": id_ric, "nome": stato_popup["nome_ricetta"], "portata_default": portata}
                stato_popup["nome_ricetta"] = ric_corrente.get("nome", stato_popup["nome_ricetta"])
                meta = estrai_meta_ricetta(ric_corrente)
                porzioni_attuali = meta["porzioni"]
                url_fonte_attuale = meta["url_fonte"]
                foto_url_attuale = meta["foto_url"]
                txt_titolo_header.value = f"🍲 {stato_popup['nome_ricetta']}"

                res_ri = supabase.table("ricette_ingredienti").select("*, ingredienti(id, nome, unita_default)").eq("id_ricetta", id_ric).execute()
                righe_ri = res_ri.data if res_ri.data else []
                ha_ing = len(righe_ri) > 0
                CACHE_ANAGRAFICA["conteggio_ing_ricette"][str(id_ric)] = len(righe_ri)

                if not ha_ing and not stato_popup["ricerca_auto_fatta"]:
                    stato_popup["mostra_web"] = True
                    stato_popup["ricerca_auto_fatta"] = True
                    lbl_det_status.value = f"🤖 Ricerca AI / Web '{stato_popup['query_web']}'..."
                    page.update()
                    stato_popup["risultati_web"] = cerca_ricette_su_internet_dettagliate(stato_popup["query_web"], stato_popup["nome_ricetta"])
                    stato_popup["idx_web"] = 0

                col_corpo.controls.clear()

                if msg_extra:
                    col_corpo.controls.append(
                        ft.Container(
                            content=ft.Text(f"✅ {msg_extra}", size=11, color=ft.Colors.GREEN_800, weight=ft.FontWeight.BOLD),
                            bgcolor="#E8F5E9", padding=6, border_radius=6
                        )
                    )

                # 1. AGGIUNTA RAPIDA MANUALE (SEMPRE VISIBILE IN CIMA)
                txt_nuovo_i = ft.TextField(
                    label="➕ Aggiungi singolo ingrediente a mano...",
                    value="",
                    hint_text="Es. Telline, Pane, Bragiolette...",
                    dense=True, text_size=11, expand=True
                )
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
                        matches = [i for i in (CACHE_ANAGRAFICA.get("ingredienti") or []) if not i.get("is_detersivo") and t in i["nome"].lower()][:6]
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

                def add_manuale(e=None):
                    n_val = (txt_nuovo_i.value or "").strip()
                    if not n_val:
                        return
                    q_v = parse_qta(box_nuova_q.textfield.value)
                    u_v = sel_nuova_u.get_value()
                    banner_det.visible = True
                    lbl_det_status.value = f"⚡ Salvataggio '{n_val}'..."
                    page.update()

                    def w_add():
                        try:
                            id_i = ottieni_o_crea_ing_sicuro(n_val, u_v, is_detersivo=False)
                            if id_i:
                                gia_es = supabase.table("ricette_ingredienti").select("id").eq("id_ricetta", id_ric).eq("id_ingrediente", id_i).execute()
                                if gia_es.data:
                                    supabase.table("ricette_ingredienti").update({"quantita": q_v, "unita_misura": u_v}).eq("id", gia_es.data[0]["id"]).execute()
                                else:
                                    try:
                                        supabase.table("ricette_ingredienti").insert({
                                            "id_ricetta": id_ric, "id_ingrediente": id_i, "quantita": q_v, "unita_misura": u_v
                                        }).execute()
                                    except Exception:
                                        id_i_fresh = ottieni_o_crea_ing_sicuro(n_val, u_v, is_detersivo=False, ignora_cache=True)
                                        supabase.table("ricette_ingredienti").insert({
                                            "id_ricetta": id_ric, "id_ingrediente": id_i_fresh, "quantita": q_v, "unita_misura": u_v
                                        }).execute()
                                stato_popup["mostra_web"] = False
                                CACHE_ANAGRAFICA["conteggio_ing_ricette"][str(id_ric)] = CACHE_ANAGRAFICA["conteggio_ing_ricette"].get(str(id_ric), 0) + 1
                                if on_close_refresh:
                                    on_close_refresh()
                                ricarica_ingredienti_ricetta(msg_extra=f"Salvato: {n_val} ({formatta_numero(q_v)} {u_v})")
                        except Exception as ex:
                            print("Errore add_manuale:", ex)
                            banner_det.visible = False
                            page.update()

                    threading.Thread(target=w_add, daemon=True).start()

                txt_nuovo_i.on_submit = add_manuale

                col_corpo.controls.append(
                    ft.Container(
                        content=ft.Column([
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
                        ], spacing=4),
                        bgcolor="#E8F5E9", padding=6, border_radius=8, border=bordo_campo_uniforme("#81C784")
                    )
                )

                # 2. SE LA RICETTA HA INGREDIENTI: RICALCOLO PERSONE + LISTA VISIBILE
                if ha_ing:
                    riga_info_top = []
                    if foto_url_attuale:
                        riga_info_top.append(ft.Image(src=foto_url_attuale, width=52, height=52, border_radius=8))

                    url_link_attivo = url_fonte_attuale or f"https://www.google.com/search?q={urllib.parse.quote_plus(stato_popup['nome_ricetta'] + ' ricetta')}"
                    col_link_e_tit = [
                        ft.Text(f"👥 Dosi attuali calibrate per: {porzioni_attuali} {'persona' if porzioni_attuali == 1 else 'persone'}", size=12, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900),
                        ft.TextButton("🔗 Apri ricetta / preparazione in una nuova scheda", url=url_link_attivo, style=ft.ButtonStyle(padding=0))
                    ]
                    riga_info_top.append(ft.Column(col_link_e_tit, spacing=1, expand=True))

                    box_nuove_persone = crea_box_quantita(valore_iniziale=str(porzioni_attuali), width=88, height=32, text_size=12, mostra_prefisso=True, prefisso_custom="Pers.:")

                    def applica_ricalibrazione_persone(e):
                        nuove_p = int(max(1, parse_qta(box_nuove_persone.textfield.value) or porzioni_attuali))
                        banner_det.visible = True
                        lbl_det_status.value = f"⚖️ Ricalcolo da {porzioni_attuali} a {nuove_p} persone..."
                        page.update()

                        def w_ric():
                            ricalibra_ingredienti_ricetta_db(id_ric, porzioni_attuali, nuove_p, meta)
                            stato_popup["persone_web"] = nuove_p
                            if on_close_refresh:
                                on_close_refresh()
                            ricarica_ingredienti_ricetta(msg_extra=f"Dosi ricalcolate per {nuove_p} persone!")

                        threading.Thread(target=w_ric, daemon=True).start()

                    def toggle_web_panel(e):
                        stato_popup["mostra_web"] = not stato_popup["mostra_web"]
                        if stato_popup["mostra_web"] and not stato_popup["risultati_web"]:
                            stato_popup["risultati_web"] = cerca_ricette_su_internet_dettagliate(stato_popup["query_web"], stato_popup["nome_ricetta"])
                        ricarica_ingredienti_ricetta()

                    col_corpo.controls.append(
                        ft.Container(
                            content=ft.Column([
                                ft.Row(riga_info_top, spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                                ft.Row([
                                    box_nuove_persone,
                                    ft.Container(
                                        content=ft.Text("⚖️ Ricalcola", size=11, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                                        bgcolor=ft.Colors.TEAL_700, height=32, padding=ft.Padding(10, 0, 10, 0),
                                        alignment=ft.Alignment(0, 0), border_radius=5, on_click=applica_ricalibrazione_persone
                                    ),
                                    ft.Container(
                                        content=ft.Text("🤖 / 🌐 Cerca Ricetta", size=11, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                                        bgcolor=ft.Colors.ORANGE_800, height=32, padding=ft.Padding(10, 0, 10, 0),
                                        alignment=ft.Alignment(0, 0), border_radius=5, on_click=toggle_web_panel
                                    )
                                ], spacing=5, alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
                            ], spacing=5),
                            bgcolor="#ECEFF1", padding=7, border_radius=8, border=bordo_campo_uniforme("#B0BEC5")
                        )
                    )

                    for ri in righe_ri:
                        ing_info = normalizza_ingrediente_record(ri.get("ingredienti") or {"id": ri["id_ingrediente"], "nome": "Ingrediente", "unita_default": "g"})
                        nome_i = ing_info.get("nome", "Ingrediente")
                        qta_init = formatta_numero(ri.get("quantita") or 0)
                        unita_init = ri.get("unita_misura") or "g"

                        box_qta_ri = crea_box_quantita(valore_iniziale=qta_init, width=88, height=32, text_size=11, mostra_prefisso=True)
                        sel_uni_ri = crea_selettore_unita(page, valore_iniziale=unita_init, width=72, height=32, text_size=11)

                        def salva_qta_ri(e, id_riga=ri["id"], b_q=box_qta_ri, s_u=sel_uni_ri):
                            banner_det.visible = True
                            lbl_det_status.value = "Salvataggio quantità..."
                            page.update()
                            threading.Thread(
                                target=lambda: (
                                    supabase.table("ricette_ingredienti").update({"quantita": parse_qta(b_q.textfield.value), "unita_misura": s_u.get_value()}).eq("id", id_riga).execute(),
                                    ricarica_ingredienti_ricetta()
                                ),
                                daemon=True
                            ).start()

                        def elim_ing_ri(e, id_riga=ri["id"]):
                            banner_det.visible = True
                            lbl_det_status.value = "Rimozione ingrediente..."
                            page.update()
                            threading.Thread(
                                target=lambda: (
                                    supabase.table("ricette_ingredienti").delete().eq("id", id_riga).execute(),
                                    on_close_refresh() if on_close_refresh else None,
                                    ricarica_ingredienti_ricetta()
                                ),
                                daemon=True
                            ).start()

                        col_corpo.controls.append(
                            ft.Container(
                                content=ft.Column([
                                    ft.Row([
                                        ft.Text(f"▪ {nome_i}", size=12, weight=ft.FontWeight.BOLD, expand=True),
                                        ft.IconButton(icon=ft.Icons.EDIT, icon_color=ft.Colors.ORANGE_800, icon_size=14, on_click=lambda e, item=ing_info: apri_popup_rinomina_ingrediente(page, item, CACHE_ANAGRAFICA.get("ingredienti") or [], ricarica_ingredienti_ricetta)),
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
                                ], spacing=2),
                                bgcolor="#F5F7FA", padding=5, border_radius=5, border=bordo_campo_uniforme("#E0E0E0")
                            )
                        )

                # 3. PANNELLO RICERCA AI GEMINI + SITI CUCINA + SCOMPOSIZIONE CONDIMENTO PRONTO
                if stato_popup["mostra_web"]:
                    tf_query_web = ft.TextField(
                        label="Descrivi il piatto (es. 'Pasta e pesto col pesto già pronto')",
                        value=stato_popup["query_web"],
                        dense=True, text_size=12, expand=True
                    )
                    box_anteprima_candidato = ft.Column(spacing=5)

                    def applica_versione_condimento_pronto(e):
                        p_finale = max(1, int(parse_qta(box_persone_web.textfield.value) or stato_popup["persone_web"] or 8))
                        cand_pronto = genera_scomposizione_intelligente_condimento_pronto(tf_query_web.value or stato_popup["nome_ricetta"], stato_popup["nome_ricetta"])
                        ings_pronti = scala_ingredienti_candidato(cand_pronto, p_finale)
                        banner_det.visible = True
                        lbl_det_status.value = f"⚡ Salvataggio Base + Condimento ({p_finale} pers.)..."
                        page.update()

                        def w_pronto():
                            salva_lista_ingredienti_in_blocco(id_ric, ings_pronti)
                            salva_meta_ricetta_db(id_ric, meta["portata"], p_finale, cand_pronto.get("url_fonte", ""), "")
                            stato_popup["mostra_web"] = False
                            if on_close_refresh:
                                on_close_refresh()
                            ricarica_ingredienti_ricetta(msg_extra=f"Applicata scomposizione rapida per {p_finale} persone!")

                        threading.Thread(target=w_pronto, daemon=True).start()

                    def aggiorna_anteprima_web_live():
                        box_anteprima_candidato.controls.clear()
                        ris_list = stato_popup["risultati_web"]
                        if not ris_list:
                            return
                        idx = min(stato_popup["idx_web"], len(ris_list) - 1)
                        cand = ris_list[idx]
                        p_target = max(1, int(stato_popup["persone_web"] or 8))
                        ings_scalati = scala_ingredienti_candidato(cand, p_target)

                        def cambia_candidato(delta):
                            stato_popup["idx_web"] = (stato_popup["idx_web"] + delta) % len(ris_list)
                            aggiorna_anteprima_web_live()
                            page.update()

                        def importa_candidato_scelto(e):
                            p_finale = max(1, int(parse_qta(box_persone_web.textfield.value) or stato_popup["persone_web"] or 8))
                            ings_da_salvare = scala_ingredienti_candidato(cand, p_finale)
                            banner_det.visible = True
                            lbl_det_status.value = f"⚡ Salvataggio ricetta ({p_finale} pers.)..."
                            page.update()

                            def w_imp():
                                try:
                                    salva_lista_ingredienti_in_blocco(id_ric, ings_da_salvare)
                                    salva_meta_ricetta_db(id_ric, meta["portata"], p_finale, cand.get("url_fonte", ""), cand.get("foto_url", ""))
                                    stato_popup["mostra_web"] = False
                                    if on_close_refresh:
                                        on_close_refresh()
                                    ricarica_ingredienti_ricetta(msg_extra=f"Applicata '{cand['titolo']}' ({p_finale} persone)!")
                                except Exception as ex:
                                    print("Errore importazione ricetta:", ex)
                                    banner_det.visible = False
                                    page.update()

                            threading.Thread(target=w_imp, daemon=True).start()

                        anteprima_ings_str = ", ".join([
                            f"{x['nome']} ({formatta_numero(x['quantita'])} {x['unita']})" if x["unita"] != "q.b." else f"{x['nome']} (q.b.)"
                            for x in ings_scalati[:10]
                        ])

                        url_cand = cand.get("url_fonte") or f"https://www.google.com/search?q={urllib.parse.quote_plus(stato_popup['query_web'] + ' ricetta')}"
                        riga_foto_cand = []
                        if cand.get("foto_url"):
                            riga_foto_cand.append(ft.Image(src=cand["foto_url"], width=68, height=68, border_radius=8))

                        col_testi_cand = [
                            ft.Text(f"📌 {cand['titolo']}", size=12, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900),
                            ft.Text(f"Fonte: {cand['sito']} (dosi per {p_target} pers.)", size=10, color=ft.Colors.GREY_700),
                            ft.TextButton("🔗 Apri ricetta / preparazione in una nuova scheda", url=url_cand, style=ft.ButtonStyle(padding=0))
                        ]
                        if cand.get("preparazione_breve"):
                            col_testi_cand.append(ft.Text(f"👩‍🍳 {cand['preparazione_breve']}", size=10, italic=True, color=ft.Colors.BLUE_GREY_800))

                        riga_foto_cand.append(ft.Column(col_testi_cand, spacing=2, expand=True))

                        nav_risultati = []
                        if len(ris_list) > 1:
                            nav_risultati.append(
                                ft.Row([
                                    ft.TextButton("◀ Prec.", on_click=lambda e: cambia_candidato(-1)),
                                    ft.Text(f"Proposta {idx + 1} di {len(ris_list)}", size=10, weight=ft.FontWeight.BOLD),
                                    ft.TextButton("Altra proposta ▶", on_click=lambda e: cambia_candidato(1))
                                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
                            )

                        box_anteprima_candidato.controls.append(
                            ft.Container(
                                content=ft.Column([
                                    *nav_risultati,
                                    ft.Row(riga_foto_cand, spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                                    ft.Text(f"Ingredienti ({p_target}p): {anteprima_ings_str}", size=10, color=ft.Colors.GREY_800, weight=ft.FontWeight.BOLD),
                                    ft.Container(
                                        content=ft.Text(f"✅ SALVA E APPLICA QUESTA RICETTA ({p_target} PERSONE)", color=ft.Colors.WHITE, size=11, weight=ft.FontWeight.BOLD),
                                        bgcolor=ft.Colors.GREEN_700, padding=8, border_radius=6, alignment=ft.Alignment(0, 0),
                                        on_click=importa_candidato_scelto
                                    )
                                ], spacing=5),
                                bgcolor=ft.Colors.WHITE, padding=8, border_radius=6, border=bordo_campo_uniforme(ft.Colors.GREEN_700)
                            )
                        )

                    def on_change_persone_web(e):
                        stato_popup["persone_web"] = int(max(1, parse_qta(box_persone_web.textfield.value) or 8))
                        aggiorna_anteprima_web_live()
                        page.update()

                    box_persone_web = crea_box_quantita(
                        valore_iniziale=str(stato_popup["persone_web"]), width=92, height=34,
                        text_size=12, mostra_prefisso=True, prefisso_custom="Pers.:", on_change_fn=on_change_persone_web
                    )

                    def lancia_ricerca_internet(e=None):
                        q_str = (tf_query_web.value or "").strip()
                        if not q_str:
                            return
                        stato_popup["query_web"] = q_str
                        stato_popup["persone_web"] = int(max(1, parse_qta(box_persone_web.textfield.value) or 8))
                        banner_det.visible = True
                        lbl_det_status.value = f"🤖 Ricerca AI / Web '{q_str}'..."
                        page.update()

                        def w_search():
                            stato_popup["risultati_web"] = cerca_ricette_su_internet_dettagliate(q_str, stato_popup["nome_ricetta"])
                            stato_popup["idx_web"] = 0
                            ricarica_ingredienti_ricetta()

                        threading.Thread(target=w_search, daemon=True).start()

                    tf_query_web.on_submit = lancia_ricerca_internet
                    aggiorna_anteprima_web_live()

                    ai_attiva = bool(os.environ.get("GEMINI_API_KEY", "").strip() or GEMINI_API_KEY)
                    col_corpo.controls.append(
                        ft.Container(
                            content=ft.Column([
                                ft.Row([
                                    ft.Text("🤖 Ricerca AI Gemini & Web" if ai_attiva else "🌐 Ricerca da Internet", size=12, weight=ft.FontWeight.BOLD, color=ft.Colors.ORANGE_900, expand=True),
                                    ft.Container(
                                        content=ft.Text("🛒 Base + Condimento pronto", size=10, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                                        bgcolor=ft.Colors.TEAL_700, padding=ft.Padding(8, 4, 8, 4), border_radius=6,
                                        on_click=applica_versione_condimento_pronto
                                    )
                                ]),
                                tf_query_web,
                                ft.Row([
                                    box_persone_web,
                                    ft.Container(
                                        content=ft.Text("🔍 CERCA RICETTA", color=ft.Colors.WHITE, size=11, weight=ft.FontWeight.BOLD),
                                        bgcolor=ft.Colors.ORANGE_800, height=34, expand=True, alignment=ft.Alignment(0, 0), border_radius=6,
                                        on_click=lancia_ricerca_internet
                                    )
                                ], spacing=6),
                                box_anteprima_candidato
                            ], spacing=5),
                            bgcolor="#FFF8E1", padding=8, border_radius=8, border=bordo_campo_uniforme("#FB8C00", 1.5)
                        )
                    )

                banner_det.visible = False
                page.update()
            except Exception as ex:
                print("Errore dettaglio ricetta:", ex)
                banner_det.visible = False
                page.update()

        threading.Thread(target=worker, daemon=True).start()

    ricarica_ingredienti_ricetta()


# ==========================================================
# POPUP INSERIMENTO LAMPO PIATTO UNICO (AUTO-RIPARAZIONE SE CANCELLATO DA SUPABASE)
# ==========================================================
def apri_popup_aggiungi_piatto_semplificato(page: ft.Page, data_target: datetime, categoria: str, subcategoria: str, voci_giorno_attuali: list, on_optimistic_add, on_done_callback):
    if not supabase:
        return

    data_str = data_target.strftime("%Y-%m-%d")
    lista_ricette = CACHE_ANAGRAFICA.get("ricette") or []

    txt_piatto = ft.TextField(label=f"Scrivi piatto ({categoria})", dense=True, text_size=13, autofocus=True)
    riga_sugg = ft.Row(wrap=True, spacing=4, run_spacing=4, visible=False)
    salvataggio_in_corso = [False]
    dlg_add = None

    def salva_piatto_scelto(nome_scelto, id_ric_esistente=None):
        if salvataggio_in_corso[0]:
            return
        nome_pulito = (nome_scelto or "").strip().capitalize()
        if not nome_pulito:
            return
        salvataggio_in_corso[0] = True

        chiudi_dialog_sicuro(page, dlg_add)

        match_db = next((r for r in lista_ricette if (id_ric_esistente and str(r["id"]) == str(id_ric_esistente)) or r["nome"].lower() == nome_pulito.lower()), None)

        gia_presente = any(
            x.get("categoria") == categoria and ((x.get("ricette") or {}).get("nome", "").lower() == nome_pulito.lower())
            for x in voci_giorno_attuali
        )
        if gia_presente:
            return

        id_temp_ric = str(match_db["id"]) if match_db else f"temp_{int(time.time()*1000)}"
        voce_ottimistica = {
            "id": f"temp_menu_{int(time.time()*1000)}",
            "data_menu": data_str,
            "categoria": categoria,
            "subcategoria": subcategoria,
            "id_ricetta": id_temp_ric,
            "in_salvataggio": True,
            "ricette": match_db or {"id": id_temp_ric, "nome": nome_pulito, "portata_default": codifica_meta_portata(subcategoria, 8)}
        }
        if on_optimistic_add:
            on_optimistic_add(voce_ottimistica)

        def worker():
            try:
                ric_reale = match_db if match_db else ottieni_o_crea_ricetta_sicura(nome_pulito, subcategoria)
                if not ric_reale:
                    ric_reale = ottieni_o_crea_ricetta_sicura(nome_pulito, subcategoria, ignora_cache=True)
                id_ric = ric_reale["id"]

                try:
                    res_ins = supabase.table("menu_giornalieri").insert({
                        "data_menu": data_str, "categoria": categoria, "subcategoria": subcategoria, "id_ricetta": id_ric
                    }).execute()
                except Exception:
                    ric_reale = ottieni_o_crea_ricetta_sicura(nome_pulito, subcategoria, ignora_cache=True)
                    id_ric = ric_reale["id"]
                    res_ins = supabase.table("menu_giornalieri").insert({
                        "data_menu": data_str, "categoria": categoria, "subcategoria": subcategoria, "id_ricetta": id_ric
                    }).execute()

                voce_ottimistica["id_ricetta"] = id_ric
                voce_ottimistica["ricette"] = ric_reale
                voce_ottimistica["in_salvataggio"] = False
                if res_ins.data:
                    voce_ottimistica["id"] = res_ins.data[0]["id"]

                aggiorna_conteggio_ricette_visibili([id_ric])
            except Exception as ex:
                print("Errore inserimento piatto unico:", ex)
                voce_ottimistica["in_salvataggio"] = False
            finally:
                if on_done_callback:
                    on_done_callback()

        threading.Thread(target=worker, daemon=True).start()

    def filtra_p(e):
        t = (txt_piatto.value or "").strip().lower()
        riga_sugg.controls.clear()
        if t:
            matches = [r for r in (CACHE_ANAGRAFICA.get("ricette") or []) if t in r["nome"].lower()][:8]
            for m in matches:
                ha_ing = ricetta_ha_ingredienti(m["id"])
                riga_sugg.controls.append(
                    ft.Container(
                        content=ft.Text(f"{'🍲' if ha_ing else '⚠️'} {m['nome']}", size=11, color=ft.Colors.WHITE if ha_ing else ft.Colors.BLACK, weight=ft.FontWeight.BOLD),
                        bgcolor=ft.Colors.BLUE_900 if ha_ing else "#FFF59D", padding=ft.Padding(8, 4, 8, 4), border_radius=10,
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
        title=ft.Text(f"🍽️ {categoria} ({data_target.strftime('%d/%m')})", size=15, weight=ft.FontWeight.BOLD),
        content=ft.Container(content=ft.Column([txt_piatto, riga_sugg], tight=True, spacing=8), width=320),
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
# POPUP INSERIMENTO VOCE (CON SCELTA INGREDIENTE vs RICETTA PER "VARIE")
# ==========================================================
def apri_popup_aggiungi_voce_diretta(page: ft.Page, data_str: str, categoria: str, titolo_box: str, on_done_callback, on_optimistic_add=None):
    if not supabase:
        return

    lista_ingredienti = CACHE_ANAGRAFICA.get("ingredienti") or []
    lista_ricette = CACHE_ANAGRAFICA.get("ricette") or []
    is_categoria_detersivi = (categoria == "Detersivi")
    consente_ricetta = (categoria == "Varie")

    stato_tipo = {"modo": "Ingrediente"}

    txt_voce = ft.TextField(
        label=f"Aggiungi a {titolo_box}...",
        hint_text="Es. Pane bianco, Frutta oppure Muffin di carote...",
        dense=True, text_size=12, autofocus=True
    )
    box_qta = crea_box_quantita(valore_iniziale="1", height=34, text_size=12, mostra_prefisso=True, expand=True)
    sel_uni = crea_selettore_unita(page, valore_iniziale="pz", width=74, height=34, text_size=12)
    riga_qta_uni = ft.Row([box_qta, sel_uni], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER, visible=True)
    riga_sugg = ft.Row(wrap=True, spacing=4, run_spacing=4, visible=False)
    salvataggio_in_corso = [False]
    dlg_v = None

    btn_modo_ing = ft.Container(
        content=ft.Text("🥖 Ingrediente semplice", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
        bgcolor=ft.Colors.BROWN_700, padding=7, border_radius=6, expand=1, alignment=ft.Alignment(0, 0)
    )
    btn_modo_ric = ft.Container(
        content=ft.Text("🧁 Ricetta (es. Muffin)", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.BROWN_700),
        bgcolor="#EFEBE9", padding=7, border_radius=6, expand=1, alignment=ft.Alignment(0, 0)
    )

    def cambia_modo_varie(nuovo_m):
        stato_tipo["modo"] = nuovo_m
        is_ing = (nuovo_m == "Ingrediente")
        btn_modo_ing.bgcolor = ft.Colors.BROWN_700 if is_ing else "#EFEBE9"
        btn_modo_ing.content.color = ft.Colors.WHITE if is_ing else ft.Colors.BROWN_700
        btn_modo_ric.bgcolor = ft.Colors.BLUE_900 if not is_ing else "#EFEBE9"
        btn_modo_ric.content.color = ft.Colors.WHITE if not is_ing else ft.Colors.BROWN_700
        riga_qta_uni.visible = is_ing
        filtra_v(None)
        page.update()

    btn_modo_ing.on_click = lambda e: cambia_modo_varie("Ingrediente")
    btn_modo_ric.on_click = lambda e: cambia_modo_varie("Ricetta")

    def sel_sugg(nome_scelto, unita_scelta, is_ric=False):
        txt_voce.value = nome_scelto
        if is_ric and consente_ricetta:
            cambia_modo_varie("Ricetta")
        elif unita_scelta in UNITA_MISURA:
            sel_uni.set_value(unita_scelta)
        riga_sugg.visible = False
        page.update()

    def filtra_v(e):
        t = (txt_voce.value or "").strip().lower()
        riga_sugg.controls.clear()
        if t:
            proposte = []
            nomi_gia = set()
            if is_categoria_detersivi:
                for i in lista_ingredienti:
                    if i.get("is_detersivo") and t in i["nome"].lower():
                        proposte.append((i["nome"], i.get("unita_default", "pz"), False))
                        nomi_gia.add(i["nome"].lower())
                for nome_cat, uni_cat in CATALOGO_DETERSIVI_BASE:
                    if t in nome_cat.lower() and nome_cat.lower() not in nomi_gia:
                        proposte.append((nome_cat, uni_cat, False))
            elif stato_tipo["modo"] == "Ricetta":
                for r in lista_ricette:
                    if t in r["nome"].lower():
                        proposte.append((r["nome"], "pz", True))
            else:
                for i in lista_ingredienti:
                    if not i.get("is_detersivo") and t in i["nome"].lower():
                        proposte.append((i["nome"], i.get("unita_default", "g"), False))

            for n_p, u_p, is_r in proposte[:8]:
                riga_sugg.controls.append(
                    ft.Container(
                        content=ft.Text(f"{'🍲' if is_r else ('🧼' if is_categoria_detersivi else '🔍')} {n_p}", size=11, color=ft.Colors.BLUE_900, weight=ft.FontWeight.BOLD),
                        bgcolor=ft.Colors.BLUE_50, padding=ft.Padding(8, 4, 8, 4), border_radius=10,
                        on_click=lambda ev, np=n_p, up=u_p, ir=is_r: sel_sugg(np, up, ir)
                    )
                )
            riga_sugg.visible = len(proposte) > 0
        else:
            riga_sugg.visible = False
        page.update()

    txt_voce.on_change = filtra_v

    def salva_voce(e=None):
        if salvataggio_in_corso[0]:
            return
        nome_v = (txt_voce.value or "").strip().capitalize()
        if not nome_v:
            return
        salvataggio_in_corso[0] = True
        q_val = parse_qta(box_qta.textfield.value)
        u_val = sel_uni.get_value()
        modo_scelto = stato_tipo["modo"]

        chiudi_dialog_sicuro(page, dlg_v)

        if on_optimistic_add:
            if modo_scelto == "Ricetta":
                id_t = f"temp_{int(time.time()*1000)}"
                on_optimistic_add({
                    "id": f"temp_m_{int(time.time()*1000)}", "data_menu": data_str, "categoria": categoria,
                    "subcategoria": "Dolci", "id_ricetta": id_t, "in_salvataggio": True,
                    "ricette": {"id": id_t, "nome": nome_v, "portata_default": "Dolci||8||||"}
                })
            else:
                on_optimistic_add({
                    "id": f"temp_m_{int(time.time()*1000)}", "data_menu": data_str, "categoria": categoria,
                    "subcategoria": None, "id_ingrediente": f"temp_i_{int(time.time()*1000)}",
                    "quantita": q_val, "unita_misura": u_val, "preso": False,
                    "ingredienti": {"nome": nome_v, "unita_default": u_val}
                })

        def worker():
            try:
                if modo_scelto == "Ricetta":
                    ric_obj = ottieni_o_crea_ricetta_sicura(nome_v, "Dolci")
                    if ric_obj:
                        try:
                            supabase.table("menu_giornalieri").insert({
                                "data_menu": data_str, "categoria": categoria, "subcategoria": "Dolci", "id_ricetta": ric_obj["id"]
                            }).execute()
                        except Exception:
                            ric_obj = ottieni_o_crea_ricetta_sicura(nome_v, "Dolci", ignora_cache=True)
                            supabase.table("menu_giornalieri").insert({
                                "data_menu": data_str, "categoria": categoria, "subcategoria": "Dolci", "id_ricetta": ric_obj["id"]
                            }).execute()
                        aggiorna_conteggio_ricette_visibili([ric_obj["id"]])
                else:
                    id_i = ottieni_o_crea_ing_sicuro(nome_v, u_val, is_detersivo=is_categoria_detersivi)
                    if id_i:
                        try:
                            supabase.table("menu_giornalieri").insert({
                                "data_menu": data_str, "categoria": categoria, "subcategoria": None,
                                "id_ingrediente": id_i, "quantita": q_val, "unita_misura": u_val
                            }).execute()
                        except Exception:
                            id_i = ottieni_o_crea_ing_sicuro(nome_v, u_val, is_detersivo=is_categoria_detersivi, ignora_cache=True)
                            supabase.table("menu_giornalieri").insert({
                                "data_menu": data_str, "categoria": categoria, "subcategoria": None,
                                "id_ingrediente": id_i, "quantita": q_val, "unita_misura": u_val
                            }).execute()
            except Exception as ex:
                print("Errore salvataggio voce:", ex)
            finally:
                if on_done_callback:
                    on_done_callback()

        threading.Thread(target=worker, daemon=True).start()

    txt_voce.on_submit = salva_voce

    controlli_col = []
    if consente_ricetta:
        controlli_col.append(ft.Row([btn_modo_ing, btn_modo_ric], spacing=6))
    controlli_col.extend([txt_voce, riga_sugg, riga_qta_uni])

    dlg_v = ft.AlertDialog(
        title=ft.Text(f"➕ {titolo_box}", size=15, weight=ft.FontWeight.BOLD),
        content=ft.Container(content=ft.Column(controlli_col, tight=True, spacing=8), width=325),
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
# FORM MENU DEL GIORNO COMPLETO (CON INGREDIENTI ESPANSI SOTTO OGNI RICETTA)
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
    lbl_stato_caricamento = ft.Text("Lettura dati...", size=11, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD)
    banner_loading = ft.Container(
        content=ft.Row([spinner_header, lbl_stato_caricamento], alignment=ft.MainAxisAlignment.CENTER, spacing=8),
        bgcolor=ft.Colors.ORANGE_800, padding=ft.Padding(10, 6, 10, 6), border_radius=6, visible=True
    )
    colonna_contenuto = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)
    dlg_menu = None

    def chiudi_dlg(e=None):
        chiudi_dialog_sicuro(page, dlg_menu)
        if modifiche_effettuate[0]:
            if on_success_callback:
                on_success_callback()
            page.pubsub.send_all("aggiorna_menu")

    dlg_menu = ft.AlertDialog(
        title=ft.Column([ft.Text(f"🍽️ Menu {data_vis}", size=15, weight=ft.FontWeight.BOLD), banner_loading], spacing=6, tight=True),
        content=ft.Container(content=colonna_contenuto, width=355, height=490),
        actions=[
            ft.Container(
                content=ft.Text("Chiudi", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=12),
                bgcolor=ft.Colors.BLUE_900, padding=ft.Padding(14, 8, 14, 8), border_radius=6, on_click=chiudi_dlg
            )
        ],
        shape=ft.RoundedRectangleBorder(radius=10), content_padding=8, inset_padding=ft.Padding(10, 15, 10, 15)
    )
    apri_dialog_sicuro(page, dlg_menu)

    def mostra_loading(testo="Operazione in corso..."):
        lbl_stato_caricamento.value = testo
        banner_loading.visible = True
        page.update()

    def nascondi_loading():
        banner_loading.visible = False
        page.update()

    def carica_dati_giorno_db(inizializza_aperture=False):
        nonlocal lista_ingredienti, lista_ricette, menu_del_giorno, mappa_ricette_ing
        lista_ingredienti, lista_ricette = assicura_cache_anagrafica(forza_ricarica=True)
        res_menu = supabase.table("menu_giornalieri").select(
            "*, ricette(id, nome, portata_default), ingredienti(id, nome, unita_default)"
        ).eq("data_menu", data_str).execute()
        menu_del_giorno = res_menu.data if res_menu.data else []
        ids_ricette_giorno = [m["id_ricetta"] for m in menu_del_giorno if is_valid_uuid(m.get("id_ricetta"))]
        mappa_ricette_ing.clear()
        if ids_ricette_giorno:
            res_ri = supabase.table("ricette_ingredienti").select(
                "*, ingredienti(id, nome, unita_default)"
            ).in_("id_ricetta", ids_ricette_giorno).execute()
            for riga in (res_ri.data or []):
                rid = riga["id_ricetta"]
                mappa_ricette_ing.setdefault(rid, []).append(riga)
            for rid in ids_ricette_giorno:
                CACHE_ANAGRAFICA["conteggio_ing_ricette"][str(rid)] = len(mappa_ricette_ing.get(rid, []))
        if inizializza_aperture:
            for m in menu_del_giorno:
                if m["categoria"] in SUPERCATEGORIE:
                    cat_aperte.add(m["categoria"])
                if m.get("subcategoria"):
                    sub_aperte.add((m["categoria"], m["subcategoria"]))

    def esegui_azione_db(testo_loading, funzione_db, ricarica_giorno=True):
        mostra_loading(testo_loading)

        def worker():
            try:
                funzione_db()
                modifiche_effettuate[0] = True
                if ricarica_giorno:
                    carica_dati_giorno_db(inizializza_aperture=False)
                disegna_form_da_memoria()
            finally:
                nascondi_loading()

        threading.Thread(target=worker, daemon=True).start()

    def apri_dialog_ripeti_mese(categoria):
        voci_correnti = [m for m in menu_del_giorno if m["categoria"] == categoria and not m.get("subcategoria")]
        dlg_rip = None

        def esegui_rip(da_oggi_bool):
            chiudi_dialog_sicuro(page, dlg_rip)

            def op():
                anno, mese = data_selezionata.year, data_selezionata.month
                ultimo_g = calendar.monthrange(anno, mese)[1]
                g_start = data_selezionata.day if da_oggi_bool else 1
                p_str, u_str = f"{anno:04d}-{mese:02d}-{g_start:02d}", f"{anno:04d}-{mese:02d}-{ultimo_g:02d}"
                supabase.table("menu_giornalieri").delete().eq("categoria", categoria).is_("subcategoria", "null").gte("data_menu", p_str).lte("data_menu", u_str).neq("data_menu", data_str).execute()
                if voci_correnti:
                    recs = []
                    for g in range(g_start, ultimo_g + 1):
                        d_t = f"{anno:04d}-{mese:02d}-{g:02d}"
                        if d_t != data_str:
                            for vc in voci_correnti:
                                recs.append({
                                    "data_menu": d_t, "categoria": categoria, "subcategoria": None,
                                    "id_ricetta": vc.get("id_ricetta"), "id_ingrediente": vc.get("id_ingrediente"),
                                    "quantita": vc.get("quantita") or 0, "unita_misura": vc.get("unita_misura") or "g"
                                })
                    if recs:
                        supabase.table("menu_giornalieri").insert(recs).execute()

            esegui_azione_db(f"⏳ Copia di {categoria} nel mese...", op, ricarica_giorno=False)

        dlg_rip = ft.AlertDialog(
            title=ft.Text(f"🔁 Ripeti {categoria} nel mese", size=15, weight=ft.FontWeight.BOLD),
            content=ft.Text(f"Vuoi copiare le voci di '{categoria}' sugli altri giorni di {MESI[data_selezionata.month]}?", size=12),
            actions=[
                ft.TextButton("Annulla", on_click=lambda e: chiudi_dialog_sicuro(page, dlg_rip)),
                ft.Container(content=ft.Text("Da oggi a fine mese", color=ft.Colors.WHITE, size=11, weight=ft.FontWeight.BOLD), bgcolor=ft.Colors.TEAL_700, padding=8, border_radius=5, on_click=lambda e: esegui_rip(True)),
                ft.Container(content=ft.Text("Tutto il mese", color=ft.Colors.WHITE, size=11, weight=ft.FontWeight.BOLD), bgcolor=ft.Colors.BLUE_900, padding=8, border_radius=5, on_click=lambda e: esegui_rip(False))
            ]
        )
        apri_dialog_sicuro(page, dlg_rip)

    def crea_sezione_ingredienti_diretti(categoria, subcategoria=None):
        voci = [m for m in menu_del_giorno if m["categoria"] == categoria and m.get("subcategoria") == subcategoria and m.get("id_ingrediente")]
        lista_ui = ft.Column(spacing=4)
        for v in voci:
            ing_obj = normalizza_ingrediente_record(v.get("ingredienti") or {"id": v["id_ingrediente"], "nome": "Ingrediente", "unita_default": "g"})
            qta_str = f"{formatta_numero(v.get('quantita') or 0)} {v.get('unita_misura') or ''}".strip()
            lista_ui.controls.append(
                ft.Container(
                    content=ft.Row([
                        ft.Text(f"• {ing_obj['nome']}", size=12, weight=ft.FontWeight.BOLD, expand=True),
                        ft.Text(qta_str, size=11, color=ft.Colors.BLUE_900, weight=ft.FontWeight.BOLD),
                        ft.IconButton(icon=ft.Icons.EDIT, icon_color=ft.Colors.ORANGE_800, icon_size=15, on_click=lambda e, item=ing_obj: apri_popup_rinomina_ingrediente(page, item, lista_ingredienti, lambda: esegui_azione_db("⏳ Aggiornamento...", lambda: None))),
                        ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_color=ft.Colors.RED_700, icon_size=16, on_click=lambda e, id_v=v["id"]: esegui_azione_db("⏳ Rimozione...", lambda: supabase.table("menu_giornalieri").delete().eq("id", id_v).execute()))
                    ], spacing=2),
                    bgcolor="#F5F5F5", padding=ft.Padding(6, 2, 2, 2), border_radius=5
                )
            )

        txt_ing = ft.TextField(label="Cerca o scrivi ingrediente...", dense=True, text_size=12, expand=True)
        box_qta = crea_box_quantita(valore_iniziale="1", height=34, text_size=12, mostra_prefisso=True, expand=True)
        sel_unita = crea_selettore_unita(page, valore_iniziale="pz", width=74, height=34, text_size=12)
        riga_sugg = ft.Row(wrap=True, spacing=4, run_spacing=4, visible=False)

        def filtra_ing(e):
            t = (txt_ing.value or "").strip().lower()
            riga_sugg.controls.clear()
            if t:
                for m_ing in [i for i in lista_ingredienti if not i.get("is_detersivo") and t in i["nome"].lower()][:6]:
                    riga_sugg.controls.append(
                        ft.Container(
                            content=ft.Text(f"🔍 {m_ing['nome']}", size=11, color=ft.Colors.BLUE_900, weight=ft.FontWeight.BOLD),
                            bgcolor=ft.Colors.BLUE_50, padding=ft.Padding(8, 4, 8, 4), border_radius=10,
                            on_click=lambda ev, item=m_ing: (setattr(txt_ing, "value", item["nome"]), sel_unita.set_value(item.get("unita_default", "pz")), setattr(riga_sugg, "visible", False), page.update())
                        )
                    )
                riga_sugg.visible = len(riga_sugg.controls) > 0
            else:
                riga_sugg.visible = False
            page.update()

        txt_ing.on_change = filtra_ing

        def add_dir(e):
            n_val = (txt_ing.value or "").strip()
            if not n_val:
                return
            q_v, u_v = parse_qta(box_qta.textfield.value), sel_unita.get_value()
            esegui_azione_db("⏳ Salvataggio...", lambda: supabase.table("menu_giornalieri").insert({
                "data_menu": data_str, "categoria": categoria, "subcategoria": subcategoria,
                "id_ingrediente": ottieni_o_crea_ing_sicuro(n_val, u_v, False), "quantita": q_v, "unita_misura": u_v
            }).execute())

        return ft.Column([
            lista_ui, txt_ing, riga_sugg,
            ft.Row([
                box_qta, sel_unita,
                ft.Container(content=ft.Text("+ Aggiungi", color=ft.Colors.WHITE, size=11, weight=ft.FontWeight.BOLD), bgcolor=ft.Colors.BLUE_900, height=34, padding=ft.Padding(12, 0, 12, 0), alignment=ft.Alignment(0, 0), border_radius=5, on_click=add_dir)
            ], spacing=6)
        ], spacing=6)

    def crea_sezione_ricetta(categoria, subcategoria):
        voci_ricetta = [m for m in menu_del_giorno if m["categoria"] == categoria and (subcategoria is None or m.get("subcategoria") == subcategoria) and m.get("id_ricetta")]
        blocchi_ui = ft.Column(spacing=8)
        for v in voci_ricetta:
            ric_obj = v.get("ricette") or {}
            id_ric = v["id_ricetta"]
            nome_ric = ric_obj.get("nome", "Ricetta")
            meta_r = estrai_meta_ricetta(ric_obj)
            porzioni_ric = meta_r["porzioni"]
            ings_associati = mappa_ricette_ing.get(id_ric, [])
            ha_i = len(ings_associati) > 0

            col_ing_espansi = ft.Column(spacing=4)
            for ri in ings_associati:
                ing_info = normalizza_ingrediente_record(ri.get("ingredienti") or {"id": ri["id_ingrediente"], "nome": "Ingrediente", "unita_default": "g"})
                box_q_ri = crea_box_quantita(valore_iniziale=formatta_numero(ri.get("quantita") or 0), width=86, height=32, text_size=11, mostra_prefisso=True)
                sel_u_ri = crea_selettore_unita(page, valore_iniziale=ri.get("unita_misura") or "g", width=70, height=32, text_size=11)

                col_ing_espansi.controls.append(
                    ft.Container(
                        content=ft.Column([
                            ft.Row([
                                ft.Text(f"▪ {ing_info['nome']}", size=11, weight=ft.FontWeight.BOLD, expand=True),
                                ft.IconButton(icon=ft.Icons.EDIT, icon_color=ft.Colors.ORANGE_800, icon_size=14, on_click=lambda e, item=ing_info: apri_popup_rinomina_ingrediente(page, item, lista_ingredienti, lambda: esegui_azione_db("⏳ Aggiornamento...", lambda: None))),
                                ft.IconButton(icon=ft.Icons.CLOSE, icon_color=ft.Colors.RED_700, icon_size=14, on_click=lambda e, id_r=ri["id"]: esegui_azione_db("⏳ Rimozione...", lambda: supabase.table("ricette_ingredienti").delete().eq("id", id_r).execute()))
                            ], spacing=2),
                            ft.Row([
                                box_q_ri, sel_u_ri,
                                ft.Container(
                                    content=ft.Text("💾 Salva Q.tà", size=10, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                                    bgcolor=ft.Colors.GREEN_700, height=32, padding=ft.Padding(8, 0, 8, 0), alignment=ft.Alignment(0, 0), border_radius=5,
                                    on_click=lambda e, id_r=ri["id"], bq=box_q_ri, su=sel_u_ri: esegui_azione_db("⏳ Salvataggio...", lambda: supabase.table("ricette_ingredienti").update({"quantita": parse_qta(bq.textfield.value), "unita_misura": su.get_value()}).eq("id", id_r).execute())
                                )
                            ], spacing=4)
                        ], spacing=2),
                        bgcolor=ft.Colors.WHITE, padding=5, border_radius=5
                    )
                )

            blocchi_ui.controls.append(
                ft.Container(
                    content=ft.Column([
                        ft.Row([
                            ft.Text(f"{'🍲' if ha_i else '⚠️'} {nome_ric} ({porzioni_ric}p)", size=13, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900 if ha_i else ft.Colors.RED_700, expand=True),
                            ft.Container(
                                content=ft.Text("🤖 AI / 🌐 Web / +Ing", size=10, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                                bgcolor=ft.Colors.ORANGE_800, padding=ft.Padding(8, 4, 8, 4), border_radius=6,
                                on_click=lambda e, vm=v, r_id=id_ric, r_n=nome_ric, r_p=meta_r["portata"]: apri_popup_dettaglio_ricetta_semplificata(
                                    page, vm["id"], r_id, r_n, r_p, lambda: esegui_azione_db("⏳ Aggiornamento...", lambda: None)
                                )
                            ),
                            ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_color=ft.Colors.RED_700, icon_size=17, on_click=lambda e, id_m=v["id"]: esegui_azione_db("⏳ Rimozione...", lambda: supabase.table("menu_giornalieri").delete().eq("id", id_m).execute()))
                        ], spacing=4),
                        col_ing_espansi
                    ], spacing=5),
                    bgcolor="#F0F4F8" if ha_i else "#FFF8E1", padding=6, border_radius=8, border=bordo_campo_uniforme("#BBDEFB" if ha_i else "#E53935", 1.5)
                )
            )

        txt_ric = ft.TextField(label=f"Aggiungi ricetta ({subcategoria or categoria})...", dense=True, text_size=12, expand=True)
        riga_sugg_r = ft.Row(wrap=True, spacing=4, run_spacing=4, visible=False)

        def assegna_r(id_r):
            esegui_azione_db("⏳ Inserimento...", lambda: supabase.table("menu_giornalieri").insert({"data_menu": data_str, "categoria": categoria, "subcategoria": subcategoria or "Dolci", "id_ricetta": id_r}).execute())

        def filtra_r(e):
            t = (txt_ric.value or "").strip().lower()
            riga_sugg_r.controls.clear()
            if t:
                for mr in [r for r in lista_ricette if t in r["nome"].lower()][:6]:
                    riga_sugg_r.controls.append(
                        ft.Container(
                            content=ft.Text(f"🍲 {mr['nome']}", size=11, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                            bgcolor=ft.Colors.BLUE_900, padding=ft.Padding(8, 4, 8, 4), border_radius=10,
                            on_click=lambda ev, rid=mr["id"]: assegna_r(rid)
                        )
                    )
                riga_sugg_r.visible = len(riga_sugg_r.controls) > 0
            else:
                riga_sugg_r.visible = False
            page.update()

        txt_ric.on_change = filtra_r

        def crea_r_click(e):
            n_r = (txt_ric.value or "").strip()
            if not n_r:
                return
            esegui_azione_db("⏳ Creazione...", lambda: supabase.table("menu_giornalieri").insert({
                "data_menu": data_str, "categoria": categoria, "subcategoria": subcategoria or "Dolci",
                "id_ricetta": ottieni_o_crea_ricetta_sicura(n_r, subcategoria or "Dolci", ignora_cache=True)["id"]
            }).execute())

        return ft.Column([
            blocchi_ui,
            ft.Row([
                txt_ric,
                ft.Container(content=ft.Text("+ Ricetta", color=ft.Colors.WHITE, size=11, weight=ft.FontWeight.BOLD), bgcolor=ft.Colors.BLUE_900, height=34, padding=ft.Padding(10, 0, 10, 0), alignment=ft.Alignment(0, 0), border_radius=5, on_click=crea_r_click)
            ], spacing=6),
            riga_sugg_r
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

            header_sup = [ft.Container(content=ft.Checkbox(label=sup.upper(), value=is_sup_open, on_change=toggle_sup), expand=True)]
            if sup not in ["Pranzo", "Cena"]:
                header_sup.append(
                    ft.Container(
                        content=ft.Text("🔁 Ripeti nel mese", size=10, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                        bgcolor=colore_sup, padding=ft.Padding(7, 4, 7, 4), border_radius=12,
                        on_click=lambda e, s_cat=sup: apri_dialog_ripeti_mese(s_cat)
                    )
                )

            contenuto_sup = ft.Column(spacing=6, visible=is_sup_open)
            if is_sup_open:
                if sup in ["Pranzo", "Cena"]:
                    for sub in SUBCATEGORIE_PASTO:
                        k_sub = (sup, sub)
                        is_sub_open = k_sub in sub_aperte

                        def toggle_sub(e, ks=k_sub):
                            if e.control.value:
                                sub_aperte.add(ks)
                            else:
                                sub_aperte.discard(ks)
                            disegna_form_da_memoria()

                        blocco_sub = ft.Column([ft.Checkbox(label=sub, value=is_sub_open, on_change=toggle_sub)], spacing=4)
                        if is_sub_open:
                            blocco_sub.controls.append(crea_sezione_ricetta(sup, sub) if sub in SUB_CON_RICETTA else crea_sezione_ingredienti_diretti(sup, sub))
                        contenuto_sup.controls.append(
                            ft.Container(content=blocco_sub, bgcolor=ft.Colors.WHITE, padding=5, border_radius=6, border=bordo_campo_uniforme("#E0E0E0"))
                        )
                elif sup == "Varie":
                    contenuto_sup.controls.append(
                        ft.Container(
                            content=ft.Column([
                                ft.Text("🥖 Ingredienti semplici in Varie:", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.BROWN_700),
                                crea_sezione_ingredienti_diretti(sup, None),
                                ft.Divider(height=6, color="#D7CCC8"),
                                ft.Text("🧁 Ricette / Preparazioni in Varie:", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900),
                                crea_sezione_ricetta(sup, None)
                            ], spacing=6),
                            bgcolor=ft.Colors.WHITE, padding=6, border_radius=6
                        )
                    )
                else:
                    contenuto_sup.controls.append(ft.Container(content=crea_sezione_ingredienti_diretti(sup, None), bgcolor=ft.Colors.WHITE, padding=6, border_radius=6))

            colonna_contenuto.controls.append(
                ft.Container(
                    content=ft.Column([ft.Row(header_sup, alignment=ft.MainAxisAlignment.SPACE_BETWEEN), contenuto_sup], spacing=4),
                    bgcolor="#FAFAFA", padding=6, border_radius=8,
                    border=ft.Border(top=ft.BorderSide(1, "#E0E0E0"), bottom=ft.BorderSide(1, "#E0E0E0"), left=ft.BorderSide(5, colore_sup), right=ft.BorderSide(1, "#E0E0E0"))
                )
            )
        page.update()

    def avvio():
        carica_dati_giorno_db(inizializza_aperture=True)
        disegna_form_da_memoria()
        nascondi_loading()

    threading.Thread(target=avvio, daemon=True).start()


# ==========================================================
# MOTORE INTELLIGENTE "FAI LA SPESA"
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
    opzioni_filtro = ["Da oggi a fine mese", "Prossimi 7 giorni", "Solo giorno sel.", "Tutto il mese"]
    riga_bottoni_filtro = ft.Row(wrap=True, spacing=4, run_spacing=4)

    def cambia_filtro_spesa(nuova_scelta):
        stato_filtro["scelta"] = nuova_scelta
        aggiorna_stile_filtri()
        avvia_calcolo_spesa()

    def aggiorna_stile_filtri():
        riga_bottoni_filtro.controls.clear()
        for opz in opzioni_filtro:
            att = (stato_filtro["scelta"] == opz)
            riga_bottoni_filtro.controls.append(
                ft.Container(
                    content=ft.Text(opz, size=10, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE if att else ft.Colors.GREEN_800),
                    bgcolor=ft.Colors.GREEN_700 if att else "#E8F5E9", padding=ft.Padding(8, 5, 8, 5), border_radius=12,
                    border=bordo_campo_uniforme(ft.Colors.GREEN_700), on_click=lambda e, sc=opz: cambia_filtro_spesa(sc)
                )
            )

    aggiorna_stile_filtri()
    dlg_spesa = None

    def chiudi_spesa(e=None):
        chiudi_dialog_sicuro(page, dlg_spesa)
        if modifiche_spunte[0] and on_close_callback:
            on_close_callback()

    dlg_spesa = ft.AlertDialog(
        title=ft.Row([ft.Text("🛒 Lista della Spesa", size=16, weight=ft.FontWeight.BOLD, color=ft.Colors.GREEN_800, expand=True), spinner_spesa]),
        content=ft.Container(
            content=ft.Column([riga_bottoni_filtro, txt_periodo_date, txt_riepilogo, ft.Divider(height=4, color="#E0E0E0"), colonna_lista], spacing=5),
            width=355, height=490
        ),
        actions=[
            ft.Container(content=ft.Text("Chiudi", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=12), bgcolor=ft.Colors.BLUE_900, padding=ft.Padding(14, 8, 14, 8), border_radius=6, on_click=chiudi_spesa)
        ],
        shape=ft.RoundedRectangleBorder(radius=10), content_padding=10, inset_padding=ft.Padding(10, 15, 10, 15)
    )
    apri_dialog_sicuro(page, dlg_spesa)

    def normalizza_qta_unita(qta, unita):
        u = str(unita or "pz").replace("|DET", "").lower().strip()
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
        page.update()

        def worker():
            nonlocal dati_aggregati
            scelta = stato_filtro["scelta"]
            if scelta == "Da oggi a fine mese":
                dt_i, dt_f = oggi_dt, oggi_dt.replace(day=calendar.monthrange(oggi_dt.year, oggi_dt.month)[1])
            elif scelta == "Prossimi 7 giorni":
                dt_i, dt_f = oggi_dt, oggi_dt + timedelta(days=6)
            elif scelta == "Solo giorno sel.":
                dt_i, dt_f = data_riferimento, data_riferimento
            else:
                dt_i = data_riferimento.replace(day=1)
                dt_f = data_riferimento.replace(day=calendar.monthrange(data_riferimento.year, data_riferimento.month)[1])

            inizio_str, fine_str = dt_i.strftime("%Y-%m-%d"), dt_f.strftime("%Y-%m-%d")
            primo_mese_str = data_riferimento.replace(day=1).strftime("%Y-%m-%d")
            fine_mese_str = data_riferimento.replace(day=calendar.monthrange(data_riferimento.year, data_riferimento.month)[1]).strftime("%Y-%m-%d")
            txt_periodo_date.value = f"📅 Intervallo: {dt_i.strftime('%d/%m/%Y')} → {dt_f.strftime('%d/%m/%Y')}"

            try:
                res_m = supabase.table("menu_giornalieri").select("*, ingredienti(id, nome), ricette(id, nome)").gte("data_menu", min(inizio_str, primo_mese_str)).lte("data_menu", max(fine_str, fine_mese_str)).execute()
                righe_valide = [
                    r for r in (res_m.data or [])
                    if (r.get("categoria") in ["Dispensa", "Detersivi"] and primo_mese_str <= r.get("data_menu", "") <= fine_mese_str)
                    or (r.get("categoria") not in ["Dispensa", "Detersivi"] and inizio_str <= r.get("data_menu", "") <= fine_str)
                ]
                ids_ric = list({r["id_ricetta"] for r in righe_valide if is_valid_uuid(r.get("id_ricetta"))})
                mappa_ri = {}
                if ids_ric:
                    res_ri = supabase.table("ricette_ingredienti").select("*, ingredienti(id, nome)").in_("id_ricetta", ids_ric).limit(5000).execute()
                    for ri in (res_ri.data or []):
                        mappa_ri.setdefault(ri["id_ricetta"], []).append(ri)

                somme = {}
                spuntati_sessione.clear()
                for m in righe_valide:
                    cat = m.get("categoria")
                    is_preso_db = bool(m.get("preso"))
                    if m.get("id_ingrediente") and m.get("ingredienti"):
                        nome_i = m["ingredienti"]["nome"]
                        q_norm, u_base = normalizza_qta_unita(m.get("quantita") or 0, m.get("unita_misura"))
                        chiave = (nome_i.lower(), u_base)
                        somme.setdefault(chiave, {"nome": nome_i, "qta": 0.0, "unita": u_base, "ids_menu_diretti": [], "preso_db": False, "etichetta_extra": ""})
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
                                somme.setdefault(chiave, {"nome": nome_i, "qta": 0.0, "unita": u_base, "ids_menu_diretti": [], "preso_db": False, "etichetta_extra": ""})
                                somme[chiave]["qta"] += q_norm

                for v in somme.values():
                    if v["preso_db"]:
                        spuntati_sessione.add(f"{v['nome'].lower()}_{v['unita']}")

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
            txt_riepilogo.value = "Nessun ingrediente pianificato."
            page.update()
            return

        da_comprare, gia_in_dispensa = [], []
        for item in dati_aggregati:
            k_id = f"{item['nome'].lower()}_{item['unita']}"
            (gia_in_dispensa if k_id in spuntati_sessione else da_comprare).append((k_id, item))

        txt_riepilogo.value = f"Da acquistare: {len(da_comprare)}  |  In dispensa/Presi: {len(gia_in_dispensa)}"

        def toggle_spunta(e, key_ing, item_obj):
            nuovo_stato = bool(e.control.value)
            (spuntati_sessione.add if nuovo_stato else spuntati_sessione.discard)(key_ing)
            modifiche_spunte[0] = True
            ids_dir = item_obj.get("ids_menu_diretti") or []
            if ids_dir:
                threading.Thread(target=lambda: [aggiorna_flag_preso_db(mid, nuovo_stato) for mid in ids_dir], daemon=True).start()
            disegna_lista_spesa()

        for k_id, item in da_comprare:
            q_finale = formatta_qta_finale(item["qta"], item["unita"])
            lbl_extra = f" {item['etichetta_extra']}" if item.get("etichetta_extra") else ""
            colonna_lista.controls.append(
                ft.Container(
                    content=ft.Row([
                        ft.Checkbox(value=False, on_change=lambda e, k=k_id, it=item: toggle_spunta(e, k, it)),
                        ft.Text(f"{item['nome']}{lbl_extra}", size=13, weight=ft.FontWeight.BOLD, expand=True),
                        ft.Container(content=ft.Text(q_finale, size=12, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD), bgcolor=ft.Colors.BLUE_900, padding=ft.Padding(8, 3, 8, 3), border_radius=8)
                    ], spacing=4),
                    bgcolor="#F5F7FA", padding=ft.Padding(4, 2, 8, 2), border_radius=6, border=bordo_campo_uniforme("#E0E0E0")
                )
            )
        if gia_in_dispensa:
            colonna_lista.controls.append(ft.Divider(height=8, color="#E0E0E0"))
            for k_id, item in gia_in_dispensa:
                q_finale = formatta_qta_finale(item["qta"], item["unita"])
                colonna_lista.controls.append(
                    ft.Container(
                        content=ft.Row([
                            ft.Checkbox(value=True, on_change=lambda e, k=k_id, it=item: toggle_spunta(e, k, it)),
                            ft.Text(f"✓ {item['nome']}", size=12, color=ft.Colors.GREY_600, italic=True, expand=True),
                            ft.Text(q_finale, size=11, color=ft.Colors.GREY_600)
                        ], spacing=4),
                        bgcolor="#EEEEEE", padding=ft.Padding(4, 1, 8, 1), border_radius=6
                    )
                )
        page.update()

    avvia_calcolo_spesa()


# ==========================================================
# CALENDARIO DEI MENU (SINCRONIZZAZIONE CACHE AUTOMATICA + VARIE BIVALENTE)
# ==========================================================
def crea_calendario_menu(page: ft.Page):
    stato = {"data": datetime.now(), "vista": "Mese", "semplificata": False}
    oggi = datetime.now()
    ram_menu = {"dizionario_menu": {}, "voci_dispensa_mese": [], "voci_detersivi_mese": []}

    testo_header = ft.Text(size=15, weight=ft.FontWeight.BOLD)
    badge_loading_cal = ft.Container(
        content=ft.Row([ft.ProgressRing(width=14, height=14, stroke_width=2, color=ft.Colors.WHITE), ft.Text("Sync...", size=10, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD)], spacing=5, tight=True),
        bgcolor=ft.Colors.ORANGE_800, padding=ft.Padding(8, 3, 8, 3), border_radius=10, visible=False
    )

    btn_giorno = ft.Container(content=ft.Text("Giorno", size=12, weight=ft.FontWeight.BOLD), border_radius=5, padding=6, on_click=lambda e: cambia_vista("Giorno"))
    btn_settimana = ft.Container(content=ft.Text("Settimana", size=12, weight=ft.FontWeight.BOLD), border_radius=5, padding=6, on_click=lambda e: cambia_vista("Settimana"))
    btn_mese = ft.Container(content=ft.Text("Mese", size=12, weight=ft.FontWeight.BOLD), border_radius=5, padding=6, on_click=lambda e: cambia_vista("Mese"))
    box_bottoni_vista = ft.Row([btn_giorno, btn_settimana, btn_mese], spacing=5, visible=True)

    def toggle_modalita_semplificata(e):
        stato["semplificata"] = bool(switch_semplificata.value)
        box_bottoni_vista.visible = not stato["semplificata"]
        disegna_vista_da_ram()
        aggiorna_calendario_menu()

    lbl_semplificata = ft.Text("⚡ Semplificata", size=12, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900)
    switch_semplificata = ft.Switch(value=False, on_change=toggle_modalita_semplificata)
    box_switch_semplificata = ft.Container(
        content=ft.Row([lbl_semplificata, switch_semplificata], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        bgcolor="#FFF3E0", padding=ft.Padding(8, 2, 6, 2), border_radius=15, border=bordo_campo_uniforme("#FFB74D")
    )

    layer_mese = ft.Column(spacing=0, visible=True)
    layer_settimana = ft.Column(spacing=0, visible=False)
    layer_giorno = ft.Column(spacing=0, visible=False)
    layer_semplificata = ft.Column(spacing=8, visible=False)
    area_dinamica = ft.Column([layer_mese, layer_settimana, layer_giorno, layer_semplificata], expand=True, spacing=0)

    def bordo_standard():
        return ft.Border(top=ft.BorderSide(0.5, "#E0E0E0"), bottom=ft.BorderSide(0.5, "#E0E0E0"), left=ft.BorderSide(0.5, "#E0E0E0"), right=ft.BorderSide(0.5, "#E0E0E0"))

    def apri_giorno_selezionato(dt_target):
        stato["data"] = dt_target
        apri_form_menu_giorno(page, dt_target, aggiorna_calendario_menu)

    def cambia_vista(nuova_vista):
        stato["vista"] = nuova_vista
        disegna_vista_da_ram()
        aggiorna_calendario_menu()

    def nav_prec(e):
        if stato["semplificata"] or stato["vista"] == "Settimana":
            stato["data"] -= timedelta(days=7)
        elif stato["vista"] == "Mese":
            scorso = stato["data"].replace(day=1) - timedelta(days=1)
            stato["data"] = stato["data"].replace(year=scorso.year, month=scorso.month, day=1)
        else:
            stato["data"] -= timedelta(days=1)
        aggiorna_calendario_menu()

    def nav_succ(e):
        if stato["semplificata"] or stato["vista"] == "Settimana":
            stato["data"] += timedelta(days=7)
        elif stato["vista"] == "Mese":
            m, a = stato["data"].month, stato["data"].year
            stato["data"] = stato["data"].replace(year=a + (1 if m == 12 else 0), month=(1 if m == 12 else m + 1), day=1)
        else:
            stato["data"] += timedelta(days=1)
        aggiorna_calendario_menu()

    def crea_card_giorno_semplificata(dt_giorno: datetime, indice_sett: int, voci_giorno: list):
        is_oggi = (dt_giorno.date() == oggi.date())
        d_str_g = dt_giorno.strftime("%Y-%m-%d")

        voci_pranzo = [x for x in voci_giorno if x.get("categoria") == "Pranzo" and x.get("id_ricetta")]
        voci_cena = [x for x in voci_giorno if x.get("categoria") == "Cena" and x.get("id_ricetta")]
        voci_varie = [x for x in voci_giorno if x.get("categoria") == "Varie"]

        def aggiungi_ottimistico_giorno(nuova_voce):
            ram_menu["dizionario_menu"].setdefault(d_str_g, []).append(nuova_voce)
            disegna_vista_da_ram()

        def costruisci_badge_piatto(voce_m, colore_tema, portata_default):
            ric = voce_m.get("ricette") or {}
            id_ric = voce_m.get("id_ricetta")
            nome_r = ric.get("nome") or "Piatto"
            meta_r = estrai_meta_ricetta(ric)
            port_r = meta_r["portata"] or voce_m.get("subcategoria") or portata_default
            porzioni_r = meta_r["porzioni"]
            in_salv = bool(voce_m.get("in_salvataggio")) or not is_valid_uuid(id_ric)

            if in_salv:
                return ft.Container(
                    content=ft.Row([
                        ft.ProgressRing(width=11, height=11, stroke_width=2, color=ft.Colors.GREY_700),
                        ft.Text(f"⏳ {nome_r} (salvataggio...)", size=11, italic=True, color=ft.Colors.GREY_700, expand=True)
                    ], spacing=6),
                    bgcolor="#E0E0E0", padding=ft.Padding(6, 4, 6, 4), border_radius=5,
                    border=bordo_campo_uniforme("#9E9E9E", 1)
                )

            compilata = ricetta_ha_ingredienti(id_ric)
            sfondo = "#E8F5E9" if (compilata and colore_tema == "#4CAF50") else ("#E3F2FD" if compilata else "#FFF59D")
            col_bordo = colore_tema if compilata else "#D32F2F"
            col_testo = "#1B5E20" if (compilata and colore_tema == "#4CAF50") else ("#0D47A1" if compilata else "#B71C1C")
            pref_icon = "🍲 " if compilata else "⚠️ "
            info_pers = f" ({porzioni_r}p)" if compilata else ""

            return ft.Container(
                content=ft.Row([
                    ft.Text(f"{pref_icon}{nome_r}{info_pers}", size=11, weight=ft.FontWeight.BOLD, color=col_testo, no_wrap=False, expand=True),
                    ft.Icon(ft.Icons.OPEN_IN_NEW, size=12, color=col_testo)
                ], spacing=2, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                bgcolor=sfondo, padding=ft.Padding(6, 4, 6, 4), border_radius=5,
                border=bordo_campo_uniforme(col_bordo, 1.5 if not compilata else 1),
                on_click=lambda e, vm=voce_m, r_p=port_r: apri_popup_dettaglio_ricetta_semplificata(
                    page, vm.get("id"), vm.get("id_ricetta"), (vm.get("ricette") or {}).get("nome", "Piatto"), r_p, aggiorna_calendario_menu
                )
            )

        casella_pranzo = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Text("☀️ PRANZO", size=9, weight=ft.FontWeight.BOLD, color="#2E7D32", expand=True),
                    ft.Container(
                        content=ft.Text("+ Piatto", size=9, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                        bgcolor="#4CAF50", padding=ft.Padding(6, 2, 6, 2), border_radius=8,
                        on_click=lambda e, dt=dt_giorno, vg=voci_giorno: apri_popup_aggiungi_piatto_semplificato(page, dt, "Pranzo", "Primo", vg, aggiungi_ottimistico_giorno, aggiorna_calendario_menu)
                    )
                ]),
                ft.Column([costruisci_badge_piatto(vp, "#4CAF50", "Primo") for vp in voci_pranzo] if voci_pranzo else [ft.Text("Nessun piatto previsto", size=10, color=ft.Colors.GREY_500, italic=True)], spacing=3)
            ], spacing=4),
            bgcolor=ft.Colors.WHITE, padding=6, border_radius=6,
            border=ft.Border(top=ft.BorderSide(1, "#C8E6C9"), bottom=ft.BorderSide(1, "#C8E6C9"), left=ft.BorderSide(4, "#4CAF50"), right=ft.BorderSide(1, "#C8E6C9"))
        )

        casella_cena = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Text("🌙 CENA", size=10, weight=ft.FontWeight.BOLD, color="#1565C0", expand=True),
                    ft.Container(
                        content=ft.Text("+ Piatto", size=9, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                        bgcolor="#2196F3", padding=ft.Padding(6, 2, 6, 2), border_radius=8,
                        on_click=lambda e, dt=dt_giorno, vg=voci_giorno: apri_popup_aggiungi_piatto_semplificato(page, dt, "Cena", "Secondo", vg, aggiungi_ottimistico_giorno, aggiorna_calendario_menu)
                    )
                ]),
                ft.Column([costruisci_badge_piatto(vc, "#2196F3", "Secondo") for vc in voci_cena] if voci_cena else [ft.Text("Nessun piatto previsto", size=10, color=ft.Colors.GREY_500, italic=True)], spacing=3)
            ], spacing=4),
            bgcolor=ft.Colors.WHITE, padding=6, border_radius=6,
            border=ft.Border(top=ft.BorderSide(1, "#BBDEFB"), bottom=ft.BorderSide(1, "#BBDEFB"), left=ft.BorderSide(4, "#2196F3"), right=ft.BorderSide(1, "#BBDEFB"))
        )

        righe_varie_ui = []
        for vv in voci_varie:
            if vv.get("id_ricetta"):
                righe_varie_ui.append(costruisci_badge_piatto(vv, "#795548", "Dolci"))
            elif vv.get("id_ingrediente"):
                ing_v = vv.get("ingredienti") or {}
                nome_v = ing_v.get("nome") or "Voce"
                q_v = vv.get("quantita") or 0
                q_str = f" ({formatta_numero(q_v)} {vv.get('unita_misura') or ''})" if q_v else ""

                def del_varie(e, id_v=vv["id"]):
                    if d_str_g in ram_menu["dizionario_menu"]:
                        ram_menu["dizionario_menu"][d_str_g] = [x for x in ram_menu["dizionario_menu"][d_str_g] if x.get("id") != id_v]
                    disegna_vista_da_ram()
                    if is_valid_uuid(id_v):
                        threading.Thread(target=lambda: (supabase.table("menu_giornalieri").delete().eq("id", id_v).execute(), aggiorna_calendario_menu()), daemon=True).start()

                righe_varie_ui.append(
                    ft.Row([
                        ft.Text(f"• {nome_v}{q_str}", size=10, weight=ft.FontWeight.BOLD, color="#4E342E", expand=True),
                        ft.GestureDetector(content=ft.Icon(ft.Icons.CLOSE, size=13, color=ft.Colors.RED_700), on_tap=del_varie)
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
                        on_click=lambda e, d_s=d_str_g: apri_popup_aggiungi_voce_diretta(page, d_s, "Varie", f"Varie {dt_giorno.strftime('%d/%m')}", aggiorna_calendario_menu, aggiungi_ottimistico_giorno)
                    )
                ]),
                ft.Column(righe_varie_ui, spacing=3)
            ], spacing=3),
            bgcolor=ft.Colors.WHITE, padding=6, border_radius=6,
            border=ft.Border(top=ft.BorderSide(1, "#D7CCC8"), bottom=ft.BorderSide(1, "#D7CCC8"), left=ft.BorderSide(4, "#795548"), right=ft.BorderSide(1, "#D7CCC8"))
        )

        intestazione_giorno = ft.Container(
            content=ft.Row([
                ft.Text(f"{GIORNI_ESTESI[indice_sett].upper()} {dt_giorno.day}/{dt_giorno.month}", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE if is_oggi else ft.Colors.BLUE_900, expand=True),
                ft.GestureDetector(content=ft.Icon(ft.Icons.LIST_ALT, size=15, color=ft.Colors.WHITE if is_oggi else ft.Colors.BLUE_900), on_tap=lambda e, dt=dt_giorno: apri_giorno_selezionato(dt))
            ]),
            bgcolor=ft.Colors.BLUE_900 if is_oggi else "#DCE4EC", padding=ft.Padding(8, 5, 8, 5), border_radius=6
        )

        return ft.Container(
            content=ft.Column([intestazione_giorno, casella_pranzo, casella_cena, casella_varie], spacing=5),
            expand=1, bgcolor="#F4F6F9", padding=6, border_radius=8,
            border=bordo_campo_uniforme(ft.Colors.BLUE_900 if is_oggi else "#CFD8DC", 2 if is_oggi else 1)
        )

    def crea_card_mensile_tutto_schermo(titolo: str, icona_em: str, categoria_db: str, colore_tema: str, voci_mese: list, data_rif_mese: datetime):
        primo_del_mese_str = data_rif_mese.replace(day=1).strftime("%Y-%m-%d")
        nome_mese = MESI[data_rif_mese.month]
        lista_voci_ui = ft.Column(spacing=4)

        def aggiungi_ottimistico_mese(nuova_v):
            ram_menu["voci_dispensa_mese" if categoria_db == "Dispensa" else "voci_detersivi_mese"].append(nuova_v)
            disegna_vista_da_ram()

        if not voci_mese:
            lista_voci_ui.controls.append(ft.Container(content=ft.Text(f"Nessun prodotto in {titolo} per {nome_mese}. Clicca '+ Aggiungi' a destra.", size=11, color=ft.Colors.GREY_600, italic=True), padding=8))
        else:
            for vm in sorted(voci_mese, key=lambda x: (1 if x.get("preso") else 0, (x.get("ingredienti") or {}).get("nome", ""))):
                nome_p = (vm.get("ingredienti") or {}).get("nome") or "Prodotto"
                q_p = vm.get("quantita") or 0
                u_p = str(vm.get("unita_misura") or "").replace("|DET", "").strip()
                q_str = f" ({formatta_numero(q_p)} {u_p})" if q_p else ""
                is_preso = bool(vm.get("preso"))

                def cambia_spunta_mese(e, voce_ref=vm):
                    voce_ref["preso"] = bool(e.control.value)
                    disegna_vista_da_ram()
                    threading.Thread(target=lambda: aggiorna_flag_preso_db(voce_ref["id"], voce_ref["preso"]), daemon=True).start()

                def elimina_voce_mese(e, id_riga=vm["id"]):
                    chiave_ram = "voci_dispensa_mese" if categoria_db == "Dispensa" else "voci_detersivi_mese"
                    ram_menu[chiave_ram] = [x for x in ram_menu[chiave_ram] if x.get("id") != id_riga]
                    disegna_vista_da_ram()
                    if is_valid_uuid(id_riga):
                        threading.Thread(target=lambda: (supabase.table("menu_giornalieri").delete().eq("id", id_riga).execute(), aggiorna_calendario_menu()), daemon=True).start()

                lista_voci_ui.controls.append(
                    ft.Container(
                        content=ft.Row([
                            ft.Checkbox(value=is_preso, on_change=cambia_spunta_mese),
                            ft.Text(f"{'✓ Presente: ' if is_preso else ''}{nome_p}{q_str}", size=12, weight=ft.FontWeight.NORMAL if is_preso else ft.FontWeight.BOLD, color=ft.Colors.GREY_600 if is_preso else ft.Colors.BLACK, italic=is_preso, expand=True),
                            ft.GestureDetector(content=ft.Icon(ft.Icons.CLOSE, size=16, color=ft.Colors.RED_700), on_tap=elimina_voce_mese)
                        ], spacing=4),
                        bgcolor="#EEEEEE" if is_preso else ft.Colors.WHITE, padding=ft.Padding(6, 2, 10, 2), border_radius=6,
                        border=bordo_campo_uniforme("#E0E0E0" if is_preso else colore_tema)
                    )
                )

        tot_presenti = sum(1 for x in voci_mese if x.get("preso"))
        barra_titolo = ft.Container(
            content=ft.Row([
                ft.Text(f"{icona_em} {titolo.upper()} ({nome_mese.upper()}) — Da prendere: {len(voci_mese) - tot_presenti} | Presenti: {tot_presenti}", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE, expand=True),
                ft.Container(
                    content=ft.Text("+ Aggiungi", size=10, color=colore_tema, weight=ft.FontWeight.BOLD),
                    bgcolor=ft.Colors.WHITE, padding=ft.Padding(10, 4, 10, 4), border_radius=8,
                    on_click=lambda e: apri_popup_aggiungi_voce_diretta(page, primo_del_mese_str, categoria_db, f"{titolo} ({nome_mese})", aggiorna_calendario_menu, aggiungi_ottimistico_mese)
                )
            ], spacing=6),
            bgcolor=colore_tema, padding=ft.Padding(10, 6, 10, 6), border_radius=6
        )

        return ft.Row([
            ft.Container(content=ft.Column([barra_titolo, lista_voci_ui], spacing=6), expand=1, bgcolor="#F4F6F9", padding=8, border_radius=8, border=bordo_campo_uniforme(colore_tema, 1.5))
        ], spacing=6)

    def disegna_vista_da_ram():
        vista, data, is_semplificata = stato["vista"], stato["data"], stato["semplificata"]

        for b, nome_v in [(btn_giorno, "Giorno"), (btn_settimana, "Settimana"), (btn_mese, "Mese")]:
            b.bgcolor = ft.Colors.BLUE_900 if vista == nome_v else ft.Colors.GREY_200
            b.content.color = ft.Colors.WHITE if vista == nome_v else ft.Colors.BLACK

        layer_semplificata.visible = is_semplificata
        layer_mese.visible = (not is_semplificata and vista == "Mese")
        layer_settimana.visible = (not is_semplificata and vista == "Settimana")
        layer_giorno.visible = (not is_semplificata and vista == "Giorno")

        dizionario_menu = ram_menu["dizionario_menu"]

        if is_semplificata:
            lunedi = data - timedelta(days=data.weekday())
            domenica = lunedi + timedelta(days=6)
            testo_header.value = f"{lunedi.day} - {domenica.day} {MESI[lunedi.month][:3]} {lunedi.year}" if lunedi.month == domenica.month else f"{lunedi.day} {MESI[lunedi.month][:3]} - {domenica.day} {MESI[domenica.month][:3]} {domenica.year}"
            layer_semplificata.controls.clear()

            cards_g = [crea_card_giorno_semplificata(lunedi + timedelta(days=i), i, dizionario_menu.get((lunedi + timedelta(days=i)).strftime("%Y-%m-%d"), [])) for i in range(7)]
            layer_semplificata.controls.extend([
                ft.Row([cards_g[0], cards_g[1], cards_g[2]], spacing=6, vertical_alignment=ft.CrossAxisAlignment.START),
                ft.Row([cards_g[3], cards_g[4], cards_g[5]], spacing=6, vertical_alignment=ft.CrossAxisAlignment.START),
                ft.Row([cards_g[6]], spacing=6, vertical_alignment=ft.CrossAxisAlignment.START),
                ft.Divider(height=6, color="#CFD8DC"),
                crea_card_mensile_tutto_schermo("Dispensa", "📦", "Dispensa", "#00897B", ram_menu["voci_dispensa_mese"], data),
                crea_card_mensile_tutto_schermo("Detersivi e Varie", "🧼", "Detersivi", "#546E7A", ram_menu["voci_detersivi_mese"], data)
            ])

        elif vista == "Mese":
            testo_header.value = f"{MESI[data.month]} {data.year}"
            layer_mese.controls.clear()
            griglia = ft.Column(spacing=0)
            for settimana in calendar.monthcalendar(data.year, data.month):
                riga_ui = ft.Row(spacing=0)
                for giorno in settimana:
                    if giorno == 0:
                        riga_ui.controls.append(ft.Container(expand=1, height=90, border=bordo_standard()))
                    else:
                        is_oggi = (giorno == oggi.day and data.month == oggi.month and data.year == oggi.year)
                        data_giorno = data.replace(day=giorno)
                        voci_giorno = dizionario_menu.get(data_giorno.strftime("%Y-%m-%d"), [])
                        cerchio = ft.Container(
                            content=ft.Text(str(giorno), color=ft.Colors.WHITE if is_oggi else ft.Colors.BLACK, size=12, weight=ft.FontWeight.BOLD if is_oggi else ft.FontWeight.NORMAL),
                            bgcolor=ft.Colors.BLUE_900 if is_oggi else ft.Colors.TRANSPARENT, border_radius=15, width=24, height=24, alignment=ft.Alignment(0, 0)
                        )
                        col_badge = ft.Column(spacing=1, expand=True)
                        for sup in SUPERCATEGORIE:
                            v_sup = [x for x in voci_giorno if x["categoria"] == sup]
                            if v_sup and len(col_badge.controls) < 2:
                                vr = next((x for x in v_sup if x.get("ricette")), v_sup[0])
                                dett = f": {vr['ricette']['nome']}" if vr.get("ricette") else (f": {vr['ingredienti']['nome']}" if vr.get("ingredienti") else "")
                                is_w = bool(vr.get("ricette")) and not ricetta_ha_ingredienti(vr.get("id_ricetta"))
                                col_badge.controls.append(
                                    ft.Container(
                                        content=ft.Text(f"{'⚠️ ' if is_w else ''}{sup[:3]}{dett}", size=7, color=ft.Colors.BLACK if is_w else ft.Colors.WHITE, weight=ft.FontWeight.BOLD, no_wrap=True),
                                        bgcolor="#FFF59D" if is_w else COLORI_PASTI.get(sup, ft.Colors.BLUE_900), padding=2, border_radius=2, height=18
                                    )
                                )
                        riga_ui.controls.append(
                            ft.Container(
                                content=ft.Column([ft.Container(content=cerchio, alignment=ft.Alignment(0, 0)), col_badge], spacing=2, horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
                                expand=1, height=90, border=bordo_standard(), padding=2, on_click=lambda e, dt=data_giorno: apri_giorno_selezionato(dt)
                            )
                        )
                griglia.controls.append(riga_ui)
            layer_mese.controls.extend([
                ft.Row([ft.Container(content=ft.Text(g, size=9, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_700), expand=1, alignment=ft.Alignment(0, 0), border=bordo_standard(), padding=2) for g in GIORNI], spacing=0),
                griglia
            ])

        elif vista == "Settimana":
            lunedi = data - timedelta(days=data.weekday())
            domenica = lunedi + timedelta(days=6)
            testo_header.value = f"{lunedi.day} - {domenica.day} {MESI[lunedi.month][:3]} {lunedi.year}"
            layer_settimana.controls.clear()
            r1, r2 = ft.Row(spacing=0), ft.Row(spacing=0)
            for i in range(7):
                gc = lunedi + timedelta(days=i)
                voci_g = dizionario_menu.get(gc.strftime("%Y-%m-%d"), [])
                col_p = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO)
                for sup in SUPERCATEGORIE:
                    vs = [x for x in voci_g if x["categoria"] == sup]
                    if vs:
                        tv = [f"• {(x.get('ricette') or x.get('ingredienti') or {}).get('nome', '')}" for x in vs]
                        col_p.controls.append(ft.Container(content=ft.Column([ft.Text(sup.upper(), size=8, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE)] + [ft.Text(t, size=8, color=ft.Colors.WHITE) for t in tv], spacing=1), bgcolor=COLORI_PASTI.get(sup, ft.Colors.BLUE_900), padding=4, border_radius=4))
                box_g = ft.Container(
                    content=ft.Column([ft.Text(f"{GIORNI[i]} {gc.day}", size=10, weight=ft.FontWeight.BOLD), ft.Divider(height=1), ft.Container(content=col_p, expand=True)], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                    expand=1, height=280, border=bordo_standard(), padding=3, on_click=lambda e, dt=gc: apri_giorno_selezionato(dt)
                )
                (r1 if i < 4 else r2).controls.append(box_g)
            r2.controls.append(ft.Container(expand=1))
            layer_settimana.controls.extend([r1, ft.Container(height=10), r2])

        elif vista == "Giorno":
            testo_header.value = f"{data.day} {MESI[data.month]} {data.year}"
            layer_giorno.controls.clear()
            voci_g = dizionario_menu.get(data.strftime("%Y-%m-%d"), [])
            layer_giorno.controls.append(
                ft.Container(
                    content=ft.Row([
                        ft.Text(f"{GIORNI[data.weekday()]} {data.day}", size=20, weight=ft.FontWeight.BOLD),
                        ft.Container(content=ft.Text("✏️ Compila Menu del Giorno", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=12), bgcolor=ft.Colors.BLUE_900, padding=10, border_radius=6, on_click=lambda e: apri_giorno_selezionato(data))
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    padding=10, border=bordo_standard()
                )
            )
            for sup in SUPERCATEGORIE:
                vs = [x for x in voci_g if x["categoria"] == sup]
                if vs:
                    righe_p = [ft.Text(f"🍽️ {sup.upper()}", size=13, weight=ft.FontWeight.BOLD, color=COLORI_PASTI.get(sup))]
                    for x in vs:
                        if x.get("ricette"):
                            righe_p.append(ft.Text(f"  🍲 {x['ricette']['nome']}", size=12, weight=ft.FontWeight.BOLD))
                        elif x.get("ingredienti"):
                            righe_p.append(ft.Text(f"  • {x['ingredienti']['nome']} ({formatta_numero(x.get('quantita') or 0)} {x.get('unita_misura') or ''})", size=12))
                    layer_giorno.controls.append(ft.Container(content=ft.Column(righe_p, spacing=2), bgcolor=ft.Colors.WHITE, padding=8, border_radius=8, border=bordo_standard(), on_click=lambda e: apri_giorno_selezionato(data)))

        try:
            page.update()
        except Exception:
            pass

    def aggiorna_calendario_menu():
        data = stato["data"]
        badge_loading_cal.visible = True
        try:
            page.update()
        except Exception:
            pass

        def worker_calendario():
            dizionario_menu, voci_dispensa_mese, voci_detersivi_mese = {}, [], []
            if supabase:
                try:
                    assicura_cache_anagrafica(forza_ricarica=True)
                    data_rif = data.replace(day=1)
                    inizio_f = (data_rif - timedelta(days=10)).strftime("%Y-%m-%d")
                    fine_f = (data_rif + timedelta(days=42)).strftime("%Y-%m-%d")
                    primo_mese_corr = data.replace(day=1).strftime("%Y-%m-%d")
                    ultimo_mese_corr = data.replace(day=calendar.monthrange(data.year, data.month)[1]).strftime("%Y-%m-%d")

                    res = supabase.table("menu_giornalieri").select("*, ricette(id, nome, portata_default), ingredienti(id, nome, unita_default)").gte("data_menu", inizio_f).lte("data_menu", fine_f).execute()
                    ids_ric_vis = set()
                    for riga in (res.data or []):
                        cat_r, d_str = riga.get("categoria"), riga["data_menu"]
                        if is_valid_uuid(riga.get("id_ricetta")):
                            ids_ric_vis.add(riga["id_ricetta"])
                        if cat_r == "Dispensa" and primo_mese_corr <= d_str <= ultimo_mese_corr:
                            voci_dispensa_mese.append(riga)
                        elif cat_r == "Detersivi" and primo_mese_corr <= d_str <= ultimo_mese_corr:
                            if riga.get("id_ingrediente"):
                                CACHE_ANAGRAFICA["ids_detersivi"].add(riga["id_ingrediente"])
                            voci_detersivi_mese.append(riga)
                        elif cat_r not in ["Dispensa", "Detersivi"]:
                            dizionario_menu.setdefault(d_str, []).append(riga)

                    if ids_ric_vis:
                        aggiorna_conteggio_ricette_visibili(list(ids_ric_vis))
                except Exception as ex:
                    print("Errore caricamento menu:", ex)

            ram_menu["dizionario_menu"] = dizionario_menu
            ram_menu["voci_dispensa_mese"] = voci_dispensa_mese
            ram_menu["voci_detersivi_mese"] = voci_detersivi_mese
            badge_loading_cal.visible = False
            disegna_vista_da_ram()

        threading.Thread(target=worker_calendario, daemon=True).start()

    page.aggiorna_vista_menu = aggiorna_calendario_menu

    header = ft.Row(
        controls=[
            ft.Row([ft.IconButton(ft.Icons.CHEVRON_LEFT, on_click=nav_prec, icon_size=20), ft.IconButton(ft.Icons.CHEVRON_RIGHT, on_click=nav_succ, icon_size=20), testo_header, badge_loading_cal], spacing=4),
            ft.Row([box_switch_semplificata, box_bottoni_vista], spacing=8, wrap=True)
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN, wrap=True, run_spacing=8
    )

    btn_fai_la_spesa = ft.Container(
        content=ft.Row([ft.Icon(ft.Icons.SHOPPING_CART, color=ft.Colors.WHITE, size=20), ft.Text("FAI LA SPESA (Somma Ingredienti & Dispensa)", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=13)], alignment=ft.MainAxisAlignment.CENTER, spacing=8),
        bgcolor=ft.Colors.GREEN_700, padding=12, border_radius=8,
        on_click=lambda e: apri_popup_fai_la_spesa(page, stato["data"], aggiorna_calendario_menu)
    )

    aggiorna_calendario_menu()

    return ft.Container(
        content=ft.Column([header, area_dinamica, ft.Divider(height=10, color="#EEEEEE"), btn_fai_la_spesa], spacing=10, scroll=ft.ScrollMode.AUTO),
        padding=5, expand=True
    )
# === FINE FILE modulo_menu.py ===
    
    
