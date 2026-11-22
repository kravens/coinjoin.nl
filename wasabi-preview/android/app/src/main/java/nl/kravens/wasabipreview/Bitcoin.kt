package nl.kravens.wasabipreview

import android.graphics.Bitmap
import com.google.zxing.BarcodeFormat
import com.google.zxing.EncodeHintType
import com.google.zxing.qrcode.QRCodeWriter
import com.google.zxing.qrcode.decoder.ErrorCorrectionLevel
import java.net.URLDecoder
import java.security.MessageDigest

// Money helpers, ported from the iOS app's Bitcoin.swift. Everything is integer satoshis; BTC strings never go through Double.
object Bitcoin {
    // Which chain. getstatus.network is NBitcoin's name: Main, TestNet, TestNet4, Signet or RegTest.
    enum class Network(val label: String) {
        MAIN("mainnet"), TEST("testnet"), REGTEST("regtest");

        companion object {
            fun wasabi(name: String): Network = when (name.lowercase()) {
                "main", "mainnet" -> MAIN
                "regtest" -> REGTEST
                else -> TEST // TestNet, TestNet4 and Signet share tb1, m, n and 2
            }
        }
    }

    data class Payment(val address: String, val sats: Long? = null, val label: String? = null, val message: String? = null)

    // Why scanned or pasted text cannot be paid, in words the scanner shows as they are.
    sealed class ParseError(message: String) : Exception(message) {
        data object NotBitcoin : ParseError("Not a bitcoin address or payment link")
        data object Invalid : ParseError("This looks like a bitcoin address, but it is not valid: a character is wrong or missing")
        data class Unsupported(val version: Int) : ParseError("Segwit version $version addresses of this shape have no spending rules yet; coins sent there could be taken by anyone")
        data class WrongNetwork(val address: Network, val wallet: Network) : ParseError("This is a ${address.label} address, but the wallet is on ${wallet.label}")
        data object Amount : ParseError("The amount in this request is not a valid BTC amount")
        data class Duplicate(val key: String) : ParseError("This request repeats $key, which is not allowed")
        data class Required(val key: String) : ParseError("This request requires $key, which this app does not support")
        data class OffChain(val keys: List<String>) : ParseError("Wasabi pays on-chain only; this request has no on-chain address, only " + list(keys.map(::offChainName)))
        data object Empty : ParseError("This payment link has no address to pay")
    }

    private fun offChainName(key: String) = when (key) {
        "lightning" -> "a Lightning invoice"
        "lno" -> "a Lightning offer"
        "sp" -> "a silent payment address"
        "pay" -> "a BIP 351 private payment address"
        else -> key
    }

    private fun list(items: List<String>) = when (items.size) {
        0 -> ""
        1 -> items[0]
        2 -> "${items[0]} and ${items[1]}"
        else -> items.dropLast(1).joinToString(", ") + ", and " + items.last()
    }

    // "0.001" -> 100_000. Null for anything that is not a plain decimal with at most 8 fraction digits.
    fun sats(btc: String): Long? {
        val text = btc.trim().replace(",", ".")
        val parts = text.split(".")
        if (text.isEmpty() || parts.size > 2 || !text.all { it.isDigit() || it == '.' }) return null
        val whole = if (parts[0].isEmpty()) 0L else parts[0].toLongOrNull() ?: return null
        val fraction = if (parts.size == 2) parts[1] else ""
        if (whole > 21_000_000 || fraction.length > 8) return null
        val frac = fraction.padEnd(8, '0').toLongOrNull() ?: return null
        return whole * 100_000_000 + frac
    }

    fun btc(sats: Long): String {
        val magnitude = Math.abs(sats)
        return (if (sats < 0) "-" else "") + "%d.%08d".format(magnitude / 100_000_000, magnitude % 100_000_000)
    }

