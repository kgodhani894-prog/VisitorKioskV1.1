package com.eliterf.kiosk

/** Gboard private options used only for kiosk editor fields. */
object LockedInputPolicy {
    const val NO_SETTINGS_KEY = "com.eliterf.kiosk.noSettingsKey"
    const val NO_RESIZE = "com.eliterf.kiosk.noResize"
    const val HIDE_COMPANION_WIDGET = "hideCompanionWidget"
    const val NO_MICROPHONE_KEY = "noMicrophoneKey"

    val gboardPrivateImeOptions: List<String> =
        listOf(NO_SETTINGS_KEY, NO_RESIZE, HIDE_COMPANION_WIDGET, NO_MICROPHONE_KEY)
}
