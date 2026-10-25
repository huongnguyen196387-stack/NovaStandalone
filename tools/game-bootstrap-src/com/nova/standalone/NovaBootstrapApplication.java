package com.nova.standalone;

import android.app.Application;
import android.util.Log;

/**
 * Game-process bootstrap. The target manifest currently uses the framework's
 * plain android.app.Application, so calling super preserves its behavior.
 * This class lives in an added DEX; the original Minecraft DEX files stay byte-for-byte unchanged.
 */
public final class NovaBootstrapApplication extends Application {
    private static final String TAG = "NovaBootstrap";

    @Override
    public void onCreate() {
        super.onCreate();
        try {
            NativeBootstrap.install();
        } catch (Throwable error) {
            // Native loading is optional for the game. Do not crash Minecraft if it fails.
            Log.e(TAG, "Game-side native bootstrap failed", error);
        }
    }
}

final class NativeBootstrap {
    private static final String TAG = "NovaBootstrap";

    private NativeBootstrap() {}

    static void install() {
        try {
            System.loadLibrary("nova_core");
            startWatcherNative();
            Log.i(TAG, "libnova_core.so loaded in Minecraft process; watcher started");
        } catch (Throwable error) {
            Log.e(TAG, "Failed to load libnova_core.so or start watcher", error);
        }
    }

    private static native void startWatcherNative();
}
