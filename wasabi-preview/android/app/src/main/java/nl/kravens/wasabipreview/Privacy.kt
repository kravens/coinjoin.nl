package nl.kravens.wasabipreview

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch

// The Privacy screen: the music box with its controls, the wallet's privacy choice, the facts (target, who signs, the
// desktop's own auto-start), sweep, and private payments waiting in a round. Technical terms are fine here.
@Composable
fun PrivacyScreen(mirror: Mirror, nav: Nav) {
    val scope = rememberCoroutineScope()
    var sweepTo by remember { mutableStateOf("") }
    Screen("Privacy", onBack = { nav.pop() }) { padding ->
        Sections(padding) {
            item { WalletGate(mirror) { MusicBox(mirror) } }
            if (mirror.walletError == null) {
                item {
                    Section(footer = privacyFooter(mirror), card = false) {
                        SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                            PrivacyStop.entries.forEachIndexed { i, stop ->
                                SegmentedButton(mirror.privacyStop == stop, { mirror.choosePrivacy(stop) }, SegmentedButtonDefaults.itemShape(i, PrivacyStop.entries.size), icon = {}) { Text(stop.label, maxLines = 1) }
                            }
                        }
                    }
                }
                item {
                    Section(footer = "Start runs coinjoins until every coin reaches the target, then stops. Keep making new coins private stays on and picks up whatever arrives later, like Wasabi's automatic coinjoin. The target itself is set in Wasabi on your computer; the RPC cannot change it.") {
                        InfoRow("Privacy target (anonymity score)", mirror.target.toString())
                        Divider(); InfoRow("Automatic start on the computer", if (mirror.info?.isAutoCoinjoin == true) "On" else "Off")
                        mirror.info?.takeIf { it.isHardwareWallet }?.let { Divider(); InfoRow("Signing", if (it.deviceSigns) "Hardware device" else "Watch-only") }
                        if (mirror.info?.coinjoining != true) {
                            Divider()
                            Row(Modifier.fillMaxWidth().clickable { start(mirror, scope, false) }.padding(vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                                Icon(WasabiIcon.coinjoin, 17.dp, LocalPalette.current.accent); Spacer(Modifier.width(10.dp)); Text("Keep making new coins private", color = LocalPalette.current.accent)
                            }
                        }
                    }
                }
                if (mirror.wallets.size > 1) item {
                    var open by remember { mutableStateOf(false) }
                    Section(footer = "Coinjoins every coin and sends the outputs to another wallet on your computer.") {
                        Box {
                            LabeledRow("Sweep into") { Text(sweepTo.ifEmpty { "Choose wallet" } + " ⌄", Modifier.clickable { open = true }, color = LocalPalette.current.accent) }
                            DropdownMenu(open, { open = false }) {
                                mirror.wallets.filter { it != mirror.wallet }.forEach { w -> DropdownMenuItem({ Text(w) }, { sweepTo = w; open = false }) }
                            }
                        }
                        Divider()
                        TextButton({
                            val target = sweepTo
                            scope.launch { mirror.perform("Sweep") { pw -> mirror.rpc.call("startcoinjoinsweep", jsonArray(pw, target), mirror.wallet); null } }
                        }, enabled = sweepTo.isNotEmpty()) { Text("Sweep via coinjoin") }
                    }
                }
                if (mirror.payments.isNotEmpty()) item {
                    Section("Private payments waiting for a round", "Swipe left to cancel a payment that has not been paid yet.") {
                        mirror.payments.forEachIndexed { i, payment ->
                            if (i > 0) Divider()
                            SwipeAction("Cancel", Wasabi.error, {
                                mirror.perform("Cancel payment") { mirror.rpc.call("cancelpaymentincoinjoin", jsonArray(payment.id), mirror.wallet); null }
                            }) {
                                Column(Modifier.padding(vertical = 10.dp)) {
                                    Row {
                                        Amount(payment.amount, mirror, Modifier.weight(1f).frosted(mirror.discreet))
                                        Secondary(payment.status, style = MaterialTheme.typography.bodyMedium)
                                    }
                                    Secondary(mirror.mask(payment.address ?: ""), Modifier.frosted(mirror.discreet))
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

// Wasabi's startcoinjoin [password, stopWhenAllMixed, overridePlebStop]. An explicit tap overrides the stop threshold, as
// the desktop's second click on "click to continue" does; the automatic start in Mirror.autoCoinjoin never does.
private fun start(mirror: Mirror, scope: kotlinx.coroutines.CoroutineScope, stopWhenAllPrivate: Boolean) {
    scope.launch {
        mirror.perform("Start coinjoin") { pw ->
            mirror.stopAfterPayments = false // started by hand: runs as asked, not just for a payment
            mirror.rpc.call("startcoinjoin", jsonArray(pw, stopWhenAllPrivate, true), mirror.wallet)
            null
        }
    }
}

// Desktop Wasabi's "music box": logo, status line and progress strip, with the play or stop control beside it.
@Composable
private fun MusicBox(mirror: Mirror) {
    val p = LocalPalette.current
    val scope = rememberCoroutineScope()
    val coinjoining = mirror.info?.coinjoining == true
    Row(verticalAlignment = Alignment.CenterVertically) {
        Row(
            Modifier.weight(1f).clip(RoundedCornerShape(15.dp)).background(p.card).padding(14.dp).semantics { contentDescription = "Coinjoin status: ${mirror.coinjoinState.first}" },
            verticalAlignment = Alignment.CenterVertically,
        ) {
            WasabiLogo(Modifier.size(36.dp))
            Spacer(Modifier.width(14.dp))
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(mirror.coinjoinState.first, style = MaterialTheme.typography.bodyMedium, maxLines = 2)
                Text("Wasabi: ${mirror.info?.coinjoinStatus ?: "Unknown"}", color = p.secondary, fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall)
                Box(Modifier.fillMaxWidth().height(4.dp).clip(CircleShape).background(if (coinjoining) p.accent.copy(alpha = 0.6f) else p.secondary.copy(alpha = 0.4f)))
            }
        }
        Spacer(Modifier.width(12.dp))
        Box(
            Modifier.size(52.dp).clip(CircleShape).background(if (coinjoining) Wasabi.error else p.accent).clickable {
                if (coinjoining) scope.launch {
                    mirror.perform("Stop coinjoin") { mirror.stopAfterPayments = false; mirror.rpc.call("stopcoinjoin", wallet = mirror.wallet); null }
                } else start(mirror, scope, true)
            }.semantics { contentDescription = if (coinjoining) "Stop coinjoin" else "Start coinjoin" },
            contentAlignment = Alignment.Center,
        ) { Icon(if (coinjoining) WasabiIcon.stop else WasabiIcon.play, 22.dp, Color.White) }
    }
}

// What the choice does for receiving and for paying, in the words of what the RPC can really do. Applies right away.
private fun privacyFooter(mirror: Mirror): String {
    val target = mirror.target
    val info = mirror.info
    val byHand = if (info?.canAutoCoinjoin == false) " This wallet's keys are on a device, so coinjoins are started by hand from the Privacy screen." else ""
    return when (mirror.privacyStop) {
        PrivacyStop.CHEAP -> "Received coins stay as they arrive and no coinjoin fees are paid. A private payment starts coinjoin and this app stops it again once the payment is out. Regular transactions spend your most private coins first and warn before combining private and non-private coins."
        PrivacyStop.BALANCED -> "Regular transactions spend your most private coins first and warn before combining private and non-private coins. " +
            (if (info?.isAutoCoinjoin == true) "Wasabi on your computer already makes received coins private, up to the privacy target ($target)."
            else "Received coins are made private while this app is open, until they reach the privacy target ($target); each round costs a share of its mining fee.") + byHand
        PrivacyStop.PRIVATE -> "Regular transactions spend only coins that reached the privacy target ($target). Coinjoin stays on for everything that arrives, now and later.$byHand"
    }
}

// One coin: what it is worth, how private it is and why, where it came from, and whether it is spent. The anonymity set is
// drawn from Wasabi's own score, since no Wasabi RPC returns the transaction's outputs and asking a block explorer would
// tell that explorer which transactions are ours.
@Composable
fun CoinScreen(mirror: Mirror, nav: Nav, coin: Coin) {
    val scope = rememberCoroutineScope()
    LaunchedEffect(coin.id) { mirror.loadAllCoins() }
    val live = mirror.coins.firstOrNull { it.id == coin.id } ?: coin
    val full = mirror.allCoins.firstOrNull { it.id == coin.id } // listcoins: adds spentBy
    val own = Bitcoin.ownEqualOutputs(coin, mirror.allCoins.ifEmpty { listOf(coin) })
    val tier = mirror.tier(live.anonymityScore)
    val score = Math.round(live.anonymityScore).toString()
    val origin = mirror.history.firstOrNull { it.tx == coin.txid }
    Screen("Coin", onBack = { nav.pop() }) { padding ->
        Sections(padding) {
            item {
                HeroTile {
                    Icon(if (tier == Mirror.Tier.PRIVATE) WasabiIcon.shieldTick else WasabiIcon.shield, 30.dp, tierColor(tier))
                    Amount(coin.amount, mirror, Modifier.frosted(mirror.discreet), style = MaterialTheme.typography.headlineMedium.copy(fontWeight = androidx.compose.ui.text.font.FontWeight.Light))
                    mirror.usd(coin.amount)?.let { Secondary(it, Modifier.frosted(mirror.discreet), MaterialTheme.typography.bodyLarge) }
                    Text("${tier.label.replaceFirstChar { it.uppercase() }} · anonymity score $score, target ${mirror.target}", color = tierColor(tier), style = MaterialTheme.typography.bodyMedium)
                }
            }
            item {
                Section("Who else looks like this coin",
                    if (own.size > 1) "Wasabi's score estimates how many equal outputs this coin blends in with; ${own.size} of them are yours from the same transaction. The exact crowd needs the full transaction, which Wasabi's RPC does not return, and this app never asks a block explorer."
                    else "Wasabi's score estimates how many equal outputs this coin blends in with. The exact crowd needs the full transaction, which Wasabi's RPC does not return, and this app never asks a block explorer.") {
                    AnonymitySetGrid(live.anonymityScore, own.size)
                }
            }
            item {
                Section("Coin") {
                    if (!live.label.isNullOrEmpty()) { InfoRow("Label", live.label); Divider() }
                    Column(Modifier.padding(vertical = 10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Secondary("Address", style = MaterialTheme.typography.bodyMedium)
                        SelectionContainer { Text(mirror.mask(coin.address), Modifier.frosted(mirror.discreet), fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall) }
                        CopyButton(coin.address, "Copy address")
                    }
                    Divider()
                    InfoRow("Status", if (live.confirmed) live.confirmations?.let { "Confirmed, $it confirmations" } ?: "Confirmed" else "Pending in the mempool")
                    Divider()
                    val spender = full?.spentBy
                    if (spender != null) Column(Modifier.padding(vertical = 10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Secondary("Spent by", style = MaterialTheme.typography.bodyMedium)
                        SelectionContainer { Text(spender, fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall) }
                        CopyButton(spender, "Copy transaction ID")
                    } else InfoRow("Spent", "No")
                    Divider()
                    LabeledRow("Coinjoin") {
                        if (live.excluded) { Icon(WasabiIcon.forbidden, 14.dp, Wasabi.orangeCoin); Spacer(Modifier.width(6.dp)) }
                        Text(if (live.excluded) "Excluded" else "Included", color = if (live.excluded) Wasabi.orangeCoin else LocalPalette.current.primary)
                    }
                    Divider()
                    TextButton({ scope.launch { excludeCoin(mirror, live)() } }) {
                        Text(if (live.excluded) "Include in coinjoin" else "Exclude from coinjoin", color = if (live.excluded) LocalPalette.current.accent else Wasabi.orangeCoin)
                    }
                }
            }
            item {
                Section("Came from transaction, output ${coin.index}") {
                    if (origin != null) {
                        Column(Modifier.fillMaxWidth().clickable { nav.push(Route.Transaction(origin)) }.padding(vertical = 10.dp)) {
                            Text(origin.title)
                            Secondary(dateTime.format(origin.datetime))
                        }
                        Divider(); InfoRow("Block", if (origin.pending) "Pending" else origin.height); Divider()
                    }
                    SelectionContainer { Text(coin.txid, Modifier.padding(vertical = 10.dp), fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall) }
                    CopyButton(coin.txid, "Copy transaction ID")
                    Spacer(Modifier.height(8.dp))
                }
            }
        }
    }
}

// The anonymity set as a crowd of equal outputs: Wasabi's score rounded to a count, ours in Wasabi green (this coin
// outlined), the rest neutral. Capped at 120 dots; the rest is said in words.
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun AnonymitySetGrid(score: Double, own: Int) {
    val p = LocalPalette.current
    val count = maxOf(1, Math.round(score).toInt())
    val shown = minOf(count, 120)
    Column(Modifier.padding(vertical = 10.dp).semantics { contentDescription = "About $count equal outputs, $own of them yours" }, verticalArrangement = Arrangement.spacedBy(10.dp)) {
        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            repeat(shown) { i ->
                Canvas(Modifier.size(14.dp)) {
                    drawCircle(if (i < own) p.privacyStrong else p.privacyNone)
                    if (i == 0) drawCircle(p.primary, style = Stroke(1.5.dp.toPx()), radius = size.minDimension / 2 - 0.75.dp.toPx())
                }
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
            Legend(p.privacyStrong, if (own > 1) "Yours ($own)" else "Yours", true)
            Legend(p.privacyNone, if (count > shown) "Others, ${count - shown} more not drawn" else "Others, estimated by Wasabi", false)
        }
    }
}

@Composable
private fun Legend(color: Color, text: String, outlined: Boolean) {
    val p = LocalPalette.current
    Row(verticalAlignment = Alignment.CenterVertically) {
        Canvas(Modifier.size(10.dp)) { drawCircle(color); if (outlined) drawCircle(p.primary, style = Stroke(1.5.dp.toPx()), radius = size.minDimension / 2 - 0.75.dp.toPx()) }
        Spacer(Modifier.width(5.dp))
        Secondary(text)
    }
}

// How the phone reaches Wasabi on the computer. Kept apart from the preferences: it needs Save, and it is what the first
// launch asks for.
@Composable
fun ConnectionScreen(mirror: Mirror, nav: Nav) {
    var server by remember { mutableStateOf(mirror.server) }
    Screen("Connection", onBack = { nav.pop() }, actions = {
        TextButton({
            val saved = server.copy(onion = server.onion.trim().lowercase())
            mirror.saveServer(saved)
            nav.pop()
        }) { Text("Save", color = LocalPalette.current.accent) }
    }) { padding ->
        Sections(padding) {
            item {
                SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                    listOf(Transport.TAILSCALE to "Tailscale", Transport.ONION to "Tor onion").forEachIndexed { i, (t, label) ->
                        SegmentedButton(server.transport == t, { server = server.copy(transport = t) }, SegmentedButtonDefaults.itemShape(i, 2), icon = {}) { Text(label) }
                    }
                }
            }
            if (server.transport == Transport.ONION) item {
                Section("Onion service", "The onion from the computer's HiddenServiceDir/hostname and the x25519 private key from tor-client.auth_private (the part after x25519:, or the whole line). Both stay encrypted in the Android Keystore; Tor runs inside this app.") {
                    Field(server.onion, { server = server.copy(onion = it) }, "xyz.onion", keyboard = androidx.compose.ui.text.input.KeyboardType.Uri)
                    Divider(); SecretRow(server.onionKey, { server = server.copy(onionKey = it) }, "Client auth key")
                    Divider(); Field(server.port.toString(), { v -> v.toIntOrNull()?.let { server = server.copy(port = it) } }, "Wasabi RPC port", keyboard = androidx.compose.ui.text.input.KeyboardType.Number)
                }
            } else item {
                Section("Computer", "Tailscale IP or name of the computer running Wasabi.") {
                    Field(server.host, { server = server.copy(host = it.trim()) }, "Host", keyboard = androidx.compose.ui.text.input.KeyboardType.Uri)
                    Divider(); Field(server.port.toString(), { v -> v.toIntOrNull()?.let { server = server.copy(port = it) } }, "Port", keyboard = androidx.compose.ui.text.input.KeyboardType.Number)
                }
            }
            item {
                Section("JSON-RPC login", "desktop/wasabi-remote.sh sets all of this up on the computer and writes these values to ~/.config/wasabi-remote/phone.txt.") {
                    Field(server.user, { server = server.copy(user = it) }, "User")
                    Divider(); SecretRow(server.password, { server = server.copy(password = it) }, "Password")
                }
            }
        }
    }
}

@Composable
private fun SecretRow(value: String, onChange: (String) -> Unit, placeholder: String) {
    androidx.compose.material3.TextField(
        value, onChange, Modifier.fillMaxWidth(), placeholder = { Text(placeholder) }, singleLine = true,
        visualTransformation = androidx.compose.ui.text.input.PasswordVisualTransformation(),
        keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = androidx.compose.ui.text.input.KeyboardType.Password, autoCorrectEnabled = false),
        colors = androidx.compose.material3.TextFieldDefaults.colors(focusedContainerColor = Color.Transparent, unfocusedContainerColor = Color.Transparent,
            focusedIndicatorColor = Color.Transparent, unfocusedIndicatorColor = Color.Transparent),
    )
}

// The app's own preferences, for every wallet. They apply right away; the wallet's privacy choice lives on the Privacy
// screen and the link to the computer under Connection.
@Composable
fun SettingsScreen(nav: Nav) {
    val context = androidx.compose.ui.platform.LocalContext.current
    val prefs = remember { context.getSharedPreferences("app", android.content.Context.MODE_PRIVATE) }
    var taproot by remember { mutableStateOf(prefs.getBoolean("taproot", true)) }
    var customFee by remember { mutableStateOf(prefs.getBoolean("customFee", false)) }
    var theme by remember { mutableStateOf(prefs.getString("theme", "wasabi")) }
    Screen("Settings", onBack = { nav.pop() }) { padding ->
        Sections(padding) {
            item {
                Section("Sending and receiving", "Taproot is the newest address type; turn it off to hand out SegWit addresses to senders whose wallets cannot pay Taproot. A custom fee rate adds a sat/vB choice next to the confirmation times.") {
                    LabeledRow("Receive to Taproot addresses") { androidx.compose.material3.Switch(taproot, { taproot = it; prefs.edit().putBoolean("taproot", it).apply() }) }
                    Divider()
                    LabeledRow("Custom fee rate when sending") { androidx.compose.material3.Switch(customFee, { customFee = it; prefs.edit().putBoolean("customFee", it).apply() }) }
                }
            }
            item {
                Section("Appearance", "Black keeps most of the screen pure black, so OLED pixels stay off and use less energy.", card = false) {
                    SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                        listOf("wasabi" to "Wasabi", "black" to "Black").forEachIndexed { i, (key, label) ->
                            SegmentedButton(theme == key, { theme = key; prefs.edit().putString("theme", key).apply() }, SegmentedButtonDefaults.itemShape(i, 2), icon = {}) { Text(label) }
                        }
                    }
                }
            }
        }
    }
}

