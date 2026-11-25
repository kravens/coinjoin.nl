package nl.kravens.wasabipreview

import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.foundation.layout.PaddingValues
import android.app.Activity
import android.content.ClipboardManager
import android.content.Context
import android.content.ContextWrapper
import android.content.Intent
import android.hardware.biometrics.BiometricManager.Authenticators.BIOMETRIC_STRONG
import android.hardware.biometrics.BiometricManager.Authenticators.DEVICE_CREDENTIAL
import android.hardware.biometrics.BiometricPrompt
import android.os.CancellationSignal
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.FilterQuality
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import org.json.JSONArray
import java.util.UUID
import kotlin.coroutines.resume

@Composable
fun Field(value: String, onChange: (String) -> Unit, placeholder: String, modifier: Modifier = Modifier, keyboard: KeyboardType = KeyboardType.Text, mono: Boolean = false, singleLine: Boolean = true, big: Boolean = false,
          imeAction: androidx.compose.ui.text.input.ImeAction = androidx.compose.ui.text.input.ImeAction.Default, onIme: (() -> Unit)? = null) {
    TextField(
        value, onChange, modifier.fillMaxWidth(), placeholder = { Text(placeholder) }, singleLine = singleLine,
        textStyle = (if (big) MaterialTheme.typography.headlineSmall.copy(fontWeight = FontWeight.Medium) else MaterialTheme.typography.bodyLarge).copy(fontFamily = if (mono) FontFamily.Monospace else null),
        keyboardOptions = KeyboardOptions(keyboardType = keyboard, autoCorrectEnabled = false, imeAction = imeAction),
        keyboardActions = androidx.compose.foundation.text.KeyboardActions(onAny = { onIme?.invoke() ?: defaultKeyboardAction(imeAction) }),
        colors = TextFieldDefaults.colors(focusedContainerColor = Color.Transparent, unfocusedContainerColor = Color.Transparent, disabledContainerColor = Color.Transparent,
            focusedIndicatorColor = Color.Transparent, unfocusedIndicatorColor = Color.Transparent),
    )
}

@Composable
fun UnitPicker(unit: String, onChange: (String) -> Unit) {
    Segmented(listOf("BTC" to "BTC", "sats" to "sats"), unit, Modifier.width(140.dp), onChange)
}

// Converts the typed amount between units, like the iOS app; keeping the digits would turn 1 BTC into 1 sat.
fun convert(amount: String, from: String, to: String): String {
    val value = (if (from == "BTC") Bitcoin.sats(amount) else amount.toLongOrNull()) ?: return ""
    return if (to == "BTC") Bitcoin.btc(value) else value.toString()
}

fun warning(text: String): @Composable () -> Unit = {
    Row(Modifier.padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        Icon(WasabiIcon.warning, 14.dp, Wasabi.error); Spacer(Modifier.width(6.dp)); Text(text, color = Wasabi.error, style = MaterialTheme.typography.bodySmall)
    }
}

fun Context.activity(): Activity? = this as? Activity ?: (this as? ContextWrapper)?.baseContext?.activity()

fun share(context: Context, text: String) {
    context.startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text), null))
}

