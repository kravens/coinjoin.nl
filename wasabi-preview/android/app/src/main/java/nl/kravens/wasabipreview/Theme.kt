package nl.kravens.wasabipreview

import android.os.Build
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.layer.GraphicsLayer
import androidx.compose.ui.graphics.layer.drawLayer
import androidx.compose.ui.graphics.rememberGraphicsLayer
import androidx.compose.ui.graphics.BlurEffect
import androidx.compose.ui.graphics.TileMode
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.graphics.drawscope.translate
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.positionInRoot
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.ui.semantics.Role
import androidx.compose.foundation.border
import androidx.compose.ui.draw.BlurredEdgeTreatment
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.Shape
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.PersistableBundle
import android.view.HapticFeedbackConstants
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.blur
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asAndroidPath
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathFillType
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.vector.PathParser
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay

// Desktop Wasabi's palette, WalletWasabi.Fluent/Styles/Themes/{Base,Dark,Light}.axaml.
object Wasabi {
    val accentDark = Color(0xFF29903B)
    val accentLight = Color(0xFF1AB248) // SystemAccentColor
    val positive = Color(0xFF58A553) // AmountControl "+" run
    val negative = Color(0xFFB43434) // AmountControl "-" run
    val warning = Color(0xFFDDA73E) // WarningMessageForeground
    val error = Color(0xFFDD4343) // DismissColor
    val orangeCoin = Color(0xFFF2994A) // OrangeCoinColor, also the excluded-coin indicator
    val logoGradient = Brush.linearGradient(listOf(Color(0xFF00FF4D), Color(0xFF17C04A)), start = Offset(Float.POSITIVE_INFINITY, 0f), end = Offset(0f, Float.POSITIVE_INFINITY))
}

// Wasabi: the desktop look over a green glow. Black: OLED-friendly, pure black everywhere it can be, so most pixels stay off.
enum class AppTheme { WASABI, BLACK }

data class Palette(
    val dark: Boolean,
    val theme: AppTheme,
    val accent: Color,
    val region: Color, // RegionColor, the window background
    val tile: Color, // TileRegionColor
    val privacyStrong: Color, // PrivacyLevelStrongBrush
    val privacyMedium: Color, // PrivacyLevelMediumBrush
    val privacyNone: Color, // PrivacyLevelNoneBrush
    val tor: Color, // Tor Project purple, lightened on dark for contrast
    val primary: Color,
    val secondary: Color,
    val tertiary: Color,
) {
    val window get() = if (theme == AppTheme.BLACK) Color.Black else region
    val card get() = if (theme == AppTheme.BLACK) Color(0xFF0E0E0E) else tile.copy(alpha = 0.72f)
    val tailscale get() = accent // Tailscale's mark is monochrome; Wasabi green says "link is up"
}

val LocalPalette = compositionLocalOf { palette(false, AppTheme.WASABI) }

fun palette(darkSystem: Boolean, theme: AppTheme): Palette {
    val dark = darkSystem || theme == AppTheme.BLACK
    return Palette(
        dark, theme,
        accent = if (dark) Wasabi.accentDark else Wasabi.accentLight,
        region = if (dark) Color(0xFF1F1F1F) else Color.White,
        tile = if (dark) Color(0xFF262626) else Color(0xFFEFEFEF),
        privacyStrong = if (dark) Color(0xFF5A8F4B) else Color(0xFF7BA170),
        privacyMedium = if (dark) Color(0xFF536A4B) else Color(0xFFB0C7A8),
        privacyNone = if (dark) Color(0xFF383838) else Color(0xFFE0E0E0),
        tor = if (dark) Color(0xFFB58BD8) else Color(0xFF7D4698),
        primary = if (dark) Color.White else Color.Black,
        secondary = if (dark) Color(0x99EBEBF5) else Color(0x993C3C43),
        tertiary = if (dark) Color(0x4DEBEBF5) else Color(0x4D3C3C43),
    )
}

