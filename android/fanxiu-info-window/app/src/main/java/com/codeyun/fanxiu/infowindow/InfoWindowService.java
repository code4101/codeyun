package com.codeyun.fanxiu.infowindow;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.PixelFormat;
import android.os.Build;
import android.os.IBinder;
import android.provider.Settings;
import android.util.DisplayMetrics;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.widget.TextView;

import java.util.ArrayList;
import java.util.List;

public final class InfoWindowService extends Service {
    private static final String CHANNEL_ID = "fanxiu_info_window";
    private static final int NOTIFICATION_ID = 4101;
    private static final String EXTRA_TEXT = "text";
    private static final String EXTRA_BOXES = "boxes";
    private static final String EXTRA_FRAME_WIDTH = "frame_width";
    private static final String EXTRA_FRAME_HEIGHT = "frame_height";
    private static final float TOUCH_THROUGH_ALPHA = 0.79f;

    private WindowManager windowManager;
    private TextView textView;
    private final List<View> boxViews = new ArrayList<>();

    public static void start(Context context, String text) {
        start(context, text, null, 0, 0);
    }

    public static void start(
        Context context,
        String text,
        float[] boxes,
        int frameWidth,
        int frameHeight
    ) {
        Intent intent = new Intent(context, InfoWindowService.class);
        intent.putExtra(EXTRA_TEXT, text);
        intent.putExtra(EXTRA_BOXES, boxes);
        intent.putExtra(EXTRA_FRAME_WIDTH, frameWidth);
        intent.putExtra(EXTRA_FRAME_HEIGHT, frameHeight);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.startForegroundService(intent);
        } else {
            context.startService(intent);
        }
    }

    @Override
    public void onCreate() {
        super.onCreate();
        createNotificationChannel();
        startForeground(NOTIFICATION_ID, buildNotification());
        windowManager = (WindowManager) getSystemService(WINDOW_SERVICE);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String text = intent == null ? null : intent.getStringExtra(EXTRA_TEXT);
        float[] boxes = intent == null ? null : intent.getFloatArrayExtra(EXTRA_BOXES);
        int frameWidth = intent == null ? 0 : intent.getIntExtra(EXTRA_FRAME_WIDTH, 0);
        int frameHeight = intent == null ? 0 : intent.getIntExtra(EXTRA_FRAME_HEIGHT, 0);
        updateOverlay(text, boxes, frameWidth, frameHeight);
        return START_STICKY;
    }

    private void updateOverlay(String text, float[] boxes, int frameWidth, int frameHeight) {
        if (!Settings.canDrawOverlays(this)) {
            return;
        }
        if (textView == null) {
            textView = new TextView(this);
            textView.setTextColor(Color.WHITE);
            textView.setTextSize(15);
            textView.setBackgroundColor(Color.TRANSPARENT);
            WindowManager.LayoutParams textParams = overlayParams(
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.WRAP_CONTENT,
                dp(12),
                dp(12)
            );
            windowManager.addView(textView, textParams);
        }
        textView.setText(text == null || text.trim().isEmpty() ? "场景 unknown" : text.trim());
        replaceBoxViews(boxes, frameWidth, frameHeight);
    }

    private void replaceBoxViews(float[] boxes, int frameWidth, int frameHeight) {
        for (View view : boxViews) {
            windowManager.removeView(view);
        }
        boxViews.clear();
        if (boxes == null || frameWidth <= 0 || frameHeight <= 0) {
            return;
        }
        DisplayMetrics metrics = new DisplayMetrics();
        windowManager.getDefaultDisplay().getRealMetrics(metrics);
        float scaleX = metrics.widthPixels / (float) frameWidth;
        float scaleY = metrics.heightPixels / (float) frameHeight;
        int padding = dp(2);
        for (int index = 0; index + 3 < boxes.length; index += 4) {
            int x = Math.round(boxes[index] * scaleX) - padding;
            int y = Math.round(boxes[index + 1] * scaleY) - padding;
            int width = Math.max(1, Math.round(boxes[index + 2] * scaleX) + padding * 2);
            int height = Math.max(1, Math.round(boxes[index + 3] * scaleY) + padding * 2);
            BoxOverlayView view = new BoxOverlayView(this);
            windowManager.addView(view, overlayParams(width, height, x, y));
            boxViews.add(view);
        }
    }

    private WindowManager.LayoutParams overlayParams(int width, int height, int x, int y) {
        WindowManager.LayoutParams params = new WindowManager.LayoutParams(
            width,
            height,
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
                : WindowManager.LayoutParams.TYPE_PHONE,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                | WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE
                | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT
        );
        params.gravity = Gravity.TOP | Gravity.START;
        params.x = x;
        params.y = y;
        params.alpha = TOUCH_THROUGH_ALPHA;
        return params;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationChannel channel = new NotificationChannel(
            CHANNEL_ID,
            "凡修信息窗",
            NotificationManager.IMPORTANCE_LOW
        );
        channel.setDescription("保持凡修场景信息窗运行");
        NotificationManager manager = getSystemService(NotificationManager.class);
        manager.createNotificationChannel(channel);
    }

    private Notification buildNotification() {
        Intent openIntent = new Intent(this, MainActivity.class);
        int pendingFlags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            pendingFlags |= PendingIntent.FLAG_IMMUTABLE;
        }
        PendingIntent pendingIntent = PendingIntent.getActivity(this, 0, openIntent, pendingFlags);
        Notification.Builder builder = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
            ? new Notification.Builder(this, CHANNEL_ID)
            : new Notification.Builder(this);
        return builder
            .setContentTitle("凡修信息窗")
            .setContentText("正在显示实时场景识别结果")
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setOngoing(true)
            .setContentIntent(pendingIntent)
            .build();
    }

    @Override
    public void onDestroy() {
        if (textView != null && windowManager != null) {
            windowManager.removeView(textView);
            textView = null;
        }
        if (windowManager != null) {
            for (View view : boxViews) {
                windowManager.removeView(view);
            }
            boxViews.clear();
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
