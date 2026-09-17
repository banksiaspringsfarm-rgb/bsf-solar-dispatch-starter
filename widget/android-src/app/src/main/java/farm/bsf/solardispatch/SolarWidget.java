package farm.bsf.solardispatch;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.content.SharedPreferences;
import android.content.res.Configuration;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RectF;
import android.os.Build;
import android.view.View;
import android.widget.RemoteViews;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Locale;

/**
 * BSF Solar Dispatch — Android home-screen widget (v3, DAY/NIGHT themed).
 *
 * Sibling of {@link BotFarmWidget}, packaged in the same APK. Polls ONE
 * public read-only endpoint (no control surface, no farm-LAN access):
 *
 *   GET /solar/state  → {ok, ts, hw:{...}, ac:{...}, loads:{...}, source}
 *
 * v3 (2026-06-16) — three Steven-requested tweaks on top of the loved v2 light card:
 *
 *  1. DAY/NIGHT THEME that follows the phone's system dark mode. Card chrome,
 *     static labels and chips theme via @color resources (values/ light vs
 *     values-night/ dark navy-charcoal); every dynamic + state-coded text colour
 *     is applied through RemoteViews.setColorInt(light, night) so the host
 *     re-resolves it the instant the system uiMode toggles — no network round
 *     trip, no broadcast. The warm yellow #F5B62E stays the accent in both;
 *     SOC bands stay lead-acid (red <= 60%, NOT lithium 20%) in both themes.
 *
 *  2. FIRE + WATER hot-water glyph: 🔥💧 always paired (matches the dashboard
 *     node label). State is carried by the text (—/ON/OFF/watts), not by
 *     swapping the glyph — colour-emoji can't be per-glyph tinted in a
 *     RemoteViews TextView anyway, so a static pair is the clean read. (AC keeps ❄.)
 *
 *  3. SOC ring as a CLOCK-STYLE depletion arc: a faint full-circle track with a
 *     state-coloured arc that sweeps CLOCKWISE from 12 o'clock, length =
 *     SOC/100 x 360deg — 100% = full circle, 75% = 12→9, 50% = 12→6, 25% = 12→3.
 *     The missing portion stays as the muted track so it reads as a partly-empty
 *     clock face, not a broken ring. Arc colour encodes battery STATE: green =
 *     charging, warm gold = resting/float/100, orange = discharging, RED <= 60%
 *     SOC (the LEAD-ACID danger floor — this bank is lead-acid, NOT lithium).
 *     The arc SNAPS to each new value: RemoteViews can't run a cheap continuous
 *     tween (no per-frame Canvas loop in a widget host), so there is no
 *     inter-update animation — a documented constraint, snap-on-update instead.
 *
 * All numbers stay RAW dispatcher signals: integer watts, no kW rounding, no
 * guessed values; a stale feed shows a chip, never a stale number dressed as live.
 * Android floors updatePeriodMillis at 30 min; tapping the widget force-refreshes
 * and opens /solar in the browser.
 */
public class SolarWidget extends AppWidgetProvider {
    private static final String TAG = "SolarWidget";
    private static final String ACTION_REFRESH = "farm.bsf.solardispatch.SOLAR_WIDGET_REFRESH";
    // Self-rescheduling refresh tick — survives Doze via setAndAllowWhileIdle. The manifest
    // updatePeriodMillis (30 min) is a NON-idle alarm the OS defers in Doze, which froze the
    // widget for hours on a stale snapshot while still showing a "just now" label.
    private static final String ACTION_TICK = "farm.bsf.solardispatch.SOLAR_WIDGET_TICK";
    private static final long REFRESH_INTERVAL_MS = 15 * 60 * 1000L;
    private static final String PREFS_NAME = "SolarWidgetPrefs";

    private static final long STALE_AFTER_MS = 5 * 60 * 1000L;

    // ── Lead-acid SOC danger floor — ring goes RED at/below this (NOT lithium 20%) ──
    // Red-ring threshold: per site (lead-acid 60, lithium 20) - read from SiteConfig at the start of every update.
    private static volatile int SOC_LOW = 60;
    // battery-flow deadband: |W| below this reads as resting/float, not charge/discharge
    private static final int FLOW_DEADBAND = 30;