@Composable
fun WasabiTheme(theme: AppTheme, content: @Composable () -> Unit) {
    val p = palette(isSystemInDarkTheme(), theme)
    val scheme = if (p.dark) {
        darkColorScheme(primary = p.accent, onPrimary = Color.White, background = p.window, surface = p.window, onSurface = p.primary, onBackground = p.primary,
            surfaceVariant = p.card, onSurfaceVariant = p.secondary, secondary = p.accent, error = Wasabi.error,
            surfaceContainer = if (theme == AppTheme.BLACK) Color(0xFF0E0E0E) else Color(0xFF262626),
            surfaceContainerHigh = if (theme == AppTheme.BLACK) Color(0xFF141414) else Color(0xFF2C2C2C),
            surfaceContainerLow = if (theme == AppTheme.BLACK) Color.Black else Color(0xFF222222))
    } else {
        lightColorScheme(primary = p.accent, onPrimary = Color.White, background = p.window, surface = p.window, onSurface = p.primary, onBackground = p.primary,
            surfaceVariant = p.card, onSurfaceVariant = p.secondary, secondary = p.accent, error = Wasabi.error,
            surfaceContainer = Color(0xFFF4F4F4), surfaceContainerHigh = Color(0xFFEDEDED), surfaceContainerLow = Color(0xFFF8F8F8))
    }
    CompositionLocalProvider(LocalPalette provides p) {
        MaterialTheme(colorScheme = scheme, content = content)
    }
}

// SVG / Avalonia path mini-language, parsed by the platform; a leading "F1" means the nonzero rule, the default is even-odd.
class Geometry(data: String) {
    private val nonZero = data.trimStart().startsWith("F1")
    val path: Path = PathParser().parsePathString(data.trimStart().removePrefix("F1").removePrefix("F0")).toPath().apply {
        fillType = if (nonZero) PathFillType.NonZero else PathFillType.EvenOdd
    }
    val bounds: Rect = path.getBounds()

    // The geometry scaled uniformly into `size` and centred, like Avalonia's PathIcon. `canvas` lets several geometries
    // share one frame (the logo mark and its accent square).
    fun fitted(size: Size, canvas: Rect = bounds): Path {
        if (canvas.width <= 0 || canvas.height <= 0) return Path()
        val scale = minOf(size.width / canvas.width, size.height / canvas.height)
        val out = Path().apply { addPath(path) }
        out.asAndroidPath().transform(android.graphics.Matrix().apply {
            setScale(scale, scale)
            postTranslate(size.width / 2 - canvas.center.x * scale, size.height / 2 - canvas.center.y * scale)
        })
        out.fillType = path.fillType
        return out
    }

    companion object {
        private val cache = HashMap<WasabiIcon, Geometry>()
        fun of(icon: WasabiIcon) = cache.getOrPut(icon) { Geometry(icon.path) }
    }
}

// A Wasabi icon as a template: fills with the given colour (the current text colour by default).
@Composable
fun Icon(icon: WasabiIcon, size: Dp = 17.dp, color: Color = LocalPalette.current.primary, modifier: Modifier = Modifier) {
    val geometry = Geometry.of(icon)
    Canvas(modifier.size(size)) { drawPath(geometry.fitted(this.size), color) }
}

// wasabi_logo_dynamic: the mark in the text colour with the accent square in the LogoAccentColor gradient.
@Composable
fun WasabiLogo(modifier: Modifier = Modifier) {
    val mark = Geometry.of(WasabiIcon.logo)
    val accent = Geometry.of(WasabiIcon.logoAccent)
    val canvas = Rect(minOf(mark.bounds.left, accent.bounds.left), minOf(mark.bounds.top, accent.bounds.top), maxOf(mark.bounds.right, accent.bounds.right), maxOf(mark.bounds.bottom, accent.bounds.bottom))
    val color = LocalPalette.current.primary
    Canvas(modifier.aspectRatio(canvas.width / canvas.height).semantics { contentDescription = "Wasabi" }) {
        drawPath(mark.fitted(size, canvas), color)
        drawPath(accent.fitted(size, canvas), Wasabi.logoGradient)
    }
}

