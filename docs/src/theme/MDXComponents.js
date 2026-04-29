import React from 'react';
import MDXComponents from '@theme-original/MDXComponents';

function Card({children}) {
  return <div className="gobii-doc-card">{children}</div>;
}

export default {
  ...MDXComponents,
  Card,
};
