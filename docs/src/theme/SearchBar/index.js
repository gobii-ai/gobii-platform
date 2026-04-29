import React, {useEffect, useRef} from 'react';
import ExecutionEnvironment from '@docusaurus/ExecutionEnvironment';

import './styles.css';

export default function SearchBar() {
  const ref = useRef(null);

  useEffect(() => {
    if (!ExecutionEnvironment.canUseDOM || !ref.current) return undefined;

    const cssId = 'pagefind-ui-css';
    if (!document.getElementById(cssId)) {
      const link = document.createElement('link');
      link.id = cssId;
      link.rel = 'stylesheet';
      link.href = '/pagefind/pagefind-ui.css';
      document.head.appendChild(link);
    }

    let disposed = false;
    const script = document.createElement('script');
    script.src = '/pagefind/pagefind-ui.js';
    script.async = true;
    script.onload = () => {
      if (disposed || !window.PagefindUI || !ref.current) return;
      ref.current.innerHTML = '';
      new window.PagefindUI({
        element: ref.current,
        showSubResults: true,
        showImages: false,
        resetStyles: false,
      });
    };
    document.body.appendChild(script);

    return () => {
      disposed = true;
      script.remove();
    };
  }, []);

  return <div className="gobii-pagefind-search" ref={ref} />;
}