// Wasabi's window colour with a soft accent glow in the shape of the logo; plain black on the Black theme.
@Composable
fun Backdrop(content: @Composable BoxScope.() -> Unit) {
    val p = LocalPalette.current
    val source = rememberGlassSource()
    Box(Modifier.fillMaxSize()) {
        Box(Modifier.fillMaxSize().glassSource(source).background(p.window)) {
        if (p.theme == AppTheme.WASABI) {
            val logo = Geometry.of(WasabiIcon.logo)
            // iOS blurs with radius 70; Android turns a blur radius into a sigma of about 0.58x, so 120 here looks the same.
            // Unbounded: the default edge treatment clips the blur to the canvas and leaves a hard-edged rectangle.
            Canvas(Modifier.align(Alignment.TopEnd).offset(x = 120.dp, y = (-80).dp).size(440.dp, 340.dp).blur(120.dp, BlurredEdgeTreatment.Unbounded).alpha(if (p.dark) 0.32f else 0.22f)) {
                drawPath(logo.fitted(size), p.accent)
            }
        }
        }
        CompositionLocalProvider(LocalGlassSources provides listOf(source)) { content() }
    }
}

// Stand-ins for the SF Symbols the iOS app uses: stroked chevrons, the menu bars and the close cross, so they have the
// same weight instead of whatever the font draws for ‹ › ⌄ ≡ ✕.
enum class Glyph { BACK, FORWARD, DOWN, UP, MENU, CLOSE }

@Composable
fun Glyph(glyph: Glyph, size: Dp, color: Color, weight: Dp = 2.dp) {
    Canvas(Modifier.size(size)) {
        val stroke = Stroke(weight.toPx(), cap = StrokeCap.Round, join = StrokeJoin.Round)
        fun line(vararg points: Pair<Float, Float>) = drawPath(
            Path().apply { points.forEachIndexed { i, (x, y) -> if (i == 0) moveTo(x * this@Canvas.size.width, y * this@Canvas.size.height) else lineTo(x * this@Canvas.size.width, y * this@Canvas.size.height) } },
            color, style = stroke,
        )
        when (glyph) {
            Glyph.BACK -> line(0.65f to 0.1f, 0.3f to 0.5f, 0.65f to 0.9f)
            Glyph.FORWARD -> line(0.35f to 0.1f, 0.7f to 0.5f, 0.35f to 0.9f)
            Glyph.DOWN -> line(0.1f to 0.35f, 0.5f to 0.7f, 0.9f to 0.35f)
            Glyph.UP -> line(0.1f to 0.65f, 0.5f to 0.3f, 0.9f to 0.65f)
            Glyph.MENU -> for (y in listOf(0.25f, 0.5f, 0.75f)) line(0.1f to y, 0.9f to y)
            Glyph.CLOSE -> { line(0.15f to 0.15f, 0.85f to 0.85f); line(0.85f to 0.15f, 0.15f to 0.85f) }
        }
    }
}

// Frosted glass, the closest Android gets to iOS Liquid Glass without a shader library: what is behind (the sources
// below: the backdrop, and on Home the transaction list) drawn again inside the shape, blurred, under a translucent tint
// and a rim that is brighter at the top. Blur needs Android 12; older versions get the tint over the glow only.
class GlassSource(val layer: GraphicsLayer) {
    var origin by mutableStateOf(Offset.Zero)
}

val LocalGlassSources = staticCompositionLocalOf<List<GlassSource>> { emptyList() }

@Composable
fun rememberGlassSource(): GlassSource {
    val layer = rememberGraphicsLayer()
    return remember(layer) { GlassSource(layer) }
}

// Draws as usual and also records itself, so glass elsewhere can show it. A source must not contain glass that reads it.
fun Modifier.glassSource(source: GlassSource): Modifier =
    onGloballyPositioned { source.origin = it.positionInRoot() }.drawWithContent {
        source.layer.record { this@drawWithContent.drawContent() }
        drawLayer(source.layer)
    }

