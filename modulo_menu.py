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
import urllib.error
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
# 🤖 MOTORE 1: INTELLIGENZA ARTIFICIALE GEMINI (SU RICHIESTA ESPLICITA)
# ==========================================================
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


def chiedi_ricetta_a_gemini_ai(query_ricerca: str, nome_ricetta_originale: str = ""):
    """
    Chiama direttamente le API di Google Gemini con User-Agent completo.
    Capisce istruzioni naturali come 'Pasta e pesto ma il pesto è già comprato pronto'.
    Restituisce (risultato_dict, messaggio_errore).
    """
    api_key = (os.environ.get("GEMINI_API_KEY") or GEMINI_API_KEY or "").strip()
    if not api_key:
        return None, "Chiave GEMINI_API_KEY non trovata nelle variabili Environment di Render."

    prompt = f"""Sei un assistente esperto di cucina casalinga italiana e lista della spesa.
L'utente ha chiesto: "{query_ricerca}" (per il piatto in menu: "{nome_ricetta_originale}").
Fornisci gli ingredienti semplici ed essenziali per 4 persone.
REGOLE FONDAMENTALI:
1. Se l'utente specifica che una parte è già pronta o comprata (es. "pasta e pesto col pesto già pronto", "tortellini col brodo pronto"), NON elencare gli ingredienti per preparare il condimento in casa (niente basilico o pinoli), ma metti la base da cuocere (es. "Pasta" 350 g) + il prodotto pronto da comprare (es. "Pesto pronto" 190 g).
2. Se invece è una normale ricetta casalinga (es. "Linguine con telline", "Tagliatelle al ragù", "Legumi misti"), elenca gli ingredienti semplici per cucinarla in famiglia.
3. Usa solo queste unità di misura: "g", "kg", "ml", "l", "pz", "fette", "cucchiai", "tazze", "q.b.". Se è "q.b.", metti qta_base a 0.
Rispondi SOLO con un JSON valido (senza markdown) con questa struttura esatta:
{{"titolo": "Titolo chiaro della ricetta", "preparazione_breve": "Spiegazione chiara in 2-3 frasi di come si prepara", "porzioni_base": 4, "ingredienti_base": [{{"nome": "Nome ingrediente", "qta_base": 350, "unita": "g"}}]}}"""

    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2}
    }).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }

    ultimo_errore = ""
    for model_name in ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]:
        try:
            url_api = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
            req = urllib.request.Request(url_api, data=payload, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                risp_json = json.loads(resp.read().decode("utf-8", errors="ignore"))
            testo_out = risp_json["candidates"][0]["content"]["parts"][0]["text"].strip()
            # Pulisce eventuali blocchi ```json ... ```
            testo_out = re.sub(r"^```(?:json)?\s*", "", testo_out, flags=re.IGNORECASE)
            testo_out = re.sub(r"\s*```$", "", testo_out).strip()
            m_json = re.search(r"\{.*\}", testo_out, flags=re.DOTALL)
            if m_json:
                testo_out = m_json.group(0)

            dati_ai = json.loads(testo_out)
            ings_validi = []
            for item in dati_ai.get("ingredienti_base", []):
                u = str(item.get("unita", "g")).strip()
                if u not in UNITA_MISURA:
                    u = "g"
                nome_i = str(item.get("nome", "")).strip().capitalize()
                if nome_i:
                    ings_validi.append({
                        "nome": nome_i,
                        "qta_base": float(item.get("qta_base") or 0),
                        "unita": u
                    })
            if ings_validi:
                q_enc = urllib.parse.quote_plus((nome_ricetta_originale or query_ricerca) + " ricetta")
                return {
                    "titolo": f"✨ {dati_ai.get('titolo') or nome_ricetta_originale}",
                    "sito": f"🤖 Intelligenza Artificiale ({model_name})",
                    "preparazione_breve": dati_ai.get("preparazione_breve", ""),
                    "url_fonte": f"https://www.google.com/search?q={q_enc}",
                    "foto_url": "",
                    "porzioni_base": int(dati_ai.get("porzioni_base") or 4),
                    "ingredienti_base": ings_validi
                }, ""
        except urllib.error.HTTPError as he:
            body_err = he.read().decode("utf-8", errors="ignore")[:160]
            ultimo_errore = f"HTTP {he.code}: {body_err}"
            continue
        except Exception as ex:
            ultimo_errore = str(ex)
            continue

    return None, f"Errore risposta Gemini: {ultimo_errore}"


# ==========================================================
# 🌐 MOTORE 2: RICERCA SUI SITI DI CUCINA (GIALLOZAFFERANO / CUCCHIAIO / MISYA)
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
            "titolo": re.sub(r"\s*[-|].*$", "", titolo_og) or "Ricetta dal Sito",
            "porzioni_base": 4,
            "raw_ings": raw_ings[:15],
            "foto_url": foto_og,
            "url_fonte": url_pagina
        }
    return None


