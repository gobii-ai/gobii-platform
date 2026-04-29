import fs from 'node:fs';
import YAML from 'yaml';

const specPath = new URL('../static/openapi/GobiiAPI.yaml', import.meta.url);
const sidebarPath = new URL('../content/api-reference/sidebar.js', import.meta.url);
const apiDocsDir = new URL('../content/api-reference/', import.meta.url);

function kebabOperationId(operationId) {
  return operationId
    .replace(/([a-z0-9])([A-Z])/g, '$1-$2')
    .replace(/([A-Za-z])([0-9]+)$/g, '$1-$2')
    .toLowerCase();
}

function slugify(value) {
  return value
    .replace(/([a-z0-9])([A-Z])/g, '$1-$2')
    .replace(/[^A-Za-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
    .toLowerCase();
}

function removeDuplicateSummary(source) {
  const title = source.match(/^title: "([^"]+)"$/m)?.[1];
  if (!title) {
    return source;
  }

  const escapedTitle = title.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return source.replace(
    new RegExp(`(</MethodEndpoint>\\n+)${escapedTitle}\\n+(<Heading\\n  id=\\{"request"\\})`),
    '$1$2'
  );
}

const spec = YAML.parse(fs.readFileSync(specPath, 'utf8'));
const groups = new Map();
const labelsById = new Map();
const descriptionsById = new Map();
const tagDescriptionsBySlug = new Map((spec.tags ?? []).map((tag) => [slugify(tag.name), tag.description]));
const apiInfoDescription = spec.info?.description;

for (const [path, pathItem] of Object.entries(spec.paths ?? {})) {
  for (const [method, operation] of Object.entries(pathItem ?? {})) {
    if (!operation || typeof operation !== 'object' || !operation.operationId) {
      continue;
    }

    const tag = operation.tags?.[0] ?? 'API';
    const sidebarTitle = operation['x-mint']?.metadata?.sidebarTitle;
    const label = sidebarTitle ?? operation.summary ?? operation.operationId;
    const id = kebabOperationId(operation.operationId);
    labelsById.set(id, label);
    descriptionsById.set(id, operation.description);
    const item = {
      type: 'doc',
      id: `api-reference/${id}`,
      label,
      className: `api-method ${method}`,
      path,
    };

    if (!groups.has(tag)) {
      groups.set(tag, []);
    }
    groups.get(tag).push(item);
  }
}

const categoryItems = [...groups].map(([label, items]) => ({
  type: 'category',
  label,
  link: {
    type: 'doc',
    id: `api-reference/${slugify(label)}`,
  },
  collapsed: false,
  items: items.map(({ path: _path, ...item }) => item),
}));

const sidebar = `module.exports = ${JSON.stringify(
  {
    apisidebar: [
      {
        type: 'doc',
        id: 'api-reference/gobii-api',
        label: 'Introduction',
      },
      ...categoryItems,
    ],
  },
  null,
  2,
)};
`;

fs.writeFileSync(sidebarPath, sidebar);

for (const [id, label] of labelsById) {
  const docPath = new URL(`${id}.api.mdx`, apiDocsDir);
  if (!fs.existsSync(docPath)) {
    continue;
  }

  let doc = fs.readFileSync(docPath, 'utf8');
  doc = doc.replace(/^sidebar_label: ".*"$/m, `sidebar_label: "${label.replace(/"/g, '\\"')}"`);
  const description = descriptionsById.get(id);
  if (description) {
    doc = doc.replace(/^description: ".*"$/m, `description: "${description.replace(/"/g, '\\"')}"`);
  }
  doc = removeDuplicateSummary(doc);
  fs.writeFileSync(docPath, doc);
}

if (apiInfoDescription) {
  const docPath = new URL('gobii-api.info.mdx', apiDocsDir);
  if (fs.existsSync(docPath)) {
    let doc = fs.readFileSync(docPath, 'utf8');
    doc = doc.replace(/^description: ".*"$/m, `description: "${apiInfoDescription.replace(/"/g, '\\"')}"`);
    fs.writeFileSync(docPath, doc);
  }
}

for (const [slug, description] of tagDescriptionsBySlug) {
  const docPath = new URL(`${slug}.tag.mdx`, apiDocsDir);
  if (!fs.existsSync(docPath) || !description) {
    continue;
  }

  let doc = fs.readFileSync(docPath, 'utf8');
  if (/^description: /m.test(doc)) {
    doc = doc.replace(/^description: ".*"$/m, `description: "${description.replace(/"/g, '\\"')}"`);
  } else {
    doc = doc.replace(/^title: .*\n/m, (match) => `${match}description: "${description.replace(/"/g, '\\"')}"\n`);
  }
  fs.writeFileSync(docPath, doc);
}
