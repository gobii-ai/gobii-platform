import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import YAML from 'yaml';

const root = process.cwd();
const contentDir = path.join(root, 'content');
const buildDir = path.join(root, 'build');
const openApiPath = path.join(root, 'static', 'openapi', 'GobiiAPI.yaml');
const siteUrl = process.env.DOCS_SITE_URL || 'https://docs.gobii.ai';

function readFrontmatter(filePath) {
  const source = fs.readFileSync(filePath, 'utf8');
  const match = source.match(/^---\n([\s\S]*?)\n---\n?/);
  const body = match ? source.slice(match[0].length) : source;
  const frontmatter = {};
  if (match) {
    for (const line of match[1].split('\n')) {
      const parsed = line.match(/^([A-Za-z0-9_-]+):\s*(.*)$/);
      if (!parsed) continue;
      frontmatter[parsed[1]] = parsed[2].replace(/^["']|["']$/g, '');
    }
  }
  return {body, frontmatter};
}

function walk(dir, callback) {
  if (!fs.existsSync(dir)) return;
  for (const entry of fs.readdirSync(dir, {withFileTypes: true})) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      walk(fullPath, callback);
    } else {
      callback(fullPath);
    }
  }
}

function ensureDir(filePath) {
  fs.mkdirSync(path.dirname(filePath), {recursive: true});
}

function writeText(relativePath, text) {
  const outPath = path.join(buildDir, relativePath);
  ensureDir(outPath);
  fs.writeFileSync(outPath, text, 'utf8');
}

function routeFromContentFile(filePath) {
  const relative = path.relative(contentDir, filePath).replace(/\\/g, '/');
  return relative.replace(/\.mdx?$/, '').replace(/\/index$/, '');
}

function collectContentDocs() {
  const docs = [];
  walk(contentDir, (filePath) => {
    if (!filePath.endsWith('.md') && !filePath.endsWith('.mdx')) return;
    if (filePath.includes(`${path.sep}api-reference${path.sep}`)) return;
    const route = routeFromContentFile(filePath);
    const {body, frontmatter} = readFrontmatter(filePath);
    docs.push({
      route,
      title: frontmatter.title || route,
      description: frontmatter.description || '',
      body,
    });
  });
  docs.sort((a, b) => a.route.localeCompare(b.route));
  return docs;
}

function collectApiDocs() {
  const docs = [];
  const apiDir = path.join(contentDir, 'api-reference');
  walk(apiDir, (filePath) => {
    if (!filePath.endsWith('.api.mdx')) return;
    const {body, frontmatter} = readFrontmatter(filePath);
    const id = frontmatter.id || path.basename(filePath, '.api.mdx');
    const route = `api-reference/${id}`;
    const endpoint = body.match(/method=\{"([^"]+)"\}[\s\S]*?path=\{"([^"]+)"\}/);
    const description = endpoint ? `${endpoint[1].toUpperCase()} ${endpoint[2]}` : frontmatter.description || '';
    docs.push({
      route,
      title: frontmatter.sidebar_label || frontmatter.title || id,
      description,
      body,
    });
  });
  docs.sort((a, b) => a.route.localeCompare(b.route));
  return docs;
}

