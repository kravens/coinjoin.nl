package nl.kravens.wasabipreview

import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.LocalContentColor
import androidx.compose.foundation.shape.CircleShape
import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SwipeToDismissBox
import androidx.compose.material3.SwipeToDismissBoxValue
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.material3.rememberDrawerState
import androidx.compose.material3.rememberSwipeToDismissBoxState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.CompositingStrategy
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.launch
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle

// Every screen of the app. The home is always the bottom of the stack; Receive and Send are flows whose Close returns
// home, like the iOS sheets; details (a transaction, a coin, Privacy) push on top.
sealed interface Route {
    data object Home : Route
    data object Privacy : Route
    data class Transaction(val tx: HistoryItem) : Route
    data class Rounds(val rounds: List<HistoryItem>) : Route
    data class CoinDetail(val coin: Coin) : Route
    data object Receive : Route
    data class Send(val step: SendStep) : Route
    data object Connection : Route
    data object Settings : Route
    data object Scanner : Route
}

class Nav(start: List<Route>) {
    val stack = mutableStateListOf<Route>().apply { addAll(start) }
    val top get() = stack.last()
    fun push(route: Route) { stack.add(route) }
    fun pop() { if (stack.size > 1) stack.removeAt(stack.lastIndex) }
    fun home() { while (stack.size > 1) stack.removeAt(stack.lastIndex) } // closes a whole flow
}

@Composable
fun App(mirror: Mirror, theme: AppTheme, link: Bitcoin.Payment?) {
    val start = remember { startRoutes(mirror, link) }
    val nav = remember { Nav(start) }
    var send by remember { mutableStateOf(if (start.any { it is Route.Send }) SendModel(mirror, link ?: demoPayment(mirror)) else null) }
    val drawer = rememberDrawerState(if (mirror.screen == "menu") DrawerValue.Open else DrawerValue.Closed)
    val scope = rememberCoroutineScope()

    fun startSend(payment: Bitcoin.Payment?) {
        send = SendModel(mirror, payment)
        nav.push(Route.Send(if (payment != null && mirror.ready) (if (send!!.amountValid) SendStep.FEE else SendStep.AMOUNT) else SendStep.TO))
    }

    RefreshLoop(mirror)
    BackHandler(nav.stack.size > 1 || drawer.isOpen) { if (drawer.isOpen) scope.launch { drawer.close() } else nav.pop() }
    LaunchedEffect(Unit) { if (mirror.server.user.isEmpty() && !mirror.demo || mirror.screen == "connection") nav.push(Route.Connection) }
    // Decoy relock (the app went to the background): nothing of the real wallet stays open behind the demo.
    LaunchedEffect(mirror.relocks) { if (mirror.relocks > 0) { nav.home(); send = null; drawer.snapTo(DrawerValue.Closed) } }

    Backdrop {
        ModalNavigationDrawer(
            drawerState = drawer,
            gesturesEnabled = nav.top == Route.Home,
            scrimColor = Color.Black.copy(alpha = 0.35f),
            drawerContent = {
                ModalDrawerSheet(
                    Modifier.width(300.dp), drawerShape = RoundedCornerShape(topEnd = 28.dp, bottomEnd = 28.dp),
                    drawerContainerColor = if (LocalPalette.current.theme == AppTheme.BLACK) Color(0xFF0E0E0E) else MaterialTheme.colorScheme.surfaceContainer,
                ) {
                    SideMenu(mirror) { route -> scope.launch { drawer.close() }; nav.push(route) }
                }
            },
        ) {
            AnimatedContent(
                nav.stack.toList(),
                transitionSpec = {
                    val forward = targetState.size >= initialState.size
                    if (forward) slideInHorizontally(tween(260)) { it / 3 } + fadeIn(tween(200)) togetherWith fadeOut(tween(160))
                    else fadeIn(tween(200)) togetherWith slideOutHorizontally(tween(260)) { it / 3 } + fadeOut(tween(200))
                },
                contentKey = { it.size.toString() + it.last().javaClass.simpleName + (it.last() as? Route.Send)?.step },
                label = "nav",
            ) { stack ->
                when (val route = stack.last()) {
                    Route.Home -> Home(mirror, nav, onMenu = { scope.launch { drawer.open() } }, onReceive = { nav.push(Route.Receive) }, onSend = { startSend(null) }, onScan = { nav.push(Route.Scanner) })
                    Route.Privacy -> PrivacyScreen(mirror, nav)
                    is Route.Transaction -> TransactionScreen(mirror, nav, route.tx)
                    is Route.Rounds -> RoundsScreen(mirror, nav, route.rounds)
                    is Route.CoinDetail -> CoinScreen(mirror, nav, route.coin)
                    Route.Receive -> ReceiveScreen(mirror, nav)
                    is Route.Send -> send?.let { SendScreen(mirror, nav, it, route.step) }
                    Route.Connection -> ConnectionScreen(mirror, nav)
                    Route.Settings -> SettingsScreen(nav)
                    Route.Scanner -> ScannerScreen(mirror.network, onClose = { nav.pop() }) { payment -> nav.pop(); startSend(payment) }
                }
            }
        }
        if (mirror.busy && nav.top !is Route.Send) Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Spinner() }
    }
    Alerts(mirror)
}

