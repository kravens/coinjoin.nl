package nl.kravens.wasabipreview

import android.Manifest
import android.content.ClipboardManager
import android.content.Context
import android.content.pm.PackageManager
import android.view.HapticFeedbackConstants
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import com.google.zxing.BarcodeFormat
import com.google.zxing.BinaryBitmap
import com.google.zxing.DecodeHintType
import com.google.zxing.MultiFormatReader
import com.google.zxing.PlanarYUVLuminanceSource
import com.google.zxing.common.HybridBinarizer
import java.util.concurrent.Executors

// Scan to pay: the camera when it is allowed, the explanation otherwise; Paste and Close in both cases. Only a payable
// on-chain payment is handed back; anything else is explained in place and scanning goes on. ZXing decodes on the phone,
// so nothing (no Google service) sees the frames.
@Composable
fun ScannerScreen(network: Bitcoin.Network?, onClose: () -> Unit, found: (Bitcoin.Payment) -> Unit) {
    val context = LocalContext.current
    val view = LocalView.current
    var problem by remember { mutableStateOf<String?>(null) }
    var done by remember { mutableStateOf(false) }
    var allowed by remember { mutableStateOf(ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) }
    val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { allowed = it }
    LaunchedEffect(Unit) { if (!allowed) ask.launch(Manifest.permission.CAMERA) }

    fun accept(text: String) {
        if (done) return
        try {
            val payment = Bitcoin.parse(text, network)
            done = true
            view.performHapticFeedback(HapticFeedbackConstants.CONFIRM)
            found(payment)
        } catch (e: Bitcoin.ParseError) {
            problem = e.message
        }
    }

    Screen("Scan to pay", onBack = null, onClose = onClose, bottom = {
        Column(Modifier.fillMaxWidth().navigationBarsPadding().padding(16.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(14.dp)) {
            problem?.let {
                Row(Modifier.glass(LocalPalette.current, RoundedCornerShape(14.dp), sources = emptyList()).padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                    Icon(WasabiIcon.warning, 14.dp, Wasabi.error); Spacer(Modifier.width(8.dp)); Text(it, color = Wasabi.error)
                }
            }
            Button({
                val clip = (context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager).primaryClip
                accept(clip?.getItemAt(0)?.coerceToText(context)?.toString() ?: "")
            }, shape = RoundedCornerShape(24.dp), colors = ButtonDefaults.buttonColors(containerColor = LocalPalette.current.accent)) {
                Icon(WasabiIcon.paste, 18.dp, Color.White); Spacer(Modifier.width(8.dp)); Text("Paste", color = Color.White)
            }
        }
    }) { padding ->
        Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
            if (allowed) Camera { accept(it) }
            else Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.padding(24.dp)) {
                Text("Camera unavailable")
                Secondary("Allow the camera to scan, or paste the address or payment link instead.")
            }
        }
    }
}

@Composable
private fun Camera(found: (String) -> Unit) {
    val context = LocalContext.current
    val owner = LocalLifecycleOwner.current
    val executor = remember { Executors.newSingleThreadExecutor() }
    val reader = remember { MultiFormatReader().apply { setHints(mapOf(DecodeHintType.POSSIBLE_FORMATS to listOf(BarcodeFormat.QR_CODE))) } }
    DisposableEffect(Unit) { onDispose { executor.shutdown() } }
    AndroidView({ ctx ->
        PreviewView(ctx).also { previewView ->
            val future = ProcessCameraProvider.getInstance(ctx)
            future.addListener({
                val provider = future.get()
                val preview = Preview.Builder().build().also { it.surfaceProvider = previewView.surfaceProvider }
                val analysis = ImageAnalysis.Builder().setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST).build()
                analysis.setAnalyzer(executor) { image ->
                    // The Y plane alone is a greyscale frame, which is all a QR decoder needs.
                    val plane = image.planes[0]
                    val bytes = ByteArray(plane.buffer.remaining()).also { plane.buffer.get(it) }
                    val source = PlanarYUVLuminanceSource(bytes, plane.rowStride, image.height, 0, 0, image.width, image.height, false)
                    val text = runCatching { reader.decodeWithState(BinaryBitmap(HybridBinarizer(source))).text }.getOrNull()
                    reader.reset()
                    image.close()
                    if (text != null) ContextCompat.getMainExecutor(ctx).execute { found(text) }
                }
                provider.unbindAll()
                provider.bindToLifecycle(owner, CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis)
            }, ContextCompat.getMainExecutor(ctx))
        }
    }, Modifier.fillMaxSize().clip(RoundedCornerShape(20.dp)))
}
