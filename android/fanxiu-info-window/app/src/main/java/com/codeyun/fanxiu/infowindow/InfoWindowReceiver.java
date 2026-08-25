package com.codeyun.fanxiu.infowindow;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public final class InfoWindowReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        int sceneId = intent == null ? -1 : intent.getIntExtra("scene_id", -1);
        float score = intent == null ? 0.0f : intent.getFloatExtra("score", 0.0f);
        float[] boxes = intent == null ? null : intent.getFloatArrayExtra("boxes");
        int frameWidth = intent == null ? 0 : intent.getIntExtra("frame_width", 0);
        int frameHeight = intent == null ? 0 : intent.getIntExtra("frame_height", 0);
        String scene = sceneId >= 0 ? "#" + sceneId : "unknown";
        String text = String.format(java.util.Locale.ROOT, "场景 %s  %.0f%%", scene, score);
        InfoWindowService.start(context, text, boxes, frameWidth, frameHeight);
    }
}