private fun startRoutes(mirror: Mirror, link: Bitcoin.Payment?): List<Route> {
    val home = listOf<Route>(Route.Home)
    if (link != null) return home + Route.Send(SendStep.TO)
    return home + when (mirror.screen) {
        "privacy" -> listOf(Route.Privacy)
        "transaction" -> mirror.history.maxByOrNull { it.datetime }?.let { listOf(Route.Transaction(it)) } ?: emptyList()
        "rounds" -> HistoryItem.chains(mirror.history).firstOrNull { it.size > 1 }?.let { listOf(Route.Rounds(it)) } ?: emptyList()
        "coin" -> mirror.coins.maxByOrNull { it.anonymityScore }?.let { listOf(Route.CoinDetail(it)) } ?: emptyList()
        "settings" -> listOf(Route.Settings)
        "receive", "receiveLow", "receiveHigh", "receiveAddress" -> listOf(Route.Receive)
        "send", "sendLow", "sendHigh" -> listOf(Route.Send(SendStep.TO))
        "sendAmount", "sendAmountHigh" -> listOf(Route.Send(SendStep.AMOUNT))
        "sendFee" -> listOf(Route.Send(SendStep.FEE))
        "sendReview" -> listOf(Route.Send(SendStep.REVIEW))
        "sendSuccess" -> listOf(Route.Send(SendStep.SUCCESS))
        else -> emptyList()
    }
}

private fun demoPayment(mirror: Mirror) = if (mirror.screen?.startsWith("send") == true && mirror.screen !in setOf("send", "sendLow", "sendHigh")) Bitcoin.Payment(Mirror.DEMO_ADDRESS, 150_000, "Demo") else null

// Polls while the app is in front. Coming back to the app or a network change (Tailscale's tunnel coming up, Wi-Fi to
// mobile data) restarts it at once instead of waiting out a request's timeout on the old path.
@Composable
private fun RefreshLoop(mirror: Mirror) {
    val owner = LocalLifecycleOwner.current
    val context = LocalContext.current
    var resumed by remember { mutableStateOf(owner.lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)) }
    var networkChanges by remember { mutableIntStateOf(0) }
    DisposableEffect(owner) {
        val observer = LifecycleEventObserver { _, _ -> resumed = owner.lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED) }
        owner.lifecycle.addObserver(observer)
        onDispose { owner.lifecycle.removeObserver(observer) }
    }
    LaunchedEffect(Unit) { networkChanges(context).drop(1).collect { networkChanges++ } }
    LaunchedEffect(mirror.server, resumed, networkChanges) {
        if (!resumed) return@LaunchedEffect
        while (true) {
            mirror.refresh()
            delay(if (mirror.torProgress == null) 5000 else 1000)
        }
    }
}

// The phone's default network, each time it changes; the first value is the current one.
private fun networkChanges(context: Context) = callbackFlow {
    val manager = context.getSystemService(ConnectivityManager::class.java)
    val callback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) { trySend(Unit) }
        override fun onLost(network: Network) { trySend(Unit) }
    }
    trySend(Unit)
    manager.registerDefaultNetworkCallback(callback)
    awaitClose { manager.unregisterNetworkCallback(callback) }
}

// Passphrase prompt and notices.
@Composable
private fun Alerts(mirror: Mirror) {
    val scope = rememberCoroutineScope()
    mirror.passphrase?.let { request ->
        var typed by remember { mutableStateOf("") }
        AlertDialog(
            onDismissRequest = { mirror.passphrase = null },
            title = { Text(request.title) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text("The wallet is not logged in on your computer. The passphrase is sent once and not stored.")
                    SecretField(typed, { typed = it }, "Wallet passphrase")
                }
            },
            confirmButton = { TextButton({ val pw = typed; mirror.passphrase = null; scope.launch { mirror.perform(request.title, pw, request.action) } }) { Text("Continue") } },
            dismissButton = { TextButton({ mirror.passphrase = null }) { Text("Cancel") } },
        )
    }
    mirror.notice?.let { notice ->
        AlertDialog(onDismissRequest = { mirror.notice = null }, title = { Text("Wasabi") }, text = { Text(notice) }, confirmButton = { TextButton({ mirror.notice = null }) { Text("OK") } })
    }
}