// Receive as bitcoin.design's payment request: who is paying and an optional amount first, then one big QR with copy and share.
@Composable
fun ReceiveScreen(mirror: Mirror, nav: Nav) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val taproot = remember { context.getSharedPreferences("app", Context.MODE_PRIVATE).getBoolean("taproot", true) } // Settings › Sending and receiving
    val demo = mirror.screen == "receiveAddress"
    val paidDemo = mirror.screen == "receivePaid" // the demo coins sit on DEMO_ADDRESS, so its request shows "Payment received"
    var label by remember { mutableStateOf("") } // what is being typed; a comma or Enter turns it into a chip
    // Labels chosen so far; Wasabi stores them as one comma-separated set.
    val chips = remember { androidx.compose.runtime.mutableStateListOf<String>().apply { if (demo) addAll(listOf("Alice", "concert tickets")); if (paidDemo) add("Bitcoin meetup refund") } }
    var keys by remember { mutableStateOf(emptyList<WalletKey>()) } // listkeys: earlier labels and addresses awaiting payment
    LaunchedEffect(Unit) { keys = mirror.loadKeys() }
    var amount by remember { mutableStateOf(if (demo) "0.001" else "") }
    var unit by remember { mutableStateOf("BTC") }
    var address by remember { mutableStateOf<String?>(if (demo) Mirror.DEMO_TAPROOT else if (paidDemo) Mirror.DEMO_ADDRESS else null) }
    val sats = if (unit == "BTC") Bitcoin.sats(amount) else amount.toLongOrNull()
    val labels = chips + Labels.split(label)
    fun commit() {
        Labels.split(label).forEach { part -> if (chips.none { it.equals(part, ignoreCase = true) }) chips.add(part) }
        label = ""
    }

    val shown = address
    if (shown == null) {
        Screen("Receive", onBack = null, onClose = { nav.home() }, bottom = {
            PrimaryAction("Create address", WasabiIcon.receive, labels.isNotEmpty() && !mirror.busy) {
                commit()
                val full = chips.joinToString(", ")
                // sabi.py: getnewaddress [label, taproot]; Wasabi refuses an empty label.
                scope.launch { mirror.perform("New address") { address = mirror.rpc.obj("getnewaddress", jsonArray(full, taproot), mirror.wallet).getString("address"); null } }
            }
        }) { padding ->
            Sections(padding) {
                item {
                    Section(footer = "Only you see the label: Wasabi keeps it so you know later where the coins came from. Separate people or reasons with a comma. Leave the amount empty and the sender fills it in.") {
                        WalletGate(mirror) {
                            Column {
                                val p = LocalPalette.current
                                if (chips.isNotEmpty()) Row(Modifier.horizontalScroll(rememberScrollState()).padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                                    for (chip in chips.toList()) androidx.compose.material3.Button(
                                        { chips.remove(chip) }, Modifier.semantics { contentDescription = "$chip, remove" },
                                        shape = CircleShape, contentPadding = PaddingValues(horizontal = 12.dp, vertical = 4.dp),
                                        colors = androidx.compose.material3.ButtonDefaults.buttonColors(containerColor = p.accent.copy(alpha = 0.85f), contentColor = Color.White),
                                    ) { Text(chip); Spacer(Modifier.width(4.dp)); Glyph(Glyph.CLOSE, 9.dp, Color.White) }
                                }
                                Field(label, { label = it; if (',' in it) commit() }, if (chips.isEmpty()) "Who is paying you" else "Add another label",
                                    imeAction = androidx.compose.ui.text.input.ImeAction.Next, onIme = { commit() })
                                val suggestions = Labels.suggestions(Labels.ranked(keys), label, labels)
                                // Earlier labels, like desktop Wasabi's suggestions: tap one to add it.
                                if (suggestions.isNotEmpty()) Row(Modifier.horizontalScroll(rememberScrollState()).padding(bottom = 8.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                                    for (s in suggestions) TintedButton({ label = ""; chips.add(s) }, contentPadding = PaddingValues(horizontal = 12.dp, vertical = 4.dp)) { Text(s) }
                                }
                                Divider()
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Field(amount, { amount = it }, "Amount (optional)", Modifier.weight(1f), KeyboardType.Decimal)
                                    UnitPicker(unit) { amount = convert(amount, unit, it); unit = it }
                                }
                                if (sats != null) mirror.usd(sats)?.let { Secondary(it, Modifier.padding(bottom = 8.dp).frosted(mirror.discreet)) }
                            }
                        }
                    }
                }
                val waiting = Labels.waiting(keys)
                if (waiting.isNotEmpty()) item {
                    Section("Waiting for payment", "Addresses you handed out that have not been paid yet. Tap one to show it again.") {
                        waiting.forEachIndexed { i, key ->
                            if (i > 0) Divider()
                            // Show this address again instead of handing out a new one to someone who has not paid yet.
                            Column(Modifier.fillMaxWidth().clickable { chips.clear(); chips.addAll(key.labels); label = ""; amount = ""; address = key.address }.padding(vertical = 10.dp)) {
                                Text(key.labels.joinToString(", "), maxLines = 1, overflow = TextOverflow.Ellipsis)
                                Text(mirror.mask(key.address), Modifier.frosted(mirror.discreet), fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall,
                                    color = LocalPalette.current.secondary, maxLines = 1, overflow = TextOverflow.MiddleEllipsis)
                            }
                        }
                    }
                }
            }
        }
    } else {
        val uri = Bitcoin.uri(shown, sats)
        // bitcoin.design: brighten the screen while a QR is shown so it scans first time.
        DisposableEffect(Unit) {
            val window = context.activity()?.window
            val before = window?.attributes?.screenBrightness
            window?.attributes = window.attributes.apply { screenBrightness = 1f }
            onDispose { if (before != null) window.attributes = window.attributes.apply { screenBrightness = before } }
        }
        Screen("Payment request", onBack = null, onClose = { nav.home() }, bottom = {
            // Done also arms the coinjoin for Balanced and More private; autoCoinjoin is quiet and does nothing for Lower fees.
            PrimaryAction("Done", WasabiIcon.check) { scope.launch { mirror.autoCoinjoin() }; nav.home() }
        }) { padding ->
            // The refresh loop keeps running under this screen, so a coin on this address shows up here within seconds.
            val paid = mirror.coins.filter { it.address == shown }
            Sections(padding) {
                if (paid.isNotEmpty()) item {
                    Row(Modifier.fillMaxWidth().glass(LocalPalette.current, RoundedCornerShape(15.dp)).padding(14.dp).semantics(mergeDescendants = true) {}, verticalAlignment = Alignment.CenterVertically) {
                        Icon(WasabiIcon.check, 26.dp, LocalPalette.current.accent)
                        Spacer(Modifier.width(12.dp))
                        Column {
                            Text("Payment received", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                            Amount(paid.sumOf { it.amount }, mirror, Modifier.frosted(mirror.discreet))
                            Secondary(if (paid.all { it.confirmed }) "Confirmed" else "Pending, waiting for a block")
                        }
                    }
                }
                item {
                    Tile(centered = true) {
                        val qr = remember(uri) { Bitcoin.qr(uri).asImageBitmap() }
                        // Wasabi draws the QR on a white card; keep it scannable in dark mode and under discreet frosting.
                        Image(qr, "QR code of the payment request", Modifier.widthIn(max = 260.dp).fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(Color.White).padding(12.dp).frosted(mirror.discreet), filterQuality = FilterQuality.None)
                        Text(mirror.spaced(shown), Modifier.frosted(mirror.discreet), fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall, textAlign = TextAlign.Center) // Copy and Share give it whole
                        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            CopyButton(uri)
                            TintedButton({ share(context, uri) }) { Icon(WasabiIcon.share, 16.dp, LocalPalette.current.accent); Spacer(Modifier.width(6.dp)); Text("Share", color = LocalPalette.current.accent) }
                        }
                    }
                }
                item {
                    Tile("Request") {
                        InfoRow("From", chips.joinToString(", "))
                        if (sats != null && sats > 0) LabeledRow("Amount") { Amount(sats, mirror, Modifier.frosted(mirror.discreet)) }
                        // From the address itself: a reopened one keeps the type it was made with, whatever Settings says now.
                        InfoRow("Address type", if (Bitcoin.isTaproot(shown)) "Taproot" else "SegWit")
                    }
                }
                item { Secondary("Each address is used once. Create a new one for the next payment.", Modifier.fillMaxWidth(), align = TextAlign.Center) }
            }
        }
    }
}