    // ── Theme-aware text colour PAIRS (light, dark) ─────────────────────────
    // Applied via RemoteViews.setColorInt(viewId,"setTextColor",light,dark) so the
    // host re-resolves them when the phone's system day/night mode toggles
    // (API 31+; older devices fall back to a one-shot pick in txt()). Card chrome,
    // chips and static labels theme via @color resources instead (values/ vs
    // values-night/), so the whole widget flips with no Java path.
    private static final int INK_L = 0xFF1A1A1A, INK_D = 0xFFECEFF4;   // primary text
    private static final int MUT_L = 0xFF7A828E, MUT_D = 0xFF9AA3B2;   // secondary text
    private static final int DIM_L = 0xFF9AA1AC, DIM_D = 0xFF6B7484;   // tertiary / off / no-data
    private static final int GREEN_L = 0xFF1A9C5B, GREEN_D = 0xFF34D399; // dispatch healthy
    private static final int RED_L   = 0xFFD33A3A, RED_D   = 0xFFF87171; // stale / lockout / throttled
    private static final int AMBER_L = 0xFFC77D12, AMBER_D = 0xFFF5B62E; // HW heating (warm accent)
    private static final int CYAN_L  = 0xFF0E7C99, CYAN_D  = 0xFF38BDF8; // surplus / AC on

    // ── SOC clock-arc ring (Canvas bitmap) ──────────────────────────────────
    // The depletion track is theme-NEUTRAL (translucent grey) so the partly-empty
    // clock face reads on both light and dark cards without redrawing the bitmap on
    // a theme toggle. The arc colour encodes battery STATE; dark gets a brighter
    // green/orange so it still pops on charcoal.
    private static final int RING_TRACK    = 0x66949CA8;                              // ~40% neutral grey
    private static final int RING_GREEN_L  = 0xFF16A34A, RING_GREEN_D  = 0xFF22C55E;  // charging
    private static final int RING_GOLD     = 0xFFF5B62E;                              // resting / float / 100
    private static final int RING_ORANGE_L = 0xFFF08A3C, RING_ORANGE_D = 0xFFFB923C;  // discharging
    private static final int RING_RED      = 0xFFEF4444;                              // SOC <= 60 (lead-acid)

    // No address and no key are compiled in. See SiteConfig: the site is typed in on the phone.

    // ── Cache keys ──────────────────────────────────────────────────────────
    private static final String KEY_SOC = "last_soc", KEY_PV = "last_pv", KEY_PVDC = "last_pvdc",
        KEY_PVFR = "last_pvfr", KEY_SURPLUS = "last_surplus", KEY_BATT = "last_batt",
        KEY_HW_ON = "last_hw_on", KEY_AC_ON = "last_ac_on", KEY_HW_W = "last_hw_w", KEY_AC_W = "last_ac_w",
        KEY_LOCKOUT = "last_lockout", KEY_CURT = "last_curt", KEY_SNAP_TS = "last_snapshot_ts";

    @Override
    public void onUpdate(Context context, AppWidgetManager appWidgetManager, int[] appWidgetIds) {
        for (int id : appWidgetIds) triggerFetch(context, appWidgetManager, id);
        scheduleNextTick(context);   // keep the Doze-surviving refresh chain alive
    }

    @Override
    public void onEnabled(Context context) {
        super.onEnabled(context);
        scheduleNextTick(context);
    }

    @Override
    public void onDisabled(Context context) {
        super.onDisabled(context);
        cancelTick(context);
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        super.onReceive(context, intent);
        String action = intent.getAction();
        if (ACTION_REFRESH.equals(action) || ACTION_TICK.equals(action)) {
            AppWidgetManager mgr = AppWidgetManager.getInstance(context);
            int[] ids = mgr.getAppWidgetIds(new android.content.ComponentName(context, SolarWidget.class));
            for (int id : ids) triggerFetch(context, mgr, id);
            if (ACTION_TICK.equals(action)) scheduleNextTick(context);   // chain the next tick
        }
    }

