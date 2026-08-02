import React, { useState, useEffect, useCallback } from "react";
import {
  ChevronRight,
  ChevronDown,
  Folder,
  FileText,
  Loader2,
  FolderOpen,
  RefreshCw,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { fetchWorkspaceTree, type WorkspaceItem } from "@/lib/bimemo-api";
import { SidebarSectionLabel } from "./sidebar-chrome";

interface WorkspaceFileTreeProps {
  onFileSelect?: (path: string) => void;
}

interface TreeNode extends WorkspaceItem {
  children?: TreeNode[];
  expanded?: boolean;
  loading?: boolean;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function renderTree(
  nodes: TreeNode[],
  _currentPath: string,
  onFileSelect: ((path: string) => void) | undefined,
  onLoadDir: (path: string) => void,
  onToggle: (path: string) => void,
  level: number
): React.ReactElement[] {
  return nodes.map((node) => {
    const isDir = node.type === "directory";
    const isSelected = false; // Could add selection state later

    return (
      <div key={node.path}>
        <div
          className={cn(
            "flex items-center gap-1 cursor-pointer select-none rounded-sm hover:bg-sidebar-accent/50",
            "py-0.5 px-1",
            isSelected && "bg-sidebar-accent"
          )}
          style={{ paddingLeft: `${level * 16 + 4}px` }}
          onClick={() => {
            if (isDir) {
              onToggle(node.path);
            } else {
              onFileSelect?.(node.path);
            }
          }}
        >
          {/* Expand/collapse icon for directories */}
          {isDir ? (
            <span className="w-4 shrink-0">
              {node.expanded ? (
                <ChevronDown className="h-3 w-3 text-sidebar-foreground/50" />
              ) : (
                <ChevronRight className="h-3 w-3 text-sidebar-foreground/50" />
              )}
            </span>
          ) : (
            <span className="w-4 shrink-0" />
          )}

          {/* Icon */}
          <span className="shrink-0 text-sm">
            {isDir ? (
              node.expanded ? (
                <FolderOpen className="h-3.5 w-3.5 text-sidebar-foreground/70" />
              ) : (
                <Folder className="h-3.5 w-3.5 text-sidebar-foreground/70" />
              )
            ) : (
              <FileText className="h-3.5 w-3.5 text-sidebar-foreground/50" />
            )}
          </span>

          {/* Name */}
          <span className="truncate text-xs text-sidebar-foreground/80">
            {node.name}
          </span>

          {/* Size for files */}
          {!isDir && node.size != null && (
            <span className="ml-auto text-[10px] text-sidebar-foreground/30 shrink-0">
              {formatSize(node.size)}
            </span>
          )}

          {/* Loading indicator */}
          {isDir && node.loading && (
            <Loader2 className="h-3 w-3 animate-spin shrink-0 text-sidebar-foreground/40" />
          )}
        </div>

        {/* Render children if expanded and loaded */}
        {isDir && node.expanded && node.children && (
          <div>
            {renderTree(
              node.children,
              node.path,
              onFileSelect,
              onLoadDir,
              onToggle,
              level + 1
            )}
          </div>
        )}
      </div>
    );
  });
}

export function WorkspaceFileTree({ onFileSelect }: WorkspaceFileTreeProps) {
  const [tree, setTree] = useState<TreeNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadDir = useCallback(async (path: string) => {
    try {
      const data = await fetchWorkspaceTree(path);
      const nodes: TreeNode[] = data.items.map((item) => ({
        ...item,
        expanded: false,
        loading: false,
      }));
      return nodes;
    } catch (err) {
      console.error(`Failed to load directory ${path}:`, err);
      setError(err instanceof Error ? err.message : "Failed to load");
      return null;
    }
  }, []);

  // Load root on mount
  useEffect(() => {
    let cancelled = false;
    const init = async () => {
      setLoading(true);
      const nodes = await loadDir("");
      if (!cancelled && nodes) {
        setTree(nodes);
      }
      setLoading(false);
    };
    init();
    return () => {
      cancelled = true;
    };
  }, [loadDir]);

  const handleToggle = useCallback(
    async (path: string) => {
      setTree((prev) => {
        const updateNode = (nodes: TreeNode[]): TreeNode[] =>
          nodes.map((node) => {
            if (node.path === path) {
              const newExpanded = !node.expanded;
              if (newExpanded && !node.children) {
                // Mark as loading, then load async
                return { ...node, expanded: true, loading: true };
              }
              return { ...node, expanded: newExpanded };
            }
            if (node.children) {
              return { ...node, children: updateNode(node.children) };
            }
            return node;
          });

        return updateNode(prev);
      });

      // If expanding and no children loaded, fetch them
      const node = findNode(tree, path);
      if (node && node.type === "directory" && !node.children) {
        const children = await loadDir(path);
        if (children) {
          setTree((prev) => {
            const updateNode = (nodes: TreeNode[]): TreeNode[] =>
              nodes.map((n) => {
                if (n.path === path) {
                  return { ...n, children, loading: false };
                }
                if (n.children) {
                  return { ...n, children: updateNode(n.children) };
                }
                return n;
              });
            return updateNode(prev);
          });
        }
      }
    },
    [tree, loadDir]
  );

  const handleRefresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    const nodes = await loadDir("");
    if (nodes) {
      setTree(nodes);
    }
    setLoading(false);
  }, [loadDir]);

  if (loading && tree.length === 0) {
    return (
      <div className="flex h-full flex-col">
        <div className="flex items-center gap-2 px-3 py-2">
          <SidebarSectionLabel className="mr-auto">Workspace</SidebarSectionLabel>
          <Loader2 className="h-3.5 w-3.5 animate-spin text-sidebar-foreground/40" />
        </div>
        <div className="flex flex-1 items-center justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-sidebar-foreground/30" />
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2">
        <SidebarSectionLabel className="mr-auto">Workspace</SidebarSectionLabel>
        <Button
          variant="ghost"
          size="sm"
          onClick={handleRefresh}
          disabled={loading}
          className="h-7 gap-1 text-xs text-sidebar-foreground/72 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
        >
          {loading ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <RefreshCw className="h-3 w-3" />
          )}
        </Button>
      </div>

      {/* Error */}
      {error && (
        <div className="px-3 py-2">
          <p className="text-xs text-destructive">{error}</p>
        </div>
      )}

      {/* Tree */}
      <div className="flex-1 min-h-0 overflow-y-auto px-1 py-1">
        {tree.length === 0 ? (
          <p className="px-3 py-4 text-center text-sm text-sidebar-foreground/50">
            No workspace configured
          </p>
        ) : (
          renderTree(tree, "", onFileSelect, loadDir, handleToggle, 0)
        )}
      </div>
    </div>
  );
}

function findNode(nodes: TreeNode[], path: string): TreeNode | null {
  for (const node of nodes) {
    if (node.path === path) return node;
    if (node.children) {
      const found = findNode(node.children, path);
      if (found) return found;
    }
  }
  return null;
}
