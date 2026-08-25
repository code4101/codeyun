package com.codeyun.fanxiu.infowindow;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.provider.Settings;
import android.view.Gravity;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

public final class MainActivity extends Activity {
    private static final int OVERLAY_PERMISSION_REQUEST = 1001;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER);
        root.setPadding(48, 48, 48, 48);

        TextView title = new TextView(this);
        title.setText("凡修信息窗");
        title.setTextSize(26);
        title.setTextColor(Color.rgb(32, 36, 48));
        title.setGravity(Gravity.CENTER);
        root.addView(title);

        TextView description = new TextView(this);
        description.setText("在游戏左上角显示凡修识别到的实时场景编号。\n信息窗不拦截触摸，不执行游戏操作。");
        description.setTextSize(16);
        description.setTextColor(Color.rgb(80, 86, 102));
        description.setGravity(Gravity.CENTER);
        description.setPadding(0, 28, 0, 28);
        root.addView(description);

        Button enable = new Button(this);
        enable.setText("开启信息窗");
        enable.setOnClickListener(view -> ensureOverlayPermissionAndStart());
        root.addView(enable);
        setContentView(root);
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (Settings.canDrawOverlays(this)) {
            InfoWindowService.start(this, "场景 unknown");
        }
    }

    private void ensureOverlayPermissionAndStart() {
        if (Settings.canDrawOverlays(this)) {
            InfoWindowService.start(this, "场景 unknown");
            return;
        }
        Intent intent = new Intent(
            Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
            Uri.parse("package:" + getPackageName())
        );
        startActivityForResult(intent, OVERLAY_PERMISSION_REQUEST);
    }
}
