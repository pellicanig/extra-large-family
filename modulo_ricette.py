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

def ottieni_lista_modelli_gemini(api_key: str):
    preferiti = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-flash", "gemini-1.5-flash-latest"]
    if MODELLO_GEMINI_RILEVATO[0] and MODELLO_GEMINI_RILEVATO[0] not in _MODELLI_NON_DISPONIBILI:
        resto = [m for m in preferiti if m != MODELLO_GEMINI_RILEVATO[0] and m not in _MODELLI_NON_DISPONIBILI]
        return [MODELLO_GEMINI_RILEVATO[0]] + resto

    attivi_rapidi = [m for m in preferiti if m not in _MODELLI_NON_DISPONIBILI]
    if attivi_rapidi:
        return attivi_rapidi

    scarti = ("tts", "image", "audio", "embedding", "vision", "aqa", "robotics", "computer", "thinking")
    trovati = []
    try:
        url_list = f"https://generativelanguage.googleapis.com/v1beta/models?key={urllib.parse.quote(api_key)}"
        req = urllib.request.Request(url_list, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            dati = json.loads(resp.read().decode("utf-8", errors="ignore"))
        disponibili = [
            m.get("name", "").replace("models/", "")
            for m in dati.get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
            and "gemini" in m.get("name", "").lower()
            and not any(s in m.get("name", "").lower() for s in scarti)
        ]
        for d in disponibili:
            if d not in trovati:
                trovati.append(d)
    except Exception as ex:
        log_render(f"[GEMINI] Errore lettura lista modelli: {ex}")
    return trovati if trovati else ["gemini-2.0-flash", "gemini-1.5-flash"]


def chiedi_ricetta_a_gemini_ai(query_ricerca: str, nome_ricetta_originale: str = ""):
    query_effettiva = (query_ricerca or nome_ricetta_originale or "Ricetta").strip()
    log_render(f"[GEMINI AI] Avvio generazione ricetta per query: '{query_effettiva}'")

    raw_key = os.environ.get("GEMINI_API_KEY", "") or GEMINI_API_KEY
    api_key = raw_key.replace("GEMINI_API_KEY=", "").strip().strip('"').strip("'")
    if not api_key:
        log_render("[GEMINI AI] ATTENZIONE: GEMINI_API_KEY assente nelle variabili d'ambiente!")
        fb = genera_fallback_ricetta(query_effettiva, query_effettiva)
        fb["is_gemini"] = True
        fb["titolo"] = f"✨ {query_effettiva.capitalize()} (Configura GEMINI_API_KEY)"
        fb["sito"] = "🤖 Intelligenza Artificiale Gemini"
        return fb

    prompt = f"""Sei uno chef di cucina casalinga italiana. L'utente deve fare la spesa per preparare esattamente: "{query_effettiva}".
Rispetta rigorosamente tutte le indicazioni e varianti scritte nella richiesta "{query_effettiva}" (es. se specifica ingredienti già pronti, varianti, aggiunte o esclusioni).
Restituisci gli ingredienti semplici ed essenziali per 4 persone.
Unità ammesse: "g", "kg", "ml", "l", "pz", "fette", "cucchiai", "tazze", "q.b.".
Rispondi SOLO con un JSON valido:
{{"titolo": "Nome Piatto", "preparazione_breve": "Spiegazione breve in 2 righe", "porzioni_base": 4, "ingredienti_base": [{{"nome": "Pasta", "qta_base": 350, "unita": "g"}}]}}"""

    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2}
    }).encode("utf-8")

    candidati_modelli = ottieni_lista_modelli_gemini(api_key)
    for modello_target in candidati_modelli[:4]:
        try:
            url_api = f"https://generativelanguage.googleapis.com/v1beta/models/{modello_target}:generateContent?key={urllib.parse.quote(api_key)}"
            req = urllib.request.Request(url_api, data=payload, headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                risp_json = json.loads(resp.read().decode("utf-8", errors="ignore"))
            testo_out = risp_json["candidates"][0]["content"]["parts"][0]["text"]
            m_json = re.search(r"\{.*\}", testo_out, flags=re.DOTALL)
            dati_ai = json.loads(m_json.group(0) if m_json else testo_out)
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
                MODELLO_GEMINI_RILEVATO[0] = modello_target
                log_render(f"[GEMINI AI] Successo con modello '{modello_target}' per '{query_effettiva}' ({len(ings_validi)} ingredienti)")
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
            log_render(f"[GEMINI AI] Tentativo fallito su modello '{modello_target}': {ex}")
            if "404" in str(ex) or "400" in str(ex):
                _MODELLI_NON_DISPONIBILI.add(modello_target)
            if MODELLO_GEMINI_RILEVATO[0] == modello_target:
                MODELLO_GEMINI_RILEVATO[0] = None
            continue

    log_render(f"[GEMINI AI] Tutti i modelli hanno fallito per '{query_effettiva}', restituisco card base Gemini.")
    fb = genera_fallback_ricetta(query_effettiva, query_effettiva)
    fb["is_gemini"] = True
    fb["titolo"] = f"✨ {query_effettiva.capitalize()} (Soluzione AI Gemini)"
    fb["sito"] = "🤖 Intelligenza Artificiale Gemini"
    return fb


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

    righe_li = re.findall(r'<li[^>]*class=["\'][^"\']*(?:wprm-recipe-ingredient|ingredient|gz-ingredient)[^"\']*["\'][^>]*>(.*?)</li>', html_page, flags=re.DOTALL | re.IGNORECASE)
    if not righe_li:
        m_sez_ing = re.search(r'(?i)<h[234][^>]*>[^<]*ingredienti[^<]*</h[234]>\s*<ul[^>]*>(.*?)</ul>', html_page, flags=re.DOTALL)
        if m_sez_ing:
            righe_li = re.findall(r'<li[^>]*>(.*?)</li>', m_sez_ing.group(1), flags=re.DOTALL)
    if righe_li and len(righe_li) >= 2:
        raw_ings = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", x)).strip() for x in righe_li]
        return {
            "titolo": re.sub(r"\s*[-|].*$", "", titolo_og) or "Ricetta Web",
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

    # 2. Ricerca parallela multi-portale
    q_enc = urllib.parse.quote_plus(query_pulita)
    portali_ricerca = [
        ("GialloZafferano", f"https://www.giallozafferano.it/ricerca-ricette/{q_enc}/"),
        ("Cucchiaio d'Argento", f"https://www.cucchiaio.it/ricerca.html?q={q_enc}"),
        ("Misya", f"https://www.misya.info/?s={q_enc}"),
        ("Fatto in casa da Benedetta", f"https://www.fattoincasadabenedetta.it/?s={q_enc}"),
        ("Sonia Peronaci", f"https://www.soniaperonaci.it/?s={q_enc}"),
        ("Motore Multi-Sito", f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(query_pulita + ' ricetta (site:cucchiaio.it OR site:misya.info OR site:fattoincasadabenedetta.it OR site:soniaperonaci.it OR site:giallozafferano.it)')}")
    ]

    pattern_links = (
        r'https://(?:ricette|blog)\.giallozafferano\.it/[a-zA-Z0-9\-_/]+\.html|'
        r'https://blog\.giallozafferano\.it/[a-zA-Z0-9\-_]+/[a-zA-Z0-9\-_]+/?|'
        r'https://www\.cucchiaio\.it/ricetta/[a-zA-Z0-9\-_/]+/?|'
        r'https://www\.misya\.info/ricetta/[a-zA-Z0-9\-_]+\.htm|'
        r'https://www\.fattoincasadabenedetta\.it/ricetta/[a-zA-Z0-9\-_]+/?|'
        r'https://www\.soniaperonaci\.it/[a-zA-Z0-9\-_]+/?'
    )

    parole_chiave = [
        w.lower() for w in re.findall(r"[a-zA-Zàèéìòù]{3,}", query_pulita)
        if w.lower() not in {"con", "alle", "alla", "agli", "del", "della", "dei", "ricetta"}
    ]
    parole_indesiderate = ["capriolo", "anatra", "cinghiale", "lepre", "astice", "lumache"]

    def calcola_score_link(lk):
        slug = lk.rstrip("/").split("/")[-1].lower()
        score = sum(12 for pk in parole_chiave if pk[:4] in slug)
        for p_bad in parole_indesiderate:
            if p_bad in slug and p_bad not in low_q:
                score -= 25
        score -= len(slug) * 0.03
        return score

    mappa_links_portali = {}
    lock_lk = threading.Lock()

    def cerca_su_portale(idx_p, nome_p, u_s):
        try:
            req = urllib.request.Request(u_s, headers=headers)
            with urllib.request.urlopen(req, timeout=4.5) as resp:
                html_s = urllib.parse.unquote(html.unescape(resp.read().decode("utf-8", errors="ignore")))
            esclusioni = ("ricerca", "categoria", "page", "/tag/", "/author/", "/contatti", "/privacy", "/chi-sono", "/video")
            trovati = [
                lk for lk in re.findall(pattern_links, html_s)
                if not any(esc in lk.lower() for esc in esclusioni)
            ]
            trovati_unici = list(dict.fromkeys(trovati))
            trovati_unici.sort(key=calcola_score_link, reverse=True)
            with lock_lk:
                mappa_links_portali[idx_p] = trovati_unici[:3]
            log_render(f"[RICERCA WEB] Portale '{nome_p}': trovati {len(trovati_unici)} link candidati")
        except Exception as ex:
            log_render(f"[RICERCA WEB] Portale '{nome_p}' non raggiungibile o timeout: {ex}")

    threads_s = [threading.Thread(target=cerca_su_portale, args=(i, np, u), daemon=True) for i, (np, u) in enumerate(portali_ricerca)]
    for t in threads_s:
        t.start()
    for t in threads_s:
        t.join(timeout=5.0)

    # Costruzione Round-Robin reale tra i vari siti (senza riordinare globalmente, così da garantire più siti diversi!)
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
                    with lock_lk:
                        risultati_mappa[pos_idx] = {
                            "titolo": info["titolo"],
                            "sito": sito_nome,
                            "url_fonte": link,
                            "foto_url": "",
                            "porzioni_base": info["porzioni_base"] or 4,
                            "ingredienti_base": ings_validi,
                            "is_gemini": False
                        }
                    log_render(f"[RICERCA WEB] Estratta ricetta '{info['titolo']}' da {sito_nome} ({len(ings_validi)} ing.)")
        except Exception:
            pass

    threads_r = [threading.Thread(target=estrai_da_link, args=(idx_l, lk), daemon=True) for idx_l, lk in enumerate(links_candidati[:8])]
    for t in threads_r:
        t.start()
    for t in threads_r:
        t.join(timeout=5.0)

    risultati_trovati = [risultati_mappa[k] for k in sorted(risultati_mappa.keys())]

    # Attende Gemini AI con timeout generoso (9s) e garantisce SEMPRE la presenza del risultato Gemini AI
    t_ai.join(timeout=9.0)
    sol_ai = box_ai_parallelo[0]
    if not sol_ai:
        log_render("[GEMINI AI] Thread AI ancora in corso o vuoto, generazione fallback Gemini immediata.")
        sol_ai = genera_fallback_ricetta(query_ricerca, query_ricerca)
        sol_ai["is_gemini"] = True
        sol_ai["titolo"] = f"✨ {query_ricerca.strip().capitalize()} (Soluzione AI Gemini)"
        sol_ai["sito"] = "🤖 Intelligenza Artificiale Gemini"

    sol_ai["foto_url"] = ""
    sol_ai["is_gemini"] = True
    risultati_trovati.append(sol_ai)

    siti_riepilogo = [r.get("sito", "") for r in risultati_trovati]
    log_render(f"[RICERCA WEB+AI] Completata per '{query_ricerca}': {len(risultati_trovati)} proposte totali -> {siti_riepilogo}")
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
        vecchio_nome = ing_obj.get("nome", "")
        n_nuovo = (txt_nome.value or "").strip().capitalize()
        if not n_nuovo:
            return
        u_scelta = sel_unita.get_value()
        is_disp_attivo = bool(ing_obj.get("is_dispensa_base")) or is_ingrediente_dispensa_attivo(n_nuovo)
        tag_det = "|DET" if ing_obj.get("is_detersivo") else ""
        tag_disp = "|DISPENSA" if is_disp_attivo else ""
        nuova_u_db = f"{u_scelta}{tag_det}{tag_disp}"

        log_render(f"[UTENTE] Rinomina ingrediente: '{vecchio_nome}' -> '{n_nuovo}' (Unità: {u_scelta}, Dispensa: {is_disp_attivo})")

        # 1. AGGIORNAMENTO ISTANTANEO IN RAM
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
            if str(ig.get("id")) == str(ing_obj.get("id")):
                ig["nome"] = n_nuovo
                ig["unita_default"] = u_scelta
                ig["is_dispensa_base"] = is_disp_attivo

        chiudi_dialog_sicuro(page, dlg_mod)
        if on_done_callback:
            on_done_callback()

        # 2. SALVATAGGIO SU DATABASE IN BACKGROUND + SYNC DISPENSA
        def w():
            try:
                id_orig = ing_obj.get("id")
                esistente_altro = next(
                    (i for i in (CACHE_ANAGRAFICA.get("ingredienti") or [])
                     if i.get("nome", "").strip().lower() == n_nuovo.lower() and str(i.get("id")) != str(id_orig)),
                    None
                )
                if esistente_altro and is_valid_uuid(esistente_altro.get("id")):
                    id_target = esistente_altro["id"]
                    if riga_ri_ref and is_valid_uuid(riga_ri_ref.get("id")):
                        supabase.table("ricette_ingredienti").update({"id_ingrediente": id_target, "unita_misura": u_scelta}).eq("id", riga_ri_ref["id"]).execute()
                        riga_ri_ref["id_ingrediente"] = id_target
                        riga_ri_ref["ingredienti"]["id"] = id_target
                elif is_valid_uuid(id_orig):
                    supabase.table("ingredienti").update({"nome": n_nuovo, "unita_default": nuova_u_db}).eq("id", id_orig).execute()
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
                            nuova_riga_ram["id_ingrediente"] = id_i
                            nuova_riga_ram["ingredienti"]["id"] = id_i
                        sincronizza_e_pulisci_dispensa_mese(ULTIMA_DATA_MENU_ATTIVA[0])
                        if on_close_refresh:
                            on_close_refresh()
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