enum class SendStep { TO, AMOUNT, FEE, REVIEW, SUCCESS }

class Review(
    val address: String, val sats: Long, val fee: Long?, val inputs: Int, val toxic: Boolean, val inCoinjoin: Boolean, val onlyPrivate: Boolean,
    val password: String, // from the build step; lives here only until the flow closes
    val send: suspend (String) -> String?,
) { val id: UUID = UUID.randomUUID() }

// The Send flow's state, alive from the first step to Done. `payment` comes from a scan or a bitcoin: link.
class SendModel(private val mirror: Mirror, payment: Bitcoin.Payment?) {
    var address by mutableStateOf(payment?.address ?: "")
    var amount by mutableStateOf(payment?.sats?.takeIf { it > 0 }?.let { Bitcoin.btc(it) } ?: "")
    var unit by mutableStateOf("BTC")
    var label by mutableStateOf(payment?.label ?: payment?.message ?: "")
    var feeTarget by mutableStateOf(if (mirror.info?.deviceSigns != true) "private" else "6") // a private payment is the mobile default
    var customRate by mutableIntStateOf(1)
    var review by mutableStateOf<Review?>(null)
    var sent by mutableStateOf<String?>(null) // txid, or the payment id when paid in coinjoin

    init {
        if (mirror.screen in setOf("sendReview", "sendSuccess")) {
            review = Review(Mirror.DEMO_ADDRESS, 150_000, 1_234, 2, true, false, false, "") { null }
            feeTarget = "6"
            if (mirror.screen == "sendSuccess") sent = Mirror.DEMO_TXID
        }
    }

