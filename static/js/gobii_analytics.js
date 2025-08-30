const COOKIE_DOMAIN = (() => {
  const hostname = window.location.hostname;
  // Do not attempt to derive a domain for IP addresses.
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(hostname)) {
    return hostname;
  }
  const parts = hostname.split('.');
  return parts.length > 1 ? '.' + parts.slice(-2).join('.') : hostname;
})();

const UTM_PARAMS = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term'];

(function() {
  const params = new URLSearchParams(window.location.search);
 UTM_PARAMS.forEach(function(p) {
    const value = params.get(p);
    if (value) {
      // Store each UTM param in a 1st-party cookie (valid for, say, 60 days)
      const d = new Date();
      d.setTime(d.getTime() + (60*24*60*60*1000));
      document.cookie = p + "=" + encodeURIComponent(value) + "; expires=" + d.toUTCString() + "; path=/; SameSite=Lax; domain=" + COOKIE_DOMAIN;
    }
  });
})();

(function () {
  const keys = ['utm_source','utm_medium','utm_campaign','utm_content','utm_term'];
  const hasUtm = keys.some(k => new URLSearchParams(location.search).has(k));
  if (!hasUtm) return;

  const utm = {};
  keys.forEach(k => {
    const v = new URLSearchParams(location.search).get(k);
    if (v) utm[k] = v;
  });
  // Persist first-touch once
  if (!document.cookie.includes('__utm_first=')) {
    document.cookie = '__utm_first=' + encodeURIComponent(JSON.stringify(utm))
                    + ';path=/;SameSite=Lax;max-age=' + 60*60*24*365
                    + ';domain=' + COOKIE_DOMAIN;
  }
})();

function getUTMParams() {
  // Returns an object with UTM parameters from cookies
  const params = {};

  UTM_PARAMS.forEach(name => {
    const match = document.cookie.match(
      new RegExp('(?:^|;\\s*)' + name + '=([^;]*)')   // ← tighter prefix, lenient space
    );
    if (match) params[name] = decodeURIComponent(match[1]);
  });

  return params;
}