    // A plain address, or a BIP 321 (BIP 21) link. `bitcoin:` and the query keys are case-insensitive; the path may be empty
    // when a query parameter carries the payment instruction, and bech32/bech32m addresses may also come under their
    // human-readable part as the key (bc=, tb=). The first supported on-chain address wins, in the order given; Lightning and
    // silent-payment instructions are explained rather than silently dropped. pop is never contacted; req-pop, like any
    // req- key this app does not implement, makes the whole link invalid.
    fun parse(input: String, wallet: Network? = null): Payment {
        val text = input.trim()
        if (!text.lowercase().startsWith("bitcoin:")) return Payment(address(text, wallet))
        val body = text.substring("bitcoin:".length)
        val path = body.substringBefore('?')
        val candidates = if (path.isEmpty()) mutableListOf() else mutableListOf(path)
        val offChain = mutableListOf<String>()
        val seen = mutableSetOf<String>()
        var payment = Payment("")
        val query = if (body.contains('?')) body.substringAfter('?') else ""
        for (item in query.split('&').filter { it.isNotEmpty() }) {
            val key = item.substringBefore('=').lowercase()
            val raw = if (item.contains('=')) item.substringAfter('=') else ""
            val value = runCatching { URLDecoder.decode(raw.replace("+", "%2B"), "UTF-8") }.getOrDefault(raw)
            when {
                key in setOf("amount", "label", "message", "pop") -> { // these MUST NOT repeat
                    if (!seen.add(key)) throw ParseError.Duplicate(key)
                    when (key) {
                        "amount" -> {
                            // Decimal BTC only: digits and one period; no sign, exponent or thousands separator.
                            if (!raw.all { it in '0'..'9' || it == '.' }) throw ParseError.Amount
                            payment = payment.copy(sats = sats(raw) ?: throw ParseError.Amount)
                        }
                        "label" -> payment = payment.copy(label = value)
                        "message" -> payment = payment.copy(message = value)
                    } // pop: proof of payment is not implemented and never contacted
                }
                key in setOf("bc", "tb", "bcrt") -> { // an address keyed by its hrp must carry that hrp
                    if (!value.lowercase().startsWith(key + "1")) throw ParseError.Invalid
                    candidates.add(value)
                }
                key in setOf("lightning", "lno", "sp", "pay") -> if (key !in offChain) offChain.add(key)
                key.startsWith("req-") -> throw ParseError.Required(key.removePrefix("req-"))
                else -> {} // pj, pjos and anything else optional can be ignored; the on-chain address still gets paid
            }
        }
        var problem: ParseError? = null
        for (candidate in candidates) {
            try {
                return payment.copy(address = address(candidate, wallet))
            } catch (e: ParseError) {
                if (e == ParseError.Invalid) throw e // a damaged address is never skipped over
                problem = problem ?: e // wrong network or a future version: the next candidate may do
            }
        }
        problem?.let { throw it }
        throw if (offChain.isEmpty()) ParseError.Empty else ParseError.OffChain(offChain)
    }

    // One entry point for "can Wasabi pay this": real checksums (base58check, BIP173 bech32, BIP350 bech32m), the shapes
    // Wasabi can pay (P2PKH, P2SH, P2WPKH, P2WSH, P2TR) and, when known, the wallet's network. Returns the address as it
    // should be sent: bech32 lower-cased, base58 untouched.
    fun address(input: String, wallet: Network? = null): String {
        val s = input.trim()
        val low = s.lowercase()
        val network: Network
        var legacy = false
        val hrp = listOf("bc", "tb", "bcrt").firstOrNull { low.startsWith(it + "1") }
        if (hrp != null) {
            val decoded = segwit(s) ?: throw ParseError.Invalid
            // Spendable today: v0 (P2WPKH 20 bytes, P2WSH 32) and v1 P2TR (32). Other versions are anyone-can-spend until a soft fork.
            if (!(decoded.version == 0 || (decoded.version == 1 && decoded.program.size == 32))) throw ParseError.Unsupported(decoded.version)
            network = if (hrp == "bc") Network.MAIN else if (hrp == "tb") Network.TEST else Network.REGTEST
        } else if (s.isNotEmpty() && s[0] in "123mn" && s.length in 25..35 && s.all { it in BASE58 }) {
            // Version byte 0x00 P2PKH, 0x05 P2SH; 0x6f and 0xc4 on the test networks. The hash is always 20 bytes.
            val payload = base58check(s)
            if (payload == null || payload.size != 21 || payload[0] !in listOf(0x00, 0x05, 0x6f, 0xc4)) throw ParseError.Invalid
            network = if (payload[0] < 0x6f) Network.MAIN else Network.TEST
            legacy = true
        } else {
            throw ParseError.NotBitcoin
        }
        // base58 test bytes are shared by testnet, signet and regtest; bech32 names its chain in the hrp.
        if (wallet != null && network != wallet && !(legacy && network == Network.TEST && wallet == Network.REGTEST)) {
            throw ParseError.WrongNetwork(network, wallet)
        }
        return if (legacy) s else low
    }

    fun isAddress(s: String, network: Network? = null) = runCatching { address(s, network) }.isSuccess

    private const val BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
    private const val BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