const legacyApiRedirects = {
  'api-reference/agents-api/create-an-agent': 'api-reference/create-persistent-agent',
  'api-reference/agents-api/delete-agents-': 'api-reference/delete-persistent-agent',
  'api-reference/agents-api/get-agent-processing-status': 'api-reference/get-persistent-agent-processing-status',
  'api-reference/agents-api/get-agent-timeline': 'api-reference/get-persistent-agent-timeline',
  'api-reference/agents-api/get-agents': 'api-reference/list-persistent-agents',
  'api-reference/agents-api/get-agents-': 'api-reference/get-persistent-agent',
  'api-reference/agents-api/get-agents-web-tasks': 'api-reference/list-web-tasks',
  'api-reference/agents-api/message-agent': 'api-reference/send-persistent-agent-message',
  'api-reference/agents-api/post-agents-activate': 'api-reference/activate-persistent-agent',
  'api-reference/agents-api/post-agents-deactivate': 'api-reference/deactivate-persistent-agent',
  'api-reference/agents-api/post-agents-schedulepreview': 'api-reference/preview-persistent-agent-schedule',
  'api-reference/agents-api/put-agents-': 'api-reference/update-persistent-agent',
  'api-reference/agents-api/update-an-agent': 'api-reference/partial-update-persistent-agent',
  'api-reference/browser-use-tasks-api/delete-agentsbrowser-use-': 'api-reference/delete-agent',
  'api-reference/browser-use-tasks-api/delete-agentsbrowser-use-tasks-': 'api-reference/delete-task',
  'api-reference/browser-use-tasks-api/delete-tasksbrowser-use-': 'api-reference/delete-task-2',
  'api-reference/browser-use-tasks-api/get-agentsbrowser-use-tasks': 'api-reference/list-tasks',
  'api-reference/browser-use-tasks-api/get-agentsbrowser-use-tasks-': 'api-reference/get-task',
  'api-reference/browser-use-tasks-api/get-agentsbrowser-use-tasks-result': 'api-reference/get-task-result',
  'api-reference/browser-use-tasks-api/get-browser-use-profile': 'api-reference/get-agent',
  'api-reference/browser-use-tasks-api/get-tasksbrowser-use-': 'api-reference/get-task-2',
  'api-reference/browser-use-tasks-api/get-tasksbrowser-use-result': 'api-reference/get-task-result-2',
  'api-reference/browser-use-tasks-api/list-all-browser-use-tasks-for-profile': 'api-reference/list-all-tasks',
  'api-reference/browser-use-tasks-api/list-browser-use-profiles': 'api-reference/list-agents',
  'api-reference/browser-use-tasks-api/patch-agentsbrowser-use-': 'api-reference/update-agent-status-partial',
  'api-reference/browser-use-tasks-api/patch-agentsbrowser-use-tasks-': 'api-reference/update-task-status-partial',
  'api-reference/browser-use-tasks-api/patch-tasksbrowser-use-': 'api-reference/update-task-status-partial-2',
  'api-reference/browser-use-tasks-api/post-agentsbrowser-use': 'api-reference/create-agent',
  'api-reference/browser-use-tasks-api/post-agentsbrowser-use-tasks': 'api-reference/assign-task',
  'api-reference/browser-use-tasks-api/post-agentsbrowser-use-tasks-cancel': 'api-reference/cancel-task',
  'api-reference/browser-use-tasks-api/post-tasksbrowser-use': 'api-reference/assign-task-2',
  'api-reference/browser-use-tasks-api/post-tasksbrowser-use-cancel': 'api-reference/cancel-task-2',
  'api-reference/browser-use-tasks-api/put-agentsbrowser-use-': 'api-reference/update-agent',
  'api-reference/browser-use-tasks-api/put-agentsbrowser-use-tasks-': 'api-reference/update-task',
  'api-reference/browser-use-tasks-api/put-tasksbrowser-use-': 'api-reference/update-task-2',
  'api-reference/utilities/ping-api': 'api-reference/ping',
};

const legacyDocRedirects = {
  'getting-started/introduction': '',
};

function writeRedirect(fromRoute, toRoute) {
  const url = toRoute ? `/${toRoute}` : '/';
  const html = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="0; url=${url}">
  <link rel="canonical" href="${siteUrl}${url}">
  <title>Redirecting...</title>
</head>
<body>
  <a href="${url}">Redirecting...</a>
</body>
</html>
`;
  writeText(`${fromRoute}.html`, html);
}

function writeLegacyApiRedirects(apiDocs) {
  const byRoute = new Map(apiDocs.map((doc) => [doc.route, doc]));
  for (const [from, to] of Object.entries(legacyApiRedirects)) {
    writeRedirect(from, to);
    const target = byRoute.get(to);
    if (target) {
      writeText(`${from}.md`, `# ${target.title}\n\n${target.description}\n\nThis page moved to ${siteUrl}/${to}.\n`);
    }
  }
}

function writeNginxRedirects() {
  const lines = [
    '# Generated by docs/scripts/postbuild.mjs.',
    '# Keep public docs URLs as real HTTP redirects for SEO and backlinks.',
  ];

  for (const [from, to] of Object.entries(legacyDocRedirects)) {
    const target = to ? `/${to}` : '/';
    lines.push(`location = /${from} { add_header Cache-Control "public, max-age=300" always; return 301 ${target}$is_args$args; }`);
    lines.push(`location = /${from}.html { add_header Cache-Control "public, max-age=300" always; return 301 ${target}$is_args$args; }`);
    lines.push(`location = /${from}/ { add_header Cache-Control "public, max-age=300" always; return 301 ${target}$is_args$args; }`);
  }

  for (const [from, to] of Object.entries(legacyApiRedirects)) {
    lines.push(`location = /${from} { add_header Cache-Control "public, max-age=300" always; return 301 /${to}$is_args$args; }`);
    lines.push(`location = /${from}.html { add_header Cache-Control "public, max-age=300" always; return 301 /${to}$is_args$args; }`);
  }

  writeText('legacy-redirects.conf', `${lines.join('\n')}\n`);
}

