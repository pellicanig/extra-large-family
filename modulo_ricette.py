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
    - Unifica varianti culinarie (es. 'Sale fino' -> 'Sale', 'Pepe nero' -> 'Pepe',
      'Cipolle ramate' -> 'Cipolla', 'Costa sedano' -> 'Sedano', 'Uova medie' -> 'Uova',
      'Olio' / 'Olio evo' -> 'Olio extravergine d'oliva')
    - Preserva le distinzioni merceologiche reali ('Olio di semi' != 'Olio extravergine d'oliva',
      'Vino rosso' != 'Vino bianco', 'Farina di mais' != 'Farina', 'Zucchero di canna' != 'Zucchero').
    """
    s = re.sub(r"\s+", " ", (nome_raw or "").strip()).lower()
    if not s:
        return ""

    # 1. Rimuove prefissi di taglio/porzione tipici delle ricette web
    s = re.sub(
        r"^(costa di |coste di |costa |coste |gambo di |gambi di |spicchio di |spicchio d'|spicchi di |spicchi d'|"
        r"foglie di |foglia di |rametto di |rametti di |ciuffo di |ciuffi di |pizzico di |scorza di |succo di |macinata di )",
        "", s
    ).strip()

    # 2. Famiglie con specificatori merceologici protetti (Olio, Vino, Aceto, Farina, Zucchero, Latte)
    if s.startswith("olio"):
        if any(k in s for k in ["semi", "arachid", "girasol", "mais", "frigg", "fritt"]):
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

    # 3. Raggruppamento radici base della dispensa
    if s.startswith("sale") and "lavastoviglie" not in s:
        return "Sale"
    if (s == "pepe" or s.startswith("pepe ")) and not s.startswith("peperon"):
        return "Pepe"
    if s.startswith("cipoll") and not s.startswith("cipollin"):
        return "Cipolla"
    if s.startswith("agli") or "aglio" in s:
        return "Aglio"
    if s.startswith("sedan") or "sedano" in s:
        return "Sedano"
    if s.startswith("carot"):
        return "Carota"
    if s.startswith("uov") or s in ["tuorlo", "tuorli", "albume", "albumi"]:
        return "Uova"
    if s.startswith("parmigian") or s.startswith("grana padan") or s == "grana":
        return "Parmigiano"
    if s.startswith("burro") and "arachid" not in s:
        return "Burro"
    if s.startswith("pangrattat") or s.startswith("pane grattugiat") or s.startswith("pan grattat"):
        return "Pangrattato"
    if "concentrato di pomodoro" in s:
        return "Concentrato di pomodoro"
    if s.startswith("passata di pomodor") or s == "passata":
        return "Passata di pomodoro"
    if s.startswith("dado"):
        return "Dado vegetale"
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

    # 4. Confronto dinamico con gli ingredienti già marcati |DISPENSA in anagrafica
    # (se il nome inizia con un ingrediente base esistente e non ha specificatori 'di ...' diversi)
    if " di " not in s and " d'" not in s and " al " not in s and " alla " not in s:
        prima_parola = s.split(" ")[0]
        if len(prima_parola) >= 4:
            radice = prima_parola[:-1]
            for ig in (CACHE_ANAGRAFICA.get("ingredienti") or []):
                if ig.get("is_dispensa_base"):
                    n_ig = ig.get("nome", "").strip().lower()
                    if n_ig == prima_parola or ( " " not in n_ig and len(n_ig) >= 4 and n_ig[:-1] == radice):
                        return ig.get("nome", "").strip().capitalize()

    return s.capitalize()


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
            except Exception:
                pass
            if ing_c:
                ing_c["is_dispensa_base"] = True
                ing_c["unita_default"] = u_clean
    return id_ing


def assicura_ingrediente_in_dispensa_mese(id_ingrediente: str, data_rif: datetime = None):
    """Inserisce l'ingrediente (in forma generalizzata) nella Dispensa del mese di riferimento se non è già presente."""
    if not supabase or not is_valid_uuid(id_ingrediente):
        return
    dt = data_rif or datetime.now()
    primo_mese = dt.replace(day=1).strftime("%Y-%m-%d")
    ultimo_gg = calendar.monthrange(dt.year, dt.month)[1]
    fine_mese = dt.replace(day=ultimo_gg).strftime("%Y-%m-%d")
    try:
        ing_c = next((x for x in (CACHE_ANAGRAFICA.get("ingredienti") or []) if str(x.get("id")) == str(id_ingrediente)), None)
        if ing_c and ing_c.get("nome"):
            id_target = ottieni_o_crea_ing_dispensa_canonico(ing_c["nome"]) or id_ingrediente
        else:
            id_target = id_ingrediente

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
    except Exception as ex:
        print("Errore sincronizzazione ingrediente in dispensa:", ex)


def sincronizza_e_pulisci_dispensa_mese(data_rif: datetime = None):
    """
    Esegue lo screening completo della Dispensa per il mese di data_rif:
    1. Legge tutte le ricette pianificate nel mese e individua gli ingredienti marcati come Dispensa.
    2. Generalizza i nomi (es. 'Pepe nero' -> 'Pepe', 'Sale fino' -> 'Sale', 'Cipolle ramate' -> 'Cipolla')
       ed elimina automaticamente i doppioni già presenti in Dispensa (preservando le spunte 'Presente' e le voci manuali).
    3. Popola automaticamente la Dispensa del mese (es. Novembre) con gli ingredienti base delle ricette del mese.
    """
    if not supabase:
        return
    dt = data_rif or datetime.now()
    primo_mese = dt.replace(day=1).strftime("%Y-%m-%d")
    ultimo_gg = calendar.monthrange(dt.year, dt.month)[1]
    fine_mese = dt.replace(day=ultimo_gg).strftime("%Y-%m-%d")

    try:
        lista_ing_cache, _ = assicura_cache_anagrafica(forza_ricarica=False)
        nomi_dispensa_attivi = {
            generalizza_nome_dispensa(i["nome"]).lower()
            for i in lista_ing_cache if i.get("is_dispensa_base") and i.get("nome")
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
        ids_ricette_mese = list({r["id_ricetta"] for r in righe_mese if r.get("categoria") not in ["Dispensa", "Detersivi"] and is_valid_uuid(r.get("id_ricetta"))})

        # 1. Raccoglie i nomi generalizzati richiesti dalle ricette pianificate nel mese
        richiesti_da_ricette = set()
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
                if "|DISPENSA" in u_def or nome_gen.lower() in nomi_dispensa_attivi:
                    richiesti_da_ricette.add(nome_gen)
                    nomi_dispensa_attivi.add(nome_gen.lower())

        # 2. Deduplica e normalizza le righe già presenti nella Dispensa del mese (mantenendo anche quelle manuali)
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
            visti_canonici.add(k_low)
            id_canonico = ottieni_o_crea_ing_dispensa_canonico(nome_gen)
            if not is_valid_uuid(id_canonico):
                continue

            preso_unificato = any(bool(x.get("preso")) for x in righe_g)
            riga_principale = next((x for x in righe_g if str(x.get("id_ingrediente")) == str(id_canonico)), righe_g[0])

            # Se la riga principale puntava a una variante (es. 'Pepe nero' invece di 'Pepe') o cambia stato 'preso', la aggiorna
            if str(riga_principale.get("id_ingrediente")) != str(id_canonico) or bool(riga_principale.get("preso")) != preso_unificato:
                try:
                    supabase.table("menu_giornalieri").update({
                        "id_ingrediente": id_canonico,
                        "preso": preso_unificato,
                        "quantita": 0,
                        "unita_misura": "pz"
                    }).eq("id", riga_principale["id"]).execute()
                except Exception:
                    pass

            # Elimina eventuali righe doppie dello stesso ingrediente generalizzato
            for r_dup in righe_g:
                if r_dup["id"] != riga_principale["id"]:
                    try:
                        supabase.table("menu_giornalieri").delete().eq("id", r_dup["id"]).execute()
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
        if nuove_righe_disp:
            supabase.table("menu_giornalieri").insert(nuove_righe_disp).execute()

    except Exception as ex:
        print("Errore sincronizza_e_pulisci_dispensa_mese:", ex)


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
    unita_db = f"{unita_clean}|DET" if is_detersivo else unita_clean

    if not ignora_cache:
        esistente = next((i for i in lista_ing if i["nome"].strip().lower() == nome_pulito.lower()), None)
        if esistente:
            if is_detersivo and not esistente.get("is_detersivo"):
                esistente["is_detersivo"] = True
                CACHE_ANAGRAFICA["ids_detersivi"].add(esistente["id"])
                tag_disp = "|DISPENSA" if esistente.get("is_dispensa_base") else ""
                try:
                    supabase.table("ingredienti").update({"unita_default": f"{unita_clean}|DET{tag_disp}"}).eq("id", esistente["id"]).execute()
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
            # Specchia in Dispensa SOLO se l'ingrediente è stato flaggato dall'utente con il checkbox |DISPENSA
            ing_cache = next((x for x in (CACHE_ANAGRAFICA.get("ingredienti") or []) if str(x.get("id")) == str(id_ing)), None)
            if ing_cache and ing_cache.get("is_dispensa_base"):
                assicura_ingrediente_in_dispensa_mese(id_ing)

    supabase.table("ricette_ingredienti").delete().eq("id_ricetta", id_ricetta).execute()
    if righe_ri_bulk:
        try:
            supabase.table("ricette_ingredienti").insert(righe_ri_bulk).execute()
        except Exception:
            pass

    CACHE_ANAGRAFICA["conteggio_ing_ricette"][str(id_ricetta)] = len(righe_ri_bulk)
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


def ottieni_lista_modelli_gemini(api_key: str):
    if MODELLO_GEMINI_RILEVATO[0]:
        return [MODELLO_GEMINI_RILEVATO[0]]
    scarti = ("tts", "image", "audio", "embedding", "vision", "aqa", "robotics", "computer", "thinking")
    preferiti = ["gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-flash", "gemini-1.5-flash-latest", "gemini-2.5-flash"]
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
        for p in preferiti:
            if p in disponibili and p not in trovati:
                trovati.append(p)
        for d in disponibili:
            if d not in trovati and "preview" not in d and "exp" not in d:
                trovati.append(d)
        for d in disponibili:
            if d not in trovati:
                trovati.append(d)
    except Exception as ex:
        print("Info lista modelli Gemini:", ex)
    return trovati if trovati else ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"]


def chiedi_ricetta_a_gemini_ai(query_ricerca: str, nome_ricetta_originale: str = ""):
    raw_key = os.environ.get("GEMINI_API_KEY", "") or GEMINI_API_KEY
    api_key = raw_key.replace("GEMINI_API_KEY=", "").strip().strip('"').strip("'")
    if not api_key:
        return genera_fallback_ricetta(query_ricerca, nome_ricetta_originale)

    prompt = f"""Sei uno chef di cucina casalinga italiana. L'utente deve fare la spesa per preparare: "{query_ricerca}" (piatto: "{nome_ricetta_originale}").
Restituisci gli ingredienti semplici ed essenziali per 4 persone.
Se l'utente specifica che il condimento è già pronto (es. pesto pronto, ragù pronto), metti la base da cuocere (es. Pasta 350 g) + il condimento già pronto (es. Pesto pronto 190 g), senza basilico o pinoli.
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
            with urllib.request.urlopen(req, timeout=6) as resp:
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
                return {
                    "titolo": f"✨ {dati_ai.get('titolo') or nome_ricetta_originale} (Soluzione AI Gemini)",
                    "sito": "🤖 Intelligenza Artificiale Gemini",
                    "preparazione_breve": dati_ai.get("preparazione_breve", ""),
                    "url_fonte": f"https://www.google.com/search?q={urllib.parse.quote_plus((nome_ricetta_originale or query_ricerca) + ' ricetta')}",
                    "foto_url": "",
                    "porzioni_base": int(dati_ai.get("porzioni_base") or 4),
                    "ingredienti_base": ings_validi
                }
        except Exception as ex:
            if MODELLO_GEMINI_RILEVATO[0] == modello_target:
                MODELLO_GEMINI_RILEVATO[0] = None
            print(f"Tentativo modello {modello_target} non riuscito ({ex}), provo il successivo...")
            continue

    return genera_fallback_ricetta(query_ricerca, nome_ricetta_originale)


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

    righe_li = re.findall(r'<li[^>]*class=["\'][^"\']*(?:wprm-recipe-ingredient|ingredient)[^"\']*["\'][^>]*>(.*?)</li>', html_page, flags=re.DOTALL | re.IGNORECASE)
    if not righe_li:
        m_sez_ing = re.search(r'(?i)<h[234][^>]*>[^<]*ingredienti[^<]*</h[234]>\s*<ul[^>]*>(.*?)</ul>', html_page, flags=re.DOTALL)
        if m_sez_ing:
            righe_li = re.findall(r'<li[^>]*>(.*?)</li>', m_sez_ing.group(1), flags=re.DOTALL)
    if righe_li and len(righe_li) >= 2:
        raw_ings = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", x)).strip() for x in righe_li]
        return {
            "titolo": re.sub(r"\s*[-|].*$", "", titolo_og) or "Ricetta dal Blog GialloZafferano",
            "porzioni_base": 4,
            "raw_ings": raw_ings[:15],
            "foto_url": foto_og,
            "url_fonte": url_pagina
        }
    return None


def cerca_ricette_su_internet_dettagliate(query_ricerca: str, nome_ricetta_originale: str = ""):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "it-IT,it;q=0.9"
    }
    low_q = query_ricerca.lower()
    query_pulita = re.sub(r"\([^)]*\)", " ", query_ricerca)
    query_pulita = re.sub(r"(?i)\b(il|la|lo|è|gia|già|pronto|pronta|comprato|comprata)\b", " ", query_pulita)
    query_pulita = re.sub(r"\s+", " ", query_pulita).strip() or query_ricerca.strip()

    box_ai_parallelo = [None]
    def worker_ai():
        box_ai_parallelo[0] = chiedi_ricetta_a_gemini_ai(query_ricerca, nome_ricetta_originale or query_pulita)
    t_ai = threading.Thread(target=worker_ai, daemon=True)
    t_ai.start()

    q_enc = urllib.parse.quote_plus(query_pulita)
    urls_ricerca = [
        f"https://www.giallozafferano.it/ricerca-ricette/{q_enc}/",
        f"https://www.cucchiaio.it/ricerca.html?q={q_enc}",
        f"https://www.misya.info/?s={q_enc}",
        f"https://www.fattoincasadabenedetta.it/?s={q_enc}",
        f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(query_pulita + ' ricetta site:blog.giallozafferano.it OR site:giallozafferano.it OR site:cucchiaio.it OR site:misya.info OR site:fattoincasadabenedetta.it')}"
    ]

    pattern_links = (
        r'https://(?:ricette|blog)\.giallozafferano\.it/[a-zA-Z0-9\-_/]+\.html|'
        r'https://blog\.giallozafferano\.it/[a-zA-Z0-9\-_]+/[a-zA-Z0-9\-_]+/?|'
        r'https://www\.cucchiaio\.it/ricetta/[a-zA-Z0-9\-_]+/?|'
        r'https://www\.misya\.info/ricetta/[a-zA-Z0-9\-_]+\.htm|'
        r'https://www\.fattoincasadabenedetta\.it/ricetta/[a-zA-Z0-9\-_]+/?'
    )

    parole_chiave = [
        w.lower() for w in re.findall(r"[a-zA-Zàèéìòù]{3,}", query_pulita)
        if w.lower() not in {"con", "alle", "alla", "agli", "del", "della", "dei"}
    ]
    parole_indesiderate = ["bianco", "capriolo", "anatra", "cinghiale", "lepre", "broccoli", "gamberi", "astice", "vegan"]

    def calcola_score_link(lk):
        slug = lk.rstrip("/").split("/")[-1].lower()
        score = sum(12 for pk in parole_chiave if pk[:4] in slug)
        for p_bad in parole_indesiderate:
            if p_bad in slug and p_bad not in low_q:
                score -= 25
        score -= len(slug) * 0.05
        return score

    links_per_portale = []
    lock_lk = threading.Lock()

    def cerca_su_portale(u_s):
        try:
            req = urllib.request.Request(u_s, headers=headers)
            with urllib.request.urlopen(req, timeout=4) as resp:
                html_s = urllib.parse.unquote(html.unescape(resp.read().decode("utf-8", errors="ignore")))
            trovati = [
                lk for lk in re.findall(pattern_links, html_s)
                if "ricerca" not in lk and "categoria" not in lk and "page" not in lk
            ]
            trovati_unici = list(dict.fromkeys(trovati))
            trovati_unici.sort(key=calcola_score_link, reverse=True)
            with lock_lk:
                links_per_portale.append(trovati_unici[:2])
        except Exception:
            pass

    threads_s = [threading.Thread(target=cerca_su_portale, args=(u,), daemon=True) for u in urls_ricerca]
    for t in threads_s:
        t.start()
    for t in threads_s:
        t.join(timeout=4.5)

    links_candidati = []
    for round_idx in range(2):
        for lista_p in links_per_portale:
            if round_idx < len(lista_p) and lista_p[round_idx] not in links_candidati:
                links_candidati.append(lista_p[round_idx])

    links_candidati.sort(key=calcola_score_link, reverse=True)

    risultati_trovati = []
    def estrai_da_link(link):
        try:
            req_r = urllib.request.Request(link, headers=headers)
            with urllib.request.urlopen(req_r, timeout=4) as resp_r:
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
                          ("Fatto in casa da Benedetta" if "benedetta" in link else "Cucchiaio d'Argento")))
                    )
                    with lock_lk:
                        risultati_trovati.append({
                            "titolo": info["titolo"],
                            "sito": sito_nome,
                            "url_fonte": link,
                            "foto_url": "",
                            "porzioni_base": info["porzioni_base"] or 4,
                            "ingredienti_base": ings_validi
                        })
        except Exception:
            pass

    threads_r = [threading.Thread(target=estrai_da_link, args=(lk,), daemon=True) for lk in links_candidati[:6]]
    for t in threads_r:
        t.start()
    for t in threads_r:
        t.join(timeout=4.5)

    t_ai.join(timeout=4)
    sol_ai = box_ai_parallelo[0]
    if sol_ai and sol_ai.get("ingredienti_base"):
        sol_ai["foto_url"] = ""
        risultati_trovati.append(sol_ai)
    elif not risultati_trovati:
        risultati_trovati.append(genera_fallback_ricetta(query_ricerca, nome_ricetta_originale or query_pulita))

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
            tag_det = "|DET" if ing_obj.get("is_detersivo") else ""
            tag_disp = "|DISPENSA" if ing_obj.get("is_dispensa_base") else ""
            nuova_u_db = f"{sel_unita.get_value()}{tag_det}{tag_disp}"
            supabase.table("ingredienti").update({"nome": n_nuovo, "unita_default": nuova_u_db}).eq("id", ing_obj["id"]).execute()
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
    ric_cache = next((r for r in (CACHE_ANAGRAFICA["ricette"] or []) if str(r.get("id")) == str(id_ricetta)), None)
    meta = estrai_meta_ricetta(ric_cache or {"portata_default": portata_attuale})
    txt_nome_ric = ft.TextField(label="Nome Ricetta", value=nome_attuale, dense=True, text_size=13)
    dlg_ric = None

    def salva_ricetta(e):
        n_u = (txt_nome_ric.value or "").strip().capitalize()
        if not n_u:
            return
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
        chiudi_dialog_sicuro(page, dlg_det)
        if stato_popup["modificato"] and on_close_refresh:
            threading.Thread(target=on_close_refresh, daemon=True).start()

    def togli_dal_menu(e):
        id_vm = stato_popup["id_voce_menu"]
        chiudi_dialog_sicuro(page, dlg_det)
        if is_valid_uuid(id_vm):
            threading.Thread(target=lambda: (supabase.table("menu_giornalieri").delete().eq("id", id_vm).execute(), on_close_refresh() if on_close_refresh else None), daemon=True).start()

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

    def disegna_popup_da_ram(msg_extra=None):
        id_ric = stato_popup["id_ricetta"]
        porzioni_attuali = stato_popup["porzioni"]
        url_fonte_attuale = stato_popup["url_fonte"]
        foto_url_attuale = stato_popup["foto_url"]

        # Deduplicazione automatica in RAM per evitare doppioni visivi
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
            ing_esistente = cerca_ingrediente_in_cache_per_nome(nv)
            u_def_init = f"{uv}|DISPENSA" if (ing_esistente and ing_esistente.get("is_dispensa_base")) else uv

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
                        ing_c = cerca_ingrediente_in_cache_per_nome(nv)
                        if ing_c and ing_c.get("is_dispensa_base"):
                            assicura_ingrediente_in_dispensa_mese(id_i)
                        if on_close_refresh:
                            on_close_refresh()
                except Exception as ex:
                    print("Errore salvataggio manuale bg:", ex)

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
                # Sincronizza lo stato con l'anagrafica in cache se presente
                ing_in_cache = cerca_ingrediente_in_cache_per_nome(raw_ing_dict.get("nome", ""))
                if ing_in_cache:
                    info_i = dict(ing_in_cache)
                else:
                    info_i = normalizza_ingrediente_record(raw_ing_dict)

                bq_i = crea_box_quantita(formatta_numero(ri.get("quantita") or 0), width=84, height=32, text_size=11, mostra_prefisso=True)
                su_i = crea_selettore_unita(page, ri.get("unita_misura") or "g", width=70, height=32, text_size=11)

                is_disp_attivo = bool(info_i.get("is_dispensa_base", False))
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

                    for ig in (CACHE_ANAGRAFICA.get("ingredienti") or []):
                        if ig.get("nome", "").strip().lower() == info_ref.get("nome", "").strip().lower():
                            ig["unita_default"] = u_def
                            ig["is_dispensa_base"] = nuovo_stato

                    def w_toggle():
                        ing_id_reale = info_ref.get("id")
                        if not is_valid_uuid(ing_id_reale):
                            ing_id_reale = ottieni_o_crea_ing_sicuro(info_ref.get("nome", ""), u_def, is_d)
                        if is_valid_uuid(ing_id_reale):
                            supabase.table("ingredienti").update({"unita_default": nuova_unita_db}).eq("id", ing_id_reale).execute()
                            if nuovo_stato:
                                assicura_ingrediente_in_dispensa_mese(ing_id_reale)
                            else:
                                try:
                                    supabase.table("menu_giornalieri").delete().eq("categoria", "Dispensa").eq("id_ingrediente", ing_id_reale).execute()
                                except Exception:
                                    pass
                            if on_close_refresh:
                                on_close_refresh()

                    threading.Thread(target=w_toggle, daemon=True).start()

                chk_dispensa.on_change = toggle_dispensa_ing

                def salva_q(e, riga_ref=ri, b=bq_i, s=su_i, nome_ing=info_i["nome"]):
                    nq, nu = parse_qta(b.textfield.value), s.get_value()
                    riga_ref["quantita"] = nq
                    riga_ref["unita_misura"] = nu
                    stato_popup["modificato"] = True
                    disegna_popup_da_ram(msg_extra=f"Quantità aggiornata: {nome_ing}")
                    if is_valid_uuid(riga_ref.get("id")):
                        threading.Thread(target=lambda: supabase.table("ricette_ingredienti").update({"quantita": nq, "unita_misura": nu}).eq("id", riga_ref["id"]).execute(), daemon=True).start()

                def elim_i(e, riga_ref=ri):
                    stato_popup["righe_ri"] = [x for x in stato_popup["righe_ri"] if x.get("id") != riga_ref.get("id")]
                    stato_popup["modificato"] = True
                    CACHE_ANAGRAFICA["conteggio_ing_ricette"][str(id_ric)] = len(stato_popup["righe_ri"])
                    disegna_popup_da_ram()
                    if is_valid_uuid(riga_ref.get("id")):
                        threading.Thread(target=lambda: (supabase.table("ricette_ingredienti").delete().eq("id", riga_ref["id"]).execute(), on_close_refresh() if on_close_refresh else None), daemon=True).start()

                col_corpo.controls.append(
                    ft.Container(
                        content=ft.Column([
                            ft.Row([
                                ft.Text(f"▪ {info_i['nome']}", size=12, weight=ft.FontWeight.BOLD, expand=True),
                                ft.IconButton(icon=ft.Icons.EDIT, icon_color=ft.Colors.ORANGE_800, icon_size=14, on_click=lambda e, it=info_i: apri_popup_rinomina_ingrediente(page, it, CACHE_ANAGRAFICA.get("ingredienti") or [], ricarica_ricetta_da_db)),
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
        tf_q_web = ft.TextField(label="Cerca ricetta (es. 'Pasta e pesto col pesto già pronto')", value=stato_popup["query_web"], dense=True, text_size=12, expand=True)
        box_anteprima = ft.Column(spacing=5)

        def render_anteprima():
            box_anteprima.controls.clear()
            if stato_popup["ricerca_in_corso"]:
                box_anteprima.controls.append(
                    ft.Container(
                        content=ft.Row([
                            ft.ProgressRing(width=16, height=16, stroke_width=2, color=ft.Colors.ORANGE_800),
                            ft.Text("Ricerca in background su GialloZafferano, Blog e Gemini AI...", size=11, color=ft.Colors.ORANGE_900, italic=True, expand=True)
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
                stato_popup["porzioni"] = p_t
                stato_popup["url_fonte"] = cand.get("url_fonte", "")
                stato_popup["foto_url"] = cand.get("foto_url", "")
                stato_popup["modificato"] = True
                stato_popup["risultati_web"] = []

                nuove_righe_ram = []
                for i, x in enumerate(ings_s):
                    ing_c = cerca_ingrediente_in_cache_per_nome(x["nome"])
                    u_def_str = f"{x['unita']}|DISPENSA" if (ing_c and ing_c.get("is_dispensa_base")) else x["unita"]
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
                            stato_popup["righe_ri"] = res_agg.data
                            CACHE_ANAGRAFICA.setdefault("dettaglio_ing_ricette", {})[str(vero_id)] = list(res_agg.data)
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

            nav = []
            if len(r_list) > 1:
                is_on_gemini = (idx == len(r_list) - 1)
                nav.append(
                    ft.Row([
                        ft.TextButton("◀ Prec.", on_click=lambda e: (stato_popup.update({"idx_web": (stato_popup["idx_web"] - 1) % len(r_list)}), render_anteprima(), page.update())),
                        ft.Text(f"Proposta {idx + 1} di {len(r_list)}", size=10, weight=ft.FontWeight.BOLD),
                        ft.TextButton("Altra ▶", on_click=lambda e: (stato_popup.update({"idx_web": (stato_popup["idx_web"] + 1) % len(r_list)}), render_anteprima(), page.update())),
                        ft.Container(
                            content=ft.Text("✨ Gemini AI", size=10, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                            bgcolor=ft.Colors.PURPLE_700 if not is_on_gemini else ft.Colors.GREY_500,
                            padding=ft.Padding(8, 4, 8, 4), border_radius=8,
                            on_click=lambda e: (stato_popup.update({"idx_web": len(r_list) - 1}), render_anteprima(), page.update())
                        )
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER)
                )

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

        def esegui_cerca_web(e=None):
            qs = (tf_q_web.value or "").strip()
            if not qs or stato_popup["ricerca_in_corso"]:
                return
            stato_popup["query_web"] = qs
            stato_popup["persone_web"] = int(max(1, parse_qta(box_p_web.textfield.value) or 8))
            stato_popup["ricerca_in_corso"] = True
            banner_det.visible = True
            lbl_det_status.value = f"🌐 Ricerca in background '{qs}'..."
            render_anteprima()
            page.update()

            def w_s():
                try:
                    stato_popup["risultati_web"] = cerca_ricette_su_internet_dettagliate(qs, stato_popup["nome_ricetta"])
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

        tf_q_web.on_submit = esegui_cerca_web
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
                            bgcolor=ft.Colors.ORANGE_800, height=34, expand=True, border_radius=6, on_click=esegui_cerca_web
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

                # Se la ricetta contiene ingredienti già marcati con |DISPENSA, si assicura che siano nella Dispensa del mese
                for r_item in righe_db:
                    ing_d = r_item.get("ingredienti") or {}
                    if "|DISPENSA" in str(ing_d.get("unita_default") or "") and is_valid_uuid(ing_d.get("id")):
                        assicura_ingrediente_in_dispensa_mese(ing_d["id"])

                ha_ing = len(stato_popup["righe_ri"]) > 0
                ha_link = bool((stato_popup.get("url_fonte") or "").strip())

                if (not ha_ing) and (not ha_link) and (not stato_popup["ricerca_fatta"]):
                    stato_popup["ricerca_fatta"] = True
                    stato_popup["ricerca_in_corso"] = True
                    banner_det.visible = True
                    lbl_det_status.value = f"🌐 Ricerca automatica '{stato_popup['query_web']}'..."
                    disegna_popup_da_ram()

                    stato_popup["risultati_web"] = cerca_ricette_su_internet_dettagliate(stato_popup["query_web"], stato_popup["nome_ricetta"])
                    stato_popup["idx_web"] = 0
                    stato_popup["ricerca_in_corso"] = False

                banner_det.visible = False
                disegna_popup_da_ram()
            except Exception as ex:
                print("Errore worker_init ricetta:", ex)
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
        salvataggio_in_corso[0] = True
        q_val = 0.0 if (is_categoria_dispensa or is_categoria_detersivi) else parse_qta(box_qta.textfield.value)
        u_val = "pz" if (is_categoria_dispensa or is_categoria_detersivi) else sel_uni.get_value()
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
