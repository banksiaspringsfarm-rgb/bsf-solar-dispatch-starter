package farm.bsf.solardispatch;

import android.app.Activity;
import android.appwidget.AppWidgetManager;
import android.content.ComponentName;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.Typeface;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONObject;

/**
 * The only screen. Whoever installs the system stands next to the owner, types the two addresses in, presses
 * Test, sees a real battery percentage come back, presses Save. After that the owner never opens this again -
 * they add the widget to the home screen and tap it to open their dashboard.
 *
 * Can also be pre-filled from an intent (extras: name, home, away, lithium, save) so an installer can set a
 * phone up from a link or a script instead of typing a 43-character token on a touch keyboard.
 */
public class SetupActivity extends Activity {
    private EditText name, home, away; private CheckBox lithium; private TextView result;
    private final Handler ui = new Handler(Looper.getMainLooper());

    private int dp(int v) { return Math.round(v * getResources().getDisplayMetrics().density); }

    private TextView label(String t) {
        TextView v = new TextView(this); v.setText(t); v.setTextSize(13); v.setTextColor(0xFF8B96B5);
        v.setPadding(0, dp(18), 0, dp(6)); v.setAllCaps(true); return v;
    }
    private EditText field(String hint, int inputType) {
        EditText e = new EditText(this); e.setHint(hint); e.setInputType(inputType); e.setTextSize(17);
        e.setTextColor(0xFFE8ECF7); e.setHintTextColor(0xFF5B6685); e.setSingleLine(true);
        e.setMinHeight(dp(52)); e.setPadding(dp(14), dp(10), dp(14), dp(10)); e.setBackgroundColor(0xFF131A2E);
        return e;
    }
    private TextView help(String t) {
        TextView v = new TextView(this); v.setText(t); v.setTextSize(14); v.setTextColor(0xFF8B96B5);
        v.setPadding(0, dp(6), 0, 0); v.setLineSpacing(dp(2), 1f); return v;
    }
    private Button button(String t, int bg, int fg) {
        Button b = new Button(this); b.setText(t); b.setAllCaps(false); b.setTextSize(17); b.setTypeface(Typeface.DEFAULT_BOLD);
        b.setTextColor(fg); b.setBackgroundColor(bg); b.setMinHeight(dp(56));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(14); b.setLayoutParams(lp); return b;
    }

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout col = new LinearLayout(this); col.setOrientation(LinearLayout.VERTICAL);
        col.setPadding(dp(20), dp(24), dp(20), dp(32)); col.setBackgroundColor(0xFF0B1020);

        TextView h = new TextView(this); h.setText("☀  Solar Dispatch"); h.setTextSize(24); h.setTypeface(Typeface.DEFAULT_BOLD);
        h.setTextColor(0xFFE8ECF7); col.addView(h);
        col.addView(help("Point this phone at a solar system. Fill in one or both addresses, press Test, then Save."));

        col.addView(label("Name shown on the widget"));
        name = field("e.g. Home", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_WORDS); col.addView(name);

        col.addView(label("At home — the system's address on the house Wi-Fi"));
        home = field("http://192.168.1.50:8780", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI); col.addView(home);
        col.addView(help("Tried first. Works without internet. Leave blank if you only have an away link."));

        col.addView(label("Away from home — the read-only away link"));
        away = field("https://…/long-code/", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI); col.addView(away);
        col.addView(help("Used when the home address doesn't answer. Anyone with this link can look, nobody can change anything."));

        lithium = new CheckBox(this); lithium.setText("Lithium battery (the ring only turns red below 20 %, not 60 %)");
        lithium.setTextSize(15); lithium.setTextColor(0xFFE8ECF7); lithium.setMinHeight(dp(52)); lithium.setPadding(dp(6), dp(14), 0, dp(6));
        col.addView(lithium);

        Button test = button("Test", 0xFF232C47, 0xFFE8ECF7); col.addView(test);
        result = new TextView(this); result.setTextSize(15); result.setTextColor(0xFFE8ECF7); result.setPadding(0, dp(12), 0, 0);
        result.setLineSpacing(dp(3), 1f); col.addView(result);
        Button save = button("Save", 0xFFF5B62E, 0xFF1A1205); col.addView(save);
        col.addView(help("Then long-press your home screen → Widgets → Solar Dispatch."));

        ScrollView sv = new ScrollView(this); sv.setBackgroundColor(0xFF0B1020); sv.setFillViewport(true); sv.addView(col);
        setContentView(sv);

        SharedPreferences p = SiteConfig.prefs(this);
        name.setText(p.getString(SiteConfig.K_NAME, "")); home.setText(p.getString(SiteConfig.K_HOME, ""));
        away.setText(p.getString(SiteConfig.K_AWAY, "")); lithium.setChecked(p.getBoolean(SiteConfig.K_LITHIUM, false));