@Composable
fun Modifier.glass(p: Palette, shape: Shape, sources: List<GlassSource> = LocalGlassSources.current): Modifier {
    val frosted = rememberGraphicsLayer()
    var origin by remember { mutableStateOf(Offset.Zero) }
    val blur = with(LocalDensity.current) { 24.dp.toPx() }
    val rim = Brush.verticalGradient(listOf(p.primary.copy(alpha = if (p.dark) 0.22f else 0.5f), p.primary.copy(alpha = 0.05f)))
    val tint = if (p.theme == AppTheme.BLACK) p.card else p.tile.copy(alpha = if (p.dark) 0.5f else 0.6f)
    return onGloballyPositioned { origin = it.positionInRoot() }
        .clip(shape)
        .then(
            if (Build.VERSION.SDK_INT < 31 || sources.isEmpty() || tint.alpha == 1f) Modifier
            else Modifier.drawBehind {
                frosted.renderEffect = BlurEffect(blur, blur, TileMode.Clamp)
                frosted.record { for (s in sources) translate(s.origin.x - origin.x, s.origin.y - origin.y) { drawLayer(s.layer) } }
                drawLayer(frosted)
            },
        )
        .background(tint)
        .border(0.75.dp, rim, shape)
}

// The iOS segmented picker: a pill track with the chosen option on a lighter thumb (Material's outlined segmented
// buttons look nothing like it).
@Composable
fun <T> Segmented(options: List<Pair<T, String>>, selected: T, modifier: Modifier = Modifier, onSelect: (T) -> Unit) {
    val p = LocalPalette.current
    Row(modifier.height(36.dp).glass(p, RoundedCornerShape(18.dp)).padding(3.dp).selectableGroup()) {
        for ((value, label) in options) {
            val chosen = value == selected
            Box(
                Modifier.weight(1f).fillMaxHeight().clip(RoundedCornerShape(15.dp)).background(if (chosen) p.primary.copy(alpha = if (p.dark) 0.16f else 0.9f) else Color.Transparent)
                    .selectable(chosen, role = Role.Tab) { onSelect(value) }.padding(horizontal = 10.dp),
                contentAlignment = Alignment.Center,
            ) {
                Text(label, style = MaterialTheme.typography.labelLarge, fontWeight = if (chosen) FontWeight.SemiBold else FontWeight.Normal, maxLines = 1, color = if (chosen && !p.dark) Color.Black else p.primary)
            }
        }
    }
}

// Desktop Wasabi's TileControl: small bold caption, 15 dp corners.
@Composable
fun Tile(title: String? = null, modifier: Modifier = Modifier, centered: Boolean = false, content: @Composable ColumnScope.() -> Unit) {
    val p = LocalPalette.current
    Column(
        modifier.fillMaxWidth().glass(p, RoundedCornerShape(15.dp)).padding(14.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
        horizontalAlignment = if (centered) Alignment.CenterHorizontally else Alignment.Start,
    ) {
        if (title != null) Caption(title, Modifier.fillMaxWidth())
        content()
    }
}

@Composable
fun Caption(text: String, modifier: Modifier = Modifier) {
    Text(text.uppercase(), modifier, style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold), color = LocalPalette.current.secondary, textAlign = TextAlign.Center)
}

@Composable
fun Secondary(text: String, modifier: Modifier = Modifier, style: TextStyle = MaterialTheme.typography.bodySmall, align: TextAlign? = null) {
    Text(text, modifier, style = style, color = LocalPalette.current.secondary, textAlign = align)
}

// Wasabi's AmountControl: leading "0.00…" dimmed, significant digits bold, an optional sign in colour.
@Composable
fun Amount(sats: Long, mirror: Mirror, modifier: Modifier = Modifier, signed: Boolean = false, style: TextStyle = MaterialTheme.typography.bodyLarge) {
    val p = LocalPalette.current
    val text = mirror.btc(Math.abs(sats)) // "0.00123456 BTC" or "*.******** BTC"
    val dim = text.takeWhile { it in "0. " } // nothing is dimmed from 1 BTC up, as on the desktop
    Text(
        buildAnnotatedString {
            if (signed && sats != 0L) withStyle(SpanStyle(color = if (sats > 0) Wasabi.positive else Wasabi.negative)) { append(if (sats > 0) "+" else "-") }
            withStyle(SpanStyle(color = p.secondary)) { append(dim) }
            withStyle(SpanStyle(fontWeight = FontWeight.Bold)) { append(text.drop(dim.length)) }
        },
        modifier, style = style.copy(fontFeatureSettings = "tnum"), color = p.primary, maxLines = 1,
    )
}

