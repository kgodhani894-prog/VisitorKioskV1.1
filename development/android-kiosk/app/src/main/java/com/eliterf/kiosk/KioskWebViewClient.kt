package com.eliterf.kiosk

import android.graphics.Bitmap
import android.net.Uri
import android.webkit.SslErrorHandler
import android.net.http.SslError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import android.webkit.RenderProcessGoneDetail
import java.io.ByteArrayInputStream

class KioskWebViewClient(
    private val settings: KioskSettings,
    private val onFailure: (KioskFailure) -> Unit,
    private val onRendererGone: (WebView) -> Unit,
    private val onPageReady: (WebView) -> Unit
) : WebViewClient() {
    private var terminalFailure = false

    private fun allowed(uri: Uri): Boolean {
        val origin = settings.approvedOrigin() ?: return false
        return KioskUrlPolicy.allowedUrl(uri.toString(), origin)
    }

    override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
        if (request.isForMainFrame && !allowed(request.url)) {
            terminalFailure = true
            onFailure(KioskFailure.UNAPPROVED_NAVIGATION)
        }
        return !allowed(request.url)
    }

    override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? {
        if (!allowed(request.url)) {
            return WebResourceResponse("text/plain", "UTF-8", 403, "Forbidden", emptyMap(), ByteArrayInputStream(ByteArray(0)))
        }
        return super.shouldInterceptRequest(view, request)
    }

    override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) {
        terminalFailure = true
        handler.cancel()
        view.stopLoading()
        onFailure(KioskFailure.CERTIFICATE_ERROR)
    }

    override fun onReceivedError(view: WebView, request: WebResourceRequest, error: android.webkit.WebResourceError) {
        if (!request.isForMainFrame) return
        terminalFailure = true
        val failure = if (
            error.errorCode == ERROR_HOST_LOOKUP ||
            error.errorCode == ERROR_CONNECT ||
            error.errorCode == ERROR_TIMEOUT
        ) KioskFailure.NETWORK_UNAVAILABLE else KioskFailure.SERVER_UNAVAILABLE
        onFailure(failure)
    }

    override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
        onRendererGone(view)
        return true
    }

    override fun onPageStarted(view: WebView, url: String, favicon: Bitmap?) {
        if (allowed(Uri.parse(url))) {
            terminalFailure = false
        } else {
            terminalFailure = true
            view.stopLoading()
            onFailure(KioskFailure.UNAPPROVED_NAVIGATION)
        }
    }

    override fun onPageFinished(view: WebView, url: String) {
        if (!terminalFailure && allowed(Uri.parse(url))) onPageReady(view)
    }
}
