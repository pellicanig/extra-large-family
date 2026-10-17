import flet as ft
import os
import re
import json
import html
import time
import calendar
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

_CLIENT_POOL = []
_POOL_LOCK = threading.Lock()

class _ThreadSafeSupabase:
    def table(self, nome_tabella: str):
        with _POOL_LOCK:
            if not _CLIENT_POOL:
                for _ in range(3):
                    c = create_client(SUPABASE_URL, SUPABASE_KEY)
                    _ = c.postgrest
                    _CLIENT_POOL.append(c)
            cli = _CLIENT_POOL.pop(0)
            _CLIENT_POOL.append(cli)
        return cli.table(nome_tabella)

try:
    supabase = _ThreadSafeSupabase() if (SUPABASE_URL and SUPABASE_KEY) else None
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

MODELLO_GEMINI_RILEVATO = [None]
ULTIMA_DATA_MENU_ATTIVA = [datetime.now()]


def log_render(messaggio: str):
    ora_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[ELF-LOG {ora_str}] {messaggio}", flush=True)


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

    if CACHE_ANAGRAFICA["ricette"]:
        for r in CACHE_ANAGRAFICA["ricette"]:
            if str(r.get("id")) == str(id_ricetta):
                r["portata_default"] = val_codificato
                if nuovo_nome:
                    r["nome"] = nuovo_nome
                break

    try:
        supabase.table("ricette").update(payload).eq("id", id_ricetta).execute()
    except Exception:
        payload["portata_default"] = codifica_meta_portata(portata, porzioni, url_fonte, "")
        try:
            supabase.table("ricette").update(payload).eq("id", id_ricetta).execute()
        except Exception:
            payload["portata_default"] = codifica_meta_portata(portata, porzioni, "", "")
            supabase.table("ricette").update(payload).eq("id", id_ricetta).execute()


def normalizza_ingrediente_record(ing_raw: dict):
    u_raw = str(ing_raw.get("unita_default") or "g")
    is_det = "|DET" in u_raw
    is_disp = "|DISPENSA" in u_raw
    u_clean = u_raw.replace("|DET", "").replace("|DISPENSA", "").strip() or ("pz" if is_det else "g")

    copia = dict(ing_raw)
    copia["unita_default"] = u_clean if u_clean in UNITA_MISURA else "pz"
    copia["is_detersivo"] = is_det
    copia["is_dispensa_base"] = is_disp
    return copia


def generalizza_nome_dispensa(nome_raw: str) -> str:
    """
    Normalizza semanticamente i nomi per la Dispensa:
    - Unifica varianti culinarie (es. 'Sale grosso' / 'Sale fino' -> 'Sale', 'Pepe nero' -> 'Pepe',
      'Cipolle ramate' -> 'Cipolla', 'Costa sedano' -> 'Sedano', 'Uova medie' -> 'Uova',
      'Olio' / 'Olio evo' -> 'Olio extravergine d'oliva')
    - Preserva le distinzioni merceologiche reali ('Olio di semi' != 'Olio extravergine d'oliva',
      'Vino rosso' != 'Vino bianco', 'Farina di mais' != 'Farina', 'Zucchero di canna' != 'Zucchero').
    """
    s = re.sub(r"\s+", " ", (nome_raw or "").strip()).lower()
    if not s:
        return ""

    # 1. Rimuove prefissi di taglio/porzione/quantità discorsive tipici delle ricette web
    s = re.sub(
        r"^(un pizzico di |pizzico di |qualche grano di |grani di |qualche foglia di |foglie di |foglia di |"
        r"un filo di |un filo d'|filo di |filo d'|una noce di |noce di |un cucchiaio di |cucchiaio di |"
        r"un cucchiaino di |cucchiaino di |costa di |coste di |costa |coste |gambo di |gambi di |"
        r"uno spicchio di |uno spicchio d'|spicchio di |spicchio d'|spicchi di |spicchi d'|testa di |"
        r"rametto di |rametti di |ciuffo di |ciuffi di |scorza grattugiata di |scorza di |succo di |"
        r"macinata di |mezza |mezzo )",
        "", s
    ).strip()

    # 2. Famiglie con specificatori merceologici protetti (Olio, Vino, Aceto, Farina, Zucchero, Latte)
    if s.startswith("olio"):
        if any(k in s for k in ["semi", "arachid", "girasol", "mais", "frigg", "fritt", "soia"]):
            return "Olio di semi"
        for spec in ["cocco", "sesamo", "noce", "noci", "tartufo", "peperoncino", "riso", "lino"]:
            if spec in s:
                return s.capitalize()
        return "Olio extravergine d'oliva"

    if s.startswith("vino"):
        if "bianco" in s:
            return "Vino bianco"
        if "rosso" in s or "nero" in s:
            return "Vino rosso"
        if "rosato" in s or "rosè" in s:
            return "Vino rosato"
        if "marsala" in s:
            return "Marsala"
        return "Vino"

    if s.startswith("aceto"):
        if "mele" in s:
            return "Aceto di mele"
        if "balsamico" in s:
            return "Aceto balsamico"
        if "riso" in s:
            return "Aceto di riso"
        return "Aceto"

    if s.startswith("farina"):
        for spec in ["mais", "polenta", "ceci", "mandorl", "castagn", "riso", "cocco", "avena", "integrale", "manitoba", "semola", "grano saraceno", "farro"]:
            if spec in s:
                return s.capitalize()
        return "Farina"

    if s.startswith("zucchero"):
        if "velo" in s:
            return "Zucchero a velo"
        if any(k in s for k in ["canna", "bruno", "mascobado", "grezzo"]):
            return "Zucchero di canna"
        return "Zucchero"

    if s.startswith("latte"):
        if any(k in s for k in ["soia", "cocco", "mandorl", "avena", "riso", "condensato", "polvere"]):
            return s.capitalize()
        return "Latte"

    # 3. Raggruppamento radici base della dispensa (incluso Sale grosso / fino / marino -> Sale)
    if (s == "sale" or s.startswith("sale ")) and "lavastoviglie" not in s:
        return "Sale"
    if (s == "pepe" or s.startswith("pepe ")) and not s.startswith("peperon"):
        return "Pepe"
    if s.startswith("cipoll") and not s.startswith("cipollin"):
        return "Cipolla"
    if s.startswith("scalogn"):
        return "Scalogno"
    if s.startswith("agli") or "aglio" in s:
        return "Aglio"
    if s.startswith("sedan") or "sedano" in s:
        return "Sedano"
    if s.startswith("carot"):
        return "Carota"
    if s.startswith("uov") or s in ["tuorlo", "tuorli", "albume", "albumi", "tuorlo d'uovo", "albume d'uovo"]:
        return "Uova"
    if s.startswith("parmigian") or s.startswith("grana padan") or s == "grana":
        return "Parmigiano"
    if s.startswith("pecorin"):
        return "Pecorino"
    if s.startswith("burro") and "arachid" not in s and "cacao" not in s:
        return "Burro"
    if s.startswith("pangrattat") or s.startswith("pane grattugiat") or s.startswith("pan grattat"):
        return "Pangrattato"
    if "concentrato di pomodoro" in s or "triplo concentrato" in s:
        return "Concentrato di pomodoro"
    if s.startswith("passata di pomodor") or s == "passata":
        return "Passata di pomodoro"
    if s.startswith("dado") or s.startswith("brodo vegetale") or s.startswith("brodo di "):
        return "Dado vegetale"
    if s.startswith("acqua") and not any(k in s for k in ["tonica", "cocco", "rose", "fiori"]):
        return "Acqua"
    if s.startswith("alloro"):
        return "Alloro"
    if s.startswith("rosmarin"):
        return "Rosmarino"
    if s.startswith("prezzemol"):
        return "Prezzemolo"
    if s.startswith("basilic"):
        return "Basilico"
    if s.startswith("peperoncin"):
        return "Peperoncino"
    if s.startswith("origano"):
        return "Origano"
    if s.startswith("salvia"):
        return "Salvia"
    if s.startswith("timo"):
        return "Timo"
    if s.startswith("noce moscata"):
        return "Noce moscata"
    if s.startswith("cannella"):
        return "Cannella"
    if s.startswith("bicarbonato"):
        return "Bicarbonato"

    # 4. Confronto dinamico con gli ingredienti già marcati |DISPENSA in anagrafica
    for ig in (CACHE_ANAGRAFICA.get("ingredienti") or []):
        if ig.get("is_dispensa_base") or "|DISPENSA" in str(ig.get("unita_default") or ""):
            n_ig = ig.get("nome", "").strip().lower()
            if not n_ig:
                continue
            if s == n_ig:
                return ig.get("nome", "").strip().capitalize()
            if s.startswith(n_ig + " ") and " di " not in s[len(n_ig):] and " d'" not in s[len(n_ig):]:
                return ig.get("nome", "").strip().capitalize()

    if " di " not in s and " d'" not in s and " al " not in s and " alla " not in s:
        prima_parola = s.split(" ")[0]
        if len(prima_parola) >= 4:
            radice = prima_parola[:-1]
            for ig in (CACHE_ANAGRAFICA.get("ingredienti") or []):
                if ig.get("is_dispensa_base") or "|DISPENSA" in str(ig.get("unita_default") or ""):
                    n_ig = ig.get("nome", "").strip().lower()
                    if n_ig == prima_parola or (" " not in n_ig and len(n_ig) >= 4 and n_ig[:-1] == radice):
                        return ig.get("nome", "").strip().capitalize()

    return s.capitalize()


def is_ingrediente_dispensa_attivo(ing_obj_or_nome) -> bool:
    """
    Restituisce True se l'ingrediente (o la sua forma generalizzata, es. 'Sale grosso' -> 'Sale')
    è attualmente configurato come ingrediente da Dispensa (|DISPENSA).
    """
    if not ing_obj_or_nome:
        return False
    if isinstance(ing_obj_or_nome, dict):
        u_def = str(ing_obj_or_nome.get("unita_default") or "")
        if "|DISPENSA" in u_def or ing_obj_or_nome.get("is_dispensa_base"):
            return True
        nome_raw = ing_obj_or_nome.get("nome") or ""
    else:
        nome_raw = str(ing_obj_or_nome)

    nome_clean = nome_raw.strip()
    if not nome_clean:
        return False
    nome_low = nome_clean.lower()
    nome_gen = generalizza_nome_dispensa(nome_clean).lower()

    for ig in (CACHE_ANAGRAFICA.get("ingredienti") or []):
        if ig.get("is_dispensa_base") or "|DISPENSA" in str(ig.get("unita_default") or ""):
            n_ig = (ig.get("nome") or "").strip().lower()
            if n_ig == nome_low or n_ig == nome_gen or generalizza_nome_dispensa(n_ig).lower() == nome_gen:
                return True
    return False


def ottieni_o_crea_ing_dispensa_canonico(nome_raw: str) -> str:
    """Restituisce l'ID dell'ingrediente generalizzato per la Dispensa e si assicura che abbia il flag |DISPENSA."""
    nome_gen = generalizza_nome_dispensa(nome_raw)
    if not nome_gen:
        return None
    id_ing = ottieni_o_crea_ing_sicuro(nome_gen, "pz", is_detersivo=False)
    if is_valid_uuid(id_ing):
        ing_c = next((x for x in (CACHE_ANAGRAFICA.get("ingredienti") or []) if str(x.get("id")) == str(id_ing)), None)
        u_base = (ing_c.get("unita_default") if ing_c else "pz") or "pz"
        u_clean = str(u_base).replace("|DET", "").replace("|DISPENSA", "").strip() or "pz"
        if not ing_c or not ing_c.get("is_dispensa_base"):
            nuova_u = f"{u_clean}|DISPENSA"
            try:
                supabase.table("ingredienti").update({"unita_default": nuova_u}).eq("id", id_ing).execute()
                log_render(f"[DISPENSA] Impostato flag |DISPENSA su ingrediente canonico '{nome_gen}' (ID: {id_ing})")
            except Exception as ex:
                log_render(f"[ERRORE] Impossibile aggiornare flag |DISPENSA su '{nome_gen}': {ex}")
            if ing_c:
                ing_c["is_dispensa_base"] = True
                ing_c["unita_default"] = u_clean
    return id_ing


def imposta_stato_dispensa_ingrediente_db(nome_ing_raw: str, id_ing_raw: str, nuovo_stato: bool, data_rif: datetime = None):
    """
    Commuta lo stato Dispensa (ON/OFF) su un ingrediente e su tutte le sue varianti generalizzate
    (es. 'Sale grosso' <-> 'Sale'), aggiornando immediatamente la Dispensa del mese.
    """
    if not supabase:
        return
    dt = data_rif or ULTIMA_DATA_MENU_ATTIVA[0] or datetime.now()
    ULTIMA_DATA_MENU_ATTIVA[0] = dt
    nome_gen = generalizza_nome_dispensa(nome_ing_raw)
    log_render(f"[UTENTE] Commutazione stato Dispensa={'ATTIVO' if nuovo_stato else 'DISATTIVO'} per '{nome_ing_raw}' (Generalizzato: '{nome_gen}') sul mese {dt.strftime('%m/%Y')}")

    assicura_cache_anagrafica(forza_ricarica=False)
    ids_coinvolti = set()
    if is_valid_uuid(id_ing_raw):
        ids_coinvolti.add(str(id_ing_raw))

    # Aggiorna in cache e su DB tutti gli ingredienti che condividono la stessa radice generalizzata
    for ig in (CACHE_ANAGRAFICA.get("ingredienti") or []):
        n_ig = (ig.get("nome") or "").strip()
        if not n_ig:
            continue
        if n_ig.lower() == (nome_ing_raw or "").strip().lower() or generalizza_nome_dispensa(n_ig).lower() == nome_gen.lower():
            ig["is_dispensa_base"] = nuovo_stato
            u_clean = str(ig.get("unita_default") or "g").replace("|DET", "").replace("|DISPENSA", "").strip() or "g"
            tag_det = "|DET" if ig.get("is_detersivo") else ""
            tag_disp = "|DISPENSA" if nuovo_stato else ""
            nuova_u_db = f"{u_clean}{tag_det}{tag_disp}"
            if is_valid_uuid(ig.get("id")):
                ids_coinvolti.add(str(ig["id"]))
                try:
                    supabase.table("ingredienti").update({"unita_default": nuova_u_db}).eq("id", ig["id"]).execute()
                except Exception as ex:
                    log_render(f"[ERRORE] Update unita_default ingrediente '{n_ig}': {ex}")

    if nuovo_stato:
        id_can = ottieni_o_crea_ing_dispensa_canonico(nome_gen)
        if is_valid_uuid(id_can):
            assicura_ingrediente_in_dispensa_mese(id_can, dt)
    else:
        if ids_coinvolti:
            try:
                supabase.table("menu_giornalieri").delete().eq("categoria", "Dispensa").in_("id_ingrediente", list(ids_coinvolti)).execute()
                log_render(f"[DISPENSA] Rimosso '{nome_gen}' (IDs: {list(ids_coinvolti)}) dalla Dispensa perché deselezionato.")
            except Exception as ex:
                log_render(f"[ERRORE] Rimozione da Dispensa per '{nome_gen}': {ex}")

    sincronizza_e_pulisci_dispensa_mese(dt)