// The one primary action of a screen, pinned above the navigation bar so it never scrolls under anything.
@Composable
fun PrimaryAction(title: String, icon: WasabiIcon? = null, enabled: Boolean = true, onClick: () -> Unit) {
    Button(
        onClick, Modifier.fillMaxWidth().padding(horizontal = 16.dp).padding(bottom = 8.dp).navigationBarsPadding().height(52.dp),
        enabled = enabled, shape = RoundedCornerShape(26.dp),
        colors = ButtonDefaults.buttonColors(containerColor = LocalPalette.current.accent, contentColor = Color.White),
    ) {
        if (icon != null) { Icon(icon, 18.dp, if (enabled) Color.White else LocalPalette.current.tertiary); Spacer(Modifier.width(8.dp)) }
        Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
    }
}

fun copy(context: Context, text: String, sensitive: Boolean = false) {
    val clip = ClipData.newPlainText("Wasabi", text)
    if (sensitive) clip.description.extras = PersistableBundle().apply { putBoolean("android.content.extra.IS_SENSITIVE", true) }
    (context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager).setPrimaryClip(clip)
}

// Copy with proof: the icon and label flip to a check for 1.5 s with a confirm haptic.
// The iOS tinted capsule (.bordered with a tint): accent label on a faint accent fill, no outline.
@Composable
fun TintedButton(onClick: () -> Unit, modifier: Modifier = Modifier, content: @Composable RowScope.() -> Unit) {
    val accent = LocalPalette.current.accent
    Button(onClick, modifier, shape = CircleShape, colors = ButtonDefaults.buttonColors(containerColor = accent.copy(alpha = 0.16f), contentColor = accent), content = content)
}

@Composable
fun CopyButton(text: String, title: String = "Copy") {
    val context = LocalContext.current
    val view = LocalView.current
    var copied by remember { mutableStateOf(false) }
    LaunchedEffect(copied) { if (copied) { delay(1500); copied = false } }
    TintedButton({ copy(context, text); copied = true; view.performHapticFeedback(HapticFeedbackConstants.CONFIRM) }) {
        Icon(if (copied) WasabiIcon.check else WasabiIcon.copy, 16.dp, LocalPalette.current.accent)
        Spacer(Modifier.width(6.dp))
        Text(if (copied) "Copied" else title, color = LocalPalette.current.accent)
    }
}

// Tailscale's mark: a 3 by 3 grid of dots with the T (middle row and bottom centre) solid, the rest faded.
@Composable
fun TailscaleMark(color: Color, modifier: Modifier = Modifier) {
    Canvas(modifier) {
        val step = minOf(size.width, size.height) / 3
        for (row in 0 until 3) for (col in 0 until 3) {
            val solid = row == 1 || (row == 2 && col == 1)
            drawCircle(color.copy(alpha = color.alpha * if (solid) 1f else 0.35f), radius = step * 0.38f, center = Offset(col * step + step / 2, row * step + step / 2))
        }
    }
}

// The link mark: onion or Tailscale dots, breathing while connecting or retrying.
@Composable
fun LinkGlyph(onion: Boolean, color: Color, pulsing: Boolean, modifier: Modifier = Modifier) {
    val breath = if (pulsing) {
        rememberInfiniteTransition(label = "breath").animateFloat(0.45f, 1f, infiniteRepeatable(tween(800), RepeatMode.Reverse), label = "breath").value
    } else 1f
    Box(modifier.alpha(breath), contentAlignment = Alignment.Center) {
        if (onion) Icon(WasabiIcon.tor, 20.dp, color) else TailscaleMark(color, Modifier.size(17.dp))
    }
}