    // ── Self-rescheduling refresh tick (Doze-tolerant) ──────────────────────
    // setAndAllowWhileIdle fires even in Doze (rate-limited to ~9 min) where the manifest
    // updatePeriodMillis alarm is suppressed, so we re-arm a 15-min tick on every fire.
    // A tap (ACTION_REFRESH) always force-refreshes regardless. NOTE: on aggressive OEMs
    // (Motorola) this is still throttled unless the app is set Battery → Unrestricted.
    private PendingIntent tickPi(Context ctx) {
        Intent i = new Intent(ctx, SolarWidget.class).setAction(ACTION_TICK);
        return PendingIntent.getBroadcast(ctx, 2, i,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }
    private void scheduleNextTick(Context ctx) {
        AlarmManager am = (AlarmManager) ctx.getSystemService(Context.ALARM_SERVICE);
        if (am == null) return;
        try {
            am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP,
                System.currentTimeMillis() + REFRESH_INTERVAL_MS, tickPi(ctx));
        } catch (Exception e) { Log.e(TAG, "scheduleNextTick: " + e.getMessage()); }
    }
    private void cancelTick(Context ctx) {
        AlarmManager am = (AlarmManager) ctx.getSystemService(Context.ALARM_SERVICE);
        if (am != null) am.cancel(tickPi(ctx));
    }

    private void triggerFetch(final Context ctx, final AppWidgetManager mgr, final int widgetId) {
        showLoading(ctx, mgr, widgetId);
        new Thread(() -> {
            SOC_LOW = SiteConfig.socLow(ctx);
            FetchResult result = fetchSolarData(ctx);
            if (result != null) { saveToPrefs(ctx, result); updateViews(ctx, mgr, widgetId, result, false); }
            else                { updateViews(ctx, mgr, widgetId, loadFromPrefs(ctx), true); }
        }).start();
    }

    private void showLoading(Context ctx, AppWidgetManager mgr, int widgetId) {
        RemoteViews rv = new RemoteViews(ctx.getPackageName(), R.layout.widget_solar);
        rv.setTextViewText(R.id.tv_updated, "Refreshing…");
        mgr.partiallyUpdateAppWidget(widgetId, rv);
    }