def assicura_ingrediente_in_dispensa_mese(id_ingrediente: str, data_rif: datetime = None):
    """Inserisce l'ingrediente (in forma generalizzata) nella Dispensa del mese di riferimento se non è già presente."""
    if not supabase or not is_valid_uuid(id_ingrediente):
        return
    dt = data_rif or ULTIMA_DATA_MENU_ATTIVA[0] or datetime.now()
    ULTIMA_DATA_MENU_ATTIVA[0] = dt
    primo_mese = dt.replace(day=1).strftime("%Y-%m-%d")
    ultimo_gg = calendar.monthrange(dt.year, dt.month)[1]
    fine_mese = dt.replace(day=ultimo_gg).strftime("%Y-%m-%d")
    try:
        ing_c = next((x for x in (CACHE_ANAGRAFICA.get("ingredienti") or []) if str(x.get("id")) == str(id_ingrediente)), None)
        if ing_c and ing_c.get("nome"):
            id_target = ottieni_o_crea_ing_dispensa_canonico(ing_c["nome"]) or id_ingrediente
            nome_log = generalizza_nome_dispensa(ing_c["nome"])
        else:
            id_target = id_ingrediente
            nome_log = str(id_ingrediente)

        esistente = (
            supabase.table("menu_giornalieri")
            .select("id")
            .eq("categoria", "Dispensa")
            .eq("id_ingrediente", id_target)
            .gte("data_menu", primo_mese)
            .lte("data_menu", fine_mese)
            .limit(1)
            .execute()
        )
        if not esistente.data:
            supabase.table("menu_giornalieri").insert({
                "data_menu": primo_mese,
                "categoria": "Dispensa",
                "subcategoria": None,
                "id_ingrediente": id_target,
                "quantita": 0,
                "unita_misura": "pz",
                "preso": False
            }).execute()
            log_render(f"[DISPENSA] Aggiunto automaticamente '{nome_log}' nella Dispensa del mese {dt.strftime('%m/%Y')}")
    except Exception as ex:
        log_render(f"[ERRORE] assicura_ingrediente_in_dispensa_mese: {ex}")


def sincronizza_e_pulisci_dispensa_mese(data_rif: datetime = None):
    """
    Sincronizza in modo bidirezionale la Dispensa per il mese di data_rif:
    1. Legge tutte le ricette e gli ingredienti pianificati nel mese.
    2. Individua tutti gli ingredienti marcati come Dispensa (o le cui varianti generalizzate sono in Dispensa).
    3. Rimuove automaticamente dalla Dispensa del mese gli ingredienti derivati da ricette che sono state
       eliminate, modificate o i cui ingredienti non sono più presenti/flaggati nel mese (preservando le voci manuali).
    4. Generalizza i nomi (es. 'Sale grosso' -> 'Sale'), elimina i doppioni e aggiunge i mancanti.
    """
    if not supabase:
        return
    dt = data_rif or ULTIMA_DATA_MENU_ATTIVA[0] or datetime.now()
    ULTIMA_DATA_MENU_ATTIVA[0] = dt
    primo_mese = dt.replace(day=1).strftime("%Y-%m-%d")
    ultimo_gg = calendar.monthrange(dt.year, dt.month)[1]
    fine_mese = dt.replace(day=ultimo_gg).strftime("%Y-%m-%d")

    try:
        log_render(f"[SYNC DISPENSA] Avvio sincronizzazione Dispensa per il mese {dt.strftime('%m/%Y')} ({primo_mese} -> {fine_mese})")
        lista_ing_cache, _ = assicura_cache_anagrafica(forza_ricarica=False)
        nomi_dispensa_attivi = {
            generalizza_nome_dispensa(i["nome"]).lower()
            for i in lista_ing_cache if (i.get("is_dispensa_base") or "|DISPENSA" in str(i.get("unita_default") or "")) and i.get("nome")
        }

        res_mese = (
            supabase.table("menu_giornalieri")
            .select("*, ingredienti(id, nome, unita_default)")
            .gte("data_menu", primo_mese)
            .lte("data_menu", fine_mese)
            .execute()
        )
        righe_mese = res_mese.data or []
        righe_dispensa = [r for r in righe_mese if r.get("categoria") == "Dispensa"]
        ids_ricette_mese = list({
            r["id_ricetta"] for r in righe_mese
            if r.get("categoria") not in ["Dispensa", "Detersivi"] and is_valid_uuid(r.get("id_ricetta"))
        })

        # 1. Raccoglie i nomi generalizzati richiesti dalle ricette e dai pasti pianificati nel mese
        richiesti_da_ricette = set()

        # Controlla anche eventuali ingredienti diretti nei pasti del mese che hanno flag Dispensa
        for r_pasto in righe_mese:
            if r_pasto.get("categoria") not in ["Dispensa", "Detersivi"] and r_pasto.get("id_ingrediente"):
                ing_p = r_pasto.get("ingredienti") or {}
                nome_p = (ing_p.get("nome") or "").strip()
                if nome_p:
                    nome_gen_p = generalizza_nome_dispensa(nome_p)
                    if "|DISPENSA" in str(ing_p.get("unita_default") or "") or nome_gen_p.lower() in nomi_dispensa_attivi:
                        richiesti_da_ricette.add(nome_gen_p)

        if ids_ricette_mese:
            res_ri = (
                supabase.table("ricette_ingredienti")
                .select("*, ingredienti(id, nome, unita_default)")
                .in_("id_ricetta", ids_ricette_mese)
                .execute()
            )
            for ri in (res_ri.data or []):
                ing_d = ri.get("ingredienti") or {}
                nome_i = (ing_d.get("nome") or "").strip()
                if not nome_i:
                    continue
                u_def = str(ing_d.get("unita_default") or "")
                nome_gen = generalizza_nome_dispensa(nome_i)
                if "|DISPENSA" in u_def or nome_gen.lower() in nomi_dispensa_attivi or is_ingrediente_dispensa_attivo(ing_d):
                    richiesti_da_ricette.add(nome_gen)
                    nomi_dispensa_attivi.add(nome_gen.lower())
                    # Se la variante (es. 'Sale grosso') non aveva ancora il flag in cache, lo allinea
                    for ig_c in lista_ing_cache:
                        if ig_c.get("nome", "").strip().lower() == nome_i.lower():
                            ig_c["is_dispensa_base"] = True

        richiesti_low_set = {x.lower() for x in richiesti_da_ricette}

        # 2. Esamina le righe già presenti nella Dispensa del mese:
        #    - Elimina quelle non più richieste da alcuna ricetta del mese (a meno che non siano state inserite manualmente con subcategoria == 'Manuale')
        #    - Deduplica e normalizza quelle valide
        gruppi_disp = {}
        for rd in righe_dispensa:
            nome_orig = ((rd.get("ingredienti") or {}).get("nome") or "").strip()
            if not nome_orig:
                continue
            nome_gen = generalizza_nome_dispensa(nome_orig)
            gruppi_disp.setdefault(nome_gen.lower(), {"nome_gen": nome_gen, "righe": []})["righe"].append(rd)

        visti_canonici = set()
        for k_low, info_g in gruppi_disp.items():
            nome_gen = info_g["nome_gen"]
            righe_g = info_g["righe"]
            is_manuale = any(x.get("subcategoria") == "Manuale" for x in righe_g)

            # Se l'ingrediente non è usato in nessuna ricetta del mese e non è un'aggiunta manuale, lo rimuove dalla Dispensa del mese
            if (k_low not in richiesti_low_set) and (not is_manuale or k_low not in nomi_dispensa_attivi):
                for r_del in righe_g:
                    try:
                        supabase.table("menu_giornalieri").delete().eq("id", r_del["id"]).execute()
                        log_render(f"[SYNC DISPENSA] Rimosso '{nome_gen}' dalla Dispensa di {dt.strftime('%m/%Y')} (non più presente nelle ricette del mese)")
                    except Exception as ex:
                        log_render(f"[ERRORE] Eliminazione riga dispensa obsoleta '{nome_gen}': {ex}")
                continue

            visti_canonici.add(k_low)
            id_canonico = ottieni_o_crea_ing_dispensa_canonico(nome_gen)
            if not is_valid_uuid(id_canonico):
                continue

            preso_unificato = any(bool(x.get("preso")) for x in righe_g)
            sub_unificata = "Manuale" if (is_manuale and k_low not in richiesti_low_set) else None
            riga_principale = next((x for x in righe_g if str(x.get("id_ingrediente")) == str(id_canonico)), righe_g[0])

            if (str(riga_principale.get("id_ingrediente")) != str(id_canonico)
                    or bool(riga_principale.get("preso")) != preso_unificato
                    or riga_principale.get("subcategoria") != sub_unificata):
                try:
                    supabase.table("menu_giornalieri").update({
                        "id_ingrediente": id_canonico,
                        "preso": preso_unificato,
                        "subcategoria": sub_unificata,
                        "quantita": 0,
                        "unita_misura": "pz"
                    }).eq("id", riga_principale["id"]).execute()
                    log_render(f"[SYNC DISPENSA] Normalizzata voce Dispensa -> '{nome_gen}' (Preso: {preso_unificato})")
                except Exception as ex:
                    log_render(f"[ERRORE] Normalizzazione riga dispensa '{nome_gen}': {ex}")

            for r_dup in righe_g:
                if r_dup["id"] != riga_principale["id"]:
                    try:
                        supabase.table("menu_giornalieri").delete().eq("id", r_dup["id"]).execute()
                        log_render(f"[SYNC DISPENSA] Eliminato duplicato Dispensa per '{nome_gen}'")
                    except Exception:
                        pass

        # 3. Inserisce nella Dispensa del mese gli ingredienti base delle ricette non ancora presenti
        nuove_righe_disp = []
        for nome_req in richiesti_da_ricette:
            if nome_req.lower() not in visti_canonici:
                id_can = ottieni_o_crea_ing_dispensa_canonico(nome_req)
                if is_valid_uuid(id_can):
                    visti_canonici.add(nome_req.lower())
                    nuove_righe_disp.append({
                        "data_menu": primo_mese,
                        "categoria": "Dispensa",
                        "subcategoria": None,
                        "id_ingrediente": id_can,
                        "quantita": 0,
                        "unita_misura": "pz",
                        "preso": False
                    })
                    log_render(f"[SYNC DISPENSA] Inserito nuovo ingrediente '{nome_req}' in Dispensa ({dt.strftime('%m/%Y')})")
        if nuove_righe_disp:
            supabase.table("menu_giornalieri").insert(nuove_righe_disp).execute()

        log_render(f"[SYNC DISPENSA] Completata per {dt.strftime('%m/%Y')}: {len(visti_canonici)} ingredienti attivi in Dispensa.")
    except Exception as ex:
        log_render(f"[ERRORE] sincronizza_e_pulisci_dispensa_mese: {ex}")





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
    val_pulito = str(valore_iniziale or "g").replace("|DET", "").replace("|DISPENSA", "").strip()
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
        v_clean = str(nuovo_v or "").replace("|DET", "").replace("|DISPENSA", "").strip()
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


def cerca_ingrediente_in_cache_per_nome(nome_raw: str):
    nome_low = re.sub(r"\s+", " ", (nome_raw or "").strip()).lower()
    if not nome_low:
        return None
    lista_ing = CACHE_ANAGRAFICA.get("ingredienti") or []
    return next((i for i in lista_ing if i.get("nome", "").strip().lower() == nome_low), None)


def ottieni_o_crea_ing_sicuro(nome_raw: str, unita_scelta: str, is_detersivo: bool = False, ignora_cache: bool = False):
    nome_pulito = re.sub(r"\s+", " ", (nome_raw or "").strip()).capitalize()
    if not nome_pulito:
        return None
    lista_ing, _ = assicura_cache_anagrafica(forza_ricarica=ignora_cache)
    unita_clean = str(unita_scelta or "g").replace("|DET", "").replace("|DISPENSA", "").strip()
    if unita_clean not in UNITA_MISURA:
        unita_clean = "pz" if is_detersivo else "g"

    # Se la forma generalizzata (es. 'Sale grosso' -> 'Sale') è già in Dispensa, eredita automaticamente |DISPENSA
    is_disp_ereditato = (not is_detersivo) and is_ingrediente_dispensa_attivo(nome_pulito)
    tag_det = "|DET" if is_detersivo else ""
    tag_disp = "|DISPENSA" if is_disp_ereditato else ""
    unita_db = f"{unita_clean}{tag_det}{tag_disp}"

    if not ignora_cache:
        esistente = next((i for i in lista_ing if i["nome"].strip().lower() == nome_pulito.lower()), None)
        if esistente:
            aggiorna_db = False
            if is_detersivo and not esistente.get("is_detersivo"):
                esistente["is_detersivo"] = True
                CACHE_ANAGRAFICA["ids_detersivi"].add(esistente["id"])
                aggiorna_db = True
            if is_disp_ereditato and not esistente.get("is_dispensa_base"):
                esistente["is_dispensa_base"] = True
                aggiorna_db = True
            if aggiorna_db:
                u_c = esistente.get("unita_default", unita_clean)
                t_det = "|DET" if esistente.get("is_detersivo") else ""
                t_disp = "|DISPENSA" if esistente.get("is_dispensa_base") else ""
                try:
                    supabase.table("ingredienti").update({"unita_default": f"{u_c}{t_det}{t_disp}"}).eq("id", esistente["id"]).execute()
                except Exception:
                    pass
            return esistente["id"]

    try:
        res_db = supabase.table("ingredienti").select("*").ilike("nome", nome_pulito).limit(1).execute()
        if res_db.data:
            trovato = normalizza_ingrediente_record(res_db.data[0])
            if is_disp_ereditato and not trovato.get("is_dispensa_base"):
                trovato["is_dispensa_base"] = True
                try:
                    supabase.table("ingredienti").update({"unita_default": unita_db}).eq("id", trovato["id"]).execute()
                except Exception:
                    pass
            CACHE_ANAGRAFICA["ingredienti"] = [x for x in lista_ing if x["nome"].strip().lower() != nome_pulito.lower()] + [trovato]
            return trovato["id"]
    except Exception:
        pass

    try:
        res_ins = supabase.table("ingredienti").insert({"nome": nome_pulito, "unita_default": unita_db}).execute()
        nuovo = normalizza_ingrediente_record(res_ins.data[0])
        CACHE_ANAGRAFICA["ingredienti"] = [x for x in lista_ing if x["nome"].strip().lower() != nome_pulito.lower()] + [nuovo]
        log_render(f"[ANAGRAFICA] Creato nuovo ingrediente '{nome_pulito}' (unita_db: '{unita_db}')")
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
        log_render(f"[ANAGRAFICA] Creata nuova ricetta '{nome_pulito}' (ID: {nuova['id']})")
        return nuova
    except Exception:
        res_retry = supabase.table("ricette").select("*").ilike("nome", nome_pulito).limit(1).execute()
        if res_retry.data:
            return res_retry.data[0]
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
        log_render(f"[ERRORE] aggiorna_conteggio_ricette_visibili: {ex}")


