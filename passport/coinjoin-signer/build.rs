use slint_keyos_platform_build::{compile_options, CompileOptions};

fn main() {
    let out_dir = std::env::var("OUT_DIR").expect("OUT_DIR is set by Cargo");
    foundation_themes::build::compile_app_theme_json(
        "theme/theme.json",
        std::path::Path::new(&out_dir).join("app_theme.rs"),
        "app_theme",
    )
    .expect("compile app theme JSON");

    compile_options(CompileOptions {
        module_path: "ui/app.slint",
        include_slint: true,
        include_router: true,
        include_translations: false,
        include_time_localization: false,
    });
}
