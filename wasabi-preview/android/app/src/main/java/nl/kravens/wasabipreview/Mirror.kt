package nl.kravens.wasabipreview

import android.content.Context
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.isActive
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.security.MessageDigest
import java.text.NumberFormat
import java.time.Instant
import java.util.Currency
import java.util.Locale

// Desktop Wasabi's three strategies (WalletCoinJoinSettingsView: Reduce Costs, Default Strategy, Enhance Privacy).
enum class PrivacyStop(val label: String) {
    CHEAP("Lower fees"), BALANCED("Balanced"), PRIVATE("More private");

    val key get() = when (this) { CHEAP -> "cheap"; BALANCED -> "balanced"; PRIVATE -> "private" }

    companion object {
        fun of(key: String?) = entries.firstOrNull { it.key == key }
    }
}

enum class Link { CONNECTING, CONNECTED, FAILED }

// A mutating call that needs the wallet passphrase; the typed passphrase only ever lives in the retry call.
class PassphraseRequest(val title: String, val action: suspend (String) -> String?)

// The phone's mirror of desktop Wasabi: what getstatus, the wallet calls and the user's choices say right now. Compose
// state, changed on the main thread only; RPC calls hop to IO inside WasabiRpc.
class Mirror(private val context: Context, demoScreen: String? = null, demo: Boolean = false) {
    private val prefs = context.getSharedPreferences("app", Context.MODE_PRIVATE)
    val demo = demo
    val screen = demoScreen

    var server by mutableStateOf(SecureStore.load(context) ?: Server())
        private set

    fun saveServer(new: Server, persist: Boolean = !demo) {
        if (persist) SecureStore.save(context, new)
        server = new
        failingSince = null; linkError = null; status = null; updated = null; walletLoaded = false
    }

    var wallets by mutableStateOf(listOf<String>())
    var wallet by mutableStateOf(prefs.getString("wallet", null))
        private set

    fun selectWallet(name: String?) {
        if (name == wallet) return
        wallet = name
        prefs.edit().putString("wallet", name).apply()
        loadPrivacyStop(); seenIncoming = null; coinjoinHint = null; allCoins = emptyList()
        // Never the old wallet's numbers under the new wallet's name.
        info = null; coins = emptyList(); history = emptyList(); payments = emptyList(); walletError = null; walletLoaded = false
    }

    // The wallet's privacy choice; the default is the desktop's Default Strategy.
    var privacyStop by mutableStateOf(PrivacyStop.BALANCED)
        private set

    fun choosePrivacy(stop: PrivacyStop) {
        privacyStop = stop
        if (screen == null) wallet?.let { prefs.edit().putString("privacy.$it", stop.key).apply() }
    }

    private fun loadPrivacyStop() {
        forcedStop()?.let { privacyStop = it; return }
        privacyStop = PrivacyStop.of(wallet?.let { prefs.getString("privacy.$it", null) }) ?: PrivacyStop.BALANCED
    }

    // A private payment on a Lower fees wallet starts coinjoin for that payment only; Wasabi itself would keep going until
    // every coin is private, so refresh stops it again once no payment waits. ponytail: only while the app refreshes; a
    // phone left closed lets rounds run until it opens again (or the wallet is private), a desktop-side stop if that bites.
    var stopAfterPayments: Boolean
        get() = wallet?.let { prefs.getBoolean("paymentStop.$it", false) } ?: false
        set(value) { wallet?.let { prefs.edit().putBoolean("paymentStop.$it", value).apply() } }

    var coinjoinHint by mutableStateOf<String?>(null) // why a coinjoin did not start by itself; shown quietly, never as a prompt
    var allCoins by mutableStateOf(listOf<Coin>()) // listcoins, fetched when a coin screen opens
    // Incoming txids already on screen, tagged with their wallet so a refresh that lands after a wallet switch can never
    // make the new wallet's history look "new" and start a coinjoin on it. Null until a wallet's first history.
    private var seenIncoming: Pair<String, Set<String>>? = null
    var discreet by mutableStateOf(prefs.getBoolean("discreet", false))
        private set

    fun toggleDiscreet() { discreet = !discreet; prefs.edit().putBoolean("discreet", discreet).apply() }

