from __future__ import annotations

import threading

import flet as ft

import app_info
import config
import website_client

APP_TITLE = "SONUS Settings"
ACCENT = "#00B3FF"
ACCENT_ALT = "#0066FF"
BG = "#030712"
SURFACE = "#0B101E"
CARD = "#0F172A"
CARD_SOFT = "#131C31"
BORDER = "#1E293B"
TEXT = "#F8FBFF"
MUTED = "#94A3B8"
MUTED_SOFT = "#64748B"
SUCCESS = "#22C55E"

MODEL_OPTIONS = [
    ("Mini", "voxtral-mini-2507"),
    ("Small", "voxtral-small-2507"),
]
BATCH_MODEL_OPTIONS = [
    ("Mini", "voxtral-mini-2507"),
]

LANGUAGE_OPTIONS = [
    ("Auto-detect", ""),
    ("English", "en"),
    ("Urdu", "ur"),
    ("Korean", "ko"),
    ("Japanese", "ja"),
    ("Chinese (Mandarin)", "zh"),
    ("German", "de"),
    ("French", "fr"),
    ("Spanish", "es"),
    ("Arabic", "ar"),
    ("Hindi", "hi"),
    ("Custom...", "custom"),
]

TABS: list[tuple[str, str, str]] = [
    ("general", "General", ft.Icons.PALETTE_OUTLINED),
    ("audio", "Audio", ft.Icons.MIC),
    ("text", "Text", ft.Icons.AUTO_AWESOME),
    ("system", "System", ft.Icons.MEMORY_OUTLINED),
    ("about", "About", ft.Icons.INFO_OUTLINE),
]


def _dropdown_options(items: list[tuple[str, str]]) -> list[ft.dropdown.Option]:
    return [ft.dropdown.Option(label) for label, _ in items]


def _dropdown_value(items: list[tuple[str, str]], stored_value: str, default_label: str) -> str:
    reverse = {value: label for label, value in items}
    return reverse.get(stored_value, default_label)


def _field_style() -> dict:
    return {
        "border_radius": 10,
        "filled": True,
        "fill_color": CARD_SOFT,
        "border_color": BORDER,
        "focused_border_color": ACCENT,
        "text_style": ft.TextStyle(color=TEXT, size=12),
        "label_style": ft.TextStyle(color=MUTED, size=10, weight=ft.FontWeight.W_700),
    }


def _list_to_multiline(values: list[str]) -> str:
    return "\n".join((value or "").strip() for value in values if (value or "").strip())


def _dict_to_multiline(values: dict[str, str]) -> str:
    lines: list[str] = []
    for key, value in values.items():
        clean_k = (key or "").strip()
        clean_v = (value or "").strip()
        if clean_k and clean_v:
            lines.append(f"{clean_k} => {clean_v}")
    return "\n".join(lines)


def _parse_multiline_list(raw_text: str) -> list[str]:
    return [line.strip() for line in (raw_text or "").splitlines() if line.strip()]


def _parse_replacements(raw_text: str) -> dict[str, str]:
    replacements: dict[str, str] = {}
    for line in (raw_text or "").splitlines():
        clean = line.strip()
        if not clean:
            continue
        if "=>" in clean:
            left, right = clean.split("=>", 1)
        elif ":" in clean:
            left, right = clean.split(":", 1)
        else:
            continue
        key = left.strip()
        value = right.strip()
        if key and value:
            replacements[key] = value
    return replacements


def _setting_row(label: str, control: ft.Control) -> ft.Row:
    return ft.Row(
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.Text(label, color=TEXT, size=12, weight=ft.FontWeight.W_500),
            control,
        ],
    )


def _card(title: str, icon: str, controls: list[ft.Control]) -> ft.Container:
    return ft.Container(
        padding=ft.Padding(14, 14, 14, 14),
        border_radius=14,
        bgcolor=SURFACE,
        border=ft.Border.all(1, BORDER),
        content=ft.Column(
            spacing=10,
            controls=[
                ft.Row(
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Icon(icon, size=14, color=ACCENT),
                        ft.Text(title, color=TEXT, size=11, weight=ft.FontWeight.W_800),
                    ],
                ),
                *controls,
            ],
        ),
    )


