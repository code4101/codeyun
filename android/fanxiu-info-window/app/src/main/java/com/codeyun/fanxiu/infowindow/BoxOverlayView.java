package com.codeyun.fanxiu.infowindow;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.view.View;

final class BoxOverlayView extends View {
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);

    BoxOverlayView(Context context) {
        super(context);
        paint.setColor(Color.WHITE);
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeWidth(dp(2));
    }

    @Override
    protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        float inset = paint.getStrokeWidth() / 2.0f;
        canvas.drawRect(inset, inset, getWidth() - inset, getHeight() - inset, paint);
    }

    private float dp(int value) {
        return value * getResources().getDisplayMetrics().density;
    }
}
