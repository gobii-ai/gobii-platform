import fs from 'node:fs';
import path from 'node:path';

const buildDir = path.resolve('build');

function walk(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const entryPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      return walk(entryPath);
    }
    return entry.isFile() && entry.name.endsWith('.html') ? [entryPath] : [];
  });
}

function prepareHtml(source) {
  let html = source;

  // Keep Pagefind excerpts focused on the documentation itself, not Docusaurus
  // navigation chrome. Pagefind falls back to indexing the whole body otherwise.
  html = html.replace('<article>', '<article data-pagefind-body>');
  html = html.replace(
    /<nav([^>]*class="[^"]*theme-doc-breadcrumbs[^"]*"[^>]*)>/g,
    '<nav$1 data-pagefind-ignore>'
  );

  return html;
}

if (!fs.existsSync(buildDir)) {
  throw new Error(`Build directory not found: ${buildDir}`);
}

let prepared = 0;
for (const filePath of walk(buildDir)) {
  const html = fs.readFileSync(filePath, 'utf8');
  const nextHtml = prepareHtml(html);
  if (nextHtml !== html) {
    fs.writeFileSync(filePath, nextHtml);
    prepared += 1;
  }
}

console.log(`Prepared ${prepared} HTML files for Pagefind indexing.`);