// A screen with an inline title, a back (or close) control and the backdrop; `bottom` is the pinned primary action.
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun Screen(title: String, onBack: (() -> Unit)?, onClose: (() -> Unit)? = null, actions: @Composable () -> Unit = {}, bottom: @Composable () -> Unit = {}, content: @Composable (PaddingValues) -> Unit) {
    Scaffold(
        containerColor = Color.Transparent,
        contentColor = MaterialTheme.colorScheme.onBackground,
        topBar = {
            CenterAlignedTopAppBar(
                title = { Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                navigationIcon = {
                    if (onBack != null) Box(
                        Modifier.padding(start = 12.dp).size(44.dp).glass(LocalPalette.current, CircleShape).clickable(onClick = onBack).semantics { contentDescription = "Back" },
                        contentAlignment = Alignment.Center,
                    ) { Glyph(Glyph.BACK, 16.dp, LocalContentColor.current) }
                },
                actions = {
                    actions()
                    if (onClose != null) IconButton(onClose, Modifier.semantics { contentDescription = "Close" }) {
                        Box(Modifier.size(44.dp).glass(LocalPalette.current, CircleShape).background(LocalPalette.current.accent.copy(alpha = 0.2f)), contentAlignment = Alignment.Center) { Glyph(Glyph.CLOSE, 14.dp, LocalPalette.current.accent) }
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = Color.Transparent),
            )
        },
        bottomBar = bottom,
        content = content,
    )
}

// A list of grouped rows (the iOS inset-grouped List). On a wide screen (a foldable opened, a tablet, a phone on its
// side) the rows stay at most 600 dp wide, centred, while the backdrop still fills the screen.
@Composable
fun Sections(padding: PaddingValues, content: LazyListScope.() -> Unit) {
    BoxWithConstraints(Modifier.fillMaxSize()) {
        val side = maxOf(16.dp, (maxWidth - 600.dp) / 2)
        LazyColumn(
            Modifier.fillMaxSize(), contentPadding = PaddingValues(start = side, end = side, top = padding.calculateTopPadding() + 4.dp, bottom = padding.calculateBottomPadding() + 24.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp), content = content,
        )
    }
}

// One inset-grouped section: optional header above the card, footer below it.
@Composable
fun Section(header: String? = null, footer: String? = null, card: Boolean = true, content: @Composable () -> Unit) {
    val p = LocalPalette.current
    Column {
        if (header != null) Text(header, Modifier.padding(start = 16.dp, bottom = 6.dp, top = 6.dp), style = MaterialTheme.typography.titleSmall, color = p.secondary)
        if (card) Column(Modifier.fillMaxWidth().glass(p, RoundedCornerShape(15.dp)).padding(horizontal = 16.dp, vertical = 4.dp)) { content() } else content()
        if (footer != null) Secondary(footer, Modifier.padding(start = 16.dp, end = 16.dp, top = 6.dp))
    }
}

@Composable
fun InfoRow(label: String, value: String, mono: Boolean = false) = LabeledRow(label) { Text(value, color = LocalPalette.current.secondary, fontFamily = if (mono) FontFamily.Monospace else null, textAlign = TextAlign.End) }

@Composable
fun LabeledRow(label: String, value: @Composable androidx.compose.foundation.layout.RowScope.() -> Unit) {
    androidx.compose.foundation.layout.Row(Modifier.fillMaxWidth().padding(vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(label, Modifier.weight(1f))
        Spacer(Modifier.width(12.dp))
        value()
    }
}

@Composable
fun Divider() = HorizontalDivider(color = LocalPalette.current.tertiary.copy(alpha = 0.3f))

// Common header: connection error, or the unloaded-wallet state with a Load button. `content` shows once the wallet is ready.
@Composable
fun WalletGate(mirror: Mirror, content: @Composable () -> Unit) {
    val scope = rememberCoroutineScope()
    val error = mirror.error ?: (if (mirror.status == null || !mirror.walletLoaded) mirror.linkError else null)
    when {
        (mirror.status == null || !mirror.walletLoaded) && mirror.error == null && mirror.linkError == null -> ConnectingLoader(mirror.server.transport, mirror.torProgress)
        error != null -> androidx.compose.foundation.layout.Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(WasabiIcon.warning, 17.dp, Wasabi.error); Spacer(Modifier.width(8.dp)); Text(error, color = Wasabi.error)
        }
        mirror.walletError != null && mirror.wallet != null -> Column {
            Secondary(mirror.walletError!!, style = MaterialTheme.typography.bodyMedium)
            if (mirror.walletNotLoaded) TextButton({ scope.launch { mirror.perform("Load wallet") { mirror.rpc.call("loadwallet", jsonArray(mirror.wallet)); null } } }) { Text("Load ${mirror.wallet} on the computer") }
        }
        else -> content()
    }
}

val dateTime: DateTimeFormatter = DateTimeFormatter.ofLocalizedDateTime(FormatStyle.MEDIUM, FormatStyle.SHORT).withZone(ZoneId.systemDefault())
val longDateTime: DateTimeFormatter = DateTimeFormatter.ofLocalizedDateTime(FormatStyle.LONG, FormatStyle.SHORT).withZone(ZoneId.systemDefault())
val dayMonth: DateTimeFormatter = DateTimeFormatter.ofPattern("d MMM").withZone(ZoneId.systemDefault())

// The home is the wallet: one card with its name in the caption, the balance, the coins folded behind it and the privacy
// state, then a Transactions card under it (bitcoin.design: balance first).
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun Home(mirror: Mirror, nav: Nav, onMenu: () -> Unit, onReceive: () -> Unit, onSend: () -> Unit, onScan: () -> Unit) {
    val p = LocalPalette.current
    val scope = rememberCoroutineScope()
    var showCoins by remember { mutableStateOf(mirror.screen == "coins" || mirror.screen == "coin") }
    var refreshing by remember { mutableStateOf(false) }
    val list = rememberGlassSource()
    val side = rememberGlassSource()
    // A foldable opened (or a phone on its side): transactions on the left, balance and privacy on the right, each
    // scrolling on its own, as on the iPhone Duo. Below 640 dp the single stacked list stays.
    val wide = LocalConfiguration.current.screenWidthDp >= 640
    Scaffold(
        containerColor = Color.Transparent,
        contentColor = MaterialTheme.colorScheme.onBackground,
        topBar = {
            TopAppBar(
                title = { WalletMenu(mirror) },
                navigationIcon = {
                    Box(
                        Modifier.padding(start = 12.dp).size(44.dp).glass(p, CircleShape).clickable(onClick = onMenu)
                            .semantics { contentDescription = if (mirror.link == Link.FAILED) "Menu, link timed out" else "Menu" },
                        contentAlignment = Alignment.Center,
                    ) {
                        Glyph(Glyph.MENU, 20.dp, p.accent)
                        if (mirror.link == Link.FAILED) Box(Modifier.align(Alignment.TopEnd).padding(9.dp).size(8.dp).clip(CircleShape).background(Color.Gray))
                    }
                },
                actions = {
                    androidx.compose.foundation.layout.Row(Modifier.padding(end = 12.dp).glass(p, RoundedCornerShape(22.dp))) {
                    IconButton(onScan, Modifier.semantics { contentDescription = "Scan to pay" }) { Icon(WasabiIcon.qr, 22.dp, p.accent) }
                    IconButton({ mirror.toggleDiscreet() }, Modifier.semantics { contentDescription = if (mirror.discreet) "Show amounts" else "Discreet mode" }) {
                        Icon(if (mirror.discreet) WasabiIcon.eyeHide else WasabiIcon.eyeShow, 22.dp, p.accent)
                    }
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = Color.Transparent),
            )
        },
        // Receive and Send float over the list, so their glass shows it blurred as well as the backdrop.
        bottomBar = {
            val sources = LocalGlassSources.current + list + if (wide) listOf(side) else emptyList()
            CompositionLocalProvider(LocalGlassSources provides sources) { ActionBar(mirror.ready, onReceive, onSend) }
        },
    ) { padding ->
        val summary: LazyListScope.() -> Unit = {
            item {
                Column(Modifier.fillMaxWidth().glass(p, RoundedCornerShape(15.dp)).padding(horizontal = 16.dp)) {
                    Balance(mirror, showCoins) { showCoins = !showCoins }
                    if (mirror.ready) {
                        if (showCoins) CoinList(mirror, nav)
                        Divider()
                        PrivacyTile(mirror) { nav.push(Route.Privacy) }
                    }
                }
            }
        }
        val transactions: LazyListScope.() -> Unit = {
            if (mirror.ready) item {
                Column(Modifier.fillMaxWidth().glass(p, RoundedCornerShape(15.dp)).padding(horizontal = 16.dp)) {
                    Caption("Transactions", Modifier.fillMaxWidth().padding(top = 14.dp, bottom = 6.dp))
                    if (mirror.history.isEmpty()) Secondary("No transactions yet", Modifier.padding(vertical = 12.dp), MaterialTheme.typography.bodyLarge)
                    HistoryItem.chains(mirror.history).forEachIndexed { i, chain ->
                        if (i > 0) Divider()
                        HistoryRow(mirror, chain) { if (chain.size > 1) nav.push(Route.Rounds(chain)) else nav.push(Route.Transaction(chain[0])) }
                    }
                }
            }
        }
        @Composable
        fun column(source: GlassSource, modifier: Modifier, start: Dp, end: Dp, content: LazyListScope.() -> Unit) = LazyColumn(
            modifier.fillMaxHeight().glassSource(source),
            contentPadding = PaddingValues(start = start, end = end, top = 6.dp, bottom = padding.calculateBottomPadding() + 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp), content = content,
        )
        PullToRefreshBox(refreshing, { scope.launch { refreshing = true; mirror.refresh(); refreshing = false } }, Modifier.padding(top = padding.calculateTopPadding())) {
            if (wide) Row(Modifier.fillMaxSize()) {
                column(list, Modifier.weight(1f), 16.dp, 6.dp, transactions)
                column(side, Modifier.width(416.dp), 6.dp, 16.dp, summary)
            } else column(list, Modifier.fillMaxWidth(), 16.dp, 16.dp) { summary(); transactions() }
        }
    }
}

// The wallet name is the title; tapping it switches wallets.
@Composable
private fun WalletMenu(mirror: Mirror) {
    var open by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
        androidx.compose.foundation.layout.Row(
            Modifier.clip(RoundedCornerShape(12.dp)).clickable { open = true }.padding(horizontal = 8.dp, vertical = 4.dp).semantics { contentDescription = "Wallet ${mirror.wallet ?: "not chosen"}, switch wallet" },
            verticalAlignment = Alignment.CenterVertically,
        ) {
            WasabiLogo(Modifier.height(17.dp))
            Spacer(Modifier.width(7.dp))
            Text(mirror.wallet ?: "Wasabi", fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.width(6.dp))
            Glyph(Glyph.DOWN, 11.dp, LocalPalette.current.secondary)
        }
        DropdownMenu(open, { open = false }) {
            mirror.wallets.forEach { name ->
                DropdownMenuItem({ Text((if (name == mirror.wallet) "✓  " else "    ") + name) }, { open = false; mirror.selectWallet(name); scope.launch { mirror.refresh() } })
            }
        }
    }
}

// "Balance <wallet>" ties the number to the name above it. Tapping it folds the coins out underneath.
@Composable
private fun Balance(mirror: Mirror, showCoins: Boolean, toggle: () -> Unit) {
    Column(
        Modifier.fillMaxWidth().then(if (mirror.ready) Modifier.clickable(onClick = toggle) else Modifier).padding(vertical = 14.dp),
        horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Caption("Balance ${mirror.wallet ?: ""}")
        WalletGate(mirror) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Amount(mirror.balance, mirror, Modifier.frosted(mirror.discreet), style = MaterialTheme.typography.headlineLarge.copy(fontWeight = FontWeight.Light))
                mirror.usd(mirror.balance)?.let { Secondary(it, Modifier.frosted(mirror.discreet), MaterialTheme.typography.bodyLarge) }
            }
        }
        if (mirror.ready) androidx.compose.foundation.layout.Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(5.dp)) {
            Secondary("${mirror.coins.size} coins", style = MaterialTheme.typography.labelLarge)
            Glyph(if (showCoins) Glyph.UP else Glyph.DOWN, 9.dp, LocalPalette.current.secondary, 1.5.dp)
        }
    }
}