function writeOpenApiJson() {
  const parsed = YAML.parse(fs.readFileSync(openApiPath, 'utf8'));
  writeText('api-reference/openapi.json', `${JSON.stringify(parsed, null, 2)}\n`);
}

function writeMarkdownCopies(contentDocs, apiDocs) {
  for (const doc of contentDocs) {
    writeText(`${doc.route}.md`, `# ${doc.title}\n\n${doc.body.trim()}\n`);
  }
  for (const doc of apiDocs) {
    writeText(`${doc.route}.md`, `# ${doc.title}\n\n${doc.description}\n\nSee ${siteUrl}/${doc.route}\n`);
  }
}

function writeLlms(contentDocs, apiDocs) {
  const lines = ['# Gobii', '', '## Docs', ''];
  for (const doc of [...contentDocs, ...apiDocs].sort((a, b) => a.route.localeCompare(b.route))) {
    const description = doc.description ? `: ${doc.description}` : '';
    lines.push(`- [${doc.title}](${siteUrl}/${doc.route}.md)${description}`);
  }
  lines.push('', '## OpenAPI Specs', '');
  lines.push(`- [GobiiAPI](${siteUrl}/openapi/GobiiAPI.yaml)`);
  lines.push(`- [GobiiAPI legacy path](${siteUrl}/GobiiAPI.yaml)`);
  writeText('llms.txt', `${lines.join('\n')}\n`);

  const full = ['# Gobii', ''];
  for (const doc of contentDocs) {
    full.push(`## ${doc.title}`, '', doc.body.trim(), '');
  }
  writeText('llms-full.txt', `${full.join('\n')}\n`);
}

function writeCompatibilityFiles() {
  fs.copyFileSync(openApiPath, path.join(buildDir, 'GobiiAPI.yaml'));
  writeText('robots.txt', `User-agent: *\nDisallow:\nSitemap: ${siteUrl}/sitemap.xml\n`);
}

function patchWebpackExports(source) {
  const moduleStartPattern = /([,{])(\d+)\(([A-Za-z_$][\w$]*),([A-Za-z_$][\w$]*),([A-Za-z_$][\w$]*)\)\{/g;
  const starts = [...source.matchAll(moduleStartPattern)];

  if (starts.length === 0) {
    return source;
  }

  let patched = '';
  let cursor = 0;

  for (let index = 0; index < starts.length; index += 1) {
    const start = starts[index];
    const next = starts[index + 1];
    const segmentStart = start.index;
    const segmentEnd = next?.index ?? source.length;
    const moduleSource = source.slice(segmentStart, segmentEnd);
    const exportsParam = start[4];

    patched += source.slice(cursor, segmentStart);
    patched += moduleSource.replace(/(^|[^.$\w])exports\./g, `$1${exportsParam}.`);
    cursor = segmentEnd;
  }

  patched += source.slice(cursor);
  return patched;
}

function patchJavaScriptAssets() {
  const assetsDir = path.join(buildDir, 'assets', 'js');
  if (!fs.existsSync(assetsDir)) {
    return;
  }

  for (const entry of fs.readdirSync(assetsDir, {withFileTypes: true})) {
    if (!entry.isFile() || !entry.name.endsWith('.js')) {
      continue;
    }

    const filePath = path.join(assetsDir, entry.name);
    const source = fs.readFileSync(filePath, 'utf8');
    const patched = patchWebpackExports(source);

    if (patched !== source) {
      fs.writeFileSync(filePath, patched, 'utf8');
    }
  }
}

const contentDocs = collectContentDocs();
const apiDocs = collectApiDocs();
writeMarkdownCopies(contentDocs, apiDocs);
for (const [from, to] of Object.entries(legacyDocRedirects)) {
  writeRedirect(from, to);
}
writeLegacyApiRedirects(apiDocs);
writeNginxRedirects();
writeLlms(contentDocs, apiDocs);
writeCompatibilityFiles();
writeOpenApiJson();
patchJavaScriptAssets();

console.log(`Wrote compatibility files for ${contentDocs.length} docs pages and ${apiDocs.length} API operations.`);