    // A private payment (payincoinjoin) is the default. Not for a device-signed wallet, whose rounds need the device
    // (payincoinjoin refuses), and not for Send all: a round cannot pay out the whole balance.
    val privateAllowed get() = mirror.info?.deviceSigns != true && !sendAll
    val inCoinjoin get() = feeTarget == "private" && privateAllowed
    val onlyPrivate get() = mirror.privacyStop == PrivacyStop.PRIVATE // regular transactions only
    val sats get() = if (unit == "BTC") Bitcoin.sats(amount) else amount.toLongOrNull()
    val spendable get() = mirror.coins.filter { it.confirmed && (!onlyPrivate || it.anonymityScore >= mirror.target) }
    val available get() = spendable.sumOf { it.amount }
    val confirmed get() = mirror.coins.filter { it.confirmed }.sumOf { it.amount }
    val notYetPrivate get() = confirmed - available
    val feeTargets get() = Bitcoin.feeChoices(mirror.fees)
    // Only a send-all pays its fee out of the amount; anything less leaves room for the fee on top.
    val sendAll get() = sats == available
    val subtractFee get() = sendAll
    // A round can draw on any confirmed coin; a regular transaction only on what the privacy setting lets it spend.
    val amountValid get() = (sats ?: 0) > 0 && sats!! <= (if (inCoinjoin) confirmed else available)

    // Checksums, address type and the wallet's network; null while the field is empty or fine.
    val addressError: String?
        get() = if (address.isEmpty()) null else try { Bitcoin.address(address, mirror.network); null } catch (e: Bitcoin.ParseError) { e.message }

    // A fee choice the screen still offers, else about an hour.
    fun keepFeeTargetUsable(customFee: Boolean) {
        val usable = feeTarget in feeTargets || (customFee && feeTarget == "custom") || (feeTarget == "private" && privateAllowed)
        if (!usable) feeTargets.minByOrNull { Math.abs(it.toInt() - 6) }?.let { feeTarget = it }
    }

    fun apply(payment: Bitcoin.Payment) {
        address = payment.address
        payment.sats?.takeIf { it > 0 }?.let { unit = "BTC"; amount = Bitcoin.btc(it) }
        val l = payment.label ?: payment.message
        if (l != null && label.isEmpty()) label = l
    }

    // Pasted text that does not parse lands in the field as it is, so the inline message can say what is wrong with it.
    fun paste(text: String) {
        try { apply(Bitcoin.parse(text, mirror.network)) } catch (e: Bitcoin.ParseError) { address = text.trim() }
    }

