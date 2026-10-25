package com.nova.standalone

import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Bundle
import android.view.Gravity
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView

class MainActivity : Activity() {
    private lateinit var status: TextView
    private var hudEnabled = false

    companion object {
        // The patcher builds an isolated game package so the store-installed game can remain installed.
        private const val NOVA_GAME_PACKAGE = "com.mojang.minecraftpe.nova"
        init { System.loadLibrary("nova_core") }
    }

    private external fun nativeGetVersion(): String
    private external fun nativeGetStatus(): String
    private external fun nativeSetHudEnabled(enabled: Boolean): Boolean
    private external fun nativeCanHookMinecraft(): Boolean
    private external fun nativeGetCullingStatus(): String
    private external fun nativeInitializeHooks(): Boolean

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.statusBarColor = Color.rgb(12, 16, 27)
        window.navigationBarColor = Color.rgb(12, 16, 27)

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(28, 42, 28, 28)
            setBackgroundColor(Color.rgb(12, 16, 27))
        }

        val title = TextView(this).apply {
            text = "N O V A"
            textSize = 30f
            setTextColor(Color.rgb(110, 190, 255))
            gravity = Gravity.CENTER
        }
        val subtitle = TextView(this).apply {
            text = "STANDALONE LAUNCHER"
            textSize = 12f
            setTextColor(Color.LTGRAY)
            gravity = Gravity.CENTER
        }
        status = TextView(this).apply {
            textSize = 14f
            setTextColor(Color.WHITE)
            setPadding(0, 28, 0, 28)
            text = nativeGetStatus()
        }

        val hudButton = makeButton("Toggle HUD flag (native)")
        hudButton.setOnClickListener {
            hudEnabled = !hudEnabled
            val ok = nativeSetHudEnabled(hudEnabled)
            status.text = nativeGetStatus() + "\nJNI toggle: " + if (ok) "OK" else "FAILED"
        }

        val checkButton = makeButton("CHECK GAME-SIDE HOOK AVAILABILITY")
        checkButton.setOnClickListener {
            val inGameProcess = nativeCanHookMinecraft()
            status.text = nativeGetStatus() + "\nGame hook available in this process: " +
                if (inGameProcess) "yes" else "no — launcher and Minecraft are separate processes"
        }

        val hookButton = makeButton("INITIALIZE CULLING HOOKS")
        hookButton.setOnClickListener {
            val ok = nativeInitializeHooks()
            status.text = nativeGetStatus() + "\n\n" + nativeGetCullingStatus() +
                "\nInitialize result: " + if (ok) "both installed" else "not installed — this is usually the launcher process"
        }

        val launchButton = makeButton("PLAY MINECRAFT")
        launchButton.setOnClickListener { launchMinecraft() }

        val note = TextView(this).apply {
            text = "Core: ${nativeGetVersion()}\nThis build contains runtime culler signature resolution + pass-through hooks. Install the patched Nova game APK to load the core in the game process; the launcher process itself cannot install the game hooks."
            textSize = 12f
            setTextColor(Color.rgb(175, 185, 202))
            setPadding(0, 24, 0, 0)
        }

        root.addView(title, matchWrap())
        root.addView(subtitle, matchWrap())
        root.addView(status, matchWrap())
        root.addView(hudButton, matchWrap())
        root.addView(checkButton, matchWrap())
        root.addView(hookButton, matchWrap())
        root.addView(launchButton, matchWrap())
        root.addView(note, matchWrap())
        setContentView(root)
    }

    private fun makeButton(label: String) = Button(this).apply {
        text = label
        isAllCaps = false
    }

    private fun matchWrap() = LinearLayout.LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT,
        ViewGroup.LayoutParams.WRAP_CONTENT
    ).apply { bottomMargin = 12 }

    private fun launchMinecraft() {
        val intent = packageManager.getLaunchIntentForPackage(NOVA_GAME_PACKAGE)
        if (intent == null) {
            status.text = "Nova game build not found. Build and install the patched Minecraft APK (package $NOVA_GAME_PACKAGE) first."
            return
        }
        try {
            startActivity(intent)
            status.text = "Launching Nova Minecraft build. Hook status must be checked in the game process via Logcat (NovaBootstrap / NovaCulling)."
        } catch (e: Exception) {
            status.text = "Could not launch Minecraft: ${e.javaClass.simpleName}"
        }
    }
}
