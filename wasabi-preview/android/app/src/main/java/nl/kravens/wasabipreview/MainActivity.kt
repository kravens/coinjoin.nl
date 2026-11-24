package nl.kravens.wasabipreview

import android.content.Context
import android.content.SharedPreferences
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.mutableStateOf

class MainActivity : ComponentActivity() {
    private val theme = mutableStateOf(AppTheme.WASABI)
    private lateinit var mirror: Mirror
    private val listener = SharedPreferences.OnSharedPreferenceChangeListener { prefs, key ->
        if (key == "theme") theme.value = themeOf(prefs)
        if (key == "decoy") hidePreview(prefs.getBoolean("decoy", false))
    }

    // Decoy: the app switcher shows no preview, so the real wallet never shows there either.
    private fun hidePreview(hide: Boolean) { if (android.os.Build.VERSION.SDK_INT >= 33) setRecentsScreenshotEnabled(!hide) }

    private fun themeOf(prefs: SharedPreferences) = if (prefs.getString("theme", "wasabi") == "black") AppTheme.BLACK else AppTheme.WASABI

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val prefs = getSharedPreferences("app", Context.MODE_PRIVATE)
        theme.value = themeOf(prefs)
        prefs.registerOnSharedPreferenceChangeListener(listener)
        hidePreview(prefs.getBoolean("decoy", false))
        // Debug runs: `adb shell am start -n nl.kravens.wasabipreview/.MainActivity --ez demo true --es screen sendReview`
        // opens that screen on the demo wallet, the same hooks as the iOS simulator's WASABI_DEMO and WASABI_SCREEN.
        val demo = BuildConfig.DEBUG && intent.getBooleanExtra("demo", false)
        // Emulator runs against a real Wasabi, read-only: `--es rpcPassword ... [--es rpcHost ...] [--es rpcUser ...]
        // [--es onionKey <onion>:descriptor:x25519:<key>]`. Nothing is stored, and the "live" screen keeps autoCoinjoin off.
        val live = if (BuildConfig.DEBUG) intent.getStringExtra("rpcPassword") else null
        val screen = if (BuildConfig.DEBUG) intent.getStringExtra("screen") ?: live?.let { "live" } else null
        if (BuildConfig.DEBUG && intent.hasExtra("discreet")) prefs.edit().putBoolean("discreet", intent.getBooleanExtra("discreet", false)).apply()
        mirror = Mirror(applicationContext, screen, demo)
        if (live != null) {
            val key = intent.getStringExtra("onionKey")
            mirror.saveServer(Server(
                if (key != null) Transport.ONION else Transport.TAILSCALE, intent.getStringExtra("rpcHost") ?: "10.0.2.2", user = intent.getStringExtra("rpcUser") ?: "preview",
                password = live, onion = key?.substringBefore(':')?.plus(".onion") ?: "", onionKey = key ?: "",
            ), persist = false)
        }
        // A bitcoin: link from another app opens Send, pre-filled; anything unpayable just opens the app.
        val link = intent.data?.toString()?.let { runCatching { Bitcoin.parse(it) }.getOrNull() }
        setContent { WasabiTheme(theme.value) { App(mirror, theme.value, link) } }
    }

    // Decoy: the demo wallet is back before anyone sees the app again.
    override fun onStop() {
        super.onStop()
        if (getSharedPreferences("app", Context.MODE_PRIVATE).getBoolean("decoy", false)) mirror.relock()
    }

    override fun onDestroy() {
        getSharedPreferences("app", Context.MODE_PRIVATE).unregisterOnSharedPreferenceChangeListener(listener)
        super.onDestroy()
    }
}