// The accordion: the same coin rows as the desktop list; swipe one away to exclude it from coinjoins, tap for the Coin screen.
@Composable
private fun CoinList(mirror: Mirror, nav: Nav) {
    val p = LocalPalette.current
    mirror.coins.sortedByDescending { it.amount }.forEach { coin ->
        Divider()
        SwipeAction(if (coin.excluded) "Include" else "Exclude", if (coin.excluded) p.accent else Wasabi.orangeCoin, { excludeCoin(mirror, coin) }) {
            CoinRow(mirror, coin) { nav.push(Route.CoinDetail(coin)) }
        }
    }
    Secondary("A green shield has reached the privacy target (${mirror.target}). Tap a coin for details; swipe left to exclude it from coinjoins.", Modifier.padding(vertical = 10.dp))
}

@Composable
fun CoinRow(mirror: Mirror, coin: Coin, onClick: () -> Unit) {
    val p = LocalPalette.current
    val tier = mirror.tier(coin.anonymityScore)
    val score = Math.round(coin.anonymityScore).toString()
    androidx.compose.foundation.layout.Row(Modifier.fillMaxWidth().clickable(onClick = onClick).padding(vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Amount(coin.amount, mirror, Modifier.frosted(mirror.discreet))
            Secondary(if (!coin.label.isNullOrEmpty()) coin.label else mirror.mask(coin.address), Modifier.frosted(mirror.discreet && coin.label.isNullOrEmpty()))
        }
        // Same indicators as the desktop coin list: excluded in coin-orange, pending clock, shield tier by anon score.
        if (coin.excluded) Icon(WasabiIcon.forbidden, 14.dp, Wasabi.orangeCoin, Modifier.padding(end = 6.dp).semantics { contentDescription = "Excluded from coinjoin" })
        if (!coin.confirmed) Icon(WasabiIcon.clock, 15.dp, p.secondary, Modifier.padding(end = 6.dp).semantics { contentDescription = "Pending" })
        Icon(if (tier == Mirror.Tier.PRIVATE) WasabiIcon.shieldTick else WasabiIcon.shield, 16.dp, tierColor(tier))
        Spacer(Modifier.width(4.dp))
        Text(score, color = tierColor(tier), modifier = Modifier.semantics { contentDescription = "Anonymity score $score, ${tier.label}" })
        Spacer(Modifier.width(8.dp)); Glyph(Glyph.FORWARD, 11.dp, p.tertiary)
    }
}