def ricetta_ha_ingredienti(id_ricetta):
    if not is_valid_uuid(id_ricetta):
        return False
    return (CACHE_ANAGRAFICA.get("conteggio_ing_ricette") or {}).get(str(id_ricetta), 0) > 0


def salva_lista_ingredienti_in_blocco(id_ricetta: str, lista_items: list):
    if not is_valid_uuid(id_ricetta):
        return 0
    log_render(f"[RICETTA] Salvataggio in blocco di {len(lista_items)} ingredienti per ricetta ID={id_ricetta}")
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
        except Exception as ex:
            log_render(f"[ERRORE] Insert bulk ricette_ingredienti: {ex}")

    CACHE_ANAGRAFICA["conteggio_ing_ricette"][str(id_ricetta)] = len(righe_ri_bulk)
    sincronizza_e_pulisci_dispensa_mese(ULTIMA_DATA_MENU_ATTIVA[0])
    return len(righe_ri_bulk)


# ==========================================================
# 🤖 MOTORE AI GEMINI + SITI WEB (IN PARALLELO)
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

    return {"nome": nome_finale[:60].capitalize(), "qta_base": qta_base, "unita": unita_trovata}


def scala_ingredienti_candidato(candidato_web: dict, num_persone_target: int):
    porzioni_base = max(1, int(candidato_web.get("porzioni_base") or 4))
    fattore = float(max(1, num_persone_target)) / float(porzioni_base)
    scalati = []
    for ing_b in candidato_web.get("ingredienti_base", []):
        q_b = float(ing_b.get("qta_base") or 0)
        uni = ing_b.get("unita") or "g"
        if uni == "q.b." or q_b <= 0:
            q_fin, uni = 0.0, "q.b."
        else:
            q_fin = q_b * fattore
            if uni in ["g", "ml"] and q_fin >= 20:
                q_fin = round(q_fin / 5.0) * 5.0
            elif uni == "pz":
                q_fin = max(1.0, round(q_fin))
            else:
                q_fin = round(q_fin, 2)
        scalati.append({"nome": ing_b["nome"], "quantita": q_fin, "unita": uni})
    return scalati


def genera_fallback_ricetta(query_ricerca: str, nome_ricetta_originale: str = ""):
    nome_base = (nome_ricetta_originale or query_ricerca or "Ricetta").strip()
    return {
        "titolo": f"Ricetta: {nome_base}",
        "sito": "Inserimento Manuale",
        "preparazione_breve": "Usa il box in alto per aggiungere gli ingredienti desiderati.",
        "url_fonte": f"https://www.google.com/search?q={urllib.parse.quote_plus(nome_base + ' ricetta')}",
        "foto_url": "",
        "porzioni_base": 4,
        "ingredienti_base": []
    }


_MODELLI_NON_DISPONIBILI = set()
_CACHE_MODELLI_ACCOUNT = []

