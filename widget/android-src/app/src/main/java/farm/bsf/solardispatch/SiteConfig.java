package farm.bsf.solardispatch;

import android.content.Context;
import android.content.SharedPreferences;

import java.util.ArrayList;
import java.util.List;

/**
 * Where this phone's solar system lives. Typed in once on the phone (SetupActivity), kept in the app's
 * private storage, never compiled in.
 *
 *  home  - the site's own box on the house Wi-Fi, e.g. http://192.168.1.50:8780 . Tried FIRST with a short
 *          timeout: it needs no internet, so it still works when the internet is down.
 *  away  - the site's read-only away link, e.g. https://solar.example.com/<token>/ . Used when home does not answer.
 *
 * Either may be blank. A base is tried as <base>/state.json (a box running this bundle) and then
 * <base>/solar/state (a relay that serves it there).
 */
final class SiteConfig {
    static final String PREFS = "SolarSite";
    static final String K_NAME = "name", K_HOME = "home", K_AWAY = "away", K_LITHIUM = "lithium",
                        K_LAST_BASE = "last_base", K_LAST_PATH = "last_path";
    static final int HOME_TIMEOUT_MS = 3000, AWAY_TIMEOUT_MS = 8000;

    private SiteConfig() {}

    static SharedPreferences prefs(Context c) { return c.getSharedPreferences(PREFS, Context.MODE_PRIVATE); }

    static String clean(String s) {
        if (s == null) return "";
        s = s.trim();
        while (s.endsWith("/")) s = s.substring(0, s.length() - 1);
        if (!s.isEmpty() && !s.startsWith("http://") && !s.startsWith("https://")) s = "http://" + s;
        return s;
    }

    static String name(Context c)      { String n = prefs(c).getString(K_NAME, ""); return n.isEmpty() ? "Solar" : n; }
    static String home(Context c)      { return clean(prefs(c).getString(K_HOME, "")); }
    static String away(Context c)      { return clean(prefs(c).getString(K_AWAY, "")); }
    static boolean configured(Context c) { return !home(c).isEmpty() || !away(c).isEmpty(); }

    /** Red-ring threshold. Lead-acid must not sit low (60 %); a lithium bank is fine far lower (20 %). */
    static int socLow(Context c)       { return prefs(c).getBoolean(K_LITHIUM, false) ? 20 : 60; }

    static final class Candidate {
        final String base, path; final int timeoutMs;
        Candidate(String base, String path, int timeoutMs) { this.base = base; this.path = path; this.timeoutMs = timeoutMs; }
        String url() { return base + path; }
    }

    static List<Candidate> candidates(Context c) {
        List<Candidate> out = new ArrayList<>();
        String h = home(c), a = away(c);
        if (!h.isEmpty()) { out.add(new Candidate(h, "/state.json", HOME_TIMEOUT_MS)); out.add(new Candidate(h, "/solar/state", HOME_TIMEOUT_MS)); }
        if (!a.isEmpty()) { out.add(new Candidate(a, "/state.json", AWAY_TIMEOUT_MS)); out.add(new Candidate(a, "/solar/state", AWAY_TIMEOUT_MS)); }
        return out;
    }

    static void rememberWorking(Context c, Candidate k) {
        prefs(c).edit().putString(K_LAST_BASE, k.base).putString(K_LAST_PATH, k.path).apply();
    }

    /** What a tap on the widget opens: the dashboard on whichever address answered last. */
    static String dashboardUrl(Context c) {
        SharedPreferences p = prefs(c);
        String base = p.getString(K_LAST_BASE, ""), path = p.getString(K_LAST_PATH, "");
        if (base.isEmpty()) base = !home(c).isEmpty() ? home(c) : away(c);
        if (base.isEmpty()) return "";
        if ("/solar/state".equals(path)) return base + "/solar";
        if (base.equals(away(c))) return base + "/";                       // the away link serves the dashboard at its root
        return base + "/solar_dispatch_dashboard.html";
    }
}
