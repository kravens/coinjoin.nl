mod theme;

use slint_keyos_platform::app_ui;

app_ui!("Coinjoin Signer");

fn app_main(_cx: AppContext, ui: AppWindow) {
    log_server::init_wait(env!("CARGO_CRATE_NAME")).unwrap();
    log::set_max_level(log::LevelFilter::Info);

    theme::init(&ui);

    ui.run().expect("UI running");
}