    // ── Build + push RemoteViews ────────────────────────────────────────────
    private void updateViews(Context ctx, AppWidgetManager mgr, int widgetId, FetchResult d, boolean offline) {
        boolean night = isNight(ctx);
        RemoteViews rv = new RemoteViews(ctx.getPackageName(), R.layout.widget_solar);
        rv.setTextViewText(R.id.tv_title, "\u2600 " + SiteConfig.name(ctx));

        // Tap anywhere → open the full /solar dashboard; tap the footer → force refresh.
        Intent refreshIntent = new Intent(ctx, SolarWidget.class).setAction(ACTION_REFRESH);
        PendingIntent refreshPi = PendingIntent.getBroadcast(ctx, 0, refreshIntent,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        // Tap: the dashboard on whichever address answered last; until the phone is set up, the setup screen.
        String dash = SiteConfig.dashboardUrl(ctx);
        Intent launchIntent = dash.isEmpty() ? new Intent(ctx, SetupActivity.class) : new Intent(Intent.ACTION_VIEW, Uri.parse(dash));
        launchIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        PendingIntent launchPi = PendingIntent.getActivity(ctx, 1, launchIntent,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        rv.setOnClickPendingIntent(R.id.widget_root, launchPi);       // body → open dashboard
        rv.setOnClickPendingIntent(R.id.refresh_strip, refreshPi);    // bottom strip → manual refresh

        boolean stale = offline || d == null || d.stale;
        int soc  = d != null ? d.soc  : Integer.MIN_VALUE;
        int batt = d != null ? d.batt : Integer.MIN_VALUE;

        // ── SOC clock-arc + centred % and state word ────────────────────────
        rv.setImageViewBitmap(R.id.iv_ring, drawSocRing(soc, batt, night));
        if (soc != Integer.MIN_VALUE) {
            rv.setTextViewText(R.id.tv_soc, soc + "%");
            txt(rv, R.id.tv_soc, INK_L, INK_D, night);
            rv.setTextViewText(R.id.tv_soc_glyph, socState(soc, batt));
            txt(rv, R.id.tv_soc_glyph, MUT_L, MUT_D, night);
        } else {
            rv.setTextViewText(R.id.tv_soc, "—%");
            txt(rv, R.id.tv_soc, DIM_L, DIM_D, night);
            rv.setTextViewText(R.id.tv_soc_glyph, "🔋 no data");
            txt(rv, R.id.tv_soc_glyph, DIM_L, DIM_D, night);
        }

        // ── PV total + Fronius/MPPT split sub-line ──────────────────────────
        rv.setTextViewText(R.id.tv_pv, (d != null && d.pv != Integer.MIN_VALUE) ? watts(d.pv) : "— W");
        txt(rv, R.id.tv_pv, INK_L, INK_D, night);
        rv.setTextViewText(R.id.tv_pv_sub,
            "AC solar " + plain(d != null ? d.pvFron : Integer.MIN_VALUE)
            + " · DC solar " + plain(d != null ? d.pvDc : Integer.MIN_VALUE));
        txt(rv, R.id.tv_pv_sub, MUT_L, MUT_D, night);

        // ── Surplus (signed; positive = cyan, negative = muted) ─────────────
        if (d != null && d.surplus != Integer.MIN_VALUE) {
            rv.setTextViewText(R.id.tv_sur, signedWatts(d.surplus));
            if (d.surplus >= 0) txt(rv, R.id.tv_sur, CYAN_L, CYAN_D, night);
            else                txt(rv, R.id.tv_sur, MUT_L, MUT_D, night);
        } else {
            rv.setTextViewText(R.id.tv_sur, "— W");
            txt(rv, R.id.tv_sur, DIM_L, DIM_D, night);
        }

        // ── Loads: HW 🔥💧 (state in text) · AC ❄ (bright when on, dim when off) ─
        boolean hwOn = d != null && d.hwOn, acOn = d != null && d.acOn;
        rv.setTextViewText(R.id.tv_hw, hwLabel(hwOn, d != null && d.hwStale, d != null ? d.hwW : Integer.MIN_VALUE));
        txt(rv, R.id.tv_hw, hwOn ? AMBER_L : DIM_L, hwOn ? AMBER_D : DIM_D, night);
        rv.setTextViewText(R.id.tv_ac, loadLabel("❄", acOn, d != null && d.acStale, d != null ? d.acW : Integer.MIN_VALUE));
        txt(rv, R.id.tv_ac, acOn ? CYAN_L : DIM_L, acOn ? CYAN_D : DIM_D, night);

        // ── Dispatch health chip (top-right) ────────────────────────────────
        boolean lockout = d != null && d.lockout;
        if (stale) {
            rv.setTextViewText(R.id.chip_dispatch, "● " + (offline ? "OFFLINE" : "STALE"));
            txt(rv, R.id.chip_dispatch, RED_L, RED_D, night);
            rv.setInt(R.id.chip_dispatch, "setBackgroundResource", R.drawable.chip_throttled);
        } else if (lockout) {
            rv.setTextViewText(R.id.chip_dispatch, "● LOCKOUT");
            txt(rv, R.id.chip_dispatch, RED_L, RED_D, night);
            rv.setInt(R.id.chip_dispatch, "setBackgroundResource", R.drawable.chip_throttled);
        } else {
            rv.setTextViewText(R.id.chip_dispatch, "● DISPATCH");
            txt(rv, R.id.chip_dispatch, GREEN_L, GREEN_D, night);
            rv.setInt(R.id.chip_dispatch, "setBackgroundResource", R.drawable.chip_dispatch);
        }

        // ── Throttled chip — only when the canonical curtailment signal is live ─
        boolean throttled = d != null && d.curtailed && !stale;
        rv.setViewVisibility(R.id.chip_throttled, throttled ? View.VISIBLE : View.GONE);
        txt(rv, R.id.chip_throttled, RED_L, RED_D, night);

        // ── Top status line (relative age) + refresh-strip freshness (absolute time) ──
        // Relative age sits up top; the ABSOLUTE "as of 6:49am" rides the refresh button so
        // a frozen widget shows an old time right where you tap (the "just now" lie fix).
        String statusWord = stale ? (offline ? "Offline" : "Stale") : (lockout ? "LOCKOUT" : "OK");
        long sts = d != null ? d.snapshotTs : 0;
        String ago = agoText(sts);
        String clk = clockTime(sts);
        rv.setTextViewText(R.id.tv_status, "· Status: " + statusWord + " · " + ago);
        txt(rv, R.id.tv_status, stale ? RED_L : MUT_L, stale ? RED_D : MUT_D, night);

        // Refresh button label is static text (themed here); freshness shows the data's clock time.
        txt(rv, R.id.tv_refresh_label, CYAN_L, CYAN_D, night);
        if (clk.isEmpty()) {
            rv.setTextViewText(R.id.tv_updated, "—");
            txt(rv, R.id.tv_updated, DIM_L, DIM_D, night);
        } else if (stale) {
            rv.setTextViewText(R.id.tv_updated, "⚠ as of " + clk);
            txt(rv, R.id.tv_updated, RED_L, RED_D, night);
        } else {
            rv.setTextViewText(R.id.tv_updated, "as of " + clk);
            txt(rv, R.id.tv_updated, MUT_L, MUT_D, night);
        }

        mgr.updateAppWidget(widgetId, rv);
    }

    // ── Theme helpers ───────────────────────────────────────────────────────
    private static boolean isNight(Context ctx) {
        return (ctx.getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK)
            == Configuration.UI_MODE_NIGHT_YES;
    }

    /** Set a text colour as a day/night pair: setColorInt live-flips on a system
     *  dark-mode toggle (API 31+); older devices get a one-shot pick. */
    private void txt(RemoteViews rv, int id, int light, int dark, boolean night) {
        if (Build.VERSION.SDK_INT >= 31) rv.setColorInt(id, "setTextColor", light, dark);
        else rv.setTextColor(id, night ? dark : light);
    }

    // ── SOC ring bitmap — clock-style depletion arc, colour by battery state ──
    private Bitmap drawSocRing(int soc, int batt, boolean night) {
        int size = 240;
        Bitmap bmp = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888);
        Canvas c = new Canvas(bmp);
        float stroke = size * 0.11f, pad = stroke / 2f + 3f;
        RectF r = new RectF(pad, pad, size - pad, size - pad);

        // Depletion TRACK — faint full circle (theme-neutral) so the empty part of
        // the "clock" is visible as a muted ring, not a gap.
        Paint track = new Paint(Paint.ANTI_ALIAS_FLAG);
        track.setStyle(Paint.Style.STROKE); track.setStrokeWidth(stroke);
        track.setStrokeCap(Paint.Cap.ROUND); track.setColor(RING_TRACK);
        c.drawArc(r, 0, 360, false, track);

        if (soc != Integer.MIN_VALUE) {
            Paint arc = new Paint(Paint.ANTI_ALIAS_FLAG);
            arc.setStyle(Paint.Style.STROKE); arc.setStrokeWidth(stroke);
            arc.setStrokeCap(Paint.Cap.ROUND); arc.setColor(ringColor(soc, batt, night));
            // Clock sweep: start at 12 o'clock (-90deg), grow CLOCKWISE.
            // 100% = full 360deg circle, 50% = 180deg (12→6), 25% = 90deg (12→3).
            // Snaps to the new value each update (no RemoteViews tween — see class doc).
            float sweep = Math.max(0, Math.min(100, soc)) / 100f * 360f;
            c.drawArc(r, -90, sweep, false, arc);
        }
        return bmp;
    }

    /** Ring arc colour by battery STATE — lead-acid: red takes priority at ≤60% SOC. */
    private int ringColor(int soc, int batt, boolean night) {
        if (soc != Integer.MIN_VALUE && soc <= SOC_LOW) return RING_RED;
        if (batt != Integer.MIN_VALUE && batt >  FLOW_DEADBAND) return night ? RING_GREEN_D  : RING_GREEN_L;
        if (batt != Integer.MIN_VALUE && batt < -FLOW_DEADBAND) return night ? RING_ORANGE_D : RING_ORANGE_L;
        return RING_GOLD;
    }

    private String socState(int soc, int batt) {
        if (soc != Integer.MIN_VALUE && soc <= SOC_LOW) return "🔋 low";
        if (soc >= 100) return "🔋 full";
        if (batt != Integer.MIN_VALUE && batt >  FLOW_DEADBAND) return "🔋 charging";
        if (batt != Integer.MIN_VALUE && batt < -FLOW_DEADBAND) return "🔋 draining";
        return "🔋 float";
    }

    // ── HTTP fetch — /solar/state ────────────────────────────────────────────
    private FetchResult fetchSolarData(Context ctx) {
        try {
            // Home first (short timeout, needs no internet), then the away link. First one that answers ok wins.
            JSONObject snap = null; String deadBase = null;
            for (SiteConfig.Candidate k : SiteConfig.candidates(ctx)) {
                if (k.base.equals(deadBase)) continue;                 // that box is not reachable; its other path won't be either
                JSONObject got = getJson(k.url() + "?_=" + System.currentTimeMillis(), k.timeoutMs);
                if (got != null && got.optBoolean("ok", false)) { snap = got; SiteConfig.rememberWorking(ctx, k); break; }
                if (got == null && lastFailUnreachable) deadBase = k.base;
            }
            if (snap == null) return null;
            JSONObject hw = snap.optJSONObject("hw");
            JSONObject ac = snap.optJSONObject("ac");
            if (hw == null && ac == null) return null;

            FetchResult r = new FetchResult();
            r.snapshotTs = snap.optLong("ts", 0);
            if (hw != null) {
                r.soc     = optInt(hw, "soc");
                r.pv      = optInt(hw, "pv_total");
                r.pvDc    = optInt(hw, "pv_dc");
                r.pvFron  = optInt(hw, "pv_fronius");
                r.surplus = optInt(hw, "surplus_now");
                r.batt    = optInt(hw, "batt_power");
                r.hwOn    = "on".equals(hw.optString("hwState", ""));
                r.curtailed = hw.optBoolean("curtailed", false);
            }
            if (r.soc == Integer.MIN_VALUE && ac != null) r.soc = optInt(ac, "soc");
            if (ac != null) r.acOn = "on".equals(ac.optString("acState", ""));

            JSONObject loads = snap.optJSONObject("loads");
            if (loads != null) {
                r.hwW = optInt(loads, "hot_water_w");
                r.acW = optInt(loads, "ac_w");
                r.hwStale = "stale".equals(loads.optString("hot_water_state", ""));
                r.acStale = "stale".equals(loads.optString("ac_state", ""));
            }

            String hwMode = hw != null ? hw.optString("mode", "")   : "";
            String acWin  = ac != null ? ac.optString("window", "") : "";
            r.lockout = "LOCKOUT".equals(hwMode) || "LOCKOUT".equals(acWin);

            long age = r.snapshotTs > 0 ? (System.currentTimeMillis() - r.snapshotTs) : 0;
            JSONArray hwStale = hw != null ? hw.optJSONArray("stale") : null;
            boolean feedStale = hwStale != null && hwStale.length() > 0;
            r.stale = (r.snapshotTs > 0 && age > STALE_AFTER_MS) || feedStale;
            return r;
        } catch (Exception e) {
            Log.e(TAG, "fetchSolarData failed: " + e.getMessage());
            return null;
        }
    }

    private int optInt(JSONObject o, String key) {
        if (o == null || o.isNull(key)) return Integer.MIN_VALUE;
        double v = o.optDouble(key, Double.NaN);
        return Double.isNaN(v) ? Integer.MIN_VALUE : (int) Math.round(v);
    }

    /** True when the last getJson() failure was 'could not reach the host at all' (as opposed to a 404 or bad JSON).
     *  Lets callers skip a second path on a box that is simply not there - e.g. the home address when the phone is out. */
    static volatile boolean lastFailUnreachable = false;

    static JSONObject getJson(String urlStr, int timeoutMs) {
        lastFailUnreachable = false;
        HttpURLConnection conn = null;
        try {
            URL url = new URL(urlStr);
            conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("GET");
            conn.setConnectTimeout(timeoutMs);
            conn.setReadTimeout(timeoutMs);
            conn.setUseCaches(false);
            conn.setRequestProperty("Cache-Control", "no-cache");
            if (conn.getResponseCode() != 200) { Log.w(TAG, "HTTP " + conn.getResponseCode()); return null; }
            BufferedReader br = new BufferedReader(new InputStreamReader(conn.getInputStream(), "UTF-8"));
            StringBuilder sb = new StringBuilder(); String line;
            while ((line = br.readLine()) != null) sb.append(line);
            br.close();
            return new JSONObject(sb.toString());
        } catch (java.net.ConnectException | java.net.SocketTimeoutException | java.net.UnknownHostException | java.net.NoRouteToHostException e) {
            lastFailUnreachable = true;
            Log.w(TAG, "getJson: unreachable - " + e.getMessage());
            return null;
        } catch (Exception e) {
            Log.e(TAG, "getJson: " + e.getMessage());
            return null;
        } finally { if (conn != null) conn.disconnect(); }
    }

    // ── SharedPreferences cache (offline fallback) ──────────────────────────
    private void saveToPrefs(Context ctx, FetchResult r) {
        ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE).edit()
            .putInt(KEY_SOC, r.soc).putInt(KEY_PV, r.pv).putInt(KEY_PVDC, r.pvDc).putInt(KEY_PVFR, r.pvFron)
            .putInt(KEY_SURPLUS, r.surplus).putInt(KEY_BATT, r.batt)
            .putBoolean(KEY_HW_ON, r.hwOn).putBoolean(KEY_AC_ON, r.acOn)
            .putInt(KEY_HW_W, r.hwW).putInt(KEY_AC_W, r.acW)
            .putBoolean(KEY_LOCKOUT, r.lockout).putBoolean(KEY_CURT, r.curtailed)
            .putLong(KEY_SNAP_TS, r.snapshotTs).apply();
    }