    private fun polymod(values: List<Int>): Long {
        val gen = longArrayOf(0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3)
        var chk = 1L
        for (v in values) {
            val top = chk ushr 25
            chk = ((chk and 0x1ffffff) shl 5) xor v.toLong()
            for (i in 0 until 5) if ((top ushr i) and 1L == 1L) chk = chk xor gen[i]
        }
        return chk
    }

    data class Bech32(val hrp: String, val data: List<Int>, val m: Boolean)

    // BIP173/BIP350: the human-readable part, the 5-bit data without its checksum, and whether the bech32m constant matched.
    // Null for anything that is not a well-formed string of either kind.
    fun bech32(s: String): Bech32? {
        if (s.length > 90 || !(s == s.lowercase() || s == s.uppercase())) return null
        val low = s.lowercase()
        val separator = low.lastIndexOf('1')
        if (separator < 0) return null
        val hrp = low.substring(0, separator)
        val rest = low.substring(separator + 1)
        if (hrp.length !in 1..83 || !hrp.all { it.code in 33..126 } || rest.length < 6) return null
        val values = rest.map { BECH32_CHARSET.indexOf(it) }
        if (values.any { it < 0 }) return null
        val expanded = hrp.map { it.code shr 5 } + listOf(0) + hrp.map { it.code and 31 }
        return when (polymod(expanded + values)) {
            1L -> Bech32(hrp, values.dropLast(6), false)
            0x2bc830a3L -> Bech32(hrp, values.dropLast(6), true)
            else -> null
        }
    }

    data class Segwit(val hrp: String, val version: Int, val program: ByteArray)

    // A segwit address: hrp, witness version and program, per BIP173 (v0, bech32) and BIP350 (v1+, bech32m). Null when invalid.
    fun segwit(s: String): Segwit? {
        val decoded = bech32(s) ?: return null
        val version = decoded.data.firstOrNull() ?: return null
        if (decoded.hrp !in listOf("bc", "tb", "bcrt") || version > 16 || (version == 0) == decoded.m) return null
        // 5-bit groups to bytes; the leftover must be fewer than 5 bits and all zero.
        var acc = 0
        var bits = 0
        val program = mutableListOf<Byte>()
        for (v in decoded.data.drop(1)) {
            acc = (acc shl 5) or v
            bits += 5
            while (bits >= 8) { bits -= 8; program.add(((acc shr bits) and 0xff).toByte()) }
            acc = acc and ((1 shl bits) - 1)
        }
        if (bits >= 5 || acc != 0 || program.size !in 2..40 || (version == 0 && program.size != 20 && program.size != 32)) return null
        return Segwit(decoded.hrp, version, program.toByteArray())
    }

    // base58check payload (version byte and hash) when the double-SHA-256 checksum holds, as unsigned ints. Null otherwise.
    fun base58check(s: String): List<Int>? {
        val bytes = ArrayList<Int>() // big-endian
        for (c in s) {
            var carry = BASE58.indexOf(c)
            if (carry < 0) return null
            for (i in bytes.indices.reversed()) {
                carry += 58 * bytes[i]
                bytes[i] = carry and 0xff
                carry = carry shr 8
            }
            while (carry > 0) { bytes.add(0, carry and 0xff); carry = carry shr 8 }
        }
        repeat(s.takeWhile { it == '1' }.length) { bytes.add(0, 0) }
        if (bytes.size <= 4) return null
        val payload = bytes.dropLast(4)
        val sha = MessageDigest.getInstance("SHA-256")
        val hash = sha.digest(sha.digest(ByteArray(payload.size) { payload[it].toByte() }))
        return if ((0 until 4).all { (hash[it].toInt() and 0xff) == bytes[bytes.size - 4 + it] }) payload else null
    }

    // No label: Wasabi's label says who is paying, which is the receiver's private note, not something to hand the payer.
    fun uri(address: String, sats: Long?): String {
        if (sats == null || sats <= 0) return "bitcoin:$address"
        return "bitcoin:$address?amount=" + btc(sats).trimEnd('0').trimEnd('.')
    }

    data class Inspected(val inputs: List<String>, val outputs: Long)