def ottieni_lista_modelli_gemini(api_key: str):
    if MODELLO_GEMINI_RILEVATO[0] and MODELLO_GEMINI_RILEVATO[0] not in _MODELLI_NON_DISPONIBILI:
        resto = [m for m in _CACHE_MODELLI_ACCOUNT if m != MODELLO_GEMINI_RILEVATO[0] and m not in _MODELLI_NON_DISPONIBILI]
        return [MODELLO_GEMINI_RILEVATO[0]] + resto

    scarti = ("tts", "image", "audio", "embedding", "vision", "aqa", "robotics", "computer", "thinking", "exp", "preview")
    preferiti = [
        "gemini-2.0-flash",
        "gemini-2.0-flash-001",
        "gemini-2.0-flash-lite",
        "gemini-2.0-flash-lite-001",
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash",
        "gemini-flash-latest",
        "gemini-1.5-flash",
        "gemini-1.5-flash-latest"
    ]

    if not _CACHE_MODELLI_ACCOUNT:
        try:
            url_list = f"https://generativelanguage.googleapis.com/v1beta/models?key={urllib.parse.quote(api_key)}"
            req = urllib.request.Request(url_list, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                dati = json.loads(resp.read().decode("utf-8", errors="ignore"))
            disponibili = [
                m.get("name", "").replace("models/", "")
                for m in dati.get("models", [])
                if "generateContent" in m.get("supportedGenerationMethods", [])
                and "gemini" in m.get("name", "").lower()
                and not any(s in m.get("name", "").lower() for s in scarti)
            ]
            log_render(f"[GEMINI] Modelli rilevati sull'account API: {disponibili[:10]}")
            for p in preferiti:
                if p in disponibili and p not in _CACHE_MODELLI_ACCOUNT:
                    _CACHE_MODELLI_ACCOUNT.append(p)
            for d in disponibili:
                if "flash" in d.lower() and d not in _CACHE_MODELLI_ACCOUNT:
                    _CACHE_MODELLI_ACCOUNT.append(d)
            for d in disponibili:
                if d not in _CACHE_MODELLI_ACCOUNT:
                    _CACHE_MODELLI_ACCOUNT.append(d)
        except Exception as ex:
            Dettaglio = ""
            if hasattr(ex, "read"):
                try:
                    Dettaglio = ex.read().decode("utf-8", errors="ignore")[:200]
                except Exception:
                    pass
            log_render(f"[GEMINI] Impossibile leggere lista modelli ({ex}) {Dettaglio}")

    base_lista = _CACHE_MODELLI_ACCOUNT if _CACHE_MODELLI_ACCOUNT else preferiti
    attivi = [m for m in base_lista if m not in _MODELLI_NON_DISPONIBILI]
    return attivi if attivi else preferiti[:4]


_INGREDIENTI_INTRUSI_CHECK = [
    ("tonno", ["tonno", "tonnata"]),
    ("acciug", ["acciug", "alici", "puttanesca", "bagna cauda"]),
    ("fungh", ["fungh", "boscaiola", "porcin", "champignon", "chiodini", "finferli", "mare e monti"]),
    ("salsicci", ["salsicci", "boscaiola", "campidanese", "norcina", "zola"]),
    ("gamber", ["gamber", "scoglio", "mare", "pesce", "crostacei", "mari e monti"]),
    ("vongol", ["vongol", "scoglio", "mare", "pesce"]),
    ("cozz", ["cozz", "scoglio", "mare", "pesce"]),
    ("salmon", ["salmon"]),
    ("pancett", ["pancett", "carbonara", "amatriciana", "gricia", "boscaiola"]),
    ("guancial", ["guancial", "carbonara", "amatriciana", "gricia"]),
    ("speck", ["speck", "tirolese", "montagna"]),
    ("panna", ["panna", "boscaiola", "salmone", "prosciutto", "4 formaggi", "quattro formaggi", "pasticciat"]),
    ("barolo", ["barolo", "vino rosso", "ubriac"]),
    ("cinghial", ["cinghial", "cacciagione", "selvaggina"]),
    ("anatra", ["anatra"]),
    ("tartuf", ["tartuf", "norcina"]),
]


def ha_ingredienti_intrusi_non_richiesti(testo_da_controllare: str, query_utente: str) -> bool:
    """Restituisce True se il titolo/ingrediente contiene un ingrediente caratterizzante mai richiesto dall'utente."""
    t_low = (testo_da_controllare or "").lower()
    q_low = (query_utente or "").lower()
    for radice_intruso, parole_autorizzanti in _INGREDIENTI_INTRUSI_CHECK:
        if radice_intruso in t_low:
            if not any(pa in q_low for pa in parole_autorizzanti):
                return True
    return False


def sintetizza_ricetta_ai_locale(query_ricerca: str, risultati_web: list = None):
    """
    Costruisce una ricetta 100% fedele esclusivamente alle parole digitate dall'utente.
    NON copia MAI ricette dai siti web per evitare qualsiasi ingrediente intruso (es. funghi, tonno, acciughe).
    """
    q_clean = re.sub(r"\s+", " ", (query_ricerca or "Ricetta").strip())
    low_q = q_clean.lower()
    is_pronto = any(k in low_q for k in ["pront", "comprat", "barattolo", "scatola", "confezion"])
    ings_sintesi = []

    # 1. Individuazione della base (formato di pasta, riso, gnocchi o piatto base)
    formati_pasta = [
        ("tagliatelle", "Tagliatelle", 350.0),
        ("fettuccine", "Fettuccine", 350.0),
        ("pappardelle", "Pappardelle", 350.0),
        ("spaghetti", "Spaghetti", 350.0),
        ("linguine", "Linguine", 350.0),
        ("bucatini", "Bucatini", 350.0),
        ("penne", "Penne", 350.0),
        ("rigatoni", "Rigatoni", 350.0),
        ("tortiglioni", "Tortiglioni", 350.0),
        ("mezze maniche", "Mezze maniche", 350.0),
        ("fusilli", "Fusilli", 350.0),
        ("farfalle", "Farfalle", 350.0),
        ("trofie", "Trofie", 350.0),
        ("orecchiette", "Orecchiette", 350.0),
        ("cavatelli", "Cavatelli", 350.0),
        ("paccheri", "Paccheri", 350.0),
        ("conchiglie", "Conchiglie", 350.0),
        ("maccheroni", "Maccheroni", 350.0),
        ("tortellini", "Tortellini", 500.0),
        ("ravioli", "Ravioli", 500.0),
        ("gnocchi", "Gnocchi di patate", 800.0),
        ("lasagn", "Lasagne", 500.0),
        ("pasta", "Pasta", 350.0),
        ("risott", "Riso", 320.0),
        ("riso", "Riso", 320.0),
        ("polenta", "Farina di mais", 350.0),
    ]

    base_trovata = None
    for chiave_f, nome_f, qta_f in formati_pasta:
        if re.search(rf"\b{chiave_f}", low_q):
            base_trovata = (nome_f, qta_f)
            break

    if base_trovata:
        ings_sintesi.append({"nome": base_trovata[0], "qta_base": base_trovata[1], "unita": "g"})

    # 2. Gestione fedele del condimento / piatto richiesto
    if is_pronto:
        if "pesto" in low_q:
            ings_sintesi.append({"nome": "Pesto pronto", "qta_base": 190.0, "unita": "g"})
        elif "ragù" in low_q or "ragu" in low_q or "bolognese" in low_q:
            ings_sintesi.append({"nome": "Ragù pronto", "qta_base": 400.0, "unita": "g"})
        elif "sugo" in low_q or "pomodor" in low_q:
            ings_sintesi.append({"nome": "Sugo pronto", "qta_base": 400.0, "unita": "g"})
        else:
            m_cond = re.search(r"(?:con|col|al|alla|alle|ai)\s+([a-zA-Zàèéìòù\s]{3,25}?)(?:\s+già|\s+gia|\s+pront|\s+comprat|$)", low_q)
            nome_c = (m_cond.group(1).strip().capitalize() + " pronto") if m_cond else "Condimento pronto"
            ings_sintesi.append({"nome": nome_c, "qta_base": 250.0, "unita": "g"})
        if base_trovata:
            ings_sintesi.append({"nome": "Parmigiano", "qta_base": 0.0, "unita": "q.b."})
        ings_sintesi.append({"nome": "Sale", "qta_base": 0.0, "unita": "q.b."})

    else:
        ha_condimento_specifico = False

        if "pesto" in low_q:
            ha_condimento_specifico = True
            ings_sintesi.extend([
                {"nome": "Pesto alla genovese", "qta_base": 190.0, "unita": "g"},
                {"nome": "Parmigiano", "qta_base": 0.0, "unita": "q.b."},
                {"nome": "Olio extravergine d'oliva", "qta_base": 0.0, "unita": "q.b."},
                {"nome": "Sale", "qta_base": 0.0, "unita": "q.b."}
            ])

        if "ragù" in low_q or "ragu" in low_q or "bolognese" in low_q:
            ha_condimento_specifico = True
            ings_sintesi.extend([
                {"nome": "Carne macinata", "qta_base": 300.0, "unita": "g"},
                {"nome": "Passata di pomodoro", "qta_base": 400.0, "unita": "g"},
                {"nome": "Carota", "qta_base": 1.0, "unita": "pz"},
                {"nome": "Sedano", "qta_base": 1.0, "unita": "pz"},
                {"nome": "Cipolla", "qta_base": 1.0, "unita": "pz"},
                {"nome": "Parmigiano", "qta_base": 0.0, "unita": "q.b."},
                {"nome": "Olio extravergine d'oliva", "qta_base": 0.0, "unita": "q.b."},
                {"nome": "Sale", "qta_base": 0.0, "unita": "q.b."}
            ])
        elif "sugo" in low_q or "pomodor" in low_q or "arrabbiata" in low_q:
            ha_condimento_specifico = True
            ings_sintesi.extend([
                {"nome": "Passata di pomodoro", "qta_base": 400.0, "unita": "g"},
                {"nome": "Cipolla" if "arrabbiata" not in low_q else "Aglio", "qta_base": 1.0, "unita": "pz"},
                {"nome": "Basilico" if "arrabbiata" not in low_q else "Peperoncino", "qta_base": 0.0, "unita": "q.b."},
                {"nome": "Parmigiano", "qta_base": 0.0, "unita": "q.b."},
                {"nome": "Olio extravergine d'oliva", "qta_base": 0.0, "unita": "q.b."},
                {"nome": "Sale", "qta_base": 0.0, "unita": "q.b."}
            ])

        if "carbonara" in low_q:
            ha_condimento_specifico = True
            ings_sintesi.extend([
                {"nome": "Guanciale", "qta_base": 150.0, "unita": "g"},
                {"nome": "Uova", "qta_base": 4.0, "unita": "pz"},
                {"nome": "Pecorino", "qta_base": 60.0, "unita": "g"},
                {"nome": "Pepe", "qta_base": 0.0, "unita": "q.b."},
                {"nome": "Sale", "qta_base": 0.0, "unita": "q.b."}
            ])
        elif "amatriciana" in low_q:
            ha_condimento_specifico = True
            ings_sintesi.extend([
                {"nome": "Guanciale", "qta_base": 150.0, "unita": "g"},
                {"nome": "Passata di pomodoro", "qta_base": 400.0, "unita": "g"},
                {"nome": "Pecorino", "qta_base": 60.0, "unita": "g"},
                {"nome": "Peperoncino", "qta_base": 0.0, "unita": "q.b."},
                {"nome": "Sale", "qta_base": 0.0, "unita": "q.b."}
            ])
        elif "bianc" in low_q or "burro" in low_q:
            ha_condimento_specifico = True
            ings_sintesi.extend([
                {"nome": "Burro", "qta_base": 50.0, "unita": "g"},
                {"nome": "Parmigiano", "qta_base": 60.0, "unita": "g"},
                {"nome": "Sale", "qta_base": 0.0, "unita": "q.b."}
            ])
        elif "aglio" in low_q and "olio" in low_q:
            ha_condimento_specifico = True
            ings_sintesi.extend([
                {"nome": "Aglio", "qta_base": 2.0, "unita": "pz"},
                {"nome": "Peperoncino", "qta_base": 0.0, "unita": "q.b."},
                {"nome": "Olio extravergine d'oliva", "qta_base": 0.0, "unita": "q.b."},
                {"nome": "Sale", "qta_base": 0.0, "unita": "q.b."}
            ])

        # Ingredienti espliciti nominati nella query (solo se presenti nella stringa dell'utente!)
        extra_espliciti = [
            ("tonno", "Tonno", 200.0, "g"),
            ("fungh", "Funghi", 350.0, "g"),
            ("salsicci", "Salsiccia", 300.0, "g"),
            ("zucca", "Zucca", 500.0, "g"),
            ("zucchin", "Zucchine", 400.0, "g"),
            ("melanzan", "Melanzane", 400.0, "g"),
            ("peperon", "Peperoni", 400.0, "g"),
            ("spinac", "Spinaci", 400.0, "g"),
            ("broccol", "Broccoli", 400.0, "g"),
            ("cavolfior", "Cavolfiore", 400.0, "g"),
            ("patat", "Patate", 500.0, "g"),
            ("lenticchi", "Lenticchie", 300.0, "g"),
            ("ceci", "Ceci", 300.0, "g"),
            ("fagiol", "Fagioli", 300.0, "g"),
            ("pisell", "Piselli", 250.0, "g"),
            ("legumi", "Legumi misti", 350.0, "g"),
            ("polpo", "Polpo", 800.0, "g"),
            ("salmon", "Salmone", 250.0, "g"),
            ("gamber", "Gamberi", 300.0, "g"),
            ("vongol", "Vongole", 500.0, "g"),
            ("cozz", "Cozze", 500.0, "g"),
            ("pollo", "Petto di pollo", 500.0, "g"),
            ("tacchino", "Fesa di tacchino", 500.0, "g"),
            ("carne macinata", "Carne macinata", 400.0, "g"),
            ("hamburger", "Hamburger", 4.0, "pz"),
            ("frittata", "Uova", 6.0, "pz"),
        ]
        for rad_e, nome_e, qta_e, uni_e in extra_espliciti:
            if rad_e in low_q and not (rad_e == "peperon" and "peperoncin" in low_q):
                ha_condimento_specifico = True
                ings_sintesi.append({"nome": nome_e, "qta_base": qta_e, "unita": uni_e})

        if any(k in low_q for k in ["legumi", "zuppa", "minestr", "lenticchi", "ceci", "fagiol"]):
            ings_sintesi.extend([
                {"nome": "Carota", "qta_base": 1.0, "unita": "pz"},
                {"nome": "Sedano", "qta_base": 1.0, "unita": "pz"},
                {"nome": "Cipolla", "qta_base": 1.0, "unita": "pz"},
            ])

        if not ha_condimento_specifico and not base_trovata:
            ings_sintesi.append({"nome": q_clean.capitalize(), "qta_base": 400.0, "unita": "g"})

        ings_sintesi.extend([
            {"nome": "Olio extravergine d'oliva", "qta_base": 0.0, "unita": "q.b."},
            {"nome": "Sale", "qta_base": 0.0, "unita": "q.b."}
        ])

    # Deduplicazione mantenendo l'ordine
    ings_unici = []
    visti = set()
    for item in ings_sintesi:
        k = item["nome"].strip().lower()
        if k not in visti:
            visti.add(k)
            ings_unici.append(item)

    return {
        "titolo": f"✨ {q_clean.capitalize()} (Soluzione AI Gemini)",
        "sito": "🤖 Intelligenza Artificiale Gemini",
        "preparazione_breve": f"Ricetta fedele ed essenziale per preparare '{q_clean}' senza ingredienti estranei.",
        "url_fonte": f"https://www.google.com/search?q={urllib.parse.quote_plus(q_clean + ' ricetta')}",
        "foto_url": "",
        "porzioni_base": 4,
        "ingredienti_base": ings_unici,
        "is_gemini": True
    }


def chiedi_ricetta_a_gemini_ai(query_ricerca: str, nome_ricetta_originale: str = ""):
    query_effettiva = (query_ricerca or nome_ricetta_originale or "Ricetta").strip()
    log_render(f"[GEMINI AI] Avvio generazione ricetta per query: '{query_effettiva}'")

    raw_key = os.environ.get("GEMINI_API_KEY", "") or GEMINI_API_KEY
    api_key = raw_key.replace("GEMINI_API_KEY=", "").strip().strip('"').strip("'")
    if not api_key:
        log_render("[GEMINI AI] ATTENZIONE: GEMINI_API_KEY assente nelle variabili d'ambiente su Render!")
        return sintetizza_ricetta_ai_locale(query_effettiva)

    prompt = f"""Sei uno chef di cucina casalinga italiana. L'utente deve fare la spesa per preparare esattamente: "{query_effettiva}".
REGOLE TASSATIVE ED INDEROGABILI:
1. FEDELTÀ ASSOLUTA AL NOME: Inserisci SOLO ed ESCLUSIVAMENTE gli ingredienti base tradizionali strettamente necessari per "{query_effettiva}".
2. DIVIETO DI AGGIUNTE NON RICHIESTE: NON aggiungere MAI ingredienti caratterizzanti non scritti dall'utente (es. VIETATO aggiungere funghi, tonno, acciughe, salsiccia, pancetta, speck, gamberi, panna, piselli, vino Barolo se non sono scritti esplicitamente in "{query_effettiva}").
   - Esempio 1: Se l'utente chiede "Tagliatelle al sugo", usa SOLO: Tagliatelle (350 g), Passata di pomodoro (400 g), Cipolla (1 pz), Basilico (q.b.), Olio extravergine d'oliva (q.b.), Sale (q.b.), Parmigiano (q.b.). Niente funghi o salsiccia!
   - Esempio 2: Se l'utente chiede "Pasta al pesto", usa SOLO: Pasta (350 g), Pesto alla genovese (190 g), Parmigiano (q.b.), Sale (q.b.). Niente tonno o acciughe!
3. CONDIMENTI GIÀ PRONTI: Se nella richiesta "{query_effettiva}" compare la parola "pronto/a", "già pronto", "comprato" o "in barattolo" (es. "Pasta al pesto pronto"), metti SOLO la base da cuocere (es. Pasta 350 g) + il condimento già pronto (es. Pesto pronto 190 g) + Sale (q.b.).
4. Calibra le dosi base per 4 persone. Unità ammesse: "g", "kg", "ml", "l", "pz", "fette", "cucchiai", "tazze", "q.b.".
Rispondi SOLO con un oggetto JSON valido (senza markdown):
{{"titolo": "{query_effettiva.capitalize()}", "preparazione_breve": "Spiegazione breve e fedele in 2 righe", "porzioni_base": 4, "ingredienti_base": [{{"nome": "Pasta", "qta_base": 350, "unita": "g"}}]}}"""

    candidati_modelli = ottieni_lista_modelli_gemini(api_key)
    for modello_target in candidati_modelli[:4]:
        try:
            payload = json.dumps({
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.1}
            }).encode("utf-8")

            url_api = f"https://generativelanguage.googleapis.com/v1beta/models/{modello_target}:generateContent?key={urllib.parse.quote(api_key)}"
            req = urllib.request.Request(url_api, data=payload, headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=6.0) as resp:
                risp_json = json.loads(resp.read().decode("utf-8", errors="ignore"))

            parts_list = risp_json.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            testo_out = ""
            for p_item in reversed(parts_list):
                if p_item.get("text") and "{" in p_item.get("text", ""):
                    testo_out = p_item["text"]
                    break
            if not testo_out and parts_list:
                testo_out = parts_list[-1].get("text", "")

            m_json = re.search(r"\{.*\}", testo_out, flags=re.DOTALL)
            dati_ai = json.loads(m_json.group(0) if m_json else testo_out)
            ings_validi = []
            for item in dati_ai.get("ingredienti_base", []):
                u = str(item.get("unita") or "g").strip()
                if u not in UNITA_MISURA:
                    u = "g"
                n_ing = str(item.get("nome") or "").strip().capitalize()
                if n_ing:
                    # Scarta automaticamente eventuali ingredienti intrusi allucinati non richiesti nella query
                    if ha_ingredienti_intrusi_non_richiesti(n_ing, query_effettiva):
                        log_render(f"[GEMINI AI] Scartato ingrediente intruso non richiesto '{n_ing}' per '{query_effettiva}'")
                        continue
                    ings_validi.append({
                        "nome": n_ing,
                        "qta_base": float(item.get("qta_base") or 0),
                        "unita": u
                    })
            if ings_validi:
                MODELLO_GEMINI_RILEVATO[0] = modello_target
                log_render(f"[GEMINI AI] Risposta OK da modello '{modello_target}' per '{query_effettiva}': {[x['nome'] for x in ings_validi]}")
                return {
                    "titolo": f"✨ {dati_ai.get('titolo') or query_effettiva.capitalize()} (Soluzione AI Gemini)",
                    "sito": "🤖 Intelligenza Artificiale Gemini",
                    "preparazione_breve": dati_ai.get("preparazione_breve", ""),
                    "url_fonte": f"https://www.google.com/search?q={urllib.parse.quote_plus(query_effettiva + ' ricetta')}",
                    "foto_url": "",
                    "porzioni_base": int(dati_ai.get("porzioni_base") or 4),
                    "ingredienti_base": ings_validi,
                    "is_gemini": True
                }
        except Exception as ex:
            err_body = ""
            if hasattr(ex, "read"):
                try:
                    err_body = ex.read().decode("utf-8", errors="ignore")[:220].replace("\n", " ")
                except Exception:
                    pass
            log_render(f"[GEMINI AI] Fallito modello '{modello_target}': {ex} | Dettaglio: {err_body}")
            if "404" in str(ex) or "400" in str(ex):
                _MODELLI_NON_DISPONIBILI.add(modello_target)
            if MODELLO_GEMINI_RILEVATO[0] == modello_target:
                MODELLO_GEMINI_RILEVATO[0] = None
            continue

    log_render(f"[GEMINI AI] Uso sintesi fedele locale per '{query_effettiva}'.")
    return sintetizza_ricetta_ai_locale(query_effettiva)


def estrai_info_complete_da_pagina_ricetta(html_page, url_pagina):
    foto_og = ""
    m_og_img = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html_page, flags=re.IGNORECASE)
    if m_og_img:
        foto_og = m_og_img.group(1).strip()

    titolo_og = ""
    m_og_tit = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html_page, flags=re.IGNORECASE)
    if m_og_tit:
        titolo_og = html.unescape(m_og_tit.group(1)).strip()
    if not titolo_og:
        m_h1 = re.search(r'<h1[^>]*>(.*?)</h1>', html_page, flags=re.DOTALL | re.IGNORECASE)
        if m_h1:
            titolo_og = re.sub(r"<[^>]+>", " ", html.unescape(m_h1.group(1))).strip()

    titolo_pulito_og = re.sub(r"\s*(?:[-|–—]\s*(?:Ricetta|GialloZafferano|Blog|Misya|Cucchiaio|Fatto in casa).*)$", "", titolo_og, flags=re.IGNORECASE).strip()

    # 1. Ricerca ricorsiva nei blocchi JSON-LD (supporta anche @graph annidati e mainEntity dei Blog GialloZafferano)
    def cerca_recipe_in_json(obj):
        if isinstance(obj, dict):
            tipo = obj.get("@type")
            is_ric = (tipo == "Recipe") or (isinstance(tipo, list) and "Recipe" in tipo)
            if is_ric and obj.get("recipeIngredient"):
                return obj
            for v in obj.values():
                trovato = cerca_recipe_in_json(v)
                if trovato:
                    return trovato
        elif isinstance(obj, list):
            for el in obj:
                trovato = cerca_recipe_in_json(el)
                if trovato:
                    return trovato
        return None

    blocchi = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html_page, flags=re.DOTALL | re.IGNORECASE)
    for b in blocchi:
        try:
            dati = json.loads(b.strip())
            item = cerca_recipe_in_json(dati)
            if item:
                porzioni_base = 4
                yield_val = item.get("recipeYield")
                if yield_val:
                    m_y = re.search(r"(\d+)", str(yield_val[0] if isinstance(yield_val, list) else yield_val))
                    if m_y and 1 <= int(m_y.group(1)) <= 30:
                        porzioni_base = int(m_y.group(1))
                titolo_ric = html.unescape(str(item.get("name") or titolo_pulito_og or "Ricetta")).strip()
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

    # 2. Fallback HTML specifico per GialloZafferano (<dd class="gz-ingredient">) e Blog GialloZafferano (WPRM / Tasty / MV / liste HTML)
    righe_dd_gz = re.findall(r'<dd[^>]*class=["\'][^"\']*gz-ingredient[^"\']*["\'][^>]*>(.*?)</dd>', html_page, flags=re.DOTALL | re.IGNORECASE)
    righe_li = righe_dd_gz or re.findall(
        r'<li[^>]*class=["\'][^"\']*(?:wprm-recipe-ingredient|ingredient|gz-ingredient|tasty-recipes-ingredient|mv-create-ingredients)[^"\']*["\'][^>]*>(.*?)</li>',
        html_page, flags=re.DOTALL | re.IGNORECASE
    )
    if not righe_li:
        m_sez_ing = re.search(r'(?i)<h[234][^>]*>[^<]*ingredienti[^<]*</h[234]>\s*(?:<div[^>]*>\s*)*<[uo]l[^>]*>(.*?)</[uo]l>', html_page, flags=re.DOTALL)
        if m_sez_ing:
            righe_li = re.findall(r'<li[^>]*>(.*?)</li>', m_sez_ing.group(1), flags=re.DOTALL)
    if righe_li and len(righe_li) >= 2:
        raw_ings = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(x))).strip() for x in righe_li]
        return {
            "titolo": titolo_pulito_og or "Ricetta Web",
            "porzioni_base": 4,
            "raw_ings": raw_ings[:15],
            "foto_url": foto_og,
            "url_fonte": url_pagina
        }
    return None