    var status by mutableStateOf<Status?>(null)
    var info by mutableStateOf<WalletInfo?>(null)
    var coins by mutableStateOf(listOf<Coin>())
    var history by mutableStateOf(listOf<HistoryItem>())
    var payments by mutableStateOf(listOf<CoinjoinPayment>())
    var fees by mutableStateOf(mapOf<String, Double>())
    var error by mutableStateOf<String?>(null)
    var walletError by mutableStateOf<String?>(null)
    var walletLoaded by mutableStateOf(false) // info, coins, history and payments belong to `wallet`
    var updated by mutableStateOf<Instant?>(null)
    var notice by mutableStateOf<String?>(null)
    var passphrase by mutableStateOf<PassphraseRequest?>(null)
    var busy by mutableStateOf(false)
    var torProgress by mutableStateOf<Int?>(null) // 0...100 while Arti bootstraps; null on Tailscale or once ready
    var linkError by mutableStateOf<String?>(null) // last link failure (Tor circuit, timeout); the home keeps its last data
    var refreshing by mutableStateOf(false)
    private var failingSince: Instant? = null

    val link: Link
        get() = when {
            linkError != null -> Link.FAILED
            torProgress != null || updated == null -> Link.CONNECTING
            else -> Link.CONNECTED
        }

    val rpc get() = WasabiRpc(server)
    val network get() = status?.let { Bitcoin.Network.wasabi(it.network) } // null until getstatus answered
    val target get() = info?.anonScoreTarget ?: 5
    val balance get() = info?.balance ?: coins.sumOf { it.amount }
    val walletNotLoaded get() = walletError?.contains("no wallet loaded") == true
    val ready get() = status != null && walletLoaded && error == null && walletError == null

    data class Split(val private: Long, val semi: Long, val nonPrivate: Long)

    // Wasabi's buckets (SmartCoinExtensions.cs): private at/above the anon target, semi-private from 2 (Constants.SemiPrivateThreshold).
    val privacy: Split
        get() {
            var p = 0L; var s = 0L; var n = 0L
            for (c in coins) if (c.anonymityScore >= target) p += c.amount else if (c.anonymityScore >= 2) s += c.amount else n += c.amount
            return Split(p, s, n)
        }

    val privacyPercentage get() = privacyPercentage(coins, target)

    // Plain words for the home tile; Wasabi's own state names stay on the Privacy screen.
    val coinjoinState: Pair<String, Boolean>
        get() = when (info?.coinjoinStatus) {
            "In progress", "In critical phase" -> "Making your coins private…" to true
            "In schedule" -> "Waiting for the next round" to true
            null -> "Unknown" to false
            else -> (if (privacyPercentage >= 100) "All coins are private" else "Paused") to false
        }

    init {
        if (demo) loadDemo()
        loadPrivacyStop()
    }

    suspend fun refresh() {
        if (demo) return // screenshots and tests: the demo wallet is the whole truth, no link is ever tried
        refreshing = true
        try {
            if (server.transport == Transport.ONION) {
                withContext(Dispatchers.IO) { Tor.start(context.filesDir.resolve("tor").path) } // opens Arti's state on disk
                // Arti waits for bootstrap before connecting anyway; polling instead keeps the progress visible.
                val progress = Tor.progress()
                if (progress in 0..99) { torProgress = progress; return }
            }
            torProgress = null
            val rpc = rpc
            val started = Instant.now() // a Tor request can hang for minutes, so the grace period counts from when it began
            try {
                status = Status.from(rpc.obj("getstatus"))
                wallets = rpc.array("listwallets").map { it.getString("walletName") }
                try { fees = rpc.obj("getfeerates").let { o -> o.keys().asSequence().associateWith { o.getDouble(it) } } } catch (e: Exception) { if (RpcError.isTransport(e)) throw e }
                error = null; linkError = null; failingSince = null; updated = Instant.now()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                if (RpcError.isTransport(e)) linkFailed(e, started) else error = e.message // Wasabi itself said no (wrong RPC password, ...)
                return
            }

            if (wallet?.let { it in wallets } != true) selectWallet(wallets.firstOrNull())
            val wallet = wallet ?: run { walletLoaded = true; return }
            try {
                // getwalletinfo fails when no coordinator is configured; balance then falls back to the coin sum.
                var info: WalletInfo? = null
                try { info = WalletInfo.from(rpc.obj("getwalletinfo", wallet = wallet)) } catch (e: Exception) { if (RpcError.isTransport(e)) throw e }
                val coins = rpc.array("listunspentcoins", wallet = wallet).map(Coin::from)
                val history = rpc.array("gethistory", wallet = wallet).map(HistoryItem::from)
                var payments: List<CoinjoinPayment>? = null // null when unknown: never read as "no payment waits"
                try { payments = rpc.array("listpaymentsincoinjoin", wallet = wallet).map(CoinjoinPayment::from) } catch (e: Exception) { if (RpcError.isTransport(e)) throw e }
                if (this.wallet != wallet) return // switched meanwhile; that wallet's own refresh fills these in
                this.info = info
                this.coins = coins
                this.history = history
                this.payments = payments ?: emptyList()
                walletLoaded = true
                walletError = null
                // A payment that was not on screen before: this is what "after receiving" means while the app is open.
                val incoming = history.filter { it.amount > 0 && !it.islikelycoinjoin }.map { it.tx }.toSet()
                val fresh = seenIncoming?.let { it.first == wallet && (incoming - it.second).isNotEmpty() } ?: false
                seenIncoming = wallet to incoming
                if (fresh) autoCoinjoin()
                if (payments != null && stopAfterPayments && payments.none { it.waiting }) stopPaymentCoinjoin(wallet)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                if (RpcError.isTransport(e)) { linkFailed(e, started); return } // keep the last coins and history on screen
                if (this.wallet != wallet) return
                coins = emptyList(); history = emptyList(); payments = emptyList()
                walletLoaded = true // loaded as far as it goes: the gate shows walletError
                walletError = e.message
            }
        } finally {
            refreshing = false
        }
    }

