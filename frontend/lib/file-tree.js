/** Builds a nested folder tree from the API's flat, sorted file list. Pure. */
export function buildFileTree(files) {
  const root = { name: '', path: '', folders: new Map(), files: [] };
  for (const file of files) {
    const parts = file.relative_path.split('/');
    let node = root;
    for (const [index, part] of parts.slice(0, -1).entries()) {
      const path = parts.slice(0, index + 1).join('/');
      if (!node.folders.has(part)) node.folders.set(part, { name: part, path, folders: new Map(), files: [] });
      node = node.folders.get(part);
    }
    node.files.push({ ...file, name: parts.at(-1) });
  }
  return root;
}
