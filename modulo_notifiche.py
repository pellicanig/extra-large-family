"""
Modulo ponte (Facade) per il sottosistema Web Push di ELF (Extra Large Family).
Riesporta tutte le funzioni principali dai sottomoduli specializzati:
- modulo_push_stato.py
- modulo_push_registrazione.py
- modulo_push_cancellazione.py
- modulo_push_invio.py
- modulo_push_web.py
- modulo_push_api.py
"""

from modulo_push_stato import _STATO, _LOCK, _DB_LOCK, log_push
from modulo_push_registrazione import (
    registra_pagina,
    is_notifiche_attive,
    ha_dispositivi_registrati,
    ottieni_diag_dispositivo,
)
from modulo_push_cancellazione import imposta_silenzioso
from modulo_push_invio import (
    forza_ricalcolo_impegni,
    invia_notifica_test_globale,
)
from modulo_push_api import collega_api_fastapi

__all__ = [
    "_STATO",
    "_LOCK",
    "_DB_LOCK",
    "log_push",
    "registra_pagina",
    "is_notifiche_attive",
    "ha_dispositivi_registrati",
    "ottieni_diag_dispositivo",
    "imposta_silenzioso",
    "forza_ricalcolo_impegni",
    "invia_notifica_test_globale",
    "collega_api_fastapi",
]
