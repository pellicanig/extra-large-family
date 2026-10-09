# main.py
import tkinter as tk
import modulo_famiglia # Importa il modulo separato

def avvia_app():
    # Inizializza la finestra principale
    root = tk.Tk()
    root.title("App Gestione - Schermata Principale")
    root.geometry("600x400")

    # Creazione della barra dei menu in alto (la "tendina")
    barra_menu = tk.Menu(root)

    # Creazione della tendina specifica "Menu"
    menu_tendina = tk.Menu(barra_menu, tearoff=0)
    
    # Aggiunta del pulsante "Famiglia" nella tendina
    # Quando cliccato, passa la finestra 'root' al modulo_famiglia
    menu_tendina.add_command(label="Famiglia", command=lambda: modulo_famiglia.apri_schermata_famiglia(root))
    
    # Aggiunta di un separatore e del pulsante di uscita
    menu_tendina.add_separator()
    menu_tendina.add_command(label="Esci", command=root.quit)

    # Associa la tendina alla barra dei menu principale
    barra_menu.add_cascade(label="Seleziona Modulo", menu=menu_tendina)
    root.config(menu=barra_menu)

    # Testo di benvenuto nella schermata principale
    tk.Label(root, text="Benvenuto!\nSeleziona un'opzione dalla tendina in alto.", font=("Arial", 16), justify="center").pack(expand=True)

    # Avvia il loop dell'interfaccia grafica
    root.mainloop()

if __name__ == "__main__":
    avvia_app()