    // With data on screen a dropped link only greys the menu's link light. Before the first answer, a slow first Tor circuit
    // (Arti reports it as a timeout) still counts as connecting for 3 minutes.
    private suspend fun linkFailed(e: Exception, started: Instant) {
        if (!currentCoroutineContext().isActive) return // the refresh loop restarted (foreground, network change); the link did not fail
        val since = failingSince ?: started
        failingSince = since
        if (status == null && server.transport == Transport.ONION && Instant.now().epochSecond - since.epochSecond < 180) return
        linkError = e.message ?: e.javaClass.simpleName
    }

    // Starts a coinjoin the way the desktop's auto-coinjoin would, for hot wallets only and without ever prompting: a wallet
    // that is not logged in on the computer gets a quiet hint in the Privacy tile. Balanced stops once every coin reaches the
    // target (desktop Play); More private stays armed for whatever arrives next (Wasabi's stopWhenAllMixed false). Neither
    // overrides Wasabi's stop threshold. Skipped when the desktop's own auto-coinjoin is on.
    suspend fun autoCoinjoin() {
        if (screen != null) return
        val wallet = wallet ?: return
        val info = info ?: return
        if (privacyStop == PrivacyStop.CHEAP || !info.canAutoCoinjoin || info.isAutoCoinjoin == true || info.coinjoining) return
        try {
            rpc.call("startcoinjoin", JSONArray(listOf("", privacyStop == PrivacyStop.BALANCED, false)), wallet)
            coinjoinHint = null
        } catch (e: RpcError) {
            coinjoinHint = if (e.needsPassphrase) "New coins arrived. Log in to $wallet on your computer and they will be made private." else e.message
        } catch (e: Exception) {
            if (e is CancellationException) throw e
            coinjoinHint = e.message
        }
    }

    private suspend fun stopPaymentCoinjoin(wallet: String) {
        if (privacyStop != PrivacyStop.CHEAP || info?.coinjoining != true) { stopAfterPayments = false; return }
        try {
            rpc.call("stopcoinjoin", wallet = wallet)
            stopAfterPayments = false
        } catch (e: Exception) {
            if (e is CancellationException) throw e // kept otherwise, so the next refresh tries again
        }
    }

    // Runs a mutating action. If Wasabi says the wallet is not logged in, asks for the passphrase once and retries with it.
    suspend fun perform(title: String, password: String = "", action: suspend (String) -> String?): Boolean {
        if (demo) { notice = "Demo wallet: $title is not performed"; return false } // screenshots never reach a real Wasabi
        busy = true
        try {
            action(password)?.let { notice = it }
            refresh()
            return true
        } catch (e: CancellationException) {
            throw e
        } catch (e: RpcError) {
            if (e.needsPassphrase && password.isEmpty()) passphrase = PassphraseRequest(title, action) else notice = e.message
        } catch (e: Exception) {
            notice = e.message ?: e.javaClass.simpleName
        } finally {
            busy = false
        }
        return false
    }

    // listcoins: every coin the wallet ever had, with what spent it. Read-only; only the coin screen needs it.
    suspend fun loadAllCoins() {
        val wallet = wallet ?: return
        if (allCoins.isNotEmpty() || demo) return
        val all = runCatching { rpc.array("listcoins", wallet = wallet).map(Coin::from) }.getOrDefault(emptyList())
        if (this.wallet == wallet) allCoins = all
    }

    // Discreet mode (sabi's DISCREET): keep the shape, hide the digits.
    fun btc(sats: Long): String {
        val text = Bitcoin.btc(sats) + " BTC"
        return if (discreet) text.replace(Regex("\\d"), "*") else text
    }

