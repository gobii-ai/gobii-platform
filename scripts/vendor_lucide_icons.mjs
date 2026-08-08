import { copyFile, mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';


const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const packageRoot = path.join(projectRoot, 'frontend', 'node_modules', 'lucide-static');
const sourceDirectory = path.join(packageRoot, 'icons');
const outputDirectory = path.join(projectRoot, 'vendor', 'lucide');
const outputPath = path.join(outputDirectory, 'icons.json');
const supportedElements = new Set([
  'circle', 'ellipse', 'line', 'path', 'polygon', 'polyline', 'rect',
]);
const supportedAttributes = new Set([
  'cx', 'cy', 'd', 'fill', 'height', 'points', 'r', 'rx', 'ry', 'width',
  'x', 'x1', 'x2', 'y', 'y1', 'y2',
]);
const elementPattern = /<([a-z]+)\b([^>]*)\/>/g;
const attributePattern = /([a-zA-Z][\w:-]*)="([^"]*)"/g;
const tagPattern = /<\/?([a-z]+)\b[^>]*>/g;


function parseIcon(source, filename) {
  for (const tagMatch of source.matchAll(tagPattern)) {
    const tagName = tagMatch[1];
    if (tagName !== 'svg' && !supportedElements.has(tagName)) {
      throw new Error(`Unsupported <${tagName}> element found in ${filename}`);
    }
  }

  const nodes = [];
  for (const elementMatch of source.matchAll(elementPattern)) {
    const elementName = elementMatch[1];
    if (!supportedElements.has(elementName)) {
      continue;
    }

    const attributes = {};
    for (const attributeMatch of elementMatch[2].matchAll(attributePattern)) {
      const attributeName = attributeMatch[1];
      if (!supportedAttributes.has(attributeName)) {
        throw new Error(`Unsupported ${attributeName} attribute found in ${filename}`);
      }
      attributes[attributeName] = attributeMatch[2];
    }
    nodes.push([elementName, attributes]);
  }

  if (nodes.length === 0) {
    throw new Error(`No supported SVG elements found in ${filename}`);
  }
  return nodes;
}


const packageMetadata = JSON.parse(await readFile(path.join(packageRoot, 'package.json'), 'utf8'));
const iconFiles = (await readdir(sourceDirectory))
  .filter((filename) => filename.endsWith('.svg'))
  .sort();
const icons = {};

for (const filename of iconFiles) {
  const iconName = filename.slice(0, -4);
  const source = await readFile(path.join(sourceDirectory, filename), 'utf8');
  icons[iconName] = parseIcon(source, filename);
}

await mkdir(outputDirectory, { recursive: true });
await copyFile(path.join(packageRoot, 'LICENSE'), path.join(outputDirectory, 'LICENSE'));
await writeFile(
  outputPath,
  `${JSON.stringify({
    version: packageMetadata.version,
    license: packageMetadata.license,
    icons,
  })}\n`,
  'utf8',
);

process.stdout.write(
  `Wrote ${Object.keys(icons).length} Lucide ${packageMetadata.version} icons to ${outputPath}\n`,
);
