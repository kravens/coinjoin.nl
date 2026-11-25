package nl.kravens.wasabipreview

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.security.KeyStore
import java.time.Instant
import java.time.OffsetDateTime
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

enum class Transport { TAILSCALE, ONION }

data class Server(
    val transport: Transport = Transport.TAILSCALE,
    val host: String = "", // the computer's Tailscale IP or MagicDNS name, entered under Connection
    val port: Int = 37128,
    val user: String = "",
    val password: String = "",
    val onion: String = "", // xyz...onion, served by the C tor on the computer
    val onionKey: String = "", // the x25519 client-auth private key, base32 as in C tor's .auth_private
) {
    // Tailscale CGNAT range 100.64.0.0/10 or a MagicDNS name; anything else would send the login unencrypted.
    val isTailscale: Boolean
        get() {
            val host = host.lowercase()
            if (host.endsWith(".ts.net")) return true
            if (BuildConfig.DEBUG && host == "10.0.2.2") return true // the emulator's alias for the Mac's loopback
            val parts = host.split(".")
            val octets = parts.mapNotNull { it.toIntOrNull()?.takeIf { n -> n in 0..255 } }
            return parts.size == 4 && octets.size == 4 && octets[0] == 100 && octets[1] in 64..127
        }

    // A v3 onion is 56 base32 characters plus ".onion"; Tor itself then encrypts and authenticates the link.
    val isOnion: Boolean get() = Regex("[a-z2-7]{56}\\.onion").matches(onion.lowercase())

    val isAllowed: Boolean get() = if (transport == Transport.ONION) isOnion else isTailscale

    // C tor's ClientOnionAuthDir line, which is what Arti parses; accepts a pasted full line in the key field too.
    val authLine: String
        get() = onion.lowercase().replace(".onion", "") + ":descriptor:x25519:" + onionKey.split(":").last().trim()

    fun json(): String = JSONObject()
        .put("transport", transport.name.lowercase()).put("host", host).put("port", port).put("user", user)
        .put("password", password).put("onion", onion).put("onionKey", onionKey).toString()

    companion object {
        fun parse(json: String): Server? = runCatching {
            val o = JSONObject(json)
            Server(
                if (o.optString("transport") == "onion") Transport.ONION else Transport.TAILSCALE,
                o.getString("host"), o.getInt("port"), o.getString("user"), o.getString("password"),
                o.optString("onion"), o.optString("onionKey"),
            )
        }.getOrNull()
    }
}

class RpcError(message: String, val transport: Boolean = false) : Exception(message) {
    // Wasabi answers "'X' wallet requires the passphrase..." or "Invalid password" when the wallet is not logged in.
    val needsPassphrase: Boolean
        get() {
            val text = message ?: return false
            if (text.startsWith("Wrong RPC user")) return false
            return text.contains("passphrase", ignoreCase = true) || text.contains("password", ignoreCase = true)
        }

    companion object {
        fun isTransport(e: Throwable) = e is IOException || (e as? RpcError)?.transport == true
    }
}

class WasabiRpc(private val server: Server) {
    companion object {
        // The same RPC credentials can also stop Wasabi, dump keys or run scripts, so this app refuses anything not on this list.
        val allowed = setOf(
            "getstatus", "listwallets", "getwalletinfo", "listunspentcoins", "listcoins", "gethistory", "getfeerates", "listpaymentsincoinjoin",
            "loadwallet", "getnewaddress", "build", "send", "payincoinjoin", "cancelpaymentincoinjoin",
            "startcoinjoin", "startcoinjoinsweep", "stopcoinjoin", "excludefromcoinjoin",
        )
        private val reads = setOf("getstatus", "listwallets", "getwalletinfo", "listunspentcoins", "listcoins", "gethistory", "getfeerates", "listpaymentsincoinjoin")
    }

