import { FileText, Folder } from 'lucide-react';
import Link from 'next/link';

/**
 * Server-rendered tree using native <details>: expand/collapse works with no
 * client JavaScript, and folders on the path to the selected file start open.
 */
function FolderNode({ node, repositoryId, selectedPath, depth }) {
  const open = depth === 0 || (selectedPath ?? '').startsWith(`${node.path}/`);
  return (
    <details open={open} className="group">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 py-0.5 pr-2 text-ink-muted hover:text-ink" style={{ paddingLeft: `${depth * 0.875 + 0.5}rem` }}>
        <Folder aria-hidden size={13} strokeWidth={1.75} className="shrink-0" />
        <span className="truncate">{node.name}</span>
      </summary>
      <TreeChildren node={node} repositoryId={repositoryId} selectedPath={selectedPath} depth={depth + 1} />
    </details>
  );
}

function TreeChildren({ node, repositoryId, selectedPath, depth }) {
  return (
    <ul>
      {[...node.folders.values()].map((folder) => (
        <li key={folder.path}>
          <FolderNode node={folder} repositoryId={repositoryId} selectedPath={selectedPath} depth={depth} />
        </li>
      ))}
      {node.files.map((file) => {
        const selected = file.relative_path === selectedPath;
        return (
          <li key={file.relative_path}>
            <Link
              href={`/repositories/${repositoryId}?path=${encodeURIComponent(file.relative_path)}`}
              aria-current={selected ? 'true' : undefined}
              scroll={false}
              className={`flex items-center gap-1.5 py-0.5 pr-2 ${selected ? 'bg-accent-soft font-medium text-accent' : 'hover:bg-sunken'}`}
              style={{ paddingLeft: `${depth * 0.875 + 0.5}rem` }}
            >
              <FileText aria-hidden size={13} strokeWidth={1.75} className="shrink-0 opacity-60" />
              <span className="truncate">{file.name}</span>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

export default function FileTree({ tree, repositoryId, selectedPath }) {
  return (
    <div className="font-mono text-[0.8125rem]">
      <TreeChildren node={tree} repositoryId={repositoryId} selectedPath={selectedPath} depth={0} />
    </div>
  );
}
