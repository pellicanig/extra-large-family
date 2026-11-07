import flet as ft


def inizia_operazione(page: ft.Page) -> bool:
    if getattr(page, "elf_operazione_attiva", False):
        return False

    page.elf_operazione_attiva = True
    overlay = getattr(page, "elf_overlay_caricamento", None)
    if overlay is None:
        overlay = ft.Container(
            expand=True,
            bgcolor=ft.Colors.with_opacity(0.38, ft.Colors.BLACK),
            alignment=ft.Alignment(0, 0),
            content=ft.ProgressRing(width=42, height=42, stroke_width=4, color=ft.Colors.WHITE),
            on_click=lambda e: None,
        )
        page.elf_overlay_caricamento = overlay
        page.overlay.append(overlay)
    overlay.visible = True
    page.update()
    return True


def termina_operazione(page: ft.Page):
    page.elf_operazione_attiva = False
    overlay = getattr(page, "elf_overlay_caricamento", None)
    if overlay is not None:
        overlay.visible = False
        try:
            page.update()
        except Exception:
            pass