    suspend fun prepare() {
        val sats = sats ?: return
        val address = address; val label = label; val onlyPrivate = onlyPrivate; val subtractFee = subtractFee
        if (inCoinjoin) {
            // Wasabi PayInCoinJoin(address, amount, password); the passphrase is re-checked at the call. A queued payment
            // only goes out while the wallet is in coinjoin and Wasabi does not start one for it, so this does, like the
            // desktop's Play; stopWhenAllMixed follows the privacy setting and PlebStop is overridden for the payment's sake.
            review = Review(address, sats, null, 0, false, true, false, "") { pw ->
                val id = mirror.rpc.call("payincoinjoin", jsonArray(address, sats, pw), mirror.wallet).toString()
                sent = id
                if (mirror.info?.coinjoining == true) return@Review null
                try {
                    mirror.rpc.call("startcoinjoin", jsonArray(pw, mirror.privacyStop != PrivacyStop.PRIVATE, true), mirror.wallet)
                    if (mirror.privacyStop == PrivacyStop.CHEAP) mirror.stopAfterPayments = true
                    null
                } catch (e: Exception) {
                    "The payment is queued, but coinjoin did not start (${e.message}). Start it from the Privacy screen."
                }
            }
            return
        }
        val need = if (subtractFee) sats else sats + maxOf(5_000, sats * 3 / 1000)
        val pick = Bitcoin.pickCoins(mirror.coins, mirror.target, need, onlyPrivate)
        if (onlyPrivate && pick.coins.sumOf { it.amount } < need) {
            // Said here rather than as Wasabi's "insufficient funds": the private coins cover the amount but not the fee too.
            mirror.notice = "Your private coins cover the amount but not the mining fee as well. Send a little less, wait until more coins are private (${mirror.btc(notYetPrivate)} is on its way), or choose Balanced."
            return
        }
        val custom = feeTarget == "custom"
        val rate = customRate
        val target = feeTarget.toIntOrNull() ?: 6
        fun params(password: String) = jsonObject(
            // sabi.py do_send(): named params; coins as {transactionid, index} (OutPointAsTxoRefJsonConverter is case-insensitive).
            "payments" to JSONArray().put(jsonObject("sendto" to address, "amount" to sats, "label" to label, "subtractFee" to subtractFee)),
            "coins" to JSONArray(pick.coins.map { jsonObject("transactionid" to it.txid, "index" to it.index) }),
            "password" to password,
        ).apply { if (custom) put("feeRate", rate) else put("feeTarget", target) }
        // `build` first: it needs the same passphrase and signing ability as `send`, and the hex gives the exact fee.
        mirror.perform("Build transaction") { pw ->
            val hex = mirror.rpc.call("build", params(pw), mirror.wallet).toString()
            val parsed = Bitcoin.inspect(hex)
            val byId = pick.coins.associate { it.id to it.amount }
            val inputs = parsed?.inputs?.mapNotNull { byId[it] }
            val fee = if (inputs != null && inputs.size == parsed.inputs.size) inputs.sum() - parsed.outputs else null
            review = Review(address, sats, fee, parsed?.inputs?.size ?: pick.coins.size, pick.toxic, false, onlyPrivate, pw) { pw2 ->
                sent = mirror.rpc.obj("send", params(pw2), mirror.wallet).getString("txid")
                null
            }
            null
        }
    }
}

// Send as bitcoin.design's step-by-step flow: where to, how much, how to pay, then review, the lock screen and success.
@Composable
fun SendScreen(mirror: Mirror, nav: Nav, model: SendModel, step: SendStep) {
    val context = LocalContext.current
    val customFee = remember { context.getSharedPreferences("app", Context.MODE_PRIVATE).getBoolean("customFee", false) } // Settings › Sending and receiving
    LaunchedEffect(model.feeTargets, model.privateAllowed) { model.keepFeeTargetUsable(customFee) }
    // Move on when the build step produced a review, and when the payment went out (a passphrase retry included).
    LaunchedEffect(model.review?.id) { if (model.review != null && step == SendStep.FEE) nav.push(Route.Send(SendStep.REVIEW)) }
    LaunchedEffect(model.sent) { if (model.sent != null && step == SendStep.REVIEW) nav.push(Route.Send(SendStep.SUCCESS)) }
    when (step) {
        SendStep.TO -> SendTo(mirror, nav, model)
        SendStep.AMOUNT -> SendAmount(mirror, nav, model)
        SendStep.FEE -> SendFee(mirror, nav, model, customFee)
        SendStep.REVIEW -> model.review?.let { ReviewStep(mirror, nav, it) }
        SendStep.SUCCESS -> SendSuccess(mirror, nav, model)
    }
}

