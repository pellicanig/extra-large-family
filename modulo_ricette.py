import flet as ft
import os
import re
import json
import html
import time
import threading
import urllib.request
import urllib.parse
from datetime import datetime
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
    print(f"Errore connessione Supabase in modulo_ricette: {e}")

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
    return {"portata": portata, "porzioni": porzioni, "url_fonte": url_fonte, "foto_url": foto_url}


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


def crea_box_quantita(valore_iniziale="1", width=90, height=34, text_size=12, mostra_prefisso=True, expand=False, prefisso_custom="Q.tà:", on_change_fn=None):
    tf = ft.TextField(
        value=str(valore_iniziale), border=ft.InputBorder.NONE, dense=True,
        text_size=text_size, content_padding=ft.Padding(2, 0, 2, 0),
        text_align=ft.TextAlign.CENTER, expand=True, on_change=on_change_fn
    )
    controlli = [ft.Text(prefisso_custom, size=10, color=ft.Colors.GREY_700), tf] if mostra_prefisso else [tf]
    box = ft.Container(
        content=ft.Row(controlli, spacing=2, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        width=None if expand else width, expand=expand, height=height,
        padding=ft.Padding(6, 0, 6, 0), bgcolor=ft.Colors.WHITE,
        border=bordo_campo_uniforme("#9E9E9E"), border_radius=5
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
        content=ft.Row([lbl_val, ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=16, color=ft.Colors.GREY_700)],
                       alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=2),
        width=width, height=height, padding=ft.Padding(8, 0, 4, 0),
        bgcolor=ft.Colors.WHITE, border=bordo_campo_uniforme("#9E9E9E"), border_radius=5
    )
    menu_btn = ft.PopupMenuButton(
        content=box_visivo,
        items=[ft.PopupMenuItem(content=ft.Text(u, size=13), on_click=lambda e, u_val=u: seleziona_u(u_val)) for u in UNITA_MISURA]
    )
    menu_btn.get_value = lambda: stato_u["value"]

    def set_value(nuovo_v):
        v_clean = str(nuovo_v or "").replace("|DET", "").strip()
        if v_clean in UNITA_MISURA:
            stato_u["value"] = v_clean
            lbl_val.value = v_clean

    menu_btn.set_value = set_value
    return menu_btn


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
            return esistente["id"]

    try:
        res_db = supabase.table("ingredienti").select("*").ilike("nome", nome_pulito).limit(1).execute()
        if res_db.data:
            trovato = normalizza_ingrediente_record(res_db.data[0])
            return trovato["id"]
    except Exception:
        pass

    try:
        res_ins = supabase.table("ingredienti").insert({"nome": nome_pulito, "unita_default": unita_db}).execute()
        nuovo = normalizza_ingrediente_record(res_ins.data[0])
        lista_ing.append(nuovo)
        return nuovo["id"]
    except Exception:
        res_retry = supabase.table("ingredienti").select("*").ilike("nome", nome_pulito).limit(1).execute()
        if res_retry.data:
            return res_retry.data[0]["id"]
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
            return res_db.data[0]
    except Exception:
        pass

    try:
        res_new = supabase.table("ricette").insert({
            "nome": nome_pulito,
            "portata_default": codifica_meta_portata(portata, 8)
        }).execute()
        nuova = res_new.data[0]
        lista_ric.append(nuova)
        CACHE_ANAGRAFICA["conteggio_ing_ricette"][str(nuova["id"])] = 0
        return nuova
    except Exception:
        res_retry = supabase.table("ricette").select("*").ilike("nome", nome_pulito).limit(1).execute()
        if res_retry.data:
            return res_retry.data[0]
    return None


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
        supabase.table("ricette_ingredienti").insert(righe_ri_bulk).execute()

    CACHE_ANAGRAFICA["conteggio_ing_ricette"][str(id_ricetta)] = len(righe_ri_bulk)
    return len(righe_ri_bulk)


# ==========================================================
# 🤖 MOTORE AI GEMINI + SCOMPOSIZIONE BASE/CONDIMENTO PRONTO
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