// Tor's purple onion while Arti bootstraps (ring fills with progress) or builds the circuit to the onion service.
@Composable
fun OnionLoader(progress: Int?) {
    val p = LocalPalette.current
    val spin = rememberInfiniteTransition(label = "spin").animateFloat(0f, 360f, infiniteRepeatable(tween(1400, easing = LinearEasing)), label = "spin").value
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(44.dp), contentAlignment = Alignment.Center) {
            Canvas(Modifier.size(44.dp).rotate(if (progress == null) spin else 0f)) {
                drawCircle(p.tor.copy(alpha = 0.2f), style = Stroke(3.dp.toPx()))
                val sweep = if (progress != null) 360f * progress / 100 else 100f
                drawArc(p.tor, -90f, sweep, false, style = Stroke(3.dp.toPx(), cap = StrokeCap.Round))
            }
            Icon(WasabiIcon.tor, 22.dp, p.tor)
        }
        Spacer(Modifier.width(14.dp))
        Column {
            Text(progress?.let { "Connecting to Tor, $it%" } ?: "Reaching your computer over Tor")
            Secondary(if (progress == null) "Building a private circuit to the onion service" else "Tor runs inside this app")
        }
    }
}

// First load with nothing to show yet: the purple onion for Tor, the Tailscale mark otherwise.
@Composable
fun ConnectingLoader(transport: Transport, progress: Int?) {
    if (transport == Transport.ONION) { OnionLoader(progress); return }
    Row(verticalAlignment = Alignment.CenterVertically) {
        LinkGlyph(false, LocalPalette.current.tailscale, true, Modifier.size(44.dp))
        Spacer(Modifier.width(14.dp))
        Text("Reaching your computer over Tailscale")
    }
}

// The link light and its facts, at the top of the side menu.
@Composable
fun LinkStatus(mirror: Mirror) {
    val p = LocalPalette.current
    val onion = mirror.server.transport == Transport.ONION
    val name = if (onion) "Tor" else "Tailscale"
    val color = if (mirror.link == Link.FAILED) Color.Gray else if (onion) p.tor else p.tailscale
    val pulsing = mirror.link == Link.CONNECTING || (mirror.link == Link.FAILED && mirror.refreshing)
    val state = when (mirror.link) {
        Link.CONNECTING -> mirror.torProgress?.let { "Connecting, $it%" } ?: "Connecting"
        Link.CONNECTED -> "Connected"
        Link.FAILED -> if (mirror.refreshing) "Timed out, retrying" else "Timed out"
    }
    Column(verticalArrangement = Arrangement.spacedBy(6.dp), modifier = Modifier.semantics { contentDescription = "$name link: $state" }) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            LinkGlyph(onion, color, pulsing, Modifier.size(22.dp))
            Spacer(Modifier.width(10.dp))
            Text(name, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
        }
        Text(state, color = p.secondary)
        mirror.linkError?.let { Secondary(it) }
        mirror.updated?.let { updated ->
            var now by remember { mutableStateOf(java.time.Instant.now()) }
            LaunchedEffect(updated) { while (true) { now = java.time.Instant.now(); delay(1000) } }
            Secondary("Last update ${relative(updated, now)} ago")
        }
    }
}

fun relative(then: java.time.Instant, now: java.time.Instant): String {
    val s = maxOf(0, now.epochSecond - then.epochSecond)
    return when {
        s < 60 -> "$s sec" + if (s == 1L) "" else "s"
        s < 3600 -> "${s / 60} min"
        else -> "${s / 3600} hr"
    }
}

// The desktop privacy bar: neutral track, semi-private + private in the medium green, private on top in the strong green.
@Composable
fun PrivacyBar(private: Long, semi: Long, total: Long, discreet: Boolean) {
    val p = LocalPalette.current
    Canvas(Modifier.fillMaxWidth().height(8.dp)) {
        val r = androidx.compose.ui.geometry.CornerRadius(size.height / 2)
        drawRoundRect(p.privacyNone, cornerRadius = r)
        if (total > 0 && !discreet) {
            drawRoundRect(p.privacyMedium, size = Size(size.width * (semi + private) / total, size.height), cornerRadius = r)
            drawRoundRect(p.privacyStrong, size = Size(size.width * private / total, size.height), cornerRadius = r)
        }
    }
}

// Discreet mode: digits are already starred; on top the text is blurred.
fun Modifier.frosted(on: Boolean): Modifier = if (on) this.blur(3.dp) else this

@Composable
fun Spinner() = CircularProgressIndicator(Modifier.size(36.dp), color = LocalPalette.current.accent, strokeWidth = 3.dp)