// Step 1: where to. Scanning is the primary way in, as the guide suggests; paste and typing are right there too.
@Composable
private fun SendTo(mirror: Mirror, nav: Nav, model: SendModel) {
    val context = LocalContext.current
    var scanning by remember { mutableStateOf(false) }
    if (scanning) {
        ScannerScreen(mirror.network, onClose = { scanning = false }) { model.apply(it); scanning = false }
        return
    }
    Screen("Send", onBack = null, onClose = { nav.home() }, bottom = {
        PrimaryAction("Continue", enabled = model.address.isNotEmpty() && model.addressError == null) { nav.push(Route.Send(SendStep.AMOUNT)) }
    }) { padding ->
        Sections(padding) {
            item {
                Section("To") {
                    WalletGate(mirror) {
                        Column {
                            Row(Modifier.padding(vertical = 10.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                                Button({ scanning = true }, Modifier.weight(1f).height(48.dp), colors = ButtonDefaults.buttonColors(containerColor = LocalPalette.current.accent)) {
                                    Icon(WasabiIcon.qr, 18.dp, Color.White); Spacer(Modifier.width(8.dp)); Text("Scan QR", color = Color.White)
                                }
                                TintedButton({
                                    val clip = (context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager).primaryClip
                                    model.paste(clip?.getItemAt(0)?.coerceToText(context)?.toString() ?: "")
                                }, Modifier.weight(1f).height(48.dp)) {
                                    Icon(WasabiIcon.paste, 18.dp, LocalPalette.current.accent); Spacer(Modifier.width(8.dp)); Text("Paste", color = LocalPalette.current.accent)
                                }
                            }
                            Field(model.address, { model.address = it }, "Address or bitcoin: link", mono = true, singleLine = false)
                            model.addressError?.let { warning(it)() }
                        }
                    }
                }
            }
            item { Section("Label", "Stored with the transaction so the history keeps its meaning.") { Field(model.label, { model.label = it }, "Who are you paying") } }
        }
    }
}

// Step 2: how much, in BTC or sats with the fiat value underneath; switching units converts instead of clearing.
@Composable
private fun SendAmount(mirror: Mirror, nav: Nav, model: SendModel) {
    val footer = when {
        model.inCoinjoin -> "Available: ${mirror.btc(model.confirmed)} in confirmed coins"
        model.onlyPrivate -> "Private and ready: ${mirror.btc(model.available)} in ${model.spendable.size} coins." + if (model.notYetPrivate > 0) " Another ${mirror.btc(model.notYetPrivate)} is still being made private." else ""
        else -> "Available: ${mirror.btc(model.available)} in ${model.spendable.size} confirmed coins"
    }
    Screen("Amount", onBack = { nav.pop() }, onClose = { nav.home() }, bottom = {
        PrimaryAction("Continue", enabled = model.amountValid && !mirror.busy) { nav.push(Route.Send(SendStep.FEE)) }
    }) { padding ->
        Sections(padding) {
            item {
                Section(footer = footer) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Field(model.amount, { model.amount = it }, "0", Modifier.weight(1f), KeyboardType.Decimal, big = true)
                        UnitPicker(model.unit) { model.amount = convert(model.amount, model.unit, it); model.unit = it }
                    }
                    model.sats?.let { s -> mirror.usd(s)?.let { Secondary(it, Modifier.padding(bottom = 8.dp).frosted(mirror.discreet), MaterialTheme.typography.bodyLarge) } }
                    if ((model.sats ?: 0) > 0 && !model.amountValid) overBalance(model)()
                }
            }
            item {
                Section(footer = "Send all is a regular transaction: it spends every ${if (model.onlyPrivate) "private" else "confirmed"} coin and takes the mining fee out of the amount.") {
                    Row(Modifier.fillMaxWidth().clickable {
                        model.amount = if (model.unit == "BTC") Bitcoin.btc(model.available) else model.available.toString() // in the unit on screen
                    }.padding(vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text("Send all (", color = LocalPalette.current.accent)
                        Amount(model.available, mirror, Modifier.frosted(mirror.discreet))
                        Text(")", color = LocalPalette.current.accent)
                    }
                }
            }
        }
    }
}

private fun overBalance(model: SendModel) = warning(if (!model.inCoinjoin && model.onlyPrivate) "More than the private balance" else "More than the confirmed balance")