@Composable
fun tierColor(tier: Mirror.Tier) = when (tier) {
    Mirror.Tier.PRIVATE -> LocalPalette.current.privacyStrong
    Mirror.Tier.SEMI -> LocalPalette.current.privacyMedium
    Mirror.Tier.NONE -> LocalPalette.current.secondary
}

fun excludeCoin(mirror: Mirror, coin: Coin): suspend () -> Unit = {
    mirror.perform("Exclude coin") { mirror.rpc.call("excludefromcoinjoin", jsonArray(coin.txid, coin.index, !coin.excluded), mirror.wallet); null }
}

// A row that reveals one action when swiped from right to left (the iOS swipe action), then springs back.
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SwipeAction(title: String, color: Color, action: suspend () -> Unit, content: @Composable () -> Unit) {
    val scope = rememberCoroutineScope()
    val state = rememberSwipeToDismissBoxState()
    LaunchedEffect(state.currentValue) {
        if (state.currentValue == SwipeToDismissBoxValue.EndToStart) { state.reset(); scope.launch { action() } }
    }
    SwipeToDismissBox(state, enableDismissFromStartToEnd = false, backgroundContent = {
        Box(Modifier.fillMaxSize().clip(RoundedCornerShape(10.dp)).background(color).padding(horizontal = 16.dp), contentAlignment = Alignment.CenterEnd) {
            Text(title, color = Color.White, fontWeight = FontWeight.SemiBold)
        }
    }) { Box(Modifier.background(LocalPalette.current.let { if (it.theme == AppTheme.BLACK) Color(0xFF0E0E0E) else lerp(it.window, it.tile, 0.72f) })) { content() } }
}