    // Whole dollars, except under a dollar where bitcoin.design asks for two meaningful digits rather than "$0".
    fun usd(sats: Long): String? {
        val rate = status?.exchangeRate?.takeIf { it > 0 } ?: return null
        if (discreet) return "$***"
        val value = sats / 1e8 * rate
        val format = NumberFormat.getCurrencyInstance(Locale.getDefault()).apply { currency = Currency.getInstance("USD") }
        if (value < 1) {
            format.maximumFractionDigits = 4
            val digits = java.math.BigDecimal(value).round(java.math.MathContext(2)).toDouble()
            return format.format(digits)
        }
        format.maximumFractionDigits = 0
        return format.format(value)
    }

    fun mask(address: String) = if (discreet) address.take(4) + "********" else address

    // Wasabi's PrivacyLevel per coin (SmartCoinExtensions.GetPrivacyLevel): private at the target, semi-private from 2.
    enum class Tier(val label: String) { PRIVATE("private"), SEMI("semi-private"), NONE("non-private") }

    fun tier(score: Double) = if (score >= target) Tier.PRIVATE else if (score >= 2) Tier.SEMI else Tier.NONE

    private fun forcedStop() = when (screen) {
        "receiveLow", "sendLow" -> PrivacyStop.CHEAP
        "receiveHigh", "sendHigh", "sendAmountHigh" -> PrivacyStop.PRIVATE
        else -> null
    }

    // Store screenshots and tests: made-up but believable data. Addresses are BIP173's public test vector, txids are
    // hashes of a counter; nothing here belongs to a real wallet.
    private fun loadDemo() {
        fun txid(i: Int) = MessageDigest.getInstance("SHA-256").digest("wasabi-preview-demo-$i".toByteArray()).joinToString("") { "%02x".format(it) }
        server = Server(host = "100.64.0.1", user = "demo", password = "demo")
        selectWallet("Daily") // first: switching wallet clears the data below
        status = Status("Running", "921337", 0, "Main", 95000.0, 8)
        info = WalletInfo(true, 4_876_543, "In progress", 10, false, false, null, null)
        wallets = listOf("Daily", "Savings")
        fees = mapOf("2" to 3.1, "6" to 1.6, "144" to 1.0)
        val coinSpecs = listOf(
            Triple(1_048_576L, 34.0, null), Triple(1_048_576L, 21.0, null), Triple(524_288L, 27.0, null), Triple(524_288L, 12.0, null),
            Triple(262_144L, 15.0, null), Triple(500_000L, 6.0, "Bitcoin meetup refund"), Triple(918_559L, 1.0, "Salary October"),
            Triple(50_000L, 1.0, "Coffee change"),
        )
        coins = coinSpecs.mapIndexed { i, (sats, score, label) ->
            Coin(txid(i), i % 3, sats, score, i != 7, if (i == 7) 0 else 6 + 17 * i, label, DEMO_ADDRESS, false, null)
        }
        val historySpecs = listOf(
            Triple(-21_500L, "Groceries", false), Triple(-1_237L, "", true), Triple(-988L, "", true), Triple(-1_412L, "", true),
            Triple(918_559L, "Salary October", false), Triple(-64_000L, "Dinner with friends", false), Triple(500_000L, "Bitcoin meetup refund", false),
            Triple(-15_000L, "Coffee at the corner bar", false), Triple(2_000_000L, "From savings", false), Triple(-1_105L, "", true),
        )
        history = historySpecs.mapIndexed { i, (sats, label, round) ->
            HistoryItem(Instant.ofEpochSecond(1_792_000_000L - i * 61_200L), if (i == 0) "Mempool" else (921_337 - 9 * i).toString(), sats, label, txid(100 + i), round)
        }
        updated = Instant.now()
        walletLoaded = true
    }

    companion object {
        const val DEMO_ADDRESS = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4" // BIP173 test vector
        const val DEMO_TAPROOT = "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0" // BIP350 test vector
        const val DEMO_TXID = "4a5e1e4baab89f3a32518a88c31bc87f618f76673e2cc77ab2127b7afdeda33b"

        // Same weighting as Wallet.GetPrivacyPercentage() in Wasabi, so the number matches the desktop: semi-private coins count partly.
        fun privacyPercentage(coins: List<Coin>, target: Int): Int {
            val current = coins.sumOf { c ->
                val cap = (if (c.anonymityScore >= target) target - 1 else target - 2).toDouble()
                c.amount * minOf(c.anonymityScore - 1, cap)
            }
            val max = coins.sumOf { it.amount }.toDouble() * (target - 1)
            return if (max == 0.0) 0 else (current * 100 / max).toInt()
        }
    }
}

fun jsonArray(vararg items: Any?) = JSONArray(items.toList())
fun jsonObject(vararg pairs: Pair<String, Any?>) = JSONObject().apply { pairs.forEach { (k, v) -> put(k, v) } }
