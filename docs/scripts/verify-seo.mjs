import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const buildDir = path.join(root, 'build');
const siteUrl = process.env.DOCS_SITE_URL || 'https://docs.gobii.ai';
const failures = [];

function walk(dir, callback) {
  for (const entry of fs.readdirSync(dir, {withFileTypes: true})) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      walk(fullPath, callback);
    } else {
      callback(fullPath);
    }
  }
}

function fail(message) {
  failures.push(message);
}

function readBuild(relativePath) {
  return fs.readFileSync(path.join(buildDir, relativePath), 'utf8');
}

function htmlText(html) {
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function isRedirectPage(html) {
  return /<meta[^>]+http-equiv=["']refresh["']/i.test(html);
}

function readAttribute(tag, name) {
  return tag?.match(new RegExp(`${name}=["']([^"']*)["']`, 'i'))?.[1] ?? '';
}

function findMetaContent(html, name) {
  const tags = html.match(/<meta\b[^>]*>/gi) ?? [];
  const tag = tags.find((candidate) => readAttribute(candidate, 'name').toLowerCase() === name);
  return readAttribute(tag, 'content');
}

function findCanonical(html) {
  const tags = html.match(/<link\b[^>]*>/gi) ?? [];
  const tag = tags.find((candidate) => readAttribute(candidate, 'rel').toLowerCase() === 'canonical');
  return readAttribute(tag, 'href');
}

function routeFromHtml(filePath) {
  const relative = path.relative(buildDir, filePath).replace(/\\/g, '/');
  if (relative === 'index.html') return '/';
  return `/${relative.replace(/\/index\.html$/, '').replace(/\.html$/, '')}`;
}

if (!fs.existsSync(buildDir)) {
  fail('build directory is missing');
} else {
  const robots = readBuild('robots.txt');
  if (!robots.includes(`Sitemap: ${siteUrl}/sitemap.xml`)) {
    fail('robots.txt does not point at the canonical sitemap');
  }

  const sitemap = readBuild('sitemap.xml');
  if (!sitemap.includes(`<loc>${siteUrl}/</loc>`)) {
    fail('sitemap.xml is missing the docs home page');
  }
  if (sitemap.includes('.html</loc>')) {
    fail('sitemap.xml contains .html URLs');
  }

  walk(buildDir, (filePath) => {
    if (!filePath.endsWith('.html')) return;
    if (path.basename(filePath) === '404.html') return;

    const html = fs.readFileSync(filePath, 'utf8');
    if (isRedirectPage(html)) return;

    const route = routeFromHtml(filePath);
    const expectedCanonical = `${siteUrl}${route === '/' ? '/' : route}`;
    const canonical = findCanonical(html);
    const description = findMetaContent(html, 'description');
    const title = html.match(/<title\b[^>]*>([^<]+)<\/title>/i)?.[1];
    const text = htmlText(html);

    if (canonical !== expectedCanonical) {
      fail(`${route} canonical mismatch: expected ${expectedCanonical}, got ${canonical || '(missing)'}`);
    }
    if (!description || description.length < 50) {
      fail(`${route} is missing a useful meta description`);
    }
    if (!title || title.length < 8) {
      fail(`${route} is missing a useful title`);
    }
    if (text.length < 180) {
      fail(`${route} has too little prerendered body text`);
    }
  });
}

if (failures.length > 0) {
  console.error('SEO verification failed:');
  for (const failure of failures) {
    console.error(`- ${failure}`);
  }
  process.exit(1);
}

console.log('SEO verification passed.');
