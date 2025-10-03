const MULTI_LABEL_PUBLIC_SUFFIXES = new Set([
  'ac.uk', 'co.uk', 'gov.uk', 'ltd.uk', 'net.uk', 'org.uk', 'plc.uk', 'sch.uk',
  'com.au', 'net.au', 'org.au', 'edu.au', 'gov.au',
  'com.br', 'net.br', 'org.br',
  'com.cn', 'net.cn', 'org.cn',
  'com.tw', 'net.tw', 'org.tw',
  'com.sg', 'net.sg', 'org.sg',
  'co.nz', 'net.nz', 'org.nz', 'gov.nz',
  'co.jp', 'ne.jp', 'or.jp', 'go.jp',
  'co.kr', 'ne.kr', 'or.kr', 'go.kr',
  'co.in', 'firm.in', 'gen.in', 'ind.in', 'net.in', 'org.in',
  'com.mx', 'net.mx', 'org.mx',
  'co.za', 'net.za', 'org.za',
  'com.tr', 'net.tr', 'org.tr'
]);

function deriveCookieDomain(hostname) {
  if (!hostname) return hostname;

  const lowerHost = hostname.toLowerCase();
  const isIPv4 = /^\d{1,3}(\.\d{1,3}){3}$/.test(lowerHost);
  const isIPv6 = lowerHost.includes(':');

  if (lowerHost === 'localhost' || isIPv4 || isIPv6) {
    return lowerHost;
  }

  const parts = lowerHost.split('.');
  if (parts.length < 2) {
    return lowerHost;
  }

  const lastTwo = parts.slice(-2).join('.');
  if (MULTI_LABEL_PUBLIC_SUFFIXES.has(lastTwo)) {
    if (parts.length >= 3) {
      return '.' + parts.slice(-3).join('.');
    }
    return lowerHost;
  }

  if (parts.length === 2) {
    return '.' + parts.join('.');
  }

  return '.' + parts.slice(-2).join('.');
}

const COOKIE_DOMAIN = deriveCookieDomain(window.location.hostname);

// Smoke check helper (manual): run `window.__gobiiAnalyticsCookieDomainFor('app.example.co.uk')`
// in the browser console to verify how we derive the cookie scope for a hostname.
if (typeof window !== 'undefined') {
  window.__gobiiAnalyticsCookieDomainFor = deriveCookieDomain;
}

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