    // Raw tx from `build`: which outpoints it spends and the output total. Fee = known inputs - outputs.
    fun inspect(hex: String): Inspected? {
        if (hex.length % 2 != 0) return null
        val bytes = (hex.indices step 2).map { hex.substring(it, it + 2).toIntOrNull(16) ?: return null }
        if (bytes.size <= 4) return null
        var p = 4
        val inputs = mutableListOf<String>()
        var outputs = 0L
        fun varint(): Long? {
            if (p >= bytes.size) return null
            val first = bytes[p]; p += 1
            val width = if (first < 0xfd) 0 else if (first == 0xfd) 2 else if (first == 0xfe) 4 else 8
            if (width == 0) return first.toLong()
            if (p + width > bytes.size) return null
            val value = (0 until width).fold(0L) { acc, i -> acc or (bytes[p + i].toLong() shl (8 * i)) }
            p += width
            return value
        }
        if (bytes.size > 6 && bytes[4] == 0 && bytes[5] == 1) p += 2 // segwit marker + flag
        val vin = varint() ?: return null
        repeat(vin.toInt()) {
            if (p + 36 > bytes.size) return null
            val txid = (p until p + 32).reversed().joinToString("") { "%02x".format(bytes[it]) }
            val index = (0 until 4).fold(0L) { acc, i -> acc or (bytes[p + 32 + i].toLong() shl (8 * i)) }
            inputs.add("$txid:$index")
            p += 36
            val scriptLength = varint() ?: return null
            p += scriptLength.toInt() + 4
        }
        val vout = varint() ?: return null
        repeat(vout.toInt()) {
            if (p + 8 > bytes.size) return null
            outputs += (0 until 8).fold(0L) { acc, i -> acc or (bytes[p + i].toLong() shl (8 * i)) }
            p += 8
            val scriptLength = varint() ?: return null
            p += scriptLength.toInt()
        }
        return Inspected(inputs, outputs)
    }

    // At most three fee choices, the ones people pick by: fastest, about an hour and about a day. Wasabi's own list can run
    // to a dozen targets, or collapse to one when fees are flat; targets with the same rate as a faster one are dropped.
    fun feeChoices(fees: Map<String, Double>): List<String> {
        val targets = fees.keys.mapNotNull { it.toIntOrNull() }.sorted()
        val fastest = targets.firstOrNull() ?: return emptyList()
        fun near(want: Int) = targets.minBy { Math.abs(it - want) }
        val choices = mutableListOf<Int>()
        for (target in setOf(fastest, near(6), near(144)).sorted()) {
            if (choices.isEmpty() || fees[choices.last().toString()] != fees[target.toString()]) choices.add(target)
        }
        return choices.map { it.toString() }
    }

    data class Pick(val coins: List<Coin>, val toxic: Boolean)

    // sabi.py pick_coins(): confirmed coins, private first, then semi, then non-private, largest first. `onlyPrivate` (the
    // "More private" stop) never reaches past the private group, so the caller sees a shortfall instead of a toxic merge.
    fun pickCoins(coins: List<Coin>, target: Int, need: Long, onlyPrivate: Boolean = false): Pick {
        val t = target.toDouble()
        val spendable = coins.filter { it.confirmed }
        val groups = mutableListOf(spendable.filter { it.anonymityScore >= t })
        if (!onlyPrivate) {
            groups += spendable.filter { 1 < it.anonymityScore && it.anonymityScore < t }
            groups += spendable.filter { it.anonymityScore <= 1 }
        }
        val picked = mutableListOf<Coin>()
        var total = 0L
        for (group in groups) {
            for (coin in group.sortedByDescending { it.amount }) {
                if (total >= need) break
                picked.add(coin); total += coin.amount
            }
            if (total >= need) break
        }
        val toxic = picked.any { it.anonymityScore >= t } && picked.any { it.anonymityScore <= 1 }
        return Pick(picked, toxic)
    }

    // Own outputs of the same transaction with the same value as `coin`, itself included: what the wallet can count of
    // the coin's anonymity set without the full transaction (no Wasabi RPC returns one).
    fun ownEqualOutputs(coin: Coin, coins: List<Coin>): List<Coin> =
        coins.filter { it.txid == coin.txid && it.amount == coin.amount }.sortedBy { it.index }

    fun qr(text: String, size: Int = 720): Bitmap {
        val matrix = QRCodeWriter().encode(text, BarcodeFormat.QR_CODE, size, size, mapOf(EncodeHintType.ERROR_CORRECTION to ErrorCorrectionLevel.M, EncodeHintType.MARGIN to 0))
        val pixels = IntArray(matrix.width * matrix.height) { if (matrix.get(it % matrix.width, it / matrix.width)) 0xFF000000.toInt() else 0xFFFFFFFF.toInt() }
        return Bitmap.createBitmap(pixels, matrix.width, matrix.height, Bitmap.Config.ARGB_8888)
    }
}