// "about 20 min", "about 1 hr", "about 1 day": a block every ten minutes.
private fun wait(blocks: Int): String {
    val minutes = blocks * 10
    return "about " + when {
        minutes >= 1440 -> "${Math.round(minutes / 1440.0)} day" + if (Math.round(minutes / 1440.0) == 1L) "" else "s"
        minutes >= 60 -> "${Math.round(minutes / 60.0)} hr"
        else -> "$minutes min"
    }
}

// Step 3: how to pay. A private payment first, as the default; then regular transactions by rough wait time.
@Composable
private fun SendFee(mirror: Mirror, nav: Nav, model: SendModel, customFee: Boolean) {
    val scope = rememberCoroutineScope()
    val p = LocalPalette.current
    @Composable fun Choice(tag: String, title: String, detail: String?, trailing: String? = null) {
        Row(Modifier.fillMaxWidth().clickable { model.feeTarget = tag }.padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            RadioButton(model.feeTarget == tag, { model.feeTarget = tag })
            Column(Modifier.weight(1f)) {
                Text(title)
                if (detail != null) Secondary(detail)
            }
            if (trailing != null) Text(trailing, color = p.secondary, style = MaterialTheme.typography.bodyMedium)
        }
    }
    Screen("How to pay", onBack = { nav.pop() }, onClose = { nav.home() }, bottom = {
        PrimaryAction(if (model.inCoinjoin) "Review payment" else "Review transaction", WasabiIcon.send, model.amountValid && !mirror.busy) { scope.launch { model.prepare() } }
    }) { padding ->
        Sections(padding) {
            if (model.privateAllowed) item {
                Section(footer = "No separate mining fee, and the receiver cannot tell which of the round's coins paid them.") {
                    Choice("private", "Private payment", "With one of the next privacy rounds, usually within hours")
                }
            }
            item {
                Section("Regular transaction", if (mirror.info?.deviceSigns == true) "A device signs this wallet's rounds, so a private payment is not possible here. The exact mining fee is shown on the next screen."
                    else "Confirms sooner, as its own transaction. The exact mining fee is shown on the next screen before anything is sent.") {
                    model.feeTargets.forEach { target ->
                        Choice(target, wait(target.toInt()), null, "$target blocks · ${"%.1f".format(mirror.fees[target] ?: 0.0)} sat/vB")
                    }
                    if (customFee) Choice("custom", "Custom rate", null)
                    if (customFee && model.feeTarget == "custom") Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(vertical = 6.dp)) {
                        Text("${model.customRate} sat/vB", Modifier.weight(1f))
                        TextButton({ if (model.customRate > 1) model.customRate-- }) { Text("−") }
                        TextButton({ if (model.customRate < 500) model.customRate++ }) { Text("+") }
                    }
                    if (!model.inCoinjoin && !model.amountValid) overBalance(model)()
                }
            }
        }
    }
}