def cerca_ricette_sui_siti_web(query_ricerca: str):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "it-IT,it;q=0.9"
    }
    low_q = query_ricerca.lower()
    query_pulita = re.sub(r"\([^)]*\)", " ", query_ricerca).strip() or query_ricerca.strip()

    queries_da_provare = [query_pulita]
    if "pizza di patate" in low_q or "tortino di patate" in low_q:
        queries_da_provare.append("gateau di patate")
    elif "tagliatelle al sugo" in low_q or "tagliatelle al rag" in low_q:
        queries_da_provare.append("tagliatelle al ragu alla bolognese")
    elif "gnocchetti" in low_q:
        queries_da_provare.append(re.sub(r"(?i)gnocchetti", "gnocchi", query_pulita))
    elif "telline" in low_q:
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

    risultati_trovati = []
    for link in links_candidati[:5]:
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
# POPUP DETTAGLIO RICETTA (APERTURA ISTANTANEA 0s + AI GEMINI & WEB SOLO SU RICHIESTA)
# ==========================================================
def apri_popup_dettaglio_ricetta_semplificata(page: ft.Page, id_voce_menu: str, id_ricetta_init: str, nome_ricetta_init: str, portata: str, on_close_refresh):
    if not supabase:
        return

    assicura_cache_anagrafica(forza_ricarica=False)
    spinner_det = ft.ProgressRing(width=18, height=18, stroke_width=2.5, color=ft.Colors.WHITE)
    lbl_det_status = ft.Text("Operazione in corso...", size=11, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD)
    banner_det = ft.Container(
        content=ft.Row([spinner_det, lbl_det_status], alignment=ft.MainAxisAlignment.CENTER, spacing=8),
        bgcolor=ft.Colors.ORANGE_800, padding=6, border_radius=6, visible=False
    )

    stato_popup = {
        "id_ricetta": str(id_ricetta_init or ""),
        "id_voce_menu": str(id_voce_menu or ""),
        "nome_ricetta": nome_ricetta_init,
        "query_web": nome_ricetta_init,
        "persone_web": 8,
        "mostra_web": False,
        "risultati_web": [],
        "idx_web": 0,
        "errore_ricerca": ""
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

                # ⚡ NESSUNA RICERCA AUTOMATICA ALL'APERTURA! SI APRE SUBITO IL PANNELLO PULITO ⚡
                if not ha_ing:
                    stato_popup["mostra_web"] = True

                col_corpo.controls.clear()

                if msg_extra:
                    col_corpo.controls.append(
                        ft.Container(
                            content=ft.Text(f"✅ {msg_extra}", size=11, color=ft.Colors.GREEN_800, weight=ft.FontWeight.BOLD),
                            bgcolor="#E8F5E9", padding=6, border_radius=6
                        )
                    )

                # =========================================================
                # 1. INSERIMENTO RAPIDO MANUALE (SEMPRE VISIBILE IN ALTO E IMMEDIATO)
                # =========================================================
                txt_nuovo_i = ft.TextField(
                    label="➕ Inserisci ingrediente a mano...",
                    value="",
                    hint_text="Es. Legumi misti, Pasta, Pesto pronto...",
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

                # =========================================================
                # 2. SE LA RICETTA HA INGREDIENTI: RICALCOLO PERSONE + LISTA VISIBILE
                # =========================================================
                if ha_ing:
                    riga_info_top = []
                    if foto_url_attuale:
                        riga_info_top.append(ft.Image(src=foto_url_attuale, width=52, height=52, border_radius=8))

                    url_link_attivo = url_fonte_attuale or f"https://www.google.com/search?q={urllib.parse.quote_plus(stato_popup['nome_ricetta'] + ' ricetta')}"
                    col_link_e_tit = [
                        ft.Text(f"👥 Dosi attuali calibrate per: {porzioni_attuali} {'persona' if porzioni_attuali == 1 else 'persone'}", size=12, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900),
                        ft.TextButton(
                            "🔗 Apri ricetta / preparazione in una nuova scheda",
                            url=url_link_attivo,
                            style=ft.ButtonStyle(padding=0)
                        )
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
                                        content=ft.Text("🔼 Chiudi Ricerca" if stato_popup["mostra_web"] else "🤖 / 🌐 Cerca con AI o Web", size=11, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                                        bgcolor=ft.Colors.DEEP_PURPLE_700 if not stato_popup["mostra_web"] else ft.Colors.BLUE_GREY_600,
                                        height=32, padding=ft.Padding(10, 0, 10, 0),
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

                # =========================================================
                # 3. PANNELLO RICERCA SU RICHIESTA (2 PULSANTI DISTINTI: AI GEMINI vs SITI WEB)
                # =========================================================
                if stato_popup["mostra_web"]:
                    tf_query_web = ft.TextField(
                        label="Cosa vuoi preparare? (es. 'Pasta e pesto col pesto già pronto')",
                        value=stato_popup["query_web"],
                        dense=True, text_size=12, expand=True
                    )
                    box_anteprima_candidato = ft.Column(spacing=5)

                    def aggiorna_anteprima_web_live():
                        box_anteprima_candidato.controls.clear()
                        if stato_popup.get("errore_ricerca"):
                            box_anteprima_candidato.controls.append(
                                ft.Container(
                                    content=ft.Text(f"⚠️ {stato_popup['errore_ricerca']}", size=11, color=ft.Colors.RED_800, weight=ft.FontWeight.BOLD),
                                    bgcolor="#FFEBEE", padding=6, border_radius=6
                                )
                            )
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
                                    stato_popup["risultati_web"] = []
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
                            for x in ings_scalati[:12]
                        ])

                        url_cand = cand.get("url_fonte") or f"https://www.google.com/search?q={urllib.parse.quote_plus(stato_popup['query_web'] + ' ricetta')}"
                        riga_foto_cand = []
                        if cand.get("foto_url"):
                            riga_foto_cand.append(ft.Image(src=cand["foto_url"], width=68, height=68, border_radius=8))

                        col_testi_cand = [
                            ft.Text(f"📌 {cand['titolo']}", size=12, weight=ft.FontWeight.BOLD, color=ft.Colors.DEEP_PURPLE_900 if "AI" in cand.get("sito", "") else ft.Colors.BLUE_900),
                            ft.Text(f"Fonte: {cand['sito']} (dosi per {p_target} pers.)", size=10, color=ft.Colors.GREY_700),
                            ft.TextButton(
                                "🔗 Apri ricetta / preparazione in una nuova scheda",
                                url=url_cand,
                                style=ft.ButtonStyle(padding=0)
                            )
                        ]
                        if cand.get("preparazione_breve"):
                            col_testi_cand.append(
                                ft.Container(
                                    content=ft.Text(f"👩‍🍳 Come si prepara: {cand['preparazione_breve']}", size=10, italic=True, color=ft.Colors.BLUE_GREY_900),
                                    bgcolor="#F3E5F5", padding=5, border_radius=5
                                )
                            )

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
                                    ft.Text(f"Ingredienti ({p_target}p): {anteprima_ings_str}", size=10, color=ft.Colors.GREY_900, weight=ft.FontWeight.BOLD),
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
                        valore_iniziale=str(stato_popup["persone_web"]), width=88, height=34,
                        text_size=12, mostra_prefisso=True, prefisso_custom="Pers.:", on_change_fn=on_change_persone_web
                    )

                    # PULSANTE 1: CHIEDI SOLO A GEMINI AI
                    def lancia_ricerca_solo_ai(e=None):
                        q_str = (tf_query_web.value or "").strip()
                        if not q_str:
                            return
                        stato_popup["query_web"] = q_str
                        stato_popup["persone_web"] = int(max(1, parse_qta(box_persone_web.textfield.value) or 8))
                        stato_popup["errore_ricerca"] = ""
                        banner_det.visible = True
                        lbl_det_status.value = f"✨ Gemini AI sta preparando '{q_str}'..."
                        page.update()

                        def w_ai():
                            ris_ai, err_msg = chiedi_ricetta_a_gemini_ai(q_str, stato_popup["nome_ricetta"])
                            if ris_ai:
                                stato_popup["risultati_web"] = [ris_ai]
                                stato_popup["idx_web"] = 0
                            else:
                                stato_popup["risultati_web"] = []
                                stato_popup["errore_ricerca"] = err_msg or "Impossibile contattare Gemini AI."
                            ricarica_ingredienti_ricetta()

                        threading.Thread(target=w_ai, daemon=True).start()

                    # PULSANTE 2: CERCA SUI SITI DI CUCINA (GIALLOZAFFERANO / WEB)
                    def lancia_ricerca_siti_web(e=None):
                        q_str = (tf_query_web.value or "").strip()
                        if not q_str:
                            return
                        stato_popup["query_web"] = q_str
                        stato_popup["persone_web"] = int(max(1, parse_qta(box_persone_web.textfield.value) or 8))
                        stato_popup["errore_ricerca"] = ""
                        banner_det.visible = True
                        lbl_det_status.value = f"🌐 Ricerca sui siti web '{q_str}'..."
                        page.update()

                        def w_web():
                            trovati = cerca_ricette_sui_siti_web(q_str)
                            if trovati:
                                stato_popup["risultati_web"] = trovati
                                stato_popup["idx_web"] = 0
                            else:
                                stato_popup["risultati_web"] = []
                                stato_popup["errore_ricerca"] = f"Nessuna ricetta trovata sui siti per '{q_str}'. Prova il pulsante '✨ Chiedi a Gemini AI'!"
                            ricarica_ingredienti_ricetta()

                        threading.Thread(target=w_web, daemon=True).start()

                    tf_query_web.on_submit = lancia_ricerca_solo_ai
                    aggiorna_anteprima_web_live()

                    col_corpo.controls.append(
                        ft.Container(
                            content=ft.Column([
                                ft.Text("🤖 Compilazione assistita (su tua richiesta):", size=12, weight=ft.FontWeight.BOLD, color=ft.Colors.DEEP_PURPLE_900),
                                tf_query_web,
                                ft.Row([
                                    box_persone_web,
                                    ft.Container(
                                        content=ft.Text("✨ CHIEDI A GEMINI AI", color=ft.Colors.WHITE, size=10, weight=ft.FontWeight.BOLD),
                                        bgcolor=ft.Colors.DEEP_PURPLE_700, height=34, expand=True, alignment=ft.Alignment(0, 0), border_radius=6,
                                        on_click=lancia_ricerca_solo_ai
                                    ),
                                    ft.Container(
                                        content=ft.Text("🌐 SITI WEB", color=ft.Colors.WHITE, size=10, weight=ft.FontWeight.BOLD),
                                        bgcolor=ft.Colors.ORANGE_800, height=34, padding=ft.Padding(10, 0, 10, 0), alignment=ft.Alignment(0, 0), border_radius=6,
                                        on_click=lancia_ricerca_siti_web
                                    )
                                ], spacing=5),
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
                        "data_menu": data_str,
                        "categoria": categoria,
                        "subcategoria": subcategoria,
                        "id_ricetta": id_ric
                    }).execute()
                except Exception:
                    ric_reale = ottieni_o_crea_ricetta_sicura(nome_pulito, subcategoria, ignora_cache=True)
                    id_ric = ric_reale["id"]
                    res_ins = supabase.table("menu_giornalieri").insert({
                        "data_menu": data_str,
                        "categoria": categoria,
                        "subcategoria": subcategoria,
                        "id_ricetta": id_ric
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
        content=ft.Container(
            content=ft.Column([txt_piatto, riga_sugg], tight=True, spacing=8),
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
                        content=ft.Text(f"{'🍲' if is_r else ('🧼' if is_categoria_detersivi else '🔍')} {n_p}", size=11, color=ft
    
