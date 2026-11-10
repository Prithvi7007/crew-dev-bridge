package fun.cadacrew.roadledger;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import java.util.List;
import java.util.Locale;

public final class Ui {
    private Ui() {}
    public static final int BG=Color.rgb(6,22,32);
    public static final int CARD=Color.rgb(13,39,51);
    public static final int CARD_ALT=Color.rgb(19,49,62);
    public static final int BORDER=Color.rgb(43,78,89);
    public static final int INK=Color.rgb(247,253,252);
    public static final int MUTED=Color.rgb(156,183,193);
    public static final int MINT=Color.rgb(99,240,191);
    public static final int BLUE=Color.rgb(143,208,244);
    public static final int ORANGE=Color.rgb(255,184,119);

    public static int dp(Context c,float n) { return Math.round(n*c.getResources().getDisplayMetrics().density); }
    public static GradientDrawable shape(int color,Context c,int radius) {
        GradientDrawable d=new GradientDrawable();
        d.setColor(color);
        d.setCornerRadius(dp(c,radius));
        return d;
    }
    public static GradientDrawable bordered(Context c) {
        GradientDrawable d=shape(CARD,c,22);
        d.setStroke(dp(c,1),BORDER);
        return d;
    }
    public static LinearLayout col(Context c) {
        LinearLayout l=new LinearLayout(c);
        l.setOrientation(LinearLayout.VERTICAL);
        return l;
    }
    public static LinearLayout row(Context c) {
        LinearLayout l=new LinearLayout(c);
        l.setOrientation(LinearLayout.HORIZONTAL);
        l.setGravity(Gravity.CENTER_VERTICAL);
        return l;
    }
    public static TextView label(Context c,String s,int size,int color,boolean bold) {
        TextView t=new TextView(c);
        t.setText(s);
        t.setTextSize(size);
        t.setTextColor(color);
        t.setIncludeFontPadding(true);
        t.setGravity(Gravity.CENTER_VERTICAL);
        if(bold) t.setTypeface(Typeface.create("sans-serif",Typeface.BOLD));
        return t;
    }
    public static LinearLayout card(Context c) {
        LinearLayout l=col(c);
        l.setPadding(dp(c,18),dp(c,17),dp(c,18),dp(c,18));
        l.setBackground(bordered(c));
        l.setElevation(dp(c,2));
        return l;
    }
    public static Button primary(Context c,String text) {
        Button b=new Button(c);
        b.setText(text);b.setAllCaps(false);b.setTextSize(14);
        b.setTypeface(Typeface.create("sans-serif-medium",Typeface.BOLD));
        b.setTextColor(BG);b.setBackground(shape(MINT,c,15));
        b.setMinHeight(dp(c,46));
        return b;
    }
    public static Button secondary(Context c,String text) {
        Button b=new Button(c);
        b.setText(text);b.setAllCaps(false);b.setTextSize(13);
        b.setTypeface(Typeface.create("sans-serif-medium",Typeface.BOLD));
        b.setTextColor(INK);b.setBackground(shape(CARD_ALT,c,15));
        b.setMinHeight(dp(c,45));
        return b;
    }
    public static View gap(Context c,int height) {
        View v=new View(c);v.setLayoutParams(new LinearLayout.LayoutParams(1,dp(c,height)));
        return v;
    }
    public static LinearLayout.LayoutParams below(Context c,int margin) {
        LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(-1,-2);
        p.topMargin=dp(c,margin);
        return p;
    }
    public static final class EconomyChart extends View {
        private final Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG);
        private final List<Double> values;
        public EconomyChart(Context context,List<Double> points) {
            super(context);this.values=points;
            setMinimumHeight(dp(context,182));
        }
        @Override protected void onMeasure(int widthSpec,int heightSpec) {
            setMeasuredDimension(MeasureSpec.getSize(widthSpec),dp(getContext(),180));
        }
        @Override protected void onDraw(Canvas c) {
            super.onDraw(c);
            float width=getWidth(),height=getHeight();
            float left=dp(getContext(),8),right=width-dp(getContext(),8);
            float top=dp(getContext(),12),base=height-dp(getContext(),35);
            paint.setStyle(Paint.Style.STROKE);paint.setStrokeWidth(dp(getContext(),1));
            paint.setColor(BORDER);
            for(int i=0;i<4;i++) {
                float y=top+(base-top)*i/3f;
                c.drawLine(left,y,right,y,paint);
            }
            paint.setStyle(Paint.Style.FILL);
            if(values.isEmpty()) {
                paint.setColor(MUTED);paint.setTextSize(dp(getContext(),14));paint.setTypeface(Typeface.DEFAULT);
                c.drawText("Two full-tank entries unlock your trend",left,base-dp(getContext(),22),paint);
                return;
            }
            int count=Math.min(6,values.size());
            double max=12;
            for(int i=values.size()-count;i<values.size();i++) max=Math.max(max,values.get(i)*1.15);
            float available=right-left;
            float slot=available/count;
            float barWidth=Math.min(dp(getContext(),33),slot*.55f);
            for(int i=0;i<count;i++) {
                double value=values.get(values.size()-count+i);
                float x=left+slot*(i+.5f);
                float barHeight=(float)(Math.max(0,Math.min(1,value/max))*(base-top));
                paint.setColor(i==count-1?MINT:BLUE);
                c.drawRoundRect(x-barWidth/2f,base-barHeight,x+barWidth/2f,base,dp(getContext(),5),dp(getContext(),5),paint);
                paint.setColor(MUTED);
                paint.setTypeface(Typeface.create("sans-serif",Typeface.NORMAL));
                paint.setTextSize(dp(getContext(),11));
                String label=String.format(Locale.US,"%.1f",value);
                c.drawText(label,x-paint.measureText(label)/2f,base+dp(getContext(),19),paint);
            }
        }
    }
}
