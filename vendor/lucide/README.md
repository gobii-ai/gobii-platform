# Vendored Lucide icon catalog

`icons.json` is generated from the pinned `lucide-static` development dependency. It is used
only by Django while rendering templates; the catalog is never sent to the browser.

Do not edit the generated JSON by hand. After changing the pinned Lucide version, regenerate it:

```shell
npm run vendor:lucide --prefix frontend
```

Commit the updated catalog, package files, and license together.