def genera_scomposizione_intelligente_condimento_pronto(query_raw: str, nome_ricetta_base: str):
    testo_completo = re.sub(r"\([^)]*\)", " ", f"{nome_ricetta_base or ''} {query_raw or ''}").strip()
    low = testo_completo.lower()

    basi_primi = [
        "spaghetti", "linguine", "tagliatelle", "rigatoni", "penne", "pennette", "fusilli",
        "orecchiette", "paccheri", "farfalle", "tortellini", "ravioli", "gnocchi", "gnocchetti",
        "trofie", "bavette", "mezze maniche", "pasta", "risotto", "riso"
    ]
    base_trovata = next((b.capitalize() for b in basi_primi if re.search(rf"\b{b}\b", low)), None)
    url_google_ai = f"https://www.google.com/search?q={urllib.parse.quote_plus((nome_ricetta_base or query_raw) + ' ricetta semplice')}"

    if "pesto" in low:
        ings = [
            {"nome": base_trovata or "Pasta", "qta_base": 350.0, "unita": "g"},
            {"nome": "Pesto pronto", "qta_base": 190.0, "unita": "g"},
            {"nome": "Parmigiano grattugiato", "qta_base": 40.0, "unita": "g"}
        ]
        prep = "Cuocere la pasta e condire con il pesto già pronto."
    elif "telline" in low or "vongole" in low or "arselle" in low:
        frutto_mare = "Telline fresche" if "telline" in low else ("Arselle" if "arselle" in low else "Vongole")
        ings = [
            {"nome": base_trovata or "Linguine", "qta_base": 350.0, "unita": "g"},
            {"nome": frutto_mare, "qta_base": 800.0, "unita": "g"},
            {"nome": "Aglio", "qta_base": 2.0, "unita": "pz"},
            {"nome": "Vino bianco secco", "qta_base": 100.0, "unita": "ml"},
            {"nome": "Prezzemolo", "qta_base": 0.0, "unita": "q.b."}
        ]
        prep = f"Saltare {frutto_mare.lower()} in padella con aglio e vino, poi mantecare la pasta."
    elif any(k in low for k in ["ragù", "ragu", "sugo", "bolognese"]):
        ings = [
            {"nome": base_trovata or "Tagliatelle", "qta_base": 350.0, "unita": "g"},
            {"nome": "Ragù pronto", "qta_base": 350.0, "unita": "g"},
            {"nome": "Parmigiano grattugiato", "qta_base": 40.0, "unita": "g"}
        ]
        prep = "Cuocere la pasta e condire con il ragù."
    else:
        nome_pulito = re.sub(r"\([^)]*\)", "", nome_ricetta_base or query_raw).strip().capitalize()
        ings = [{"nome": nome_pulito, "qta_base": 600.0, "unita": "g"}]
        prep = "Preparazione semplice per la spesa."

    return {
        "titolo": f"✨ {(nome_ricetta_base or query_raw).capitalize()} (Base + Condimento)",
        "sito": "Scomposizione Rapida Spesa",
        "preparazione_breve": prep,
        "url_fonte": url_google_ai,
        "foto_url": "",
        "porzioni_base": 4,
        "ingredienti_base": ings
    }


def chiedi_ricetta_a_gemini_ai(query_ricerca: str, nome_ricetta_originale: str = ""):
    api_key = os.environ.get("GEMINI_API_KEY", "").strip() or GEMINI_API_KEY
    if not api_key:
        return genera_scomposizione_intelligente_condimento_pronto(query_ricerca, nome_ricetta_originale)

    prompt = f"""Sei uno chef di cucina italiana. L'utente cerca: "{query_ricerca}" (piatto: "{nome_ricetta_originale}").
Restituisci gli ingredienti semplici per 4 persone. Se c'è un condimento pronto (es. pesto, ragù), metti la base (es. Pasta) + il condimento pronto.
Rispondi SOLO in JSON:
{{"titolo": "Nome Piatto", "preparazione_breve": "Spiegazione breve", "porzioni_base": 4, "ingredienti_base": [{{"nome": "Pasta", "qta_base": 350, "unita": "g"}}]}}"""

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
            dati_ai = json.loads(risp_json["candidates"][0]["content"]["parts"][0]["text"])
            ings_validi = []
            for item in dati_ai.get("ingredienti_base", []):
                u = item.get("unita", "g")
                if u not in UNITA_MISURA:
                    u = "g"
                ings_validi.append({"nome": str(item.get("nome", "")).strip().capitalize(), "qta_base": float(item.get("qta_base") or 0), "unita": u})
            if ings_validi:
                return {
                    "titolo": f"✨ {dati_ai.get('titolo') or nome_ricetta_originale} (Soluzione AI Gemini)",
                    "sito": "🤖 Intelligenza Artificiale Gemini",
                    "preparazione_breve": dati_ai.get("preparazione_breve", ""),
                    "url_fonte": f"https://www.google.com/search?q={urllib.parse.quote_plus((nome_ricetta_originale or query_ricerca) + ' ricetta')}",
                    "foto_url": "",
                    "porzioni_base": int(dati_ai.get("porzioni_base") or 4),
                    "ingredienti_base": ings_validi
                }
        except Exception:
            continue
    return genera_scomposizione_intelligente_condimento_pronto(query_ricerca, nome_ricetta_originale)


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
                    return {
                        "titolo": html.unescape(str(item.get("name") or titolo_og or "Ricetta")).strip(),
                        "porzioni_base": porzioni_base,
                        "raw_ings": item.get("recipeIngredient", []),
                        "foto_url": foto_og,
                        "url_fonte": url_pagina
                    }
        except Exception:
            continue
    return None