def cerca_ricette_su_internet_dettagliate(query_ricerca: str, nome_ricetta_originale: str = ""):
    log_render(f"[RICERCA WEB+AI] Avvio ricerca multi-sito per query: '{query_ricerca}' (originale: '{nome_ricetta_originale}')")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "it-IT,it;q=0.9"
    }
    low_q = query_ricerca.lower()
    query_pulita = re.sub(r"\([^)]*\)", " ", query_ricerca)
    query_pulita = re.sub(r"(?i)\b(il|la|lo|è|gia|già|pronto|pronta|comprato|comprata)\b", " ", query_pulita)
    query_pulita = re.sub(r"\s+", " ", query_pulita).strip() or query_ricerca.strip()

    # 1. Avvia Gemini AI in parallelo passando la query esatta digitata dall'utente
    box_ai_parallelo = [None]
    def worker_ai():
        box_ai_parallelo[0] = chiedi_ricetta_a_gemini_ai(query_ricerca, query_ricerca)
    t_ai = threading.Thread(target=worker_ai, daemon=True)
    t_ai.start()

    # 2. Ricerca parallela multi-portale (con endpoint dedicato sia per GialloZafferano che per Blog GialloZafferano)
    q_enc_plus = urllib.parse.quote_plus(query_pulita)
    q_enc_path = urllib.parse.quote(query_pulita)
    portali_ricerca = [
        ("GialloZafferano", f"https://www.giallozafferano.it/ricerca-ricette/{q_enc_path}/"),
        ("Blog GialloZafferano", f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(query_pulita + ' ricetta (site:blog.giallozafferano.it OR site:ricette.giallozafferano.it)')}"),
        ("Cucchiaio d'Argento", f"https://www.cucchiaio.it/ricerca.html?q={q_enc_plus}"),
        ("Misya", f"https://www.misya.info/?s={q_enc_plus}"),
        ("Fatto in casa da Benedetta", f"https://www.fattoincasadabenedetta.it/?s={q_enc_plus}"),
        ("Sonia Peronaci", f"https://www.soniaperonaci.it/?s={q_enc_plus}"),
        ("Motore Multi-Sito", f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(query_pulita + ' ricetta (site:cucchiaio.it OR site:misya.info OR site:fattoincasadabenedetta.it OR site:soniaperonaci.it)')}")
    ]

    pattern_links = (
        r'https://(?:ricette|blog)\.giallozafferano\.it/[a-zA-Z0-9\-_/]+\.html|'
        r'https://blog\.giallozafferano\.it/[a-zA-Z0-9\-_]+/[a-zA-Z0-9\-_/]+/?|'
        r'https://www\.cucchiaio\.it/ricetta/[a-zA-Z0-9\-_/]+/?|'
        r'https://www\.misya\.info/ricetta/[a-zA-Z0-9\-_]+\.htm|'
        r'https://www\.fattoincasadabenedetta\.it/ricetta/[a-zA-Z0-9\-_]+/?|'
        r'https://www\.soniaperonaci\.it/[a-zA-Z0-9\-_]+/?'
    )

    parole_stop = {"con", "col", "alle", "alla", "agli", "del", "della", "dei", "degli", "delle", "ricetta", "al", "ai", "in", "da", "per"}
    parole_chiave = [
        w.lower() for w in re.findall(r"[a-zA-Zàèéìòù]{3,}", query_pulita)
        if w.lower() not in parole_stop
    ]
    slug_ideale = "-".join(re.findall(r"[a-zA-Zàèéìòù]+", query_pulita.lower()))

    def calcola_score_link(lk):
        parti_u = [p for p in lk.rstrip("/").split("/") if p]
        slug = parti_u[-1].replace(".html", "").replace(".htm", "").lower() if parti_u else ""
        slug_norm = re.sub(r"^ricetta-", "", slug)

        score = 0.0
        # Bonus altissimo se lo slug corrisponde esattamente alla ricetta cercata (es. 'tagliatelle-al-sugo')
        if slug_norm == slug_ideale or slug_ideale in slug_norm:
            score += 45.0

        for pk in parole_chiave:
            if pk in slug_norm or pk[:4] in slug_norm:
                score += 15.0
            else:
                score -= 10.0

        # Forte penalizzazione se l'URL contiene ingredienti intrusi non richiesti (es. barolo, funghi, salsiccia, tonno)
        if ha_ingredienti_intrusi_non_richiesti(slug_norm.replace("-", " "), query_ricerca):
            score -= 80.0

        # Penalizza parole extra nello slug non presenti nella richiesta dell'utente
        parole_slug = [w for w in slug_norm.split("-") if len(w) >= 4 and w not in parole_stop]
        for ps in parole_slug:
            if not any(ps[:4] == pk[:4] or ps in pk or pk in ps for pk in parole_chiave):
                score -= 12.0

        return score

    mappa_links_portali = {}
    lock_lk = threading.Lock()

    def cerca_su_portale(idx_p, nome_p, u_s):
        try:
            req = urllib.request.Request(u_s, headers=headers)
            with urllib.request.urlopen(req, timeout=4.5) as resp:
                raw_html = resp.read().decode("utf-8", errors="ignore")
                html_s = urllib.parse.unquote(html.unescape(raw_html))
            esclusioni = ("ricerca", "categoria", "page", "/tag/", "/author/", "/contatti", "/privacy", "/chi-sono", "/video", "wp-content", "wp-json", "/feed", "comment")
            trovati = [
                lk.rstrip("\\").rstrip('"').rstrip("'") for lk in re.findall(pattern_links, html_s)
                if not any(esc in lk.lower() for esc in esclusioni)
            ]
            trovati_unici = list(dict.fromkeys(trovati))
            trovati_unici.sort(key=calcola_score_link, reverse=True)
            with lock_lk:
                mappa_links_portali[idx_p] = trovati_unici[:3]
            log_render(f"[RICERCA WEB] Portale '{nome_p}': trovati {len(trovati_unici)} link candidati (Top: {trovati_unici[0] if trovati_unici else 'nessuno'})")
        except Exception as ex:
            log_render(f"[RICERCA WEB] Portale '{nome_p}' non raggiungibile o timeout: {ex}")

    threads_s = [threading.Thread(target=cerca_su_portale, args=(i, np, u), daemon=True) for i, (np, u) in enumerate(portali_ricerca)]
    for t in threads_s:
        t.start()
    for t in threads_s:
        t.join(timeout=5.0)

    # Costruzione Round-Robin tra i vari siti dando precedenza ai link privi di intrusi
    links_candidati = []
    for round_idx in range(3):
        for idx_p in sorted(mappa_links_portali.keys()):
            lista_p = mappa_links_portali[idx_p]
            if round_idx < len(lista_p):
                lk = lista_p[round_idx]
                if lk not in links_candidati:
                    links_candidati.append(lk)

    risultati_mappa = {}
    def estrai_da_link(pos_idx, link):
        try:
            req_r = urllib.request.Request(link, headers=headers)
            with urllib.request.urlopen(req_r, timeout=4.5) as resp_r:
                html_r = resp_r.read().decode("utf-8", errors="ignore")
            info = estrai_info_complete_da_pagina_ricetta(html_r, link)
            if info and info.get("raw_ings"):
                ings_validi, nomi_visti = [], set()
                for r_str in info["raw_ings"]:
                    parsed = analizza_stringa_ingrediente_web_base(r_str)
                    if parsed and parsed["nome"].lower() not in nomi_visti:
                        nomi_visti.add(parsed["nome"].lower())
                        ings_validi.append(parsed)
                if ings_validi:
                    sito_nome = (
                        "Blog GialloZafferano" if "blog.giallozafferano" in link else
                        ("GialloZafferano" if "giallozafferano" in link else
                         ("Misya" if "misya" in link else
                          ("Fatto in casa da Benedetta" if "benedetta" in link else
                           ("Sonia Peronaci" if "soniaperonaci" in link else "Cucchiaio d'Argento"))))
                    )
                    tit_r = info["titolo"]
                    testo_check_intrusi = tit_r + " " + " ".join(x["nome"] for x in ings_validi)
                    ha_intrusi = ha_ingredienti_intrusi_non_richiesti(testo_check_intrusi, query_ricerca)
                    match_parole = sum(1 for pk in parole_chiave if pk[:4] in tit_r.lower())

                    with lock_lk:
                        risultati_mappa[pos_idx] = {
                            "titolo": tit_r,
                            "sito": sito_nome,
                            "url_fonte": link,
                            "foto_url": "",
                            "porzioni_base": info["porzioni_base"] or 4,
                            "ingredienti_base": ings_validi,
                            "is_gemini": False,
                            "_ha_intrusi": ha_intrusi,
                            "_match_parole": match_parole,
                            "_ordine_rr": pos_idx
                        }
                    log_render(f"[RICERCA WEB] Estratta '{tit_r}' da {sito_nome} ({len(ings_validi)} ing., intrusi={ha_intrusi})")
        except Exception:
            pass

    threads_r = [threading.Thread(target=estrai_da_link, args=(idx_l, lk), daemon=True) for idx_l, lk in enumerate(links_candidati[:9])]
    for t in threads_r:
        t.start()
    for t in threads_r:
        t.join(timeout=5.0)

    tutti_web = list(risultati_mappa.values())
    # Se ci sono ricette web fedeli (senza intrusi come funghi/barolo/tonno), scarta del tutto quelle con intrusi!
    web_puliti = [r for r in tutti_web if not r.get("_ha_intrusi")]
    lista_web_finale = web_puliti if web_puliti else tutti_web

    # Ordina mettendo in cima le ricette che contengono tutte le parole chiave cercate, mantenendo il round-robin tra siti
    lista_web_finale.sort(key=lambda x: (-x.get("_match_parole", 0), x.get("_ordine_rr", 0)))
    risultati_trovati = lista_web_finale

    # Attende Gemini AI e se l'API Google non risponde usa la sintesi intelligente fedele
    t_ai.join(timeout=8.0)
    sol_ai = box_ai_parallelo[0]
    if not sol_ai or not sol_ai.get("ingredienti_base"):
        sol_ai = sintetizza_ricetta_ai_locale(query_ricerca, risultati_trovati)

    sol_ai["foto_url"] = ""
    sol_ai["is_gemini"] = True
    risultati_trovati.append(sol_ai)

    siti_riepilogo = [f"{r.get('sito', '')}: {r.get('titolo', '')}" for r in risultati_trovati]
    log_render(f"[RICERCA WEB+AI] Completata per '{query_ricerca}': {len(risultati_trovati)} proposte -> {siti_riepilogo}")
    return risultati_trovati


def ricalibra_ingredienti_ricetta_db(id_ricetta: str, vecchie_persone: int, nuove_persone: int, meta_attuale: dict, stato_popup=None, disegna_callback=None):
    if not is_valid_uuid(id_ricetta):
        return
    vecchie_p, nuove_p = max(1, int(vecchie_persone or 8)), max(1, int(nuove_persone or 8))
    fattore = float(nuove_p) / float(vecchie_p)

    res_ri = supabase.table("ricette_ingredienti").select("*, ingredienti(id, nome, unita_default)").eq("id_ricetta", id_ricetta).execute()

    righe_rimaste = []
    for r in (res_ri.data or []):
        q = float(r.get("quantita") or 0)
        if q > 0 and r.get("unita_misura") != "q.b.":
            nq = round(q * fattore, 2)
            supabase.table("ricette_ingredienti").update({"quantita": nq}).eq("id", r["id"]).execute()
            r["quantita"] = nq
        righe_rimaste.append(r)

    salva_meta_ricetta_db(id_ricetta, meta_attuale.get("portata", "Primo"), nuove_p, meta_attuale.get("url_fonte", ""), meta_attuale.get("foto_url", ""))

    if stato_popup is not None and disegna_callback is not None:
        stato_popup["righe_ri"] = righe_rimaste
        CACHE_ANAGRAFICA["conteggio_ing_ricette"][str(id_ricetta)] = len(righe_rimaste)
        disegna_callback(msg_extra=f"Dosi ricalcolate per {nuove_p} persone!")