        test.setOnClickListener(new View.OnClickListener() { @Override public void onClick(View v) { store(); runTest(); } });
        save.setOnClickListener(new View.OnClickListener() { @Override public void onClick(View v) { store(); refreshWidgets(); say("Saved. The widget will update in a moment.", 0xFF38D39F); } });

        // A setup link fills the form and runs Test, but never saves by itself: a link from anywhere must not be able
        // to silently re-point someone's widget. The person sees what it filled in and presses Save.
        android.net.Uri link = getIntent() != null ? getIntent().getData() : null;
        if (link != null && "solardispatch".equals(link.getScheme()) && "setup".equals(link.getHost())) {
            String n = link.getQueryParameter("name"), hm = link.getQueryParameter("home"), aw = link.getQueryParameter("away"), li = link.getQueryParameter("lithium");
            if (n != null) name.setText(n.length() > 40 ? n.substring(0, 40) : n);
            if (hm != null) home.setText(hm); if (aw != null) away.setText(aw);
            if (li != null) lithium.setChecked("1".equals(li) || "true".equalsIgnoreCase(li));
            say("Filled in from a setup link. Check it, press Test, then Save.", 0xFFF5B62E);
        }

        Intent in = getIntent();
        if (in != null && (in.hasExtra("home") || in.hasExtra("away") || in.hasExtra("name"))) {
            if (in.hasExtra("name")) name.setText(in.getStringExtra("name"));
            if (in.hasExtra("home")) home.setText(in.getStringExtra("home"));
            if (in.hasExtra("away")) away.setText(in.getStringExtra("away"));
            if (in.hasExtra("lithium")) lithium.setChecked(in.getBooleanExtra("lithium", false));
            if (in.getBooleanExtra("save", false)) { store(); refreshWidgets(); runTest(); }
        }
    }

    private void store() {
        SiteConfig.prefs(this).edit()
            .putString(SiteConfig.K_NAME, name.getText().toString().trim())
            .putString(SiteConfig.K_HOME, home.getText().toString().trim())
            .putString(SiteConfig.K_AWAY, away.getText().toString().trim())
            .putBoolean(SiteConfig.K_LITHIUM, lithium.isChecked())
            .remove(SiteConfig.K_LAST_BASE).remove(SiteConfig.K_LAST_PATH).apply();
    }

    private void say(final String t, final int color) { ui.post(new Runnable() { @Override public void run() { result.setTextColor(color); result.setText(t); } }); }

    private void runTest() {
        if (!SiteConfig.configured(this)) { say("Enter at least one address first.", 0xFFFF6B6B); return; }
        say("Testing…", 0xFF8B96B5);
        new Thread(new Runnable() { @Override public void run() {
            StringBuilder log = new StringBuilder(); boolean anyOk = false;
            String h = SiteConfig.home(SetupActivity.this), a = SiteConfig.away(SetupActivity.this);
            for (String base : new String[]{h, a}) {
                if (base.isEmpty()) continue;
                String which = base.equals(h) ? "Home address" : "Away link";
                JSONObject got = null;
                for (SiteConfig.Candidate k : SiteConfig.candidates(SetupActivity.this)) {
                    if (!k.base.equals(base)) continue;
                    got = SolarWidget.getJson(k.url(), k.timeoutMs);
                    if (got != null && got.optBoolean("ok", false)) break;
                    boolean unreachable = got == null && SolarWidget.lastFailUnreachable; got = null;
                    if (unreachable) break;          // the box isn't there; don't wait for its second path too
                }
                if (got != null) {
                    anyOk = true; JSONObject hw = got.optJSONObject("hw");
                    double soc = hw != null ? hw.optDouble("soc", Double.NaN) : Double.NaN;
                    log.append("✓ ").append(which).append(" works").append(Double.isNaN(soc) ? "" : " — battery " + Math.round(soc) + " %").append("\n");
                } else {
                    log.append("✗ ").append(which).append(base.equals(h) ? " didn't answer (normal if this phone isn't on the house Wi-Fi right now)" : " didn't answer — check the link").append("\n");
                }
            }
            say(log.toString().trim(), anyOk ? 0xFF38D39F : 0xFFFF6B6B);
        } }).start();
    }

    private void refreshWidgets() {
        AppWidgetManager mgr = AppWidgetManager.getInstance(this);
        int[] ids = mgr.getAppWidgetIds(new ComponentName(this, SolarWidget.class));
        if (ids.length == 0) return;
        Intent i = new Intent(this, SolarWidget.class).setAction(AppWidgetManager.ACTION_APPWIDGET_UPDATE);
        i.putExtra(AppWidgetManager.EXTRA_APPWIDGET_IDS, ids); sendBroadcast(i);
    }
}
