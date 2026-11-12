package com.eliterf.kiosk

object RendererRecoveryPolicy {
    fun mayRecreate(isDeviceOwner: Boolean, policyApplied: Boolean, lockTaskLocked: Boolean): Boolean =
        isDeviceOwner && policyApplied && lockTaskLocked
}