    // params: positional JSONArray or named JSONObject, as the Wasabi method expects (sabi.py uses both). Returns `result`.
    suspend fun call(method: String, params: Any = JSONArray(), wallet: String? = null): Any? = withContext(Dispatchers.IO) {
        if (method !in allowed) throw RpcError("$method is not allowed from this app")
        if (!server.isAllowed) throw RpcError("Only Tailscale addresses (100.64.0.0/10 or *.ts.net) or a Tor onion are allowed: the RPC login can move funds and plain HTTP is only safe inside WireGuard or Tor")
        val body = JSONObject().put("jsonrpc", "2.0").put("id", "1").put("method", method).put("params", params).toString().toByteArray()
        val credentials = Base64.encodeToString("${server.user}:${server.password}".toByteArray(), Base64.NO_WRAP)
        val path = "/" + (wallet?.let { URLEncoder.encode(it, "UTF-8").replace("+", "%20") } ?: "")
        val (status, data) = if (server.transport == Transport.ONION) {
            // Wasabi's HttpListener only answers its own prefix, so Host names the loopback RPC port, not the onion.
            Tor.request(server.authLine, 80, path, "127.0.0.1:${server.port}", credentials, body, method in reads) // HiddenServicePort 80
        } else {
            val connection = URL("http://${server.host}:${server.port}$path").openConnection() as HttpURLConnection
            try {
                // Reads are instant on a working link, so fail fast; build/send/coinjoin calls can take a while.
                val timeout = if (method in reads) 10_000 else 60_000
                connection.connectTimeout = timeout
                connection.readTimeout = timeout
                connection.requestMethod = "POST"
                connection.doOutput = true
                connection.setRequestProperty("Authorization", "Basic $credentials")
                connection.setRequestProperty("Content-Type", "application/json")
                connection.outputStream.use { it.write(body) }
                val code = connection.responseCode
                code to ((if (code >= 400) connection.errorStream else connection.inputStream)?.use { it.readBytes() } ?: ByteArray(0))
            } finally {
                connection.disconnect()
            }
        }
        if (status == 401) throw RpcError("Wrong RPC user or password")
        if (status == 404) throw RpcError("Wasabi answered 404: wrong wallet name or RPC port")
        val envelope = runCatching { JSONObject(String(data)) }.getOrElse { throw RpcError("Wasabi sent something that is not JSON (HTTP $status)") }
        envelope.optJSONObject("error")?.let { throw RpcError(it.optString("message", "Wasabi error")) }
        envelope.opt("result")?.takeIf { it != JSONObject.NULL }
    }

    suspend fun obj(method: String, params: Any = JSONArray(), wallet: String? = null): JSONObject =
        call(method, params, wallet) as? JSONObject ?: throw RpcError("Empty response for $method")

    suspend fun array(method: String, params: Any = JSONArray(), wallet: String? = null): JSONArray =
        call(method, params, wallet) as? JSONArray ?: throw RpcError("Empty response for $method")
}

// Response shapes from WalletWasabi.Client/Rpc/WasabiJsonRpcService.cs. Amounts are satoshis.
data class Status(
    val torStatus: String,
    val bestBlockchainHeight: String,
    val filtersLeft: Int,
    val network: String,
    val exchangeRate: Double?, // absent when the desktop has no exchange-rate provider; then no fiat amounts
    val connectedPeers: Int,
) {
    companion object {
        fun from(o: JSONObject): Status {
            val peers = o.optJSONArray("peers") ?: JSONArray()
            return Status(
                o.getString("torStatus"), o.get("bestBlockchainHeight").toString(), o.getInt("filtersLeft"), o.getString("network"),
                if (o.isNull("exchangeRate")) null else o.optDouble("exchangeRate").takeIf { !it.isNaN() },
                (0 until peers.length()).count { peers.getJSONObject(it).optBoolean("isConnected") },
            )
        }
    }
}

data class WalletInfo(
    val loaded: Boolean,
    val balance: Long?,
    val coinjoinStatus: String?,
    val anonScoreTarget: Int,
    val isWatchOnly: Boolean,
    val isHardwareWallet: Boolean,
    val isAutoCoinjoin: Boolean?, // the desktop's own "Automatically start coinjoin"; when on, Wasabi starts coinjoins itself
    val coinjoinSignedByDevice: Boolean?, // preview builds: a Trezor signs the rounds, so payincoinjoin is impossible
) {
    val deviceSigns get() = coinjoinSignedByDevice ?: isHardwareWallet
    val coinjoining get() = coinjoinStatus?.let { it != "Idle" } ?: false
    // Desktop CoinJoinStateViewModel: hardware and watch-only wallets never auto-start; a device-signed one needs a tap on the device.
    val canAutoCoinjoin get() = !isHardwareWallet && !isWatchOnly

    companion object {
        fun from(o: JSONObject) = WalletInfo(
            o.getBoolean("loaded"), o.optLongOrNull("balance"), o.optStringOrNull("coinjoinStatus"), o.getInt("anonScoreTarget"),
            o.getBoolean("isWatchOnly"), o.getBoolean("isHardwareWallet"), o.optBooleanOrNull("isAutoCoinjoin"), o.optBooleanOrNull("coinjoinSignedByDevice"),
        )
    }
}

