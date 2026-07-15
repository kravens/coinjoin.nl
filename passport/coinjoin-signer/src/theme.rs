slint_keyos_platform::settings::use_api!(
    slint_keyos_platform::settings,
    slint_keyos_platform::server
);

use foundation_themes::{self, ColorScheme, ComponentState, ExportTheme, ThemeColor, TokenValue};
use slint_keyos_platform::futures_lite::StreamExt;
use slint_keyos_platform::slint::ComponentHandle;

include!(concat!(env!("OUT_DIR"), "/app_theme.rs"));

pub fn init(ui: &crate::AppWindow) {
    apply_with_system_theme(ui, SettingsApi::default().get_system_theme());

    let ui_weak = ui.as_weak();
    let mut updates = slint_keyos_platform::subscribe_scalar::<settings_permissions::SettingsPermissions, _>(
        settings_permissions::settings::messages::SubscribeSystemTheme,
    );
    slint_keyos_platform::spawn_local(async move {
        while let Some(system_theme) = updates.next().await {
            let Some(ui) = ui_weak.upgrade() else {
                break;
            };
            apply_with_system_theme(&ui, system_theme);
        }
    })
    .detach();
}

fn apply_with_system_theme(
    ui: &crate::AppWindow,
    _system_theme: settings_permissions::settings::global::SystemTheme,
) {
    let theme = app_theme();
    apply_theme(ui, &theme, ColorScheme::Light);
}

fn apply_theme(ui: &crate::AppWindow, theme: &ExportTheme, scheme: ColorScheme) {
    let theme_global = ui.global::<crate::Theme>();

    theme_global.set_is_dark(matches!(scheme, ColorScheme::Dark));
    theme_global.set_palette_primary(token_color(
        theme,
        scheme,
        "color",
        "primary",
        slint_keyos_platform::slint::Color::from_rgb_u8(0, 157, 185),
    ));
    theme_global.set_palette_primary_pressed(token_color(
        theme,
        scheme,
        "color",
        "primary.dark",
        slint_keyos_platform::slint::Color::from_rgb_u8(0, 111, 131),
    ));
    theme_global.set_palette_secondary(token_color(
        theme,
        scheme,
        "color",
        "secondary",
        slint_keyos_platform::slint::Color::from_rgb_u8(213, 212, 213),
    ));
    theme_global.set_palette_secondary_pressed(token_color(
        theme,
        scheme,
        "color",
        "secondary.dark",
        slint_keyos_platform::slint::Color::from_rgb_u8(227, 226, 226),
    ));
    theme_global.set_palette_danger(token_color(
        theme,
        scheme,
        "color",
        "danger",
        slint_keyos_platform::slint::Color::from_rgb_u8(255, 51, 51),
    ));
    theme_global.set_palette_surface(token_color(
        theme,
        scheme,
        "color",
        "surface",
        slint_keyos_platform::slint::Color::from_rgb_u8(255, 255, 255),
    ));
    theme_global.set_palette_background(token_color(
        theme,
        scheme,
        "color",
        "background",
        slint_keyos_platform::slint::Color::from_rgb_u8(255, 255, 255),
    ));
    theme_global.set_palette_foreground(token_color(
        theme,
        scheme,
        "color",
        "foreground",
        slint_keyos_platform::slint::Color::from_rgb_u8(35, 31, 32),
    ));
    theme_global.set_palette_muted(token_color(
        theme,
        scheme,
        "color",
        "muted",
        slint_keyos_platform::slint::Color::from_rgb_u8(149, 147, 148),
    ));
    theme_global.set_palette_border(token_color(
        theme,
        scheme,
        "color",
        "border",
        slint_keyos_platform::slint::Color::from_rgb_u8(213, 212, 213),
    ));

    theme_global.set_primary_normal(button_style(theme, scheme, "primary", ComponentState::Default));
    theme_global.set_primary_focused(button_style(theme, scheme, "primary", ComponentState::Focused));
    theme_global.set_primary_loading(button_style(theme, scheme, "primary", ComponentState::Loading));
    theme_global.set_primary_pressed(button_style(theme, scheme, "primary", ComponentState::Pressed));
    theme_global.set_primary_disabled(button_style(theme, scheme, "primary", ComponentState::Disabled));

    theme_global.set_secondary_normal(button_style(theme, scheme, "secondary", ComponentState::Default));
    theme_global.set_secondary_focused(button_style(theme, scheme, "secondary", ComponentState::Focused));
    theme_global.set_secondary_loading(button_style(theme, scheme, "secondary", ComponentState::Loading));
    theme_global.set_secondary_pressed(button_style(theme, scheme, "secondary", ComponentState::Pressed));
    theme_global.set_secondary_disabled(button_style(theme, scheme, "secondary", ComponentState::Disabled));

    theme_global.set_tertiary_normal(button_style(theme, scheme, "tertiary", ComponentState::Default));
    theme_global.set_tertiary_focused(button_style(theme, scheme, "tertiary", ComponentState::Focused));
    theme_global.set_tertiary_loading(button_style(theme, scheme, "tertiary", ComponentState::Loading));
    theme_global.set_tertiary_pressed(button_style(theme, scheme, "tertiary", ComponentState::Pressed));
    theme_global.set_tertiary_disabled(button_style(theme, scheme, "tertiary", ComponentState::Disabled));

    theme_global.set_ghost_normal(button_style(theme, scheme, "ghost", ComponentState::Default));
    theme_global.set_ghost_focused(button_style(theme, scheme, "ghost", ComponentState::Focused));
    theme_global.set_ghost_loading(button_style(theme, scheme, "ghost", ComponentState::Loading));
    theme_global.set_ghost_pressed(button_style(theme, scheme, "ghost", ComponentState::Pressed));
    theme_global.set_ghost_disabled(button_style(theme, scheme, "ghost", ComponentState::Disabled));

    theme_global.set_danger_normal(button_style(theme, scheme, "danger", ComponentState::Default));
    theme_global.set_danger_focused(button_style(theme, scheme, "danger", ComponentState::Focused));
    theme_global.set_danger_loading(button_style(theme, scheme, "danger", ComponentState::Loading));
    theme_global.set_danger_pressed(button_style(theme, scheme, "danger", ComponentState::Pressed));
    theme_global.set_danger_disabled(button_style(theme, scheme, "danger", ComponentState::Disabled));

    theme_global.set_size_sm(button_size(theme, scheme, "sm"));
    theme_global.set_size_md(button_size(theme, scheme, "md"));
    theme_global.set_size_lg(button_size(theme, scheme, "lg"));

    // Add app-specific theme overrides below.
}