    private FetchResult loadFromPrefs(Context ctx) {
        SharedPreferences p = ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        FetchResult r = new FetchResult();
        r.soc = p.getInt(KEY_SOC, Integer.MIN_VALUE); r.pv = p.getInt(KEY_PV, Integer.MIN_VALUE);
        r.pvDc = p.getInt(KEY_PVDC, Integer.MIN_VALUE); r.pvFron = p.getInt(KEY_PVFR, Integer.MIN_VALUE);
        r.surplus = p.getInt(KEY_SURPLUS, Integer.MIN_VALUE); r.batt = p.getInt(KEY_BATT, Integer.MIN_VALUE);
        r.hwOn = p.getBoolean(KEY_HW_ON, false); r.acOn = p.getBoolean(KEY_AC_ON, false);
        r.hwW = p.getInt(KEY_HW_W, Integer.MIN_VALUE); r.acW = p.getInt(KEY_AC_W, Integer.MIN_VALUE);
        r.lockout = p.getBoolean(KEY_LOCKOUT, false); r.curtailed = p.getBoolean(KEY_CURT, false);
        r.snapshotTs = p.getLong(KEY_SNAP_TS, 0);
        r.stale = true;
        return r;
    }

    // ── Formatting helpers (raw integer watts, no kW rounding) ──────────────
    private String watts(int w)        { return String.format(Locale.US, "%,d W", w); }
    private String signedWatts(int w)  { return String.format(Locale.US, "%+,d W", w); }
    private String plain(int w)        { return w == Integer.MIN_VALUE ? "—" : String.format(Locale.US, "%,d", w); }

