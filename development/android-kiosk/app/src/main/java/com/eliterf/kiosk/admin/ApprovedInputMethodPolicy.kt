package com.eliterf.kiosk.admin

/** Pure rules for the device-owner input-method allow-list. */
object ApprovedInputMethodPolicy {
    const val APPROVED_GBOARD_PACKAGE = "com.google.android.inputmethod.latin"
    const val PREVENT_EXTERNAL_INTENTS_KEY = "prevent_external_intents"
    private const val SETTINGS_ACCESS_POINT_KEY = "config_settings_access_point"
    private const val ACCESS_POINTS_ORDER_KEY = "access_points_order"
    private const val QUICK_SETTINGS_KEY = "enable_quick_settings"
    private const val CUSTOMIZE_ACCESS_POINTS_KEY = "show_access_points_customize_ui"
    private const val SPLIT_TABLET_KEY = "enable_split_keyboard_on_tablet_large"
    private const val AUTO_FLOAT_LANDSCAPE_KEY = "enable_auto_float_keyboard_in_landscape"
    private const val AUTO_FLOAT_FREEFORM_KEY = "enable_auto_float_keyboard_in_freeform"
    private const val AUTO_FLOAT_MULTI_WINDOW_KEY = "enable_auto_float_keyboard_in_multi_window"
    private const val DEFAULT_KEYBOARD_MODE_KEY = "config_default_keyboard_mode"
    private const val DEFAULT_ACCESS_POINTS_COUNT_KEY = "config_default_access_points_num_on_bar"
    private const val VOICE_WIDGET_KEY = "enable_voice_widget"
    private const val CONFIG_ACCESS_POINTS_ORDER_KEY = "config_access_points_order"
    private const val FEATURES_QUICK_SETTINGS_ORDER_KEY = "features_quick_settings_order"
    private const val APPEARANCE_QUICK_SETTINGS_ORDER_KEY = "appearance_quick_settings_order"
    private const val ACCESS_POINTS_PREFERENCE_KEY = "pref_key_access_points_showing_order"

    fun userControlDisabledPackages(): List<String> = listOf(APPROVED_GBOARD_PACKAGE)

    fun gboardManagedPreferenceValues(): Map<String, String> = mapOf(
        ACCESS_POINTS_PREFERENCE_KEY to "sticker;gif_search;translate;clipboard;textediting;share"
    )

    fun gboardManagedIntegerPreferenceValues(): Map<String, Int> = mapOf(
        "access_points_count_on_bar" to 0
    )

    /**
     * Gboard 18.0.5 reads these typed managed flags through RestrictionsManager.
     * They remove its configuration entry points and non-normal tablet modes while
     * leaving ordinary text input and the approved keyboard package unchanged.
     */
    fun gboardManagedRestrictionValues(): Map<String, Any> = mapOf(
        PREVENT_EXTERNAL_INTENTS_KEY to true,
        SETTINGS_ACCESS_POINT_KEY to false,
        QUICK_SETTINGS_KEY to false,
        CUSTOMIZE_ACCESS_POINTS_KEY to false,
        SPLIT_TABLET_KEY to false,
        AUTO_FLOAT_LANDSCAPE_KEY to false,
        AUTO_FLOAT_FREEFORM_KEY to false,
        AUTO_FLOAT_MULTI_WINDOW_KEY to false,
        DEFAULT_KEYBOARD_MODE_KEY to "normal",
        DEFAULT_ACCESS_POINTS_COUNT_KEY to 0,
        VOICE_WIDGET_KEY to false,
        // Keep useful editing tools while removing layout-changing entry points.
        // Gboard uses access_points_order for the active toolbar and appends omitted
        // controls when this value is not managed.
        ACCESS_POINTS_ORDER_KEY to "sticker;gif_search;translate;clipboard;textediting;share",
        CONFIG_ACCESS_POINTS_ORDER_KEY to "sticker;gif_search;translate;clipboard;textediting;share",
        FEATURES_QUICK_SETTINGS_ORDER_KEY to
            "auto_correction,language,emoji_kitchen,emoji_row,emoji_suggestion,sound,vibration,number_row",
        APPEARANCE_QUICK_SETTINGS_ORDER_KEY to
            "theme,emoji_switch_key,punctuation_keys,language_switch_key,font_size,key_shape"
    )

    fun permittedPackages(installedInputMethodPackages: Set<String>): List<String>? =
        if (APPROVED_GBOARD_PACKAGE in installedInputMethodPackages) listOf(APPROVED_GBOARD_PACKAGE) else null

    fun isPermitted(packageName: String): Boolean = packageName == APPROVED_GBOARD_PACKAGE

    fun packageFromInputMethodId(inputMethodId: String?): String? =
        inputMethodId?.substringBefore('/')?.takeIf { it.isNotBlank() }

    fun isApprovedInputMethodId(inputMethodId: String?): Boolean =
        packageFromInputMethodId(inputMethodId) == APPROVED_GBOARD_PACKAGE
}
