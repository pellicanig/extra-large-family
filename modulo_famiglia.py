# modulo_famiglia.py
import tkinter as tk
from tkinter import messagebox

def apri_schermata_famiglia(finestra_principale):
    # Crea una nuova finestra "figlia" sovrapposta alla principale
    finestra_famiglia = tk.Toplevel(finestra_principale)
    finestra_famiglia.title("Gestione Famiglia")
    finestra_famiglia.geometry("400x300")

    # Titolo della pagina
    tk.Label(finestra_famiglia, text="Inserisci un nuovo componente", font=("Arial", 14, "bold")).pack(pady=15)

    # Campi di inserimento
    tk.Label(finestra_famiglia, text="Nome del componente:").pack()
    entry_nome = tk.Entry(finestra_famiglia, width=30)
    entry_nome.pack(pady=5)

    def salva_componente():
        nome = entry_nome.get()
        if nome:
            # Qui in futuro inseriremo la connessione per salvare su Supabase
            print(f"Pronto per salvare '{nome}' su Supabase!")
            messagebox.showinfo("Successo", f"Componente '{nome}' aggiunto con successo!")
            entry_nome.delete(0, tk.END) # Svuota il campo dopo il salvataggio
        else:
            messagebox.showwarning("Attenzione", "Il campo nome non può essere vuoto.")

    # Pulsante di salvataggio
    tk.Button(finestra_famiglia, text="Salva", command=salva_componente, bg="lightblue").pack(pady=20)
