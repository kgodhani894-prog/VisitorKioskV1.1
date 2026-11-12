package com.eliterf.kiosk

import android.content.Context
import android.util.AttributeSet
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.InputMethodManager
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputConnection
import android.view.inputmethod.InputConnectionWrapper
import android.webkit.WebView

/** Supplies Gboard with field-level kiosk hints without replacing the keyboard. */
class LockedInputWebView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : WebView(context, attrs, defStyleAttr) {
    init {
        isFocusable = true
        isFocusableInTouchMode = true
        descendantFocusability = ViewGroup.FOCUS_AFTER_DESCENDANTS
    }

    private var keyboardRequest: Runnable? = null

    private fun requestKeyboardWhenServed() {
        keyboardRequest?.let(::removeCallbacks)
        keyboardRequest = Runnable {
            if (!isShown || windowToken == null) return@Runnable
            getContext().getSystemService(InputMethodManager::class.java)
                ?.showSoftInput(this, InputMethodManager.SHOW_IMPLICIT)
        }.also { postDelayed(it, 100L) }
    }

    private fun refreshInputConnection() {
        val inputMethodManager = getContext().getSystemService(InputMethodManager::class.java)
        inputMethodManager?.restartInput(this)
        requestKeyboardWhenServed()
    }

    private fun hideKeyboardForNonEditorTap() {
        keyboardRequest?.let(::removeCallbacks)
        keyboardRequest = null
        getContext().getSystemService(InputMethodManager::class.java)
            ?.hideSoftInputFromWindow(windowToken, 0)
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        val handled = super.onTouchEvent(event)
        if (event.actionMasked == MotionEvent.ACTION_UP) {
            val density = resources.displayMetrics.density.coerceAtLeast(1f)
            val x = event.x / density
            val y = event.y / density
            evaluateJavascript(
                """(() => { const element = document.elementFromPoint($x, $y); return !!element?.closest('input, textarea, [contenteditable=\"true\"]'); })()"""
            ) { result ->
                if (result == "true") {
                    // WebView can focus an HTML editor without asking Android to show the IME.
                    // Request it after WebView finishes dispatching the touch so the field is active.
                    requestFocus(View.FOCUS_DOWN)
                    refreshInputConnection()
                } else {
                    // Buttons, selectors, links, labels, and blank space should expose the form.
                    hideKeyboardForNonEditorTap()
                }
            }
        }
        return handled
    }

    override fun onCreateInputConnection(outAttrs: EditorInfo): InputConnection? {
        val inputConnection = super.onCreateInputConnection(outAttrs)
        if (inputConnection != null) requestKeyboardWhenServed()
        val existing = outAttrs.privateImeOptions
            ?.split(',')
            ?.filter { it.isNotBlank() }
            .orEmpty()
        outAttrs.privateImeOptions =
            (existing + LockedInputPolicy.gboardPrivateImeOptions).distinct().joinToString(",")
        if (inputConnection == null) return null
        return object : InputConnectionWrapper(inputConnection, true) {
            override fun performEditorAction(actionCode: Int): Boolean {
                if (actionCode == EditorInfo.IME_ACTION_NEXT || actionCode == EditorInfo.IME_ACTION_GO) {
                    post {
                        evaluateJavascript(
                            """(() => { const active = document.activeElement; if (active?.name !== 'company') return false; const picker = document.getElementById('employeePicker'); if (!picker) return false; picker.focus(); return true; })()"""
                        ) { handled ->
                            if (handled != "true") inputConnection.performEditorAction(actionCode)
                        }
                    }
                    return true
                }
                return super.performEditorAction(actionCode)
            }
        }
    }
}
