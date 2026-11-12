import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val signingProperties = Properties()
val signingPropertiesFile = rootProject.file("keystore.properties")
if (signingPropertiesFile.exists()) {
    signingPropertiesFile.inputStream().use(signingProperties::load)
}

android {
    namespace = "com.eliterf.kiosk"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.eliterf.kiosk"
        minSdk = 29
        targetSdk = 34
        versionCode = 2
        versionName = "1.1.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    signingConfigs {
        if (signingPropertiesFile.exists()) {
            create("release") {
                val storePath = signingProperties.getProperty("storeFile")?.trim().orEmpty()
                val storePass = signingProperties.getProperty("storePassword")?.trim().orEmpty()
                val alias = signingProperties.getProperty("keyAlias")?.trim().orEmpty()
                val keyPass = signingProperties.getProperty("keyPassword")?.trim().orEmpty()
                require(storePath.isNotEmpty() && storePass.isNotEmpty() && alias.isNotEmpty() && keyPass.isNotEmpty()) {
                    "keystore.properties must define storeFile, storePassword, keyAlias, and keyPassword"
                }
                storeFile = file(storePath.replace('\\', '/'))
                storePassword = storePass
                keyAlias = alias
                keyPassword = keyPass
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            if (signingPropertiesFile.exists()) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
        debug {
            versionNameSuffix = "-debug"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-ktx:1.9.2")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.camera:camera-camera2:1.3.4")
    implementation("androidx.camera:camera-lifecycle:1.3.4")
    implementation("androidx.camera:camera-view:1.3.4")
    testImplementation("junit:junit:4.13.2")
}
