//! Resident-mode system tray, using the official Tauri 2 tray APIs.
//!
//! The tray is created once in `setup` and never touches privileged
//! surfaces: it is a pure desktop-integration surface (show/hide/quit) for
//! the same user process that already runs the window. If the tray cannot
//! be created on the current desktop (some minimal sessions have no
//! StatusNotifier host), `init` reports that plainly and the application
//! deliberately falls back to its normal close behaviour, so a user can
//! never end up with a hidden window and no way back to it.
use tauri::{
    menu::{MenuBuilder, MenuItemBuilder},
    AppHandle, Manager,
};

/// Whether the tray icon is really available for this session. Only when
/// this is true does the close button become "hide to tray" instead of
/// "quit" (see the `CloseRequested` handling in `lib.rs`).
pub struct TrayState {
    pub available: bool,
}

/// Brings the main window back on screen: used by the tray menu, by a
/// second launch attempt (single-instance) and by the first visible start.
pub fn show_main_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

fn hide_main_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.hide();
    }
}

/// Builds the single tray icon with its minimal menu. Returns `false`
/// (rather than propagating an error) when the desktop cannot host a tray,
/// so the caller can choose the safe close-to-quit fallback.
pub fn init(app: &AppHandle) -> bool {
    let build = || -> tauri::Result<()> {
        let title = MenuItemBuilder::with_id("mg-title", "M.G Linux Toolbox")
            .enabled(false)
            .build(app)?;
        let show = MenuItemBuilder::with_id("mg-show", "Apri M.G").build(app)?;
        let hide = MenuItemBuilder::with_id("mg-hide", "Nascondi M.G").build(app)?;
        let quit = MenuItemBuilder::with_id("mg-quit", "Esci").build(app)?;
        let menu = MenuBuilder::new(app)
            .item(&title)
            .item(&show)
            .item(&hide)
            .separator()
            .item(&quit)
            .build()?;
        let mut builder = tauri::tray::TrayIconBuilder::with_id("mg-tray")
            .menu(&menu)
            .tooltip("M.G Linux Toolbox")
            .on_menu_event(|app, event| handle_menu_event(app, event.id().as_ref()));
        // The official application icon, exactly as bundled: never a new
        // graphic, and no extra asset is introduced for the tray.
        if let Some(icon) = app.default_window_icon() {
            builder = builder.icon(icon.clone());
        }
        builder.build(app)?;
        Ok(())
    };
    match build() {
        Ok(()) => true,
        Err(error) => {
            eprintln!("M.G Linux Toolbox: tray unavailable on this desktop ({error})");
            false
        }
    }
}

pub fn handle_menu_event(app: &AppHandle, event_id: &str) {
    match event_id {
        "mg-show" => show_main_window(app),
        "mg-hide" => hide_main_window(app),
        // A real quit: the tray icon belongs to this process, so ending it
        // removes the icon as well.
        "mg-quit" => app.exit(0),
        _ => {}
    }
}