// The music box in plain words: shield, percentage, what Wasabi is doing, the bar, and the private amount. Opens Privacy.
@Composable
private fun PrivacyTile(mirror: Mirror, open: () -> Unit) {
    val p = LocalPalette.current
    val split = mirror.privacy
    val percentage = mirror.privacyPercentage.coerceIn(0, 100)
    val (text, active) = mirror.coinjoinState
    val shield by animateColorAsState(lerp(p.secondary, p.privacyStrong, percentage / 100f), tween(600), label = "shield")
    Column(Modifier.fillMaxWidth().clickable(onClick = open).padding(vertical = 14.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Box(Modifier.fillMaxWidth()) {
            Caption("Privacy", Modifier.align(Alignment.Center))
            Box(Modifier.align(Alignment.CenterEnd)) { Glyph(Glyph.FORWARD, 12.dp, p.tertiary) }
        }
        androidx.compose.foundation.layout.Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.semantics { contentDescription = "$percentage percent private, $text" }) {
            Icon(WasabiIcon.shieldTick, 34.dp, shield)
            Spacer(Modifier.width(10.dp))
            Text("$percentage%", Modifier.frosted(mirror.discreet), style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
        }
        androidx.compose.foundation.layout.Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(if (active) WasabiIcon.play else WasabiIcon.pause, 11.dp, if (active) p.accent else p.secondary)
            Spacer(Modifier.width(5.dp))
            Text(text, color = if (active) p.accent else p.secondary, style = MaterialTheme.typography.bodyMedium)
        }
        PrivacyBar(split.private, split.semi, mirror.balance, mirror.discreet)
        androidx.compose.foundation.layout.Row(verticalAlignment = Alignment.CenterVertically) {
            Canvas(Modifier.size(8.dp)) { drawCircle(p.privacyStrong) }
            Spacer(Modifier.width(6.dp))
            Secondary("Private ", style = MaterialTheme.typography.bodyMedium)
            Amount(split.private, mirror, Modifier.frosted(mirror.discreet), style = MaterialTheme.typography.bodyMedium)
        }
        mirror.coinjoinHint?.let { Secondary(it, align = TextAlign.Center) }
    }
}

