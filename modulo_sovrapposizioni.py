def calcola_minuti(orario_str):
    if not orario_str: return 0
    try:
        h, m = map(int, str(orario_str)[:5].split(':'))
        return h * 60 + m
    except: return 0

def analizza_sovrapposizioni_giornata(eventi_giornata):
    """
    Analizza gli eventi di una singola giornata.
    Ciascun evento in eventi_giornata deve essere un dict con:
    - 'id'
    - 'ora_inizio', 'ora_fine'
    - 'assegnatari_ids': set o list di ID familiari (relazionali Supabase)
    
    Restituisce un dizionario: { id_impegno: livello_sovrapposizione }
    Livelli possibili: 'lieve', 'media', 'grave'
    """
    risultati = {}
    n = len(eventi_giornata)
    
    for i in range(n):
        ev1 = eventi_giornata[i]
        id1 = ev1.get('id')
        i_start = calcola_minuti(ev1.get('ora_inizio'))
        i_end = calcola_minuti(ev1.get('ora_fine')) if ev1.get('ora_fine') else i_start + 60
        asg1 = set(ev1.get('assegnatari_ids', []))
        
        livello_peggiore = None
        
        for j in range(n):
            if i == j: continue
            ev2 = eventi_giornata[j]
            j_start = calcola_minuti(ev2.get('ora_inizio'))
            j_end = calcola_minuti(ev2.get('ora_fine')) if ev2.get('ora_fine') else j_start + 60
            asg2 = set(ev2.get('assegnatari_ids', []))
            
            # Calcolo intersezione temporale
            inizio_sov = max(i_start, j_start)
            fine_sov = min(i_end, j_end)
            durata_sov = fine_sov - inizio_sov
            
            # Verifica eventi consecutivi / attigui (margine <= 5 minuti)
            consecutivo = (abs(i_end - j_start) <= 5) or (abs(j_end - i_start) <= 5)
            
            if durata_sov > 5:
                # Sovrapposizione superiore a 5 minuti (Media o Grave)
                tutta_famiglia1 = len(asg1) == 0
                tutta_famiglia2 = len(asg2) == 0
                
                # C'è condivisione di assegnatari se hanno ID in comune o se uno/entrambi coinvolgono tutta la famiglia
                assegnatari_comuni = not asg1.isdisjoint(asg2) if (not tutta_famiglia1 and not tutta_famiglia2) else True
                
                if assegnatari_comuni:
                    livello_peggiore = 'grave' # Priorità massima (Triangolo Rosso)
                    break
                else:
                    if livello_peggiore != 'grave':
                        livello_peggiore = 'media' # Triangolo Arancione
            elif consecutivo and durata_sov <= 0:
                # Sovrapposizione Lieve (fine coincide con inizio con margine <= 5 min)
                if not livello_peggiore:
                    livello_peggiore = 'lieve'
                    
        if livello_peggiore:
            risultati[id1] = livello_peggiore
            
    return risultati
