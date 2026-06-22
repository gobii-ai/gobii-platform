import React, {useEffect, useRef} from 'react';
import ExecutionEnvironment from '@docusaurus/ExecutionEnvironment';

import './styles.css';

const PAGEFIND_CSS_PATH = '/pagefind/pagefind-ui.css';
const PAGEFIND_JS_PATH = '/pagefind/pagefind-ui.js';

function stripHtmlExtension(url) {
  return url?.replace(/\.html(?=$|#|\?)/, '') ?? url;
}

function normalizeResultUrls(result) {
  result.url = stripHtmlExtension(result.url);
  result.sub_results = result.sub_results?.map((subResult) => ({
    ...subResult,
    url: stripHtmlExtension(subResult.url),
  }));
  return result;
}

function isJavaScriptResponse(response) {
  const contentType = response.headers.get('content-type') ?? '';
  return response.ok && /\b(?:java|ecma)script\b/i.test(contentType);
}

function ensurePagefindCss() {
  const cssId = 'pagefind-ui-css';
  if (document.getElementById(cssId)) return;

  const link = document.createElement('link');
  link.id = cssId;
  link.rel = 'stylesheet';
  link.href = PAGEFIND_CSS_PATH;
  document.head.appendChild(link);
}

export default function SearchBar() {
  const ref = useRef(null);

  useEffect(() => {
    if (!ExecutionEnvironment.canUseDOM || !ref.current) return undefined;

    let disposed = false;
    let script;

    const initializePagefind = () => {
      if (disposed || !window.PagefindUI || !ref.current) return;
      ref.current.innerHTML = '';
      new window.PagefindUI({
        element: ref.current,
        showSubResults: true,
        showImages: false,
        processResult: normalizeResultUrls,
        resetStyles: false,
      });
    };

    const loadPagefind = async () => {
      if (window.PagefindUI) {
        ensurePagefindCss();
        initializePagefind();
        return;
      }

      // Pagefind assets are generated after production builds, so local dev may
      // serve the Docusaurus HTML fallback at this path.
      const response = await fetch(PAGEFIND_JS_PATH, {method: 'GET', cache: 'no-store'});
      if (disposed || !isJavaScriptResponse(response)) return;

      ensurePagefindCss();
      script = document.createElement('script');
      script.src = PAGEFIND_JS_PATH;
      script.async = true;
      script.onload = initializePagefind;
      document.body.appendChild(script);
    };

    loadPagefind().catch(() => {
      // Leave search empty when Pagefind is unavailable instead of surfacing a
      // noisy runtime error during local docs development.
    });

    return () => {
      disposed = true;
      script?.remove();
    };
  }, []);

  return <div className="gobii-pagefind-search" ref={ref} />;
}