// Transaction row as bitcoin.design describes it: direction icon, label, date, signed amount; tap for details. More than
// one item is a chain of privacy rounds, which opens its own list.
@Composable
fun HistoryRow(mirror: Mirror, items: List<HistoryItem>, onClick: () -> Unit) {
    val p = LocalPalette.current
    val item = items[0]
    val title = if (items.size > 1) "Privacy rounds" else item.title
    val sats = items.sumOf { it.amount }
    val pending = items.any { it.pending }
    val amount = @Composable {
        Amount(sats, mirror, Modifier.frosted(mirror.discreet), signed = true)
        if (pending) Icon(WasabiIcon.clock, 15.dp, p.secondary, Modifier.padding(start = 6.dp).semantics { contentDescription = "Pending" })
    }
    val date = @Composable {
        Text(if (items.size > 1) "${items.size} rounds, ${dayMonth.format(item.datetime)}" else dateTime.format(item.datetime),
            style = MaterialTheme.typography.bodySmall, color = p.secondary, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
    val measurer = rememberTextMeasurer()
    val body = MaterialTheme.typography.bodyLarge
    BoxWithConstraints(Modifier.fillMaxWidth()) {
        // Side by side where title and amount both fit; on a narrow screen (a foldable's cover display) the amount moves
        // under the title instead of cutting "Groceries" down to "Groce...". 22 + 12 + 8 + 8 + 11 dp of icon, gaps, chevron.
        val need = with(LocalDensity.current) {
            (measurer.measure(title, body).size.width + measurer.measure((if (sats < 0) "-" else "+") + mirror.btc(Math.abs(sats)), body.copy(fontWeight = FontWeight.Bold)).size.width).toDp()
        } + 61.dp + if (pending) 21.dp else 0.dp
        val fits = need <= maxWidth
        androidx.compose.foundation.layout.Row(Modifier.fillMaxWidth().clickable(onClick = onClick).padding(vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.width(22.dp)) {
                if (items.size > 1) ShieldStack(16.dp, p.secondary)
                else Icon(if (item.islikelycoinjoin) WasabiIcon.shield else if (item.amount > 0) WasabiIcon.receive else WasabiIcon.send, 16.dp, p.secondary)
            }
            Spacer(Modifier.width(12.dp))
            if (fits) {
                Column(Modifier.weight(1f)) { Text(title, maxLines = 1, overflow = TextOverflow.Ellipsis); date() }
                Spacer(Modifier.width(8.dp))
                amount()
            } else Column(Modifier.weight(1f)) {
                Text(title, maxLines = 1, overflow = TextOverflow.Ellipsis)
                androidx.compose.foundation.layout.Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.weight(1f)) { date() }
                    Spacer(Modifier.width(8.dp))
                    amount()
                }
            }
            Spacer(Modifier.width(8.dp)); Glyph(Glyph.FORWARD, 11.dp, p.tertiary)
        }
    }
}

// Stacked shields for a chain of privacy rounds: each one hides what is behind it, so the outlines don't tangle.
@Composable
fun ShieldStack(size: androidx.compose.ui.unit.Dp, color: Color) {
    val shield = Geometry.of(WasabiIcon.shield)
    val outer = remember { Geometry(WasabiIcon.shield.path.takeWhile { it != 'Z' } + "Z") }
    // Offscreen, so the clear only erases this stack's own earlier shields, not the card under it.
    Canvas(Modifier.size(size * 1.25f, size).graphicsLayer { compositingStrategy = CompositingStrategy.Offscreen }) {
        val s = size.toPx() * 0.875f
        for (layer in 2 downTo 0) {
            val dx = size.toPx() * 0.1875f * layer
            val box = androidx.compose.ui.geometry.Size(s, s)
            val silhouette = outer.fitted(box).apply { translate(androidx.compose.ui.geometry.Offset(dx, (this@Canvas.size.height - s) / 2)) }
            drawPath(silhouette, Color.Black, blendMode = androidx.compose.ui.graphics.BlendMode.Clear)
            val icon = shield.fitted(box).apply { translate(androidx.compose.ui.geometry.Offset(dx, (this@Canvas.size.height - s) / 2)) }
            drawPath(icon, color.copy(alpha = color.alpha * (1 - layer * 0.3f)))
        }
    }
}