# ==========================================================
# POPUP DI GESTIONE E DETTAGLIO RICETTA
# ==========================================================
def apri_popup_rinomina_ingrediente(page: ft.Page, ing_obj: dict, lista_ingredienti: list, on_done_callback, riga_ri_ref: dict = None):
    txt_nome = ft.TextField(label="Nome ingrediente", value=ing_obj.get("nome", ""), dense=True, text_size=13, autofocus=True)
    sel_unita = crea_selettore_unita(page, valore_iniziale=ing_obj.get("unita_default", "g"), width=90, height=36, text_size=12)
    dlg_mod = None

    def salva_mod(e=None):
        vecchio_nome = (ing_obj.get("nome") or "").strip()
        n_nuovo = (txt_nome.value or "").strip().capitalize()
        if not n_nuovo:
            return
        u_scelta = sel_unita.get_value()
        is_disp_attivo = bool(ing_obj.get("is_dispensa_base")) or is_ingrediente_dispensa_attivo(n_nuovo)
        tag_det = "|DET" if ing_obj.get("is_detersivo") else ""
        tag_disp = "|DISPENSA" if is_disp_attivo else ""
        nuova_u_db = f"{u_scelta}{tag_det}{tag_disp}"

        log_render(f"[UTENTE] Rinomina ingrediente: '{vecchio_nome}' -> '{n_nuovo}' (Unità: {u_scelta}, Dispensa: {is_disp_attivo})")

        # 1. AGGIORNAMENTO ISTANTANEO IN RAM (0ms: visibile subito anche alla prima selezione!)
        ing_obj["nome"] = n_nuovo
        ing_obj["unita_default"] = u_scelta
        ing_obj["is_dispensa_base"] = is_disp_attivo
        if riga_ri_ref is not None:
            if not isinstance(riga_ri_ref.get("ingredienti"), dict):
                riga_ri_ref["ingredienti"] = {}
            riga_ri_ref["ingredienti"]["nome"] = n_nuovo
            riga_ri_ref["ingredienti"]["unita_default"] = nuova_u_db
            riga_ri_ref["unita_misura"] = u_scelta

        for ig in (CACHE_ANAGRAFICA.get("ingredienti") or []):
            if str(ig.get("id")) == str(ing_obj.get("id")) or (vecchio_nome and ig.get("nome", "").strip().lower() == vecchio_nome.lower()):
                ig["nome"] = n_nuovo
                ig["unita_default"] = u_scelta
                ig["is_dispensa_base"] = is_disp_attivo

        chiudi_dialog_sicuro(page, dlg_mod)
        if on_done_callback:
            on_done_callback()

        # 2. SALVATAGGIO SU DATABASE IN BACKGROUND (gestisce anche ricetta appena applicata alla prima selezione)
        def w():
            try:
                # Se la ricetta è appena stata applicata in background, attende fino a 2.5s che l'ID temporaneo diventi UUID reale
                if riga_ri_ref is not None and not is_valid_uuid(riga_ri_ref.get("id")):
                    for _ in range(10):
                        time.sleep(0.25)
                        if is_valid_uuid(riga_ri_ref.get("id")):
                            break

                id_orig = ing_obj.get("id") or (riga_ri_ref.get("id_ingrediente") if riga_ri_ref else "")
                if not is_valid_uuid(id_orig) and vecchio_nome:
                    ing_old_c = cerca_ingrediente_in_cache_per_nome(vecchio_nome)
                    if ing_old_c and is_valid_uuid(ing_old_c.get("id")):
                        id_orig = ing_old_c["id"]

                esistente_altro = next(
                    (i for i in (CACHE_ANAGRAFICA.get("ingredienti") or [])
                     if i.get("nome", "").strip().lower() == n_nuovo.lower() and str(i.get("id")) != str(id_orig)),
                    None
                )
                if esistente_altro and is_valid_uuid(esistente_altro.get("id")):
                    id_target = esistente_altro["id"]
                    if riga_ri_ref:
                        if is_valid_uuid(riga_ri_ref.get("id")):
                            supabase.table("ricette_ingredienti").update({"id_ingrediente": id_target, "unita_misura": u_scelta}).eq("id", riga_ri_ref["id"]).execute()
                        elif is_valid_uuid(riga_ri_ref.get("id_ricetta")) and is_valid_uuid(id_orig):
                            supabase.table("ricette_ingredienti").update({"id_ingrediente": id_target, "unita_misura": u_scelta}).eq("id_ricetta", riga_ri_ref["id_ricetta"]).eq("id_ingrediente", id_orig).execute()
                        riga_ri_ref["id_ingrediente"] = id_target
                        riga_ri_ref["ingredienti"]["id"] = id_target
                elif is_valid_uuid(id_orig):
                    supabase.table("ingredienti").update({"nome": n_nuovo, "unita_default": nuova_u_db}).eq("id", id_orig).execute()
                    if riga_ri_ref and is_valid_uuid(riga_ri_ref.get("id")):
                        supabase.table("ricette_ingredienti").update({"unita_misura": u_scelta}).eq("id", riga_ri_ref["id"]).execute()
                else:
                    id_creato = ottieni_o_crea_ing_sicuro(n_nuovo, u_scelta, bool(ing_obj.get("is_detersivo")), ignora_cache=True)
                    if id_creato and riga_ri_ref and is_valid_uuid(riga_ri_ref.get("id")):
                        supabase.table("ricette_ingredienti").update({"id_ingrediente": id_creato, "unita_misura": u_scelta}).eq("id", riga_ri_ref["id"]).execute()
                        riga_ri_ref["id_ingrediente"] = id_creato
                        riga_ri_ref["ingredienti"]["id"] = id_creato

                assicura_cache_anagrafica(forza_ricarica=True)
                sincronizza_e_pulisci_dispensa_mese(ULTIMA_DATA_MENU_ATTIVA[0])
                if on_done_callback:
                    on_done_callback()
            except Exception as ex:
                log_render(f"[ERRORE] Rinomina ingrediente '{n_nuovo}': {ex}")

        threading.Thread(target=w, daemon=True).start()

    txt_nome.on_submit = salva_mod

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
    ric_cache = next((r for r in (CACHE_ANAGRAFICA["ricette"] or []) if str(r.get("id")) == str(id_ricetta)), None)
    meta = estrai_meta_ricetta(ric_cache or {"portata_default": portata_attuale})
    txt_nome_ric = ft.TextField(label="Nome Ricetta", value=nome_attuale, dense=True, text_size=13)
    dlg_ric = None

    def salva_ricetta(e):
        n_u = (txt_nome_ric.value or "").strip().capitalize()
        if not n_u:
            return
        log_render(f"[UTENTE] Rinomina ricetta ID={id_ricetta}: '{nome_attuale}' -> '{n_u}'")
        chiudi_dialog_sicuro(page, dlg_ric)
        def w():
            salva_meta_ricetta_db(id_ricetta, meta["portata"], meta["porzioni"], meta["url_fonte"], meta["foto_url"], nuovo_nome=n_u)
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

    log_render(f"[UTENTE] Apertura dettaglio ricetta '{nome_ricetta_init}' (ID ricetta: {id_ricetta_init}, ID voce menu: {id_voce_menu})")
    assicura_cache_anagrafica(forza_ricarica=False)
    cache_ing_ric = CACHE_ANAGRAFICA.setdefault("dettaglio_ing_ricette", {}).get(str(id_ricetta_init), [])
    ric_init_cache = next((r for r in (CACHE_ANAGRAFICA.get("ricette") or []) if str(r.get("id")) == str(id_ricetta_init)), None)
    meta_init = estrai_meta_ricetta(ric_init_cache)
    gia_compilata_init = bool(cache_ing_ric) or ricetta_ha_ingredienti(id_ricetta_init) or bool(meta_init["url_fonte"])

    spinner_det = ft.ProgressRing(width=18, height=18, stroke_width=2.5, color=ft.Colors.WHITE)
    lbl_det_status = ft.Text("Lettura ingredienti...", size=11, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD)
    banner_det = ft.Container(
        content=ft.Row([spinner_det, lbl_det_status], spacing=8),
        bgcolor=ft.Colors.ORANGE_800, padding=6, border_radius=6,
        visible=not bool(cache_ing_ric)
    )

    stato_popup = {
        "id_ricetta": str(id_ricetta_init or ""),
        "id_voce_menu": str(id_voce_menu or ""),
        "nome_ricetta": nome_ricetta_init,
        "query_web": nome_ricetta_init,
        "ultima_query_cercata": "",
        "portata": meta_init["portata"] or portata or "Primo",
        "porzioni": meta_init["porzioni"] or 8,
        "url_fonte": meta_init["url_fonte"] or "",
        "foto_url": meta_init["foto_url"] or "",
        "righe_ri": list(cache_ing_ric),
        "persone_web": 8,
        "risultati_web": [],
        "idx_web": 0,
        "ricerca_fatta": gia_compilata_init,
        "ricerca_in_corso": False,
        "modificato": False
    }

    txt_titolo_header = ft.Text(f"🍲 {nome_ricetta_init}", size=15, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900, expand=True)
    col_corpo = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)
    dlg_det = None

    def chiudi_det(e=None):
        log_render(f"[UTENTE] Chiusura dettaglio ricetta '{stato_popup['nome_ricetta']}' (Modificato: {stato_popup['modificato']})")
        chiudi_dialog_sicuro(page, dlg_det)
        if stato_popup["modificato"]:
            def w_close():
                sincronizza_e_pulisci_dispensa_mese(ULTIMA_DATA_MENU_ATTIVA[0])
                if on_close_refresh:
                    on_close_refresh()
            threading.Thread(target=w_close, daemon=True).start()

    def togli_dal_menu(e):
        id_vm = stato_popup["id_voce_menu"]
        log_render(f"[UTENTE] Eliminazione ricetta '{stato_popup['nome_ricetta']}' dal menu (ID voce: {id_vm})")
        chiudi_dialog_sicuro(page, dlg_det)
        if is_valid_uuid(id_vm):
            def w_del():
                supabase.table("menu_giornalieri").delete().eq("id", id_vm).execute()
                sincronizza_e_pulisci_dispensa_mese(ULTIMA_DATA_MENU_ATTIVA[0])
                if on_close_refresh:
                    on_close_refresh()
            threading.Thread(target=w_del, daemon=True).start()

    dlg_det = ft.AlertDialog(
        title=ft.Column([
            ft.Row([
                txt_titolo_header,
                ft.IconButton(icon=ft.Icons.EDIT, icon_color=ft.Colors.ORANGE_800, icon_size=18, on_click=lambda e: apri_popup_gestione_ricetta(page, stato_popup["id_ricetta"], stato_popup["nome_ricetta"], stato_popup["portata"], lambda n: (stato_popup.update({"nome_ricetta": n, "query_web": n, "modificato": True}), disegna_popup_da_ram()))),
                ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_color=ft.Colors.RED_700, icon_size=18, on_click=togli_dal_menu)
            ], spacing=2),
            banner_det
        ], spacing=4, tight=True),
        content=ft.Container(content=col_corpo, width=368, height=525),
        actions=[
            ft.Container(content=ft.Text("Chiudi", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=12), bgcolor=ft.Colors.BLUE_900, padding=ft.Padding(16, 8, 16, 8), border_radius=6, on_click=chiudi_det)
        ],
        shape=ft.RoundedRectangleBorder(radius=10), content_padding=10
    )
    apri_dialog_sicuro(page, dlg_det)

    def assicura_id_reale():
        if is_valid_uuid(stato_popup["id_ricetta"]):
            return stato_popup["id_ricetta"]
        r_c = ottieni_o_crea_ricetta_sicura(stato_popup["nome_ricetta"], stato_popup["portata"], ignora_cache=True)
        stato_popup["id_ricetta"] = str(r_c["id"])
        return stato_popup["id_ricetta"]

    def trova_indice_gemini(lista_ris):
        if not lista_ris:
            return 0
        for i, r in enumerate(lista_ris):
            if r.get("is_gemini") or "gemini" in str(r.get("sito", "")).lower() or "gemini" in str(r.get("titolo", "")).lower():
                return i
        return len(lista_ris) - 1

    def disegna_popup_da_ram(msg_extra=None):
        id_ric = stato_popup["id_ricetta"]
        porzioni_attuali = stato_popup["porzioni"]
        url_fonte_attuale = stato_popup["url_fonte"]
        foto_url_attuale = stato_popup["foto_url"]

        righe_uniche = []
        visti_ing = set()
        for r_item in stato_popup["righe_ri"]:
            ing_d = r_item.get("ingredienti") or {}
            nome_k = str(ing_d.get("nome") or "").strip().lower()
            id_k = str(r_item.get("id_ingrediente") or ing_d.get("id") or "")
            chiave_u = nome_k if nome_k else id_k
            if chiave_u and chiave_u not in visti_ing:
                visti_ing.add(chiave_u)
                righe_uniche.append(r_item)
        stato_popup["righe_ri"] = righe_uniche
        righe_ri = righe_uniche
        ha_ing = len(righe_ri) > 0

        txt_titolo_header.value = f"🍲 {stato_popup['nome_ricetta']}"
        col_corpo.controls.clear()

        if msg_extra:
            col_corpo.controls.append(
                ft.Container(content=ft.Text(f"✅ {msg_extra}", size=11, color=ft.Colors.GREEN_800, weight=ft.FontWeight.BOLD), bgcolor="#E8F5E9", padding=6, border_radius=6)
            )

        # 1. AGGIUNTA MANUALE RAPIDA IN CIMA
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
            nv = (txt_ing_man.value or "").strip().capitalize()
            if not nv:
                return
            qv, uv = parse_qta(box_q_man.textfield.value), sel_u_man.get_value()
            log_render(f"[UTENTE] Aggiunta ingrediente manuale '{nv}' ({qv} {uv}) alla ricetta '{stato_popup['nome_ricetta']}'")
            ing_esistente = cerca_ingrediente_in_cache_per_nome(nv)
            is_disp = is_ingrediente_dispensa_attivo(ing_esistente or nv)
            u_def_init = f"{uv}|DISPENSA" if is_disp else uv

            id_temp_riga = f"temp_ri_{int(time.time()*1000)}"
            nuova_riga_ram = {
                "id": id_temp_riga,
                "id_ricetta": id_ric,
                "id_ingrediente": ing_esistente["id"] if ing_esistente else "",
                "quantita": qv,
                "unita_misura": uv,
                "ingredienti": {"id": ing_esistente["id"] if ing_esistente else "", "nome": nv, "unita_default": u_def_init}
            }
            stato_popup["righe_ri"].append(nuova_riga_ram)
            stato_popup["modificato"] = True
            CACHE_ANAGRAFICA["conteggio_ing_ricette"][str(id_ric)] = len(stato_popup["righe_ri"])
            disegna_popup_da_ram(msg_extra=f"Aggiunto: {nv} ({formatta_numero(qv)} {uv})")

            def w_db():
                try:
                    vero_id_ric = assicura_id_reale()
                    id_i = ottieni_o_crea_ing_sicuro(nv, uv, False)
                    if id_i:
                        res_ins = supabase.table("ricette_ingredienti").insert({
                            "id_ricetta": vero_id_ric, "id_ingrediente": id_i, "quantita": qv, "unita_misura": uv
                        }).execute()
                        if res_ins.data:
                            nuova_riga_ram["id"] = res_ins.data[0]["id"]
                            nuova_riga_ram["id_ricetta"] = vero_id_ric
                            nuova_riga_ram["id_ingrediente"] = id_i
                            nuova_riga_ram["ingredienti"]["id"] = id_i
                        sincronizza_e_pulisci_dispensa_mese(ULTIMA_DATA_MENU_ATTIVA[0])
                        if on_close_refresh:
                            on_close_refresh()
                except Exception as ex:
                    log_render(f"[ERRORE] Salvataggio manuale ingrediente '{nv}': {ex}")

            threading.Thread(target=w_db, daemon=True).start()

        txt_ing_man.on_submit = salva_man

        col_corpo.controls.append(
            ft.Container(
                content=ft.Column([
                    txt_ing_man, riga_sugg_man,
                    ft.Row([
                        box_q_man, sel_u_man,
                        ft.Container(content=ft.Text("+ Ingrediente", color=ft.Colors.WHITE, size=11, weight=ft.FontWeight.BOLD), bgcolor=ft.Colors.GREEN_700, height=34, padding=ft.Padding(10, 0, 10, 0), alignment=ft.Alignment(0, 0), border_radius=5, on_click=salva_man)
                    ], spacing=5)
                ], spacing=4),
                bgcolor="#E8F5E9", padding=6, border_radius=8, border=bordo_campo_uniforme("#81C784")
            )
        )

        # 2. SE LA RICETTA HA INGREDIENTI: FOTO + LINK PREPARAZIONE + RICALCOLO + LISTA UNICA
        if ha_ing:
            url_prep_attivo = url_fonte_attuale or f"https://www.google.com/search?q={urllib.parse.quote_plus(stato_popup['nome_ricetta'] + ' ricetta preparazione')}"
            riga_top_meta = []
            if foto_url_attuale:
                riga_top_meta.append(ft.Image(src=foto_url_attuale, width=54, height=54, border_radius=8))

            riga_top_meta.append(
                ft.Column([
                    ft.Text(f"👥 Dosi attuali calibrate per {porzioni_attuali} persone", size=12, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900),
                    ft.TextButton(
                        "🔗 Apri preparazione sul sito della ricetta",
                        url=url_prep_attivo,
                        style=ft.ButtonStyle(padding=0)
                    )
                ], spacing=2, expand=True)
            )

            box_p_pers = crea_box_quantita(str(porzioni_attuali), width=84, height=32, text_size=12, mostra_prefisso=True, prefisso_custom="Pers.:")

            def ricalcola_pers(e):
                np = int(max(1, parse_qta(box_p_pers.textfield.value) or porzioni_attuali))
                log_render(f"[UTENTE] Ricalcolo porzioni ricetta '{stato_popup['nome_ricetta']}': {porzioni_attuali} -> {np} persone")
                fattore = float(np) / float(max(1, porzioni_attuali))
                for r in stato_popup["righe_ri"]:
                    q_old = float(r.get("quantita") or 0)
                    if q_old > 0 and r.get("unita_misura") != "q.b.":
                        r["quantita"] = round(q_old * fattore, 2)
                vecchie_p = porzioni_attuali
                stato_popup["porzioni"] = np
                stato_popup["persone_web"] = np
                stato_popup["modificato"] = True
                disegna_popup_da_ram(msg_extra=f"Quantità ricalcolate per {np} persone!")

                threading.Thread(
                    target=lambda: ricalibra_ingredienti_ricetta_db(
                        id_ric, vecchie_p, np,
                        {"portata": stato_popup["portata"], "url_fonte": stato_popup["url_fonte"], "foto_url": stato_popup["foto_url"]}
                    ),
                    daemon=True
                ).start()

            col_corpo.controls.append(
                ft.Container(
                    content=ft.Column([
                        ft.Row(riga_top_meta, spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        ft.Row([
                            ft.Text("Ricalibra dosi per:", size=11, color=ft.Colors.GREY_800, expand=True),
                            box_p_pers,
                            ft.Container(content=ft.Text("⚖️ Ricalcola", size=10, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD), bgcolor=ft.Colors.TEAL_700, height=32, padding=ft.Padding(10, 0, 10, 0), alignment=ft.Alignment(0, 0), border_radius=5, on_click=ricalcola_pers)
                        ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER)
                    ], spacing=6),
                    bgcolor="#ECEFF1", padding=7, border_radius=8, border=bordo_campo_uniforme("#B0BEC5")
                )
            )

            for ri in righe_ri:
                raw_ing_dict = ri.get("ingredienti") or {"id": ri.get("id_ingrediente", ""), "nome": "Ingrediente", "unita_default": "g"}
                ing_in_cache = cerca_ingrediente_in_cache_per_nome(raw_ing_dict.get("nome", ""))
                if ing_in_cache:
                    info_i = dict(ing_in_cache)
                else:
                    info_i = normalizza_ingrediente_record(raw_ing_dict)

                bq_i = crea_box_quantita(formatta_numero(ri.get("quantita") or 0), width=84, height=32, text_size=11, mostra_prefisso=True)
                su_i = crea_selettore_unita(page, ri.get("unita_misura") or "g", width=70, height=32, text_size=11)

                is_disp_attivo = is_ingrediente_dispensa_attivo(info_i)
                chk_dispensa = ft.Checkbox(
                    label="📌 Dispensa (Base)",
                    value=is_disp_attivo
                )

                def toggle_dispensa_ing(e, riga_ref=ri, info_ref=info_i):
                    nuovo_stato = bool(e.control.value)
                    u_def = info_ref.get("unita_default", "g")
                    is_d = info_ref.get("is_detersivo", False)
                    tag_det = "|DET" if is_d else ""
                    tag_disp = "|DISPENSA" if nuovo_stato else ""
                    nuova_unita_db = f"{u_def}{tag_det}{tag_disp}"

                    stato_popup["modificato"] = True
                    if riga_ref.get("ingredienti"):
                        riga_ref["ingredienti"]["unita_default"] = nuova_unita_db

                    def w_toggle():
                        imposta_stato_dispensa_ingrediente_db(
                            info_ref.get("nome", ""),
                            info_ref.get("id", ""),
                            nuovo_stato,
                            ULTIMA_DATA_MENU_ATTIVA[0]
                        )
                        if on_close_refresh:
                            on_close_refresh()

                    threading.Thread(target=w_toggle, daemon=True).start()

                chk_dispensa.on_change = toggle_dispensa_ing

                def salva_q(e, riga_ref=ri, b=bq_i, s=su_i, nome_ing=info_i["nome"]):
                    nq, nu = parse_qta(b.textfield.value), s.get_value()
                    log_render(f"[UTENTE] Modifica quantità ingrediente '{nome_ing}' in '{stato_popup['nome_ricetta']}': {nq} {nu}")
                    riga_ref["quantita"] = nq
                    riga_ref["unita_misura"] = nu
                    stato_popup["modificato"] = True
                    disegna_popup_da_ram(msg_extra=f"Quantità aggiornata: {nome_ing}")

                    def w_sq():
                        if not is_valid_uuid(riga_ref.get("id")):
                            for _ in range(10):
                                time.sleep(0.25)
                                if is_valid_uuid(riga_ref.get("id")):
                                    break
                        if is_valid_uuid(riga_ref.get("id")):
                            supabase.table("ricette_ingredienti").update({"quantita": nq, "unita_misura": nu}).eq("id", riga_ref["id"]).execute()
                    threading.Thread(target=w_sq, daemon=True).start()

                def elim_i(e, riga_ref=ri, nome_ing=info_i["nome"]):
                    log_render(f"[UTENTE] Rimozione ingrediente '{nome_ing}' dalla ricetta '{stato_popup['nome_ricetta']}'")
                    stato_popup["righe_ri"] = [x for x in stato_popup["righe_ri"] if x.get("id") != riga_ref.get("id")]
                    stato_popup["modificato"] = True
                    CACHE_ANAGRAFICA["conteggio_ing_ricette"][str(id_ric)] = len(stato_popup["righe_ri"])
                    disegna_popup_da_ram()

                    def w_elim_i():
                        if not is_valid_uuid(riga_ref.get("id")):
                            for _ in range(10):
                                time.sleep(0.25)
                                if is_valid_uuid(riga_ref.get("id")):
                                    break
                        if is_valid_uuid(riga_ref.get("id")):
                            supabase.table("ricette_ingredienti").delete().eq("id", riga_ref["id"]).execute()
                        elif is_valid_uuid(riga_ref.get("id_ricetta")) and is_valid_uuid(riga_ref.get("id_ingrediente")):
                            supabase.table("ricette_ingredienti").delete().eq("id_ricetta", riga_ref["id_ricetta"]).eq("id_ingrediente", riga_ref["id_ingrediente"]).execute()
                        sincronizza_e_pulisci_dispensa_mese(ULTIMA_DATA_MENU_ATTIVA[0])
                        if on_close_refresh:
                            on_close_refresh()
                    threading.Thread(target=w_elim_i, daemon=True).start()

                col_corpo.controls.append(
                    ft.Container(
                        content=ft.Column([
                            ft.Row([
                                ft.Text(f"▪ {info_i['nome']}", size=12, weight=ft.FontWeight.BOLD, expand=True),
                                ft.IconButton(icon=ft.Icons.EDIT, icon_color=ft.Colors.ORANGE_800, icon_size=14, on_click=lambda e, it=info_i, r_ref=ri: apri_popup_rinomina_ingrediente(page, it, CACHE_ANAGRAFICA.get("ingredienti") or [], lambda: (stato_popup.update({"modificato": True}), disegna_popup_da_ram()), riga_ri_ref=r_ref)),
                                ft.IconButton(icon=ft.Icons.CLOSE, icon_color=ft.Colors.RED_700, icon_size=15, on_click=elim_i)
                            ], spacing=2),
                            ft.Row([
                                bq_i, su_i,
                                ft.Container(content=ft.Text("💾 Salva", size=10, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD), bgcolor=ft.Colors.GREEN_700, height=32, padding=ft.Padding(8, 0, 8, 0), alignment=ft.Alignment(0, 0), border_radius=5, on_click=salva_q)
                            ], spacing=4),
                            chk_dispensa
                        ], spacing=3),
                        bgcolor="#F5F7FA", padding=6, border_radius=6, border=bordo_campo_uniforme("#E0E0E0")
                    )
                )

        # 3. BARRA DI RICERCA UNICA + SITI WEB + GEMINI AI
        def on_change_tf_q_web(e):
            stato_popup["query_web"] = tf_q_web.value

        tf_q_web = ft.TextField(
            label="Cerca ricetta (es. 'Pasta e pesto col pesto già pronto')",
            value=stato_popup["query_web"],
            dense=True, text_size=12, expand=True,
            on_change=on_change_tf_q_web
        )
        box_anteprima = ft.Column(spacing=5)

        def esegui_cerca_web(e=None, punta_su_gemini=False):
            qs = (tf_q_web.value or stato_popup["query_web"] or "").strip()
            if not qs or stato_popup["ricerca_in_corso"]:
                return
            termini_modificati = (qs.lower() != stato_popup["nome_ricetta"].strip().lower())
            stato_popup["query_web"] = qs
            stato_popup["persone_web"] = int(max(1, parse_qta(box_p_web.textfield.value) or 8))
            stato_popup["ricerca_in_corso"] = True
            banner_det.visible = True
            lbl_det_status.value = f"🌐 Ricerca multi-sito e Gemini AI per '{qs}'..."
            log_render(f"[UTENTE] Avvio ricerca manuale ricetta per '{qs}' (Termini modificati: {termini_modificati}, Punta su Gemini: {punta_su_gemini})")
            render_anteprima()
            page.update()

            def w_s():
                try:
                    ris = cerca_ricette_su_internet_dettagliate(qs, qs)
                    stato_popup["risultati_web"] = ris
                    stato_popup["ultima_query_cercata"] = qs
                    if punta_su_gemini or termini_modificati:
                        stato_popup["idx_web"] = trova_indice_gemini(ris)
                        log_render(f"[RICERCA WEB+AI] Selezionato automaticamente risultato Gemini AI (indice {stato_popup['idx_web']})")
                    else:
                        stato_popup["idx_web"] = 0
                finally:
                    stato_popup["ricerca_in_corso"] = False
                    banner_det.visible = False
                    render_anteprima()
                    try:
                        page.update()
                    except Exception:
                        pass

            threading.Thread(target=w_s, daemon=True).start()

        def salta_a_gemini(e):
            qs_attuale = (tf_q_web.value or stato_popup["query_web"] or "").strip()
            r_list = stato_popup["risultati_web"]
            if qs_attuale and qs_attuale.lower() != (stato_popup.get("ultima_query_cercata") or "").strip().lower():
                esegui_cerca_web(punta_su_gemini=True)
                return
            idx_g = trova_indice_gemini(r_list)
            log_render(f"[UTENTE] Click su pulsante '✨ Gemini AI' -> selezionato indice {idx_g} ({r_list[idx_g].get('titolo') if r_list else 'N/A'})")
            stato_popup["idx_web"] = idx_g
            render_anteprima()
            page.update()

        def render_anteprima():
            box_anteprima.controls.clear()
            if stato_popup["ricerca_in_corso"]:
                box_anteprima.controls.append(
                    ft.Container(
                        content=ft.Row([
                            ft.ProgressRing(width=16, height=16, stroke_width=2, color=ft.Colors.ORANGE_800),
                            ft.Text("Ricerca in corso su più siti web e su Gemini AI...", size=11, color=ft.Colors.ORANGE_900, italic=True, expand=True)
                        ], spacing=8),
                        bgcolor=ft.Colors.WHITE, padding=10, border_radius=6
                    )
                )
                return

            r_list = stato_popup["risultati_web"]
            if not r_list:
                return
            idx = min(stato_popup["idx_web"], len(r_list) - 1)
            cand = r_list[idx]
            p_t = max(1, int(stato_popup["persone_web"] or 8))
            ings_s = scala_ingredienti_candidato(cand, p_t)

            def applica_cand(e):
                log_render(f"[UTENTE] Applicata proposta '{cand.get('titolo')}' da '{cand.get('sito')}' per {p_t} persone")
                stato_popup["porzioni"] = p_t
                stato_popup["url_fonte"] = cand.get("url_fonte", "")
                stato_popup["foto_url"] = cand.get("foto_url", "")
                stato_popup["modificato"] = True
                stato_popup["risultati_web"] = []

                nuove_righe_ram = []
                for i, x in enumerate(ings_s):
                    ing_c = cerca_ingrediente_in_cache_per_nome(x["nome"])
                    is_disp = is_ingrediente_dispensa_attivo(ing_c or x["nome"])
                    u_def_str = f"{x['unita']}|DISPENSA" if is_disp else x["unita"]
                    nuove_righe_ram.append({
                        "id": f"temp_ri_{i}_{int(time.time()*1000)}",
                        "id_ricetta": id_ric,
                        "id_ingrediente": ing_c["id"] if ing_c else "",
                        "quantita": x["quantita"],
                        "unita_misura": x["unita"],
                        "ingredienti": {"id": ing_c["id"] if ing_c else "", "nome": x["nome"], "unita_default": u_def_str}
                    })

                stato_popup["righe_ri"] = nuove_righe_ram
                CACHE_ANAGRAFICA["conteggio_ing_ricette"][str(id_ric)] = len(ings_s)
                disegna_popup_da_ram(msg_extra=f"Applicata '{cand['titolo']}' ({p_t} persone)!")

                def w_app():
                    vero_id = assicura_id_reale()
                    salva_lista_ingredienti_in_blocco(vero_id, ings_s)
                    salva_meta_ricetta_db(vero_id, stato_popup["portata"], p_t, cand.get("url_fonte", ""), cand.get("foto_url", ""))
                    try:
                        res_agg = supabase.table("ricette_ingredienti").select("*, ingredienti(id, nome, unita_default)").eq("id_ricetta", vero_id).execute()
                        if res_agg.data:
                            # Aggiorna in-place gli ID reali sulle righe già in RAM, così eventuali modifiche fatte subito dall'utente non vengono mai perse!
                            mappa_db = {((r_db.get("ingredienti") or {}).get("nome") or "").strip().lower(): r_db for r_db in res_agg.data}
                            for r_ram in stato_popup["righe_ri"]:
                                n_ram = ((r_ram.get("ingredienti") or {}).get("nome") or "").strip().lower()
                                if n_ram in mappa_db:
                                    r_db = mappa_db[n_ram]
                                    r_ram["id"] = r_db["id"]
                                    r_ram["id_ricetta"] = vero_id
                                    r_ram["id_ingrediente"] = r_db["id_ingrediente"]
                                    if isinstance(r_ram.get("ingredienti"), dict) and isinstance(r_db.get("ingredienti"), dict):
                                        r_ram["ingredienti"]["id"] = r_db["ingredienti"].get("id", "")
                            CACHE_ANAGRAFICA.setdefault("dettaglio_ing_ricette", {})[str(vero_id)] = list(stato_popup["righe_ri"])
                    except Exception:
                        pass
                    if on_close_refresh:
                        on_close_refresh()

                threading.Thread(target=w_app, daemon=True).start()

            ing_str = ", ".join([f"{x['nome']} ({formatta_numero(x['quantita'])} {x['unita']})" if x["unita"] != "q.b." else f"{x['nome']} (q.b.)" for x in ings_s[:10]])
            url_f = cand.get("url_fonte") or f"https://www.google.com/search?q={urllib.parse.quote_plus(stato_popup['query_web'] + ' ricetta')}"

            riga_f = [
                ft.Column([
                    ft.Text(f"📌 {cand['titolo']}", size=12, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900),
                    ft.Text(f"Fonte: {cand['sito']} (dosi per {p_t} pers.)", size=10, color=ft.Colors.GREY_700),
                    ft.TextButton(
                        "🔗 Apri ricetta / preparazione in una nuova scheda",
                        url=url_f,
                        style=ft.ButtonStyle(padding=0)
                    ),
                    *([ft.Text(f"👩‍🍳 {cand['preparazione_breve']}", size=10, italic=True, color=ft.Colors.BLUE_GREY_800)] if cand.get("preparazione_breve") else [])
                ], spacing=2, expand=True)
            ]

            idx_gem = trova_indice_gemini(r_list)
            is_on_gemini = bool(cand.get("is_gemini")) or (idx == idx_gem)
            nav = [
                ft.Row([
                    ft.TextButton("◀ Prec.", on_click=lambda e: (stato_popup.update({"idx_web": (stato_popup["idx_web"] - 1) % len(r_list)}), render_anteprima(), page.update())),
                    ft.Text(f"Proposta {idx + 1} di {len(r_list)}", size=10, weight=ft.FontWeight.BOLD),
                    ft.TextButton("Altra ▶", on_click=lambda e: (stato_popup.update({"idx_web": (stato_popup["idx_web"] + 1) % len(r_list)}), render_anteprima(), page.update())),
                    ft.Container(
                        content=ft.Text("✨ Gemini AI", size=10, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                        bgcolor=ft.Colors.PURPLE_700 if not is_on_gemini else ft.Colors.GREEN_800,
                        padding=ft.Padding(8, 4, 8, 4), border_radius=8,
                        on_click=salta_a_gemini
                    )
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER)
            ]

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

        def on_change_p_web(e):
            stato_popup["persone_web"] = int(max(1, parse_qta(box_p_web.textfield.value) or 8))
            render_anteprima()
            page.update()

        box_p_web = crea_box_quantita(str(stato_popup["persone_web"]), width=88, height=34, text_size=12, mostra_prefisso=True, prefisso_custom="Pers.:", on_change_fn=on_change_p_web)

        tf_q_web.on_submit = lambda e: esegui_cerca_web(e, punta_su_gemini=False)
        render_anteprima()

        col_corpo.controls.append(
            ft.Container(
                content=ft.Column([
                    ft.Text("🔍 Ricerca Ricette Web & ✨ Gemini AI", size=12, weight=ft.FontWeight.BOLD, color=ft.Colors.ORANGE_900),
                    ft.Container(height=4),
                    tf_q_web,
                    ft.Row([
                        box_p_web,
                        ft.Container(
                            content=ft.Row([ft.Icon(ft.Icons.SEARCH, color=ft.Colors.WHITE, size=15), ft.Text("CERCA RICETTA", color=ft.Colors.WHITE, size=11, weight=ft.FontWeight.BOLD)], alignment=ft.MainAxisAlignment.CENTER, spacing=4),
                            bgcolor=ft.Colors.ORANGE_800, height=34, expand=True, border_radius=6, on_click=lambda e: esegui_cerca_web(e, punta_su_gemini=False)
                        )
                    ], spacing=6),
                    box_anteprima
                ], spacing=5),
                bgcolor="#FFF8E1", padding=8, border_radius=8, border=bordo_campo_uniforme("#FB8C00", 1.5)
            )
        )

        try:
            page.update()
        except Exception:
            pass

    def ricarica_ricetta_da_db():
        disegna_popup_da_ram()

        def worker_init():
            try:
                id_ric = assicura_id_reale()
                res_db = supabase.table("ricette").select("*").eq("id", id_ric).execute()
                if res_db.data:
                    ric_c = res_db.data[0]
                    meta = estrai_meta_ricetta(ric_c)
                    stato_popup["nome_ricetta"] = ric_c.get("nome", stato_popup["nome_ricetta"])
                    stato_popup["portata"] = meta["portata"]
                    stato_popup["porzioni"] = meta["porzioni"]
                    stato_popup["url_fonte"] = meta["url_fonte"]
                    stato_popup["foto_url"] = meta["foto_url"]

                res_ri = supabase.table("ricette_ingredienti").select("*, ingredienti(id, nome, unita_default)").eq("id_ricetta", id_ric).execute()
                righe_db = res_ri.data if res_ri.data else []
                stato_popup["righe_ri"] = righe_db
                CACHE_ANAGRAFICA.setdefault("dettaglio_ing_ricette", {})[str(id_ric)] = list(righe_db)
                CACHE_ANAGRAFICA["conteggio_ing_ricette"][str(id_ric)] = len(righe_db)

                if righe_db:
                    sincronizza_e_pulisci_dispensa_mese(ULTIMA_DATA_MENU_ATTIVA[0])

                ha_ing = len(stato_popup["righe_ri"]) > 0
                ha_link = bool((stato_popup.get("url_fonte") or "").strip())

                if (not ha_ing) and (not ha_link) and (not stato_popup["ricerca_fatta"]):
                    stato_popup["ricerca_fatta"] = True
                    stato_popup["ricerca_in_corso"] = True
                    banner_det.visible = True
                    lbl_det_status.value = f"🌐 Ricerca automatica '{stato_popup['query_web']}'..."
                    disegna_popup_da_ram()

                    stato_popup["risultati_web"] = cerca_ricette_su_internet_dettagliate(stato_popup["query_web"], stato_popup["nome_ricetta"])
                    stato_popup["ultima_query_cercata"] = stato_popup["query_web"]
                    stato_popup["idx_web"] = 0
                    stato_popup["ricerca_in_corso"] = False

                banner_det.visible = False
                disegna_popup_da_ram()
            except Exception as ex:
                log_render(f"[ERRORE] worker_init ricetta: {ex}")
                stato_popup["ricerca_in_corso"] = False
                banner_det.visible = False
                disegna_popup_da_ram()

        threading.Thread(target=worker_init, daemon=True).start()

    ricarica_ricetta_da_db()


# ==========================================================
# POPUP INSERIMENTO LAMPO PIATTO UNICO E VOCI DIRETTE
# ==========================================================
def apri_popup_aggiungi_piatto_semplificato(page: ft.Page, data_target: datetime, categoria: str, subcategoria: str, voci_giorno_attuali: list, on_optimistic_add, on_done_callback):
    if not supabase:
        return

    ULTIMA_DATA_MENU_ATTIVA[0] = data_target
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
        log_render(f"[UTENTE] Inserimento piatto '{nome_pulito}' in {categoria} per il giorno {data_str}")

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
                sincronizza_e_pulisci_dispensa_mese(data_target)
            except Exception as ex:
                log_render(f"[ERRORE] Inserimento piatto '{nome_pulito}': {ex}")
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


def apri_popup_aggiungi_voce_diretta(page: ft.Page, data_str: str, categoria: str, titolo_box: str, on_done_callback, on_optimistic_add=None):
    if not supabase:
        return

    lista_ingredienti = CACHE_ANAGRAFICA.get("ingredienti") or []
    lista_ricette = CACHE_ANAGRAFICA.get("ricette") or []
    is_categoria_detersivi = (categoria == "Detersivi")
    is_categoria_dispensa = (categoria == "Dispensa")
    nascondi_qta = is_categoria_dispensa or is_categoria_detersivi

    consente_ricetta = (categoria == "Varie")

    stato_tipo = {"modo": "Ingrediente"}

    hint_testo = (
        "Es. Detersivo piatti, Candeggina, Scottex..." if is_categoria_detersivi 
        else ("Scrivi il prodotto..." if is_categoria_dispensa else "Es. Pane bianco, Frutta oppure Muffin di carote...")
    )

    txt_voce = ft.TextField(
        label=f"Aggiungi a {titolo_box}...",
        hint_text=hint_testo,
        dense=True, text_size=12, autofocus=True
    )
    box_qta = crea_box_quantita(valore_iniziale="1", height=34, text_size=12, mostra_prefisso=True, expand=True)
    sel_uni = crea_selettore_unita(page, valore_iniziale="pz", width=74, height=34, text_size=12)
    riga_qta_uni = ft.Row([box_qta, sel_uni], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER, visible=not nascondi_qta)
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
        if is_categoria_dispensa:
            nome_v = generalizza_nome_dispensa(nome_v)
        salvataggio_in_corso[0] = True
        q_val = 0.0 if (is_categoria_dispensa or is_categoria_detersivi) else parse_qta(box_qta.textfield.value)
        u_val = "pz" if (is_categoria_dispensa or is_categoria_detersivi) else sel_uni.get_value()
        modo_scelto = stato_tipo["modo"]
        sub_db = "Manuale" if is_categoria_dispensa else None

        log_render(f"[UTENTE] Aggiunta voce diretta '{nome_v}' in categoria '{categoria}' (Modo: {modo_scelto}, Data: {data_str})")
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
                    "subcategoria": sub_db, "id_ingrediente": f"temp_i_{int(time.time()*1000)}",
                    "quantita": q_val, "unita_misura": u_val, "preso": False,
                    "ingredienti": {"nome": nome_v, "unita_default": u_val}
                })

        def worker():
            try:
                dt_rif = datetime.strptime(data_str, "%Y-%m-%d")
            except Exception:
                dt_rif = ULTIMA_DATA_MENU_ATTIVA[0]
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
                        sincronizza_e_pulisci_dispensa_mese(dt_rif)
                else:
                    if is_categoria_dispensa:
                        id_i = ottieni_o_crea_ing_dispensa_canonico(nome_v)
                    else:
                        id_i = ottieni_o_crea_ing_sicuro(nome_v, u_val, is_detersivo=is_categoria_detersivi)
                    if id_i:
                        try:
                            supabase.table("menu_giornalieri").insert({
                                "data_menu": data_str, "categoria": categoria, "subcategoria": sub_db,
                                "id_ingrediente": id_i, "quantita": q_val, "unita_misura": u_val
                            }).execute()
                        except Exception:
                            id_i = ottieni_o_crea_ing_sicuro(nome_v, u_val, is_detersivo=is_categoria_detersivi, ignora_cache=True)
                            supabase.table("menu_giornalieri").insert({
                                "data_menu": data_str, "categoria": categoria, "subcategoria": sub_db,
                                "id_ingrediente": id_i, "quantita": q_val, "unita_misura": u_val
                            }).execute()
                        sincronizza_e_pulisci_dispensa_mese(dt_rif)
            except Exception as ex:
                log_render(f"[ERRORE] Salvataggio voce '{nome_v}': {ex}")
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
