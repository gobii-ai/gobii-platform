import React from 'react';
import MDXComponents from '@theme-original/MDXComponents';

function Card({children}) {
  return <div className="gobii-doc-card">{children}</div>;
}

function Screenshot({src, alt, caption, children}) {
  return (
    <figure className="gobii-doc-screenshot">
      <img src={src} alt={alt} loading="lazy" />
      {caption || children ? <figcaption>{caption || children}</figcaption> : null}
    </figure>
  );
}

export default {
  ...MDXComponents,
  Card,
  Screenshot,
};