def cerca_ricette_su_internet_dettagliate(query_ricerca: str, nome_ricetta_originale: str = ""):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "it-IT,it;q=0.9"
    }
    risultati_trovati = []

    # 1. SITI WEB DI CUCINA
    query_pulita = re.sub(r"\([^)]*\)", " ", query_ricerca).strip() or query_ricerca.strip()
    q_enc = urllib.parse.quote_plus(query_pulita)
    urls = [
        f"https://www.giallozafferano.it/ricerca-ricette/{q_enc}/",
        f"https://www.cucchiaio.it/ricerca.html?q={q_enc}"
    ]
    links = []
    for u in urls:
        try:
            req = urllib.request.Request(u, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                html_s = resp.read().decode("utf-8", errors="ignore")
            for lk in re.findall(r'https://(?:ricette|blog)\.giallozafferano\.it/[a-zA-Z0-9\-_/]+\.html|https://www\.cucchiaio\.it/ricetta/[a-zA-Z0-9\-_]+/?', html_s):
                if lk not in links and "ricerca" not in lk:
                    links.append(lk)
        except Exception:
            continue

    for link in links[:3]:
        try:
            req_r = urllib.request.Request(link, headers=headers)
            with urllib.request.urlopen(req_r, timeout=5) as resp_r:
                html_r = resp_r.read().decode("utf-8", errors="ignore")
            info = estrai_info_complete_da_pagina_ricetta(html_r, link)
            if info and info.get("raw_ings"):
                ings = [analizza_stringa_ingrediente_web_base(rs) for rs in info["raw_ings"]]
                ings_validi = [x for x in ings if x]
                if ings_validi:
                    risultati_trovati.append({
                        "titolo": info["titolo"],
                        "sito": "GialloZafferano" if "giallozafferano" in link else "Cucchiaio d'Argento",
                        "url_fonte": link,
                        "foto_url": info["foto_url"],
                        "porzioni_base": info["porzioni_base"] or 4,
                        "ingredienti_base": ings_validi
                    })
        except Exception:
            continue

    # 2. ULTIMA SOLUZIONE: GEMINI AI
    sol_ai = chiedi_ricetta_a_gemini_ai(query_ricerca, nome_ricetta_originale or query_pulita)
    if sol_ai:
        risultati_trovati.append(sol_ai)

    return risultati_trovati


def ricalibra_ingredienti_ricetta_db(id_ricetta: str, vecchie_persone: int, nuove_persone: int, meta_attuale: dict):
    if not is_valid_uuid(id_ricetta):
        return
    vecchie_p, nuove_p = max(1, int(vecchie_persone or 8)), max(1, int(nuove_persone or 8))
    fattore = float(nuove_p) / float(vecchie_p)
    res_ri = supabase.table("ricette_ingredienti").select("*").eq("id_ricetta", id_ricetta).execute()
    for r in (res_ri.data or []):
        q = float(r.get("quantita") or 0)
        if q > 0 and r.get("unita_misura") != "q.b.":
            nq = round(q * fattore, 2)
            supabase.table("ricette_ingredienti").update({"quantita": nq}).eq("id", r["id"]).execute()
    salva_meta_ricetta_db(id_ricetta, meta_attuale.get("portata", "Primo"), nuove_p, meta_attuale.get("url_fonte", ""), meta_attuale.get("foto_url", ""))


# ==========================================================
# POPUP DI GESTIONE E DETTAGLIO RICETTA (UNICO BOTTONE 🔍 CERCA RICETTA)
# ==========================================================
def apri_popup_rinomina_ingrediente(page: ft.Page, ing_obj: dict, lista_ingredienti: list, on_done_callback):
    txt_nome = ft.TextField(label="Nome ingrediente", value=ing_obj.get("nome", ""), dense=True, text_size=13)
    sel_unita = crea_selettore_unita(page, valore_iniziale=ing_obj.get("unita_default", "g"), width=90, height=36, text_size=12)
    dlg_mod = None

    def salva_mod(e):
        n_nuovo = (txt_nome.value or "").strip().capitalize()
        if not n_nuovo:
            return
        chiudi_dialog_sicuro(page, dlg_mod)
        def w():
            supabase.table("ingredienti").update({"nome": n_nuovo, "unita_default": sel_unita.get_value()}).eq("id", ing_obj["id"]).execute()
            assicura_cache_anagrafica(forza_ricarica=True)
            if on_done_callback:
                on_done_callback()
        threading.Thread(target=w, daemon=True).start()

    dlg_mod = ft.AlertDialog(
        title=ft.Text("✏️ Modifica Ingrediente", size=15, weight=ft.FontWeight.BOLD),
        content=ft.Container(content=ft.Column([txt_nome, ft.Row([ft.Text("Unità:", size=12), sel_unita], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)], tight=True, spacing=8), width=310),
        actions=[
            ft.TextButton("Annulla", on_click=lambda e: chiudi_dialog_sicuro(page, dlg_mod)),
            ft.Container(content=ft.Text("Salva", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=12), bgcolor=ft.Colors.ORANGE_800, padding=8, border_radius=5, on_click=salva_mod)
        ]
    )
    apri_dialog_sicuro(page, dlg_mod)


def apri_popup_gestione_ricetta(page: ft.Page, id_ricetta: str, nome_attuale: str, portata_attuale: str, on_done_callback):
    txt_nome_ric = ft.TextField(label="Nome Ricetta", value=nome_attuale, dense=True, text_size=13)
    dlg_ric = None

    def salva_ricetta(e):
        n_u = (txt_nome_ric.value or "").strip().capitalize()
        if not n_u:
            return
        chiudi_dialog_sicuro(page, dlg_ric)
        def w():
            salva_meta_ricetta_db(id_ricetta, portata_attuale, 8, "", "", nuovo_nome=n_u)
            if on_done_callback:
                on_done_callback(n_u)
        threading.Thread(target=w, daemon=True).start()

    dlg_ric = ft.AlertDialog(
        title=ft.Text("🍲 Rinomina Ricetta", size=15, weight=ft.FontWeight.BOLD),
        content=ft.Container(content=txt_nome_ric, width=310),
        actions=[
            ft.TextButton("Annulla", on_click=lambda e: chiudi_dialog_sicuro(page, dlg_ric)),
            ft.Container(content=ft.Text("Salva", color=ft.Colors.WHITE, size=12, weight=ft.FontWeight.BOLD), bgcolor=ft.Colors.BLUE_900, padding=8, border_radius=5, on_click=salva_ricetta)
        ]
    )
    apri_dialog_sicuro(page, dlg_ric)


def apri_popup_dettaglio_ricetta_semplificata(page: ft.Page, id_voce_menu: str, id_ricetta_init: str, nome_ricetta_init: str, portata: str, on_close_refresh):
    if not supabase:
        return

    lista_ingredienti, _ = assicura_cache_anagrafica(forza_ricarica=False)
    spinner_det = ft.ProgressRing(width=18, height=18, stroke_width=2.5, color=ft.Colors.WHITE)
    lbl_det_status = ft.Text("Caricamento...", size=11, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD)
    banner_det = ft.Container(content=ft.Row([spinner_det, lbl_det_status], spacing=8), bgcolor=ft.Colors.ORANGE_800, padding=6, border_radius=6, visible=True)

    stato_popup = {
        "id_ricetta": str(id_ricetta_init or ""),
        "id_voce_menu": str(id_voce_menu or ""),
        "nome_ricetta": nome_ricetta_init,
        "query_web": nome_ricetta_init,
        "persone_web": 8,
        "risultati_web": [],
        "idx_web": 0,
        "ricerca_fatta": False
    }

    txt_titolo_header = ft.Text(f"🍲 {nome_ricetta_init}", size=15, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900, expand=True)
    col_corpo = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)
    dlg_det = None

    def chiudi_det(e=None):
        chiudi_dialog_sicuro(page, dlg_det)
        if on_close_refresh:
            on_close_refresh()

    def togli_dal_menu(e):
        id_vm = stato_popup["id_voce_menu"]
        chiudi_dialog_sicuro(page, dlg_det)
        if is_valid_uuid(id_vm):
            threading.Thread(target=lambda: (supabase.table("menu_giornalieri").delete().eq("id", id_vm).execute(), on_close_refresh() if on_close_refresh else None), daemon=True).start()

    dlg_det = ft.AlertDialog(
        title=ft.Column([
            ft.Row([
                txt_titolo_header,
                ft.IconButton(icon=ft.Icons.EDIT, icon_color=ft.Colors.ORANGE_800, icon_size=18, on_click=lambda e: apri_popup_gestione_ricetta(page, stato_popup["id_ricetta"], stato_popup["nome_ricetta"], portata, lambda n: (stato_popup.update({"nome_ricetta": n, "query_web": n}), ricarica_ricetta()))),
                ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_color=ft.Colors.RED_700, icon_size=18, on_click=togli_dal_menu)
            ], spacing=2),
            banner_det
        ], spacing=4, tight=True),
        content=ft.Container(content=col_corpo, width=368, height=520),
        actions=[
            ft.Container(content=ft.Text("Chiudi", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=12), bgcolor=ft.Colors.BLUE_900, padding=14, border_radius=6, on_click=chiudi_det)
        ],
        shape=ft.RoundedRectangleBorder(radius=10), content_padding=10
    )
    apri_dialog_sicuro(page, dlg_det)

    def assicura_id_reale():
        if is_valid_uuid(stato_popup["id_ricetta"]):
            return stato_popup["id_ricetta"]
        r_c = ottieni_o_crea_ricetta_sicura(stato_popup["nome_ricetta"], portata, ignora_cache=True)
        stato_popup["id_ricetta"] = str(r_c["id"])
        return stato_popup["id_ricetta"]

    def ricarica_ricetta(msg_extra=None):
        banner_det.visible = True
        lbl_det_status.value = "Caricamento ricetta..."
        page.update()

        def worker():
            try:
                id_ric = assicura_id_reale()
                res_db = supabase.table("ricette").select("*").eq("id", id_ric).execute()
                ric_c = res_db.data[0] if res_db.data else {"id": id_ric, "nome": stato_popup["nome_ricetta"], "portata_default": portata}
                meta = estrai_meta_ricetta(ric_c)
                porzioni_attuali = meta["porzioni"]
                txt_titolo_header.value = f"🍲 {ric_c.get('nome', stato_popup['nome_ricetta'])}"

                res_ri = supabase.table("ricette_ingredienti").select("*, ingredienti(id, nome, unita_default)").eq("id_ricetta", id_ric).execute()
                righe_ri = res_ri.data if res_ri.data else []
                ha_ing = len(righe_ri) > 0
                CACHE_ANAGRAFICA["conteggio_ing_ricette"][str(id_ric)] = len(righe_ri)

                # Avvio automatico ricerca in background se la ricetta è vuota
                if not ha_ing and not stato_popup["ricerca_fatta"]:
                    stato_popup["ricerca_fatta"] = True
                    lbl_det_status.value = f"🌐 Ricerca automatica '{stato_popup['query_web']}'..."
                    page.update()
                    stato_popup["risultati_web"] = cerca_ricette_su_internet_dettagliate(stato_popup["query_web"], stato_popup["nome_ricetta"])
                    stato_popup["idx_web"] = 0

                col_corpo.controls.clear()

                if msg_extra:
                    col_corpo.controls.append(ft.Container(content=ft.Text(f"✅ {msg_extra}", size=11, color=ft.Colors.GREEN_800, weight=ft.FontWeight.BOLD), bgcolor="#E8F5E9", padding=6, border_radius=6))

                # 1. AGGIUNTA MANUALE IN CIMA
                txt_ing_man = ft.TextField(label="➕ Aggiungi ingrediente manuale...", dense=True, text_size=11, expand=True)
                box_q_man = crea_box_quantita("100", height=34, text_size=11, mostra_prefisso=True, expand=True)
                sel_u_man = crea_selettore_unita(page, "g", width=72, height=34, text_size=11)
                riga_sugg_man = ft.Row(wrap=True, spacing=4, visible=False)

                def filtra_man(e):
                    t = (txt_ing_man.value or "").strip().lower()
                    riga_sugg_man.controls.clear()
                    if t:
                        matches = [i for i in (CACHE_ANAGRAFICA.get("ingredienti") or []) if not i.get("is_detersivo") and t in i["nome"].lower()][:5]
                        for m in matches:
                            riga_sugg_man.controls.append(
                                ft.Container(
                                    content=ft.Text(f"🔍 {m['nome']}", size=10, color=ft.Colors.BLUE_900, weight=ft.FontWeight.BOLD),
                                    bgcolor=ft.Colors.BLUE_50, padding=6, border_radius=8,
                                    on_click=lambda ev, item=m: (setattr(txt_ing_man, "value", item["nome"]), sel_u_man.set_value(item.get("unita_default", "g")), setattr(riga_sugg_man, "visible", False), page.update())
                                )
                            )
                        riga_sugg_man.visible = len(matches) > 0
                    else:
                        riga_sugg_man.visible = False
                    page.update()

                txt_ing_man.on_change = filtra_man

                def salva_man(e=None):
                    nv = (txt_ing_man.value or "").strip()
                    if not nv:
                        return
                    qv, uv = parse_qta(box_q_man.textfield.value), sel_u_man.get_value()
                    banner_det.visible = True
                    lbl_det_status.value = f"⚡ Salvataggio '{nv}'..."
                    page.update()
                    def w():
                        id_i = ottieni_o_crea_ing_sicuro(nv, uv, False)
                        if id_i:
                            try:
                                supabase.table("ricette_ingredienti").insert({"id_ricetta": id_ric, "id_ingrediente": id_i, "quantita": qv, "unita_misura": uv}).execute()
                            except Exception:
                                id_i_f = ottieni_o_crea_ing_sicuro(nv, uv, False, ignora_cache=True)
                                supabase.table("ricette_ingredienti").insert({"id_ricetta": id_ric, "id_ingrediente": id_i_f, "quantita": qv, "unita_misura": uv}).execute()
                            CACHE_ANAGRAFICA["conteggio_ing_ricette"][str(id_ric)] = CACHE_ANAGRAFICA["conteggio_ing_ricette"].get(str(id_ric), 0) + 1
                            if on_close_refresh:
                                on_close_refresh()
                            ricarica_ricetta(msg_extra=f"Ingrediente aggiunto: {nv}")
                    threading.Thread(target=w, daemon=True).start()

                txt_ing_man.on_submit = salva_man

                col_corpo.controls.append(
                    ft.Container(
                        content=ft.Column([
                            txt_ing_man, riga_sugg_man,
                            ft.Row([box_q_man, sel_u_man, ft.Container(content=ft.Text("+ Ingrediente", color=ft.Colors.WHITE, size=11, weight=ft.FontWeight.BOLD), bgcolor=ft.Colors.GREEN_700, height=34, padding=ft.Padding(10, 0, 10, 0), alignment=ft.Alignment(0, 0), border_radius=5, on_click=salva_man)], spacing=5)
                        ], spacing=4),
                        bgcolor="#E8F5E9", padding=6, border_radius=8, border=bordo_campo_uniforme("#81C784")
                    )
                )

                # 2. SE HA INGREDIENTI: LISTA E RICALCOLO
                if ha_ing:
                    box_p_pers = crea_box_quantita(str(porzioni_attuali), width=84, height=32, text_size=12, mostra_prefisso=True, prefisso_custom="Pers.:")

                    def ricalcola_pers(e):
                        np = int(max(1, parse_qta(box_p_pers.textfield.value) or porzioni_attuali))
                        banner_det.visible = True
                        lbl_det_status.value = f"⚖️ Ricalcolo per {np} persone..."
                        page.update()
                        def w_r():
                            ricalibra_ingredienti_ricetta_db(id_ric, porzioni_attuali, np, meta)
                            if on_close_refresh:
                                on_close_refresh()
                            ricarica_ricetta(msg_extra=f"Quantità ricalcolate per {np} persone!")
                        threading.Thread(target=w_r, daemon=True).start()

                    col_corpo.controls.append(
                        ft.Container(
                            content=ft.Column([
                                ft.Row([
                                    ft.Text(f"👥 Calibrate per {porzioni_attuali} persone", size=12, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900, expand=True),
                                    box_p_pers,
                                    ft.Container(content=ft.Text("⚖️ Ricalcola", size=10, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD), bgcolor=ft.Colors.TEAL_700, height=32, padding=ft.Padding(8, 0, 8, 0), alignment=ft.Alignment(0, 0), border_radius=5, on_click=ricalcola_pers)
                                ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER)
                            ], spacing=4),
                            bgcolor="#ECEFF1", padding=7, border_radius=8, border=bordo_campo_uniforme("#B0BEC5")
                        )
                    )

                    for ri in righe_ri:
                        info_i = normalizza_ingrediente_record(ri.get("ingredienti") or {"id": ri["id_ingrediente"], "nome": "Ingrediente", "unita_default": "g"})
                        bq_i = crea_box_quantita(formatta_numero(ri.get("quantita") or 0), width=84, height=32, text_size=11, mostra_prefisso=True)
                        su_i = crea_selettore_unita(page, ri.get("unita_misura") or "g", width=70, height=32, text_size=11)

                        def salva_q(e, id_r=ri["id"], b=bq_i, s=su_i):
                            threading.Thread(target=lambda: (supabase.table("ricette_ingredienti").update({"quantita": parse_qta(b.textfield.value), "unita_misura": s.get_value()}).eq("id", id_r).execute(), ricarica_ricetta()), daemon=True).start()

                        def elim_i(e, id_r=ri["id"]):
                            threading.Thread(target=lambda: (supabase.table("ricette_ingredienti").delete().eq("id", id_r).execute(), on_close_refresh() if on_close_refresh else None, ricarica_ricetta()), daemon=True).start()

                        col_corpo.controls.append(
                            ft.Container(
                                content=ft.Column([
                                    ft.Row([
                                        ft.Text(f"▪ {info_i['nome']}", size=12, weight=ft.FontWeight.BOLD, expand=True),
                                        ft.IconButton(icon=ft.Icons.EDIT, icon_color=ft.Colors.ORANGE_800, icon_size=14, on_click=lambda e, it=info_i: apri_popup_rinomina_ingrediente(page, it, CACHE_ANAGRAFICA.get("ingredienti") or [], ricarica_ricetta)),
                                        ft.IconButton(icon=ft.Icons.CLOSE, icon_color=ft.Colors.RED_700, icon_size=15, on_click=elim_i)
                                    ], spacing=2),
                                    ft.Row([bq_i, su_i, ft.Container(content=ft.Text("💾 Salva", size=10, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD), bgcolor=ft.Colors.GREEN_700, height=32, padding=ft.Padding(8, 0, 8, 0), alignment=ft.Alignment(0, 0), border_radius=5, on_click=salva_q)], spacing=4)
                                ], spacing=2),
                                bgcolor="#F5F7FA", padding=5, border_radius=5, border=bordo_campo_uniforme("#E0E0E0")
                            )
                        )

                # 3. BARRA DI RICERCA UNICA + BOTTONE ARANCIONE 🔍 CERCA RICETTA
                tf_q_web = ft.TextField(label="Cerca ricetta (es. 'Pasta e pesto col pesto già pronto')", value=stato_popup["query_web"], dense=True, text_size=12, expand=True)
                box_anteprima = ft.Column(spacing=5)

                def render_anteprima():
                    box_anteprima.controls.clear()
                    r_list = stato_popup["risultati_web"]
                    if not r_list:
                        return
                    idx = min(stato_popup["idx_web"], len(r_list) - 1)
                    cand = r_list[idx]
                    p_t = max(1, int(stato_popup["persone_web"] or 8))
                    ings_s = scala_ingredienti_candidato(cand, p_t)

                    def applica_cand(e):
                        banner_det.visible = True
                        lbl_det_status.value = f"⚡ Salvataggio ricetta ({p_t} pers.)..."
                        page.update()
                        def w_app():
                            salva_lista_ingredienti_in_blocco(id_ric, ings_s)
                            salva_meta_ricetta_db(id_ric, meta["portata"], p_t, cand.get("url_fonte", ""), cand.get("foto_url", ""))
                            if on_close_refresh:
                                on_close_refresh()
                            ricarica_ricetta(msg_extra=f"Applicata '{cand['titolo']}' ({p_t} persone)!")
                        threading.Thread(target=w_app, daemon=True).start()

                    ing_str = ", ".join([f"{x['nome']} ({formatta_numero(x['quantita'])} {x['unita']})" if x["unita"] != "q.b." else f"{x['nome']} (q.b.)" for x in ings_s[:10]])
                    url_f = cand.get("url_fonte") or f"https://www.google.com/search?q={urllib.parse.quote_plus(stato_popup['query_web'] + ' ricetta')}"

                    riga_f = []
                    if cand.get("foto_url"):
                        riga_f.append(ft.Image(src=cand["foto_url"], width=64, height=64, border_radius=8))

                    col_t = [
                        ft.Text(f"📌 {cand['titolo']}", size=12, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900),
                        ft.Text(f"Fonte: {cand['sito']} (dosi per {p_t} pers.)", size=10, color=ft.Colors.GREY_700),
                        ft.TextButton("🔗 Apri ricetta / preparazione in una nuova scheda", url=url_f, style=ft.ButtonStyle(padding=0))
                    ]
                    if cand.get("preparazione_breve"):
                        col_t.append(ft.Text(f"👩‍🍳 {cand['preparazione_breve']}", size=10, italic=True, color=ft.Colors.BLUE_GREY_800))
                    riga_f.append(ft.Column(col_t, spacing=2, expand=True))

                    nav = []
                    if len(r_list) > 1:
                        nav.append(ft.Row([
                            ft.TextButton("◀ Prec.", on_click=lambda e: (stato_popup.update({"idx_web": (stato_popup["idx_web"] - 1) % len(r_list)}), render_anteprima(), page.update())),
                            ft.Text(f"Proposta {idx + 1} di {len(r_list)}", size=10, weight=ft.FontWeight.BOLD),
                            ft.TextButton("Altra proposta ▶", on_click=lambda e: (stato_popup.update({"idx_web": (stato_popup["idx_web"] + 1) % len(r_list)}), render_anteprima(), page.update()))
                        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN))

                    box_anteprima.controls.append(
                        ft.Container(
                            content=ft.Column([
                                *nav,
                                ft.Row(riga_f, spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                                ft.Text(f"Ingredienti ({p_t}p): {ing_str}", size=10, color=ft.Colors.GREY_800, weight=ft.FontWeight.BOLD),
                                ft.Container(content=ft.Text(f"✅ SALVA E APPLICA QUESTA RICETTA ({p_t} PERSONE)", color=ft.Colors.WHITE, size=11, weight=ft.FontWeight.BOLD), bgcolor=ft.Colors.GREEN_700, padding=8, border_radius=6, alignment=ft.Alignment(0, 0), on_click=applica_cand)
                            ], spacing=5),
                            bgcolor=ft.Colors.WHITE, padding=8, border_radius=6, border=bordo_campo_uniforme(ft.Colors.GREEN_700)
                        )
                    )

                box_p_web = crea_box_quantita(str(stato_popup["persone_web"]), width=88, height=34, text_size=12, mostra_prefisso=True, prefisso_custom="Pers.:", on_change_fn=lambda e: stato_popup.update({"persone_web": int(max(1, parse_qta(box_p_web.textfield.value) or 8))}))

                def esegui_cerca_web(e=None):
                    qs = (tf_q_web.value or "").strip()
                    if not qs:
                        return
                    stato_popup["query_web"] = qs
                    stato_popup["persone_web"] = int(max(1, parse_qta(box_p_web.textfield.value) or 8))
                    banner_det.visible = True
                    lbl_det_status.value = f"🌐 Ricerca '{qs}'..."
                    page.update()
                    def w_s():
                        stato_popup["risultati_web"] = cerca_ricette_su_internet_dettagliate(qs, stato_popup["nome_ricetta"])
                        stato_popup["idx_web"] = 0
                        ricarica_ricetta()
                    threading.Thread(target=w_s, daemon=True).start()

                tf_q_web.on_submit = esegui_cerca_web
                render_anteprima()

                col_corpo.controls.append(
                    ft.Container(
                        content=ft.Column([
                            ft.Text("🔍 Ricerca Ricette Web & ✨ Gemini AI", size=12, weight=ft.FontWeight.BOLD, color=ft.Colors.ORANGE_900),
                            tf_q_web,
                            ft.Row([
                                box_p_web,
                                ft.Container(
                                    content=ft.Row([ft.Icon(ft.Icons.SEARCH, color=ft.Colors.WHITE, size=15), ft.Text("CERCA RICETTA", color=ft.Colors.WHITE, size=11, weight=ft.FontWeight.BOLD)], alignment=ft.MainAxisAlignment.CENTER, spacing=4),
                                    bgcolor=ft.Colors.ORANGE_800, height=34, expand=True, border_radius=6, on_click=esegui_cerca_web
                                )
                            ], spacing=6),
                            box_anteprima
                        ], spacing=5),
                        bgcolor="#FFF8E1", padding=8, border_radius=8, border=bordo_campo_uniforme("#FB8C00", 1.5)
                    )
                )

                banner_det.visible = False
                page.update()
            except Exception as ex:
                print("Errore ricarica_ricetta:", ex)
                banner_det.visible = False
                page.update()

        threading.Thread(target=worker, daemon=True).start()

    ricarica_ricetta()
# === FINE FILE modulo_ricette.py ===
