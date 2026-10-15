# ---------------------------------------------------------
    # COSTRUTTORE CASELLE MENSILI A TUTTO SCHERMO: DISPENSA & DETERSIVI E VARIE
    # ---------------------------------------------------------
    def crea_card_mensile_tutto_schermo(titolo: str, icona_em: str, categoria_db: str, colore_tema: str, voci_mese: list, data_rif_mese: datetime):
        primo_del_mese_str = data_rif_mese.replace(day=1).strftime("%Y-%m-%d")
        nome_mese = MESI[data_rif_mese.month]

        lista_voci_ui = ft.Column(spacing=4)

        if not voci_mese:
            lista_voci_ui.controls.append(
                ft.Container(
                    content=ft.Text(f"Nessun prodotto in {titolo} per {nome_mese}. Clicca '+ Aggiungi' in alto a destra.", size=11, color=ft.Colors.GREY_600, italic=True),
                    padding=8
                )
            )
        else:
            voci_ordinate = sorted(voci_mese, key=lambda x: (1 if x.get("preso") else 0, (x.get("ingredienti") or {}).get("nome", "")))
            for vm in voci_ordinate:
                ing_o = vm.get("ingredienti") or {}
                nome_p = ing_o.get("nome") or "Prodotto"
                q_p = vm.get("quantita") or 0
                u_p = str(vm.get("unita_misura") or "").replace("|DET", "").strip()
                q_str = f" ({formatta_numero(q_p)} {u_p})" if q_p else ""
                is_preso = bool(vm.get("preso"))

                def cambia_spunta_mese(e, voce_ref=vm):
                    nuovo_v = bool(e.control.value)
                    voce_ref["preso"] = nuovo_v
                    disegna_vista_da_ram()
                    threading.Thread(target=lambda: aggiorna_flag_preso_db(voce_ref["id"], nuovo_v), daemon=True).start()

                def elimina_voce_mese(e, id_riga=vm["id"]):
                    if categoria_db == "Dispensa":
                        ram_menu["voci_dispensa_mese"] = [x for x in ram_menu["voci_dispensa_mese"] if x.get("id") != id_riga]
                    else:
                        ram_menu["voci_detersivi_mese"] = [x for x in ram_menu["voci_detersivi_mese"] if x.get("id") != id_riga]
                    disegna_vista_da_ram()
                    threading.Thread(target=lambda: (supabase.table("menu_giornalieri").delete().eq("id", id_riga).execute(), aggiorna_calendario_menu()), daemon=True).start()

                lista_voci_ui.controls.append(
                    ft.Container(
                        content=ft.Row([
                            ft.Checkbox(value=is_preso, on_change=cambia_spunta_mese),
                            ft.Text(
                                f"{'✓ Presente: ' if is_preso else ''}{nome_p}{q_str}",
                                size=12,
                                weight=ft.FontWeight.NORMAL if is_preso else ft.FontWeight.BOLD,
                                color=ft.Colors.GREY_600 if is_preso else ft.Colors.BLACK,
                                italic=is_preso,
                                expand=True
                            ),
                            ft.GestureDetector(
                                content=ft.Icon(ft.Icons.CLOSE, size=16, color=ft.Colors.RED_700),
                                on_tap=elimina_voce_mese
                            )
                        ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        bgcolor="#EEEEEE" if is_preso else ft.Colors.WHITE,
                        padding=ft.Padding(6, 2, 10, 2),
                        border_radius=6,
                        border=bordo_campo_uniforme("#E0E0E0" if is_preso else colore_tema)
                    )
                )

        tot_presenti = sum(1 for x in voci_mese if x.get("preso"))
        tot_da_prendere = len(voci_mese) - tot_presenti

        # ⚡ INTESTAZIONE SENZA wrap=True PER EVITARE IL RIQUADRO GRIGIO DI ERRORE FLUTTER ⚡
        barra_titolo = ft.Container(
            content=ft.Row([
                ft.Text(
                    f"{icona_em} {titolo.upper()} ({nome_mese.upper()})  —  Da prendere: {tot_da_prendere} | Presenti: {tot_presenti}",
                    size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE, expand=True
                ),
                ft.Container(
                    content=ft.Text("+ Aggiungi", size=10, color=colore_tema, weight=ft.FontWeight.BOLD),
                    bgcolor=ft.Colors.WHITE, padding=ft.Padding(10, 4, 10, 4), border_radius=8,
                    on_click=lambda e: apri_popup_aggiungi_voce_diretta(page, primo_del_mese_str, categoria_db, f"{titolo} ({nome_mese})", aggiorna_calendario_menu)
                )
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=6),
            bgcolor=colore_tema, padding=ft.Padding(10, 6, 10, 6), border_radius=6
        )

        card_box = ft.Container(
            content=ft.Column([
                barra_titolo,
                lista_voci_ui
            ], spacing=6),
            expand=1,
            bgcolor="#F4F6F9",
            padding=8,
            border_radius=8,
            border=bordo_campo_uniforme(colore_tema, 1.5)
        )

        return ft.Row([card_box], spacing=6, vertical_alignment=ft.CrossAxisAlignment.START)