// listunspentcoins; listcoins (every coin ever, with spentBy) has no label or excludedFromCoinjoin.
data class Coin(
    val txid: String,
    val index: Int,
    val amount: Long,
    val anonymityScore: Double,
    val confirmed: Boolean,
    val confirmations: Int? = null,
    val label: String? = null,
    val address: String = "",
    val excludedFromCoinjoin: Boolean? = false,
    val spentBy: String? = null,
) {
    val id get() = "$txid:$index"
    val excluded get() = excludedFromCoinjoin == true

    companion object {
        fun from(o: JSONObject) = Coin(
            o.getString("txid"), o.getInt("index"), o.getLong("amount"), o.getDouble("anonymityScore"), o.getBoolean("confirmed"),
            o.optIntOrNull("confirmations"), o.optStringOrNull("label"), o.getString("address"), o.optBooleanOrNull("excludedFromCoinjoin"),
            o.optStringOrNull("spentBy"),
        )
    }
}

data class HistoryItem(
    val datetime: Instant,
    val height: String,
    val amount: Long,
    val label: String?,
    val tx: String,
    val islikelycoinjoin: Boolean,
) {
    val title get() = if (islikelycoinjoin) "Privacy round" else if (!label.isNullOrEmpty()) label else if (amount > 0) "Received" else "Sent"
    val pending get() = height == "Mempool"

    companion object {
        // Wasabi 2.8 sends epoch seconds, older builds an ISO string.
        fun from(o: JSONObject): HistoryItem {
            val raw = o.get("datetime")
            val time = if (raw is Number) Instant.ofEpochMilli((raw.toDouble() * 1000).toLong())
            else runCatching { OffsetDateTime.parse(raw.toString()).toInstant() }.getOrDefault(Instant.EPOCH)
            return HistoryItem(time, o.get("height").toString(), o.getLong("amount"), o.optStringOrNull("label"), o.getString("tx"), o.getBoolean("islikelycoinjoin"))
        }

        // Newest first, with each run of consecutive privacy rounds as one entry; any other transaction ends a run.
        fun chains(items: List<HistoryItem>): List<List<HistoryItem>> {
            val chains = mutableListOf<MutableList<HistoryItem>>()
            for (item in items.sortedByDescending { it.datetime }) {
                if (item.islikelycoinjoin && chains.lastOrNull()?.last()?.islikelycoinjoin == true) chains.last().add(item)
                else chains.add(mutableListOf(item))
            }
            return chains
        }
    }
}

// listpaymentsincoinjoin; state[0] is the newest state.
data class CoinjoinPayment(val id: String, val amount: Long, val address: String?, val status: String) {
    val waiting get() = status == "Pending" || status == "In progress" // then Signed, Finished

    companion object {
        fun from(o: JSONObject) = CoinjoinPayment(
            o.getString("id"), o.getLong("amount"), o.optStringOrNull("address"),
            o.optJSONArray("state")?.optJSONObject(0)?.optString("status") ?: "?",
        )
    }
}

fun JSONObject.optStringOrNull(key: String): String? = if (isNull(key)) null else optString(key)
fun JSONObject.optLongOrNull(key: String): Long? = if (isNull(key)) null else optLong(key)
fun JSONObject.optIntOrNull(key: String): Int? = if (isNull(key)) null else optInt(key)
fun JSONObject.optBooleanOrNull(key: String): Boolean? = if (isNull(key)) null else optBoolean(key)
fun <T> JSONArray.map(f: (JSONObject) -> T): List<T> = (0 until length()).map { f(getJSONObject(it)) }

// The connection settings, AES-GCM encrypted with a key that never leaves the Android Keystore (the iOS app's Keychain).
object SecureStore {
    private const val ALIAS = "wasabi-server"
    private const val PREF = "server"

    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getEntry(ALIAS, null) as? KeyStore.SecretKeyEntry)?.let { return it.secretKey }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build(),
        )
        return generator.generateKey()
    }

    fun load(context: Context): Server? {
        val stored = context.getSharedPreferences("secure", Context.MODE_PRIVATE).getString(PREF, null) ?: return null
        return runCatching {
            val bytes = Base64.decode(stored, Base64.NO_WRAP)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, bytes, 0, 12))
            Server.parse(String(cipher.doFinal(bytes, 12, bytes.size - 12)))
        }.getOrNull()
    }

    fun save(context: Context, server: Server) {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val sealed = cipher.iv + cipher.doFinal(server.json().toByteArray())
        context.getSharedPreferences("secure", Context.MODE_PRIVATE).edit().putString(PREF, Base64.encodeToString(sealed, Base64.NO_WRAP)).apply()
    }
}