def main(page: ft.Page) -> None:
    page.title = APP_TITLE
    page.bgcolor = BG
    page.padding = 0
    page.spacing = 0
    page.theme_mode = ft.ThemeMode.DARK
    page.window.bgcolor = BG
    page.window.title_bar_hidden = True
    page.window.title_bar_buttons_hidden = True
    page.window.resizable = False
    page.window.width = 500
    page.window.height = 650
    page.window.min_width = 500
    page.window.max_width = 500
    page.window.min_height = 650
    page.window.max_height = 650

    cfg = config.load()

    known_language_codes = {value for _, value in LANGUAGE_OPTIONS if value != "custom"}
    stored_language = (cfg.get("language", "") or "").strip().lower()
    initial_language_label = _dropdown_value(LANGUAGE_OPTIONS, stored_language, "Auto-detect")
    custom_language_initial = "" if stored_language in {"", *known_language_codes} else stored_language

    theme_switch = ft.Switch(value=(cfg.get("theme", "dark") == "dark"), active_color=ACCENT_ALT)
    always_on_top_switch = ft.Switch(value=bool(cfg.get("always_on_top", True)), active_color=ACCENT_ALT)
    auto_minimize_switch = ft.Switch(value=bool(cfg.get("auto_minimize", True)), active_color=ACCENT_ALT)

    language_field = ft.Dropdown(
        label="Language",
        value=initial_language_label,
        options=_dropdown_options(LANGUAGE_OPTIONS),
        **_field_style(),
    )
    custom_language_field = ft.TextField(
        label="Custom language code",
        value=custom_language_initial,
        hint_text="e.g. en, ur, ar",
        **_field_style(),
    )
    custom_language_row = ft.Container(
        visible=initial_language_label == "Custom...",
        content=custom_language_field,
    )

    delay_text = ft.Text(
        f"{int(cfg.get('auto_type_delay', 3))} s",
        color=ACCENT,
        size=10,
        weight=ft.FontWeight.W_700,
    )
    delay_slider = ft.Slider(
        min=1,
        max=10,
        divisions=9,
        value=float(cfg.get("auto_type_delay", 3)),
        active_color=ACCENT_ALT,
        inactive_color=BORDER,
    )

    minimize_timeout_value = int(cfg.get("minimize_timeout", 10))
    minimize_timeout_value = max(5, min(120, minimize_timeout_value))
    minimize_timeout_text = ft.Text(
        f"{minimize_timeout_value} s",
        color=ACCENT,
        size=10,
        weight=ft.FontWeight.W_700,
    )
    minimize_timeout_slider = ft.Slider(
        min=5,
        max=120,
        divisions=23,
        value=float(minimize_timeout_value),
        active_color=ACCENT_ALT,
        inactive_color=BORDER,
    )

    mode_field = ft.Dropdown(
        label="Mode",
        value=cfg.get("mode", "Live"),
        options=[ft.dropdown.Option("Batch"), ft.dropdown.Option("Live")],
        **_field_style(),
    )
    source_field = ft.Dropdown(
        label="Source",
        value=cfg.get("source", "mic").capitalize(),
        options=[ft.dropdown.Option("Mic"), ft.dropdown.Option("System")],
        **_field_style(),
    )
    model_field = ft.Dropdown(
        label="Model",
        value=_dropdown_value(MODEL_OPTIONS, cfg.get("model", "voxtral-mini-2507"), "Mini"),
        options=_dropdown_options(MODEL_OPTIONS),
        **_field_style(),
    )
    model_hint_text = ft.Text("", color=MUTED_SOFT, size=9)

    live_retry_limit_field = ft.TextField(
        label="Live retry limit",
        value=str(int(cfg.get("live_retry_limit", 2))),
        hint_text="0 - 10",
        **_field_style(),
    )
    voice_commands_switch = ft.Switch(value=bool(cfg.get("voice_commands_enabled", True)), active_color=ACCENT_ALT)
    auto_fallback_switch = ft.Switch(value=bool(cfg.get("auto_fallback_enabled", True)), active_color=ACCENT_ALT)
    silence_trim_switch = ft.Switch(value=bool(cfg.get("silence_trim_enabled", True)), active_color=ACCENT_ALT)
    command_prefix_field = ft.TextField(
        label="Command prefix",
        value=(cfg.get("command_prefix", "command") or "command").strip(),
        hint_text="e.g. command",
        **_field_style(),
    )

    personal_dictionary_field = ft.TextField(
        label="Personal dictionary",
        value=_list_to_multiline(cfg.get("personal_dictionary", [])),
        hint_text="One term per line",
        multiline=True,
        min_lines=3,
        max_lines=5,
        **_field_style(),
    )
    text_replacements_field = ft.TextField(
        label="Text replacements",
        value=_dict_to_multiline(cfg.get("text_replacements", {})),
        hint_text="abbr => expanded text",
        multiline=True,
        min_lines=3,
        max_lines=5,
        **_field_style(),
    )

    reliability_events_switch = ft.Switch(value=bool(cfg.get("send_reliability_events", False)), active_color=ACCENT_ALT)
    check_updates_switch = ft.Switch(value=bool(cfg.get("check_for_updates", True)), active_color=ACCENT_ALT)
    status_text = ft.Text("Ready to save", color=MUTED, size=10)
    update_status_text = ft.Text(f"Current version: v{app_info.APP_VERSION}", size=10, color=MUTED)
    latest_update_text = ft.Text("", size=10, color=TEXT, weight=ft.FontWeight.W_600)
    update_note_text = ft.Text("", size=9, color=MUTED)
    update_link_button = ft.TextButton("Download update")
    ignore_update_button = ft.TextButton("Ignore this version")
    check_now_button = ft.OutlinedButton(
        "Check now",
        icon=ft.Icons.UPDATE,
        style=ft.ButtonStyle(
            color=TEXT,
            side=ft.BorderSide(1, BORDER),
            shape=ft.RoundedRectangleBorder(radius=10),
        ),
    )

    _update_check_in_flight = False
    _latest_update_info: website_client.UpdateInfo | None = None

    update_notice = ft.Container(
        visible=False,
        padding=ft.Padding(10, 10, 10, 10),
        border_radius=12,
        bgcolor="#173359",
        border=ft.Border.all(1, ACCENT),
        content=ft.Column(
            spacing=6,
            controls=[
                latest_update_text,
                update_note_text,
                ft.Row(spacing=8, controls=[update_link_button, ignore_update_button]),
            ],
        ),
    )

    timeout_row = ft.Container(
        visible=bool(auto_minimize_switch.value),
        content=ft.Column(
            spacing=6,
            controls=[
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    controls=[
                        ft.Text("Idle timeout", color=MUTED, size=10, weight=ft.FontWeight.W_700),
                        minimize_timeout_text,
                    ],
                ),
                minimize_timeout_slider,
            ],
        ),
    )

    api_source_note = ft.Container(
        padding=ft.Padding(10, 10, 10, 10),
        border_radius=10,
        bgcolor=CARD_SOFT,
        border=ft.Border.all(1, BORDER),
        content=ft.Text(
            "API key is managed on the website and checked automatically when you press Start.",
            size=10,
            color=MUTED,
        ),
    )

    async def _close_window_async() -> None:
        try:
            await page.window.close()
        except Exception:
            try:
                page.window.destroy()
            except Exception:
                pass

    def _close_window(_event=None) -> None:
        page.run_task(_close_window_async)

    def _sync_model_by_mode() -> None:
        selected_mode = (mode_field.value or "Batch").strip().lower()
        is_live_mode = selected_mode == "live"
        allowed_options = MODEL_OPTIONS if is_live_mode else BATCH_MODEL_OPTIONS
        allowed_labels = [label for label, _ in allowed_options]
        if model_field.value not in allowed_labels:
            model_field.value = allowed_labels[0]
        model_field.options = _dropdown_options(allowed_options)
        model_hint_text.value = "Live supports Mini and Small." if is_live_mode else "Batch supports Mini only."
        model_field.update()
        model_hint_text.update()

    def on_mode_change(_event: ft.ControlEvent) -> None:
        _sync_model_by_mode()

    def on_language_change(_event: ft.ControlEvent) -> None:
        custom_language_row.visible = language_field.value == "Custom..."
        custom_language_row.update()

    def on_delay_change(_event: ft.ControlEvent) -> None:
        delay_text.value = f"{int(delay_slider.value)} s"
        delay_text.update()

    def on_timeout_change(_event: ft.ControlEvent) -> None:
        minimize_timeout_text.value = f"{int(minimize_timeout_slider.value)} s"
        minimize_timeout_text.update()

    def on_auto_minimize_toggle(_event: ft.ControlEvent) -> None:
        timeout_row.visible = bool(auto_minimize_switch.value)
        timeout_row.update()

    def on_updates_toggle(_event: ft.ControlEvent) -> None:
        if not check_updates_switch.value:
            update_notice.visible = False
            update_status_text.value = f"Current version: v{app_info.APP_VERSION} - Disabled"
            update_status_text.color = MUTED
            page.update()
            return
        _check_updates(manual=False)

    def on_save(_event: ft.ControlEvent) -> None:
        selected_language = language_field.value or "Auto-detect"
        language_code = next((value for label, value in LANGUAGE_OPTIONS if label == selected_language), "")
        if selected_language == "Custom...":
            language_code = (custom_language_field.value or "").strip().lower()

        is_live_mode = (mode_field.value or "Batch").strip().lower() == "live"
        allowed_options = MODEL_OPTIONS if is_live_mode else BATCH_MODEL_OPTIONS
        model_value = next((value for label, value in allowed_options if label == model_field.value), allowed_options[0][1])
        source_value = (source_field.value or "Mic").lower()

        latest_cfg = config.load()
        latest_cfg.pop("api_key", None)
        latest_cfg["model"] = model_value
        latest_cfg["language"] = language_code
        latest_cfg["auto_type_delay"] = int(delay_slider.value or 3)
        latest_cfg["mode"] = "Live" if is_live_mode else "Batch"
        latest_cfg["source"] = source_value
        latest_cfg["always_on_top"] = bool(always_on_top_switch.value)
        latest_cfg["check_for_updates"] = bool(check_updates_switch.value)
        latest_cfg["runtime_channel"] = app_info.APP_CHANNEL
        latest_cfg["theme"] = "dark" if theme_switch.value else "light"
        latest_cfg["auto_minimize"] = bool(auto_minimize_switch.value)
        latest_cfg["minimize_timeout"] = int(minimize_timeout_slider.value or 10)

        try:
            retry_limit = int((live_retry_limit_field.value or "2").strip())
        except ValueError:
            retry_limit = 2
        latest_cfg["live_retry_limit"] = max(0, min(10, retry_limit))
        latest_cfg["voice_commands_enabled"] = bool(voice_commands_switch.value)
        latest_cfg["command_prefix"] = (command_prefix_field.value or "command").strip().lower() or "command"
        latest_cfg["auto_fallback_enabled"] = bool(auto_fallback_switch.value)
        latest_cfg["silence_trim_enabled"] = bool(silence_trim_switch.value)
        latest_cfg["send_reliability_events"] = bool(reliability_events_switch.value)
        latest_cfg["personal_dictionary"] = _parse_multiline_list(personal_dictionary_field.value or "")
        latest_cfg["text_replacements"] = _parse_replacements(text_replacements_field.value or "")
        config.save(latest_cfg)

        status_text.value = "Saved"
        status_text.color = SUCCESS
        page.snack_bar = ft.SnackBar(
            content=ft.Text("Settings saved", color=TEXT),
            bgcolor=CARD_SOFT,
            open=True,
        )
        page.update()

    def _open_update_download(_event: ft.ControlEvent) -> None:
        nonlocal _latest_update_info
        if not _latest_update_info or not _latest_update_info.download_url:
            return
        page.launch_url(_latest_update_info.download_url)

    def _ignore_update_version(_event: ft.ControlEvent) -> None:
        nonlocal _latest_update_info
        if not _latest_update_info:
            return
        latest_cfg = config.load()
        latest_cfg["ignored_update_version"] = _latest_update_info.latest_version
        config.save(latest_cfg)
        update_notice.visible = False
        status_text.value = f"Ignored v{_latest_update_info.latest_version}"
        status_text.color = MUTED
        page.update()

    def _finish_update_check(
        info: website_client.UpdateInfo,
        manual: bool,
        ignored_version: str,
        updates_enabled: bool,
    ) -> None:
        nonlocal _update_check_in_flight, _latest_update_info
        _update_check_in_flight = False
        check_now_button.disabled = False

        is_ignored = ignored_version == info.latest_version and not info.mandatory
        has_update = info.update_available and bool(info.download_url) and not is_ignored

        if not updates_enabled and not manual:
            update_notice.visible = False
            update_status_text.value = f"Current version: v{app_info.APP_VERSION}"
            page.update()
            return

        if has_update:
            _latest_update_info = info
            latest_update_text.value = f"New version v{info.latest_version} is available."
            update_note_text.value = info.notes or "Performance and reliability improvements are ready."
            update_notice.visible = True
            update_status_text.value = f"Current version: v{app_info.APP_VERSION} - Update ready"
            update_status_text.color = SUCCESS
        else:
            update_notice.visible = False
            update_status_text.value = f"Current version: v{app_info.APP_VERSION} - Up to date"
            update_status_text.color = MUTED
            if manual:
                page.snack_bar = ft.SnackBar(
                    content=ft.Text("You are already on the latest version.", color=TEXT),
                    bgcolor=CARD_SOFT,
                    open=True,
                )
        page.update()

    def _fail_update_check(message: str, manual: bool) -> None:
        nonlocal _update_check_in_flight
        _update_check_in_flight = False
        check_now_button.disabled = False
        update_status_text.value = f"Current version: v{app_info.APP_VERSION} - Update check unavailable"
        update_status_text.color = MUTED
        if manual:
            page.snack_bar = ft.SnackBar(
                content=ft.Text(message, color=TEXT),
                bgcolor=CARD_SOFT,
                open=True,
            )
        page.update()

    def _check_updates(manual: bool = False) -> None:
        nonlocal _update_check_in_flight
        if _update_check_in_flight:
            return
        _update_check_in_flight = True
        check_now_button.disabled = True
        update_status_text.value = f"Current version: v{app_info.APP_VERSION} - Checking..."
        update_status_text.color = MUTED
        page.update()

        def _worker() -> None:
            updates_enabled = bool(check_updates_switch.value)
            ignored_version = (config.load().get("ignored_update_version") or "").strip()
            if not updates_enabled and not manual:
                fallback = website_client.UpdateInfo(False, app_info.APP_VERSION, "", "", False, "")
                page.run_thread(_finish_update_check, fallback, manual, ignored_version, updates_enabled)
                return
            try:
                info = website_client.get_update_info(
                    current_version=app_info.APP_VERSION,
                    platform=app_info.APP_PLATFORM,
                    channel=app_info.APP_CHANNEL,
                )
                page.run_thread(_finish_update_check, info, manual, ignored_version, updates_enabled)
            except Exception as exc:
                page.run_thread(_fail_update_check, str(exc), manual)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_check_now(_event: ft.ControlEvent) -> None:
        _check_updates(manual=True)

    update_link_button.on_click = _open_update_download
    ignore_update_button.on_click = _ignore_update_version
    check_now_button.on_click = _on_check_now

    language_field.on_change = on_language_change
    delay_slider.on_change = on_delay_change
    minimize_timeout_slider.on_change = on_timeout_change
    auto_minimize_switch.on_change = on_auto_minimize_toggle
    check_updates_switch.on_change = on_updates_toggle
    mode_field.on_change = on_mode_change

    general_content = ft.Column(
        spacing=10,
        controls=[
            _card(
                "Appearance",
                ft.Icons.PALETTE_OUTLINED,
                [
                    _setting_row("Dark theme", theme_switch),
                ],
            ),
            _card(
                "Language & Typing",
                ft.Icons.LANGUAGE,
                [
                    language_field,
                    custom_language_row,
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            ft.Text("Auto-type delay", color=MUTED, size=10, weight=ft.FontWeight.W_700),
                            delay_text,
                        ],
                    ),
                    delay_slider,
                ],
            ),
            _card(
                "Smart Behavior",
                ft.Icons.AUTO_AWESOME,
                [
                    _setting_row("Auto-minimize to mic", auto_minimize_switch),
                    timeout_row,
                ],
            ),
        ],
    )

    audio_content = ft.Column(
        spacing=10,
        controls=[
            _card(
                "Mode & Source",
                ft.Icons.MIC,
                [
                    ft.Row(spacing=10, controls=[ft.Container(expand=True, content=mode_field), ft.Container(expand=True, content=source_field)]),
                    live_retry_limit_field,
                    _setting_row("Voice commands", voice_commands_switch),
                    command_prefix_field,
                    _setting_row("Auto fallback", auto_fallback_switch),
                    _setting_row("Adaptive trim", silence_trim_switch),
                    _setting_row("Always on top", always_on_top_switch),
                ],
            ),
        ],
    )

    text_content = ft.Column(
        spacing=10,
        controls=[
            _card(
                "Text Tools",
                ft.Icons.AUTO_FIX_HIGH,
                [
                    personal_dictionary_field,
                    text_replacements_field,
                ],
            ),
        ],
    )

    system_content = ft.Column(
        spacing=10,
        controls=[
            _card(
                "Runtime",
                ft.Icons.MEMORY_OUTLINED,
                [
                    api_source_note,
                    model_field,
                    model_hint_text,
                ],
            ),
            _card(
                "Updates & Telemetry",
                ft.Icons.UPDATE,
                [
                    _setting_row("Check for updates", check_updates_switch),
                    _setting_row("Send reliability events", reliability_events_switch),
                    update_status_text,
                    check_now_button,
                    update_notice,
                ],
            ),
        ],
    )

    about_content = ft.Column(
        spacing=10,
        controls=[
            _card(
                "About SONUS",
                ft.Icons.INFO_OUTLINE,
                [
                    ft.Container(
                        padding=ft.Padding(12, 12, 12, 12),
                        border_radius=10,
                        bgcolor=CARD_SOFT,
                        border=ft.Border.all(1, BORDER),
                        content=ft.Column(
                            spacing=6,
                            controls=[
                                ft.Row(
                                    spacing=8,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    controls=[
                                        ft.Icon(ft.Icons.MIC, size=22, color=ACCENT),
                                        ft.Text("SONUS", size=16, color=TEXT, weight=ft.FontWeight.W_900),
                                    ],
                                ),
                                ft.Text("Speech to Text Engine", size=10, color=MUTED, weight=ft.FontWeight.W_700),
                                ft.Text(f"Version {app_info.APP_VERSION}", size=10, color=MUTED_SOFT),
                            ],
                        ),
                    ),
                    ft.OutlinedButton(
                        "Documentation",
                        icon=ft.Icons.OPEN_IN_NEW,
                        on_click=lambda _e: page.launch_url("https://brevios.com"),
                        style=ft.ButtonStyle(
                            color=TEXT,
                            side=ft.BorderSide(1, BORDER),
                            shape=ft.RoundedRectangleBorder(radius=10),
                        ),
                    ),
                    ft.OutlinedButton(
                        "Privacy Policy",
                        icon=ft.Icons.OPEN_IN_NEW,
                        on_click=lambda _e: page.launch_url("http://127.0.0.1:5050/privacy-policy"),
                        style=ft.ButtonStyle(
                            color=TEXT,
                            side=ft.BorderSide(1, BORDER),
                            shape=ft.RoundedRectangleBorder(radius=10),
                        ),
                    ),
                    ft.OutlinedButton(
                        "Terms of Service",
                        icon=ft.Icons.OPEN_IN_NEW,
                        on_click=lambda _e: page.launch_url("http://127.0.0.1:5050/terms-and-conditions"),
                        style=ft.ButtonStyle(
                            color=TEXT,
                            side=ft.BorderSide(1, BORDER),
                            shape=ft.RoundedRectangleBorder(radius=10),
                        ),
                    ),
                ],
            ),
        ],
    )

    tab_containers: dict[str, ft.Container] = {
        "general": ft.Container(visible=True, content=general_content),
        "audio": ft.Container(visible=False, content=audio_content),
        "text": ft.Container(visible=False, content=text_content),
        "system": ft.Container(visible=False, content=system_content),
        "about": ft.Container(visible=False, content=about_content),
    }

    tab_buttons: dict[str, dict[str, ft.Control]] = {}

    def _activate_tab(tab_id: str) -> None:
        for item_id, refs in tab_buttons.items():
            is_active = item_id == tab_id
            refs["container"].bgcolor = CARD_SOFT if is_active else ft.Colors.TRANSPARENT
            refs["icon"].color = ACCENT if is_active else MUTED_SOFT
            refs["label"].color = TEXT if is_active else MUTED_SOFT
            tab_containers[item_id].visible = is_active
        page.update()

    segmented_buttons: list[ft.Control] = []
    for tab_id, label, icon_name in TABS:
        icon = ft.Icon(icon_name, size=14, color=MUTED_SOFT)
        text = ft.Text(label, size=9, weight=ft.FontWeight.W_700, color=MUTED_SOFT)
        button = ft.Container(
            expand=True,
            border_radius=10,
            padding=ft.Padding(6, 8, 6, 8),
            alignment=ft.Alignment(0, 0),
            on_click=lambda _e, tid=tab_id: _activate_tab(tid),
            content=ft.Column(
                spacing=3,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[icon, text],
            ),
        )
        tab_buttons[tab_id] = {"container": button, "icon": icon, "label": text}
        segmented_buttons.append(button)

    _activate_tab("general")

    save_button = ft.FilledButton(
        content=ft.Row(
            tight=True,
            spacing=6,
            controls=[
                ft.Icon(ft.Icons.SAVE, size=14, color=TEXT),
                ft.Text("Save Changes", weight=ft.FontWeight.W_700),
            ],
        ),
        on_click=on_save,
        style=ft.ButtonStyle(
            color=TEXT,
            bgcolor=ACCENT_ALT,
            overlay_color=ACCENT,
            shape=ft.RoundedRectangleBorder(radius=10),
            padding=ft.Padding(14, 10, 14, 10),
        ),
    )

    root = ft.Container(
        expand=True,
        bgcolor=BG,
        padding=ft.Padding(10, 10, 10, 10),
        content=ft.Column(
            spacing=10,
            controls=[
                ft.Container(
                    padding=ft.Padding(12, 12, 12, 12),
                    border_radius=14,
                    bgcolor=SURFACE,
                    border=ft.Border.all(1, BORDER),
                    content=ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Column(
                                spacing=2,
                                controls=[
                                    ft.Text("SONUS", size=16, weight=ft.FontWeight.W_900, color=TEXT),
                                    ft.Text("Command Center", size=9, color=MUTED, weight=ft.FontWeight.W_700),
                                ],
                            ),
                            ft.IconButton(
                                icon=ft.Icons.CLOSE,
                                icon_size=16,
                                icon_color=MUTED,
                                tooltip="Close",
                                on_click=_close_window,
                            ),
                        ],
                    ),
                ),
                ft.Container(
                    padding=ft.Padding(4, 4, 4, 4),
                    border_radius=14,
                    bgcolor=CARD,
                    border=ft.Border.all(1, BORDER),
                    content=ft.Row(spacing=4, controls=segmented_buttons),
                ),
                ft.Container(
                    expand=True,
                    padding=ft.Padding(0, 2, 0, 2),
                    content=ft.Column(
                        scroll=ft.ScrollMode.AUTO,
                        spacing=10,
                        controls=[
                            tab_containers["general"],
                            tab_containers["audio"],
                            tab_containers["text"],
                            tab_containers["system"],
                            tab_containers["about"],
                        ],
                    ),
                ),
                ft.Container(
                    padding=ft.Padding(12, 12, 12, 12),
                    border_radius=14,
                    bgcolor=SURFACE,
                    border=ft.Border.all(1, BORDER),
                    content=ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[status_text, save_button],
                    ),
                ),
            ],
        ),
    )

    page.add(root)
    page.update()
    _sync_model_by_mode()
    _check_updates(manual=False)


if __name__ == "__main__":
    ft.run(main, view=ft.AppView.FLET_APP)