// Receive | Send, pinned under the home: the two things a wallet is for.
@Composable
private fun ActionBar(enabled: Boolean, onReceive: () -> Unit, onSend: () -> Unit) {
    val p = LocalPalette.current
    androidx.compose.foundation.layout.Row(
        Modifier.fillMaxWidth().background(androidx.compose.ui.graphics.Brush.verticalGradient(listOf(p.window.copy(alpha = 0f), p.window.copy(alpha = 0.94f)))).navigationBarsPadding().padding(horizontal = 16.dp, vertical = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        for ((title, icon, action) in listOf(Triple("Receive", WasabiIcon.receive, onReceive), Triple("Send", WasabiIcon.send, onSend))) {
            Column(
                Modifier.weight(1f).glass(p, RoundedCornerShape(30.dp)).clickable(enabled, onClick = action).padding(vertical = 10.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Icon(icon, 22.dp, if (enabled) p.accent else p.tertiary)
                Text(title, style = MaterialTheme.typography.labelLarge, fontWeight = FontWeight.SemiBold, color = if (enabled) p.accent else p.tertiary)
            }
        }
    }
}

// The rounds of one chain, each opening its own transaction.
@Composable
private fun RoundsScreen(mirror: Mirror, nav: Nav, rounds: List<HistoryItem>) {
    val total = rounds.sumOf { it.amount }
    Screen("Privacy rounds", onBack = { nav.pop() }) { padding ->
        Sections(padding) {
            item { HeroTile { ShieldStack(30.dp, LocalPalette.current.secondary); Amount(total, mirror, Modifier.frosted(mirror.discreet), signed = true, style = MaterialTheme.typography.headlineMedium.copy(fontWeight = FontWeight.Light)); mirror.usd(Math.abs(total))?.let { Secondary(it, Modifier.frosted(mirror.discreet), MaterialTheme.typography.bodyLarge) } } }
            item {
                Section("${rounds.size} rounds") {
                    rounds.forEachIndexed { i, round -> if (i > 0) Divider(); HistoryRow(mirror, listOf(round)) { nav.push(Route.Transaction(round)) } }
                }
            }
        }
    }
}

@Composable
fun HeroTile(content: @Composable () -> Unit) {
    Column(Modifier.fillMaxWidth().glass(LocalPalette.current, RoundedCornerShape(15.dp)).padding(vertical = 18.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(6.dp)) { content() }
}

// Transaction details: the amount, then the facts, the txid last (bitcoin.design keeps technical details reachable but out of the way).
@Composable
private fun TransactionScreen(mirror: Mirror, nav: Nav, item: HistoryItem) {
    val p = LocalPalette.current
    Screen(item.title, onBack = { nav.pop() }) { padding ->
        Sections(padding) {
            item {
                HeroTile {
                    Icon(if (item.islikelycoinjoin) WasabiIcon.shield else if (item.amount > 0) WasabiIcon.receive else WasabiIcon.send, 30.dp, p.secondary)
                    Amount(item.amount, mirror, Modifier.frosted(mirror.discreet), signed = true, style = MaterialTheme.typography.headlineMedium.copy(fontWeight = FontWeight.Light))
                    mirror.usd(Math.abs(item.amount))?.let { Secondary(it, Modifier.frosted(mirror.discreet), MaterialTheme.typography.bodyLarge) }
                }
            }
            item {
                Section {
                    InfoRow("Type", if (item.islikelycoinjoin) "Coinjoin round" else if (item.amount > 0) "Received" else "Sent")
                    if (!item.label.isNullOrEmpty()) { Divider(); InfoRow("Label", item.label) }
                    Divider(); InfoRow("Date", longDateTime.format(item.datetime))
                    Divider(); InfoRow("Status", if (item.pending) "Pending in the mempool" else "Confirmed in block ${item.height}")
                }
            }
            item {
                Section("Transaction ID") {
                    SelectionContainer { Text(item.tx, Modifier.padding(vertical = 10.dp), fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall) }
                    CopyButton(item.tx, "Copy transaction ID")
                    Spacer(Modifier.height(8.dp))
                }
            }
        }
    }
}

// By scope: the link to the computer on top (tapping it edits the connection), then this wallet's privacy and the app's
// own settings, then what the computer's Wasabi reports, for reference.
@Composable
private fun SideMenu(mirror: Mirror, open: (Route) -> Unit) {
    val p = LocalPalette.current
    val scope = rememberCoroutineScope()
    Column(Modifier.verticalScroll(rememberScrollState()).statusBarsPadding().padding(horizontal = 20.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(18.dp)) {
        androidx.compose.foundation.layout.Row(Modifier.fillMaxWidth().clickable { open(Route.Connection) }, verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.weight(1f)) { LinkStatus(mirror) }
            Glyph(Glyph.FORWARD, 12.dp, p.tertiary)
        }
        if (mirror.link == Link.FAILED) TextButton({ scope.launch { mirror.refresh() } }, enabled = !mirror.refreshing) { Text("Retry now") }
        Divider()
        MenuItem("Privacy", WasabiIcon.shieldTick, mirror.wallet?.let { "$it: ${mirror.privacyStop.label}" }) { open(Route.Privacy) }
        MenuItem("Settings", WasabiIcon.settings, "Addresses, fees, theme") { open(Route.Settings) }
        mirror.status?.let { status ->
            Divider()
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Caption("Wasabi on the computer")
                NodeRow(WasabiIcon.network, "Network", status.network)
                NodeRow(WasabiIcon.tor, "Tor", status.torStatus)
                NodeRow(WasabiIcon.blockHeight, "Block height", status.bestBlockchainHeight)
                if (status.filtersLeft > 0) NodeRow(WasabiIcon.clock, "Filters left", status.filtersLeft.toString())
                NodeRow(WasabiIcon.peers, "Peers", status.connectedPeers.toString())
            }
        }
    }
}

@Composable
private fun MenuItem(title: String, icon: WasabiIcon, detail: String?, onClick: () -> Unit) {
    androidx.compose.foundation.layout.Row(Modifier.fillMaxWidth().clickable(onClick = onClick).padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        Icon(icon, 20.dp)
        Spacer(Modifier.width(14.dp))
        Column {
            Text(title)
            if (detail != null) Secondary(detail)
        }
    }
}

@Composable
private fun NodeRow(icon: WasabiIcon, label: String, value: String) {
    androidx.compose.foundation.layout.Row(verticalAlignment = Alignment.CenterVertically) {
        Icon(icon, 16.dp)
        Spacer(Modifier.width(10.dp))
        Text(label, Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
        Text(value, color = LocalPalette.current.secondary, style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
fun SecretField(value: String, onChange: (String) -> Unit, label: String) {
    OutlinedTextField(value, onChange, label = { Text(label) }, singleLine = true, visualTransformation = androidx.compose.ui.text.input.PasswordVisualTransformation(),
        keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = androidx.compose.ui.text.input.KeyboardType.Password, autoCorrectEnabled = false))
}