// Arti, the Rust Tor client in libwasabitor.so (the same crate the iOS app links), over JNI.
object Tor {
    init { System.loadLibrary("wasabitor") }

    @JvmStatic external fun start(dir: String): Int
    @JvmStatic external fun progress(): Int
    // Returns 4 big-endian bytes of HTTP status, then the body; throws IOException when the onion cannot be reached.
    @JvmStatic external fun post(authLine: String, port: Int, path: String, host: String, authorization: String, body: ByteArray, retry: Boolean): ByteArray

    fun request(authLine: String, port: Int, path: String, host: String, authorization: String, body: ByteArray, retry: Boolean): Pair<Int, ByteArray> {
        val reply = try {
            post(authLine, port, path, host, authorization, body, retry)
        } catch (e: IOException) {
            throw RpcError(e.message ?: "Tor error", transport = true)
        }
        val status = ((reply[0].toInt() and 0xff) shl 24) or ((reply[1].toInt() and 0xff) shl 16) or ((reply[2].toInt() and 0xff) shl 8) or (reply[3].toInt() and 0xff)
        return status to reply.copyOfRange(4, reply.size)
    }
}

// listkeys: every key of the wallet. Receive (not internal) keys carry the labels typed on Receive; a clean one with a
// label is an address handed out and not paid yet, desktop Wasabi's "addresses awaiting payment".
data class WalletKey(val fullKeyPath: String, val isInternal: Boolean, val keyState: Int, val label: String, val address: String) {
    val index get() = fullKeyPath.substringAfterLast('/').toIntOrNull() ?: 0
    val labels get() = Labels.split(label)
    val waiting get() = !isInternal && keyState == 0 && labels.isNotEmpty() // KeyState: 0 Clean, 1 Locked, 2 Used

    companion object {
        fun from(o: JSONObject) = WalletKey(
            o.getString("fullKeyPath"), o.getBoolean("internal"),
            // An enum, serialized as its number; accept its name too in case a Wasabi version switches to strings.
            o.opt("keyState").let { (it as? Number)?.toInt() ?: mapOf("Clean" to 0, "Locked" to 1, "Used" to 2)[it.toString()] ?: 2 },
            o.optString("label", ""), o.getString("address"),
        )
    }
}

object Labels {
    // Wasabi keeps a label as a comma-separated set ("Alice, concert tickets").
    fun split(label: String) = label.split(',').map { it.trim() }.filter { it.isNotEmpty() }

    // Desktop's GetLabelsWithRanking for Receive: each receive label scores 100 plus a recency bonus (100 for the newest
    // key, one less per older one, at least 1); change labels score 1. Case-insensitive, the first spelling wins.
    fun ranked(keys: List<WalletKey>): List<String> {
        val score = linkedMapOf<String, Pair<String, Int>>()
        fun add(label: String, points: Int) {
            val key = label.lowercase()
            score[key] = (score[key]?.first ?: label) to ((score[key]?.second ?: 0) + points)
        }
        keys.filter { !it.isInternal }.sortedByDescending { it.index }.forEachIndexed { n, k -> k.labels.forEach { add(it, 100 + maxOf(1, 100 - n)) } }
        keys.filter { it.isInternal }.forEach { k -> k.labels.forEach { add(it, 1) } }
        return score.values.sortedWith(compareByDescending<Pair<String, Int>> { it.second }.thenBy { it.first.lowercase() }).map { it.first }
    }

    // What to offer under the field: the best-ranked labels that contain what is typed, minus the ones already chosen.
    fun suggestions(ranked: List<String>, typed: String, chosen: List<String>, limit: Int = 6): List<String> {
        val t = typed.trim().lowercase()
        val taken = chosen.map { it.lowercase() }.toSet()
        return ranked.filter { it.lowercase() !in taken && (t.isEmpty() || it.lowercase().contains(t)) }.take(limit)
    }

    // Addresses handed out and not paid yet, newest first, one per address.
    fun waiting(keys: List<WalletKey>) = keys.filter { it.waiting }.sortedByDescending { it.index }.distinctBy { it.address }
}