    /** Hot Water label — always pairs 🔥💧 to match the dashboard node label;
     *  state is carried by the text (—/ON/OFF/watts), not by swapping glyph. */
    private String hwLabel(boolean on, boolean stale, int w) {
        if (stale) return "🔥💧 —";
        if (on)    return (w != Integer.MIN_VALUE) ? "🔥💧 " + watts(w) : "🔥💧 ON";
        return "🔥💧 OFF";
    }

    private String loadLabel(String icon, boolean on, boolean stale, int w) {
        if (stale) return icon + " —";
        if (on)    return (w != Integer.MIN_VALUE) ? icon + " " + watts(w) : icon + " ON";
        return icon + " OFF";
    }

    private String agoText(long ts) {
        if (ts <= 0) return "—";
        long m = (System.currentTimeMillis() - ts) / 60000L;
        if (m <= 0) return "just now";
        if (m < 60) return m + "m ago";
        long h = m / 60;
        return h + "h ago";
    }

    /** Absolute LOCAL clock time of the snapshot, e.g. "6:49am" — makes a frozen widget
     *  self-evident (a stale render shows an old time next to the live phone clock). */
    private String clockTime(long ts) {
        if (ts <= 0) return "";
        return new java.text.SimpleDateFormat("h:mma", Locale.US)
            .format(new java.util.Date(ts)).toLowerCase(Locale.US);
    }

    // ── Data holder ─────────────────────────────────────────────────────────
    static class FetchResult {
        int soc = Integer.MIN_VALUE, pv = Integer.MIN_VALUE, pvDc = Integer.MIN_VALUE,
            pvFron = Integer.MIN_VALUE, surplus = Integer.MIN_VALUE, batt = Integer.MIN_VALUE,
            hwW = Integer.MIN_VALUE, acW = Integer.MIN_VALUE;
        boolean hwOn = false, acOn = false, hwStale = false, acStale = false,
            lockout = false, curtailed = false, stale = false;
        long snapshotTs = 0;
    }
}