fn button_style(
    theme: &ExportTheme,
    scheme: ColorScheme,
    variant: &str,
    state: ComponentState,
) -> crate::ButtonStyleProps {
    let style = theme.get_component_style("button", variant, None, state, scheme);
    crate::ButtonStyleProps {
        background: style.background.unwrap_or_default().to_slint(),
        foreground: style.foreground.unwrap_or_default().to_slint(),
        border_color: style.border_color.unwrap_or_default().to_slint(),
        border_width: style.border_width.unwrap_or(0.0),
        font_weight: style.font_weight.unwrap_or(500),
        opacity: style.opacity.unwrap_or(1.0),
        touch_expansion: style.touch_expansion.unwrap_or(4.0),
    }
}

fn button_size(theme: &ExportTheme, scheme: ColorScheme, size: &str) -> crate::ButtonSizeProps {
    let style = theme.get_component_style_by_state_key("button", "primary", Some(size), "default", scheme);
    let (fallback_font_size, fallback_padding_h, fallback_padding_v, fallback_icon_size, fallback_min_height) =
        match size {
            "sm" => (20.0, 20.0, 10.0, 20.0, 52.0),
            "lg" => (26.0, 32.0, 14.0, 28.0, 68.0),
            _ => (24.0, 24.0, 12.0, 24.0, 60.0),
        };
    crate::ButtonSizeProps {
        font_family: style.font_family.unwrap_or_else(|| "Montserrat".to_string()).into(),
        font_size: style.font_size.unwrap_or(fallback_font_size),
        padding_h: style.padding_horizontal.unwrap_or(fallback_padding_h),
        padding_v: style.padding_vertical.unwrap_or(fallback_padding_v),
        icon_size: style.icon_size.unwrap_or(fallback_icon_size),
        min_height: style.min_height.unwrap_or(fallback_min_height),
        border_radius: style.border_radius.unwrap_or(24.0),
    }
}

fn token_color(
    theme: &ExportTheme,
    scheme: ColorScheme,
    category: &str,
    key: &str,
    fallback: slint_keyos_platform::slint::Color,
) -> slint_keyos_platform::slint::Color {
    match foundation_themes::get_token(&theme.tokens, category, key, scheme) {
        Some(TokenValue::Color(color)) => color.to_slint(),
        Some(TokenValue::String(text)) => ThemeColor::from_hex(&text).map(|color| color.to_slint()).unwrap_or(fallback),
        _ => fallback,
    }
}