// Step 4: the compact summary the guide asks for, anything to notice (toxic merge) highlighted, the lock screen as final consent.
@Composable
private fun ReviewStep(mirror: Mirror, nav: Nav, review: Review) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var authError by remember { mutableStateOf<String?>(null) }
    Screen("Review", onBack = { nav.pop() }, onClose = { nav.home() }, bottom = {
        PrimaryAction(if (review.inCoinjoin) "Confirm and queue" else "Confirm and send", WasabiIcon.check, !mirror.busy) {
            scope.launch {
                authError = authenticate(context, "Send ${Bitcoin.btc(review.sats)} BTC")
                if (authError == null) mirror.perform(if (review.inCoinjoin) "Private payment" else "Send", review.password, review.send)
            }
        }
    }) { padding ->
        Sections(padding) {
            item {
                Section(if (review.inCoinjoin) "Private payment" else "Transaction") {
                    LabeledRow("To") { Text(mirror.spaced(review.address), Modifier.weight(1.5f, false).frosted(mirror.discreet), fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall, textAlign = TextAlign.End) }
                    Divider(); LabeledRow("Amount") { Amount(review.sats, mirror, Modifier.frosted(mirror.discreet)) }
                    mirror.usd(review.sats)?.let { Divider(); LabeledRow("Value") { Text(it, Modifier.frosted(mirror.discreet)) } }
                    Divider()
                    if (review.inCoinjoin) InfoRow("Mining fee", "None separately; paid within the round")
                    else {
                        LabeledRow("Mining fee") { val fee = review.fee; if (fee != null) Amount(fee, mirror, Modifier.frosted(mirror.discreet)) else Text("Unknown") }
                        Divider(); InfoRow("Coins spent", if (review.onlyPrivate) "${review.inputs}, all private" else "${review.inputs}")
                    }
                }
            }
            if (review.toxic) item {
                Tile {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(WasabiIcon.warning, 17.dp, Wasabi.warning); Spacer(Modifier.width(10.dp))
                        Text("This spend combines private and non-private coins, which undoes their privacy. Choose More private on the Privacy screen to avoid it.", color = Wasabi.warning)
                    }
                }
            }
            authError?.let { item { Text(it, color = Wasabi.error) } }
            if (mirror.info?.isWatchOnly == true && mirror.info?.isHardwareWallet == true && !review.inCoinjoin) item {
                // Wasabi signs through the device on the computer ("no Trezor device found" when it is unplugged).
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(WasabiIcon.wallet, 17.dp, LocalPalette.current.secondary); Spacer(Modifier.width(10.dp))
                    Secondary("Your hardware wallet signs this payment. Connect it to your computer and confirm on the device after unlocking.")
                }
            }
        }
    }
}

// The lock screen (fingerprint, face or PIN) as consent, through the platform prompt: no Google services needed. Null when it passed.
suspend fun authenticate(context: Context, reason: String): String? = suspendCancellableCoroutine { done ->
    val cancel = CancellationSignal()
    done.invokeOnCancellation { cancel.cancel() }
    runCatching {
        BiometricPrompt.Builder(context).setTitle(reason).setAllowedAuthenticators(BIOMETRIC_STRONG or DEVICE_CREDENTIAL).build()
            .authenticate(cancel, context.mainExecutor, object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) { if (done.isActive) done.resume(null) }
                override fun onAuthenticationError(code: Int, message: CharSequence) { if (done.isActive) done.resume(message.toString()) }
            })
    }.onFailure { if (done.isActive) done.resume(it.message ?: "Set a screen lock to confirm payments") }
}

// Step 5: done. The proof (txid) is right here to copy or share; Done closes the whole flow.
@Composable
private fun SendSuccess(mirror: Mirror, nav: Nav, model: SendModel) {
    val context = LocalContext.current
    val review = model.review
    val queued = review?.inCoinjoin == true
    Screen("", onBack = null, onClose = { nav.home() }, bottom = { PrimaryAction("Done", WasabiIcon.check) { nav.home() } }) { padding ->
        Sections(padding) {
            item {
                Column(Modifier.fillMaxWidth().padding(top = 24.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(18.dp)) {
                    Icon(WasabiIcon.check, 56.dp, LocalPalette.current.accent)
                    Text(if (queued) "Queued" else "Sent", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold)
                    if (review != null) {
                        Amount(review.sats, mirror, Modifier.frosted(mirror.discreet), style = MaterialTheme.typography.headlineMedium.copy(fontWeight = FontWeight.Light))
                        Text(mirror.spaced(review.address), Modifier.frosted(mirror.discreet), fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall, textAlign = TextAlign.Center)
                    }
                    model.sent?.let { sent ->
                        Tile(if (queued) "Payment ID" else "Transaction ID", centered = true) {
                            SelectionContainer { Text(sent, fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall, textAlign = TextAlign.Center) }
                            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                                CopyButton(sent)
                                TintedButton({ share(context, sent) }) { Icon(WasabiIcon.share, 16.dp, LocalPalette.current.accent); Spacer(Modifier.width(6.dp)); Text("Share", color = LocalPalette.current.accent) }
                            }
                        }
                    }
                    Secondary(if (queued) "It goes out with one of the next privacy rounds, usually within hours. Until then it is listed on the Privacy screen, where you can still cancel it." else "It shows as pending in Transactions until a block confirms it.", align = TextAlign.Center)
                }
            }
        }
    }
}
