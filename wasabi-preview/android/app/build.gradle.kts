plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "nl.kravens.wasabipreview"
    compileSdk {
        version = release(37) { minorApiLevel = 2 } // Compose 2026.11 needs 37.1+
    }

    defaultConfig {
        applicationId = "nl.kravens.wasabipreview"
        minSdk = 30 // platform BiometricPrompt with a device-credential fallback; every GrapheneOS phone is far newer
        targetSdk = 36
        versionCode = 1
        versionName = "1.0"
        ndk { abiFilters += "arm64-v8a" } // libwasabitor.so (Arti) is built for arm64 only: phones, Waydroid, the Apple-silicon emulator
    }

    // The release key stays outside the repo; build.sh passes its path and password (from the macOS Keychain).
    val keystore = System.getenv("WASABI_KEYSTORE")
    signingConfigs {
        if (keystore != null) create("release") {
            storeFile = file(keystore)
            storePassword = System.getenv("WASABI_KEYSTORE_PASSWORD")
            keyAlias = "wasabi"
            keyPassword = storePassword
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
        }
    }

    buildFeatures { compose = true; buildConfig = true }
    packaging { jniLibs { useLegacyPackaging = false } }
}

dependencies {
    // Deliberately few: Compose, CameraX and ZXing for the QR code. HTTP, JSON, Keystore, biometrics and network
    // monitoring come from the platform, and nothing needs Google Play services, so the app runs on GrapheneOS.
    val compose = platform("androidx.compose:compose-bom:2026.11.01")
    implementation(compose)
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.activity:activity-compose:1.13.0")
    implementation("androidx.camera:camera-camera2:1.7.0")
    implementation("androidx.camera:camera-lifecycle:1.7.0")
    implementation("androidx.camera:camera-view:1.7.0")
    implementation("com.google.zxing:core:3.5.4")
    debugImplementation("androidx.compose.ui:ui-tooling")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20250517")
}
